from dataclasses import dataclass
from pathlib import Path

from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.nutstore_service import NutstoreService
from server.app.run_service import RunService
from server.app.schedule_service import ScheduleService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import SystemUpdateService
from server.app.wechat_channel_manager import WechatChannelManager
from server.app.workspace_file_service import WorkspaceFileService
from server.infrastructure.session import SessionCodec


@dataclass(frozen=True)
class AppContainer:
    workspace: Path
    auth_service: AuthService
    config_file_service: ConfigFileService
    run_service: RunService
    nutstore_service: NutstoreService
    schedule_service: ScheduleService
    system_log_service: SystemLogService
    system_update_service: SystemUpdateService
    workspace_file_service: WorkspaceFileService
    session_codec: SessionCodec
    wechat_channel_manager: WechatChannelManager | None = None
