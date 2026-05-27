from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from server.app.agent_chat_service import AgentChatService


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
        project_root: Path,
        agent_chat_service: AgentChatService | None = None,
    ) -> None:
        self._workspace = workspace
        self._project_root = project_root
        self._agent_chat_service = agent_chat_service
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._login_state = "stopped"
        self._qrcode_url = ""
        self._qrcode_data_url = ""
        self._qrcode_status = ""
        self._user = ""
        self._error = ""
        self._logs: deque[dict[str, Any]] = deque(maxlen=80)

    def status(self) -> WechatChannelStatus:
        with self._lock:
            return self._snapshot_locked()

    def start(self) -> WechatChannelStatus:
        with self._lock:
            if self._process and self._process.poll() is None:
                return self._snapshot_locked()
            script = self._project_root / "server" / "infrastructure" / "wechat" / "wechaty_sidecar.mjs"
            self._login_state = "starting"
            self._error = ""
            self._qrcode_url = ""
            self._qrcode_data_url = ""
            self._qrcode_status = ""
            env = self._sidecar_env()
            self._process = subprocess.Popen(
                ["node", str(script)],
                cwd=str(script.parent),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            return self._snapshot_locked()

    def stop(self) -> WechatChannelStatus:
        with self._lock:
            process = self._process
            self._process = None
            self._login_state = "stopped"
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        with self._lock:
            return self._snapshot_locked()

    def _read_loop(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"type": "log", "message": line}
            self._handle_event(event)
        with self._lock:
            if self._login_state != "stopped":
                self._login_state = "exited"
                if not self._error:
                    self._error = self._last_error_locked() or f"Wechaty sidecar exited with code {process.poll()}"

    def _handle_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._logs.append(event)
            event_type = str(event.get("type") or "")
            if event_type == "scan":
                self._login_state = "scan"
                self._qrcode_url = str(event.get("qrcode_url") or "")
                self._qrcode_data_url = str(event.get("qrcode_data_url") or "")
                self._qrcode_status = str(event.get("status") or "")
                self._error = ""
            elif event_type == "login":
                self._login_state = "logged_in"
                self._user = str(event.get("user") or "")
                self._qrcode_url = ""
                self._qrcode_data_url = ""
                self._error = ""
            elif event_type == "logout":
                self._login_state = "logged_out"
                self._user = ""
            elif event_type == "error":
                self._error = str(event.get("error") or "")
        if event.get("type") == "message":
            self._handle_message(event)

    def _handle_message(self, event: dict[str, Any]) -> None:
        text = str(event.get("text") or "").strip()
        if not text or not self._message_allowed(event):
            return
        config = self._channel_config()
        agent_id = str(config.get("default_agent_id") or "").strip()
        if self._agent_chat_service is None:
            self._send_reply(str(event.get("message_id") or ""), "微信通道已收到消息，但 Agent 服务不可用。")
            return
        try:
            reply = asyncio.run(self._agent_chat_service.chat(agent_id, text))
        except Exception as exc:
            reply = f"Agent 处理失败：{exc}"
        self._send_reply(str(event.get("message_id") or ""), reply)

    def _send_reply(self, message_id: str, text: str) -> None:
        if not message_id:
            return
        with self._lock:
            process = self._process
        if not process or not process.stdin or process.poll() is not None:
            return
        process.stdin.write(json.dumps({"type": "reply", "message_id": message_id, "text": text}) + "\n")
        process.stdin.flush()

    def _message_allowed(self, event: dict[str, Any]) -> bool:
        config = self._channel_config()
        allow_contacts = {str(item).strip() for item in config.get("allow_contacts") or [] if str(item).strip()}
        allow_rooms = {str(item).strip() for item in config.get("allow_rooms") or [] if str(item).strip()}
        talker = str(event.get("talker_name") or "").strip()
        room = str(event.get("room_topic") or "").strip()
        if room:
            return not allow_rooms or room in allow_rooms
        return not allow_contacts or talker in allow_contacts

    def _sidecar_env(self) -> dict[str, str]:
        config = self._channel_config()
        env = os.environ.copy()
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        ):
            env.pop(name, None)
        env.update({
            "SPP_WECHAT_PROFILE": str(config.get("profile") or "super-personal-platform"),
        })
        puppet = str(config.get("puppet") or "").strip()
        token = str(config.get("token") or "").strip()
        proxy = str(config.get("proxy") or "").strip()
        if puppet:
            env["SPP_WECHAT_PUPPET"] = puppet
        if token:
            env["SPP_WECHAT_PUPPET_SERVICE_TOKEN"] = token
        if proxy:
            env["HTTPS_PROXY"] = proxy
            env["HTTP_PROXY"] = proxy
        return env

    def _last_error_locked(self) -> str:
        for event in reversed(self._logs):
            if str(event.get("type") or "") == "error":
                return str(event.get("error") or "")
        return ""

    def _channel_config(self) -> dict[str, Any]:
        path = self._workspace / "config.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return {}
        channels = raw.get("channels") if isinstance(raw, dict) else {}
        wechat = channels.get("wechat_personal") if isinstance(channels, dict) else {}
        return wechat if isinstance(wechat, dict) else {}

    def _snapshot_locked(self) -> WechatChannelStatus:
        running = bool(self._process and self._process.poll() is None)
        return WechatChannelStatus(
            running=running,
            login_state=self._login_state,
            qrcode_url=self._qrcode_url,
            qrcode_data_url=self._qrcode_data_url,
            qrcode_status=self._qrcode_status,
            user=self._user,
            error=self._error,
            logs=tuple(self._logs),
        )
