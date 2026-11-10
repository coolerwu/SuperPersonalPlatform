# Claude 运行时核心迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把平台执行核换成 Claude Agent SDK（`ClaudeSDKClient` + DeepSeek Anthropic 端点），聊天/消息/会话改由 SDK 原生管理，配置迁到 `data/settings.json`。

**Architecture:** 平台生成 UUID 作为 SDK `session_id`；每会话一个常驻 `ClaudeSDKClient` 与内存队列；实时流式经 SSE 转发，不落库；`running_sessions` 单列表承担重启恢复；会话列表与历史读 `list_sessions` / `get_session_messages`。

**Tech Stack:** FastAPI、SQLAlchemy async、SQLite、claude-agent-sdk（Python）、pytest、React + Vite。

**Spec:** `docs/superpowers/specs/2026-11-10-claude-agent-sdk-migration-design.md`

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `apps/api/app/services/settings_store.py` | `data/settings.json` 的 schema、原子读写、节级回退、seed |
| `apps/api/app/services/claude_env.py` | 目录约定与 `ClaudeAgentOptions.env` 构造 |
| `apps/api/app/services/sdk_bridge.py` | SDK 模块函数的薄封装（便于测试 monkeypatch） |
| `apps/api/app/services/turn_lease.py` | `running_sessions` 增删查 |
| `apps/api/app/services/sdk_events.py` | SDK 消息 → 平台事件（纯函数） |
| `apps/api/app/services/turn_stream.py` | 单轮事件的内存广播（SSE 订阅源） |
| `apps/api/app/services/claude_runner.py` | 一轮执行：租约、投递、query、终态 |
| `apps/api/app/services/session_registry.py` | 每会话队列与并发控制 |
| `apps/api/app/services/session_catalog.py` | 会话列表、历史消息、重命名、删除 |
| `apps/api/app/routers/chat.py` | 入队接口 |
| `apps/api/app/routers/sessions.py` | 会话列表/历史/流式/中断/删除 |
| `apps/api/app/main.py` | 启动装配与 seed |
| `apps/web/src/api.ts` | 新接口与类型 |
| `apps/web/src/App.tsx` | 聊天状态改为 transcript + 实时流 |

**约定：** 测试从仓库根目录运行 `.venv/bin/python -m pytest apps/api/app/tests -q`；每个任务结束提交一次。

---

### Task 1: SDK 依赖与桥接层

**Files:**
- Modify: `apps/api/pyproject.toml`
- Create: `apps/api/app/services/sdk_bridge.py`
- Test: `apps/api/app/tests/test_sdk_bridge.py`

- [ ] **Step 1: 写失败测试**

```python
from app.services import sdk_bridge


def test_bridge_exposes_session_api():
    for name in ("list_sessions", "get_session_messages", "rename_session", "claude_client_class"):
        assert callable(getattr(sdk_bridge, name))


def test_bridge_imports_agent_sdk():
    assert sdk_bridge.sdk_version()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_sdk_bridge.py -q`
Expected: FAIL（ModuleNotFoundError 或导入失败）

- [ ] **Step 3: 加依赖并实现桥接**

`apps/api/pyproject.toml` 的 `dependencies` 增加一行：

```toml
  "claude-agent-sdk>=0.2.180,<0.3",
```

安装：`.venv/bin/pip install -e "apps/api[dev]"`

`apps/api/app/services/sdk_bridge.py`：

```python
from __future__ import annotations

from typing import Any


def sdk_version() -> str:
    import claude_agent_sdk

    return str(getattr(claude_agent_sdk, "__version__", "unknown"))


def claude_client_class() -> Any:
    from claude_agent_sdk import ClaudeSDKClient

    return ClaudeSDKClient


def options_class() -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions


def list_sessions(directory: str | None = None) -> list[Any]:
    from claude_agent_sdk import list_sessions as _list_sessions

    return list(_list_sessions(directory=directory))


def get_session_messages(session_id: str) -> list[Any]:
    from claude_agent_sdk import get_session_messages as _get_messages

    return list(_get_messages(session_id))


def rename_session(session_id: str, title: str) -> None:
    from claude_agent_sdk import rename_session as _rename

    _rename(session_id, title)


def delete_session(session_id: str) -> None:
    from claude_agent_sdk import delete_session as _delete

    _delete(session_id)
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_sdk_bridge.py -q`
Expected: PASS（需要真实安装，不访问网络）

- [ ] **Step 5: 提交**

```bash
git add apps/api/pyproject.toml apps/api/app/services/sdk_bridge.py apps/api/app/tests/test_sdk_bridge.py
git commit -m "Add Claude Agent SDK dependency and bridge"
```

---

### Task 2: settings.json 存储

**Files:**
- Create: `apps/api/app/services/settings_store.py`
- Test: `apps/api/app/tests/test_settings_store.py`

- [ ] **Step 1: 写失败测试**

```python
import json

import pytest

from app.services.settings_store import JsonStore, SettingsStore, build_default_settings


@pytest.mark.asyncio
async def test_json_store_atomic_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    store = JsonStore(path)
    await store.update(lambda data: data.update({"runtime": {"session_retention_days": 7}}))
    assert json.loads(path.read_text("utf-8"))["runtime"]["session_retention_days"] == 7
    assert (tmp_path / "settings.json.bak").exists() is False


@pytest.mark.asyncio
async def test_settings_store_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{broken", "utf-8")
    store = SettingsStore(path)
    settings = await store.get()
    assert settings.runtime.session_retention_days == 30


@pytest.mark.asyncio
async def test_settings_store_keeps_bad_section_isolated(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"runtime": {"session_retention_days": "x"}, "agents": []}), "utf-8")
    settings = await SettingsStore(path).get()
    assert settings.runtime.session_retention_days == 30


@pytest.mark.asyncio
async def test_seed_default_settings_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_RETENTION_DAYS", "5")
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 5
    assert data["agents"][0]["id"] == "default"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_settings_store.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`apps/api/app/services/settings_store.py`：

```python
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.config import APP_HOME, settings as app_settings
from app.prompts import DEFAULT_AGENT_INSTRUCTIONS

logger = logging.getLogger(__name__)


class RuntimeSection(BaseModel):
    session_retention_days: int = 30


class KnowledgeSection(BaseModel):
    enabled: bool = False
    url: str = ""
    username: str = ""
    password: str = ""
    root: str = ""
    interval_seconds: int = 300


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
    enabled_skills: list[str] = Field(default_factory=list)
    provider: ProviderSection = Field(default_factory=ProviderSection)
    created_at: str | None = None


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

    def _read_raw(self) -> dict[str, Any]:
        for candidate in (self.path, Path(str(self.path) + ".bak")):
            try:
                data = json.loads(candidate.read_text("utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                logger.exception("cannot read settings file %s", candidate)
                continue
            if isinstance(data, dict):
                return data
        return {}

    def _write_raw(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            if self.path.exists():
                try:
                    shutil.copy2(self.path, Path(str(self.path) + ".bak"))
                except OSError:
                    logger.exception("cannot back up settings file")
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    async def read(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._read_raw)

    async def update(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        async with self._lock:
            data = await asyncio.to_thread(self._read_raw)
            mutate(data)
            await asyncio.to_thread(self._write_raw, data)
            return data


def _section(model: type[BaseModel], raw: Any) -> Any:
    if not isinstance(raw, dict):
        return model()
    try:
        return model.model_validate(raw)
    except ValidationError:
        logger.warning("invalid settings section %s; using defaults", model.__name__)
        return model()


def _parse(raw: dict[str, Any]) -> PlatformSettings:
    agents: list[AgentSection] = []
    for item in raw.get("agents") or []:
        try:
            agents.append(AgentSection.model_validate(item))
        except ValidationError:
            logger.warning("skipping invalid agent entry")
    channels: list[ChannelSection] = []
    for item in raw.get("channels") or []:
        try:
            channels.append(ChannelSection.model_validate(item))
        except ValidationError:
            logger.warning("skipping invalid channel entry")
    conversations: dict[str, ConversationSection] = {}
    for key, item in (raw.get("channel_conversations") or {}).items():
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
    provider = ProviderSection(
        name="deepseek",
        base_url=app_settings.llm_base_url,
        model=app_settings.llm_model,
        api_key=app_settings.llm_api_key,
    )
    agent = AgentSection(
        id="default",
        name="Assistant",
        instructions=DEFAULT_AGENT_INSTRUCTIONS,
        provider=provider,
    )
    defaults = PlatformSettings(
        runtime=RuntimeSection(
            session_retention_days=app_settings.session_retention_days
        ),
        knowledge=KnowledgeSection(
            url=app_settings.kb_webdav_url,
            username=app_settings.kb_webdav_username,
            password=app_settings.kb_webdav_password,
            root=app_settings.kb_webdav_root,
        ),
        agents=[agent],
    )
    return defaults.model_dump()


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._json = JsonStore(path)

    @property
    def path(self) -> Path:
        return self._json.path

    async def get(self) -> PlatformSettings:
        return _parse(await self._json.read())

    async def ensure_seeded(self) -> PlatformSettings:
        if not self.path.exists():
            await self._json.update(lambda data: data.update(build_default_settings()))
        return await self.get()

    async def update_agent(self, agent: AgentSection) -> None:
        def mutate(data: dict[str, Any]) -> None:
            agents = [item for item in data.get("agents") or [] if item.get("id") != agent.id]
            agents.append(agent.model_dump())
            data["agents"] = agents

        await self._json.update(mutate)

    async def update_runtime(self, **values: Any) -> None:
        await self._json.update(
            lambda data: data.setdefault("runtime", {}).update(values)
        )

    async def update_knowledge(self, **values: Any) -> None:
        await self._json.update(
            lambda data: data.setdefault("knowledge", {}).update(values)
        )

    async def update_channel(self, channel: ChannelSection) -> None:
        def mutate(data: dict[str, Any]) -> None:
            channels = [
                item for item in data.get("channels") or [] if item.get("id") != channel.id
            ]
            channels.append(channel.model_dump())
            data["channels"] = channels

        await self._json.update(mutate)

    async def update_conversation(
        self, external_id: str, conversation: ConversationSection
    ) -> None:
        await self._json.update(
            lambda data: data.setdefault("channel_conversations", {}).update(
                {external_id: conversation.model_dump()}
            )
        )


def default_settings_path() -> Path:
    return app_settings.app_data_dir / "settings.json"


_settings_store: SettingsStore | None = None


def get_settings_store() -> SettingsStore:
    global _settings_store
    if _settings_store is None:
        _settings_store = SettingsStore(default_settings_path())
    return _settings_store
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_settings_store.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/settings_store.py apps/api/app/tests/test_settings_store.py
git commit -m "Add JSON settings store"
```

---

### Task 3: Claude 环境构造

**Files:**
- Create: `apps/api/app/services/claude_env.py`
- Test: `apps/api/app/tests/test_claude_env.py`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path

from app.services.claude_env import build_claude_env, claude_paths
from app.services.settings_store import AgentSection, ProviderSection


def test_claude_paths_are_inside_home(tmp_path):
    paths = claude_paths(tmp_path)
    assert paths["config"] == tmp_path / "claude"
    assert paths["tmp"] == tmp_path / "tmp"


def test_build_env_targets_deepseek(tmp_path):
    agent = AgentSection(
        id="default",
        name="Assistant",
        provider=ProviderSection(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash[1m]",
            api_key="sk-test",
        ),
    )
    env = build_claude_env(agent, home=tmp_path)
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")
    assert env["CLAUDE_CODE_PROJECT_DIR_NAME"] == "default"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    assert env["ANTHROPIC_MODEL"] == "deepseek-flash[1m]"
    assert "HOME" not in env
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_env.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/services/claude_env.py`：

```python
from __future__ import annotations

from pathlib import Path

from app.config import APP_HOME
from app.services.settings_store import AgentSection


def claude_paths(home: Path | None = None) -> dict[str, Path]:
    root = home or APP_HOME
    return {
        "home": root,
        "config": root / "claude",
        "tmp": root / "tmp",
        "workspaces": root / "workspaces",
    }


def workspace_for(agent_id: str, home: Path | None = None) -> Path:
    return claude_paths(home)["workspaces"] / agent_id


def build_claude_env(agent: AgentSection, *, home: Path | None = None) -> dict[str, str]:
    paths = claude_paths(home)
    provider = agent.provider
    env = {
        "CLAUDE_CONFIG_DIR": str(paths["config"]),
        "CLAUDE_CODE_PROJECT_DIR_NAME": agent.id,
        "TMPDIR": str(paths["tmp"]),
        "ANTHROPIC_BASE_URL": provider.base_url,
        "ANTHROPIC_AUTH_TOKEN": provider.api_key,
        "ANTHROPIC_MODEL": provider.model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": provider.model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": provider.model,
        "CLAUDE_CODE_SUBAGENT_MODEL": provider.model,
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
    }
    return {key: value for key, value in env.items() if value}


def ensure_claude_dirs(home: Path | None = None) -> None:
    paths = claude_paths(home)
    for key in ("config", "tmp", "workspaces"):
        paths[key].mkdir(parents=True, exist_ok=True)
```

> **质量审查修订（执行时已落实）：** 新增 `install_claude_process_env()`（强制写入进程级 `CLAUDE_CONFIG_DIR`，供 SDK 的 in-process 会话 API 使用）；Haiku/子 Agent 模型使用 `provider.model.removesuffix("[1m]")`；`home is None` 时 workspaces 使用 `app_settings.workspaces_dir`；config 目录权限 0700；provider 字符串先 `strip()` 再过滤空值；测试扩展为完整 env 断言。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_env.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/claude_env.py apps/api/app/tests/test_claude_env.py
git commit -m "Add Claude SDK environment builder"
```

---

### Task 4: running_sessions 租约

**Files:**
- Modify: `apps/api/app/models.py`（在文件末尾新增模型，旧模型暂留）
- Create: `apps/api/app/services/turn_lease.py`
- Test: `apps/api/app/tests/test_turn_lease.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest

from app.db import SessionLocal, init_db
from app.services import turn_lease


@pytest.mark.asyncio
async def test_lease_add_list_remove():
    await init_db()
    await turn_lease.add("session-a")
    assert "session-a" in await turn_lease.list_all()
    assert await turn_lease.is_running("session-a")
    await turn_lease.remove("session-a")
    assert not await turn_lease.is_running("session-a")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_turn_lease.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/models.py` 末尾新增：

```python
class RunningSession(Base):
    __tablename__ = "running_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
```

`apps/api/app/services/turn_lease.py`：

```python
from __future__ import annotations

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import RunningSession


async def add(session_id: str) -> None:
    async with SessionLocal() as db:
        db.add(RunningSession(session_id=session_id))
        try:
            await db.commit()
        except Exception:
            await db.rollback()


async def remove(session_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(RunningSession).where(RunningSession.session_id == session_id)
        )
        await db.commit()


async def list_all() -> list[str]:
    async with SessionLocal() as db:
        rows = await db.scalars(select(RunningSession.session_id))
        return [str(row) for row in rows]


async def is_running(session_id: str) -> bool:
    async with SessionLocal() as db:
        row = await db.get(RunningSession, session_id)
        return row is not None
```

> **质量审查修订（执行时已落实）：** `add` 先捕获 `IntegrityError`（重复主键，debug 日志），其余异常 rollback 后 `logger.exception` 且不重抛；新增 `add_in(db, session_id)` 供调用方在同一事务内写租约；模块 docstring 写明“行存在 = 有一轮附着，依赖每会话同时只有一轮”；测试覆盖重复 add/remove 与清理。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_turn_lease.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/models.py apps/api/app/services/turn_lease.py apps/api/app/tests/test_turn_lease.py
git commit -m "Add running session lease"
```

---

### Task 5: SDK 事件映射

**Files:**
- Create: `apps/api/app/services/sdk_events.py`
- Test: `apps/api/app/tests/test_sdk_events.py`

- [ ] **Step 1: 写失败测试**

```python
from types import SimpleNamespace

from app.services.sdk_events import StreamMapper, outcome_from_result


def test_text_and_thinking_deltas():
    mapper = StreamMapper()
    assert mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "hi"},
        }
    ) == [{"type": "assistant_delta", "delta": "hi"}]
    assert mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "hm"},
        }
    ) == [{"type": "reasoning_delta", "delta": "hm"}]


def test_tool_call_accumulates_input():
    mapper = StreamMapper()
    assert mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        }
    ) == []
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"file_path":'},
        }
    )
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '"/tmp/x"}'},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == [
        {"type": "tool_call", "name": "Read", "input": {"file_path": "/tmp/x"}}
    ]


def test_outcome_completed():
    message = SimpleNamespace(
        subtype="success",
        terminal_reason="completed",
        result="done",
        usage={"input_tokens": 1, "output_tokens": 2},
        errors=None,
        api_error_status=None,
    )
    outcome = outcome_from_result(message, fallback_text="")
    assert outcome.status == "completed"
    assert outcome.output == "done"


def test_outcome_cancelled_and_failed():
    cancelled = SimpleNamespace(
        subtype="success", terminal_reason="aborted_streaming",
        result=None, usage=None, errors=None, api_error_status=None,
    )
    assert outcome_from_result(cancelled, fallback_text="x").status == "cancelled"
    failed = SimpleNamespace(
        subtype="error_during_execution", terminal_reason="api_error",
        result=None, usage=None, errors=["boom"], api_error_status=500,
    )
    outcome = outcome_from_result(failed, fallback_text="x")
    assert outcome.status == "failed"
    assert "boom" in (outcome.error or "")


def test_success_with_is_error_is_failed():
    message = SimpleNamespace(
        subtype="success",
        terminal_reason=None,
        result="API Error: 401 unauthorized",
        usage=None,
        errors=None,
        api_error_status=401,
        is_error=True,
    )
    outcome = outcome_from_result(message, fallback_text="x")
    assert outcome.status == "failed"
    assert "API Error" in (outcome.error or "")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_sdk_events.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/services/sdk_events.py`：

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class TurnOutcome:
    status: str
    output: str = ""
    error: str | None = None
    terminal_reason: str | None = None
    usage: dict[str, Any] | None = None


class StreamMapper:
    def __init__(self) -> None:
        self._tool_name: str | None = None
        self._tool_json = ""

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                self._tool_name = str(block.get("name") or "")
                self._tool_json = ""
            return []
        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = str(delta.get("text") or "")
                return [{"type": "assistant_delta", "delta": text}] if text else []
            if delta.get("type") == "thinking_delta":
                thinking = str(delta.get("thinking") or "")
                return [{"type": "reasoning_delta", "delta": thinking}] if thinking else []
            if delta.get("type") == "input_json_delta":
                self._tool_json += str(delta.get("partial_json") or "")
            return []
        if kind == "content_block_stop" and self._tool_name:
            name, raw = self._tool_name, self._tool_json
            self._tool_name, self._tool_json = None, ""
            try:
                parsed = json.loads(raw) if raw else {}
            except ValueError:
                parsed = {}
            return [{"type": "tool_call", "name": name, "input": parsed}]
        return []


def outcome_from_result(message: Any, *, fallback_text: str) -> TurnOutcome:
    subtype = str(getattr(message, "subtype", "") or "")
    terminal = getattr(message, "terminal_reason", None)
    result = getattr(message, "result", None)
    errors = getattr(message, "errors", None) or []
    usage = getattr(message, "usage", None)
    is_error = bool(getattr(message, "is_error", False))
    if terminal in ("aborted_streaming", "aborted_tools"):
        return TurnOutcome(
            status="cancelled",
            output=fallback_text,
            terminal_reason=terminal,
            usage=usage,
        )
    if subtype == "success" and terminal in (None, "completed") and not is_error:
        return TurnOutcome(
            status="completed",
            output=str(result or fallback_text),
            terminal_reason=terminal,
            usage=usage,
        )
    error_text = "; ".join(str(item) for item in errors if item) or str(
        result or terminal or "run failed"
    )
    return TurnOutcome(
        status="failed",
        output=fallback_text,
        error=error_text,
        terminal_reason=terminal,
        usage=usage,
    )
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_sdk_events.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/sdk_events.py apps/api/app/tests/test_sdk_events.py
git commit -m "Add SDK event mapping"
```

---

### Task 6: 单轮事件广播

**Files:**
- Create: `apps/api/app/services/turn_stream.py`
- Test: `apps/api/app/tests/test_turn_stream.py`

- [ ] **Step 1: 写失败测试**

```python
import asyncio

import pytest

from app.services.turn_stream import TurnStream


@pytest.mark.asyncio
async def test_subscriber_receives_buffered_and_live_events():
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    queue = await stream.subscribe()
    assert await asyncio.wait_for(queue.get(), 1) == {"type": "assistant_delta", "delta": "a"}
    stream.publish({"type": "assistant_delta", "delta": "b"})
    assert await asyncio.wait_for(queue.get(), 1) == {"type": "assistant_delta", "delta": "b"}
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == {"type": "turn_end"}


@pytest.mark.asyncio
async def test_publish_after_close_is_ignored():
    stream = TurnStream()
    stream.close()
    stream.publish({"type": "assistant_delta", "delta": "x"})
    queue = await stream.subscribe()
    assert await asyncio.wait_for(queue.get(), 1) == {"type": "turn_end"}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_turn_stream.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/services/turn_stream.py`：

```python
from __future__ import annotations

import asyncio
from typing import Any


class TurnStream:
    def __init__(self) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._closed = False

    def publish(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        self._buffer.append(event)
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for event in self._buffer:
            queue.put_nowait(event)
        if self._closed:
            queue.put_nowait({"type": "turn_end"})
            return queue
        self._subscribers.add(queue)
        return queue

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for queue in list(self._subscribers):
            queue.put_nowait({"type": "turn_end"})
        self._subscribers.clear()
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_turn_stream.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/turn_stream.py apps/api/app/tests/test_turn_stream.py
git commit -m "Add turn event stream"
```

---

### Task 7: 单轮执行器

**Files:**
- Create: `apps/api/app/services/claude_runner.py`
- Test: `apps/api/app/tests/test_claude_runner.py`

- [ ] **Step 1: 写失败测试**

```python
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import init_db
from app.services import turn_lease
from app.services.claude_runner import ClaudeTurnRunner, Turn
from app.services.settings_store import AgentSection, PlatformSettings, ProviderSectionfrom app.services.turn_stream import TurnStream


class FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.queries = []
        self.interrupted = False

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        for message in self._messages:
            yield message

    async def interrupt(self):
        self.interrupted = True


class FakeStore:
    def __init__(self, agent):
        self._agent = agent

    async def get(self):
        return PlatformSettings(agents=[self._agent], channels=[], channel_conversations={})


class ResultMessage:
    subtype = "success"
    terminal_reason = "completed"
    result = "hello"
    usage = {"input_tokens": 1, "output_tokens": 1}
    errors = None
    api_error_status = None
    is_error = False


@pytest.mark.asyncio
async def test_runner_maps_stream_and_completes(tmp_path, monkeypatch):
    await init_db()
    agent = AgentSection(
        id="default",
        name="Assistant",
        provider=ProviderSection(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash[1m]",
            api_key="sk-test",
        ),
    )
    store = FakeStore(agent)
    messages = [
        SimpleNamespace(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            }
        ),
        ResultMessage(),
    ]
    client = FakeClient(messages)
    runner = ClaudeTurnRunner(
        store=store,
        home=tmp_path,
        client_factory=lambda options: client,
    )
    stream = TurnStream()
    turn = Turn(session_id="11111111-1111-1111-1111-111111111111", agent_id="default", prompt="hi")
    outcome = await runner.run(turn, stream)
    assert outcome.status == "completed"
    assert outcome.output == "hello"
    assert client.queries == ["hi"]
    assert not await turn_lease.is_running(turn.session_id)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/services/claude_runner.py`：

```python
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.config import settings as app_settings
from app.services import sdk_bridge, turn_lease
from app.services.claude_env import build_claude_env, claude_paths, workspace_for
from app.services.sdk_events import StreamMapper, TurnOutcome, outcome_from_result
from app.services.settings_store import SettingsStore
from app.services.turn_stream import TurnStream

logger = logging.getLogger(__name__)


@dataclass
class Turn:
    session_id: str
    agent_id: str
    prompt: str
    source: str = "manual"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


def transcript_path(session_id: str, agent_id: str, *, home: Path | None = None) -> Path:
    paths = claude_paths(home)
    return paths["config"] / "projects" / agent_id / f"{session_id}.jsonl"


class ClaudeTurnRunner:
    def __init__(
        self,
        store: SettingsStore,
        *,
        home: Path | None = None,
        client_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._store = store
        self._home = home
        self._clients: dict[str, Any] = {}
        self._factory = client_factory
        self._locks: dict[str, asyncio.Lock] = {}

    def _client_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def _client(self, turn: Turn, agent: Any) -> Any:
        lock = self._client_lock(turn.session_id)
        async with lock:
            client = self._clients.get(turn.session_id)
            if client is not None:
                return client
            options_class = sdk_bridge.options_class()
            exists = transcript_path(turn.session_id, agent.id, home=self._home).is_file()
            workspace = workspace_for(agent.id, self._home)
            workspace.mkdir(parents=True, exist_ok=True)
            if not (
                agent.provider.base_url
                and agent.provider.model
                and agent.provider.api_key
            ):
                raise RuntimeError("provider base_url/model/api_key is not configured")
            kwargs: dict[str, Any] = {
                "cwd": str(workspace_for(agent.id, self._home)),
                "env": build_claude_env(agent, home=self._home),
                "include_partial_messages": True,
                "setting_sources": ["user", "project"],
                "permission_mode": app_settings.claude_permission_mode,
            }
            if exists:
                kwargs["resume"] = turn.session_id
            else:
                kwargs["session_id"] = turn.session_id
            options = options_class(**kwargs)
            if self._factory is not None:
                client = self._factory(options)
            else:
                client = sdk_bridge.claude_client_class()(options)
            await client.connect()
            self._clients[turn.session_id] = client
            return client

    async def run(self, turn: Turn, stream: TurnStream) -> TurnOutcome:
        mapper = StreamMapper()
        text_parts: list[str] = []
        outcome = TurnOutcome(status="failed", error="run ended without result")
        lease_added = False
        try:
            async with asyncio.timeout(app_settings.run_timeout_seconds):
                settings = await self._store.get()
                agent = next(
                    (item for item in settings.agents if item.id == turn.agent_id), None
                )
                if agent is None:
                    outcome = TurnOutcome(status="failed", error="agent not found")
                    return outcome
                await turn_lease.add(turn.session_id)
                lease_added = True
                client = await self._client(turn, agent)
                if turn.source == "resume":
                    prompt = (
                        "上次任务因服务重启被中断。请先检查当前状态，"
                        "继续完成原任务；已经完成的步骤不要重复执行。"
                    )
                else:
                    prompt = turn.prompt
                await client.query(prompt)
                async for message in client.receive_response():
                    if getattr(message, "parent_tool_use_id", None):
                        continue
                    event = getattr(message, "event", None)
                    if isinstance(event, dict):
                        for mapped in mapper.feed(event):
                            if mapped["type"] == "assistant_delta":
                                text_parts.append(mapped["delta"])
                            stream.publish(mapped)
                        continue
                    # SystemMessage also carries .subtype; match the class name so
                    # the init frame is not mistaken for a result.
                    if type(message).__name__ != "ResultMessage":
                        continue
                    origin = getattr(message, "origin", None)
                    kind = (
                        origin.get("kind")
                        if isinstance(origin, dict)
                        else getattr(origin, "kind", None)
                    )
                    if kind not in (None, "human"):
                        continue
                    outcome = outcome_from_result(
                        message, fallback_text="".join(text_parts)
                    )
                    break
        except TimeoutError:
            outcome = TurnOutcome(
                status="failed",
                error="run exceeded the execution timeout",
                output="".join(text_parts),
            )
            try:
                await asyncio.wait_for(self.interrupt(turn.session_id), timeout=5)
            except Exception:
                logger.exception("interrupt after timeout failed")
            await self._evict(turn.session_id)
        except asyncio.CancelledError:
            outcome = TurnOutcome(status="cancelled", output="".join(text_parts))
            try:
                await asyncio.shield(self._evict(turn.session_id))
            except BaseException:
                logger.exception("client eviction after cancel failed")
            raise
        except Exception as exc:
            logger.exception(
                "claude turn failed session=%s agent=%s", turn.session_id, turn.agent_id
            )
            outcome = TurnOutcome(
                status="failed", error=str(exc), output="".join(text_parts)
            )
            await self._evict(turn.session_id)
        finally:
            stream.publish(
                {
                    "type": "turn_end",
                    "status": outcome.status,
                    "error": outcome.error,
                    "terminal_reason": outcome.terminal_reason,
                    "usage": outcome.usage,
                }
            )
            stream.close()
            if lease_added:
                try:
                    await turn_lease.remove(turn.session_id)
                except BaseException:
                    logger.exception(
                        "failed to remove turn lease session=%s", turn.session_id
                    )
        return outcome

    async def interrupt(self, session_id: str) -> bool:
        client = self._clients.get(session_id)
        if client is None:
            return False
        await client.interrupt()
        return True

    async def _evict(self, session_id: str) -> None:
        await self.close(session_id)

    async def close(self, session_id: str) -> None:
        lock = self._client_lock(session_id)
        async with lock:
            client = self._clients.pop(session_id, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("client disconnect failed session=%s", session_id)

    async def close_all(self) -> None:
        for session_id in list(self._clients):
            await self.close(session_id)
```

`apps/api/app/config.py` 增加一个字段（并在文件顶部导入 `Literal`）：

```python
    # The SDK runs the full built-in tool set under this mode; this version
    # applies no tool allowlist, so it is not a security boundary.
    claude_permission_mode: Literal[
        "default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"
    ] = "bypassPermissions"
```

> **质量审查修订（执行时已落实）：** `receive_response()` 在第一个 `ResultMessage` 后就会返回，无法继续 drain 注入结果。执行器改用 `client.receive_messages()`；只有 `origin.kind in (None, "human")` 的结果才算本轮终态（`result_received=True`）。任何未拿到本轮结果的路径（clean EOF、注入结果后结束、超时、异常、取消）都在 `finally` 中逐出 client；**失败结果（`outcome.status != "completed"`）同样逐出**（SDK 在 `is_error` 后会让 CLI 退出），只有成功结果保留缓存。`turn_end` 与 `stream.close()` 先同步执行，再 `asyncio.shield` 逐出、最后删租约。超时不调用 interrupt（close 会断开 CLI）。新增 `close_all()` 供停机时断开所有缓存 client；`interrupt()` 读取缓存时加锁。取消路径的日志不带 traceback。**CancelledError（进程优雅停机）时保留租约**（`keep_lease=True`，仍发布 cancelled 终态并逐出 client），由下次启动恢复；用户 `/stop` 走 `interrupt()` 得到 aborted 结果，不进入该分支。`close_all()` 的每个 `disconnect()` 限时 10s。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/claude_runner.py apps/api/app/config.py apps/api/app/tests/test_claude_runner.py
git commit -m "Add Claude turn runner"
```

---

### Task 8: 会话队列与并发

**Files:**
- Create: `apps/api/app/services/session_registry.py`
- Test: `apps/api/app/tests/test_session_registry.py`

- [ ] **Step 1: 写失败测试**

```python
import asyncio

import pytest

from app.services.claude_runner import Turn
from app.services.session_registry import SessionRegistry
from app.services.turn_stream import TurnStream


class FakeRunner:
    def __init__(self):
        self.runs = []
        self.interrupted = []

    async def run(self, turn, stream):
        self.runs.append(turn.prompt)
        stream.publish({"type": "assistant_delta", "delta": turn.prompt})
        stream.close()

    async def interrupt(self, session_id):
        self.interrupted.append(session_id)
        return True


@pytest.mark.asyncio
async def test_turns_in_same_session_are_serialized():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    first = await registry.submit(Turn(session_id="s1", agent_id="default", prompt="a"))
    second = await registry.submit(Turn(session_id="s1", agent_id="default", prompt="b"))
    assert first != second
    assert registry.stream(first) is not registry.stream(second)
    await asyncio.sleep(0.1)
    await registry.close()
    assert runner.runs == ["a", "b"]


@pytest.mark.asyncio
async def test_interrupt_delegates():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    assert await registry.interrupt("s1") is True
    await registry.close()
    assert runner.interrupted == ["s1"]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_session_registry.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/services/session_registry.py`：

```python
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.claude_runner import Turn
from app.services.turn_stream import TurnStream

logger = logging.getLogger(__name__)


class SessionRegistry:
    def __init__(self, runner: Any, *, concurrency: int = 5) -> None:
        self._runner = runner
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._queues: dict[str, asyncio.Queue[tuple[Turn, TurnStream]]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._streams: dict[str, TurnStream] = {}
        self._current: dict[str, str] = {}
        self._running: set[str] = set()
        self._max_streams = 256

    async def submit(self, turn: Turn) -> str:
        queue = self._queues.get(turn.session_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[turn.session_id] = queue
            self._workers[turn.session_id] = asyncio.create_task(
                self._session_loop(turn.session_id), name=f"session-{turn.session_id[:8]}"
            )
        stream = TurnStream()
        self._streams[turn.id] = stream
        while len(self._streams) > self._max_streams:
            oldest = next(iter(self._streams))
            self._streams.pop(oldest, None)
        await queue.put((turn, stream))
        return turn.id

    async def _session_loop(self, session_id: str) -> None:
        queue = self._queues[session_id]
        while True:
            turn, stream = await queue.get()
            self._current[session_id] = turn.id
            self._running.add(session_id)
            try:
                async with self._semaphore:
                    await self._runner.run(turn, stream)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("session turn failed session=%s", session_id)
                stream.close()
            finally:
                self._running.discard(session_id)
                self._current.pop(session_id, None)
                queue.task_done()

    def stream(self, turn_id: str) -> TurnStream | None:
        return self._streams.get(turn_id)

    def current_turn_id(self, session_id: str) -> str | None:
        return self._current.get(session_id)

    def is_busy(self, session_id: str) -> bool:
        queue = self._queues.get(session_id)
        return session_id in self._running or (queue is not None and not queue.empty())

    async def interrupt(self, session_id: str) -> bool:
        return bool(await self._runner.interrupt(session_id))

    async def close(self) -> None:
        for task in self._workers.values():
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()
        self._streams.clear()
```

> **质量审查修订（执行时已落实）：** `TurnStream` 增加只读 `closed` 属性；流容量淘汰只弹已关闭的流（无可淘汰项时允许临时超限），绝不淘汰运行/排队轮的流。worker 在队列为空时自行回收 `_queues/_workers/_current`；`close()` 置 `_closed=True`，之后 `submit` 抛 `RuntimeError`，并清空全部映射。`/stop` 语义：排空排队轮次（各自发布 cancelled `turn_end` 并关闭流）＋中断正在执行的轮次；registry 用 `_pending/_cancelled/_started` 覆盖“已出队等信号量”的窗口，runner 用 `_active/_cancel_requested` 覆盖“已开始但还没到 query”的窗口，空闲会话的 interrupt 返回 False。测试改为事件驱动并断言同会话/跨会话并发上限、淘汰、is_busy、close 后 submit、回收后再次 submit、两种等待窗口与 `_active` 不泄漏。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_session_registry.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/session_registry.py apps/api/app/tests/test_session_registry.py
git commit -m "Add session turn registry"
```

---

### Task 9: 会话目录（列表/历史/删除）

**Files:**
- Create: `apps/api/app/services/session_catalog.py`
- Test: `apps/api/app/tests/test_session_catalog.py`

- [ ] **Step 1: 写失败测试**

```python
from types import SimpleNamespace

import pytest

from app.services import sdk_bridge, session_catalog
from app.services.claude_runner import transcript_path
from app.services.settings_store import PlatformSettings


class FakeStore:
    async def get(self):
        return PlatformSettings(agents=[], channels=[], channel_conversations={
            "wx-1": {"session_id": "s1", "channel_id": "ch1", "display_name": "张三"}
        })


@pytest.mark.asyncio
async def test_list_sessions_maps_channel_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sdk_bridge,
        "list_sessions",
        lambda directory=None: [
            SimpleNamespace(
                session_id="s1",
                summary="hi",
                last_modified=10,
                first_prompt="hi",
                cwd=str(tmp_path / "workspaces" / "default"),
            )
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    rows = await catalog.list_sessions("default")
    assert rows[0]["id"] == "s1"
    assert rows[0]["channel_name"] == "张三"


@pytest.mark.asyncio
async def test_messages_extract_text_and_blocks(monkeypatch):
    monkeypatch.setattr(
        sdk_bridge,
        "get_session_messages",
        lambda session_id: [
            SimpleNamespace(
                type="user",
                uuid="u1",
                message={"role": "user", "content": "hi"},
                parent_tool_use_id=None,
            ),
            SimpleNamespace(
                type="assistant",
                uuid="a1",
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hm"},
                        {"type": "text", "text": "hello"},
                    ],
                },
                parent_tool_use_id=None,
            ),
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=__import__("pathlib").Path("/tmp"))
    rows = await catalog.messages("s1")
    assert rows[0] == {"id": "u1", "role": "user", "content": "hi", "blocks": []}
    assert rows[1]["content"] == "hello"
    assert rows[1]["blocks"][0]["type"] == "thinking"


@pytest.mark.asyncio
async def test_delete_removes_transcript(tmp_path):
    path = transcript_path("s1", "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    assert await catalog.delete("default", "s1") is True
    assert not path.exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_session_catalog.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/services/session_catalog.py`：

```python
from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

from app.services import sdk_bridge
from app.services.claude_env import workspace_for
from app.services.claude_runner import transcript_path


def _text_from_message(message: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    content = message.get("content")
    if isinstance(content, str):
        return content, []
    blocks: list[dict[str, Any]] = []
    texts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                texts.append(str(block.get("text") or ""))
            elif kind in ("thinking", "tool_use"):
                blocks.append(block)
    return "\n".join(part for part in texts if part), blocks


class SessionCatalog:
    def __init__(self, store: Any, *, home: Path | None = None) -> None:
        self._store = store
        self._home = home

    async def list_sessions(self, agent_id: str) -> list[dict[str, Any]]:
        settings = await self._store.get()
        labels = {
            entry.session_id: entry
            for entry in settings.channel_conversations.values()
        }
        sessions = await asyncio.to_thread(sdk_bridge.list_sessions)
        workspace = workspace_for(agent_id, self._home).resolve()
        rows: list[dict[str, Any]] = []
        for session in sessions:
            session_cwd = str(getattr(session, "cwd", "") or "")
            if not session_cwd or Path(session_cwd).resolve() != workspace:
                continue
            session_id = str(getattr(session, "session_id", ""))
            if not session_id:
                continue
            conversation = labels.get(session_id)
            title = str(getattr(session, "summary", "") or "新会话")
            if len(title) > 80:
                title = f"{title[:79]}…"
            rows.append(
                {
                    "id": session_id,
                    "title": title,
                    "last_modified": int(getattr(session, "last_modified", 0) or 0),
                    "channel_name": conversation.display_name if conversation else None,
                    "channel_type": "wechat" if conversation else None,
                }
            )
        rows.sort(key=lambda item: item["last_modified"], reverse=True)
        return rows

    async def messages(
        self, session_id: str, *, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        if agent_id is not None and not transcript_path(
            session_id, agent_id, home=self._home
        ).is_file():
            return []
        rows: list[dict[str, Any]] = []
        raw_messages = await asyncio.to_thread(sdk_bridge.get_session_messages, session_id)
        for message in raw_messages:
            if getattr(message, "parent_tool_use_id", None):
                continue
            if str(getattr(message, "type", "")) == "user":
                content, _ = _text_from_message(getattr(message, "message", {}) or {})
                if not content.strip():
                    continue
                rows.append(
                    {"id": str(getattr(message, "uuid", "")), "role": "user", "content": content, "blocks": []}
                )
            elif str(getattr(message, "type", "")) == "assistant":
                content, blocks = _text_from_message(getattr(message, "message", {}) or {})
                if not content.strip() and not blocks:
                    continue
                rows.append(
                    {
                        "id": str(getattr(message, "uuid", "")),
                        "role": "assistant",
                        "content": content,
                        "blocks": blocks,
                    }
                )
        return rows

    async def rename(
        self, session_id: str, title: str, *, agent_id: str | None = None
    ) -> None:
        if agent_id is not None and not transcript_path(
            session_id, agent_id, home=self._home
        ).is_file():
            raise FileNotFoundError(session_id)
        await asyncio.to_thread(sdk_bridge.rename_session, session_id, title)

    async def delete(self, agent_id: str, session_id: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id):
            return False
        try:
            if str(uuid.UUID(session_id)) != session_id:
                return False
        except ValueError:
            return False
        path = transcript_path(session_id, agent_id, home=self._home)
        if not path.is_file():
            return False
        await asyncio.to_thread(sdk_bridge.delete_session, session_id)
        return True
```

> **质量审查修订（执行时已落实）：** 删除改为走 SDK 的 `delete_session`（`sdk_bridge` 包装），由 SDK 校验 UUID 并清理 `<project>/<session_id>/subagents/`；`agent_id` 用 `[A-Za-z0-9_-]{1,64}` 校验，`session_id` 要求规范 UUID 字符串，且 transcript 必须属于该 Agent 才删除，杜绝路径穿越。`messages`/`rename` 增加可选 `agent_id` 归属校验（transcript 不存在时分别返回 `[]` / 抛 `FileNotFoundError`）。标题截断到 80 字符。测试补 cwd 不匹配过滤、倒序、穿越拒绝、缺失文件、tool-result-only 跳过、多 text 拼接。**遗留（Task 10/15）：** 删除路由需带 `agent_id` 并在 `registry.is_busy()` 时 409；图片/附件在历史里只保留文本，需在文档中说明。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_session_catalog.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/session_catalog.py apps/api/app/tests/test_session_catalog.py
git commit -m "Add SDK session catalog"
```

---

### Task 10: Chat 与会话 API

**Files:**
- Modify: `apps/api/app/schemas.py`（新增 `TurnAcceptedOut`、`SessionSummaryOut`、`TranscriptMessageOut`）
- Modify: `apps/api/app/routers/chat.py`（发送改为入队）
- Modify: `apps/api/app/routers/sessions.py`（列表/历史/流式/中断/删除）
- Test: `apps/api/app/tests/test_chat_api.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest


@pytest.fixture
def fake_registry(client):
    class FakeRegistry:
        def __init__(self):
            self.turns = []

        async def submit(self, turn):
            self.turns.append(turn)
            return "turn-1"

        def is_busy(self, session_id):
            return False

    registry = FakeRegistry()
    client.app.state.registry = registry
    return registry


def test_send_message_enqueues(client, fake_registry):
    response = client.post(
        "/api/chat/messages",
        json={
            "agent_id": "default",
            "session_id": "11111111-1111-1111-1111-111111111111",
            "content": "hi",
        },
    )
    assert response.status_code == 201
    assert response.json()["turn_id"] == "turn-1"
    assert fake_registry.turns[0].prompt == "hi"


def test_interrupt_endpoint(client):
    class FakeRegistry:
        async def interrupt(self, session_id):
            return True

    client.app.state.registry = FakeRegistry()
    response = client.post("/api/sessions/11111111-1111-1111-1111-111111111111/interrupt")
    assert response.status_code == 200
    assert response.json() == {"interrupted": True}


def test_delete_busy_session_conflicts(client):
    class FakeRegistry:
        def is_busy(self, session_id):
            return True

    client.app.state.registry = FakeRegistry()
    response = client.delete(
        "/api/sessions/11111111-1111-1111-1111-111111111111?agent_id=default"
    )
    assert response.status_code == 409
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_chat_api.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/schemas.py` 追加：

```python
class TurnAcceptedOut(BaseModel):
    session_id: str
    turn_id: str
    accepted: bool = True


class SessionSummaryOut(BaseModel):
    id: str
    title: str
    last_modified: int = 0
    channel_type: str | None = None
    channel_name: str | None = None
    busy: bool = False


class TranscriptMessageOut(BaseModel):
    id: str
    role: str
    content: str = ""
    blocks: list[dict] = []
```

`apps/api/app/routers/chat.py` 替换整个文件：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import (
    ChatCommandRequest,
    ChatCommandResponse,
    ChatRequest,
    TurnAcceptedOut,
)
from app.services import chat_commands
from app.services.claude_runner import Turn

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_auth)])

HELP_TEXT = (
    "可用命令：\n"
    "/new 开始新会话（旧会话保留在会话列表）\n"
    "/clear 同 /new\n"
    "/status 查看当前会话与执行状态\n"
    "/stop 停止当前任务\n"
    "/help 显示本说明\n\n"
    "直接发送其他内容即可与 Agent 对话。"
)


@router.post("/messages", response_model=TurnAcceptedOut, status_code=201)
async def send_message(payload: ChatRequest, request: Request) -> TurnAcceptedOut:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    turn_id = await registry.submit(
        Turn(
            session_id=payload.session_id,
            agent_id=payload.agent_id,
            prompt=content,
            source="manual",
        )
    )
    return TurnAcceptedOut(session_id=payload.session_id, turn_id=turn_id)


@router.post("/commands", response_model=ChatCommandResponse)
async def run_command(payload: ChatCommandRequest, request: Request) -> ChatCommandResponse:
    command = chat_commands.parse_command(payload.command)
    if command is None:
        raise HTTPException(status_code=400, detail="not a command")
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    if command in ("/new", "/clear"):
        reply = "已开始新会话，之前的对话不会带入；旧会话仍保留在会话列表。"
    elif command == "/help":
        reply = HELP_TEXT
    elif command == "/status":
        busy = registry.is_busy(payload.session_id)
        reply = (
            f"Agent：{payload.agent_id}\n会话：{payload.session_id}\n"
            f"状态：{'执行中' if busy else '空闲'}"
        )
    elif command == "/stop":
        stopped = await registry.interrupt(payload.session_id)
        reply = "已请求停止当前任务。" if stopped else "当前没有正在执行的任务。"
    else:
        reply = f"未知命令。\n\n{HELP_TEXT}"
    return ChatCommandResponse(command=command, reply=reply)
```

`ChatRequest` 增加 `agent_id: str`（默认 `"default"`）。

`apps/api/app/routers/sessions.py` 替换为：

```python
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import require_auth
from app.schemas import SessionSummaryOut, TranscriptMessageOut

router = APIRouter(prefix="/api", tags=["sessions"], dependencies=[Depends(require_auth)])


def _catalog(request: Request):
    catalog = getattr(request.app.state, "session_catalog", None)
    if catalog is None:
        raise HTTPException(status_code=503, detail="session catalog is not ready")
    return catalog


@router.get("/agents/{agent_id}/sessions", response_model=list[SessionSummaryOut])
async def list_sessions(agent_id: str, request: Request) -> list[SessionSummaryOut]:
    catalog = _catalog(request)
    registry = getattr(request.app.state, "registry", None)
    rows = await catalog.list_sessions(agent_id)
    return [
        SessionSummaryOut(**row, busy=bool(registry and registry.is_busy(row["id"])))
        for row in rows
    ]


@router.get("/sessions/{session_id}/messages", response_model=list[TranscriptMessageOut])
async def list_messages(
    session_id: str, request: Request, agent_id: str | None = None
) -> list[TranscriptMessageOut]:
    catalog = _catalog(request)
    return [
        TranscriptMessageOut(**row)
        for row in await catalog.messages(session_id, agent_id=agent_id)
    ]


@router.get("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str, request: Request, turn_id: str | None = None
) -> StreamingResponse:
    registry = getattr(request.app.state, "registry", None)
    stream = None
    if registry is not None:
        resolved = turn_id or registry.current_turn_id(session_id)
        stream = registry.stream(resolved) if resolved else None
    if stream is None:
        raise HTTPException(status_code=409, detail="no active turn for session")

    async def generator():
        queue = await stream.subscribe()
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") == "turn_end":
                return

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str, request: Request) -> dict[str, bool]:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    return {"interrupted": await registry.interrupt(session_id)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, agent_id: str, request: Request) -> dict[str, bool]:
    registry = getattr(request.app.state, "registry", None)
    if registry is not None and registry.is_busy(session_id):
        raise HTTPException(status_code=409, detail="session has an active turn")
    catalog = _catalog(request)
    deleted = await catalog.delete(agent_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": True}


@router.post("/sessions/{session_id}/rename")
async def rename_session(
    session_id: str,
    payload: dict[str, str],
    request: Request,
    agent_id: str | None = None,
) -> dict[str, bool]:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        await _catalog(request).rename(session_id, title, agent_id=agent_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return {"renamed": True}
```

同时删除 `apps/api/app/routers/runs.py` 的注册（`main.py`），因为 Run 概念移除。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_chat_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/chat.py apps/api/app/routers/sessions.py apps/api/app/tests/test_chat_api.py
git commit -m "Switch chat API to SDK turns"
```

> **质量审查修订（执行时已落实）：** `TurnStream` 增加 `unsubscribe(queue)`，SSE generator 用 `try/finally` 退出时退订；`SessionRegistry` 记录 `turn_id → session_id` 并让 `stream(turn_id, session_id)` 校验归属，新增 `close_session(session_id)` 供删除会话时释放缓存 client；`SessionCatalog.delete` 成功后清理 `workspaces/<agent>/screenshots/<session_id>` 并解绑 `channel_conversations`（`SettingsStore.mutate_raw`）；`rename`/`messages` 把 SDK 的 `ValueError` 转为 400/空列表，`agent_id` 在 messages/rename 上改为必填；stream 在 registry 未就绪时返回 503；聊天命令的 `/status`、`/stop` 处理 `session_id=None`；解析函数与文案抽到纯模块 `services/commands.py`，`chat_commands` 改为重导出。测试补 SSE 回放/退订、rename 非法 id、命令分支、503 守卫、删除后 close_session 调用。

---

### Task 11: 启动恢复

**Files:**
- Create: `apps/api/app/services/recovery.py`
- Test: `apps/api/app/tests/test_recovery.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest

from app.services import recovery, turn_lease
from app.services.claude_runner import transcript_path


class FakeStore:
    async def get(self):
        from app.services.settings_store import AgentSection, PlatformSettings

        return PlatformSettings(agents=[AgentSection(id="default", name="Assistant")])


class FakeRegistry:
    def __init__(self):
        self.turns = []

    async def submit(self, turn):
        self.turns.append(turn)


@pytest.mark.asyncio
async def test_recovery_resubmits_only_existing_transcripts(tmp_path):
    path = transcript_path("s1", "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")
    await turn_lease.add("s1")
    await turn_lease.add("s2")
    registry = FakeRegistry()
    await recovery.recover_running_sessions(
        registry, FakeStore(), home=tmp_path
    )
    assert [turn.session_id for turn in registry.turns] == ["s1"]
    assert await turn_lease.is_running("s2") is False
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_recovery.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/services/recovery.py`：

```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services import turn_lease
from app.services.claude_runner import Turn, transcript_path

logger = logging.getLogger(__name__)


async def recover_running_sessions(
    registry: Any, store: Any, *, home: Path | None = None
) -> None:
    settings = await store.get()
    agents = {agent.id: agent for agent in settings.agents}
    for session_id in await turn_lease.list_all():
        found = None
        for agent in agents.values():
            if transcript_path(session_id, agent.id, home=home).is_file():
                found = agent
                break
        if found is None:
            logger.info("dropping lease without transcript session=%s", session_id)
            await turn_lease.remove(session_id)
            continue
        await registry.submit(
            Turn(
                session_id=session_id,
                agent_id=found.id,
                prompt="",
                source="resume",
            )
        )
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_recovery.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/recovery.py apps/api/app/tests/test_recovery.py
git commit -m "Add restart recovery for running sessions"
```

> **质量审查修订（执行时已落实）：** 恢复前校验 canonical UUID（非法行删除并 warning）；若 settings 读取失败/损坏导致 `agents` 为空，**直接跳过本轮并保留全部租约**（避免把有效租约误删）；单行失败只跳过该行。测试补空 agents 保留租约、非法 id 清理。**只考虑 `status == "active"` 的 Agent**；被暂停/归档 Agent 的租约**保留并跳过**（重新启用后下次启动可续跑），完全没有归属的租约才删除。

---

### Task 12: 启动装配

**Files:**
- Modify: `apps/api/app/main.py`
- Test: `apps/api/app/tests/test_app_boot.py`

- [ ] **Step 1: 写失败测试**

```python
def test_app_boots_with_settings_store(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_settings_file_seeded(client):
    from app.services.settings_store import get_settings_store

    assert get_settings_store().path.exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_app_boot.py -q`
Expected: FAIL（settings 未 seed）

- [ ] **Step 3: 实现**

`main.py` 的 lifespan 中：

- 暂时保留 `seed_default_provider`、`seed_default_agent`、`sync_default_agent_instructions`（配置接口在第二份计划迁移，届时删除）；
- 增加：

```python
        from app.services.claude_env import ensure_claude_dirs, install_claude_process_env
        from app.services.settings_store import get_settings_store

        install_claude_process_env()
        ensure_claude_dirs()
        store = get_settings_store()
        await store.ensure_seeded()
        app.state.settings_store = store
```

- `start_worker=True` 时：

```python
            from app.services.claude_runner import ClaudeTurnRunner
            from app.services.recovery import recover_running_sessions
            from app.services.session_catalog import SessionCatalog
            from app.services.session_registry import SessionRegistry

            runner = ClaudeTurnRunner(store)
            registry = SessionRegistry(runner, concurrency=settings.run_concurrency)
            app.state.registry = registry
            app.state.session_catalog = SessionCatalog(store)
            await recover_running_sessions(registry, store)
```

- 停止时 `await registry.close()`，再 `await runner.close_all()` 断开仍缓存的 SDK 客户端子进程。
- 只删除 `runs_router` 的注册；`memories_router`、`providers_router` 暂时保留，由第二份计划改写为 settings 版本后继续注册。
- 删除 `memory_service` 相关导入。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_app_boot.py apps/api/app/tests/test_chat_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/main.py apps/api/app/tests/test_app_boot.py
git commit -m "Wire SDK runtime into app startup"
```

> **质量审查修订（执行时已落实）：** 停机时 `registry.close()` / `runner.close_all()` 分别 try/except 记录日志，避免一处失败跳过后续 worker 停止；整个 `start_worker` 停机块用 `try/finally` 保证 `market_service.close()` 一定执行，`start_worker=False` 路径也释放行情 client。**启动阶段的异常同样进入统一 teardown**（lifespan 的启动代码包在 `try` 中，所有旧 worker 的 stop 逐个兜底）。新增 `start_worker=True` 的装配测试（runner/registry/catalog 存在、停机顺序、启动退出不挂起），seed 测试收紧到路径、默认 Agent 与 0700 权限。运行器在 `CancelledError`（优雅停机）时保留租约以便重启续跑。

---

### Task 13: 前端 API 与类型

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/types.ts`

- [ ] **Step 1: 改类型**

`types.ts` 增加：

```ts
export type SessionSummary = {
  id: string;
  title: string;
  last_modified: number;
  channel_type: string | null;
  channel_name: string | null;
  busy: boolean;
};

export type TranscriptMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  blocks: Array<{ type: string; [key: string]: unknown }>;
};

export type TurnStreamEvent =
  | { type: "assistant_delta"; delta: string }
  | { type: "reasoning_delta"; delta: string }
  | { type: "tool_call"; name: string; input: Record<string, unknown> }
  | { type: "turn_end"; status?: "completed" | "failed" | "cancelled"; error?: string | null; terminal_reason?: string | null; usage?: Record<string, unknown> | null }
  | { type: "turn_error"; error: string };
```

- [ ] **Step 2: 改 api.ts**

删除 `getSessionActiveRun`、`getRunEvents`、`retryRun`、`cancelRun`、`getMessagesPage`、`searchMessages`、`streamUrl`。

新增：

```ts
export async function sendMessage(input: {
  agent_id: string;
  session_id: string;
  content: string;
}): Promise<{ session_id: string; turn_id: string; accepted: boolean }> {
  return request("/api/chat/messages", { method: "POST", body: JSON.stringify(input) });
}

export async function listSessions(agentId: string): Promise<SessionSummary[]> {
  return request(`/api/agents/${agentId}/sessions`);
}

export async function listSessionMessages(sessionId: string): Promise<TranscriptMessage[]> {
  return request(`/api/sessions/${sessionId}/messages`);
}

export async function interruptSession(sessionId: string): Promise<{ interrupted: boolean }> {
  return request(`/api/sessions/${sessionId}/interrupt`, { method: "POST" });
}

export async function deleteSession(sessionId: string, agentId: string): Promise<{ deleted: boolean }> {
  return request(`/api/sessions/${sessionId}?agent_id=${encodeURIComponent(agentId)}`, {
    method: "DELETE",
  });
}

export function sessionStreamUrl(sessionId: string, turnId?: string): string {
  const suffix = turnId ? `?turn_id=${encodeURIComponent(turnId)}` : "";
  return `/api/sessions/${encodeURIComponent(sessionId)}/stream${suffix}`;
}
```

（`request` 函数沿用 `api.ts` 现有封装；若签名不同，按其参数形式调整。）

- [ ] **Step 3: 编译检查**

Run: `cd apps/web && npm run build`
Expected: PASS（App.tsx 仍引用旧函数，会报错；本任务允许 FAIL，Task 14 修复后必须 PASS）

- [ ] **Step 4: 提交**

```bash
git add apps/web/src/api.ts apps/web/src/types.ts
git commit -m "Add SDK session API client"
```

---

### Task 14: 前端聊天状态

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/components/ChatPanel.tsx`（props 改为 transcript + live 流；移除 run/事件/重试/分页/截图 props；渲染 transcript 的 thinking/tool_use blocks）
- Modify: `apps/web/src/components/Sidebar.tsx`（`Session` → `SessionSummary`：只用 id/title/last_modified/busy；移除搜索框与结果）
- Modify: `apps/web/src/components/Drawer.tsx`（移除 run/events props，改为展示执行过程活动行）

- [ ] **Step 1: 替换会话状态**

`SessionLive` 从 `{activeRun, events, streamText, streamReasoning, activity, notice, tracking}` 改为：

```ts
type SessionLive = {
  streamText: string;
  streamReasoning: string;
  activity: string[];
  notice: string;
  tracking: boolean;
};
```

删除与 `Run` / `RunEvent` 有关的类型与函数：`pollRun`、`applyEvent`、`finishRun`、`rememberEvents`、`isActiveRun`、`terminal`、`mergeRunEvents`。

- [ ] **Step 2: 新发送流程**

```ts
async function sendMessage(content: string) {
  const sessionId = currentSessionId ?? crypto.randomUUID();
  const live = { streamText: "", streamReasoning: "", activity: [], notice: "", tracking: true };
  updateLive(sessionId, () => live);
  const { turn_id } = await api.sendMessage({
    agent_id: agentId,
    session_id: sessionId,
    content,
  });
  await openStream(sessionId, turn_id);
}

async function openStream(sessionId: string, turnId?: string) {
  const source = new EventSource(api.sessionStreamUrl(sessionId, turnId));
  source.onmessage = (message) => {
    const event: TurnStreamEvent = JSON.parse(message.data);
    if (event.type === "assistant_delta") {
      updateLive(sessionId, (live) => ({ ...live, streamText: live.streamText + event.delta }));
    } else if (event.type === "reasoning_delta") {
      updateLive(sessionId, (live) => ({ ...live, streamReasoning: live.streamReasoning + event.delta }));
    } else if (event.type === "tool_call") {
      updateLive(sessionId, (live) => ({ ...live, activity: [...live.activity, event.name] }));
    } else if (event.type === "turn_end") {
      source.close();
      void refreshTranscript(sessionId);
    } else if (event.type === "turn_error") {
      updateLive(sessionId, (live) => ({ ...live, notice: event.error }));
    }
  };
  source.onerror = () => source.close();
}

async function refreshTranscript(sessionId: string) {
  const messages = await api.listSessionMessages(sessionId, agentId);
  setMessages(sessionId, messages);
  updateLive(sessionId, (live) => ({ ...live, streamText: "", streamReasoning: "", activity: [], tracking: false }));
}
```

- [ ] **Step 3: 打开会话**

打开会话时只调 `refreshTranscript`，不再查 active run、不再轮询。若侧栏返回的 `busy` 为 true，额外 `openStream(sessionId)`（不带 turn_id，后端用当前轮次解析）接回进行中的输出；EventSource 报错/收到 409 时关闭并 `refreshTranscript`，同时把 `tracking` 置 false，避免输入区卡在流式态。切换会话时清空当前 live 状态即可。

- [ ] **Step 4: 删除 UI**

删除“执行过程”中依赖 `RunEvent` 的折叠结构、重试按钮、Run 状态徽标；保留 Markdown、附件、引用、命令面板、草稿、移动端。

- [ ] **Step 5: 构建**

Run: `cd apps/web && npm run build`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/App.tsx apps/web/dist
git commit -m "Switch chat UI to native SDK sessions"
```

> **质量审查修订（执行时已落实）：** 发送成功路径必须校验发起时的 agent（`sendAgentId === agentIdRef.current`）才 `setSessionId`/插侧栏/接流，避免切换 Agent 时把 A 的会话与流带进 B。新增 `pendingUserBySession`：流式中追问的乐观 user 消息在 transcript 追上之前始终合并显示（按相同 content 的 user 消息逐条消费）。`turn_end` 后若还有排队轮次，用 `EventSource.onopen` 成功停止、`onerror` 每 500ms 重试最多 10 次的 attach，避免后端 `current_turn_id` 尚未切换导致的 409。清理死代码：`runActivity.ts`、旧 `Session/Message/Run/RunEvent/MessagePage/SessionSearchHit/ScreenshotInfo` 类型（确认无引用后）、无入口的 `renameSession`/截图 API/`describeRunError`/`previewPayload` 与对应 CSS；ChatPanel 删除未使用的 `sessionId` prop；Sidebar busy 文案与 `is_busy` 语义一致。**pending 消息结算以发送时 transcript 里同 content 的 user 消息数为基线**（reload/历史已有同文本时不会被提前吞掉）；`openStream`/`openStreamWithRetry` 在已有存活流时直接返回，避免同一轮次重复订阅导致缓冲 delta 重播。外部入口取消排队轮次后可能残留幽灵 pending，记录为已知边界。**排队轮次以发送返回的 `turn_id` 为准**：每次 send 记住 turn_id，`turn_end` 后直接订阅下一个排队 turn 的流（后端对排队轮次已注册流并回放缓冲），不再依赖无 turn id 的 409 重试；pending 行带 turnId 并区分是否排队（排队行渲染在流式气泡之后）；失败/取消的本轮未写入 transcript 的 pending 行移除并保留错误提示。首次 transcript 加载完成前禁止发送（基线必须来自发送前的快照）。Agent 切换在事件处理里同步清空会话，session 加载 effect 加 owner 守卫，深链会话补登记归属。前端移除 `/memory`（用户记忆由 `CLAUDE.md` 承担），删除确认文案去掉 checkpoint。**transcript 首次加载失败不能锁死输入区**：失败后自动重试一次，点击当前会话强制重新拉取，失败态 placeholder 提示重试，只有成功或 `wasNew` 才标记已加载；带内容的 `/memory ...` 给明确提示而非当普通消息发送。**点击当前会话的重试只在聊天视图生效**（其它页面仍然导航回聊天）；不在侧栏列表的会话按归属登记后允许置为已加载；自动重试计时器与 cancel 的延迟刷新分成两个 map，`closeStream` 不吞自动重试。

---

### Task 15: 核心验收与临时说明

**Files:**
- Modify: `docs/intent.md`、`CLAUDE.md`、`README.md`（仅加“迁移进行中”说明，完整文档同步放到第二份计划 Task 9）

- [ ] **Step 1: 加迁移中说明**

在三处各加一段简短说明（指向设计 spec 与两份计划）：

- 执行核已切换为 Claude Agent SDK + `ClaudeSDKClient`（DeepSeek Anthropic 端点）；
- 会话/消息由 SDK transcript 管理，平台保留 `running_sessions`；
- 配置在 `data/settings.json`；
- 已下线：rubric、eval、会话搜索、Run 历史、记忆系统（v1 阶段）；
- 渠道、定时、配置接口与旧表清理仍在第二份计划执行中，文档以最终完成后为准。

- [ ] **Step 2: 后端测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`
Expected: PASS（旧 run/executor/memory 测试在后续计划删除；本计划结束时允许这些旧测试失败，但必须记录清单）

- [ ] **Step 3: 前端构建**

Run: `cd apps/web && npm run build`
Expected: PASS

- [ ] **Step 4: 冒烟（真实 key，可选）**

```bash
.venv/bin/python - <<'PY'
import asyncio
from app.services.settings_store import get_settings_store
from app.services.claude_runner import ClaudeTurnRunner, Turn
from app.services.turn_stream import TurnStream
async def main():
    store = get_settings_store()
    runner = ClaudeTurnRunner(store)
    stream = TurnStream()
    outcome = await runner.run(Turn(session_id="11111111-1111-1111-1111-111111111111", agent_id="default", prompt="回复 OK"), stream)
    print(outcome.status, outcome.output[:100])
asyncio.run(main())
PY
```

Expected: `completed OK`

- [ ] **Step 5: 提交**

```bash
git add docs/intent.md CLAUDE.md README.md
git commit -m "Document SDK runtime migration"
```

---

## 自审记录

- 覆盖 spec：目录/env（Task 3）、settings（Task 2）、租约/恢复（Task 4/11）、执行核（Task 7/8）、流式接口（Task 10）、会话目录（Task 9）、前端（Task 13/14）。
- 渠道、定时、清理、旧表删除、记忆与配置接口在第二份计划 `2026-11-10-channels-cron-decommission.md`。
- 类型一致性：`Turn`、`TurnOutcome`、`TurnStream`、`SettingsStore` 在跨任务中签名一致。
