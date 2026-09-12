from __future__ import annotations

from server.infrastructure.agent_workspace import agent_workspace_path
from server.domain.chat_group import GroupRunContext

import asyncio
import json
import os
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.app.session_service import SessionService
from server.domain.agent_config import AgentConfigError, AgentDefinition, ModelDefinition
from server.domain.run_approval import (
    ApprovalDecisionType,
    RunApprovalDecision,
    RunApprovalRequest,
    RunApprovalResume,
)
from server.app.run_usage import add_call, summarize_usage
from server.domain.run_events import (
    ModelUsagePayload,
    DeepAgentGraphUpdatePayload,
    DeepAgentMessageDeltaPayload,
    DeepAgentSubagentResponsePayload,
    ImageAttachmentsTextifiedPayload,
    RunErrorPayload,
    RunEventPayload,
    RunEventRecord,
    RunEventType,
    RunLifecyclePayload,
    RunApprovalRequiredPayload,
    RunApprovalResolvedPayload,
    run_event_from_json,
)
from server.infrastructure.config import load_settings
from server.infrastructure.deepagent_runtime import (
    DeepAgentRuntime,
    DeepAgentRuntimeOptions,
    DeepAgentStreamEvent,
    RuntimeAttachment,
    RuntimeMessage,
)
from server.infrastructure.tool_runtime import PlatformToolContext


RUN_STATUSES = {"queued", "running", "waiting_approval", "completed", "failed", "cancelled"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
SESSION_HISTORY_READ_LIMIT = 120
SESSION_RUNTIME_MESSAGE_LIMIT = 60
STREAM_PARTIAL_FLUSH_SECONDS = 0.5
STREAM_PARTIAL_FLUSH_CHARS = 160
RUN_EXECUTION_TIMEOUT_SECONDS = 30 * 60
RUN_LOCK_HEARTBEAT_SECONDS = 15
RUN_STALE_HEARTBEAT_SECONDS = 120
RUN_RETRY_MAX_ATTEMPTS = 2
RUN_WORKER_POLL_SECONDS = 1


class RunNotFoundError(Exception):
    pass


class RunStateError(Exception):
    pass


class SessionRunConflictError(RunStateError):
    def __init__(self, *, session_id: str, run_id: str, status: str) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self.status = status
        super().__init__(f"session already has an active run: {run_id} ({status})")


@dataclass(frozen=True)
class RunInput:
    run_id: str
    source: str
    agent_id: str
    session_id: str
    content: str
    attachments: tuple[dict[str, Any], ...]
    context_ids: tuple[str, ...]
    created_at: str
    metadata: dict[str, Any]
    snapshot: dict[str, Any]


class RunService:
    def __init__(self, workspace: Path, session_service: SessionService | None = None) -> None:
        self._workspace = workspace
        self._runs_dir = workspace / "runs"
        self._session_service = session_service
        self._schedule_service: Any = None

    def set_schedule_service(self, schedule_service: Any) -> None:
        self._schedule_service = schedule_service

    def reconcile_incomplete_runs(self) -> int:
        """Recover runs left non-terminal by a previous service process."""
        reconciled = 0
        for item in self._list_runs_no_reconcile():
            run_id = str(item.get("run_id") or "").strip()
            if not run_id:
                continue
            try:
                state = _read_json(self._run_dir(run_id) / "state.json")
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                continue
            status = state.get("status")
            if status == "queued":
                continue
            if status != "running":
                continue
            if self._recover_active_run(
                run_id,
                error={
                    "type": "RunInterruptedError",
                    "message": "service restarted before run completion",
                },
            ):
                reconciled += 1
        return reconciled

    async def create_run(
        self,
        *,
        content: str,
        agent_id: str = "",
        context_ids: tuple[str, ...] = (),
        source: str = "api",
        session_id: str = "",
        attachments: tuple[dict[str, Any], ...] = (),
        metadata: dict[str, Any] | None = None,
        group_context: GroupRunContext | None = None,
    ) -> dict[str, Any]:
        if session_id and self._session_service is not None and not self._session_service.exists(session_id):
            raise ValueError("session does not exist")
        if group_context is not None:
            existing = self._run_dir(group_context.run_id)
            if (existing / "state.json").exists():
                if not any(item.get("run_id") == group_context.run_id for item in self._list_runs_no_reconcile()):
                    self._upsert_index(self._summary_from_state(group_context.run_id))
                return self.get_run(group_context.run_id)
        elif session_id and self._session_service and self._session_service.session_summary(session_id).get("channel") == "chat_group":
            raise ValueError("群成员会话只能通过群聊接口执行")
        if session_id:
            active_run = self.active_run_for_session(session_id)
            if active_run is not None:
                state = active_run.get("state") if isinstance(active_run.get("state"), dict) else {}
                raise SessionRunConflictError(
                    session_id=session_id,
                    run_id=str(active_run.get("run_id") or ""),
                    status=str(state.get("status") or "queued"),
                )
        saved_attachments: tuple[dict[str, Any], ...] = ()
        if session_id and self._session_service is not None:
            saved_attachments = self._session_service.save_attachments(session_id, attachments)
        else:
            saved_attachments = _normalize_attachment_metadata(attachments)
        text = content.strip() or ("用户发送了图片。" if _has_image_attachment(saved_attachments) else "")
        if not text and not saved_attachments:
            raise ValueError("content is required")

        settings = load_settings(self._workspace / "config.yaml")
        agent = self._resolve_agent(settings.agent_workspace.agents, agent_id)
        model_id = (group_context.agent_snapshot.get("model_id") if group_context else agent.model_id) or settings.agent_workspace.default_model_id
        model = settings.agent_workspace.get_model(model_id)
        now = _now()
        run_id = group_context.run_id if group_context else self._new_run_id()
        selected_context_ids = context_ids or agent.context_ids
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=group_context is not None)

        run_input = RunInput(
            run_id=run_id,
            source=source,
            agent_id=agent.id,
            session_id=session_id,
            content=text,
            attachments=saved_attachments,
            context_ids=selected_context_ids,
            created_at=now,
            metadata=metadata or {},
            snapshot={
                "agent": group_context.agent_snapshot if group_context else _public_agent(agent),
                **({"group_context": group_context.model_dump()} if group_context else {}),
                "model": group_context.model_snapshot if group_context and group_context.model_snapshot else _public_model(model),
                "context": self._snapshot_context(),
            },
        )
        _write_json(run_dir / "input.json", asdict(run_input))
        delivery_target = _initial_delivery_target(source, run_input.metadata, agent_id=agent.id)
        delivery_payload: dict[str, Any] = {
            "schema_version": 1,
            "source": source,
            "session_id": session_id,
            "status": "pending",
            "attempts": 0,
            "updated_at": now,
        }
        if delivery_target:
            delivery_payload["target"] = delivery_target
        _write_json(run_dir / "delivery.json", delivery_payload)
        if session_id and self._session_service is not None:
            self._session_service.append_message(
                session_id,
                role="user",
                content=text,
                attachments=saved_attachments,
                run_id=run_id,
                metadata={"source": source},
            )
            self._session_service.append_run(
                session_id,
                run_id=run_id,
                status="queued",
                source=source,
                agent_id=agent.id,
            )
        _write_json(
            run_dir / "state.json",
            {
                "run_id": run_id,
                "session_id": session_id,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "seq": 0,
                "attempts": 0,
                "max_attempts": RUN_RETRY_MAX_ATTEMPTS,
            },
        )
        self._append_event(run_id, RunEventType.QUEUED, RunLifecyclePayload(message="run queued"))
        self._upsert_index(self._summary_from_state(run_id))
        return self.get_run(run_id)

    async def execute_run(self, run_id: str, *, lease_id: str = "") -> dict[str, Any]:
        execution_lease_id = self._ensure_execution_lease(run_id, lease_id=lease_id)
        run_input = self._load_input(run_id)
        settings = load_settings(self._workspace / "config.yaml")
        model_id = str(run_input["snapshot"]["agent"].get("model_id") or settings.agent_workspace.default_model_id)
        model = settings.agent_workspace.get_model(model_id)
        agent_snapshot = run_input["snapshot"]["agent"]
        system_prompt = str(agent_snapshot.get("system_prompt") or "")
        content = str(run_input.get("content") or "")
        session_id = str(run_input.get("session_id") or "")
        runtime_options = _runtime_options(agent_snapshot.get("deepagent"), name=str(agent_snapshot.get("name") or ""), webdav=agent_snapshot.get("webdav"))
        group_data = run_input.get("snapshot", {}).get("group_context")
        if group_data:
            from dataclasses import replace
            from server.domain.agent_config import ModelProvider
            frozen_model = run_input["snapshot"]["model"]
            model = replace(model, **{**frozen_model, "provider": ModelProvider(frozen_model["provider"])})
            runtime_options = replace(runtime_options, group_control=group_data if group_data.get("control_members") else None)
        use_session_checkpoint = bool(session_id)
        approval_resume = self._approval_resume(run_id)
        use_approval_checkpoint = bool(
            runtime_options.interrupt_on or approval_resume is not None
            or (runtime_options.webdav.enabled and any(d.permission == "write" for d in runtime_options.webdav.directories))
        )
        history = (
            self._session_service.read_messages(session_id, limit=SESSION_HISTORY_READ_LIMIT)
            if session_id and self._session_service is not None
            else []
        )
        fallback_attachments = _runtime_attachments(run_input.get("attachments") or [], workspace=self._workspace)
        checkpoint_path = (
            self._workspace / "sessions" / "checkpoints.sqlite"
            if use_session_checkpoint or use_approval_checkpoint
            else None
        )
        runtime_thread_id = session_id if use_session_checkpoint else (run_id if use_approval_checkpoint else "")
        runtime_history = _current_run_messages(history, run_id) if use_session_checkpoint else history[-SESSION_RUNTIME_MESSAGE_LIMIT:]
        runtime_messages = _runtime_messages(
            runtime_history,
            fallback_content=content,
            fallback_attachments=fallback_attachments,
            workspace=self._workspace,
        )
        if group_data:
            runtime_messages = tuple(RuntimeMessage(role="user", content=item["content"], id=item["id"]) for item in group_data["messages"])
        downgraded_image_count = 0
        if any(message.has_images for message in runtime_messages) and not model.supports_images:
            runtime_messages, downgraded_image_count = _textify_image_attachments(
                runtime_messages,
                workspace=self._workspace,
            )
        effective_system_prompt = system_prompt
        stream_recorder = _RunStreamRecorder.restore(
            run_id=run_id,
            run_dir=self._run_dir(run_id),
            append_event=self._append_event,
        )
        self._set_state(run_id, "running", extra={"worker_lease_id": execution_lease_id})
        running_payload = RunLifecyclePayload(message="DeepAgent started")
        self._append_event(run_id, RunEventType.RUNNING, running_payload)
        stream_recorder.record_snapshot_event(RunEventType.RUNNING, running_payload)
        if downgraded_image_count:
            image_payload = ImageAttachmentsTextifiedPayload(
                message="model does not support image input; image binaries were removed and metadata was passed as text",
                items=downgraded_image_count,
            )
            self._append_event(
                run_id,
                RunEventType.IMAGE_ATTACHMENTS_TEXTIFIED,
                image_payload,
            )
            stream_recorder.record_snapshot_event(RunEventType.IMAGE_ATTACHMENTS_TEXTIFIED, image_payload)

        usage_path = self._run_dir(run_id) / "usage.json"
        usage_ledger = _read_json(usage_path) if usage_path.exists() else {"schema_version": 1, "calls": {}, "execution_seconds": 0}
        segment_id = uuid.uuid4().hex
        usage_ledger.setdefault("segments", {})[segment_id] = {"finished": False}
        execution_started = time.monotonic()

        def persist_usage():
            _write_json(usage_path, usage_ledger)
            self._upsert_index(self._summary_from_state(run_id))

        def record_event(event):
            if isinstance(event.payload, ModelUsagePayload):
                add_call(usage_ledger, event.payload, model)
                persist_usage()
            stream_recorder.record(event)

        persist_usage()
        heartbeat_task = asyncio.create_task(self._heartbeat_run_lock(run_id, lease_id=execution_lease_id))
        try:
            async with asyncio.timeout(RUN_EXECUTION_TIMEOUT_SECONDS):
                runtime = DeepAgentRuntime(
                    model,
                    context_workspace=self._workspace / "context",
                    agent_id=str(run_input.get("agent_id") or ""),
                    agent_workspace=self._agent_workspace(str(run_input.get("agent_id") or "")),
                    schedule_service=self._schedule_service,
                    tool_context=PlatformToolContext(
                        run_id=str(run_input.get("run_id") or run_id),
                        source=str(run_input.get("source") or "api"),
                        agent_id=str(run_input.get("agent_id") or ""),
                        session_id=session_id,
                        metadata=run_input.get("metadata") if isinstance(run_input.get("metadata"), dict) else {},
                    ),
                )
                runtime_kwargs: dict[str, Any] = {
                    "instructions": effective_system_prompt,
                    "messages": runtime_messages,
                    "options": runtime_options,
                    "checkpoint_path": checkpoint_path,
                    "thread_id": runtime_thread_id,
                    "stream_callback": record_event,
                }
                if approval_resume is not None:
                    runtime_kwargs["resume"] = approval_resume
                result = await runtime.run(
                    **runtime_kwargs,
                )
            if isinstance(result, RunApprovalRequest):
                self._wait_for_approval(
                    run_id,
                    result,
                    lease_id=execution_lease_id,
                    stream_recorder=stream_recorder,
                )
            else:
                stream_recorder.finish(result)
        except asyncio.CancelledError:
            error = {
                "message": "run execution task was cancelled before completion",
                "type": "RunExecutionCancelledError",
            }
            if not self._run_has_terminal_state(run_id):
                stream_recorder.fail(error)
                self._fail_executing_run(
                    run_id,
                    error,
                    lease_id=execution_lease_id,
                    status="cancelled",
                    retryable=False,
                )
            raise
        except Exception as exc:
            error = {
                "message": (
                    f"run exceeded the {RUN_EXECUTION_TIMEOUT_SECONDS}-second execution limit"
                    if isinstance(exc, TimeoutError)
                    else str(exc)
                ),
                "type": "RunExecutionTimeoutError" if isinstance(exc, TimeoutError) else exc.__class__.__name__,
            }
            stream_recorder.fail(error)
            self._fail_executing_run(run_id, error, lease_id=execution_lease_id, retryable=True)
            raise
        finally:
            usage_ledger["execution_seconds"] += time.monotonic() - execution_started
            usage_ledger["segments"][segment_id]["finished"] = True
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            persist_usage()

        if isinstance(result, RunApprovalRequest):
            return self.get_run(run_id)

        cancel_requested_at = self._cancel_requested_at(run_id)
        if cancel_requested_at:
            error = {"message": "run was cancelled", "type": "RunCancelledError"}
            stream_recorder.fail(error)
            self._fail_executing_run(run_id, error, lease_id=execution_lease_id, status="cancelled", retryable=False)
            return self.get_run(run_id)

        if self._run_has_terminal_state(run_id) or not self._lease_matches(run_id, execution_lease_id):
            return self.get_run(run_id)

        result_payload = {
            "run_id": run_id,
            "status": "completed",
            "content": result,
            "completed_at": _now(),
        }
        _write_json(self._run_dir(run_id) / "result.json", result_payload)
        self._complete_approval(run_id)
        if session_id and self._session_service is not None:
            self._session_service.append_message(
                session_id,
                role="assistant",
                content=result,
                run_id=run_id,
                metadata={"source": run_input.get("source", "api")},
            )
        self._set_state(run_id, "completed")
        self._append_event(run_id, RunEventType.COMPLETED, RunLifecyclePayload(message="run completed"))
        self._set_delivery(run_id, "ready")
        self._release_lock(run_id)
        return self.get_run(run_id)

    def outgoing_attachments(self, run_id: str) -> list[dict[str, Any]]:
        from server.infrastructure.outgoing_attachments import list_attachments
        return list_attachments(self._run_dir(run_id))

    def read_outgoing_attachment(self, run_id: str, item: dict[str, Any]) -> bytes:
        from server.infrastructure.outgoing_attachments import read_attachment
        return read_attachment(self._run_dir(run_id), item)

    def set_delivery_status(
        self,
        run_id: str,
        status: str,
        *,
        extra: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        self._set_delivery(run_id, status, extra=extra, error=error)

    def fail_run(self, run_id: str, *, error: dict[str, Any]) -> None:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            return
        state_path = run_dir / "state.json"
        state = _read_json(state_path)
        if state.get("status") in TERMINAL_RUN_STATUSES:
            return
        existing_result = run_dir / "result.json"
        if not existing_result.exists():
            _write_json(
                existing_result,
                {"run_id": run_id, "status": "failed", "error": error, "completed_at": _now()},
            )
        self._set_state(run_id, "failed", error=error)
        self._append_event(
            run_id,
            RunEventType.FAILED,
            RunErrorPayload(message=str(error.get("message") or ""), type=str(error.get("type") or "")),
        )
        self._set_delivery(run_id, "failed", error=error)
        self._mark_partial_failed(run_id, error)
        self._release_lock(run_id)

    def claim_next_run(self, *, worker_id: str) -> dict[str, str] | None:
        self.reconcile_stale_active_runs()
        for item in reversed(self._list_runs_no_reconcile()):
            run_id = str(item.get("run_id") or "").strip()
            if not run_id:
                continue
            claim = self.claim_run(run_id, worker_id=worker_id)
            if claim is not None:
                return claim
        return None

    def claim_run(self, run_id: str, *, worker_id: str) -> dict[str, str] | None:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise RunNotFoundError(run_id)
        state = _read_json(run_dir / "state.json")
        if state.get("status") != "queued":
            return None
        if state.get("cancel_requested_at"):
            self.cancel_run(run_id)
            return None
        lock_path = run_dir / "lock.json"
        if lock_path.exists():
            if _lock_is_stale(lock_path):
                lock_path.unlink(missing_ok=True)
            else:
                return None
        lease_id = uuid.uuid4().hex
        now = _now()
        payload = {
            "pid": os.getpid(),
            "worker_id": worker_id,
            "lease_id": lease_id,
            "created_at": now,
            "heartbeat_at": now,
        }
        try:
            fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
                lock_file.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        except Exception:
            with suppress(OSError):
                lock_path.unlink()
            raise
        is_approval_resume = bool(state.get("approval_resume_pending"))
        attempts = int(state.get("attempts") or 0) + (0 if is_approval_resume else 1)
        self._set_state(
            run_id,
            "running",
            extra={
                "attempts": attempts,
                "max_attempts": int(state.get("max_attempts") or RUN_RETRY_MAX_ATTEMPTS),
                "worker_id": worker_id,
                "worker_lease_id": lease_id,
                "started_at": now,
                "heartbeat_at": now,
                "cancel_requested_at": None,
                "approval_resume_pending": False,
            },
        )
        return {"run_id": run_id, "lease_id": lease_id}

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise RunNotFoundError(run_id)
        state = _read_json(run_dir / "state.json")
        if state.get("status") in TERMINAL_RUN_STATUSES:
            return self.get_run(run_id)
        now = _now()
        error = {"type": "RunCancelledError", "message": "run was cancelled"}
        if state.get("status") in {"queued", "running", "waiting_approval"}:
            _write_json(
                run_dir / "result.json",
                {"run_id": run_id, "status": "cancelled", "error": error, "completed_at": now},
            )
            self._set_state(run_id, "cancelled", error=error, extra={"cancel_requested_at": now})
            self._append_event(run_id, RunEventType.CANCELLED, RunErrorPayload(message=error["message"], type=error["type"]))
            self._set_delivery(run_id, "cancelled", error=error)
            self._mark_partial_failed(run_id, error, status="cancelled")
            self._cancel_approval(run_id)
            self._release_lock(run_id)
            return self.get_run(run_id)
        return self.get_run(run_id)

    def rerun(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise RunNotFoundError(run_id)
        state = _read_json(run_dir / "state.json")
        if state.get("status") not in TERMINAL_RUN_STATUSES:
            raise RunStateError("only terminal runs can be rerun")
        now = _now()
        for filename in ("result.json", "partial.json", "approval.json"):
            with suppress(OSError):
                (run_dir / filename).unlink()
        next_state = {
            **state,
            "status": "queued",
            "updated_at": now,
            "attempts": 0,
            "max_attempts": int(state.get("max_attempts") or RUN_RETRY_MAX_ATTEMPTS),
            "last_error": state.get("error"),
            "error": None,
            "cancel_requested_at": None,
            "worker_id": "",
            "worker_lease_id": "",
            "rerun_count": int(state.get("rerun_count") or 0) + 1,
        }
        _write_json(run_dir / "state.json", next_state)
        self._set_delivery(run_id, "pending")
        self._append_event(run_id, RunEventType.QUEUED, RunLifecyclePayload(message="run rerun queued"))
        self._release_lock(run_id)
        self._upsert_index(self._summary_from_state(run_id))
        return self.get_run(run_id)

    def approve_run(self, run_id: str) -> dict[str, Any]:
        return self.resume_run(run_id, decision="approve")

    def reject_run(self, run_id: str, *, message: str = "") -> dict[str, Any]:
        return self.resume_run(run_id, decision="reject", message=message)

    def resume_run(
        self,
        run_id: str,
        *,
        decision: ApprovalDecisionType,
        message: str = "",
    ) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise RunNotFoundError(run_id)
        state = _read_json(run_dir / "state.json")
        if state.get("status") != "waiting_approval":
            raise RunStateError("only runs waiting for approval can be resumed")
        approval_path = run_dir / "approval.json"
        approval = _read_json(approval_path) if approval_path.exists() else {}
        if approval.get("status") != "pending":
            raise RunStateError("run has no pending approval request")
        request = RunApprovalRequest.from_json(approval.get("request") or {})
        if not request.interrupts:
            raise RunStateError("run approval request is invalid")
        rejection_message = message.strip() or "用户拒绝执行该操作"
        resume_values: list[tuple[str, tuple[RunApprovalDecision, ...]]] = []
        for interrupt in request.interrupts:
            decisions: list[RunApprovalDecision] = []
            for action in interrupt.actions:
                if decision not in action.allowed_decisions:
                    raise RunStateError(f"{action.name} does not allow the {decision} decision")
                decisions.append(
                    RunApprovalDecision(
                        type=decision,
                        message=rejection_message if decision == "reject" else "",
                    )
                )
            resume_values.append((interrupt.interrupt_id, tuple(decisions)))
        resume = RunApprovalResume(values=tuple(resume_values))
        now = _now()
        history = approval.get("history") if isinstance(approval.get("history"), list) else []
        history.append(
            {
                "request": request.to_json(),
                "resolution": {"decision": decision, "message": rejection_message if decision == "reject" else ""},
                "resolved_at": now,
            }
        )
        _write_json(
            approval_path,
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": "resume_queued",
                "request": request.to_json(),
                "resume": resume.to_json(),
                "history": history,
                "requested_at": approval.get("requested_at") or now,
                "updated_at": now,
            },
        )
        self._set_state(
            run_id,
            "queued",
            extra={
                "approval_resume_pending": True,
                "worker_id": "",
                "worker_lease_id": "",
                "heartbeat_at": None,
                "cancel_requested_at": None,
            },
        )
        self._append_event(
            run_id,
            RunEventType.APPROVAL_RESOLVED,
            RunApprovalResolvedPayload(
                decision=decision,
                message=rejection_message if decision == "reject" else "",
                interrupt_ids=tuple(item.interrupt_id for item in request.interrupts),
            ),
        )
        self._append_event(run_id, RunEventType.QUEUED, RunLifecyclePayload(message="run resume queued"))
        self._set_delivery(run_id, "pending")
        self._release_lock(run_id)
        return self.get_run(run_id)

    async def wait_for_terminal(self, run_id: str, *, poll_seconds: float = RUN_WORKER_POLL_SECONDS) -> dict[str, Any]:
        while True:
            run = self.get_run(run_id)
            state = run.get("state") if isinstance(run.get("state"), dict) else {}
            if state.get("status") in TERMINAL_RUN_STATUSES:
                return run
            await asyncio.sleep(poll_seconds)

    def _wait_for_approval(
        self,
        run_id: str,
        request: RunApprovalRequest,
        *,
        lease_id: str,
        stream_recorder: _RunStreamRecorder,
    ) -> None:
        if not request.interrupts:
            raise RunStateError("DeepAgent returned an empty approval request")
        if not self._lease_matches(run_id, lease_id):
            return
        approval_path = self._run_dir(run_id) / "approval.json"
        previous = _read_json(approval_path) if approval_path.exists() else {}
        now = _now()
        _write_json(
            approval_path,
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": "pending",
                "request": request.to_json(),
                "resume": None,
                "history": previous.get("history") if isinstance(previous.get("history"), list) else [],
                "requested_at": now,
                "updated_at": now,
            },
        )
        self._set_state(
            run_id,
            "waiting_approval",
            extra={
                "approval_requested_at": now,
                "approval_resume_pending": False,
                "worker_id": "",
                "worker_lease_id": "",
                "heartbeat_at": None,
            },
        )
        payload = RunApprovalRequiredPayload(request=request)
        self._append_event(run_id, RunEventType.APPROVAL_REQUIRED, payload)
        stream_recorder.wait_for_approval(payload)
        self._set_delivery(run_id, "waiting_approval")
        self._release_lock(run_id)

    def _approval_resume(self, run_id: str) -> RunApprovalResume | None:
        approval_path = self._run_dir(run_id) / "approval.json"
        if not approval_path.exists():
            return None
        approval = _read_json(approval_path)
        if approval.get("status") != "resume_queued":
            return None
        resume = RunApprovalResume.from_json(approval.get("resume") or {})
        return resume if resume.values else None

    def _complete_approval(self, run_id: str) -> None:
        approval_path = self._run_dir(run_id) / "approval.json"
        if not approval_path.exists():
            return
        approval = _read_json(approval_path)
        approval["status"] = "completed"
        approval["resume"] = None
        approval["updated_at"] = _now()
        _write_json(approval_path, approval)

    def _cancel_approval(self, run_id: str) -> None:
        approval_path = self._run_dir(run_id) / "approval.json"
        if not approval_path.exists():
            return
        approval = _read_json(approval_path)
        approval["status"] = "cancelled"
        approval["resume"] = None
        approval["updated_at"] = _now()
        _write_json(approval_path, approval)

    def reconcile_stale_active_runs(self) -> int:
        reconciled = 0
        for item in self._list_runs_no_reconcile():
            run_id = str(item.get("run_id") or "").strip()
            if not run_id:
                continue
            if self._reconcile_run_if_stale(run_id):
                reconciled += 1
        return reconciled

    def _mark_partial_failed(self, run_id: str, error: dict[str, Any], *, status: str = "failed") -> None:
        partial_path = self._run_dir(run_id) / "partial.json"
        partial = _read_json(partial_path) if partial_path.exists() else {}
        thinking = partial.get("thinking") if isinstance(partial.get("thinking"), list) else []
        message = "运行已取消，未生成最终正文" if status == "cancelled" else "运行中断，未生成最终正文"
        if not thinking or thinking[-1] != message:
            thinking.append(message)
        _write_json(
            partial_path,
            {
                "run_id": run_id,
                "status": status,
                "content": str(partial.get("content") or ""),
                "thinking": thinking[-20:],
                "thinking_status": status,
                "thinking_collapsed": True,
                "error": error,
                "updated_at": _now(),
            },
        )

    def _release_lock(self, run_id: str) -> None:
        (self._run_dir(run_id) / "lock.json").unlink(missing_ok=True)

    def latest_active_run_for_schedule(self, schedule_id: str) -> str:
        self.reconcile_stale_active_runs()
        target_schedule_id = str(schedule_id or "").strip()
        if not target_schedule_id:
            return ""
        for item in self.list_runs():
            run_id = str(item.get("run_id") or "").strip()
            status = str(item.get("status") or "").strip()
            if not run_id or status in TERMINAL_RUN_STATUSES:
                continue
            try:
                run_input = self._load_input(run_id)
                state = _read_json(self._run_dir(run_id) / "state.json")
            except Exception:
                continue
            metadata = run_input.get("metadata") if isinstance(run_input.get("metadata"), dict) else {}
            if metadata.get("schedule_id") == target_schedule_id and state.get("status") not in TERMINAL_RUN_STATUSES:
                return run_id
        return ""

    def list_runs(self) -> list[dict[str, Any]]:
        self.reconcile_stale_active_runs()
        return self._list_runs_no_reconcile()

    def active_run_for_session(self, session_id: str) -> dict[str, Any] | None:
        target = session_id.strip()
        if not target:
            return None
        for summary in self._list_runs_no_reconcile():
            if str(summary.get("session_id") or "") != target:
                continue
            run_id = str(summary.get("run_id") or "").strip()
            if not run_id:
                continue
            try:
                run = self.get_run(run_id)
            except (RunNotFoundError, OSError, ValueError, json.JSONDecodeError):
                continue
            state = run.get("state") if isinstance(run.get("state"), dict) else {}
            if state.get("status") in {"queued", "running", "waiting_approval"}:
                return run
        return None

    def _list_runs_no_reconcile(self) -> list[dict[str, Any]]:
        index = self._read_index()
        runs = index.get("runs") if isinstance(index, dict) else []
        if not isinstance(runs, list):
            return []
        return [item for item in runs if isinstance(item, dict)]

    def get_run(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not (run_dir / "state.json").exists():
            raise RunNotFoundError(run_id)
        self._reconcile_run_if_stale(run_id)
        payload = {
            "run_id": run_id,
            "input": _read_json(run_dir / "input.json"),
            "state": _read_json(run_dir / "state.json"),
            "delivery": _read_json(run_dir / "delivery.json") if (run_dir / "delivery.json").exists() else {},
            "result": None,
            "partial": None,
            "approval": None,
            "usage": summarize_usage(_read_json(run_dir / "usage.json")) if (run_dir / "usage.json").exists() else None,
        }
        result_path = run_dir / "result.json"
        if result_path.exists():
            payload["result"] = _read_json(result_path)
        partial_path = run_dir / "partial.json"
        if partial_path.exists():
            payload["partial"] = _read_json(partial_path)
        approval_path = run_dir / "approval.json"
        if approval_path.exists():
            payload["approval"] = _read_json(approval_path)
        return payload

    def get_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        if self._run_dir(run_id).exists():
            self._reconcile_run_if_stale(run_id)
        events_path = self._run_dir(run_id) / "events.jsonl"
        if not events_path.exists():
            if not self._run_dir(run_id).exists():
                raise RunNotFoundError(run_id)
            return []
        events: list[dict[str, Any]] = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if int(item.get("seq") or 0) > after:
                events.append(run_event_from_json(item).to_json())
        return events

    def _resolve_agent(
        self,
        agents: tuple[AgentDefinition, ...],
        agent_id: str,
    ) -> AgentDefinition:
        selected_id = agent_id.strip()
        if not selected_id:
            if not agents:
                raise AgentConfigError("no agents configured")
            return agents[0]
        for agent in agents:
            if agent.id == selected_id:
                return agent
        raise AgentConfigError("Agent does not exist")

    def _snapshot_context(self) -> dict[str, Any]:
        files_dir = self._workspace / "context" / "knowledge" / "files"
        files: list[dict[str, Any]] = []
        if files_dir.exists():
            for path in sorted(files_dir.rglob("*")):
                if not path.is_file():
                    continue
                stat = path.stat()
                files.append(
                    {
                        "path": f"/files/{path.relative_to(files_dir).as_posix()}",
                        "size": stat.st_size,
                        "modified_at": stat.st_mtime,
                    }
                )
        return {
            "knowledge_files_path": "context/knowledge/files",
            "files": files,
        }

    def _set_state(
        self,
        run_id: str,
        status: str,
        *,
        error: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if status not in RUN_STATUSES:
            raise ValueError("invalid run status")
        state_path = self._run_dir(run_id) / "state.json"
        state = _read_json(state_path)
        state["status"] = status
        state["updated_at"] = _now()
        if error is not None:
            state["error"] = error
        if extra:
            state.update(extra)
        _write_json(state_path, state)
        self._upsert_index(self._summary_from_state(run_id))

    def _set_delivery(
        self,
        run_id: str,
        status: str,
        *,
        extra: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        run_input = self._load_input(run_id)
        delivery_path = self._run_dir(run_id) / "delivery.json"
        previous = _read_json(delivery_path) if delivery_path.exists() else {}
        payload: dict[str, Any] = {
            **previous,
            "schema_version": 1,
            "source": run_input.get("source", "api"),
            "session_id": run_input.get("session_id", ""),
            "status": status,
            "updated_at": _now(),
        }
        if extra:
            payload.update(extra)
        if error is not None:
            payload["error"] = error
        if error is None and status in {"pending", "ready", "sending", "delivered", "skipped"}:
            payload.pop("error", None)
        _write_json(delivery_path, payload)
        self._upsert_index(self._summary_from_state(run_id))

    def _fail_executing_run(
        self,
        run_id: str,
        error: dict[str, Any],
        *,
        lease_id: str,
        status: str = "failed",
        retryable: bool = False,
    ) -> None:
        if self._run_has_terminal_state(run_id) or not self._lease_matches(run_id, lease_id):
            return
        state = _read_json(self._run_dir(run_id) / "state.json")
        attempts = int(state.get("attempts") or 0)
        max_attempts = int(state.get("max_attempts") or RUN_RETRY_MAX_ATTEMPTS)
        if retryable and status == "failed" and attempts < max_attempts:
            self._release_lock(run_id)
            self._set_state(
                run_id,
                "queued",
                extra={
                    "last_error": error,
                    "worker_id": "",
                    "worker_lease_id": "",
                    "heartbeat_at": None,
                    "cancel_requested_at": None,
                },
            )
            self._append_event(
                run_id,
                RunEventType.QUEUED,
                RunLifecyclePayload(message=f"run retry queued: {error.get('type') or 'execution error'}"),
            )
            return
        _write_json(
            self._run_dir(run_id) / "result.json",
            {"run_id": run_id, "status": status, "error": error, "completed_at": _now()},
        )
        self._set_state(run_id, status, error=error)
        event_type = RunEventType.CANCELLED if status == "cancelled" else RunEventType.FAILED
        self._append_event(run_id, event_type, RunErrorPayload(message=error["message"], type=error["type"]))
        self._set_delivery(run_id, status, error=error)
        self._release_lock(run_id)

    def _run_has_terminal_state(self, run_id: str) -> bool:
        try:
            state = _read_json(self._run_dir(run_id) / "state.json")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        return state.get("status") in TERMINAL_RUN_STATUSES

    def _cancel_requested_at(self, run_id: str) -> str:
        try:
            state = _read_json(self._run_dir(run_id) / "state.json")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return ""
        return str(state.get("cancel_requested_at") or "").strip()

    def _ensure_execution_lease(self, run_id: str, *, lease_id: str) -> str:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise RunNotFoundError(run_id)
        if lease_id and self._lease_matches(run_id, lease_id):
            return lease_id
        state = _read_json(run_dir / "state.json")
        if state.get("status") in TERMINAL_RUN_STATUSES:
            raise RunStateError("terminal run cannot be executed")
        claim = self.claim_run(run_id, worker_id="direct")
        if claim is None:
            raise RunStateError("run could not be claimed for execution")
        return claim["lease_id"]

    async def _heartbeat_run_lock(self, run_id: str, *, lease_id: str) -> None:
        while True:
            await asyncio.sleep(RUN_LOCK_HEARTBEAT_SECONDS)
            run_dir = self._run_dir(run_id)
            lock_path = run_dir / "lock.json"
            try:
                state = _read_json(run_dir / "state.json")
                if state.get("status") not in {"queued", "running"}:
                    return
                lock = _read_json(lock_path) if lock_path.exists() else {}
                if str(lock.get("lease_id") or "").strip() != lease_id:
                    return
                created_at = str(lock.get("created_at") or state.get("created_at") or _now())
                now = _now()
                _write_json(
                    lock_path,
                    {
                        "pid": os.getpid(),
                        "worker_id": str(lock.get("worker_id") or ""),
                        "lease_id": lease_id,
                        "created_at": created_at,
                        "heartbeat_at": now,
                    },
                )
                self._set_state(run_id, "running", extra={"heartbeat_at": now})
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return

    def _lease_matches(self, run_id: str, lease_id: str) -> bool:
        if not lease_id:
            return False
        try:
            lock = _read_json(self._run_dir(run_id) / "lock.json")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        return str(lock.get("lease_id") or "").strip() == lease_id

    def _reconcile_run_if_stale(self, run_id: str) -> bool:
        run_dir = self._run_dir(run_id)
        try:
            state = _read_json(run_dir / "state.json")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        if state.get("status") != "running":
            return False

        now = datetime.now(timezone.utc)
        started_at = _parse_datetime(state.get("started_at")) or _parse_datetime(state.get("updated_at"))
        if started_at is not None and (now - started_at).total_seconds() > RUN_EXECUTION_TIMEOUT_SECONDS:
            return self._recover_active_run(
                run_id,
                error={
                    "type": "RunExecutionTimeoutError",
                    "message": f"run exceeded the {RUN_EXECUTION_TIMEOUT_SECONDS}-second execution limit",
                },
            )

        lock_path = run_dir / "lock.json"
        if not lock_path.exists():
            return False
        try:
            lock = _read_json(lock_path)
        except (json.JSONDecodeError, OSError):
            return False
        heartbeat_at = _parse_datetime(lock.get("heartbeat_at"))
        if heartbeat_at is not None and (now - heartbeat_at).total_seconds() > RUN_STALE_HEARTBEAT_SECONDS:
            return self._recover_active_run(
                run_id,
                error={
                    "type": "RunStaleHeartbeatError",
                    "message": f"run heartbeat is older than {RUN_STALE_HEARTBEAT_SECONDS} seconds",
                },
            )
        return False

    def _recover_active_run(self, run_id: str, *, error: dict[str, Any]) -> bool:
        run_dir = self._run_dir(run_id)
        try:
            state = _read_json(run_dir / "state.json")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        if state.get("status") in TERMINAL_RUN_STATUSES:
            return False
        attempts = int(state.get("attempts") or 0)
        max_attempts = int(state.get("max_attempts") or RUN_RETRY_MAX_ATTEMPTS)
        if attempts < max_attempts:
            self._release_lock(run_id)
            self._set_state(
                run_id,
                "queued",
                extra={
                    "last_error": error,
                    "worker_id": "",
                    "worker_lease_id": "",
                    "heartbeat_at": None,
                    "cancel_requested_at": None,
                },
            )
            self._append_event(
                run_id,
                RunEventType.QUEUED,
                RunLifecyclePayload(message=f"run recovered and requeued: {error.get('type') or 'stale run'}"),
            )
            return True
        self.fail_run(run_id, error=error)
        return True

    def _append_event(self, run_id: str, event_type: RunEventType | str, payload: RunEventPayload) -> None:
        run_dir = self._run_dir(run_id)
        state_path = run_dir / "state.json"
        state = _read_json(state_path)
        seq = int(state.get("seq") or 0) + 1
        state["seq"] = seq
        state["updated_at"] = _now()
        _write_json(state_path, state)
        event = RunEventRecord(
            seq=seq,
            run_id=run_id,
            type=event_type,
            created_at=state["updated_at"],
            payload=payload,
        )
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as event_file:
            event_file.write(json.dumps(event.to_json(), ensure_ascii=False, sort_keys=True) + "\n")

    def _summary_from_state(self, run_id: str) -> dict[str, Any]:
        run_input = self._load_input(run_id)
        metadata = run_input.get("metadata") if isinstance(run_input.get("metadata"), dict) else {}
        run_dir = self._run_dir(run_id)
        state = _read_json(run_dir / "state.json")
        delivery = _read_json(run_dir / "delivery.json") if (run_dir / "delivery.json").exists() else {}
        summary = {
            "run_id": run_id,
            "status": state.get("status", "queued"),
            "source": run_input.get("source", "api"),
            "agent_id": run_input.get("agent_id", ""),
            "session_id": run_input.get("session_id", ""),
            "created_at": run_input.get("created_at", ""),
            "updated_at": state.get("updated_at", ""),
            "seq": state.get("seq", 0),
            "delivery_status": delivery.get("status", ""),
            "usage": summarize_usage(_read_json(run_dir / "usage.json")) if (run_dir / "usage.json").exists() else None,
        }
        client_message_id = str(metadata.get("client_message_id") or "").strip()
        if client_message_id:
            summary["client_message_id"] = client_message_id
        return summary

    def _upsert_index(self, summary: dict[str, Any]) -> None:
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        index = self._read_index()
        runs = index.get("runs") if isinstance(index, dict) else []
        if not isinstance(runs, list):
            runs = []
        next_runs = [item for item in runs if isinstance(item, dict) and item.get("run_id") != summary["run_id"]]
        next_runs.insert(0, summary)
        _write_json(self._index_path, {"schema_version": 1, "runs": next_runs})

    def _read_index(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {"schema_version": 1, "runs": []}
        return _read_json(self._index_path)

    def _load_input(self, run_id: str) -> dict[str, Any]:
        input_path = self._run_dir(run_id) / "input.json"
        if not input_path.exists():
            raise RunNotFoundError(run_id)
        return _read_json(input_path)

    def _run_dir(self, run_id: str) -> Path:
        return self._runs_dir / run_id

    def _agent_workspace(self, agent_id: str) -> Path:
        if not agent_id or any(part in agent_id for part in ("/", "\\")) or agent_id in {".", ".."}:
            raise AgentConfigError("agents.definitions[].id must be a single path segment for filesystem access")
        return agent_workspace_path(self._workspace, agent_id)

    @property
    def _index_path(self) -> Path:
        return self._runs_dir / "index.json"

    def _new_run_id(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"run_{timestamp}_{uuid.uuid4().hex[:10]}"


@dataclass
class _RunStreamRecorder:
    run_id: str
    run_dir: Path
    append_event: Any
    content: str = ""
    buffer: str = ""
    node: str = ""
    agent: str = ""
    source_class: str = ""
    last_flush_at: float = 0.0
    had_delta: bool = False
    thinking: tuple[str, ...] = ()
    snapshot_dirty: bool = False

    @classmethod
    def restore(cls, *, run_id: str, run_dir: Path, append_event: Any) -> _RunStreamRecorder:
        partial_path = run_dir / "partial.json"
        partial = _read_json(partial_path) if partial_path.exists() else {}
        raw_thinking = partial.get("thinking")
        return cls(
            run_id=run_id,
            run_dir=run_dir,
            append_event=append_event,
            content=str(partial.get("content") or ""),
            thinking=tuple(str(item) for item in raw_thinking) if isinstance(raw_thinking, list) else (),
        )

    def record(self, event: DeepAgentStreamEvent) -> None:
        try:
            if event.type == RunEventType.ASSISTANT_DELTA and isinstance(event.payload, DeepAgentMessageDeltaPayload):
                self._record_delta(event.payload)
                return
            self.append_event(self.run_id, event.type, event.payload)
            self.record_snapshot_event(event.type, event.payload)
        except Exception:
            return

    def record_snapshot_event(self, event_type: RunEventType | str, payload: RunEventPayload) -> None:
        text = _thinking_text(event_type, payload)
        if not text:
            return
        self.thinking = _merge_thinking(self.thinking, (text,))
        self.snapshot_dirty = True
        self._flush()

    def finish(self, final_content: str) -> None:
        if not self.had_delta and not self.thinking:
            return
        if final_content:
            self.content = final_content
        self._flush(force=True)
        _write_json(
            self.run_dir / "partial.json",
            {
                "run_id": self.run_id,
                "status": "completed",
                "content": self.content,
                "thinking": list(self.thinking),
                "thinking_status": "completed",
                "thinking_collapsed": True,
                "updated_at": _now(),
            },
        )

    def fail(self, error: dict[str, Any]) -> None:
        self.thinking = _merge_thinking(self.thinking, ("运行失败，已停止生成正文",))
        _write_json(
            self.run_dir / "partial.json",
            {
                "run_id": self.run_id,
                "status": "failed",
                "content": self.content,
                "thinking": list(self.thinking),
                "thinking_status": "failed",
                "thinking_collapsed": True,
                "error": error,
                "updated_at": _now(),
            },
        )

    def wait_for_approval(self, payload: RunApprovalRequiredPayload) -> None:
        self.record_snapshot_event(RunEventType.APPROVAL_REQUIRED, payload)
        self._flush(force=True)
        _write_json(
            self.run_dir / "partial.json",
            {
                "run_id": self.run_id,
                "status": "waiting_approval",
                "content": self.content,
                "thinking": list(self.thinking),
                "thinking_status": "waiting_approval",
                "thinking_collapsed": False,
                "updated_at": _now(),
            },
        )

    def _record_delta(self, payload: DeepAgentMessageDeltaPayload) -> None:
        delta = payload.delta
        if not delta:
            return
        self.content += delta
        self.buffer += delta
        self.node = payload.node
        self.agent = payload.agent
        self.source_class = payload.source_class
        self.had_delta = True
        self._flush()

    def _flush(self, *, force: bool = False) -> None:
        if not force and not self.buffer and not self.snapshot_dirty:
            return
        now_monotonic = time.monotonic()
        if (
            not force
            and len(self.buffer) < STREAM_PARTIAL_FLUSH_CHARS
            and not self.snapshot_dirty
            and now_monotonic - self.last_flush_at < STREAM_PARTIAL_FLUSH_SECONDS
        ):
            return
        delta = self.buffer
        self.buffer = ""
        self.snapshot_dirty = False
        self.last_flush_at = now_monotonic
        _write_json(
            self.run_dir / "partial.json",
            {
                "run_id": self.run_id,
                "status": "streaming",
                "content": self.content,
                "thinking": list(self.thinking),
                "thinking_status": "running",
                "thinking_collapsed": False,
                "updated_at": _now(),
            },
        )
        if delta:
            self.append_event(
                self.run_id,
                RunEventType.ASSISTANT_DELTA,
                DeepAgentMessageDeltaPayload(
                    delta=delta,
                    node=self.node,
                    agent=self.agent,
                    source_class=self.source_class,
                ),
            )


def _thinking_text(event_type: RunEventType | str, payload: RunEventPayload) -> str:
    if event_type == RunEventType.RUNNING and isinstance(payload, RunLifecyclePayload):
        return payload.message or "DeepAgent 已开始处理"
    if event_type == RunEventType.AGENT_UPDATE and isinstance(payload, DeepAgentGraphUpdatePayload):
        if payload.preview:
            return payload.preview
        nodes = ", ".join(item for item in payload.nodes if item)
        return f"图节点更新：{nodes}" if nodes else "DeepAgent 状态已更新"
    if event_type == RunEventType.SUBAGENT_RESPONSE and isinstance(payload, DeepAgentSubagentResponsePayload):
        label = payload.agent or "sub-agent"
        content = payload.content.strip()
        if len(content) > 1000:
            content = f"{content[:1000]}..."
        return f"子 Agent {label}：{content}" if content else f"子 Agent {label} 已完成"
    if event_type == RunEventType.APPROVAL_REQUIRED and isinstance(payload, RunApprovalRequiredPayload):
        names = [action.name for interrupt in payload.request.interrupts for action in interrupt.actions]
        return f"等待审批：{', '.join(names)}" if names else "等待用户审批"
    if event_type == RunEventType.STREAM_FALLBACK:
        message = getattr(payload, "message", "")
        return str(message or "当前运行时不支持增量流，已切换为最终结果模式")
    if event_type == RunEventType.IMAGE_ATTACHMENTS_TEXTIFIED and isinstance(payload, ImageAttachmentsTextifiedPayload):
        return payload.message or "图片已转为文本附件说明"
    return ""


def _merge_thinking(current: tuple[str, ...], updates: tuple[str, ...]) -> tuple[str, ...]:
    merged = [item for item in current if item]
    for update in updates:
        text = str(update or "").strip()
        if not text or (merged and merged[-1] == text):
            continue
        merged.append(text)
    return tuple(merged[-20:])


def _public_agent(agent: AgentDefinition) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "system_prompt": agent.system_prompt,
        "model_id": agent.model_id,
        "context_ids": list(agent.context_ids),
        "webdav": {"enabled": agent.webdav.enabled, "directories": [
            {"path": d.path, "permission": d.permission, "description": d.description}
            for d in agent.webdav.directories]},
        "deepagent": {
            "max_iterations": agent.deepagent.max_iterations,
            "todo_list": agent.deepagent.todo_list,
            "tools": list(agent.deepagent.tools),
        },
    }


def _public_model(model: ModelDefinition) -> dict[str, Any]:
    return {
        "id": model.id,
        "name": model.name,
        "provider": model.provider.value,
        "base_url": model.base_url,
        "model": model.model,
        "temperature": model.temperature,
        "supports_images": model.supports_images,
        "input_price_per_million": model.input_price_per_million,
        "output_price_per_million": model.output_price_per_million,
        "price_currency": model.price_currency,
    }


def _runtime_messages(
    history: list[dict[str, Any]],
    *,
    fallback_content: str,
    fallback_attachments: tuple[RuntimeAttachment, ...] = (),
    workspace: Path,
) -> tuple[RuntimeMessage, ...]:
    messages: list[RuntimeMessage] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        attachments = _runtime_attachments(item.get("attachments") or [], workspace=workspace)
        if role and (content or attachments):
            messages.append(RuntimeMessage(role=role, content=content, attachments=attachments))
    if not messages and (fallback_content.strip() or fallback_attachments):
        messages.append(
            RuntimeMessage(
                role="user",
                content=fallback_content.strip(),
                attachments=fallback_attachments,
            )
        )
    return tuple(messages)


def _current_run_messages(history: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    current = [item for item in history if isinstance(item, dict) and str(item.get("run_id") or "") == run_id]
    return current[-1:] if current else []


def _textify_image_attachments(
    messages: tuple[RuntimeMessage, ...],
    *,
    workspace: Path,
) -> tuple[tuple[RuntimeMessage, ...], int]:
    textified: list[RuntimeMessage] = []
    total_images = 0
    for message in messages:
        image_attachments = tuple(attachment for attachment in message.attachments if attachment.is_image)
        if not image_attachments:
            textified.append(message)
            continue
        total_images += len(image_attachments)
        non_image_attachments = tuple(attachment for attachment in message.attachments if not attachment.is_image)
        textified.append(
            RuntimeMessage(
                role=message.role,
                content=_join_prompt_sections(message.content, _image_attachment_notice(image_attachments, workspace=workspace)),
                attachments=non_image_attachments,
            )
        )
    return tuple(textified), total_images


def _image_attachment_notice(attachments: tuple[RuntimeAttachment, ...], *, workspace: Path) -> str:
    lines = [
        "[系统附件说明]",
        f"用户发送了 {len(attachments)} 张图片，但当前 Agent 主模型未开启图片能力。系统没有读取图片画面内容，已去掉图片二进制，仅保留附件元信息。",
    ]
    for index, attachment in enumerate(attachments, start=1):
        lines.append(f"- image {index}: {_image_attachment_metadata(attachment, workspace=workspace)}")
    return "\n".join(lines)


def _image_attachment_metadata(attachment: RuntimeAttachment, *, workspace: Path) -> str:
    parts = [
        f"filename={attachment.filename or attachment.path.name}",
        f"mime={attachment.mime}",
    ]
    try:
        parts.append(f"size={attachment.path.stat().st_size} bytes")
    except OSError:
        pass
    try:
        relative_path = attachment.path.resolve().relative_to(workspace.resolve()).as_posix()
        parts.append(f"workspace_path={relative_path}")
    except ValueError:
        pass
    return ", ".join(parts)


def _join_prompt_sections(*sections: str) -> str:
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


def _runtime_attachments(raw: Any, *, workspace: Path) -> tuple[RuntimeAttachment, ...]:
    if not isinstance(raw, list | tuple):
        return ()
    root = workspace.resolve()
    attachments: list[RuntimeAttachment] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        workspace_path = str(item.get("workspace_path") or "").strip()
        if not workspace_path:
            continue
        target = (workspace / workspace_path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            continue
        attachments.append(
            RuntimeAttachment(
                type=str(item.get("type") or "file").strip().lower(),
                mime=str(item.get("mime") or "application/octet-stream").strip(),
                filename=str(item.get("filename") or target.name),
                path=target,
            )
        )
    return tuple(attachments)


def _normalize_attachment_metadata(attachments: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    metadata: list[dict[str, Any]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        item = {
            key: value
            for key, value in attachment.items()
            if key not in {"bytes", "data_url", "content_base64", "base64", "content", "source_path"}
        }
        if item:
            metadata.append(item)
    return tuple(metadata)


def _has_image_attachment(attachments: tuple[dict[str, Any], ...]) -> bool:
    return any(
        str(attachment.get("type") or "").lower() == "image"
        or str(attachment.get("mime") or "").lower().startswith("image/")
        for attachment in attachments
        if isinstance(attachment, dict)
    )


def _initial_delivery_target(source: str, metadata: dict[str, Any], *, agent_id: str) -> dict[str, str]:
    nested = metadata.get("delivery") if isinstance(metadata.get("delivery"), dict) else {}
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
        "agent_id": agent_id,
        "peer_id": str(raw.get("peer_id") or to_user_id).strip(),
        "peer_type": str(raw.get("peer_type") or "private").strip(),
        "to_user_id": to_user_id,
        "context_token": str(raw.get("context_token") or "").strip(),
    }


def _runtime_options(raw: Any, *, name: str = "", webdav: Any = None) -> DeepAgentRuntimeOptions:
    from server.infrastructure.config import parse_agent_webdav
    options = raw if isinstance(raw, dict) else {}
    return DeepAgentRuntimeOptions(
        max_iterations=int(options.get("max_iterations") or 60),
        name=name,
        tools=tuple(str(item).strip() for item in options.get("tools") or [] if str(item).strip()),
        todo_list=bool(options.get("todo_list", True)),
        webdav=parse_agent_webdav(webdav or {}),
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lock_is_stale(path: Path) -> bool:
    try:
        payload = _read_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return True
    heartbeat = _parse_datetime(payload.get("heartbeat_at")) or _parse_datetime(payload.get("created_at"))
    if heartbeat is None:
        return True
    return (datetime.now(timezone.utc) - heartbeat).total_seconds() > RUN_STALE_HEARTBEAT_SECONDS
