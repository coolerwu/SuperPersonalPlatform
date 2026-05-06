from pathlib import Path

import yaml

from server.infrastructure.config import parse_settings


class InvalidConfigFileError(Exception):
    pass


class ConfigFileService:
    def __init__(self, workspace: Path) -> None:
        self.config_path = workspace / "config.yaml"

    def read_config(self) -> str:
        return self.config_path.read_text(encoding="utf-8")

    def write_config(self, content: str) -> None:
        try:
            raw = yaml.safe_load(content) or {}
            if not isinstance(raw, dict):
                raise InvalidConfigFileError("config.yaml 顶层必须是对象")
            parse_settings(raw)
        except InvalidConfigFileError:
            raise
        except Exception as exc:
            raise InvalidConfigFileError(str(exc)) from exc

        tmp_path = self.config_path.with_suffix(".yaml.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(self.config_path)
