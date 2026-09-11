from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from server.app.run_service import RunNotFoundError, RunService
from server.app.system_log_service import SystemLogService


DELIVERY_POLL_SECONDS = 2.0
DELIVERY_MAX_ATTEMPTS = 6
DELIVERY_RETRY_DELAYS_SECONDS = (5, 15, 60, 180, 600, 1800)
FINAL_DELIVERY_STATUSES = {"delivered", "skipped", "dead_letter"}


class RunDeliveryService:
    """Durable, retrying delivery for run results and approval requests."""

    def __init__(
        self,
        *,
        run_service: RunService,
        channel_delivery_service: Any,
        system_log_service: SystemLogService,
        poll_interval_seconds: float = DELIVERY_POLL_SECONDS,
    ) -> None:
        self._run_service = run_service
        self._channel_delivery_service = channel_delivery_service
        self._system_log_service = system_log_service
        self._poll_interval_seconds = poll_interval_seconds
        self._wake_event = asyncio.Event()
        self._in_flight: set[str] = set()

    def wake(self) -> None:
        self._wake_event.set()

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.tick()
            self._wake_event.clear()
            try:
                await asyncio.wait_for(_wait_for_any(stop, self._wake_event), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        for summary in self._run_service.list_runs():
            run_id = str(summary.get("run_id") or "").strip()
            run_status = str(summary.get("status") or "")
            delivery_status = str(summary.get("delivery_status") or "")
            if not run_id:
                continue
            if run_status == "waiting_approval" or (
                run_status in {"completed", "failed", "cancelled"}
                and delivery_status not in FINAL_DELIVERY_STATUSES
            ):
                try:
                    await self.process_run(run_id)
                except Exception as exc:  # noqa: BLE001
                    self._system_log_service.append_line(f"run delivery scan failed run={run_id} error={exc}")

    async def process_run(self, run_id: str) -> dict[str, Any]:
        if run_id in self._in_flight:
            try:
                return self._run_service.get_run(run_id).get("delivery") or {}
            except RunNotFoundError:
                return {}
        self._in_flight.add(run_id)
        try:
            try:
                run = self._run_service.get_run(run_id)
            except RunNotFoundError:
                return {}
            state = run.get("state") if isinstance(run.get("state"), dict) else {}
            status = str(state.get("status") or "")
            if status == "waiting_approval":
                await self._deliver_approval_request(run)
                return self._run_service.get_run(run_id).get("delivery") or {}
            if status in {"completed", "failed", "cancelled"}:
                await self._deliver_final_result(run)
                return self._run_service.get_run(run_id).get("delivery") or {}
            return run.get("delivery") if isinstance(run.get("delivery"), dict) else {}
        finally:
            self._in_flight.discard(run_id)

    async def wait_for_delivery(self, run_id: str) -> dict[str, Any]:
        while True:
            delivery = await self.process_run(run_id)
            status = str(delivery.get("status") or "")
            if status in FINAL_DELIVERY_STATUSES:
                return delivery
            await asyncio.sleep(self._poll_interval_seconds)

    async def _deliver_approval_request(self, run: dict[str, Any]) -> None:
        run_id = str(run.get("run_id") or "")
        target = _delivery_target(run)
        if not target:
            return
        approval = run.get("approval") if isinstance(run.get("approval"), dict) else {}
        if approval.get("status") != "pending":
            return
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        request_id = _approval_request_id(approval)
        delivery = run.get("delivery") if isinstance(run.get("delivery"), dict) else {}
        previous = delivery.get("approval_notification")
        notification = dict(previous) if isinstance(previous, dict) and previous.get("request_id") == request_id else {}
        if notification.get("status") in {"delivered", "dead_letter"}:
            return
        if not _retry_is_due(notification):
            return

        attempts = int(notification.get("attempts") or 0) + 1
        client_id = str(notification.get("client_id") or f"spp-{run_id}-approval-{request_id[:12]}")
        sending = {
            **notification,
            "request_id": request_id,
            "status": "sending",
            "attempts": attempts,
            "max_attempts": DELIVERY_MAX_ATTEMPTS,
            "client_id": client_id,
            "updated_at": _now(),
        }
        self._run_service.set_delivery_status(
            run_id,
            "waiting_approval",
            extra={"target": target, "approval_notification": sending},
        )
        try:
            await self._send(target, text=_approval_message(run_id, request), client_id=client_id)
        except Exception as exc:  # noqa: BLE001
            failed = _failed_attempt(sending, exc)
            self._run_service.set_delivery_status(
                run_id,
                "waiting_approval",
                extra={"target": target, "approval_notification": failed},
            )
            self._log_failure(run_id, "approval", exc, attempts)
            return
        delivered = {
            **sending,
            "status": "delivered",
            "delivered_at": _now(),
            "next_attempt_at": "",
        }
        delivered.pop("error", None)
        self._run_service.set_delivery_status(
            run_id,
            "waiting_approval",
            extra={"target": target, "approval_notification": delivered},
        )

    async def _deliver_final_result(self, run: dict[str, Any]) -> None:
        run_id = str(run.get("run_id") or "")
        delivery = run.get("delivery") if isinstance(run.get("delivery"), dict) else {}
        if delivery.get("status") in FINAL_DELIVERY_STATUSES:
            return
        target = _delivery_target(run)
        if not target:
            self._run_service.set_delivery_status(run_id, "skipped", extra={"reason": "no delivery target"})
            return
        if not _retry_is_due(delivery):
            return

        attempts = int(delivery.get("attempts") or 0) + 1
        generation = int((run.get("state") or {}).get("rerun_count") or 0)
        client_id = f"spp-{run_id}-final-r{generation}" if generation else str(delivery.get("client_id") or f"spp-{run_id}-final")
        delivered_parts = list(delivery.get("delivered_parts") or [])
        sending = {
            "target": target,
            "delivered_parts": delivered_parts,
            "status": "sending",
            "attempts": attempts,
            "max_attempts": DELIVERY_MAX_ATTEMPTS,
            "client_id": client_id,
            "updated_at": _now(),
        }
        self._run_service.set_delivery_status(run_id, "sending", extra=sending)
        try:
            if client_id not in delivered_parts:
                await self._send(target, text=_final_message(run), client_id=client_id)
                delivered_parts.append(client_id)
                self._run_service.set_delivery_status(run_id, "sending", extra={"delivered_parts": delivered_parts})
            if (run.get("state") or {}).get("status") == "completed":
                for item in self._run_service.outgoing_attachments(run_id):
                    part_id = f"spp-{run_id}-r{generation}-{item['id'][:20]}"
                    if part_id in delivered_parts: continue
                    data = self._run_service.read_outgoing_attachment(run_id, item)
                    await self._channel_delivery_service.deliver_attachment(
                        channel="wechat", account_id=target["account_id"], to_user_id=target["to_user_id"],
                        context_token=target.get("context_token", ""), data=data,
                        filename=item["filename"], kind=item["kind"], client_id=part_id,
                    )
                    delivered_parts.append(part_id)
                    self._run_service.set_delivery_status(run_id, "sending", extra={"delivered_parts": delivered_parts})
        except Exception as exc:  # noqa: BLE001
            failed = _failed_attempt({**delivery, **sending}, exc)
            self._run_service.set_delivery_status(
                run_id,
                str(failed["status"]),
                extra=failed,
                error=failed["error"],
            )
            self._log_failure(run_id, "final", exc, attempts)
            return
        self._run_service.set_delivery_status(
            run_id,
            "delivered",
            extra={
                **sending,
                "status": "delivered",
                "delivered_at": _now(),
                "next_attempt_at": "",
            },
        )

    async def _send(self, target: dict[str, str], *, text: str, client_id: str) -> None:
        await self._channel_delivery_service.deliver_text(
            channel="wechat",
            account_id=target["account_id"],
            to_user_id=target["to_user_id"],
            context_token=target.get("context_token", ""),
            text=text,
            client_id=client_id,
        )

    def _log_failure(self, run_id: str, kind: str, exc: Exception, attempts: int) -> None:
        self._system_log_service.append_line(
            f"run delivery failed run={run_id} kind={kind} attempt={attempts}/{DELIVERY_MAX_ATTEMPTS} "
            f"error={exc}"
        )


def _delivery_target(run: dict[str, Any]) -> dict[str, str]:
    run_input = run.get("input") if isinstance(run.get("input"), dict) else {}
    metadata = run_input.get("metadata") if isinstance(run_input.get("metadata"), dict) else {}
    nested = metadata.get("delivery") if isinstance(metadata.get("delivery"), dict) else {}
    source = str(run_input.get("source") or "")
    raw = nested if nested else metadata if source == "wechat" else {}
    channel = str(raw.get("channel") or ("wechat" if source == "wechat" else "")).strip()
    account_id = str(raw.get("account_id") or "").strip()
    recipient = (
        (raw.get("to_user_id") or raw.get("peer_id"))
        if nested
        else (raw.get("from_user_id") or raw.get("peer_id"))
    )
    to_user_id = str(recipient or "").strip()
    if channel != "wechat" or not account_id or not to_user_id:
        return {}
    return {
        "channel": "wechat",
        "account_id": account_id,
        "agent_id": str(run_input.get("agent_id") or "").strip(),
        "peer_id": str(raw.get("peer_id") or to_user_id).strip(),
        "peer_type": str(raw.get("peer_type") or "private").strip(),
        "to_user_id": to_user_id,
        "context_token": str(raw.get("context_token") or "").strip(),
    }


def _approval_request_id(approval: dict[str, Any]) -> str:
    payload = {
        "requested_at": approval.get("requested_at"),
        "request": approval.get("request"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _approval_message(run_id: str, request: dict[str, Any]) -> str:
    lines = ["此任务需要审批后才能继续：", f"Run: {run_id}"]
    action_number = 0
    interrupts = request.get("interrupts") if isinstance(request.get("interrupts"), list) else []
    for interrupt in interrupts:
        actions = (
            interrupt.get("actions")
            if isinstance(interrupt, dict) and isinstance(interrupt.get("actions"), list)
            else []
        )
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_number += 1
            name = str(action.get("name") or "tool")
            description = str(action.get("description") or "").strip()
            lines.append(f"{action_number}. {name}" + (f"：{description}" if description else ""))
    lines.extend(
        [
            "",
            f"批准：/approve {run_id}",
            f"拒绝：/reject {run_id} 原因",
        ]
    )
    return "\n".join(lines)


def _final_message(run: dict[str, Any]) -> str:
    state = run.get("state") if isinstance(run.get("state"), dict) else {}
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    status = str(state.get("status") or "")
    if status == "completed":
        return str(result.get("content") or "任务没有返回内容")
    error = result.get("error") if isinstance(result.get("error"), dict) else state.get("error")
    message = str(error.get("message") or "") if isinstance(error, dict) else str(error or "")
    if status == "cancelled":
        return f"任务已取消" + (f"：{message}" if message else "")
    return f"DeepAgent 处理失败" + (f"：{message}" if message else "")


def _failed_attempt(attempt: dict[str, Any], exc: Exception) -> dict[str, Any]:
    attempts = int(attempt.get("attempts") or 0)
    dead_letter = attempts >= DELIVERY_MAX_ATTEMPTS
    delay = DELIVERY_RETRY_DELAYS_SECONDS[min(max(attempts - 1, 0), len(DELIVERY_RETRY_DELAYS_SECONDS) - 1)]
    return {
        **attempt,
        "status": "dead_letter" if dead_letter else "failed",
        "error": {"type": exc.__class__.__name__, "message": str(exc)},
        "next_attempt_at": "" if dead_letter else (_now_dt() + timedelta(seconds=delay)).isoformat(),
        "updated_at": _now(),
    }


def _retry_is_due(delivery: dict[str, Any]) -> bool:
    next_attempt_at = str(delivery.get("next_attempt_at") or "").strip()
    if not next_attempt_at:
        return True
    try:
        parsed = datetime.fromisoformat(next_attempt_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) <= _now_dt()
    except (TypeError, ValueError):
        return True


async def _wait_for_any(*events: asyncio.Event) -> None:
    tasks = [asyncio.create_task(event.wait()) for event in events]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()
