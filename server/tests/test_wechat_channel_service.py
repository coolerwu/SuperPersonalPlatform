import asyncio
import base64
from pathlib import Path
from typing import Any

from server.app.session_service import SessionService
from server.app.wechat_channel_service import WechatChannelService


CONFIG = """\
auth:
  token: secret-token
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://api.openai.com/v1
      api_key: test-key
      model: gpt-4o-mini
      supports_images: true
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.
      model_id: default
      context_ids: []
      deepagent:
        max_iterations: 7
channels:
  wechat_personal:
    accounts:
      - id: default
        default_agent_id: assistant
"""


class FakeRunService:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_run(
        self,
        *,
        content: str,
        agent_id: str,
        source: str,
        session_id: str,
        attachments: tuple[dict[str, Any], ...],
        metadata: dict[str, Any],
    ) -> dict[str, str]:
        run_id = f"run-{len(self.created) + 1}"
        self.created.append(
            {
                "run_id": run_id,
                "content": content,
                "agent_id": agent_id,
                "source": source,
                "session_id": session_id,
                "attachments": attachments,
                "metadata": metadata,
            }
        )
        return {"run_id": run_id}

    async def execute_run(self, run_id: str) -> dict[str, Any]:
        return {"result": {"content": f"reply for {run_id}"}}


class FakeWechatClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.media_reads: list[dict[str, Any]] = []
        self.media_content = b""
        self.media_content_type = "application/octet-stream"

    async def send_message(self, baseurl: str, bot_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append({"baseurl": baseurl, "bot_token": bot_token, "payload": payload})
        return {"_debug_status": 200}

    async def read_media_bytes(self, url: str, *, bot_token: str = "", max_bytes: int = 8 * 1024 * 1024):
        self.media_reads.append({"url": url, "bot_token": bot_token, "max_bytes": max_bytes})
        return self.media_content, self.media_content_type


def _image_message(context_token: str = "imgctx") -> dict[str, Any]:
    return {
        "from_user_id": "wxid_user",
        "to_user_id": "wxid_bot",
        "context_token": context_token,
        "item_list": [
            {
                "image_item": {
                    "id": "photo-1",
                    "filename": "photo.png",
                    "mime": "image/png",
                    "content_base64": base64.b64encode(b"image-bytes").decode("ascii"),
                }
            }
        ],
    }


def _encrypted_cdn_image_message(context_token: str = "imgctx") -> dict[str, Any]:
    return {
        "from_user_id": "wxid_user",
        "to_user_id": "wxid_bot",
        "context_token": context_token,
        "item_list": [
            {
                "type": 2,
                "image_item": {
                    "md5": "photo-md5",
                    "aeskey": "0123456789abcdef",
                    "media": {
                        "encrypt_query_param": "encrypted-param",
                    },
                },
            }
        ],
    }


def _text_message(text: str, context_token: str = "textctx") -> dict[str, Any]:
    return {
        "from_user_id": "wxid_user",
        "to_user_id": "wxid_bot",
        "context_token": context_token,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }


def _service(tmp_path: Path) -> tuple[WechatChannelService, FakeRunService, FakeWechatClient]:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    run_service = FakeRunService()
    client = FakeWechatClient()
    service = WechatChannelService(tmp_path, run_service, session_service=SessionService(tmp_path))
    service._client = client
    service._baseurl = "https://ilink.example"
    service._bot_token = "bot-token"
    service._pending_input_delay_seconds = 0.03
    return service, run_service, client


def test_wechat_text_waits_for_pending_window_before_run(tmp_path) -> None:
    async def scenario() -> None:
        service, run_service, client = _service(tmp_path)

        await service._process_message(_text_message("你好"))
        assert run_service.created == []

        await asyncio.sleep(0.06)

        assert len(run_service.created) == 1
        created = run_service.created[0]
        assert created["content"] == "你好"
        assert created["metadata"]["batched_messages"] == 1
        assert created["metadata"]["delay_seconds"] == 0.03
        assert client.sent[0]["payload"]["context_token"] == "textctx"

    asyncio.run(scenario())


def test_wechat_image_then_text_waits_and_merges_into_one_run(tmp_path) -> None:
    async def scenario() -> None:
        service, run_service, client = _service(tmp_path)

        await service._process_message(_image_message())
        assert run_service.created == []

        await service._process_message(_text_message("看看这张图"))
        assert run_service.created == []
        await asyncio.sleep(0.05)

        assert len(run_service.created) == 1
        created = run_service.created[0]
        assert created["content"] == "看看这张图"
        assert len(created["attachments"]) == 1
        assert created["attachments"][0]["filename"] == "photo.png"
        assert created["metadata"]["merged_pending_images"] == 1
        assert created["metadata"]["batched_messages"] == 2
        assert created["metadata"]["context_token"] == "textctx"
        assert client.sent[0]["payload"]["context_token"] == "textctx"

    asyncio.run(scenario())


def test_wechat_encrypted_cdn_image_then_text_merges_into_one_run(tmp_path) -> None:
    async def scenario() -> None:
        service, run_service, client = _service(tmp_path)
        client.media_content = _encrypt_aes_ecb_pkcs7(b"\x89PNG\r\n\x1a\nimage-bytes", b"0123456789abcdef")

        await service._process_message(_encrypted_cdn_image_message())
        assert run_service.created == []

        await service._process_message(_text_message("你看看"))
        assert run_service.created == []
        await asyncio.sleep(0.05)

        assert len(run_service.created) == 1
        created = run_service.created[0]
        assert created["content"] == "你看看"
        assert len(created["attachments"]) == 1
        assert created["attachments"][0]["mime"] == "image/png"
        assert created["attachments"][0]["bytes"] == b"\x89PNG\r\n\x1a\nimage-bytes"
        assert created["metadata"]["merged_pending_images"] == 1
        assert client.media_reads[0]["url"].startswith("https://novac2c.cdn.weixin.qq.com/c2c/download?")

    asyncio.run(scenario())


def test_wechat_image_only_flushes_after_short_pending_window(tmp_path) -> None:
    async def scenario() -> None:
        service, run_service, client = _service(tmp_path)

        await service._process_message(_image_message(context_token="img-only"))
        await asyncio.sleep(0.06)

        assert len(run_service.created) == 1
        created = run_service.created[0]
        assert created["content"] == "用户发送了一张图片。"
        assert len(created["attachments"]) == 1
        assert created["metadata"]["image_only_flush"] is True
        assert created["metadata"]["batched_messages"] == 1
        assert client.sent[0]["payload"]["context_token"] == "img-only"

    asyncio.run(scenario())


def test_wechat_clear_session_command_rotates_active_session_without_run(tmp_path) -> None:
    async def scenario() -> None:
        service, run_service, client = _service(tmp_path)

        await service._process_message(_text_message("第一句", context_token="first"))
        await asyncio.sleep(0.06)
        first_session_id = run_service.created[0]["session_id"]

        await service._process_message(_text_message("清空上下文", context_token="clear"))
        await asyncio.sleep(0.06)

        assert len(run_service.created) == 1
        assert client.sent[-1]["payload"]["context_token"] == "clear"
        assert "已清空上下文" in client.sent[-1]["payload"]["item_list"][0]["text_item"]["text"]

        await service._process_message(_text_message("第二句", context_token="second"))
        await asyncio.sleep(0.06)

        assert len(run_service.created) == 2
        assert run_service.created[1]["session_id"] != first_session_id

    asyncio.run(scenario())


def _encrypt_aes_ecb_pkcs7(content: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padding = 16 - (len(content) % 16)
    padded = content + bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()
