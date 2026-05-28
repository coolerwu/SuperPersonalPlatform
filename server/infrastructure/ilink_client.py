from __future__ import annotations

import base64
import io
import random
from typing import Any

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
        try:
            result = response.json()
        except Exception:
            result = {}
        result.setdefault("_debug_url", url)
        result.setdefault("_debug_status", response.status_code)
        result.setdefault("_debug_body", _json.dumps(body, ensure_ascii=False)[:500])
        result.setdefault("_debug_raw", raw[:500])
        return result

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
