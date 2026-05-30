from dataclasses import dataclass


class ChatSessionNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class ChatImageData:
    mime_type: str
    data: str

    def __post_init__(self) -> None:
        if not self.mime_type.strip():
            raise ValueError("image mime_type is required")
        if not self.data.strip():
            raise ValueError("image data is required")


@dataclass(frozen=True)
class ChatCheckpointData:
    stage: str
    title: str
    detail: str = ""


@dataclass(frozen=True)
class ChatMessageData:
    role: str
    content: str
    images: tuple[ChatImageData, ...] = ()
    checkpoints: tuple[ChatCheckpointData, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ValueError(f"message role must be user or assistant, got {self.role}")


@dataclass(frozen=True)
class ChatSession:
    id: str
    title: str
    agent_id: str
    messages: tuple[ChatMessageData, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("session id is required")
        if not self.agent_id.strip():
            raise ValueError("session agent_id is required")
