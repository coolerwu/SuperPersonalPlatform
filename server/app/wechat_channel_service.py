from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from server.infrastructure.ilink_client import (
    ILinkAPIError,
    ILinkClient,
    ILinkSessionExpiredError,
    generate_qrcode_data_url,
)


@dataclass(frozen=True)
class WechatChannelStatus:
    running: bool
    login_state: str
    qrcode_url: str
    qrcode_data_url: str
    qrcode_status: str
    user: str
    error: str
    logs: tuple[dict[str, Any], ...]


class WechatChannelService:
    def __init__(
        self,
        workspace: Path,
        run_service: Any,
        session_service: Any = None,
        system_log_service: Any = None,
        account_id: str = "default",
    ) -> None:
        self._workspace = workspace
        self._run_service = run_service
        self._session_service = session_service
        self._system_log_service = system_log_service
        self._account_id = account_id
        self._client: ILinkClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._login_state = "stopped"
        self._qrcode_url = ""
        self._qrcode_data_url = ""
        self._qrcode_status = ""
        self._user = ""
        self._error = ""
        self._logs: deque[dict[str, Any]] = deque(maxlen=80)
        self._bot_token = ""
        self._baseurl = ""

    async def status(self) -> WechatChannelStatus:
        async with self._lock:
            return self._snapshot_locked()

    async def start(self) -> WechatChannelStatus:
        async with self._lock:
            if self._task and not self._task.done():
                return self._snapshot_locked()
            self._login_state = "connecting"
            self._error = ""
            self._qrcode_url = ""
            self._qrcode_data_url = ""
            self._qrcode_status = ""
            self._user = ""
            self._bot_token = ""
            self._baseurl = ""
            self._task = asyncio.create_task(self._run())
            return self._snapshot_locked()

    async def stop(self) -> WechatChannelStatus:
        async with self._lock:
            task = self._task
            self._task = None
            self._login_state = "stopped"
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_client()
        async with self._lock:
            return self._snapshot_locked()

    async def _run(self) -> None:
        try:
            while True:
                if not await self._try_resume_session():
                    await self._login_phase()
                else:
                    proxy = self._channel_config().get("proxy", "")
                    self._client = ILinkClient(proxy=str(proxy).strip() if proxy else None)
                await self._message_loop()
        except asyncio.CancelledError:
            async with self._lock:
                self._login_state = "stopped"
            raise
        except Exception as exc:
            async with self._lock:
                self._login_state = "exited"
                self._error = str(exc)
                self._logs.append({"type": "error", "error": str(exc)})

    async def _try_resume_session(self) -> bool:
        session = self._load_session()
        if not session:
            return False
        bot_token = str(session.get("bot_token") or "")
        baseurl = str(session.get("baseurl") or "")
        if not bot_token or not baseurl:
            return False
        async with self._lock:
            self._bot_token = bot_token
            self._baseurl = baseurl
            self._login_state = "logged_in"
            self._user = "微信用户"
            self._logs.append({"type": "login", "user": self._user, "resumed": True})
        return True

    async def _login_phase(self) -> None:
        proxy = self._channel_config().get("proxy", "")
        self._client = ILinkClient(proxy=str(proxy).strip() if proxy else None)
        try:
            qr_response = await self._client.get_bot_qrcode()
        except Exception as exc:
            async with self._lock:
                self._error = str(exc)
                self._login_state = "exited"
            return

        qrcode_str = str(qr_response.get("qrcode") or "")
        qrcode_img_content = str(qr_response.get("qrcode_img_content") or "")
        qrcode_data_url = ""
        if qrcode_img_content and qrcode_img_content.startswith("data:image"):
            qrcode_data_url = qrcode_img_content
        elif qrcode_img_content or qrcode_str:
            try:
                qrcode_data_url = generate_qrcode_data_url(qrcode_img_content or qrcode_str)
            except Exception:
                pass

        async with self._lock:
            self._login_state = "scan"
            self._qrcode_url = qrcode_str
            self._qrcode_data_url = qrcode_data_url
            self._qrcode_status = "等待扫码"
            self._logs.append({"type": "scan", "status": "waiting", "qrcode": qrcode_str})

        while True:
            try:
                status_response = await self._client.get_qrcode_status(qrcode_str)
            except Exception as exc:
                async with self._lock:
                    self._error = str(exc)
                    self._login_state = "exited"
                    self._logs.append({"type": "error", "error": str(exc)})
                return

            status = str(status_response.get("status") or status_response.get("state") or "")
            async with self._lock:
                self._qrcode_status = status

            if status in ("confirmed", "200", "success", "ok"):
                bot_token = str(
                    status_response.get("bot_token")
                    or status_response.get("token")
                    or status_response.get("botToken")
                    or ""
                )
                baseurl = str(
                    status_response.get("baseurl")
                    or status_response.get("base_url")
                    or status_response.get("baseUrl")
                    or ""
                )
                if not bot_token:
                    await asyncio.sleep(2)
                    continue
                async with self._lock:
                    self._bot_token = bot_token
                    self._baseurl = baseurl
                    self._login_state = "logged_in"
                    self._user = "微信用户"
                    self._qrcode_url = ""
                    self._qrcode_data_url = ""
                    self._logs.append({"type": "login", "user": self._user})
                self._save_session(bot_token, baseurl)
                return

            if status in ("expired", "cancelled", "timeout", "408", "fail"):
                async with self._lock:
                    self._error = f"扫码{status}"
                    self._login_state = "exited"
                    self._logs.append({"type": "error", "error": f"qr_status: {status}"})
                return

            await asyncio.sleep(1.5)

    async def _message_loop(self) -> None:
        cursor = ""
        consecutive_errors = 0
        while True:
            if self._client is None:
                return
            try:
                updates = await self._client.get_updates(self._baseurl, self._bot_token, cursor)
            except ILinkSessionExpiredError:
                self._delete_session()
                async with self._lock:
                    self._login_state = "exited"
                    self._error = "会话已过期，需要重新扫码"
                    self._logs.append({"type": "error", "error": "session expired"})
                return
            except ILinkAPIError as exc:
                consecutive_errors += 1
                if consecutive_errors > 3:
                    async with self._lock:
                        self._error = str(exc)
                        self._login_state = "exited"
                    return
                await asyncio.sleep(min(consecutive_errors * 2, 10))
                continue
            except Exception:
                consecutive_errors += 1
                if consecutive_errors > 5:
                    async with self._lock:
                        self._login_state = "exited"
                        self._logs.append({"type": "error", "error": "network error"})
                    return
                await asyncio.sleep(min(consecutive_errors * 2, 10))
                continue

            consecutive_errors = 0
            cursor = str(updates.get("get_updates_buf") or cursor)
            for msg in updates.get("msgs") or []:
                if msg.get("message_type") == 1:
                    await self._process_message(msg)

    async def _process_message(self, msg: dict[str, Any]) -> None:
        text = ""
        for item in msg.get("item_list") or []:
            if item.get("type") == 1 and item.get("text_item"):
                text = str(item["text_item"].get("text") or "")
                break
        if not text.strip():
            return

        from_user_id = str(msg.get("from_user_id") or "")
        to_user_id = str(msg.get("to_user_id") or "")
        context_token = str(msg.get("context_token") or "")
        async with self._lock:
            self._logs.append(
                {
                    "type": "message",
                    "text": text,
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                    "context_token": context_token,
                }
            )
        if self._system_log_service:
            self._system_log_service.append_line(f"wechat rx account={self._account_id} text={text[:200]}")

        agent_id = str(self._channel_config().get("default_agent_id") or "").strip()
        if not agent_id:
            await self._send_reply(from_user_id, context_token, "微信通道未绑定 Agent。")
            return

        try:
            peer_id = _wechat_peer_id(from_user_id, to_user_id)
            peer_type = _wechat_peer_type(peer_id)
            session_id = ""
            if self._session_service is not None:
                session = self._session_service.get_or_create(
                    channel="wechat",
                    channel_account_id=self._account_id,
                    peer_type=peer_type,
                    peer_id=peer_id,
                    agent_id=agent_id,
                    metadata={"to_user_id": to_user_id},
                )
                session_id = session.session_id
            run = await self._run_service.create_run(
                content=text,
                agent_id=agent_id,
                source="wechat",
                session_id=session_id,
                metadata={
                    "account_id": self._account_id,
                    "peer_id": peer_id,
                    "peer_type": peer_type,
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                    "context_token": context_token,
                },
            )
            completed = await self._run_service.execute_run(str(run["run_id"]))
            result = completed.get("result") or {}
            reply = str(result.get("content") or result.get("error") or "任务没有返回内容")
        except Exception as exc:
            reply = f"DeepAgent 处理失败：{exc}"
            async with self._lock:
                self._logs.append({"type": "error", "error": reply})

        await self._send_reply(from_user_id, context_token, reply)

    async def _send_reply(self, to_user_id: str, context_token: str, text: str) -> None:
        if not self._client or not self._baseurl or not self._bot_token:
            return
        try:
            resp = await self._client.send_message(
                self._baseurl,
                self._bot_token,
                {
                    "to_user_id": to_user_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                },
            )
        except Exception as exc:
            async with self._lock:
                self._logs.append({"type": "error", "error": f"send reply failed: {exc}"})
            return

        if self._system_log_service:
            status = resp.get("_debug_status", "") if isinstance(resp, dict) else ""
            self._system_log_service.append_line(
                f"wechat tx account={self._account_id} to={to_user_id} status={status} text={text[:200]}"
            )

    def _channel_config(self) -> dict[str, Any]:
        raw = self._workspace_config()
        channels = raw.get("channels") if isinstance(raw, dict) else {}
        wechat = channels.get("wechat_personal") if isinstance(channels, dict) else {}
        if not isinstance(wechat, dict):
            return {}
        accounts = wechat.get("accounts")
        if isinstance(accounts, list) and accounts:
            for entry in accounts:
                if isinstance(entry, dict) and entry.get("id") == self._account_id:
                    merged = dict(wechat)
                    merged.pop("accounts", None)
                    merged.update(entry)
                    return merged
            return {}
        return wechat

    def _workspace_config(self) -> dict[str, Any]:
        try:
            raw = yaml.safe_load((self._workspace / "config.yaml").read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _snapshot_locked(self) -> WechatChannelStatus:
        return WechatChannelStatus(
            running=bool(self._task and not self._task.done()),
            login_state=self._login_state,
            qrcode_url=self._qrcode_url,
            qrcode_data_url=self._qrcode_data_url,
            qrcode_status=self._qrcode_status,
            user=self._user,
            error=self._error,
            logs=tuple(self._logs),
        )

    @property
    def _session_path(self) -> Path:
        new_path = self._workspace / "channels" / "wechat" / "sessions" / f"{self._account_id}.json"
        legacy_dir = self._workspace / ".run"
        legacy_path = legacy_dir / "wechat_session.json"
        legacy_account_path = legacy_dir / f"wechat_session_{self._account_id}.json"
        if legacy_account_path.exists() and not new_path.exists():
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_account_path.rename(new_path)
            except Exception:
                pass
        if self._account_id == "default" and legacy_path.exists() and not new_path.exists():
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_path.rename(new_path)
            except Exception:
                pass
        return new_path

    def _save_session(self, bot_token: str, baseurl: str) -> None:
        try:
            self._session_path.parent.mkdir(parents=True, exist_ok=True)
            self._session_path.write_text(
                json.dumps({"bot_token": bot_token, "baseurl": baseurl}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_session(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._session_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("bot_token"):
                return data
        except Exception:
            pass
        return None

    def _delete_session(self) -> None:
        self._session_path.unlink(missing_ok=True)

    async def _close_client(self) -> None:
        client = self._client
        self._client = None
        if client:
            try:
                await client.close()
            except Exception:
                pass


def _wechat_peer_id(from_user_id: str, to_user_id: str) -> str:
    return from_user_id or to_user_id or "unknown"


def _wechat_peer_type(peer_id: str) -> str:
    if "@chatroom" in peer_id:
        return "room"
    return "private"
