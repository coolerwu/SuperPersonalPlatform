"""出站通知：把高优先级行情信号推送到 Telegram。

只做出站单向推送，不接入平台的渠道 / session / 投递体系，
因此不会影响微信通道和 Run 的 delivery 语义。
"""

from __future__ import annotations

import httpx

from server.domain.market import TelegramNotifyConfig

DEFAULT_TIMEOUT_SECONDS = 10.0


class TelegramNotifier:
    def __init__(self, config: TelegramNotifyConfig, proxy: str = "") -> None:
        self._config = config
        self._proxy = proxy

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled and self._config.bot_token and self._config.chat_id)

    async def send(self, text: str) -> tuple[bool, str]:
        """返回 (是否送达, 说明)。失败只作为观察结果返回，不向上抛。"""
        if not self.enabled:
            return False, "telegram notification is disabled"
        url = f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage"
        payload = {
            "chat_id": self._config.chat_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }
        kwargs: dict[str, object] = {
            "timeout": httpx.Timeout(DEFAULT_TIMEOUT_SECONDS, connect=DEFAULT_TIMEOUT_SECONDS / 2)
        }
        if self._proxy:
            kwargs["proxy"] = self._proxy
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            return False, f"telegram request failed: {exc}"
        if response.status_code >= 400:
            return False, f"telegram rejected the message: HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            return False, "telegram response is not JSON"
        if not isinstance(body, dict) or not body.get("ok"):
            description = body.get("description") if isinstance(body, dict) else ""
            return False, f"telegram returned ok=false: {description}"
        return True, ""
