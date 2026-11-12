from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import Settings, settings as app_settings
from app.prompts import (
    DEFAULT_AGENT_INSTRUCTIONS,
    LEGACY_DEFAULT_INSTRUCTION_HASHES,
)

logger = logging.getLogger(__name__)


TASK_BUDGET_MIN = 20000
TASK_BUDGET_MAX = 1_000_000


class RuntimeSection(BaseModel):
    session_retention_days: int = Field(default=30, ge=0)
    # The runner passes this to the SDK as ``task_budget=TaskBudget(total=N)``
    # and ``None`` when 0. A nonzero total must be at least 20000 tokens. The
    # budget paces token spend; the wall-clock timeout remains the hard stop.
    task_budget_tokens: int = Field(default=0, ge=0, le=TASK_BUDGET_MAX)
    # One-way marker: the legacy app_configs retention value has been imported
    # (or the legacy table was absent) on a previous boot.
    legacy_import_done: bool = False

    @field_validator("task_budget_tokens")
    @classmethod
    def _check_task_budget(cls, value: int) -> int:
        if 0 < value < TASK_BUDGET_MIN:
            raise ValueError(
                f"task_budget_tokens must be 0 or at least {TASK_BUDGET_MIN}"
            )
        return value


class KnowledgeSection(BaseModel):
    enabled: bool = False
    url: str = ""
    username: str = ""
    password: str = ""
    root: str = ""
    interval_seconds: int = Field(default=300, gt=0)


class ProviderSection(BaseModel):
    name: str = "deepseek"
    base_url: str = ""
    model: str = ""
    api_key: str = ""


class AgentSection(BaseModel):
    id: str
    name: str
    instructions: str = ""
    status: str = "active"
    provider: ProviderSection = Field(default_factory=ProviderSection)
    # Kept for the existing Agent API/UI. The SDK manages auto memory itself,
    # so the runtime does not consult this flag.
    auto_memory: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class ChannelSection(BaseModel):
    id: str
    agent_id: str
    type: str = "wechat"
    name: str = ""
    status: str = "pairing"
    account_id: str = ""
    bot_token: str = ""
    base_url: str = ""
    ilink_user_id: str = ""
    sync_buf: str = ""
    last_seen_at: str | None = None
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ConversationSection(BaseModel):
    session_id: str
    channel_id: str = ""
    display_name: str = ""
    context_token: str | None = None
    updated_at: str | None = None


class PlatformSettings(BaseModel):
    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    knowledge: KnowledgeSection = Field(default_factory=KnowledgeSection)
    agents: list[AgentSection] = Field(default_factory=list)
    channels: list[ChannelSection] = Field(default_factory=list)
    channel_conversations: dict[str, ConversationSection] = Field(default_factory=dict)


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    @property
    def backup_path(self) -> Path:
        return Path(str(self.path) + ".bak")

    def _read_file(self, candidate: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(candidate.read_text("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("cannot read settings file %s: %s", candidate, exc)
            return None
        return data if isinstance(data, dict) else None

    def _load(self) -> tuple[dict[str, Any], str | None]:
        """Return the parsed data plus where it came from.

        ``source`` is ``"main"`` for a readable settings file, ``"backup"``
        when the main file was corrupt and the backup was used, or ``None``
        when neither file could be read.
        """
        data = self._read_file(self.path)
        if data is not None:
            return data, "main"
        data = self._read_file(self.backup_path)
        if data is not None:
            return data, "backup"
        return {}, None

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            logger.warning("cannot fsync settings directory %s", directory)
        finally:
            os.close(fd)

    def _atomic_write(self, target: Path, data: dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=target.parent, prefix=target.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, target)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        self._fsync_dir(target.parent)

    def _write_raw(
        self, data: dict[str, Any], backup: dict[str, Any] | None = None
    ) -> None:
        # Only a parseable previous main file is worth backing up; writing the
        # corrupt bytes over a good .bak would destroy the only good copy.
        if backup is not None:
            try:
                self._atomic_write(self.backup_path, backup)
            except OSError:
                logger.warning("cannot back up settings file %s", self.backup_path)
        self._atomic_write(self.path, data)

    def _read_raw(self) -> dict[str, Any]:
        data, source = self._load()
        if source == "backup":
            # Self-heal: replace the corrupt main file with the recovered data.
            try:
                self._write_raw(data)
            except OSError:
                logger.warning("cannot self-heal settings file %s", self.path)
        return data

    async def read(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._read_raw)

    async def load_state(self) -> tuple[dict[str, Any], str | None]:
        """Return the readable data and its source (``main``/``backup``/None).

        A corrupt main file is healed from the backup, matching ``read``.
        Startup seeding uses the source to tell "no usable file" apart from a
        parseable file whose owner deliberately emptied a section.
        """
        async with self._lock:
            data, source = await asyncio.to_thread(self._load)
            if source == "backup":
                try:
                    await asyncio.to_thread(self._write_raw, data)
                except OSError:
                    logger.warning("cannot self-heal settings file %s", self.path)
            return data, source

    async def update(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        async with self._lock:
            data, source = await asyncio.to_thread(self._load)
            backup = copy.deepcopy(data) if source == "main" else None
            mutate(data)
            await asyncio.to_thread(self._write_raw, data, backup)
            return data


def _section(model: type[BaseModel], raw: Any) -> Any:
    if not isinstance(raw, dict):
        return model()
    try:
        return model.model_validate(raw)
    except ValidationError:
        logger.warning("invalid settings section %s; using defaults", model.__name__)
        return model()


def _list_section(data: dict[str, Any], key: str) -> list[Any]:
    """Return the list at ``key``, treating a corrupt value as empty."""
    section = data.get(key)
    return section if isinstance(section, list) else []


def _dict_section(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a mutable mapping at ``key``, replacing a corrupt value."""
    section = data.get(key)
    if not isinstance(section, dict):
        section = {}
        data[key] = section
    return section


def _validated_update(
    model: type[BaseModel], values: dict[str, Any]
) -> dict[str, Any]:
    """Validate a partial section update and return the coerced values."""
    unknown = set(values) - set(model.model_fields)
    if unknown:
        raise ValueError("unknown settings field(s): " + ", ".join(sorted(unknown)))
    parsed = model.model_validate(values)
    return parsed.model_dump(include=set(values))


def _anthropic_base_url(base_url: str) -> str:
    """Point the provider endpoint at the Anthropic-compatible API root."""
    normalized = base_url.rstrip("/")
    if not normalized:
        return ""
    if not normalized.endswith("/anthropic"):
        normalized += "/anthropic"
    return normalized


def _parse(raw: dict[str, Any]) -> PlatformSettings:
    agents: list[AgentSection] = []
    raw_agents = raw.get("agents")
    if isinstance(raw_agents, list):
        for item in raw_agents:
            try:
                agents.append(AgentSection.model_validate(item))
            except ValidationError:
                logger.warning("skipping invalid agent entry")
    channels: list[ChannelSection] = []
    raw_channels = raw.get("channels")
    if isinstance(raw_channels, list):
        for item in raw_channels:
            try:
                channels.append(ChannelSection.model_validate(item))
            except ValidationError:
                logger.warning("skipping invalid channel entry")
    conversations: dict[str, ConversationSection] = {}
    raw_conversations = raw.get("channel_conversations")
    if isinstance(raw_conversations, dict):
        for key, item in raw_conversations.items():
            try:
                conversations[str(key)] = ConversationSection.model_validate(item)
            except ValidationError:
                logger.warning("skipping invalid conversation entry")
    return PlatformSettings(
        runtime=_section(RuntimeSection, raw.get("runtime")),
        knowledge=_section(KnowledgeSection, raw.get("knowledge")),
        agents=agents,
        channels=channels,
        channel_conversations=conversations,
    )


def build_default_settings() -> dict[str, Any]:
    # Read a fresh Settings instance so a first boot after the file was deleted
    # re-seeds from the environment as it is at that moment, not at import time.
    current = Settings()
    provider = ProviderSection(
        name="deepseek",
        base_url=_anthropic_base_url(current.llm_base_url),
        model=current.llm_model,
        api_key=current.llm_api_key,
    )
    agent = AgentSection(
        id="default",
        name="Assistant",
        instructions=DEFAULT_AGENT_INSTRUCTIONS,
        provider=provider,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    knowledge_url = current.kb_webdav_url.strip()
    defaults = PlatformSettings(
        runtime=RuntimeSection(session_retention_days=current.session_retention_days),
        knowledge=KnowledgeSection(
            enabled=bool(knowledge_url),
            url=current.kb_webdav_url,
            username=current.kb_webdav_username,
            password=current.kb_webdav_password,
            root=current.kb_webdav_root,
            interval_seconds=max(30, current.kb_sync_interval_seconds),
        ),
        agents=[agent],
    )
    return defaults.model_dump()


def _legacy_session_retention_days() -> int | None:
    """Read ``app_configs.session_retention_days`` from the pre-settings DB.

    ``ensure_seeded`` runs this once per installation, guarded by the
    ``runtime.legacy_import_done`` marker, so an instance that already had
    ``settings.json`` before this migration still adopts a legacy value
    (including 0, which disables cleanup). Any failure is logged and ignored so
    a missing or unreadable legacy database cannot block startup.
    """
    url = app_settings.app_database_url
    if not url.startswith("sqlite") or ":///" not in url:
        return None
    path = Path(url.split(":///", 1)[1].split("?", 1)[0])
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(str(path)) as connection:
            row = connection.execute(
                "SELECT session_retention_days FROM app_configs WHERE id = 'default'"
            ).fetchone()
    except sqlite3.Error as exc:
        logger.info("legacy app_configs is not readable: %s", exc)
        return None
    if row is None:
        return None
    try:
        value = int(row[0])
    except (TypeError, ValueError):
        logger.warning("legacy session_retention_days is invalid: %r", row[0])
        return None
    return max(0, min(value, 3650))


def _merge_with_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    """Rebuild sections that cannot be read while keeping what still can.

    Used when a settings file is unreadable or has no ``agents`` list. A file
    that parses and explicitly holds ``agents: []`` is treated as the owner's
    deliberate choice and is never passed through here.
    """
    defaults = build_default_settings()
    for key, model in (("runtime", RuntimeSection), ("knowledge", KnowledgeSection)):
        current = raw.get(key)
        if isinstance(current, dict):
            try:
                defaults[key] = model.model_validate(
                    {**defaults[key], **current}
                ).model_dump()
            except ValidationError:
                logger.warning(
                    "invalid settings section %s while re-seeding; using defaults",
                    key,
                )
    if isinstance(raw.get("channels"), list):
        defaults["channels"] = raw["channels"]
    if isinstance(raw.get("channel_conversations"), dict):
        defaults["channel_conversations"] = raw["channel_conversations"]
    return defaults


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._json = JsonStore(path)

    @property
    def path(self) -> Path:
        return self._json.path

    async def get(self) -> PlatformSettings:
        return _parse(await self._json.read())

    async def mutate_raw(
        self, mutate: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        """Apply a raw JSON mutation and persist it atomically."""
        return await self._json.update(mutate)

    async def ensure_seeded(self) -> PlatformSettings:
        data, source = await self._json.load_state()
        if source is None:
            # The file is missing, unreadable, or corrupt with no usable
            # backup. Write defaults so the platform does not boot with no
            # Agents; ``get()`` would otherwise stay empty forever.
            logger.warning(
                "settings file %s is unreadable or missing; re-seeding defaults",
                self.path,
            )
            await self._json.update(
                lambda raw: raw.update(build_default_settings())
            )
        elif not isinstance(data.get("agents"), list):
            # Parseable but the agents section is absent/corrupt (for example
            # a legacy partial file). Fill in defaults while preserving the
            # readable runtime/knowledge/channels sections.
            merged = _merge_with_defaults(data)
            logger.warning(
                "settings file %s has no agents section; re-seeding defaults",
                self.path,
            )
            await self._json.update(lambda raw: raw.update(merged))
        elif not data.get("agents"):
            # A parseable, deliberately empty agents list is valid: keep it
            # and warn, because re-seeding an Agent could surprise its owner.
            logger.warning(
                "settings file %s has an empty agents list; leaving it as-is",
                self.path,
            )
        elif not _parse(data).agents:
            # A non-empty list whose entries all fail validation leaves the
            # platform with no usable Agent, the same permanent-empty state as
            # a corrupt file.
            merged = _merge_with_defaults(data)
            logger.warning(
                "settings file %s has no valid agent entries; re-seeding defaults",
                self.path,
            )
            await self._json.update(lambda raw: raw.update(merged))

        settings = await self.get()
        if not settings.runtime.legacy_import_done:
            legacy_retention = _legacy_session_retention_days()

            def migrate(data: dict[str, Any]) -> None:
                runtime = _dict_section(data, "runtime")
                # Another startup path may have finished the import first.
                if runtime.get("legacy_import_done") is True:
                    return
                if legacy_retention is not None:
                    runtime["session_retention_days"] = legacy_retention
                runtime["legacy_import_done"] = True

            await self._json.update(migrate)
            settings = await self.get()
        return await self._migrate_default_instructions(settings)

    async def _migrate_default_instructions(
        self, settings: PlatformSettings
    ) -> PlatformSettings:
        """Replace Agent instructions that still equal an old default seed."""
        agent_ids = {
            agent.id
            for agent in settings.agents
            if hashlib.sha256(
                agent.instructions.encode("utf-8")
            ).hexdigest()
            in LEGACY_DEFAULT_INSTRUCTION_HASHES
        }
        if not agent_ids:
            return settings

        def mutate(data: dict[str, Any]) -> None:
            updated_at = datetime.now(timezone.utc).isoformat()
            for item in _list_section(data, "agents"):
                if (
                    isinstance(item, dict)
                    and item.get("id") in agent_ids
                ):
                    item["instructions"] = DEFAULT_AGENT_INSTRUCTIONS
                    item["updated_at"] = updated_at

        try:
            await self._json.update(mutate)
        except Exception:
            # A cosmetic instruction refresh must not abort startup.
            logger.exception("updating legacy default instructions failed")
            return settings
        logger.info(
            "updated legacy default instructions for %d agent(s)",
            len(agent_ids),
        )
        return await self.get()

    async def update_agent(self, agent: AgentSection) -> None:
        def mutate(data: dict[str, Any]) -> None:
            agents = [
                item
                for item in _list_section(data, "agents")
                if isinstance(item, dict) and item.get("id") != agent.id
            ]
            agents.append(agent.model_dump())
            data["agents"] = agents

        await self._json.update(mutate)

    async def update_agent_fields(self, agent_id: str, **fields: Any) -> bool:
        """Patch only the given fields of one agent.

        Multiple UI actions (enable skill, rename, toggle status) may race;
        each patches its own fields so a stale full-agent write cannot undo the
        others. The merged entry is validated before it is persisted. Returns
        whether the agent exists.
        """
        unknown = set(fields) - set(AgentSection.model_fields)
        if unknown:
            raise ValueError("unknown agent field(s): " + ", ".join(sorted(unknown)))

        updated = False

        def mutate(data: dict[str, Any]) -> None:
            nonlocal updated
            agents = _list_section(data, "agents")
            for index, item in enumerate(agents):
                if isinstance(item, dict) and item.get("id") == agent_id:
                    agents[index] = AgentSection.model_validate(
                        {**item, **fields}
                    ).model_dump()
                    updated = True
                    return

        await self._json.update(mutate)
        return updated

    async def update_runtime(self, **values: Any) -> None:
        validated = _validated_update(RuntimeSection, values)

        def mutate(data: dict[str, Any]) -> None:
            _dict_section(data, "runtime").update(validated)

        await self._json.update(mutate)

    async def update_knowledge(self, **values: Any) -> None:
        validated = _validated_update(KnowledgeSection, values)

        def mutate(data: dict[str, Any]) -> None:
            _dict_section(data, "knowledge").update(validated)

        await self._json.update(mutate)

    async def update_channel(self, channel: ChannelSection) -> None:
        def mutate(data: dict[str, Any]) -> None:
            channels = [
                item
                for item in _list_section(data, "channels")
                if isinstance(item, dict) and item.get("id") != channel.id
            ]
            channels.append(channel.model_dump())
            data["channels"] = channels

        await self._json.update(mutate)

    async def update_channel_fields(self, channel_id: str, **fields: Any) -> None:
        """Patch only the given fields of one channel.

        Polling and delivery touch different fields concurrently; a full
        channel write from one path must not overwrite the other's update.
        The merged entry is validated before it is persisted so an invalid
        value cannot make the whole channel unparseable.
        """
        unknown = set(fields) - set(ChannelSection.model_fields)
        if unknown:
            raise ValueError(
                "unknown channel field(s): " + ", ".join(sorted(unknown))
            )

        def mutate(data: dict[str, Any]) -> None:
            channels = _list_section(data, "channels")
            for index, item in enumerate(channels):
                if isinstance(item, dict) and item.get("id") == channel_id:
                    channels[index] = ChannelSection.model_validate(
                        {**item, **fields}
                    ).model_dump()
                    return

        await self._json.update(mutate)

    async def update_conversation(
        self, external_id: str, conversation: ConversationSection
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            _dict_section(data, "channel_conversations")[
                external_id
            ] = conversation.model_dump()

        await self._json.update(mutate)

    async def update_conversation_if_unchanged(
        self,
        external_id: str,
        expected_session_id: str | None,
        conversation: ConversationSection,
    ) -> bool:
        """Compare-and-set a conversation binding inside the store lock.

        The caller reads a binding, decides which session should own the
        contact, then writes it back. If another inbound turn rebound the
        contact in between, the write is refused so the caller can re-read and
        decide again instead of resurrecting a stale session.
        """
        updated = False

        def mutate(data: dict[str, Any]) -> None:
            nonlocal updated
            conversations = _dict_section(data, "channel_conversations")
            current = conversations.get(external_id)
            current_session = (
                current.get("session_id") if isinstance(current, dict) else None
            )
            if current_session != expected_session_id:
                return
            conversations[external_id] = conversation.model_dump()
            updated = True

        await self._json.update(mutate)
        return updated


def default_settings_path() -> Path:
    return app_settings.app_data_dir / "settings.json"


_settings_store: SettingsStore | None = None


def get_settings_store() -> SettingsStore:
    global _settings_store
    path = default_settings_path()
    # Tests may point ``app_data_dir`` at a temporary directory between
    # cases; keep the singleton bound to the current runtime home.
    if _settings_store is None or _settings_store.path != path:
        _settings_store = SettingsStore(path)
    return _settings_store
