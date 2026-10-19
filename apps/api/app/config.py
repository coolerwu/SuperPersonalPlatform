from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_auth_token: str = "dev-token"
    app_database_url: str = "sqlite+aiosqlite:///./data/app.db"
    app_data_dir: Path = Path("./data")
    app_cors_origins: str = "http://localhost:5173"

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.7

    run_timeout_seconds: int = 300
    max_history_messages: int = 40

    @property
    def checkpoint_dir(self) -> Path:
        return self.app_data_dir / "checkpoints"

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.app_cors_origins.split(",") if item.strip()]


settings = Settings()
