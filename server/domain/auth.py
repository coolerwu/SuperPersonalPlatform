from dataclasses import dataclass
from hmac import compare_digest

from server.domain.errors import InvalidTokenError


@dataclass(frozen=True)
class AuthToken:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("auth token must not be empty")

    def verify(self, candidate: str) -> None:
        if not compare_digest(self.value, candidate):
            raise InvalidTokenError("invalid token")
