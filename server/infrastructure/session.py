import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionCodec:
    secret: str

    def issue(self) -> str:
        digest = hmac.new(
            self.secret.encode("utf-8"),
            b"super-personal-platform-session",
            hashlib.sha256,
        ).hexdigest()
        return f"v1.{digest}"

    def verify(self, value: str | None) -> bool:
        if not value:
            return False
        return hmac.compare_digest(value, self.issue())
