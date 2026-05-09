from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobLease:
    worker_id: str
    leased_at: str


@dataclass(frozen=True)
class Job:
    id: str
    task_id: str
    type: str
    status: JobStatus
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    lease: JobLease | None = None
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def create(cls, task_id: str, job_type: str, payload: dict[str, Any] | None = None) -> "Job":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=uuid4().hex,
            task_id=task_id,
            type=job_type,
            status=JobStatus.QUEUED,
            payload=payload or {},
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any], task_id: str = "") -> "Job":
        lease_raw = raw.get("lease")
        if isinstance(lease_raw, JobLease):
            lease = lease_raw
        elif isinstance(lease_raw, dict):
            lease = JobLease(**lease_raw)
        else:
            lease = None
        return cls(
            id=str(raw.get("id") or ""),
            task_id=str(raw.get("task_id") or task_id),
            type=str(raw.get("type") or ""),
            status=JobStatus(str(raw.get("status") or JobStatus.QUEUED)),
            payload=raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
            result=raw.get("result") if isinstance(raw.get("result"), dict) else {},
            lease=lease,
            attempts=int(raw.get("attempts") or 0),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["status"] = str(self.status)
        return raw
