from dataclasses import dataclass
from typing import Any, Literal


LogPayloadType = Literal["json", "text"]


@dataclass(frozen=True)
class LogsPayload:
    type: LogPayloadType
    data: Any

    @classmethod
    def from_json(cls, data: Any) -> "LogsPayload":
        return cls(type="json", data=data)

    @classmethod
    def from_text(cls, text: str) -> "LogsPayload":
        lines = [line for line in text.splitlines() if line.strip()]
        return cls(type="text", data=lines)
