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

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.7

    run_timeout_seconds: int = 300
    max_history_messages: int = 40

    # Browser tools are skill-gated; these bound one Run's browser session.
    browser_concurrency: int = 2
    browser_navigation_timeout_ms: int = 30_000
    browser_action_timeout_ms: int = 10_000
    browser_settle_ms: int = 500
    browser_max_elements: int = 120
    browser_max_text_chars: int = 4_000
    browser_max_snapshot_chars: int = 12_000
    browser_no_sandbox: bool = False

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
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.app_cors_origins.split(",") if item.strip()]


settings = Settings()
