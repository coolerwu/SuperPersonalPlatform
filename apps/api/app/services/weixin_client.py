"""Minimal Tencent iLink Bot API client for personal WeChat accounts.

The protocol flow (QR login, ``getupdates`` long polling, ``sendmessage`` with
the latest per-peer ``context_token``, and the iLink headers) follows
Nous Research's hermes-agent Weixin adapter, which is MIT licensed. See
``THIRD_PARTY_NOTICES.md``. Only text messages are handled here.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import uuid
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

ITEM_TEXT = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

QR_STATUSES = {"wait", "scaned", "scaned_but_redirect", "expired", "confirmed"}


class WeixinError(RuntimeError):
    pass


class WeixinApiError(WeixinError):
    def __init__(self, message: str, *, ret: Any = None, errcode: Any = None) -> None:
        super().__init__(message)
        self.ret = ret
        self.errcode = errcode


class WeixinSessionExpired(WeixinApiError):
    """The bot token/session needs a fresh QR login."""


class WeixinRateLimited(WeixinApiError):
    """iLink asked us to slow down; retry after a backoff."""


def _safe_id(value: str, keep: int = 8) -> str:
    value = str(value or "")
    return value[:keep] if value else "?"


def _uin_header() -> str:
    return base64.b64encode(str(int.from_bytes(secrets.token_bytes(4), "big")).encode()).decode()


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _uin_header(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_stale_session(ret: Any, errcode: Any, errmsg: str) -> bool:
    lowered = (errmsg or "").strip().lower()
    return -2 in (ret, errcode) and lowered in {"unknown error", "prepare failed"}


def _raise_for_result(data: dict[str, Any]) -> None:
    ret = data.get("ret", 0)
    errcode = data.get("errcode", 0)
    if ret in (0, None) and errcode in (0, None):
        return
    errmsg = str(data.get("errmsg") or data.get("msg") or "iLink request failed")
    if -14 in (ret, errcode) or _is_stale_session(ret, errcode, errmsg):
        raise WeixinSessionExpired(
            f"{errmsg} — the WeChat login has expired; scan the QR code again",
            ret=ret,
            errcode=errcode,
        )
    if -2 in (ret, errcode):
        raise WeixinRateLimited(errmsg, ret=ret, errcode=errcode)
    raise WeixinApiError(errmsg, ret=ret, errcode=errcode)


def extract_text(item_list: list[dict[str, Any]] | None) -> str:
    for item in item_list or []:
        if item.get("type") != ITEM_TEXT:
            continue
        text = str((item.get("text_item") or {}).get("text") or "")
        ref = item.get("ref_msg") or {}
        ref_item = ref.get("message_item") or {}
        if ref_item.get("type") == ITEM_TEXT:
            quoted = extract_text([ref_item])
            if quoted:
                title = str(ref.get("title") or "").strip()
                prefix = f"[引用: {title}]" if title else "[引用]"
                return f"{prefix}\n{text}".strip()
        return text
    return ""


def is_group_message(message: dict[str, Any]) -> bool:
    return bool(message.get("room_id") or message.get("chat_room_id"))


def is_self_message(message: dict[str, Any], account_id: str) -> bool:
    return bool(account_id) and str(message.get("from_user_id") or "") == account_id


def qr_svg(scan_url: str) -> str:
    """Render the scan URL as an inline SVG for the web QR dialog."""
    if not scan_url:
        return ""
    try:
        import segno

        return segno.make(scan_url, error="m").svg_inline(omitsize=True)
    except Exception:
        logger.exception("failed to render WeChat QR code")
        return ""


class WeixinClient:
    """One client per channel; the runtime shares it for poll and send."""

    def __init__(
        self,
        *,
        account_id: str = "",
        token: str = "",
        base_url: str = ILINK_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.account_id = account_id
        self.token = token
        self.base_url = (base_url or ILINK_BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                connect=15.0,
                read=60.0,
                write=30.0,
                pool=15.0,
            ),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "WeixinClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        base_url: str | None = None,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
        timeout_ms: int = 15_000,
    ) -> dict[str, Any]:
        url = f"{(base_url or self.base_url).rstrip('/')}/{endpoint}"
        headers = _headers(token)
        request_kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout_ms / 1000}
        if payload is not None:
            body = json.dumps(
                {**payload, "base_info": {"channel_version": CHANNEL_VERSION}},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            request_kwargs["content"] = body.encode("utf-8")
        try:
            response = await self._client.request(method, url, **request_kwargs)
        except httpx.TimeoutException:
            raise
        except httpx.HTTPError as exc:
            raise WeixinApiError(f"iLink {method} {endpoint} failed: {exc}") from exc
        if response.status_code >= 400:
            raise WeixinApiError(
                f"iLink {method} {endpoint} HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise WeixinApiError(f"iLink {method} {endpoint} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise WeixinApiError(f"iLink {method} {endpoint} returned a non-object payload")
        return data

    async def fetch_qr(self, bot_type: str = "3") -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"{EP_GET_BOT_QR}?bot_type={bot_type}",
            token=None,
            timeout_ms=settings.weixin_qr_timeout_ms,
        )
        _raise_for_result(data)
        return data

    async def qr_status(self, qrcode: str, *, base_url: str | None = None) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"{EP_GET_QR_STATUS}?qrcode={qrcode}",
            base_url=base_url,
            token=None,
            timeout_ms=settings.weixin_qr_timeout_ms,
        )
        _raise_for_result(data)
        return data

    async def get_updates(self, sync_buf: str) -> dict[str, Any]:
        if not self.token:
            raise WeixinApiError("weixin bot token is missing")
        try:
            data = await self._request(
                "POST",
                EP_GET_UPDATES,
                payload={"get_updates_buf": sync_buf or ""},
                token=self.token,
                timeout_ms=settings.weixin_long_poll_timeout_ms,
            )
        except httpx.TimeoutException:
            # Normal long-poll expiry: no messages arrived in the window.
            return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}
        _raise_for_result(data)
        return data

    async def send_text(
        self,
        *,
        to_user_id: str,
        text: str,
        context_token: str | None = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.token:
            raise WeixinApiError("weixin bot token is missing")
        if not text.strip():
            raise ValueError("text must not be empty")
        message: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id or f"acc-{uuid.uuid4().hex}",
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
        }
        if context_token:
            message["context_token"] = context_token
        data = await self._request(
            "POST",
            EP_SEND_MESSAGE,
            payload={"msg": message},
            token=self.token,
            timeout_ms=settings.weixin_api_timeout_ms,
        )
        _raise_for_result(data)
        return data
