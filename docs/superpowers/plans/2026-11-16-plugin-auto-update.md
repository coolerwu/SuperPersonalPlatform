# 插件每周自动更新实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在插件页提供一个默认关闭的「每周自动更新」开关，由平台进程内调度每周刷新官方集市并逐个更新其下已安装插件，失败可观测、可重试。

**Architecture:** `settings.json` 新增 `plugin_updates` 节保存开关与最近一次结果；新增 `PluginAutoUpdateRuntime` 每小时检查是否到期（成功 7 天后、失败 1 天后），到期时在插件配置锁内先 `plugin marketplace update`，再对每个已安装插件执行 `plugin update`；更新成功后写 `.plugins-revision`，下一轮对话重建缓存 client。插件页显示开关、上次结果与下次计划。不启用 Claude 自带的 marketplace `autoUpdate`。

**Tech Stack:** FastAPI（Python 3.12）、Claude Code 内置 CLI、React 18 + TypeScript + Vite、pytest。

**依据设计：** `docs/superpowers/specs/2026-11-16-plugin-auto-update-design.md`

## 执行前

这个 checkout 可能被并行会话共用（当前工作区里就有侧栏工作留下的未提交改动）。执行本计划前：

1. 按 `superpowers:using-git-worktrees` 建立独立 worktree，或在确认 `git status --short` 干净后再开始。
2. 不要提交与 F25 无关的改动；尤其不要提交并行会话对 `CLAUDE.md`、`README.md`、`docs/intent.md`、`apps/web/src/components/Sidebar.tsx`、`apps/web/src/styles.css` 与 `apps/web/dist` 的修改。
3. 并行工作合并时，`docs/intent.md`、`CLAUDE.md`、`README.md` 可能在同一段落附近出现冲突；按各自新增内容合并，不要丢掉任何一方。

---

## 文件结构

### 后端

- `apps/api/app/services/settings_store.py`：`PluginUpdatesSection`、`PlatformSettings.plugin_updates`、解析/reseed、`update_plugin_updates()`。
- `apps/api/app/services/plugins.py`：`AutoUpdateResult`、`auto_update_installed()`。
- `apps/api/app/services/plugin_updates.py`（新建）：到期判定与 `PluginAutoUpdateRuntime`。
- `apps/api/app/config.py`：`plugin_auto_update_tick_seconds`。
- `apps/api/app/main.py`：启动/停止 runtime，挂到 `app.state.plugin_updates`。
- `apps/api/app/schemas.py`：`PluginAutoUpdateUpdate`、`PluginAutoUpdateOut`。
- `apps/api/app/routers/plugins.py`：`GET/PUT /api/plugins/auto-update`。
- 测试：`test_settings_store.py`、`test_plugins.py`、`test_plugin_updates.py`（新建）、`test_plugins_api.py`、`test_app_boot.py`。

### 前端

- `apps/web/src/types.ts`：`PluginAutoUpdate`。
- `apps/web/src/api.ts`：`getPluginAutoUpdate` / `updatePluginAutoUpdate`。
- `apps/web/src/components/PluginsPage.tsx`：自动更新卡片与开关。

### 文档

- `docs/intent.md`：F25。
- `CLAUDE.md`、`README.md`：插件集市说明。
- `docs/superpowers/specs/2026-11-16-plugin-auto-update-design.md`（已存在）。
- 本计划文档。

---

## Task 1: 需求记录与设计文档

**Files:**
- Modify: `docs/intent.md`（头部编号水位与末尾追加）
- Already created: `docs/superpowers/specs/2026-11-16-plugin-auto-update-design.md`

- [ ] **Step 1: 更新 `docs/intent.md` 头部**

把：

```markdown
- 最后更新：2026-11-15
- 编号水位：F24
```

改为：

```markdown
- 最后更新：2026-11-16
- 编号水位：F25
```

- [ ] **Step 2: 在 `docs/intent.md` 末尾追加 F25**

```markdown
### F25 插件每周自动更新

- 状态：现行
- 日期：2026-11-16
- 来源：2026-11-16 插件每周自动更新设计

#### 功能点

1. 插件页提供「每周自动更新」开关，默认关闭；所有 Agent 共享。启用前必须已有官方集市。
2. 启用后，平台在进程内每小时检查一次；距上次尝试满 7 天时，先刷新官方集市清单，再逐个更新官方集市下已安装的插件（包括已停用的插件），其他集市不处理。
3. 单个插件更新失败不中断其余插件；本轮有失败时记录第一个失败原因，并在 1 天后重试；全部成功时记录成功时间。更新期间持有插件配置锁，与页面上的安装/启停/更新/卸载互斥；锁被占用时本轮跳过并等待下一次检查。
4. 更新成功后下一轮对话重建缓存 client 并加载新版本；正在执行的 turn 不受影响。
5. 插件页展示开关状态、上次尝试时间与结果、上次成功时间、下次计划时间和错误摘要；关闭开关保留历史记录。
6. 平台不启用 Claude Code 自带的 marketplace auto-update（它在交互式会话启动后按 marketplace 自动执行，不是每周调度），避免与平台调度重复更新。
7. 本期只做固定 7 天周期，不支持自定义星期/时间或按 Agent 配置。

#### 验收标准

- 默认 `GET /api/plugins/auto-update` 返回 `enabled: false`；`PUT` 开启后写入 `settings.json` 的 `plugin_updates` 节并立即唤醒检查；没有官方集市时开启返回 409。
- 启用且从未执行时，服务启动后或开启后的一次检查内执行；成功后 `last_attempt_at`、`last_success_at`、`last_summary` 更新，`next_attempt_at` 约为 7 天后。
- 7 天内不重复执行；失败后 1 天重试；部分失败时 `last_error` 非空且 `last_success_at` 保持不变。
- 逐个更新只针对官方集市下已安装插件；一个失败不影响其他插件；更新成功后 `.plugins-revision` 变化，下一轮对话加载新版本。
- 插件配置锁被占用时本轮不记录尝试，下一次检查重试。
- 后端测试与前端构建通过。
```

- [ ] **Step 3: 提交**

```bash
git add docs/intent.md docs/superpowers/specs/2026-11-16-plugin-auto-update-design.md docs/superpowers/plans/2026-11-16-plugin-auto-update.md
git commit -m "Record the weekly plugin auto-update requirements

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: `plugin_updates` 配置节

**Files:**
- Modify: `apps/api/app/services/settings_store.py`
- Test: `apps/api/app/tests/test_settings_store.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_settings_store.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_plugin_updates_section_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(build_default_settings()), "utf-8")
    store = SettingsStore(path)

    await store.update_plugin_updates(
        enabled=True,
        last_attempt_at="2026-11-16T00:00:00+00:00",
        last_summary="已更新集市",
    )

    section = (await store.get()).plugin_updates
    assert section.enabled is True
    assert section.last_attempt_at == "2026-11-16T00:00:00+00:00"
    assert section.last_summary == "已更新集市"
    data = json.loads(path.read_text("utf-8"))
    assert data["plugin_updates"]["enabled"] is True
    assert data["plugin_updates"]["last_summary"] == "已更新集市"


@pytest.mark.asyncio
async def test_plugin_updates_section_rejects_long_messages(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(build_default_settings()), "utf-8")
    store = SettingsStore(path)

    with pytest.raises(ValueError):
        await store.update_plugin_updates(last_error="x" * 501)


@pytest.mark.asyncio
async def test_plugin_updates_invalid_section_falls_back_to_defaults(tmp_path):
    data = build_default_settings()
    data["plugin_updates"] = {"enabled": "not-a-bool"}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), "utf-8")

    section = (await SettingsStore(path).get()).plugin_updates
    assert section.enabled is False
    assert section.last_summary == ""
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_settings_store.py -v
```

Expected: FAIL，`PluginUpdatesSection` 或 `update_plugin_updates` 不存在。

- [ ] **Step 3: 实现配置节**

在 `apps/api/app/services/settings_store.py` 的 `MapsSection` 之后新增：

```python
class PluginUpdatesSection(BaseModel):
    enabled: bool = False
    last_attempt_at: str = ""
    last_success_at: str = ""
    last_summary: str = Field(default="", max_length=500)
    last_error: str = Field(default="", max_length=500)
```

把 `PlatformSettings` 改为：

```python
class PlatformSettings(BaseModel):
    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    knowledge: KnowledgeSection = Field(default_factory=KnowledgeSection)
    maps: MapsSection = Field(default_factory=MapsSection)
    plugin_updates: PluginUpdatesSection = Field(
        default_factory=PluginUpdatesSection
    )
    agents: list[AgentSection] = Field(default_factory=list)
    channels: list[ChannelSection] = Field(default_factory=list)
    channel_conversations: dict[str, ConversationSection] = Field(
        default_factory=dict
    )
```

在 `_parse()` 的 `PlatformSettings(...)` 里加入：

```python
        plugin_updates=_section(
            PluginUpdatesSection, raw.get("plugin_updates")
        ),
```

在 `_merge_with_defaults()` 的 section 元组里加入：

```python
        ("plugin_updates", PluginUpdatesSection),
```

在 `SettingsStore` 的 `update_knowledge()` 之后新增：

```python
    async def update_plugin_updates(self, **values: Any) -> None:
        validated = _validated_update(PluginUpdatesSection, values)

        def mutate(data: dict[str, Any]) -> None:
            _dict_section(data, "plugin_updates").update(validated)

        await self._json.update(mutate)
```

`build_default_settings()` 不需要显式加字段：`PlatformSettings` 的默认工厂会生成空节。

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_settings_store.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/settings_store.py apps/api/app/tests/test_settings_store.py
git commit -m "Add the plugin auto-update settings section

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: 批量更新服务

**Files:**
- Modify: `apps/api/app/services/plugins.py`
- Test: `apps/api/app/tests/test_plugins.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_plugins.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_auto_update_refreshes_marketplace_and_plugins(
    monkeypatch, tmp_path
):
    manifest_dir = _manifest(tmp_path, ["alpha"])
    calls: list[list[str]] = []
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed=_installed(f"alpha@{OFFICIAL}"),
        calls=calls,
    )

    result = await plugins.auto_update_installed(home=tmp_path)

    assert result.updated == (f"alpha@{OFFICIAL}",)
    assert result.failed == ()
    assert ["plugin", "marketplace", "update", OFFICIAL] in calls
    assert [
        "plugin",
        "update",
        f"alpha@{OFFICIAL}",
        "--scope",
        "user",
    ] in calls
    assert (tmp_path / "claude" / ".plugins-revision").is_file()


@pytest.mark.asyncio
async def test_auto_update_isolates_plugin_failures(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, ["alpha", "beta"])
    installed = json.dumps(
        [
            {
                "id": f"alpha@{OFFICIAL}",
                "version": "1.0.0",
                "scope": "user",
                "enabled": True,
                "installedAt": "2026-01-01T00:00:00Z",
                "lastUpdated": "2026-01-01T00:00:00Z",
            },
            {
                "id": f"beta@{OFFICIAL}",
                "version": "1.0.0",
                "scope": "user",
                "enabled": False,
                "installedAt": "2026-01-01T00:00:00Z",
                "lastUpdated": "2026-01-01T00:00:00Z",
            },
        ]
    )
    calls: list[list[str]] = []
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed=installed,
        calls=calls,
        fail_on=f"plugin update beta@{OFFICIAL} --scope user",
    )

    result = await plugins.auto_update_installed(home=tmp_path)

    assert result.updated == (f"alpha@{OFFICIAL}",)
    assert result.failed[0][0] == f"beta@{OFFICIAL}"
    assert "boom" in result.failed[0][1]
    assert [
        "plugin",
        "update",
        f"alpha@{OFFICIAL}",
        "--scope",
        "user",
    ] in calls
    assert (tmp_path / "claude" / ".plugins-revision").is_file()


@pytest.mark.asyncio
async def test_auto_update_requires_official_marketplace(monkeypatch, tmp_path):
    _patch_cli(
        monkeypatch,
        marketplaces="[]",
        installed="[]",
        calls=[],
    )

    with pytest.raises(plugins.PluginNotFoundError):
        await plugins.auto_update_installed(home=tmp_path)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_plugins.py -v
```

Expected: FAIL，`auto_update_installed` 不存在。

- [ ] **Step 3: 实现服务**

在 `apps/api/app/services/plugins.py` 的 `PluginCatalog` 之后新增：

```python
@dataclass(frozen=True)
class AutoUpdateResult:
    updated: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
```

在 `update_official_marketplace()` 之后新增：

```python
async def auto_update_installed(
    home: Path | None = None,
) -> AutoUpdateResult:
    """Refresh the official marketplace and update its installed plugins."""
    async with _serialized():
        current = await _catalog_locked(home)
        if not current.cli_available:
            raise PluginUnavailableError("未找到 Claude Code CLI")
        official = _official_marketplace(current.marketplaces)
        if official is None:
            raise PluginNotFoundError("还没有添加官方集市")
        await run_cli(
            ["plugin", "marketplace", "update", official.name],
            timeout=NETWORK_TIMEOUT_SECONDS,
            home=home,
        )
        current = await _catalog_locked(home)
        updated: list[str] = []
        failed: list[tuple[str, str]] = []
        for item in current.installed:
            if item.marketplace != official.name:
                continue
            try:
                await run_cli(
                    ["plugin", "update", item.id, "--scope", "user"],
                    timeout=NETWORK_TIMEOUT_SECONDS,
                    home=home,
                )
            except PluginError as exc:
                failed.append((item.id, str(exc)))
            else:
                updated.append(item.id)
        if updated:
            _touch_revision(home)
        return AutoUpdateResult(
            updated=tuple(updated), failed=tuple(failed)
        )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_plugins.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/plugins.py apps/api/app/tests/test_plugins.py
git commit -m "Add a batch plugin update service

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: 每周调度运行时

**Files:**
- Create: `apps/api/app/services/plugin_updates.py`
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/tests/test_app_boot.py`
- Test: `apps/api/app/tests/test_plugin_updates.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_plugin_updates.py`：

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import plugins
from app.services.plugin_updates import (
    PluginAutoUpdateRuntime,
    is_due,
    next_attempt_at,
)
from app.services.settings_store import PluginUpdatesSection

OFFICIAL = "claude-plugins-official"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


class _Store:
    def __init__(self, section: PluginUpdatesSection) -> None:
        self.section = section
        self.writes: list[dict[str, str]] = []

    async def get(self):
        return SimpleNamespace(plugin_updates=self.section)

    async def update_plugin_updates(self, **values: str) -> None:
        self.writes.append(dict(values))
        self.section = self.section.model_copy(update=values)


def _runtime(store: _Store) -> PluginAutoUpdateRuntime:
    return PluginAutoUpdateRuntime(
        store=store,
        tick_seconds=60,
        now=lambda: NOW,
    )


def test_next_attempt_at_waits_a_week_after_success():
    section = PluginUpdatesSection(
        enabled=True,
        last_attempt_at=(NOW - timedelta(days=2)).isoformat(),
    )

    assert next_attempt_at(section, NOW) == NOW + timedelta(days=5)
    assert is_due(section, NOW) is False


def test_next_attempt_at_retries_a_day_after_failure():
    section = PluginUpdatesSection(
        enabled=True,
        last_attempt_at=(NOW - timedelta(days=2)).isoformat(),
        last_error="boom",
    )

    assert next_attempt_at(section, NOW) == NOW - timedelta(days=1)
    assert is_due(section, NOW) is True


def test_disabled_section_is_never_due():
    section = PluginUpdatesSection(enabled=False)

    assert next_attempt_at(section, NOW) is None
    assert is_due(section, NOW) is False


@pytest.mark.asyncio
async def test_first_run_records_success(monkeypatch):
    async def fake_update(home=None):
        return plugins.AutoUpdateResult(
            updated=(f"alpha@{OFFICIAL}",)
        )

    monkeypatch.setattr(plugins, "auto_update_installed", fake_update)
    store = _Store(PluginUpdatesSection(enabled=True))

    await _runtime(store).run_once()

    assert store.section.last_attempt_at == NOW.isoformat()
    assert store.section.last_success_at == NOW.isoformat()
    assert store.section.last_error == ""
    assert "更新 1 个插件" in store.section.last_summary


@pytest.mark.asyncio
async def test_recent_success_is_not_run_again(monkeypatch):
    async def fake_update(home=None):
        raise AssertionError("must not run")

    monkeypatch.setattr(plugins, "auto_update_installed", fake_update)
    store = _Store(
        PluginUpdatesSection(
            enabled=True,
            last_attempt_at=(NOW - timedelta(days=2)).isoformat(),
            last_success_at=(NOW - timedelta(days=2)).isoformat(),
        )
    )

    await _runtime(store).run_once()

    assert store.writes == []


@pytest.mark.asyncio
async def test_stale_success_is_run_again(monkeypatch):
    ran = []

    async def fake_update(home=None):
        ran.append(True)
        return plugins.AutoUpdateResult()

    monkeypatch.setattr(plugins, "auto_update_installed", fake_update)
    store = _Store(
        PluginUpdatesSection(
            enabled=True,
            last_attempt_at=(NOW - timedelta(days=8)).isoformat(),
            last_success_at=(NOW - timedelta(days=8)).isoformat(),
        )
    )

    await _runtime(store).run_once()

    assert ran == [True]
    assert "没有已安装插件" in store.section.last_summary


@pytest.mark.asyncio
async def test_busy_lock_is_skipped_without_recording(monkeypatch):
    async def fake_update(home=None):
        raise plugins.PluginBusyError("busy")

    monkeypatch.setattr(plugins, "auto_update_installed", fake_update)
    store = _Store(PluginUpdatesSection(enabled=True))

    await _runtime(store).run_once()

    assert store.writes == []


@pytest.mark.asyncio
async def test_plugin_error_records_attempt_and_clips_message(monkeypatch):
    async def fake_update(home=None):
        raise plugins.PluginCommandError("x" * 1000)

    monkeypatch.setattr(plugins, "auto_update_installed", fake_update)
    store = _Store(PluginUpdatesSection(enabled=True))

    await _runtime(store).run_once()

    assert store.section.last_attempt_at == NOW.isoformat()
    assert store.section.last_success_at == ""
    assert len(store.section.last_error) == 500


@pytest.mark.asyncio
async def test_partial_failure_keeps_last_success(monkeypatch):
    async def fake_update(home=None):
        return plugins.AutoUpdateResult(
            updated=(f"alpha@{OFFICIAL}",),
            failed=((f"beta@{OFFICIAL}", "boom"),),
        )

    monkeypatch.setattr(plugins, "auto_update_installed", fake_update)
    previous = (NOW - timedelta(days=8)).isoformat()
    store = _Store(
        PluginUpdatesSection(
            enabled=True,
            last_attempt_at=previous,
            last_success_at=previous,
        )
    )

    await _runtime(store).run_once()

    assert store.section.last_attempt_at == NOW.isoformat()
    assert store.section.last_success_at == previous
    assert store.section.last_error.startswith(f"beta@{OFFICIAL}: boom")
    assert "1 个失败" in store.section.last_summary
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_plugin_updates.py -v
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现运行时**

创建 `apps/api/app/services/plugin_updates.py`：

```python
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.config import settings
from app.services import plugins
from app.services.settings_store import PluginUpdatesSection

logger = logging.getLogger(__name__)

WEEK_SECONDS = 7 * 24 * 60 * 60
RETRY_SECONDS = 24 * 60 * 60
MAX_MESSAGE_CHARS = 500


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def next_attempt_at(
    section: PluginUpdatesSection, now: datetime
) -> datetime | None:
    if not section.enabled:
        return None
    last = _parse_time(section.last_attempt_at)
    if last is None:
        return now
    interval = RETRY_SECONDS if section.last_error else WEEK_SECONDS
    return last + timedelta(seconds=interval)


def is_due(section: PluginUpdatesSection, now: datetime) -> bool:
    if not section.enabled:
        return False
    next_at = next_attempt_at(section, now)
    return next_at is not None and next_at <= now


def _clip(value: str) -> str:
    return value[:MAX_MESSAGE_CHARS]


def _summary(result: plugins.AutoUpdateResult) -> str:
    parts = ["已更新集市"]
    if result.updated:
        parts.append(f"更新 {len(result.updated)} 个插件")
    else:
        parts.append("没有已安装插件")
    if result.failed:
        names = "、".join(
            plugin_id.split("@", 1)[0] for plugin_id, _ in result.failed[:3]
        )
        suffix = "等" if len(result.failed) > 3 else ""
        parts.append(f"{len(result.failed)} 个失败（{names}{suffix}）")
    return _clip("，".join(parts))


def _error(result: plugins.AutoUpdateResult) -> str:
    if not result.failed:
        return ""
    plugin_id, message = result.failed[0]
    return _clip(f"{plugin_id}: {message}")


class PluginAutoUpdateRuntime:
    def __init__(
        self,
        *,
        store: Any,
        tick_seconds: int | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.tick_seconds = max(
            60,
            tick_seconds
            if tick_seconds is not None
            else settings.plugin_auto_update_tick_seconds,
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(
            self._loop(), name="plugin-auto-update"
        )

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def run_once(self) -> None:
        section = (await self.store.get()).plugin_updates
        now = self._now()
        if not is_due(section, now):
            return
        try:
            result = await plugins.auto_update_installed()
        except plugins.PluginBusyError:
            logger.info(
                "plugin auto update skipped: another plugin command is running"
            )
            return
        except plugins.PluginError as exc:
            await self.store.update_plugin_updates(
                last_attempt_at=now.isoformat(),
                last_summary="",
                last_error=_clip(str(exc)),
            )
            logger.warning("plugin auto update failed: %s", exc)
            return
        fields: dict[str, str] = {
            "last_attempt_at": now.isoformat(),
            "last_summary": _summary(result),
            "last_error": _error(result),
        }
        if not result.failed:
            fields["last_success_at"] = now.isoformat()
        await self.store.update_plugin_updates(**fields)
        logger.info(
            "plugin auto update finished: %s", fields["last_summary"]
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("plugin auto update tick failed")
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.tick_seconds
                )
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_plugin_updates.py -v
```

Expected: PASS。

- [ ] **Step 5: 接入配置与 lifespan**

在 `apps/api/app/config.py` 的 `cron_default_timezone` 之后新增：

```python
    # Plugin auto-update runs inside the API process. A weekly due check does
    # not need a fast tick; one hour bounds how late a run can start.
    plugin_auto_update_tick_seconds: int = 3600
```

在 `apps/api/app/main.py` 顶部导入：

```python
from app.services.plugin_updates import PluginAutoUpdateRuntime
```

在 lifespan 中 `await knowledge.start()` 之后新增：

```python
                plugin_updates = PluginAutoUpdateRuntime(store=store)
                app.state.plugin_updates = plugin_updates
                await plugin_updates.start()
```

在 finally 中 `knowledge_sync shutdown failed` 的 `try/except` 之后新增：

```python
                    plugin_updates = getattr(
                        app.state, "plugin_updates", None
                    )
                    if plugin_updates is not None:
                        try:
                            await plugin_updates.stop()
                        except Exception:
                            logger.exception(
                                "plugin auto update shutdown failed"
                            )
```

- [ ] **Step 6: 更新启动测试**

在 `apps/api/app/tests/test_app_boot.py` 顶部导入：

```python
from app.services.plugin_updates import PluginAutoUpdateRuntime
```

在 `quiet_background_workers` 的 `worker_class` 元组中加入 `PluginAutoUpdateRuntime`：

```python
    for worker_class in (
        WeixinRuntime,
        CronRuntime,
        KnowledgeSyncRuntime,
        BacktestWorker,
        PluginAutoUpdateRuntime,
    ):
        monkeypatch.setattr(worker_class, "start", noop)
```

在 `test_worker_app_wires_runtime_state` 的断言中增加：

```python
        assert state.plugin_updates is not None
```

- [ ] **Step 7: 运行相关测试**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_plugin_updates.py apps/api/app/tests/test_app_boot.py -v
```

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add apps/api/app/services/plugin_updates.py apps/api/app/config.py apps/api/app/main.py apps/api/app/tests/test_plugin_updates.py apps/api/app/tests/test_app_boot.py
git commit -m "Add the weekly plugin auto-update runtime

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: 自动更新 API

**Files:**
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/routers/plugins.py`
- Test: `apps/api/app/tests/test_plugins_api.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_plugins_api.py` 顶部加入：

```python
import json
```

在文件末尾追加：

```python
def test_auto_update_defaults(client):
    response = client.get("/api/plugins/auto-update")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "interval_days": 7,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_summary": "",
        "last_error": "",
        "next_attempt_at": None,
    }


def test_auto_update_enable_requires_official_marketplace(
    client, monkeypatch
):
    async def empty_catalog(home=None):
        return plugins.PluginCatalog(cli_available=True)

    monkeypatch.setattr(plugins, "catalog", empty_catalog)

    response = client.put("/api/plugins/auto-update", json={"enabled": True})

    assert response.status_code == 409
    assert client.get("/api/plugins/auto-update").json()["enabled"] is False


def test_auto_update_toggle_persists(client, monkeypatch):
    async def fake_catalog(home=None):
        return _catalog()

    monkeypatch.setattr(plugins, "catalog", fake_catalog)

    response = client.put("/api/plugins/auto-update", json={"enabled": True})

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["next_attempt_at"] is None
    data = json.loads(
        client.app.state.settings_store.path.read_text("utf-8")
    )
    assert data["plugin_updates"]["enabled"] is True

    response = client.put(
        "/api/plugins/auto-update", json={"enabled": False}
    )
    assert response.json()["enabled"] is False
    assert response.json()["next_attempt_at"] is None


def test_auto_update_reports_next_attempt_after_success(client):
    path = client.app.state.settings_store.path
    data = json.loads(path.read_text("utf-8"))
    data["plugin_updates"] = {
        "enabled": True,
        "last_attempt_at": "2999-01-01T00:00:00+00:00",
        "last_success_at": "2999-01-01T00:00:00+00:00",
        "last_summary": "已更新集市",
        "last_error": "",
    }
    path.write_text(json.dumps(data), "utf-8")

    body = client.get("/api/plugins/auto-update").json()

    assert body["enabled"] is True
    assert body["next_attempt_at"] == "2999-01-08T00:00:00+00:00"


def test_auto_update_requires_auth():
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert (
            anonymous.get("/api/plugins/auto-update").status_code == 401
        )
        assert (
            anonymous.put(
                "/api/plugins/auto-update", json={"enabled": False}
            ).status_code
            == 401
        )
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_plugins_api.py -v
```

Expected: FAIL，接口不存在（404）。

- [ ] **Step 3: 增加响应模型**

在 `apps/api/app/schemas.py` 的 `PluginsCatalogOut` 之后新增：

```python
class PluginAutoUpdateUpdate(BaseModel):
    enabled: bool


class PluginAutoUpdateOut(BaseModel):
    enabled: bool
    interval_days: int
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_summary: str = ""
    last_error: str = ""
    next_attempt_at: str | None = None
```

- [ ] **Step 4: 增加路由**

在 `apps/api/app/routers/plugins.py` 顶部补导入：

```python
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.schemas import (
    AvailablePluginOut,
    InstalledPluginOut,
    PluginAutoUpdateOut,
    PluginAutoUpdateUpdate,
    PluginInstallRequest,
    PluginMarketplaceOut,
    PluginsCatalogOut,
)
from app.services import plugin_updates
from app.services.settings_store import (
    PluginUpdatesSection,
    get_settings_store,
)
```

在 `router` 定义之后新增：

```python
AUTO_UPDATE_INTERVAL_DAYS = 7


def _store(request: Request):
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _auto_update_out(
    section: PluginUpdatesSection,
) -> PluginAutoUpdateOut:
    now = datetime.now(timezone.utc)
    next_at = plugin_updates.next_attempt_at(section, now)
    if next_at is not None and next_at <= now:
        next_at = None
    return PluginAutoUpdateOut(
        enabled=section.enabled,
        interval_days=AUTO_UPDATE_INTERVAL_DAYS,
        last_attempt_at=section.last_attempt_at or None,
        last_success_at=section.last_success_at or None,
        last_summary=section.last_summary,
        last_error=section.last_error,
        next_attempt_at=next_at.isoformat() if next_at else None,
    )
```

在 `list_plugins()` 之后新增：

```python
@router.get("/auto-update", response_model=PluginAutoUpdateOut)
async def get_auto_update(request: Request) -> PluginAutoUpdateOut:
    section = (await _store(request).get()).plugin_updates
    return _auto_update_out(section)


@router.put("/auto-update", response_model=PluginAutoUpdateOut)
async def set_auto_update(
    payload: PluginAutoUpdateUpdate, request: Request
) -> PluginAutoUpdateOut:
    store = _store(request)
    if payload.enabled:
        try:
            catalog_value = await plugin_service.catalog()
        except PluginError as exc:
            raise _http_error(exc) from exc
        if not catalog_value.cli_available:
            raise HTTPException(
                status_code=503, detail="未找到 Claude Code CLI"
            )
        if not catalog_value.marketplaces:
            raise HTTPException(
                status_code=409, detail="请先添加官方集市"
            )
    await store.update_plugin_updates(enabled=payload.enabled)
    runtime = getattr(request.app.state, "plugin_updates", None)
    if runtime is not None and payload.enabled:
        runtime.wake()
    section = (await store.get()).plugin_updates
    return _auto_update_out(section)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_plugins_api.py -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/plugins.py apps/api/app/tests/test_plugins_api.py
git commit -m "Expose the plugin auto-update settings API

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6: 插件页自动更新卡片

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/components/PluginsPage.tsx`

- [ ] **Step 1: 增加前端类型**

在 `apps/web/src/types.ts` 的 `PluginsCatalog` 之后新增：

```typescript
export type PluginAutoUpdate = {
  enabled: boolean;
  interval_days: number;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_summary: string;
  last_error: string;
  next_attempt_at: string | null;
};
```

- [ ] **Step 2: 增加 API 方法**

在 `apps/web/src/api.ts` 的类型导入里加入 `PluginAutoUpdate`，并在 `listPlugins` 之后新增：

```typescript
  getPluginAutoUpdate: () =>
    request<PluginAutoUpdate>("/api/plugins/auto-update"),
  updatePluginAutoUpdate: (enabled: boolean) =>
    request<PluginAutoUpdate>("/api/plugins/auto-update", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
```

- [ ] **Step 3: 更新插件页**

把 `apps/web/src/components/PluginsPage.tsx` 顶部导入改为：

```tsx
import { useEffect, useMemo, useState } from "react";

import { api, errorText } from "../api";
import { formatTime } from "../format";
import { peekPageCache, storePageCache } from "../pageCache";
import type { PluginAutoUpdate, PluginsCatalog } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
```

在 `PluginsPage` 组件内新增状态：

```tsx
  const [autoUpdate, setAutoUpdate] = useState<PluginAutoUpdate | null>(
    null,
  );
  const [autoUpdateError, setAutoUpdateError] = useState("");
  const [autoSaving, setAutoSaving] = useState(false);
```

在 `load()` 之后新增：

```tsx
  async function loadAutoUpdate() {
    setAutoUpdateError("");
    try {
      setAutoUpdate(await api.getPluginAutoUpdate());
    } catch (exc) {
      setAutoUpdate(null);
      setAutoUpdateError(errorText(exc));
    }
  }

  async function toggleAutoUpdate(enabled: boolean) {
    setAutoSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await api.updatePluginAutoUpdate(enabled);
      setAutoUpdate(next);
      setNotice(enabled ? "已开启每周自动更新" : "已关闭每周自动更新");
    } catch (exc) {
      setError(errorText(exc));
      void loadAutoUpdate();
    } finally {
      setAutoSaving(false);
    }
  }
```

把首次加载的 `useEffect` 改为：

```tsx
  useEffect(() => {
    void load();
    void loadAutoUpdate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
```

把「官方集市」卡片里的刷新按钮改为同时刷新自动更新状态：

```tsx
                <button
                  className="ghost-button"
                  onClick={() => {
                    void load();
                    void loadAutoUpdate();
                  }}
                  disabled={loading || disabled}
                >
                  {loading ? "刷新中…" : "刷新"}
                </button>
```

把 `const disabled = busy !== "";` 改为：

```tsx
  const disabled = busy !== "" || autoSaving;
```

在「官方集市」卡片之后、「已安装」卡片之前插入：

```tsx
              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>自动更新</h2>
                    <p>
                      每 7 天刷新官方集市并更新已安装插件；更新在下一轮对话生效。
                    </p>
                  </div>
                </div>
                {autoUpdateError && (
                  <div className="hint skill-warning">{autoUpdateError}</div>
                )}
                {!autoUpdate && !autoUpdateError && (
                  <div className="empty-note">读取中…</div>
                )}
                {autoUpdate && (
                  <label className="toggle-row" htmlFor="plugin-auto-update">
                    <input
                      id="plugin-auto-update"
                      type="checkbox"
                      checked={autoUpdate.enabled}
                      disabled={disabled}
                      onChange={(event) =>
                        void toggleAutoUpdate(event.target.checked)
                      }
                    />
                    <span>
                      <strong>每周自动更新已安装插件</strong>
                      <small>
                        失败后 1 天重试；更新期间页面上的插件操作会暂时不可用。
                      </small>
                    </span>
                  </label>
                )}
                {autoUpdate &&
                  (autoUpdate.last_attempt_at || autoUpdate.last_error) && (
                    <div className="hint">
                      {autoUpdate.last_attempt_at
                        ? `上次自动更新：${formatTime(
                            autoUpdate.last_attempt_at,
                          )}${
                            autoUpdate.last_summary
                              ? ` · ${autoUpdate.last_summary}`
                              : ""
                          }`
                        : "还没有执行过自动更新。"}
                      {autoUpdate.enabled &&
                        (autoUpdate.next_attempt_at
                          ? ` · 下次：${formatTime(
                              autoUpdate.next_attempt_at,
                            )}`
                          : " · 将在下次检查时执行")}
                    </div>
                  )}
                {autoUpdate?.last_error && (
                  <div className="hint skill-warning">
                    上次失败：{autoUpdate.last_error}
                  </div>
                )}
              </section>
```

- [ ] **Step 4: 构建前端**

```bash
cd apps/web && npm run build
```

Expected: 构建成功，更新 `apps/web/dist`。

- [ ] **Step 5: 提交**

```bash
git add apps/web/src/types.ts apps/web/src/api.ts apps/web/src/components/PluginsPage.tsx apps/web/dist
git commit -m "Add the plugin auto-update card

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7: 文档同步、三次自我 review 与全量验证

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: 更新 `CLAUDE.md` 插件集市条目**

在「插件集市」段落末尾追加：

```markdown
插件页可开启「每周自动更新」（默认关闭）：进程内 `PluginAutoUpdateRuntime` 每小时检查一次，距上次尝试满 7 天时先刷新官方集市，再逐个更新官方集市下已安装插件（含停用插件）；单个插件失败不中断其余插件，有失败时 1 天后重试；结果记录在 `settings.json` 的 `plugin_updates` 节并在页面展示。更新与页面插件操作共用配置锁。平台不启用 Claude Code 自带的 marketplace auto-update（它只在交互式会话启动后执行，不是周调度）。
```

- [ ] **Step 2: 更新 `README.md` 插件集市小节**

在「插件集市」小节末尾追加：

```markdown
- 插件页可开启「每周自动更新」（默认关闭）：平台每小时检查一次，距上次尝试满 7 天时刷新官方集市并逐个更新已安装插件；失败后 1 天重试，结果与下次计划时间显示在插件页。该调度在平台进程内执行，不依赖 Claude Code 自带的会话级插件自动更新。
```

- [ ] **Step 3: 第一次自我 code review（正确性）**

```bash
BASE=$(git log --format=%H --grep='Record the weekly plugin auto-update requirements' -1)^
git diff "$BASE" -- apps/api apps/web/src CLAUDE.md README.md docs/intent.md
```

逐项检查并记录：

- 到期计算是否正确覆盖「从未执行」「成功后 7 天」「失败后 1 天」「时间戳损坏」。
- 失败路径是否保留 `last_success_at`，成功路径是否清除 `last_error`。
- 单个插件失败是否继续更新其余插件。
- 配置锁被占用时是否没有写入任何状态。
- 更新成功后是否写 `.plugins-revision`。

发现问题立即修复并重跑相关测试。

- [ ] **Step 4: 第二次自我 code review（并发与边界）**

逐项检查并记录：

- `auto_update_installed()` 是否整段持有 `_serialized()` 锁，且没有嵌套获取同一把锁。
- runtime 停止时 `CancelledError` 是否向上传播，不会被 `except Exception` 吞掉。
- 关闭开关后 `next_attempt_at` 是否为 `None` 且历史记录保留。
- 没有官方集市时开启是否返回 409，移除集市后运行时是否按失败记录并每日重试。
- `last_summary` / `last_error` 是否始终不超过 500 字符。
- 前后端字段名、大小写与可空性是否一致。

发现问题立即修复并重跑相关测试。

- [ ] **Step 5: 第三次自我 code review（接口与文档一致性）**

逐项检查并记录：

- `GET/PUT /api/plugins/auto-update` 的响应字段与 `types.ts`、插件页渲染一致。
- 页面在没有官方集市时不显示自动更新卡片；读取失败时有可见错误。
- `docs/intent.md` F25、设计文档、`CLAUDE.md`、`README.md` 的描述与实现一致。
- 默认关闭、固定 7 天、失败 1 天重试三点在文档与代码中一致。

发现问题立即修复并重跑相关测试。

- [ ] **Step 6: 运行全量后端测试**

```bash
.venv/bin/python -m pytest apps/api/app/tests
```

Expected: 全部通过。

- [ ] **Step 7: 重新构建前端**

```bash
cd apps/web && npm run build
```

Expected: 构建成功，`apps/web/dist` 为最新产物。

- [ ] **Step 8: 提交文档与最终产物**

```bash
git add CLAUDE.md README.md docs apps/api apps/web/dist
git status --short
git commit -m "Document the weekly plugin auto-update

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

`git status --short` 应只包含本次改动；不要提交 `.super-personal-platform/`、`.env`、`data/`。

---

## Self-Review

**Spec coverage（设计文档 → 任务）：**

- `plugin_updates` 配置节 → Task 2。
- 批量刷新集市并逐个更新插件 → Task 3。
- 7 天周期、失败 1 天重试、锁冲突跳过、启动接入 → Task 4。
- 开关与状态 API、开启前校验官方集市 → Task 5。
- 插件页开关与状态展示 → Task 6。
- 文档同步、三次 review、全量验证 → Task 7。
- 不启用 Claude 自带 auto-update → 设计文档与文档同步步骤中明确记录，无代码开关需要实现。

**Placeholder scan：** 无 TBD/TODO；所有步骤包含实际代码、命令与预期结果。三次 review 使用同一个 `BASE` 表达式定位本次改动的起始提交，不依赖固定提交数量。

**Type consistency：** `PluginUpdatesSection`（settings_store）→ `PluginAutoUpdateOut`（schemas）→ `PluginAutoUpdate`（types.ts）字段一致；`AutoUpdateResult.updated/failed` 在服务、runtime、测试中命名一致；`next_attempt_at()` 在 runtime 与 router 中共用；router 对「已到期」返回 `null`，页面据此显示「将在下次检查时执行」。

**已知取舍：** 批量更新期间持有插件锁，页面插件操作可能返回 409；这是保证不会边更新边卸载的必要代价，已在设计文档与 intent 中记录。自动更新默认关闭，避免无人值守时替换 hooks/MCP 代码。
