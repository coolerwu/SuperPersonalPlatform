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
        run_service: Any,
        system_log_service: Any = None,
    ) -> None:
        self._workspace = workspace
        self._run_service = run_service
        self._system_log_service = system_log_service
        self._instances: dict[str, WechatChannelService] = {}
        self._lock = asyncio.Lock()

    def parse_accounts(self) -> list[dict[str, Any]]:
        wechat = self._wechat_section()
        accounts = wechat.get("accounts")
        if isinstance(accounts, list) and accounts:
            return [a for a in accounts if isinstance(a, dict) and a.get("id")]
        if wechat.get("enabled") or wechat.get("enabled") is None:
            return [
                {
                    "id": "default",
                    "name": "默认账号",
                    "default_agent_id": wechat.get("default_agent_id", ""),
                    "auto_start": wechat.get("auto_start", False),
                    "proxy": wechat.get("proxy", ""),
                }
            ]
        return []

    async def all_statuses(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for account in self.parse_accounts():
            instance = self._get_or_create_instance(account)
            result.append(self._account_payload(account, await instance.status()))
        return result

    async def account_status(self, account_id: str) -> dict[str, Any]:
        account = self._find_account(account_id)
        instance = self._get_or_create_instance(account)
        return self._account_payload(account, await instance.status())

    async def start_account(self, account_id: str) -> dict[str, Any]:
        account = self._find_account(account_id)
        instance = self._get_or_create_instance(account)
        return self._account_payload(account, await instance.start())

    async def stop_account(self, account_id: str) -> dict[str, Any]:
        account = self._find_account(account_id)
        instance = self._get_or_create_instance(account)
        return self._account_payload(account, await instance.stop())

    async def add_account(self, config: dict[str, Any]) -> dict[str, Any]:
        account_id = str(config.get("id") or "").strip()
        if not _ACCOUNT_ID_RE.match(account_id):
            raise WechatChannelManagerError("账号ID只能包含字母、数字、短横线和下划线，长度1-64")
        name = str(config.get("name") or "").strip() or account_id

        async with self._lock:
            existing = self.parse_accounts()
            if any(account.get("id") == account_id for account in existing):
                raise WechatChannelManagerError(f"账号 {account_id} 已存在")
            next_account: dict[str, Any] = {"id": account_id, "name": name}
            for key in ("default_agent_id", "auto_start", "proxy"):
                if key in config:
                    next_account[key] = config[key]
            self._write_accounts_to_config([*existing, next_account])
        return self._account_metadata(next_account)

    async def update_account(self, account_id: str, config: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            updated: dict[str, Any] | None = None
            accounts: list[dict[str, Any]] = []
            for account in self.parse_accounts():
                if account.get("id") != account_id:
                    accounts.append(dict(account))
                    continue
                updated = dict(account)
                if "name" in config:
                    updated["name"] = str(config.get("name") or "").strip() or account_id
                if "default_agent_id" in config:
                    updated["default_agent_id"] = str(config.get("default_agent_id") or "").strip()
                if "auto_start" in config:
                    updated["auto_start"] = bool(config.get("auto_start"))
                if "proxy" in config:
                    updated["proxy"] = str(config.get("proxy") or "").strip()
                accounts.append(updated)
            if updated is None:
                raise WechatChannelManagerError(f"账号 {account_id} 不存在")
            self._write_accounts_to_config(accounts)
        return self._account_metadata(updated)

    async def remove_account(self, account_id: str) -> None:
        async with self._lock:
            accounts = self.parse_accounts()
            if not any(account.get("id") == account_id for account in accounts):
                raise WechatChannelManagerError(f"账号 {account_id} 不存在")
            if account_id in self._instances:
                instance = self._instances.pop(account_id)
                await instance.stop()
            self._session_path(account_id).unlink(missing_ok=True)
            self._write_accounts_to_config([a for a in accounts if a.get("id") != account_id])

    async def auto_start_all(self) -> None:
        for account in self.parse_accounts():
            if account.get("auto_start"):
                try:
                    await self._get_or_create_instance(account).start()
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
        return str(accounts[0].get("id", "default")) if accounts else None

    def _get_or_create_instance(self, account: dict[str, Any]) -> WechatChannelService:
        account_id = str(account.get("id", "default"))
        if account_id not in self._instances:
            self._instances[account_id] = WechatChannelService(
                workspace=self._workspace,
                run_service=self._run_service,
                system_log_service=self._system_log_service,
                account_id=account_id,
            )
        return self._instances[account_id]

    def _find_account(self, account_id: str) -> dict[str, Any]:
        for account in self.parse_accounts():
            if account.get("id") == account_id:
                return account
        raise WechatChannelManagerError(f"账号 {account_id} 不存在")

    def _account_metadata(self, account: dict[str, Any]) -> dict[str, Any]:
        account_id = str(account.get("id", "default"))
        return {
            "id": account_id,
            "name": account.get("name", account_id),
            "default_agent_id": account.get("default_agent_id", ""),
            "auto_start": account.get("auto_start", False),
            "proxy": account.get("proxy", ""),
        }

    def _account_payload(self, account: dict[str, Any], status: WechatChannelStatus) -> dict[str, Any]:
        return {**self._account_metadata(account), "status": _status_dict(status)}

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
        tmp_path = config_path.with_suffix(".yaml.tmp")
        tmp_path.write_text(
            yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        tmp_path.replace(config_path)

    def _session_path(self, account_id: str) -> Path:
        return self._workspace / "channels" / "wechat" / "sessions" / f"{account_id}.json"


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
