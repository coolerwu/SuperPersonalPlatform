import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from deepagents.backends import CompositeBackend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from server.infrastructure.agent_filesystem_backend import AgentFilesystemBackend
from server.infrastructure.ilink_client import ILinkClient, ILinkAPIError
from server.infrastructure.outgoing_attachments import queue_attachment, list_attachments, read_attachment
from server.infrastructure.tool_runtime import PlatformToolContext, _send_attachment_tool
from server.tests.test_run_delivery_service import CONFIG
from server.app.run_service import RunService
from server.app.run_delivery_service import RunDeliveryService
from server.app.system_log_service import SystemLogService


def backend_at(root):
    (root/'artifacts').mkdir(parents=True, exist_ok=True)
    return CompositeBackend(default=AgentFilesystemBackend(root_dir=root, virtual_mode=True), routes={})


@pytest.mark.parametrize('kind,data', [('image', b'\x89PNG\r\n\x1a\nimage'), ('file', b'%PDF-1.4 document')])
@pytest.mark.parametrize('full_url', [True, False])
def test_ilink_encrypt_upload_and_message(kind, data, full_url):
    requests=[]
    encryption={}
    def handle(request):
        requests.append(request)
        if request.url.path.endswith('/getuploadurl'):
            payload=json.loads(request.content)
            encryption.update(payload)
            assert request.headers['authorization']=='Bearer token'
            assert payload['media_type']==(1 if kind=='image' else 3)
            assert payload['rawsize']==len(data) and payload['no_need_thumb'] is True
            return httpx.Response(200,json={'upload_full_url':'https://cdn.weixin.qq.com/upload'} if full_url else {'upload_param':'x+y&z'})
        if request.url.path.endswith('/upload'):
            assert 'authorization' not in request.headers
            if not full_url: assert request.url.params['encrypted_query_param']=='x+y&z'
            decryptor=Cipher(algorithms.AES(bytes.fromhex(encryption['aeskey'])),modes.ECB()).decryptor()
            padded=decryptor.update(request.content)+decryptor.finalize()
            unpadder=padding.PKCS7(128).unpadder()
            assert unpadder.update(padded)+unpadder.finalize()==data
            assert len(request.content)==encryption['filesize']
            return httpx.Response(200,headers={'x-encrypted-param':'download-key'})
        message=json.loads(request.content)['msg']
        item=message['item_list'][0]
        media=item['image_item' if kind=='image' else 'file_item']['media']
        assert base64.b64decode(media['aes_key']).decode()==encryption['aeskey']
        assert media['encrypt_query_param']=='download-key'
        if kind=='file':
            assert item['file_item']['len']==str(len(data))
            assert item['file_item']['file_name']=='报告.pdf'
        assert message['client_id']=='stable-id'
        return httpx.Response(200,json={'ret':0})
    async def run():
        client=ILinkClient()
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        item=await client.upload_attachment('https://ilink.example','token',to_user_id='user',data=data,filename='报告.pdf',kind=kind)
        await client.send_message('https://ilink.example','token',{'to_user_id':'user','client_id':'stable-id','item_list':[item]})
        await client._client.aclose()
    asyncio.run(run())
    assert len(requests)==3


@pytest.mark.parametrize('failure', ['api', 'cdn', 'header', 'send', 'unsafe'])
def test_media_failures_are_not_reported_as_sent(failure):
    def handle(request):
        if request.url.path.endswith('getuploadurl'):
            if failure=='api': return httpx.Response(200,json={'ret':-1})
            return httpx.Response(200,json={'upload_full_url':'https://127.0.0.1/upload' if failure=='unsafe' else 'https://cdn.weixin.qq.com/upload'})
        if request.url.path.endswith('upload'):
            if failure=='cdn': return httpx.Response(503)
            return httpx.Response(200,headers={} if failure=='header' else {'x-encrypted-param':'download'})
        return httpx.Response(200,json={'errcode':123})
    async def run():
        client=ILinkClient()
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            item=await client.upload_attachment('https://ilink.example','token',to_user_id='user',data=b'data',filename='a.txt',kind='file')
            await client.send_message('https://ilink.example','token',{'item_list':[item]})
        finally: await client._client.aclose()
    with pytest.raises(ILinkAPIError): asyncio.run(run())


def test_queue_freezes_file_deduplicates_and_enforces_paths(tmp_path):
    run=tmp_path/'run'
    run.mkdir()
    (run/'state.json').write_text('{}')
    root=tmp_path/'agent'
    backend=backend_at(root)
    (root/'artifacts/文件.txt').write_text('original')
    first=queue_attachment(run,backend,'/artifacts/文件.txt')
    assert queue_attachment(run,backend,'/artifacts/文件.txt')==first
    (root/'artifacts/文件.txt').write_text('changed')
    assert read_attachment(run,first)==b'original'
    assert len(list_attachments(run))==1
    (root/'artifacts/link.txt').symlink_to(tmp_path/'secret')
    for path in ['/browser/cookies','/artifacts/link.txt','/artifacts/../config.yaml','relative','/artifacts/']:
        with pytest.raises((OSError,ValueError)): queue_attachment(run,backend,path)
    with pytest.raises(ValueError): queue_attachment(run,backend,'/artifacts/文件.txt','image')
    (run/'state.json').write_text('{"rerun_count":1}')
    assert list_attachments(run)==[]


def test_delivery_retries_only_remaining_parts_and_survives_restart(tmp_path, monkeypatch):
    (tmp_path/'config.yaml').write_text(CONFIG)
    async def fake_run(self,**kwargs): return 'attachments follow'
    monkeypatch.setattr('server.infrastructure.deepagent_runtime.DeepAgentRuntime.run',fake_run)
    monkeypatch.setattr('server.app.run_delivery_service.DELIVERY_RETRY_DELAYS_SECONDS',(0,)*6)
    service=RunService(tmp_path)
    run=asyncio.run(service.create_run(content='Send files',agent_id='assistant',source='wechat',metadata={'account_id':'default','from_user_id':'recipient','context_token':'ctx'}))
    run_id=run['run_id']
    root=tmp_path/'agents/assistant/workspace'
    backend=backend_at(root)
    (root/'artifacts/a.txt').write_text('one')
    (root/'artifacts/b.png').write_bytes(b'\x89PNG\r\n\x1a\nimage')
    context=PlatformToolContext(run_id=run_id,source='wechat',agent_id='assistant',session_id='',metadata={})
    tool=_send_attachment_tool(tmp_path,context,backend)
    for path in ['/artifacts/a.txt','/artifacts/b.png']:
        assert json.loads(tool.invoke({'file_path':path}))['status']=='queued'
    asyncio.run(service.execute_run(run_id))
    class Channel:
        def __init__(self): self.texts=[];self.attachments=[];self.fail=True
        async def deliver_text(self,**kwargs): self.texts.append(kwargs)
        async def deliver_attachment(self,**kwargs):
            self.attachments.append(kwargs)
            if kwargs['filename']=='b.png' and self.fail:
                self.fail=False
                raise TimeoutError('transient')
    channel=Channel()
    def delivery(): return RunDeliveryService(run_service=RunService(tmp_path),channel_delivery_service=channel,system_log_service=SystemLogService(tmp_path))
    assert asyncio.run(delivery().process_run(run_id))['status']=='failed'
    assert asyncio.run(delivery().process_run(run_id))['status']=='delivered'
    assert len(channel.texts)==1
    assert [a['filename'] for a in channel.attachments]==['a.txt','b.png','b.png']
    assert channel.attachments[1]['client_id']==channel.attachments[2]['client_id']
    assert all(a['to_user_id']=='recipient' for a in channel.attachments)
    assert channel.attachments[-1]['kind']=='image'
    service.rerun(run_id)
    assert service.outgoing_attachments(run_id)==[]


def test_tool_refuses_non_wechat_target(tmp_path):
    run=tmp_path/'runs/r'
    run.mkdir(parents=True)
    (run/'state.json').write_text('{}')
    (run/'input.json').write_text('{"agent_id":"a","source":"web_chat"}')
    tool=_send_attachment_tool(tmp_path,PlatformToolContext('r','web_chat','a','',{}),backend_at(tmp_path/'a'))
    assert json.loads(tool.invoke({'file_path':'/artifacts/a.txt'}))['ok'] is False


def test_attachment_size_count_and_tamper_limits(tmp_path, monkeypatch):
    import server.infrastructure.outgoing_attachments as module
    monkeypatch.setattr(module,'MAX_ATTACHMENT_BYTES',8)
    monkeypatch.setattr(module,'MAX_ATTACHMENTS',1)
    run=tmp_path/'run'
    run.mkdir()
    (run/'state.json').write_text('{}')
    root=tmp_path/'agent'
    backend=backend_at(root)
    (root/'artifacts/large').write_bytes(b'x'*9)
    with pytest.raises(ValueError): queue_attachment(run,backend,'/artifacts/large')
    (root/'artifacts/a').write_bytes(b'valid')
    item=queue_attachment(run,backend,'/artifacts/a')
    (root/'artifacts/b').write_bytes(b'valid')
    with pytest.raises(ValueError): queue_attachment(run,backend,'/artifacts/b')
    (module.outbox(run)/item['id']).write_bytes(b'changed')
    with pytest.raises(ValueError): read_attachment(run,item)


def test_wechat_channel_uploads_then_sends_one_media_item(tmp_path):
    from server.tests.test_wechat_channel_service import _service
    service,_,_= _service(tmp_path)
    calls=[]
    class Client:
        async def upload_attachment(self,base,token,**kwargs):
            calls.append(('upload',kwargs))
            return {'type':4,'file_item':{'file_name':kwargs['filename']}}
        async def send_message(self,base,token,message):
            calls.append(('send',message))
            return {'ret':0}
    service._client=Client()
    asyncio.run(service.deliver_attachment(to_user_id='recipient',context_token='ctx',data=b'pdf',filename='report.pdf',kind='file',client_id='stable'))
    assert [call[0] for call in calls]==['upload','send']
    assert calls[1][1]['item_list']==[{'type':4,'file_item':{'file_name':'report.pdf'}}]
    assert calls[1][1]['context_token']=='ctx'
    assert calls[1][1]['client_id']=='stable'


def test_readonly_webdav_can_be_sent_but_unselected_paths_cannot(tmp_path):
    from server.tests.test_webdav_backend import setup_backend
    backend,_,_,calls=setup_backend(tmp_path,'read')
    run=tmp_path/'run'
    run.mkdir()
    (run/'state.json').write_text('{}')
    item=queue_attachment(run,backend,'/webdav/team/note.md')
    assert read_attachment(run,item)==b'original'
    with pytest.raises(PermissionError): queue_attachment(run,backend,'/webdav/secret.md')
    assert calls==[]
