from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
from typing import Any


class RunEventType(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    ASSISTANT_DELTA = "assistant_delta"
    AGENT_UPDATE = "agent_update"
    SUBAGENT_RESPONSE = "subagent_response"
    STREAM_FALLBACK = "stream_fallback"
    IMAGE_ATTACHMENTS_TEXTIFIED = "image_attachments_textified"
    COMPLETED = "completed"
    FAILED = "failed"


class RunEventPayload:
    kind = "generic"

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = asdict(self) if is_dataclass(self) else {}
        data["kind"] = self.kind
        return data


@dataclass(frozen=True)
class GenericRunEventPayload(RunEventPayload):
    data: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        payload = dict(self.data)
        payload.setdefault("kind", self.kind)
        return payload


@dataclass(frozen=True)
class RunLifecyclePayload(RunEventPayload):
    kind = "run_lifecycle"

    message: str


@dataclass(frozen=True)
class RunErrorPayload(RunEventPayload):
    kind = "run_error"

    message: str
    type: str


@dataclass(frozen=True)
class DeepAgentMessageDeltaPayload(RunEventPayload):
    kind = "deepagent_message_delta"

    delta: str
    node: str = ""
    agent: str = ""
    source_class: str = ""


@dataclass(frozen=True)
class DeepAgentGraphUpdatePayload(RunEventPayload):
    kind = "deepagent_graph_update"

    nodes: tuple[str, ...]
    preview: str = ""
    source_class: str = ""


@dataclass(frozen=True)
class DeepAgentSubagentResponsePayload(RunEventPayload):
    kind = "deepagent_subagent_response"

    content: str
    namespace: tuple[str, ...]
    agent: str = ""
    node: str = ""
    source_class: str = ""


@dataclass(frozen=True)
class StreamFallbackPayload(RunEventPayload):
    kind = "deepagent_stream_fallback"

    message: str
    error: str = ""


@dataclass(frozen=True)
class ImageAttachmentsTextifiedPayload(RunEventPayload):
    kind = "image_attachments_textified"

    message: str
    items: int


@dataclass(frozen=True)
class RunEventRecord:
    seq: int
    run_id: str
    type: RunEventType | str
    created_at: str
    payload: RunEventPayload

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "run_id": self.run_id,
            "type": str(self.type),
            "created_at": self.created_at,
            "payload": self.payload.to_json(),
        }


def run_event_payload_from_json(event_type: str, payload: Any) -> RunEventPayload:
    data = payload if isinstance(payload, dict) else {}
    if event_type in {RunEventType.QUEUED, RunEventType.RUNNING, RunEventType.COMPLETED}:
        return RunLifecyclePayload(message=str(data.get("message") or ""))
    if event_type == RunEventType.FAILED:
        return RunErrorPayload(message=str(data.get("message") or ""), type=str(data.get("type") or ""))
    if event_type == RunEventType.ASSISTANT_DELTA:
        return DeepAgentMessageDeltaPayload(
            delta=str(data.get("delta") or ""),
            node=str(data.get("node") or ""),
            agent=str(data.get("agent") or ""),
            source_class=str(data.get("source_class") or ""),
        )
    if event_type == RunEventType.AGENT_UPDATE:
        raw_nodes = data.get("nodes") or ()
        nodes = tuple(str(item) for item in raw_nodes) if isinstance(raw_nodes, list | tuple) else ()
        return DeepAgentGraphUpdatePayload(
            nodes=nodes,
            preview=str(data.get("preview") or ""),
            source_class=str(data.get("source_class") or ""),
        )
    if event_type == RunEventType.SUBAGENT_RESPONSE:
        raw_namespace = data.get("namespace") or ()
        namespace = tuple(str(item) for item in raw_namespace) if isinstance(raw_namespace, list | tuple) else ()
        return DeepAgentSubagentResponsePayload(
            content=str(data.get("content") or ""),
            namespace=namespace,
            agent=str(data.get("agent") or ""),
            node=str(data.get("node") or ""),
            source_class=str(data.get("source_class") or ""),
        )
    if event_type == RunEventType.STREAM_FALLBACK:
        return StreamFallbackPayload(message=str(data.get("message") or ""), error=str(data.get("error") or ""))
    if event_type == RunEventType.IMAGE_ATTACHMENTS_TEXTIFIED:
        return ImageAttachmentsTextifiedPayload(
            message=str(data.get("message") or ""),
            items=int(data.get("items") or 0),
        )
    return GenericRunEventPayload(data=data)


def run_event_from_json(raw: Any) -> RunEventRecord:
    data = raw if isinstance(raw, dict) else {}
    event_type = str(data.get("type") or "")
    return RunEventRecord(
        seq=int(data.get("seq") or 0),
        run_id=str(data.get("run_id") or ""),
        type=event_type,
        created_at=str(data.get("created_at") or ""),
        payload=run_event_payload_from_json(event_type, data.get("payload") or {}),
    )
