from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.app.session_service import SessionService
from server.domain.agent_config import AgentConfigError, AgentDefinition, ModelDefinition
from server.infrastructure.config import load_settings
from server.infrastructure.deepagent_runtime import DeepAgentRuntime, DeepAgentRuntimeOptions, RuntimeMessage


RUN_STATUSES = {"queued", "running", "completed", "failed"}


class RunNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class RunInput:
    run_id: str
    source: str
    agent_id: str
    session_id: str
    content: str
    context_ids: tuple[str, ...]
    created_at: str
    metadata: dict[str, Any]
    snapshot: dict[str, Any]


class RunService:
    def __init__(self, workspace: Path, session_service: SessionService | None = None) -> None:
        self._workspace = workspace
        self._runs_dir = workspace / "runs"
        self._session_service = session_service

    async def create_run(
        self,
        *,
        content: str,
        agent_id: str = "",
        context_ids: tuple[str, ...] = (),
        source: str = "api",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = content.strip()
        if not text:
            raise ValueError("content is required")
        if session_id and self._session_service is not None and not self._session_service.exists(session_id):
            raise ValueError("session does not exist")

        settings = load_settings(self._workspace / "config.yaml")
        agent = self._resolve_agent(settings.agent_workspace.agents, agent_id)
        model_id = agent.model_id or settings.agent_workspace.default_model_id
        model = settings.agent_workspace.get_model(model_id)
        now = _now()
        run_id = self._new_run_id()
        selected_context_ids = context_ids or agent.context_ids
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)

        run_input = RunInput(
            run_id=run_id,
            source=source,
            agent_id=agent.id,
            session_id=session_id,
            content=text,
            context_ids=selected_context_ids,
            created_at=now,
            metadata=metadata or {},
            snapshot={
                "agent": _public_agent(agent),
                "model": _public_model(model),
                "context": self._snapshot_context(),
            },
        )
        _write_json(run_dir / "input.json", asdict(run_input))
        _write_json(
            run_dir / "state.json",
            {
                "run_id": run_id,
                "session_id": session_id,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "seq": 0,
            },
        )
        _write_json(run_dir / "lock.json", {"pid": os.getpid(), "created_at": now})
        _write_json(run_dir / "delivery.json", {"source": source, "session_id": session_id, "status": "pending"})
        if session_id and self._session_service is not None:
            self._session_service.append_message(
                session_id,
                role="user",
                content=text,
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
        self._append_event(run_id, "queued", {"message": "run queued"})
        self._upsert_index(self._summary_from_state(run_id))
        return self.get_run(run_id)

    async def execute_run(self, run_id: str) -> dict[str, Any]:
        run_input = self._load_input(run_id)
        settings = load_settings(self._workspace / "config.yaml")
        model_id = str(run_input["snapshot"]["agent"].get("model_id") or settings.agent_workspace.default_model_id)
        model = settings.agent_workspace.get_model(model_id)
        agent_snapshot = run_input["snapshot"]["agent"]
        system_prompt = str(agent_snapshot.get("system_prompt") or "")
        content = str(run_input.get("content") or "")
        session_id = str(run_input.get("session_id") or "")
        history = self._session_service.read_messages(session_id) if session_id and self._session_service is not None else []
        runtime_messages = _runtime_messages(history, fallback_content=content)
        runtime_options = _runtime_options(agent_snapshot.get("deepagent") if isinstance(agent_snapshot, dict) else {})
        self._set_state(run_id, "running")
        self._append_event(run_id, "running", {"message": "DeepAgent started"})

        try:
            result = await DeepAgentRuntime(model, context_workspace=self._workspace / "context").run(
                instructions=system_prompt,
                messages=runtime_messages,
                options=runtime_options,
            )
        except Exception as exc:
            error = {"message": str(exc), "type": exc.__class__.__name__}
            _write_json(
                self._run_dir(run_id) / "result.json",
                {"run_id": run_id, "status": "failed", "error": error, "completed_at": _now()},
            )
            self._set_state(run_id, "failed", error=error)
            self._append_event(run_id, "failed", error)
            self._set_delivery(run_id, "failed", error=error)
            raise

        result_payload = {
            "run_id": run_id,
            "status": "completed",
            "content": result,
            "completed_at": _now(),
        }
        _write_json(self._run_dir(run_id) / "result.json", result_payload)
        if session_id and self._session_service is not None:
            self._session_service.append_message(
                session_id,
                role="assistant",
                content=result,
                run_id=run_id,
                metadata={"source": run_input.get("source", "api")},
            )
        self._set_state(run_id, "completed")
        self._append_event(run_id, "completed", {"message": "run completed"})
        self._set_delivery(run_id, "ready")
        return self.get_run(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        index = self._read_index()
        runs = index.get("runs") if isinstance(index, dict) else []
        if not isinstance(runs, list):
            return []
        return [item for item in runs if isinstance(item, dict)]

    def get_run(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise RunNotFoundError(run_id)
        payload = {
            "run_id": run_id,
            "input": _read_json(run_dir / "input.json"),
            "state": _read_json(run_dir / "state.json"),
            "delivery": _read_json(run_dir / "delivery.json") if (run_dir / "delivery.json").exists() else {},
            "result": None,
        }
        result_path = run_dir / "result.json"
        if result_path.exists():
            payload["result"] = _read_json(result_path)
        return payload

    def get_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
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
                events.append(item)
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
    ) -> None:
        if status not in RUN_STATUSES:
            raise ValueError("invalid run status")
        state_path = self._run_dir(run_id) / "state.json"
        state = _read_json(state_path)
        state["status"] = status
        state["updated_at"] = _now()
        if error is not None:
            state["error"] = error
        _write_json(state_path, state)
        self._upsert_index(self._summary_from_state(run_id))

    def _set_delivery(
        self,
        run_id: str,
        status: str,
        *,
        error: dict[str, Any] | None = None,
    ) -> None:
        run_input = self._load_input(run_id)
        payload: dict[str, Any] = {
            "source": run_input.get("source", "api"),
            "session_id": run_input.get("session_id", ""),
            "status": status,
        }
        if error is not None:
            payload["error"] = error
        _write_json(self._run_dir(run_id) / "delivery.json", payload)

    def _append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        run_dir = self._run_dir(run_id)
        state_path = run_dir / "state.json"
        state = _read_json(state_path)
        seq = int(state.get("seq") or 0) + 1
        state["seq"] = seq
        state["updated_at"] = _now()
        _write_json(state_path, state)
        event = {
            "seq": seq,
            "run_id": run_id,
            "type": event_type,
            "created_at": state["updated_at"],
            "payload": payload,
        }
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as event_file:
            event_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def _summary_from_state(self, run_id: str) -> dict[str, Any]:
        run_input = self._load_input(run_id)
        state = _read_json(self._run_dir(run_id) / "state.json")
        return {
            "run_id": run_id,
            "status": state.get("status", "queued"),
            "source": run_input.get("source", "api"),
            "agent_id": run_input.get("agent_id", ""),
            "session_id": run_input.get("session_id", ""),
            "created_at": run_input.get("created_at", ""),
            "updated_at": state.get("updated_at", ""),
            "seq": state.get("seq", 0),
        }

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

    @property
    def _index_path(self) -> Path:
        return self._runs_dir / "index.json"

    def _new_run_id(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"run_{timestamp}_{uuid.uuid4().hex[:10]}"


def _public_agent(agent: AgentDefinition) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "system_prompt": agent.system_prompt,
        "model_id": agent.model_id,
        "context_ids": list(agent.context_ids),
        "deepagent": {
            "max_iterations": agent.deepagent.max_iterations,
            "name": agent.deepagent.name,
            "debug": agent.deepagent.debug,
            "use_longterm_memory": agent.deepagent.use_longterm_memory,
            "tools": list(agent.deepagent.tools),
            "interrupt_on": list(agent.deepagent.interrupt_on),
            "middleware": list(agent.deepagent.middleware),
            "subagents": list(agent.deepagent.subagents),
            "response_format": agent.deepagent.response_format,
            "context_schema": agent.deepagent.context_schema,
            "checkpointer": agent.deepagent.checkpointer,
            "store": agent.deepagent.store,
            "cache": agent.deepagent.cache,
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
    }


def _runtime_messages(history: list[dict[str, Any]], *, fallback_content: str) -> tuple[RuntimeMessage, ...]:
    messages: list[RuntimeMessage] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role and content:
            messages.append(RuntimeMessage(role=role, content=content))
    if not messages and fallback_content.strip():
        messages.append(RuntimeMessage(role="user", content=fallback_content.strip()))
    return tuple(messages)


def _runtime_options(raw: Any) -> DeepAgentRuntimeOptions:
    options = raw if isinstance(raw, dict) else {}
    return DeepAgentRuntimeOptions(
        max_iterations=int(options.get("max_iterations") or 60),
        name=str(options.get("name") or "").strip(),
        debug=bool(options.get("debug", False)),
        tools=tuple(str(item).strip() for item in options.get("tools") or [] if str(item).strip()),
        interrupt_on=tuple(str(item).strip() for item in options.get("interrupt_on") or [] if str(item).strip()),
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
