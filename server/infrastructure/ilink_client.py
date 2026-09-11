from __future__ import annotations

import base64
import io
import ipaddress
import random
from typing import Any
from urllib.parse import urlparse

import httpx
import qrcode

ILINK_BASE = "https://ilinkai.weixin.qq.com"


def generate_qrcode_data_url(qrcode_str: str, width: int = 280) -> str:
    img = qrcode.make(qrcode_str)
    img = img.resize((width, width))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"


class ILinkAPIError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"iLink API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class ILinkSessionExpiredError(ILinkAPIError):
    pass


class ILinkClient:
    def __init__(self, proxy: str | None = None) -> None:
        timeout = httpx.Timeout(40.0, connect=10.0)
        proxy_url = proxy.strip() if proxy else None
        self._client = httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy_url,
        )

    async def get_bot_qrcode(self) -> dict[str, Any]:
        response = await self._client.get(
            f"{ILINK_BASE}/ilink/bot/get_bot_qrcode",
            params={"bot_type": "3"},
            headers=self._auth_headers(),
        )
        if response.status_code != 200:
            raise ILinkAPIError(response.status_code, response.text)
        return response.json()

    async def get_qrcode_status(self, qrcode: str) -> dict[str, Any]:
        response = await self._client.get(
            f"{ILINK_BASE}/ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode},
            headers=self._auth_headers(),
        )
        if response.status_code != 200:
            raise ILinkAPIError(response.status_code, response.text)
        return response.json()

    async def get_updates(
        self,
        baseurl: str,
        bot_token: str,
        get_updates_buf: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{baseurl}/ilink/bot/getupdates",
                json={
                    "get_updates_buf": get_updates_buf or "",
                    "base_info": {"channel_version": "1.0.2"},
                },
                headers=self._auth_headers(bot_token),
            )
        except httpx.ReadTimeout:
            return {"msgs": [], "get_updates_buf": get_updates_buf or ""}

        if response.status_code in (401, 403):
            raise ILinkSessionExpiredError(response.status_code, response.text)
        if response.status_code != 200:
            raise ILinkAPIError(response.status_code, response.text)
        return response.json()

    async def send_message(
        self,
        baseurl: str,
        bot_token: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        import json as _json
        import uuid as _uuid
        url = f"{baseurl}/ilink/bot/sendmessage"
        message.setdefault("from_user_id", "")
        message.setdefault("client_id", f"wx-{_uuid.uuid4().hex[:12]}")
        body = {
            "msg": message,
            "base_info": {"channel_version": "1.0.2"},
        }
        response = await self._client.post(
            url,
            json=body,
            headers=self._auth_headers(bot_token),
        )
        if response.status_code in (401, 403):
            raise ILinkSessionExpiredError(response.status_code, response.text)
        if response.status_code != 200:
            raise ILinkAPIError(response.status_code, response.text)
        raw = response.text
        result = self._checked_response(response)
        result.setdefault("_debug_url", url)
        result.setdefault("_debug_status", response.status_code)
        result.setdefault("_debug_body", _json.dumps(body, ensure_ascii=False)[:500])
        result.setdefault("_debug_raw", raw[:500])
        return result

    async def upload_attachment(self, baseurl: str, bot_token: str, *, to_user_id: str,
                                data: bytes, filename: str, kind: str) -> dict[str, Any]:
        import hashlib
        import secrets
        from urllib.parse import urlencode
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from server.infrastructure.outgoing_attachments import MAX_ATTACHMENT_BYTES, image_mime
        if not data or len(data) > MAX_ATTACHMENT_BYTES or kind not in {"image", "file"}:
            raise ValueError("Invalid attachment type or size")
        if kind == "image" and not image_mime(data):
            raise ValueError("Unsupported image format")
        key = secrets.token_bytes(16)
        filekey = secrets.token_hex(16)
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        response = await self._client.post(
            f"{baseurl.rstrip('/')}/ilink/bot/getuploadurl", headers=self._auth_headers(bot_token),
            json={"filekey": filekey, "media_type": 1 if kind == "image" else 3,
                  "to_user_id": to_user_id, "rawsize": len(data), "rawfilemd5": hashlib.md5(data).hexdigest(),
                  "filesize": len(encrypted), "no_need_thumb": True, "aeskey": key.hex(),
                  "base_info": {"channel_version": "1.0.2"}},
        )
        payload = self._checked_response(response)
        url = str(payload.get("upload_full_url") or "").strip()
        if not url:
            param = payload.get("upload_param")
            if not param: raise ILinkAPIError(502, "Missing media upload parameters")
            url = "https://novac2c.cdn.weixin.qq.com/c2c/upload?" + urlencode({"encrypted_query_param": param, "filekey": filekey})
        if not _safe_media_url(url) or urlparse(url).scheme != "https":
            raise ILinkAPIError(400, "Unsafe media upload URL")
        # CDN upload carries ciphertext only, never the bot Authorization header.
        uploaded = await self._client.post(url, content=encrypted,
            headers={"Content-Type": "application/octet-stream"}, follow_redirects=False)
        if uploaded.status_code != 200:
            raise ILinkAPIError(uploaded.status_code, "Media CDN upload failed")
        download = uploaded.headers.get("x-encrypted-param")
        if not download: raise ILinkAPIError(502, "Missing media download parameter")
        media = {"encrypt_query_param": download, "aes_key": base64.b64encode(key.hex().encode()).decode(), "encrypt_type": 1}
        if kind == "image": return {"type": 2, "image_item": {"media": media, "mid_size": len(encrypted)}}
        return {"type": 4, "file_item": {"media": media, "file_name": filename, "len": str(len(data))}}

    @staticmethod
    def _checked_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code in (401, 403):
            raise ILinkSessionExpiredError(response.status_code, "iLink authentication expired")
        if response.status_code != 200:
            raise ILinkAPIError(response.status_code, "iLink request failed")
        payload = response.json()
        if not isinstance(payload, dict) or any(payload.get(k) not in (None, 0, "0") for k in ("ret", "errcode")):
            raise ILinkAPIError(502, "iLink rejected request")
        return payload

    async def read_media_bytes(
        self,
        url: str,
        *,
        bot_token: str = "",
        max_bytes: int = 8 * 1024 * 1024,
    ) -> tuple[bytes, str]:
        if not _safe_media_url(url):
            raise ILinkAPIError(400, "unsafe media url")
        response = await self._client.get(
            url,
            headers=self._auth_headers(bot_token or None),
            follow_redirects=True,
        )
        if response.status_code in (401, 403):
            raise ILinkSessionExpiredError(response.status_code, response.text)
        if response.status_code != 200:
            raise ILinkAPIError(response.status_code, response.text[:500])
        content = response.content[: max_bytes + 1]
        if len(content) > max_bytes:
            raise ILinkAPIError(413, "media file is too large")
        return content, str(response.headers.get("content-type") or "")

    async def close(self) -> None:
        await self._client.aclose()

    def _auth_headers(self, bot_token: str | None = None) -> dict[str, str]:
        headers = {
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": base64.b64encode(
                str(random.randint(0, 2**32 - 1)).encode()
            ).decode(),
        }
        if bot_token:
            headers["Authorization"] = f"Bearer {bot_token}"
        return headers


def _safe_media_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
