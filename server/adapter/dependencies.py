from dataclasses import dataclass

from server.app.agent_chat_service import AgentChatService
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.proxy_service import ProxyService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import SystemUpdateService
from server.app.self_dev_service import SelfDevService
from server.app.terminal_session_service import TerminalSessionService
from server.infrastructure.session import SessionCodec


@dataclass(frozen=True)
class AppContainer:
    auth_service: AuthService
    config_file_service: ConfigFileService
    proxy_service: ProxyService
    system_log_service: SystemLogService
    system_update_service: SystemUpdateService
    terminal_session_service: TerminalSessionService
    session_codec: SessionCodec
    agent_chat_service: AgentChatService | None = None
    self_dev_service: SelfDevService | None = None
