from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

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
        self._pending_inputs: dict[str, dict[str, Any]] = {}
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_input_delay_seconds = 5.0

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
        await self._cancel_pending_inputs()
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
                if _message_has_processable_content(msg):
                    await self._process_message(msg)

    async def _process_message(self, msg: dict[str, Any]) -> None:
        text_parts: list[str] = []
        attachments: list[dict[str, Any]] = []
        for item in msg.get("item_list") or []:
            if item.get("type") == 1 and item.get("text_item"):
                text = str(item["text_item"].get("text") or "")
                if text.strip():
                    text_parts.append(text.strip())
                continue
            attachment = await self._image_attachment_from_item(item)
            if attachment:
                attachments.append(attachment)
        text = "\n".join(text_parts).strip()
        if not text and not attachments:
            return

        from_user_id = str(msg.get("from_user_id") or "")
        to_user_id = str(msg.get("to_user_id") or "")
        context_token = str(msg.get("context_token") or "")
        agent_id = str(self._channel_config().get("default_agent_id") or "").strip()
        async with self._lock:
            self._logs.append(
                {
                    "type": "message",
                    "text": text,
                    "attachments": len(attachments),
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                    "context_token": context_token,
                }
            )
        if self._system_log_service:
            self._system_log_service.append_line(f"wechat rx account={self._account_id} text={text[:200]}")

        if not agent_id:
            await self._send_reply(from_user_id, context_token, "微信通道未绑定 Agent。")
            return

        peer_id = _wechat_peer_id(from_user_id, to_user_id)
        peer_type = _wechat_peer_type(peer_id)
        if _is_clear_session_command(text) and not attachments:
            await self._clear_wechat_session(
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                context_token=context_token,
                agent_id=agent_id,
                peer_id=peer_id,
                peer_type=peer_type,
                text=text,
            )
            return
        pending_key = _pending_key(self._account_id, agent_id, peer_type, peer_id)

        await self._queue_pending_message(
            key=pending_key,
            text=text,
            attachments=attachments,
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            context_token=context_token,
            agent_id=agent_id,
            peer_id=peer_id,
            peer_type=peer_type,
        )

    async def _queue_pending_message(
        self,
        *,
        key: str,
        text: str,
        attachments: list[dict[str, Any]],
        from_user_id: str,
        to_user_id: str,
        context_token: str,
        agent_id: str,
        peer_id: str,
        peer_type: str,
    ) -> None:
        async with self._lock:
            pending = self._pending_inputs.get(key)
            if pending is None:
                pending = {
                    "text_parts": [],
                    "attachments": [],
                    "message_count": 0,
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                    "context_token": context_token,
                    "agent_id": agent_id,
                    "peer_id": peer_id,
                    "peer_type": peer_type,
                }
                self._pending_inputs[key] = pending
            if text.strip():
                pending["text_parts"].append(text.strip())
            pending["attachments"].extend(attachments)
            pending["message_count"] += 1
            pending["from_user_id"] = from_user_id
            pending["to_user_id"] = to_user_id
            pending["context_token"] = context_token
            pending["agent_id"] = agent_id
            pending["peer_id"] = peer_id
            pending["peer_type"] = peer_type

            old_task = self._pending_tasks.get(key)
            if old_task and not old_task.done():
                old_task.cancel()
            self._pending_tasks[key] = asyncio.create_task(self._flush_pending_input_after_delay(key))
            self._logs.append(
                {
                    "type": "message_pending",
                    "texts": len(pending["text_parts"]),
                    "attachments": len(pending["attachments"]),
                    "message_count": pending["message_count"],
                    "peer_id": peer_id,
                    "context_token": context_token,
                }
            )

    async def _flush_pending_input_after_delay(self, key: str) -> None:
        try:
            await asyncio.sleep(self._pending_input_delay_seconds)
            pending = await self._take_pending_input(key)
            if not pending:
                return
            text = "\n".join(str(part) for part in pending["text_parts"] if str(part).strip()).strip()
            image_only = not text and bool(pending["attachments"])
            if image_only:
                text = "用户发送了一张图片。"
            if not text and not pending["attachments"]:
                return
            await self._execute_wechat_run(
                text=text,
                attachments=tuple(pending["attachments"]),
                from_user_id=str(pending["from_user_id"]),
                to_user_id=str(pending["to_user_id"]),
                context_token=str(pending["context_token"]),
                agent_id=str(pending["agent_id"]),
                peer_id=str(pending["peer_id"]),
                peer_type=str(pending["peer_type"]),
                metadata_extra={
                    "batched_messages": int(pending["message_count"]),
                    "merged_pending_images": len(pending["attachments"]),
                    "image_only_flush": image_only,
                    "delay_seconds": self._pending_input_delay_seconds,
                },
            )
        except asyncio.CancelledError:
            pass

    async def _take_pending_input(self, key: str) -> dict[str, Any] | None:
        async with self._lock:
            pending = self._pending_inputs.pop(key, None)
            task = self._pending_tasks.pop(key, None)
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
            return pending

    async def _cancel_pending_inputs(self) -> None:
        async with self._lock:
            tasks = list(self._pending_tasks.values())
            self._pending_tasks.clear()
            self._pending_inputs.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _execute_wechat_run(
        self,
        *,
        text: str,
        attachments: tuple[dict[str, Any], ...],
        from_user_id: str,
        to_user_id: str,
        context_token: str,
        agent_id: str,
        peer_id: str,
        peer_type: str,
        metadata_extra: dict[str, Any] | None = None,
    ) -> None:
        try:
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
            metadata = {
                "account_id": self._account_id,
                "peer_id": peer_id,
                "peer_type": peer_type,
                "from_user_id": from_user_id,
                "to_user_id": to_user_id,
                "context_token": context_token,
            }
            if metadata_extra:
                metadata.update(metadata_extra)
            run = await self._run_service.create_run(
                content=text,
                agent_id=agent_id,
                source="wechat",
                session_id=session_id,
                attachments=tuple(attachments),
                metadata=metadata,
            )
            completed = await self._run_service.execute_run(str(run["run_id"]))
            result = completed.get("result") or {}
            reply = str(result.get("content") or result.get("error") or "任务没有返回内容")
        except Exception as exc:
            reply = f"DeepAgent 处理失败：{exc}"
            async with self._lock:
                self._logs.append({"type": "error", "error": reply})

        await self._send_reply(from_user_id, context_token, reply)

    async def _clear_wechat_session(
        self,
        *,
        from_user_id: str,
        to_user_id: str,
        context_token: str,
        agent_id: str,
        peer_id: str,
        peer_type: str,
        text: str,
    ) -> None:
        if self._session_service is None:
            await self._send_reply(from_user_id, context_token, "当前没有启用长期会话。")
            return
        session = self._session_service.clear_active(
            channel="wechat",
            channel_account_id=self._account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
            reason=text,
            metadata={"to_user_id": to_user_id},
        )
        async with self._lock:
            self._logs.append(
                {
                    "type": "session_cleared",
                    "session_id": session.session_id,
                    "peer_id": peer_id,
                    "peer_type": peer_type,
                }
            )
        if self._system_log_service:
            self._system_log_service.append_line(
                f"wechat session cleared account={self._account_id} peer={peer_type}:{peer_id} session={session.session_id}"
            )
        await self._send_reply(from_user_id, context_token, "已清空上下文，并开启新的会话。")

    async def _image_attachment_from_item(self, item: dict[str, Any]) -> dict[str, Any] | None:
        image_payload = _image_payload(item)
        if image_payload is None:
            return None
        media_payload = _preferred_image_media(image_payload)
        attachment_id = str(
            image_payload.get("id")
            or image_payload.get("media_id")
            or image_payload.get("file_id")
            or image_payload.get("md5")
            or image_payload.get("aes_key")
            or "wechat_image"
        )
        filename = str(
            image_payload.get("filename")
            or image_payload.get("file_name")
            or image_payload.get("name")
            or f"{attachment_id}.jpg"
        )
        media_mime = media_payload.get("mime") if media_payload else ""
        mime = str(
            image_payload.get("mime")
            or image_payload.get("mime_type")
            or image_payload.get("content_type")
            or media_mime
            or mimetypes.guess_type(filename)[0]
            or "image/jpeg"
        )
        data_url = str(image_payload.get("data_url") or image_payload.get("dataUrl") or "")
        if data_url.startswith("data:image"):
            return {"id": attachment_id, "type": "image", "mime": mime, "filename": filename, "data_url": data_url}

        for key in ("content_base64", "base64", "data", "content", "file_content", "fileContent"):
            value = image_payload.get(key)
            if isinstance(value, str) and value.strip():
                raw = value.strip()
                if raw.startswith("data:image"):
                    return {"id": attachment_id, "type": "image", "mime": mime, "filename": filename, "data_url": raw}
                try:
                    base64.b64decode(raw, validate=False)
                except Exception:
                    continue
                return {
                    "id": attachment_id,
                    "type": "image",
                    "mime": mime,
                    "filename": filename,
                    "content_base64": raw,
                }

        media_url = _image_url(image_payload)
        if media_url and self._client:
            try:
                url = urljoin(self._baseurl, media_url)
                content, content_type = await self._client.read_media_bytes(url, bot_token=self._bot_token)
                content = _decrypt_wechat_media(content, _image_aes_key(image_payload, media_payload))
                actual_mime = _guess_image_mime_from_bytes(content) or (content_type.split(";", 1)[0] if content_type else "")
                return {
                    "id": attachment_id,
                    "type": "image",
                    "mime": actual_mime or mime,
                    "filename": _ensure_image_filename(filename, actual_mime or mime),
                    "bytes": content,
                }
            except Exception as exc:
                await self._append_image_warning(f"download failed: {exc.__class__.__name__}: {exc}")
        return None

    async def _append_image_warning(self, message: str) -> None:
        async with self._lock:
            self._logs.append({"type": "image_warning", "error": message})
        if self._system_log_service:
            self._system_log_service.append_line(f"wechat image account={self._account_id} {message[:240]}")

    async def _send_reply(self, to_user_id: str, context_token: str, text: str) -> None:
        try:
            await self.deliver_text(to_user_id=to_user_id, context_token=context_token, text=text)
        except Exception as exc:
            async with self._lock:
                self._logs.append({"type": "error", "error": f"send reply failed: {exc}"})

    async def deliver_text(self, *, to_user_id: str, context_token: str, text: str) -> dict[str, Any]:
        if not to_user_id:
            raise RuntimeError("wechat to_user_id is required")
        await self._ensure_delivery_client()
        resp = await self._client.send_message(  # type: ignore[union-attr]
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
        if self._system_log_service:
            status = resp.get("_debug_status", "") if isinstance(resp, dict) else ""
            self._system_log_service.append_line(
                f"wechat tx account={self._account_id} to={to_user_id} status={status} text={text[:200]}"
            )
        return resp if isinstance(resp, dict) else {}

    async def _ensure_delivery_client(self) -> None:
        if not self._client or not self._baseurl or not self._bot_token:
            proxy = self._channel_config().get("proxy", "")
            if self._client is None:
                self._client = ILinkClient(proxy=str(proxy).strip() if proxy else None)
            if not self._baseurl or not self._bot_token:
                resumed = await self._try_resume_session()
                if not resumed:
                    raise RuntimeError("wechat account is not logged in")

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
        return self._workspace / "channels" / "wechat" / "sessions" / f"{self._account_id}.json"

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


def _pending_key(account_id: str, agent_id: str, peer_type: str, peer_id: str) -> str:
    return f"{account_id}:{agent_id}:{peer_type}:{peer_id}"


def _message_has_processable_content(msg: dict[str, Any]) -> bool:
    for item in msg.get("item_list") or []:
        if item.get("type") == 1 and item.get("text_item"):
            if str(item["text_item"].get("text") or "").strip():
                return True
        if _image_payload(item) is not None:
            return True
    return False


def _is_clear_session_command(text: str) -> bool:
    normalized = "".join(str(text or "").strip().lower().split())
    return normalized in {
        "/clear",
        "/new",
        "clear",
        "new",
        "清空",
        "清空上下文",
        "清空会话",
        "重置上下文",
        "重置会话",
        "开启新会话",
        "开始新会话",
        "新会话",
    }


def _image_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    for key in (
        "image_item",
        "img_item",
        "picture_item",
        "pic_item",
        "media_item",
        "file_item",
    ):
        payload = item.get(key)
        if isinstance(payload, dict) and _payload_looks_like_image(payload):
            return payload
    if _payload_looks_like_image(item):
        return item
    return None


def _payload_looks_like_image(payload: dict[str, Any]) -> bool:
    mime = str(payload.get("mime") or payload.get("mime_type") or payload.get("content_type") or "").lower()
    if mime.startswith("image/"):
        return True
    filename = str(payload.get("filename") or payload.get("file_name") or payload.get("name") or "").lower()
    if Path(filename).suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}:
        return True
    data_url = str(payload.get("data_url") or payload.get("dataUrl") or payload.get("content") or "")
    if data_url.startswith("data:image"):
        return True
    if any(payload.get(key) for key in ("image_url", "imageUrl", "thumb_url", "thumbUrl")):
        return True
    if any(isinstance(payload.get(key), dict) for key in ("media", "thumb_media", "thumbMedia")):
        return True
    if any(payload.get(key) for key in ("aeskey", "aes_key", "mid_size", "thumb_size", "hd_size")):
        return True
    return False


def _image_url(payload: dict[str, Any]) -> str:
    for media in (_preferred_image_media(payload),):
        if media:
            direct = _media_direct_url(media)
            if direct:
                return direct
            query = _media_encrypt_query_param(media)
            if query:
                return _wechat_cdn_download_url(query)
    for key in (
        "download_url",
        "downloadUrl",
        "url",
        "cdn_url",
        "cdnUrl",
        "image_url",
        "imageUrl",
        "file_url",
        "fileUrl",
        "thumb_url",
        "thumbUrl",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _preferred_image_media(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("media", "Media", "thumb_media", "thumbMedia"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return None


def _media_direct_url(media: dict[str, Any]) -> str:
    for key in (
        "full_url",
        "fullUrl",
        "download_url",
        "downloadUrl",
        "url",
        "cdn_url",
        "cdnUrl",
        "file_url",
        "fileUrl",
    ):
        value = media.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _media_encrypt_query_param(media: dict[str, Any]) -> str:
    for key in (
        "encrypt_query_param",
        "encryptQueryParam",
        "encrypted_query_param",
        "encryptedQueryParam",
    ):
        value = media.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _wechat_cdn_download_url(encrypt_query_param: str) -> str:
    value = encrypt_query_param.strip()
    if value.startswith(("http://", "https://")):
        return value
    if "encrypted_query_param=" in value:
        query = value.lstrip("?")
    else:
        query = f"encrypted_query_param={quote(value, safe='')}"
    return f"https://novac2c.cdn.weixin.qq.com/c2c/download?{query}"


def _image_aes_key(payload: dict[str, Any], media: dict[str, Any] | None) -> bytes | None:
    candidates: list[Any] = []
    for key in ("aeskey", "aes_key", "AESKey", "aesKey"):
        candidates.append(payload.get(key))
    if media:
        for key in ("aes_key", "aeskey", "AESKey", "aesKey"):
            candidates.append(media.get(key))
    for candidate in candidates:
        decoded = _decode_wechat_aes_key(candidate)
        if decoded:
            return decoded
    return None


def _decode_wechat_aes_key(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if len(raw) == 32 and all(char in "0123456789abcdefABCDEF" for char in raw):
        try:
            return bytes.fromhex(raw)
        except ValueError:
            return None
    if len(raw) == 16:
        return raw.encode("utf-8")
    try:
        decoded = base64.b64decode(raw, validate=False)
    except Exception:
        return None
    if len(decoded) == 16:
        return decoded
    try:
        decoded_text = decoded.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if len(decoded_text) == 32 and all(char in "0123456789abcdefABCDEF" for char in decoded_text):
        try:
            return bytes.fromhex(decoded_text)
        except ValueError:
            return None
    return None


def _decrypt_wechat_media(content: bytes, aes_key: bytes | None) -> bytes:
    if not aes_key or _guess_image_mime_from_bytes(content):
        return content
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception:
        return content
    if len(aes_key) != 16:
        return content
    decryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
    decrypted = decryptor.update(content) + decryptor.finalize()
    if not decrypted:
        return content
    padding = decrypted[-1]
    if 1 <= padding <= 16 and decrypted.endswith(bytes([padding]) * padding):
        decrypted = decrypted[:-padding]
    return decrypted or content


def _guess_image_mime_from_bytes(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.lstrip().startswith(b"<svg"):
        return "image/svg+xml"
    return ""


def _ensure_image_filename(filename: str, mime: str) -> str:
    suffix = Path(filename).suffix
    if suffix:
        return filename
    extension = mimetypes.guess_extension(mime) or ".jpg"
    return f"{filename}{extension}"
