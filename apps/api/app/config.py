import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_HOME = Path(os.environ.get("APP_HOME", Path.home() / ".super-personal-platform"))


class Settings(BaseSettings):
    # `$APP_HOME/.env` is the shared config; a `.env` in the startup directory can override it.
    model_config = SettingsConfigDict(
        env_file=(str(APP_HOME / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_auth_token: str = "dev-token"
    app_database_url: str = f"sqlite+aiosqlite:///{APP_HOME / 'data' / 'app.db'}"
    app_data_dir: Path = APP_HOME / "data"
    app_workspaces_dir: Path | None = None
    app_skills_dir: Path | None = None
    app_cors_origins: str = "http://localhost:5173"

    # Standard outbound proxy settings. One proxy applies to every HTTP client
    # (LLM SDKs, web tools, WeChat, market data). The values are also exported
    # to the process environment below so third-party SDKs see them.
    http_proxy: str | None = None
    https_proxy: str | None = None
    no_proxy: str | None = None

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.7

    run_timeout_seconds: int = 300
    run_recursion_limit: int = 500
    max_history_messages: int = 40

    # Cron jobs run inside the API process. The tick only needs to be shorter
    # than the smallest supported interval (one minute).
    cron_tick_seconds: int = 20
    cron_default_timezone: str = "Asia/Shanghai"

    # Browser tools are skill-gated; these bound one Run's browser session.
    browser_concurrency: int = 2
    browser_navigation_timeout_ms: int = 30_000
    browser_action_timeout_ms: int = 10_000
    browser_settle_ms: int = 500
    browser_max_elements: int = 120
    browser_max_text_chars: int = 4_000
    browser_max_snapshot_chars: int = 12_000
    browser_screenshot_max_bytes: int = 4_000_000
    browser_no_sandbox: bool = False

    # WeChat personal-account channels (iLink Bot API). The runtime runs
    # inside the API process; these bound inbound text, reply chunking, and
    # delivery retries.
    channel_reply_max_chars: int = 1800
    channel_delivery_max_attempts: int = 3
    channel_inbound_max_chars: int = 8000
    weixin_long_poll_timeout_ms: int = 35_000
    weixin_api_timeout_ms: int = 15_000
    weixin_qr_timeout_ms: int = 35_000

    # Personal knowledge base mirrored from a WebDAV folder (Nutstore).
    # The mirror is mounted as /knowledge/ for Agents that enable the
    # knowledge-base skill; a background task syncs it every few minutes.
    kb_webdav_url: str = ""
    kb_webdav_username: str = ""
    kb_webdav_password: str = ""
    kb_webdav_root: str = ""
    kb_sync_interval_seconds: int = 300
    kb_sync_timeout_seconds: int = 30
    kb_max_file_bytes: int = 50 * 1024 * 1024
    app_knowledge_dir: Path | None = None

    # Crypto market data (Bitget public REST) and backtesting. Bitget is not
    # reachable directly from every network, so the proxy can be set explicitly.
    bitget_base_url: str = "https://api.bitget.com"
    market_http_timeout_seconds: int = 15
    market_rate_limit_per_second: float = 5.0
    market_max_retries: int = 3
    market_gap_repair_bars: int = 50_000
    market_gap_repair_ranges: int = 50
    backtest_concurrency: int = 1
    backtest_timeout_seconds: int = 120
    backtest_fetch_timeout_seconds: int = 600
    backtest_tool_wait_seconds: int = 90
    backtest_memory_limit_mb: int = 2048
    backtest_max_bars: int = 200_000
    backtest_max_trades: int = 5_000
    backtest_equity_points: int = 2_000
    backtest_future_check: bool = True

    @property
    def checkpoint_dir(self) -> Path:
        return self.app_data_dir / "checkpoints"

    @property
    def workspaces_dir(self) -> Path:
        return self.app_workspaces_dir or (APP_HOME / "workspaces")

    @property
    def skills_dir(self) -> Path:
        return self.app_skills_dir or (APP_HOME / "skills")

    @property
    def knowledge_dir(self) -> Path:
        return (self.app_knowledge_dir or (APP_HOME / "knowledge")).expanduser()

    @property
    def knowledge_state_path(self) -> Path:
        return self.app_data_dir / "knowledge_sync_state.json"

    @property
    def market_db_path(self) -> Path:
        return self.app_data_dir / "market.db"

    @property
    def backtests_dir(self) -> Path:
        return self.app_data_dir / "backtests"

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.app_cors_origins.split(",") if item.strip()]

    @property
    def run_concurrency(self) -> int:
        # A session still has at most one active Run; different sessions may run
        # in parallel. The pool follows the browser tab limit so browser Runs
        # cannot multiply Chromium instances beyond that bound.
        return max(1, self.browser_concurrency)


settings = Settings()

# pydantic reads `$APP_HOME/.env`, but httpx/OpenAI SDKs read os.environ.
# Export the configured proxy once so every outbound client uses it, including
# when the service is started without shell proxy exports.
for _proxy_name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
    _proxy_value = getattr(settings, _proxy_name.lower(), None)
    if _proxy_value:
        os.environ.setdefault(_proxy_name, _proxy_value)
        os.environ.setdefault(_proxy_name.lower(), _proxy_value)
