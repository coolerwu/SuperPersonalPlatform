from dataclasses import dataclass

from server.app.agent_chat_service import AgentChatService
from server.app.auth_service import AuthService
from server.app.chat_session_service import ChatSessionService
from server.app.config_file_service import ConfigFileService
from server.app.portfolio_service import PortfolioService
from server.app.proxy_service import ProxyService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import SystemUpdateService
from server.app.self_dev_service import SelfDevService
from server.app.job_service import JobService
from server.app.wechat_channel_manager import WechatChannelManager
from server.infrastructure.session import SessionCodec


@dataclass(frozen=True)
class AppContainer:
    auth_service: AuthService
    config_file_service: ConfigFileService
    proxy_service: ProxyService
    system_log_service: SystemLogService
    system_update_service: SystemUpdateService
    session_codec: SessionCodec
    agent_chat_service: AgentChatService | None = None
    chat_session_service: ChatSessionService | None = None
    portfolio_service: PortfolioService | None = None
    job_service: JobService | None = None
    self_dev_service: SelfDevService | None = None
    wechat_channel_manager: WechatChannelManager | None = None
