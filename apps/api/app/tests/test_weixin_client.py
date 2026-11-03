import base64
import hashlib
import json

import httpx
import pytest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services.weixin_client import (
    WeixinClient,
    WeixinRateLimited,
    WeixinSessionExpired,
    extract_text,
    is_group_message,
    is_self_message,
    qr_svg,
)


def _client(handler, **kwargs) -> WeixinClient:
    return WeixinClient(
        account_id="bot@im.bot",
        token="bot-token",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_extract_text_and_message_classification() -> None:
    items = [{"type": 1, "text_item": {"text": "你好"}}]
    assert extract_text(items) == "你好"
    assert extract_text([{"type": 2}]) == ""
    assert is_group_message({"room_id": "room"})
    assert not is_group_message({"from_user_id": "user"})
    assert is_self_message({"from_user_id": "bot@im.bot"}, "bot@im.bot")
    assert not is_self_message({"from_user_id": "user"}, "bot@im.bot")


def test_qr_svg_renders() -> None:
    svg = qr_svg("https://weixin.qq.com/x/abc")
    assert svg.startswith("<svg") or "<svg" in svg


@pytest.mark.asyncio
async def test_send_image_uploads_encrypted_png_and_sends_image_item() -> None:
    captured: list[httpx.Request] = []
    image = b"\x89PNG\r\n\x1a\n" + b"qr-code-bytes" * 20

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        url = str(request.url)
        if "getuploadurl" in url:
            return httpx.Response(200, json={"ret": 0, "upload_param": "up-param"})
        if url.startswith("https://novac2c.cdn.weixin.qq.com"):
            return httpx.Response(200, headers={"x-encrypted-param": "dl-param"})
        return httpx.Response(200, json={"ret": 0, "errcode": 0})

    client = _client(handler)
    try:
        result = await client.send_image(
            to_user_id="user-1", image=image, context_token="ctx-1"
        )
        assert result["ret"] == 0
    finally:
        await client.aclose()

    upload_request, cdn_request, send_request = captured
    upload_body = json.loads(upload_request.content)
    assert upload_body["media_type"] == 1
    assert upload_body["to_user_id"] == "user-1"
    assert upload_body["rawsize"] == len(image)
    assert upload_body["rawfilemd5"] == hashlib.md5(image).hexdigest()
    assert upload_body["filesize"] % 16 == 0
    assert upload_body["filesize"] >= len(image)
    assert upload_body["no_need_thumb"] is True

    aes_key = bytes.fromhex(upload_body["aeskey"])
    decryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
    plain = decryptor.update(cdn_request.content) + decryptor.finalize()
    padding = plain[-1]
    assert plain[:-padding] == image
    assert cdn_request.headers["content-type"] == "application/octet-stream"

    send_body = json.loads(send_request.content)
    message = send_body["msg"]
    assert message["to_user_id"] == "user-1"
    assert message["context_token"] == "ctx-1"
    item = message["item_list"][0]
    assert item["type"] == 2
    media = item["image_item"]["media"]
    assert media["encrypt_query_param"] == "dl-param"
    assert media["encrypt_type"] == 1
    assert media["aes_key"] == base64.b64encode(upload_body["aeskey"].encode()).decode()
    assert item["image_item"]["mid_size"] == len(cdn_request.content)


@pytest.mark.asyncio
async def test_send_image_requires_upload_param() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": 0})

    client = _client(handler)
    try:
        with pytest.raises(Exception, match="upload_param"):
            await client.send_image(to_user_id="u", image=b"png")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_send_text_payload_and_headers() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ret": 0, "errcode": 0})

    client = _client(handler)
    try:
        result = await client.send_text(
            to_user_id="user-1", text="hello", context_token="ctx-1"
        )
        assert result["ret"] == 0
    finally:
        await client.aclose()

    request = captured[0]
    assert request.headers["authorization"] == "Bearer bot-token"
    assert request.headers["authorizationtype"] == "ilink_bot_token"
    assert request.headers["ilink-app-id"] == "bot"
    body = json.loads(request.content)
    assert body["base_info"]["channel_version"] == "2.2.0"
    message = body["msg"]
    assert message["to_user_id"] == "user-1"
    assert message["message_type"] == 2
    assert message["message_state"] == 2
    assert message["context_token"] == "ctx-1"
    assert message["item_list"] == [{"type": 1, "text_item": {"text": "hello"}}]


@pytest.mark.asyncio
async def test_stale_session_and_rate_limit_errors() -> None:
    def stale_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": -2, "errmsg": "prepare failed"})

    client = _client(stale_handler)
    try:
        with pytest.raises(WeixinSessionExpired):
            await client.send_text(to_user_id="u", text="x")
    finally:
        await client.aclose()

    def expired_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": 0, "errcode": -14})

    client = _client(expired_handler)
    try:
        with pytest.raises(WeixinSessionExpired):
            await client.get_updates("buf")
    finally:
        await client.aclose()

    def limited_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": -2, "errmsg": "too frequent"})

    client = _client(limited_handler)
    try:
        with pytest.raises(WeixinRateLimited):
            await client.send_text(to_user_id="u", text="x")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_long_poll_timeout_is_an_empty_poll() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("poll window elapsed", request=request)

    client = _client(timeout_handler)
    try:
        response = await client.get_updates("sync-buffer")
        assert response == {
            "ret": 0,
            "msgs": [],
            "get_updates_buf": "sync-buffer",
        }
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_qr_endpoints() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "get_bot_qrcode" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "qrcode": "qr-token",
                    "qrcode_img_content": "https://weixin.qq.com/x/scan",
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "confirmed",
                "ilink_bot_id": "bot@im.bot",
                "bot_token": "token",
                "baseurl": "https://ilinkai.weixin.qq.com",
                "ilink_user_id": "user",
            },
        )

    client = _client(handler)
    try:
        qr = await client.fetch_qr()
        assert qr["qrcode"] == "qr-token"
        status = await client.qr_status("qr-token")
        assert status["status"] == "confirmed"
    finally:
        await client.aclose()

    assert any("bot_type=3" in url for url in seen)
    assert any("qrcode=qr-token" in url for url in seen)
