from dataclasses import dataclass

from server.app.auth_service import AuthService
from server.app.proxy_service import ProxyService
from server.app.system_update_service import SystemUpdateService
from server.infrastructure.session import SessionCodec


@dataclass(frozen=True)
class AppContainer:
    auth_service: AuthService
    proxy_service: ProxyService
    system_update_service: SystemUpdateService
    session_codec: SessionCodec
