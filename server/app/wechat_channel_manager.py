from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import yaml

from server.app.wechat_channel_service import WechatChannelService, WechatChannelStatus


_ACCOUNT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class WechatChannelManagerError(Exception):
    pass


class WechatChannelManager:
    def __init__(
        self,
        workspace: Path,
        agent_chat_service: Any = None,
        system_log_service: Any = None,
        chat_session_service: Any = None,
    ) -> None:
        self._workspace = workspace
        self._agent_chat_service = agent_chat_service
        self._chat_session_service = chat_session_service
        self._system_log_service = system_log_service
        self._instances: dict[str, WechatChannelService] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # config reading
    # ------------------------------------------------------------------

    def _read_raw_config(self) -> dict[str, Any]:
        path = self._workspace / "config.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _wechat_section(self) -> dict[str, Any]:
        raw = self._read_raw_config()
        channels = raw.get("channels") if isinstance(raw, dict) else {}
        wechat = channels.get("wechat_personal") if isinstance(channels, dict) else {}
        return wechat if isinstance(wechat, dict) else {}

    def parse_accounts(self) -> list[dict[str, Any]]:
        wechat = self._wechat_section()
        accounts = wechat.get("accounts")
        if isinstance(accounts, list) and len(accounts) > 0:
            return [a for a in accounts if isinstance(a, dict) and a.get("id")]
        if wechat.get("enabled") or wechat.get("enabled") is None:
            return [{
                "id": "default",
                "name": "默认账号",
                "default_agent_id": wechat.get("default_agent_id", ""),
                "auto_start": wechat.get("auto_start", False),
                "proxy": wechat.get("proxy", ""),
            }]
        return []

    # ------------------------------------------------------------------
    # instance management
    # ------------------------------------------------------------------

    def _get_or_create_instance(self, account_config: dict[str, Any]) -> WechatChannelService:
        account_id = account_config.get("id", "default")
        if account_id not in self._instances:
            self._instances[account_id] = WechatChannelService(
                workspace=self._workspace,
                agent_chat_service=self._agent_chat_service,
                chat_session_service=self._chat_session_service,
                system_log_service=self._system_log_service,
                account_id=account_id,
            )
        return self._instances[account_id]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def all_statuses(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for acct in self.parse_accounts():
            account_id = acct.get("id", "default")
            instance = self._get_or_create_instance(acct)
            status = await instance.status()
            result.append(self._account_payload(acct, status))
        return result

    async def account_status(self, account_id: str) -> dict[str, Any]:
        acct = self._find_account(account_id)
        instance = self._get_or_create_instance(acct)
        status = await instance.status()
        return self._account_payload(acct, status)

    async def start_account(self, account_id: str) -> dict[str, Any]:
        acct = self._find_account(account_id)
        instance = self._get_or_create_instance(acct)
        status = await instance.start()
        return self._account_payload(acct, status)

    async def stop_account(self, account_id: str) -> dict[str, Any]:
        acct = self._find_account(account_id)
        instance = self._get_or_create_instance(acct)
        status = await instance.stop()
        return self._account_payload(acct, status)

    async def add_account(self, config: dict[str, Any]) -> dict[str, Any]:
        account_id = str(config.get("id") or "").strip()
        if not _ACCOUNT_ID_RE.match(account_id):
            raise WechatChannelManagerError("账号ID只能包含字母、数字、短横线和下划线，长度1-64")
        name = str(config.get("name") or "").strip() or account_id

        async with self._lock:
            existing = self.parse_accounts()
            for a in existing:
                if a.get("id") == account_id:
                    raise WechatChannelManagerError(f"账号 {account_id} 已存在")
            await self._write_account_list(existing, account_id, name, config)

        return self._account_metadata({"id": account_id, "name": name, **{
            key: config[key] for key in ("default_agent_id", "auto_start", "proxy") if key in config
        }})

    async def update_account(self, account_id: str, config: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            existing = self.parse_accounts()
            found = False
            updated_list: list[dict[str, Any]] = []
            updated_account: dict[str, Any] | None = None
            for acct in existing:
                if acct.get("id") != account_id:
                    updated_list.append(dict(acct))
                    continue

                found = True
                next_acct = dict(acct)
                if "name" in config:
                    next_acct["name"] = str(config.get("name") or "").strip() or account_id
                if "default_agent_id" in config:
                    next_acct["default_agent_id"] = str(config.get("default_agent_id") or "").strip()
                if "auto_start" in config:
                    next_acct["auto_start"] = bool(config.get("auto_start"))
                if "proxy" in config:
                    next_acct["proxy"] = str(config.get("proxy") or "").strip()

                updated_account = next_acct
                updated_list.append(next_acct)

            if not found or updated_account is None:
                raise WechatChannelManagerError(f"账号 {account_id} 不存在")

            self._write_accounts_to_config(updated_list)

        return self._account_metadata(updated_account)

    async def remove_account(self, account_id: str) -> None:
        async with self._lock:
            existing = self.parse_accounts()
            found = None
            remaining: list[dict[str, Any]] = []
            for a in existing:
                if a.get("id") == account_id:
                    found = a
                else:
                    remaining.append(a)
            if found is None:
                raise WechatChannelManagerError(f"账号 {account_id} 不存在")

            if account_id in self._instances:
                instance = self._instances.pop(account_id)
                try:
                    await instance.stop()
                except Exception:
                    pass

            session_path = self._session_path(account_id)
            try:
                session_path.unlink(missing_ok=True)
            except Exception:
                pass

            self._write_accounts_to_config(remaining)

    async def auto_start_all(self) -> None:
        for acct in self.parse_accounts():
            if acct.get("auto_start"):
                instance = self._get_or_create_instance(acct)
                try:
                    await instance.start()
                except Exception:
                    pass

    async def stop_all(self) -> None:
        for instance in list(self._instances.values()):
            try:
                await instance.stop()
            except Exception:
                pass

    def first_account_id(self) -> str | None:
        accounts = self.parse_accounts()
        if accounts:
            return accounts[0].get("id", "default")
        return None

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _find_account(self, account_id: str) -> dict[str, Any]:
        for acct in self.parse_accounts():
            if acct.get("id") == account_id:
                return acct
        raise WechatChannelManagerError(f"账号 {account_id} 不存在")

    def _account_metadata(self, acct: dict[str, Any]) -> dict[str, Any]:
        account_id = acct.get("id", "default")
        return {
            "id": account_id,
            "name": acct.get("name", account_id),
            "default_agent_id": acct.get("default_agent_id", ""),
            "auto_start": acct.get("auto_start", False),
            "proxy": acct.get("proxy", ""),
        }

    def _account_payload(self, acct: dict[str, Any], status: WechatChannelStatus) -> dict[str, Any]:
        return {**self._account_metadata(acct), "status": _status_dict(status)}

    async def _write_account_list(
        self,
        existing: list[dict[str, Any]],
        account_id: str,
        name: str,
        config: dict[str, Any],
    ) -> None:
        new_account: dict[str, Any] = {"id": account_id, "name": name}
        for key in ("default_agent_id", "auto_start", "proxy"):
            if key in config:
                new_account[key] = config[key]
        new_list = [dict(a) for a in existing]
        new_list.append(new_account)
        self._write_accounts_to_config(new_list)

    def _write_accounts_to_config(self, accounts: list[dict[str, Any]]) -> None:
        raw = self._read_raw_config()
        channels = raw.get("channels") if isinstance(raw, dict) else {}
        if not isinstance(channels, dict):
            channels = {}
        wechat = channels.get("wechat_personal") if isinstance(channels, dict) else {}
        if not isinstance(wechat, dict):
            wechat = {}
        wechat["accounts"] = accounts
        channels["wechat_personal"] = wechat
        raw["channels"] = channels

        config_path = self._workspace / "config.yaml"
        content = yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False)
        tmp_path = config_path.with_suffix(".yaml.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(config_path)

    def _session_path(self, account_id: str) -> Path:
        return self._workspace / ".run" / f"wechat_session_{account_id}.json"


def _status_dict(status: WechatChannelStatus) -> dict[str, Any]:
    return {
        "running": status.running,
        "login_state": status.login_state,
        "qrcode_url": status.qrcode_url,
        "qrcode_data_url": status.qrcode_data_url,
        "qrcode_status": status.qrcode_status,
        "user": status.user,
        "error": status.error,
    }
