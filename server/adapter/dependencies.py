from dataclasses import dataclass

from server.app.auth_service import AuthService
from server.app.logs_service import LogsService
from server.infrastructure.session import SessionCodec


@dataclass(frozen=True)
class AppContainer:
    auth_service: AuthService
    logs_service: LogsService
    session_codec: SessionCodec
