# 会话级后台任务事件通道实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 turn 结束后仍在运行的后台任务通过会话级 SSE 实时显示，支持快照恢复、10 分钟保活，并在 client 逐出或服务重启时准确标记「已中断」。

**Architecture:** 扩展 `running_sessions` 为「turn 租约 + 任务快照」两用表（`has_turn` / `tasks_json` / `updated_at`）；新增进程内 `SessionTaskService` 负责事件合并、广播、防抖写库、保活和清理；runner 在读取 SDK 消息时同步发布任务事件；新增 `GET /api/sessions/{id}/tasks` 快照和 `GET /api/sessions/{id}/events` SSE；前端新增输入框上方的会话级任务区，移除每轮「执行过程」里的任务列表。

**Tech Stack:** FastAPI（Python 3.12）、SQLAlchemy + SQLite、Claude Agent SDK（任务消息与终态集合）、React 18 + TypeScript + Vite、pytest、`asyncio`。

**依据设计：** `docs/superpowers/specs/2026-11-16-session-task-channel-design.md`（已提交 `6980695`）

## 执行前

这个 checkout 可能被并行会话共用。执行本计划前：

1. 按 `superpowers:using-git-worktrees` 建立独立 worktree，或确认 `git status --short` 干净后再开始。
2. 不要提交与 F26 无关的改动；`running_sessions`、`claude_runner.py`、`sessions.py` 都是高风险共享文件。
3. 本计划涉及 schema 变更（给 `running_sessions` 加列）。迁移必须先于任何依赖新列的读取执行。

## 文件结构

### 后端

- `apps/api/app/models.py`：`RunningSession` 增加 `has_turn`、`tasks_json`、`updated_at`。
- `apps/api/app/db.py`：`_ensure_running_session_columns()` 迁移。
- `apps/api/app/services/turn_lease.py`：`add_in`/`add` 标记 `has_turn=1`；新增 `end_turn()`、`list_turn_leases()`；`is_running()` 只看 `has_turn`。
- `apps/api/app/services/session_tasks.py`（新建）：`SessionTaskService`、任务合并、快照、订阅、保活、清理。
- `apps/api/app/services/claude_runner.py`：构造参数、发布任务事件、保活跳过、逐出标记中断、turn 结束 flush + `end_turn`。
- `apps/api/app/services/recovery.py`：只恢复 `has_turn=1` 的行。
- `apps/api/app/routers/sessions.py`：`GET /tasks`、`GET /events`，删除会话时 `forget`。
- `apps/api/app/schemas.py`：`SessionTaskOut`、`SessionTasksOut`。
- `apps/api/app/main.py`：创建/启动/停止 `SessionTaskService`，传给 runner。
- 测试：`test_turn_lease.py`、`test_session_tasks.py`、`test_claude_runner.py`、`test_recovery.py`、`test_session_tasks_api.py`。

### 前端

- `apps/web/src/types.ts`：`SessionTask`、`SessionTaskEvent`、`LiveTask` 时间字段、`FrozenLive` 移除 tasks。
- `apps/web/src/api.ts`：`sessionTasksUrl`、`sessionEventsUrl`。
- `apps/web/src/taskFormat.ts`（新建）：状态标签/颜色、snake→camel 映射、事件合并、排序。
- `apps/web/src/App.tsx`：`tasksBySession` 状态、会话 SSE、任务事件处理、移除 `live.tasks`。
- `apps/web/src/components/SessionTaskPanel.tsx`（新建）：输入框上方任务区。
- `apps/web/src/components/ChatPanel.tsx`：接收并渲染任务区；移除 MessageList 的 tasks 参数。
- `apps/web/src/components/MessageList.tsx`：移除 `LiveProcess` 的任务列表和 tasks 参数。
- `apps/web/src/styles.css`：任务区样式。

### 文档

- `docs/intent.md`：F26，编号水位更新。
- `CLAUDE.md`：`running_sessions` 两用语义、任务区、保活/重启中断。
- `README.md`：聊天运行时说明与验收步骤。
- 设计文档：`docs/superpowers/specs/2026-11-16-session-task-channel-design.md`（已提交）。

---

## Task 1: F26 需求记录

**Files:**
- Modify: `docs/intent.md`

- [ ] **Step 1: 更新 `docs/intent.md` 头部**

把：

```markdown
- 最后更新：2026-11-16
- 编号水位：F25
```

改为：

```markdown
- 最后更新：2026-11-16
- 编号水位：F26
```

- [ ] **Step 2: 在 `docs/intent.md` 末尾追加 F26**

```markdown
### F26 会话级后台任务事件

- 状态：现行
- 日期：2026-11-16
- 来源：2026-11-16 会话级后台任务事件通道设计

#### 功能点

1. turn 结束后仍在运行的后台任务，其 `task_started` / `task_progress` / `task_updated` / `task_notification` 通过会话级 SSE 实时送到前端，不再依赖下一轮读取。
2. 聊天页新增「后台任务」区域，位于输入框上方，跨 turn 保留；运行中优先展示，完成后保留 10 分钟。
3. `running_sessions` 扩展为两用：`has_turn=1` 表示有 turn 在跑，启动恢复只处理这种行；`has_turn=0` 表示只有后台任务或已完成任务保留。
4. 任务状态保存到 `running_sessions.tasks_json`；页面刷新或断线重连用 `GET /api/sessions/{id}/tasks` / SSE 首帧快照恢复。
5. 有运行中任务时 client 不按 5 分钟空闲 TTL 逐出；每个任务从开始时间起最多保活 10 分钟，超过或被逐出时标记「已中断」。
6. 服务重启时所有非终态任务标记「已中断」并保留 10 分钟；`has_turn=1` 的 turn 仍按现有恢复流程续跑。
7. 每轮「执行过程」不再显示任务列表，任务只在会话级任务区出现；子 Agent、思考、工具调用仍在「执行过程」。
8. 不把后台任务写入 transcript；历史刷新不恢复任务；不提供任务取消或跨会话总览。

#### 验收标准

- 启动一个超过当前 turn 的后台任务：turn 结束后任务区继续显示「运行中」，完成/失败/停止时实时更新。
- 刷新页面或断线重连后运行中任务恢复；已完成任务 10 分钟内可见，之后消失。
- 服务重启：运行中任务显示「已中断」10 分钟；`has_turn=1` 的 turn 正常续跑。
- 有运行中任务的 client 不被 5 分钟空闲 TTL 逐出；最多保活 10 分钟；逐出后任务标记「已中断」。
- 每轮「执行过程」不再出现任务列表；会话级任务区无重复渲染。
- 后端测试与前端构建通过。
```

- [ ] **Step 3: 提交**

```bash
git add docs/intent.md
git commit -m "Record the session-level background task requirements

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: `running_sessions` 列与租约生命周期

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/db.py`
- Modify: `apps/api/app/services/turn_lease.py`
- Modify: `apps/api/app/services/recovery.py`
- Test: `apps/api/app/tests/test_turn_lease.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_turn_lease.py`：

```python
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import SessionLocal, _ensure_running_session_columns, init_db
from app.models import RunningSession
from app.services import turn_lease

SESSION_ID = "11111111-1111-1111-1111-111111111111"


def _task(task_id: str, status: str = "running") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "task_id": task_id,
        "tool_use_id": None,
        "description": task_id,
        "status": status,
        "summary": "",
        "last_tool_name": "",
        "usage": None,
        "started_at": now,
        "updated_at": now,
        "completed_at": now if status != "running" else None,
    }


@pytest.mark.asyncio
async def test_begin_turn_and_end_turn_keep_task_row():
    await init_db()
    await turn_lease.add(SESSION_ID)
    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        row.tasks_json = json.dumps([_task("t1")])
        await db.commit()

    await turn_lease.end_turn(SESSION_ID)

    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        assert row is not None
        assert row.has_turn is False
        assert json.loads(row.tasks_json)[0]["task_id"] == "t1"
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_end_turn_deletes_empty_row():
    await init_db()
    await turn_lease.add(SESSION_ID)

    await turn_lease.end_turn(SESSION_ID)

    async with SessionLocal() as db:
        assert await db.get(RunningSession, SESSION_ID) is None


@pytest.mark.asyncio
async def test_list_turn_leases_ignores_task_only_rows():
    await init_db()
    other = "22222222-2222-2222-2222-222222222222"
    await turn_lease.add(SESSION_ID)
    async with SessionLocal() as db:
        db.add(
            RunningSession(
                session_id=other,
                has_turn=False,
                tasks_json=json.dumps([_task("t2")]),
            )
        )
        await db.commit()

    assert await turn_lease.list_turn_leases() == [SESSION_ID]
    assert await turn_lease.is_running(SESSION_ID) is True
    assert await turn_lease.is_running(other) is False
    await turn_lease.remove(SESSION_ID)
    await turn_lease.remove(other)


@pytest.mark.asyncio
async def test_begin_turn_reactivates_task_row():
    await init_db()
    await turn_lease.add(SESSION_ID)
    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        row.has_turn = False
        row.tasks_json = json.dumps([_task("t1")])
        await db.commit()

    await turn_lease.add(SESSION_ID)

    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        assert row.has_turn is True
        assert json.loads(row.tasks_json)[0]["task_id"] == "t1"
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_ensure_running_session_columns_migrates_old_table(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'old.db'}", future=True
    )
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE running_sessions (session_id VARCHAR(36) PRIMARY KEY)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO running_sessions (session_id) VALUES ('old-session')"
        )
        await _ensure_running_session_columns(conn)
        result = await conn.exec_driver_sql("PRAGMA table_info(running_sessions)")
        columns = {row[1] for row in result.fetchall()}
        assert {"has_turn", "tasks_json", "updated_at"} <= columns
        result = await conn.exec_driver_sql(
            "SELECT has_turn, tasks_json FROM running_sessions WHERE session_id = 'old-session'"
        )
        row = result.fetchone()
        assert row[0] == 1
        assert row[1] == "[]"
    await engine.dispose()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_turn_lease.py -v
```

Expected: FAIL，`RunningSession.has_turn` / `end_turn` / `list_turn_leases` / `_ensure_running_session_columns` 不存在。

- [ ] **Step 3: 扩展模型**

在 `apps/api/app/models.py` 中把 `RunningSession` 改为：

```python
class RunningSession(Base):
    """Per-session row for an active turn or retained background tasks.

    ``has_turn`` distinguishes a real turn lease from a task-only row;
    startup recovery must only consume ``has_turn=True`` rows.
    """

    __tablename__ = "running_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    has_turn: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tasks_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
```

- [ ] **Step 4: 增加 SQLite 迁移**

在 `apps/api/app/db.py` 中，`_drop_stale_channel_inbounds` 之后新增：

```python
async def _ensure_running_session_columns(conn) -> None:  # noqa: ANN001
    """Add the task columns to a pre-F26 running_sessions table."""
    result = await conn.exec_driver_sql("PRAGMA table_info(running_sessions)")
    columns = {row[1] for row in result.fetchall()}
    if not columns:
        return
    if "has_turn" not in columns:
        await conn.exec_driver_sql(
            "ALTER TABLE running_sessions "
            "ADD COLUMN has_turn INTEGER NOT NULL DEFAULT 1"
        )
    if "tasks_json" not in columns:
        await conn.exec_driver_sql(
            "ALTER TABLE running_sessions "
            "ADD COLUMN tasks_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "updated_at" not in columns:
        await conn.exec_driver_sql(
            "ALTER TABLE running_sessions ADD COLUMN updated_at DATETIME"
        )
        await conn.exec_driver_sql(
            "UPDATE running_sessions "
            "SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"
        )
```

在 `init_db()` 中调用：

```python
        if settings.app_database_url.startswith("sqlite"):
            await _drop_stale_channel_deliveries(conn)
            await _drop_stale_channel_inbounds(conn)
            await _ensure_running_session_columns(conn)
        await conn.run_sync(Base.metadata.create_all)
```

- [ ] **Step 5: 更新 `turn_lease.py`**

把 `add_in` 改为 upsert 并标记 `has_turn=True`：

```python
async def add_in(db: AsyncSession, session_id: str) -> None:
    """Insert or reactivate a turn lease inside the caller's transaction."""
    existing = await db.get(RunningSession, session_id)
    if existing is not None:
        existing.has_turn = True
        existing.updated_at = utcnow()
        await db.flush()
        return
    db.add(
        RunningSession(
            session_id=session_id,
            has_turn=True,
            tasks_json="[]",
            updated_at=utcnow(),
        )
    )
    await db.flush()
```

新增 `end_turn` 和 `list_turn_leases`，并把 `is_running` 改为只看 `has_turn`：

```python
async def end_turn(session_id: str) -> None:
    """Release the turn part of a row, keeping retained tasks.

    The runner calls this after the turn's task events have been flushed.
    Rows with no tasks are deleted exactly like the old lease behavior.
    """
    async with SessionLocal() as db:
        row = await db.get(RunningSession, session_id)
        if row is None:
            return
        row.has_turn = False
        row.updated_at = utcnow()
        try:
            tasks = json.loads(row.tasks_json or "[]")
        except ValueError:
            tasks = None
        if isinstance(tasks, list) and not tasks:
            await db.delete(row)
        await db.commit()


async def list_turn_leases() -> list[str]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(RunningSession.session_id).where(
                RunningSession.has_turn.is_(True)
            )
        )
        return list(rows)


async def is_running(session_id: str) -> bool:
    async with SessionLocal() as db:
        row = await db.get(RunningSession, session_id)
        return row is not None and bool(row.has_turn)
```

在 `turn_lease.py` 顶部导入 `json` 和 `utcnow`：

```python
import json

from app.models import RunningSession, utcnow
```

- [ ] **Step 6: 恢复只处理 turn 租约**

在 `apps/api/app/services/recovery.py` 中把：

```python
        session_ids = await turn_lease.list_all()
```

改为：

```python
        session_ids = await turn_lease.list_turn_leases()
```

- [ ] **Step 7: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_turn_lease.py apps/api/app/tests/test_recovery.py -v
```

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add apps/api/app/models.py apps/api/app/db.py apps/api/app/services/turn_lease.py apps/api/app/services/recovery.py apps/api/app/tests/test_turn_lease.py
git commit -m "Store background task state on running_sessions

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: `SessionTaskService`

**Files:**
- Create: `apps/api/app/services/session_tasks.py`
- Test: `apps/api/app/tests/test_session_tasks.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_session_tasks.py`：

```python
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SessionLocal, init_db
from app.models import RunningSession
from app.services import turn_lease
from app.services.session_tasks import SessionTaskService

SESSION_ID = "33333333-3333-3333-3333-333333333333"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _service(now=None, retention_seconds=600):
    return SessionTaskService(
        now=now or (lambda: NOW),
        retention_seconds=retention_seconds,
        cleanup_interval_seconds=3600,
    )


def _started(task_id="t1"):
    return {
        "type": "task_started",
        "task_id": task_id,
        "tool_use_id": "toolu_1",
        "description": "回测 BTC 1h",
    }


@pytest.mark.asyncio
async def test_publish_merges_lifecycle_into_snapshot():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()

    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {
            "type": "task_progress",
            "task_id": "t1",
            "description": "回测进行中",
            "last_tool_name": "Bash",
            "usage": {"tool_uses": 2},
        },
    )
    await service.publish(
        SESSION_ID,
        {
            "type": "task_notification",
            "task_id": "t1",
            "status": "completed",
            "summary": "完成",
            "usage": {"tool_uses": 3},
        },
    )

    snapshot = await service.snapshot(SESSION_ID)
    assert snapshot["has_turn"] is True
    assert len(snapshot["tasks"]) == 1
    task = snapshot["tasks"][0]
    assert task["task_id"] == "t1"
    assert task["status"] == "completed"
    assert task["summary"] == "完成"
    assert task["last_tool_name"] == "Bash"
    assert task["usage"] == {"tool_uses": 3}
    assert task["completed_at"] is not None
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_task_updated_terminal_without_notification():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()

    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {"type": "task_updated", "task_id": "t1", "status": "killed"},
    )

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "killed"
    assert task["completed_at"] is not None
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_mark_interrupted_updates_running_tasks():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())

    await service.mark_interrupted(SESSION_ID, "SDK client 已断开")

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "interrupted"
    assert task["summary"] == "SDK client 已断开"
    assert task["completed_at"] is not None
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_keep_alive_uses_task_start_not_progress():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {"type": "task_progress", "task_id": "t1", "description": "still"},
    )

    deadline = await service.keep_alive_until(SESSION_ID)

    assert deadline == NOW + timedelta(seconds=600)
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_cleanup_removes_completed_tasks_after_retention():
    await init_db()
    now = [NOW]
    service = _service(now=lambda: now[0], retention_seconds=10)
    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {
            "type": "task_notification",
            "task_id": "t1",
            "status": "completed",
            "summary": "done",
        },
    )
    now[0] = NOW + timedelta(seconds=11)

    await service.cleanup_once()

    assert (await service.snapshot(SESSION_ID))["tasks"] == []
    async with SessionLocal() as db:
        assert await db.get(RunningSession, SESSION_ID) is None


@pytest.mark.asyncio
async def test_startup_marks_running_tasks_interrupted():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())

    await service.startup()

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "interrupted"
    assert task["summary"] == "服务重启，任务已中断"
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_subscribe_receives_published_events():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    queue = service.subscribe(SESSION_ID)

    await service.publish(SESSION_ID, _started())

    event = await queue.get()
    assert event["type"] == "task_started"
    assert event["task_id"] == "t1"
    service.unsubscribe(SESSION_ID, queue)
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_cleanup_marks_long_running_tasks_interrupted():
    await init_db()
    await turn_lease.add(SESSION_ID)
    now = [NOW]
    service = _service(now=lambda: now[0], retention_seconds=10)
    await service.publish(SESSION_ID, _started())
    now[0] = NOW + timedelta(seconds=11)

    await service.cleanup_once()

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "interrupted"
    assert task["summary"] == "后台任务超过保活上限"
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_publish_creates_task_only_row():
    await init_db()
    service = _service()

    await service.publish(SESSION_ID, _started())

    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        assert row is not None
        assert row.has_turn is False
        assert json.loads(row.tasks_json)[0]["task_id"] == "t1"
    await turn_lease.remove(SESSION_ID)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_session_tasks.py -v
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现 `SessionTaskService`**

创建 `apps/api/app/services/session_tasks.py`：

```python
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from claude_agent_sdk.types import TERMINAL_TASK_STATUSES
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import RunningSession

logger = logging.getLogger(__name__)

RETENTION_SECONDS = 10 * 60
CLEANUP_INTERVAL_SECONDS = 60.0
MAX_TASKS = 32
MAX_TASKS_JSON_BYTES = 64 * 1024
TERMINAL_STATUSES = frozenset(TERMINAL_TASK_STATUSES) | {"interrupted"}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_tasks(raw: str) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return {}
    if not isinstance(data, list):
        return {}
    tasks: dict[str, dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "")
        if task_id:
            tasks[task_id] = dict(item)
    return tasks


def _dump_tasks(tasks: dict[str, dict[str, Any]]) -> str:
    return json.dumps(
        list(tasks.values()), ensure_ascii=False, separators=(",", ":")
    )


def _is_terminal(task: dict[str, Any]) -> bool:
    return str(task.get("status") or "running") in TERMINAL_STATUSES


def _task_time(task: dict[str, Any], key: str) -> datetime | None:
    return _parse_iso(task.get(key))


class SessionTaskService:
    """Session-level task snapshot plus live event hub.

    Task state is authoritative in memory for active sessions and persisted to
    ``running_sessions.tasks_json`` with a short debounce. Subscribers receive
    live events; reconnecting clients get a fresh snapshot from the API.
    """

    def __init__(
        self,
        *,
        retention_seconds: int = RETENTION_SECONDS,
        cleanup_interval_seconds: float = CLEANUP_INTERVAL_SECONDS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.retention = timedelta(seconds=retention_seconds)
        self.cleanup_interval = cleanup_interval_seconds
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._live: dict[str, dict[str, dict[str, Any]]] = {}
        self._subscribers: dict[
            str, set[asyncio.Queue[dict[str, Any] | None]]
        ] = {}
        self._dirty: set[str] = set()
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._writer: asyncio.Task[None] | None = None
        self._cleaner: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._writer is None:
            self._writer = asyncio.create_task(
                self._writer_loop(), name="session-task-writer"
            )
        if self._cleaner is None:
            self._cleaner = asyncio.create_task(
                self._cleaner_loop(), name="session-task-cleaner"
            )

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for task in (self._writer, self._cleaner):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        async with self._lock:
            for session_id, tasks in list(self._live.items()):
                if not tasks:
                    continue
                try:
                    await self._persist(session_id, tasks)
                except Exception:
                    logger.exception(
                        "session task flush failed session=%s", session_id
                    )
        for queues in list(self._subscribers.values()):
            for queue in list(queues):
                queue.put_nowait(None)

    async def startup(self) -> None:
        """Mark tasks left running by the previous process as interrupted."""
        async with self._lock:
            async with SessionLocal() as db:
                rows = list(await db.scalars(select(RunningSession)))
                now = self._now()
                now_iso = _iso(now)
                for row in rows:
                    tasks = _load_tasks(row.tasks_json)
                    changed = False
                    for task in tasks.values():
                        if _is_terminal(task):
                            continue
                        task["status"] = "interrupted"
                        task["summary"] = "服务重启，任务已中断"
                        task["updated_at"] = now_iso
                        task["completed_at"] = now_iso
                        changed = True
                    if changed:
                        row.tasks_json = _dump_tasks(tasks)
                        row.updated_at = now
                await db.commit()
            self._live.clear()

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        task_id = str(event.get("task_id") or "")
        kind = str(event.get("type") or "")
        if not task_id or kind not in (
            "task_started",
            "task_progress",
            "task_updated",
            "task_notification",
        ):
            return
        async with self._lock:
            tasks = self._live.get(session_id)
            if tasks is None:
                tasks = await self._row_tasks(session_id)
                self._live[session_id] = tasks
            now = self._now()
            now_iso = _iso(now)
            task = tasks.get(task_id)
            if task is None:
                task = {
                    "task_id": task_id,
                    "tool_use_id": event.get("tool_use_id"),
                    "description": "",
                    "status": "running",
                    "summary": "",
                    "last_tool_name": "",
                    "usage": None,
                    "started_at": now_iso,
                    "updated_at": now_iso,
                    "completed_at": None,
                }
                tasks[task_id] = task
            if kind == "task_progress" and _is_terminal(task):
                return
            if kind == "task_started":
                task["tool_use_id"] = event.get("tool_use_id") or task.get(
                    "tool_use_id"
                )
                task["description"] = str(
                    event.get("description") or task.get("description") or ""
                )
                task["status"] = "running"
                task["completed_at"] = None
                task["summary"] = ""
                task.setdefault("started_at", now_iso)
            elif kind == "task_progress":
                task["description"] = str(
                    event.get("description") or task.get("description") or ""
                )
                task["last_tool_name"] = str(
                    event.get("last_tool_name")
                    or task.get("last_tool_name")
                    or ""
                )
                if event.get("usage") is not None:
                    task["usage"] = event["usage"]
            elif kind == "task_updated":
                status = event.get("status")
                if status:
                    task["status"] = str(status)
                    if str(status) in TERMINAL_STATUSES:
                        task["completed_at"] = task.get("completed_at") or now_iso
            else:
                status = event.get("status")
                if status:
                    task["status"] = str(status)
                task["summary"] = str(
                    event.get("summary") or task.get("summary") or ""
                )
                if event.get("usage") is not None:
                    task["usage"] = event["usage"]
                if _is_terminal(task):
                    task["completed_at"] = task.get("completed_at") or now_iso
            task["updated_at"] = now_iso
            self._broadcast(session_id, event)
            self._trim(session_id, tasks)
            if kind == "task_progress":
                self._dirty.add(session_id)
                self._wake.set()
            else:
                await self._persist(session_id, tasks)
                self._dirty.discard(session_id)

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            tasks = self._live.get(session_id)
            async with SessionLocal() as db:
                row = await db.get(RunningSession, session_id)
            if tasks is None:
                tasks = _load_tasks(row.tasks_json) if row is not None else {}
                self._live[session_id] = tasks
            return {
                "has_turn": bool(row.has_turn) if row is not None else False,
                "updated_at": _iso(row.updated_at)
                if row is not None and row.updated_at
                else None,
                "tasks": [dict(task) for task in tasks.values()],
            }

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(
        self, session_id: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        subscribers = self._subscribers.get(session_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(session_id, None)

    async def mark_interrupted(self, session_id: str, reason: str) -> None:
        async with self._lock:
            tasks = self._live.get(session_id)
            if tasks is None:
                tasks = await self._row_tasks(session_id)
                self._live[session_id] = tasks
            now_iso = _iso(self._now())
            changed = False
            for task in tasks.values():
                if _is_terminal(task):
                    continue
                task["status"] = "interrupted"
                task["summary"] = reason
                task["updated_at"] = now_iso
                task["completed_at"] = now_iso
                changed = True
                self._broadcast(
                    session_id,
                    {
                        "type": "task_notification",
                        "task_id": task["task_id"],
                        "status": "interrupted",
                        "summary": reason,
                        "usage": task.get("usage"),
                    },
                )
            if changed:
                await self._persist(session_id, tasks)
                self._dirty.discard(session_id)

    async def keep_alive_until(self, session_id: str) -> datetime | None:
        async with self._lock:
            tasks = self._live.get(session_id)
            if tasks is None:
                tasks = await self._row_tasks(session_id)
                self._live[session_id] = tasks
            deadlines: list[datetime] = []
            for task in tasks.values():
                if _is_terminal(task):
                    continue
                started = _task_time(task, "started_at") or _task_time(
                    task, "updated_at"
                )
                if started is not None:
                    deadlines.append(started + self.retention)
            return max(deadlines) if deadlines else None

    async def flush(self, session_id: str) -> None:
        async with self._lock:
            tasks = self._live.get(session_id)
            if tasks is None:
                return
            await self._persist(session_id, tasks)
            self._dirty.discard(session_id)

    async def forget(self, session_id: str) -> None:
        async with self._lock:
            self._live.pop(session_id, None)
            self._dirty.discard(session_id)
            for queue in self._subscribers.pop(session_id, set()):
                queue.put_nowait(None)
            async with SessionLocal() as db:
                row = await db.get(RunningSession, session_id)
                if row is not None:
                    await db.delete(row)
                    await db.commit()

    async def cleanup_once(self) -> None:
        async with self._lock:
            now = self._now()
            now_iso = _iso(now)
            async with SessionLocal() as db:
                rows = list(await db.scalars(select(RunningSession)))
                for row in rows:
                    tasks = self._live.get(row.session_id)
                    if tasks is None:
                        tasks = _load_tasks(row.tasks_json)
                        self._live[row.session_id] = tasks
                    changed = False
                    removed: list[str] = []
                    for task_id, task in list(tasks.items()):
                        if _is_terminal(task):
                            completed = _task_time(task, "completed_at") or _task_time(
                                task, "updated_at"
                            )
                            if completed is None or now - completed >= self.retention:
                                tasks.pop(task_id, None)
                                removed.append(task_id)
                                changed = True
                        else:
                            started = _task_time(task, "started_at") or _task_time(
                                task, "updated_at"
                            )
                            if started is not None and now - started >= self.retention:
                                task["status"] = "interrupted"
                                task["summary"] = "后台任务超过保活上限"
                                task["updated_at"] = now_iso
                                task["completed_at"] = now_iso
                                changed = True
                                self._broadcast(
                                    row.session_id,
                                    {
                                        "type": "task_notification",
                                        "task_id": task_id,
                                        "status": "interrupted",
                                        "summary": "后台任务超过保活上限",
                                        "usage": task.get("usage"),
                                    },
                                )
                    if changed:
                        row.tasks_json = _dump_tasks(tasks)
                        row.updated_at = now
                    for task_id in removed:
                        self._broadcast(
                            row.session_id,
                            {"type": "task_removed", "task_id": task_id},
                        )
                    if not row.has_turn and not tasks:
                        await db.execute(
                            delete(RunningSession).where(
                                RunningSession.session_id == row.session_id,
                                RunningSession.has_turn.is_(False),
                            )
                        )
                        self._live.pop(row.session_id, None)
                await db.commit()

    async def _row_tasks(self, session_id: str) -> dict[str, dict[str, Any]]:
        async with SessionLocal() as db:
            row = await db.get(RunningSession, session_id)
        return _load_tasks(row.tasks_json) if row is not None else {}

    async def _persist(
        self, session_id: str, tasks: dict[str, dict[str, Any]]
    ) -> None:
        raw = _dump_tasks(tasks)
        now = self._now()
        async with SessionLocal() as db:
            row = await db.get(RunningSession, session_id)
            if row is None:
                row = RunningSession(
                    session_id=session_id,
                    has_turn=False,
                    tasks_json="[]",
                    updated_at=now,
                )
                db.add(row)
            row.tasks_json = raw
            row.updated_at = now
            await db.commit()

    def _broadcast(self, session_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(session_id, ())):
            queue.put_nowait(event)

    def _trim(
        self, session_id: str, tasks: dict[str, dict[str, Any]]
    ) -> None:
        removed: list[str] = []

        def drop(limit: int | None) -> None:
            terminal = sorted(
                (task for task in tasks.values() if _is_terminal(task)),
                key=lambda task: str(
                    task.get("completed_at") or task.get("updated_at") or ""
                ),
            )
            for task in terminal:
                if limit is not None and len(tasks) <= limit:
                    break
                if limit is None and len(_dump_tasks(tasks).encode("utf-8")) <= MAX_TASKS_JSON_BYTES:
                    break
                tasks.pop(str(task.get("task_id") or ""), None)
                removed.append(str(task.get("task_id") or ""))

        if len(tasks) > MAX_TASKS:
            drop(MAX_TASKS)
        if len(_dump_tasks(tasks).encode("utf-8")) > MAX_TASKS_JSON_BYTES:
            drop(None)
        for task_id in removed:
            self._broadcast(
                session_id, {"type": "task_removed", "task_id": task_id}
            )

    async def _writer_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            async with self._lock:
                dirty = list(self._dirty)
                self._dirty.clear()
                for session_id in dirty:
                    tasks = self._live.get(session_id)
                    if tasks is None:
                        continue
                    try:
                        await self._persist(session_id, tasks)
                    except Exception:
                        logger.exception(
                            "session task write failed session=%s", session_id
                        )
                        self._dirty.add(session_id)

    async def _cleaner_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("session task cleanup failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.cleanup_interval
                )
            except asyncio.TimeoutError:
                pass
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_session_tasks.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/session_tasks.py apps/api/app/tests/test_session_tasks.py
git commit -m "Add the session task service

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: Runner 接入与 main 生命周期

**Files:**
- Modify: `apps/api/app/services/claude_runner.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/app/tests/test_claude_runner.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_claude_runner.py` 末尾追加：

```python
class FakeTaskService:
    def __init__(self, *, keep_until=None):
        self.published = []
        self.interrupted = []
        self.flushed = []
        self.keep_until = keep_until

    async def publish(self, session_id, event):
        self.published.append((session_id, event))

    async def keep_alive_until(self, session_id):
        return self.keep_until

    async def mark_interrupted(self, session_id, reason):
        self.interrupted.append((session_id, reason))

    async def flush(self, session_id):
        self.flushed.append(session_id)


class TaskMessage:
    def __init__(self, task_id="t1"):
        self.subtype = "task_started"
        self.task_id = task_id
        self.tool_use_id = "toolu_1"
        self.description = "回测"

    def __getattr__(self, name):
        return None


@pytest.mark.asyncio
async def test_task_events_publish_to_session_service(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    client = FakeClient([TaskMessage(), ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="31313131-3131-3131-3131-313131313131"
    )

    await runner.run(turn, TurnStream())

    assert tasks.published == [(turn.session_id, {
        "type": "task_started",
        "task_id": "t1",
        "tool_use_id": "toolu_1",
        "description": "回测",
        "task_type": None,
    })]


@pytest.mark.asyncio
async def test_sweep_skips_client_with_active_background_task(tmp_path):
    await init_db()
    keep_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    tasks = FakeTaskService(keep_until=keep_until)
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        client_ttl_seconds=0,
        sweep_seconds=3600,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="32323232-3232-3232-3232-323232323232"
    )
    await runner.run(turn, TurnStream())
    await asyncio.sleep(0.01)

    assert await runner.sweep_idle_clients() == 0
    assert client.disconnected is False


@pytest.mark.asyncio
async def test_eviction_marks_background_tasks_interrupted(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="33333333-4444-4444-4444-444444444444"
    )
    await runner.run(turn, TurnStream())

    await runner.close(turn.session_id)

    assert tasks.flushed == [turn.session_id]
    assert tasks.interrupted[0][0] == turn.session_id
```

在文件顶部补导入：

```python
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py -k "session_service or active_background_task or background_tasks_interrupted" -v
```

Expected: FAIL，`session_tasks` 构造参数不存在，任务事件未发布。

- [ ] **Step 3: 修改构造参数**

在 `ClaudeTurnRunner.__init__` 签名中加入：

```python
        session_tasks: Any | None = None,
```

在方法体保存：

```python
        self._session_tasks = session_tasks
```

（`Any` 已在文件顶部导入。）

- [ ] **Step 4: 发布任务事件**

在 `run()` 的 `mapped_task` 分支中，把：

```python
                    if mapped_task is not None:
                        # Task lifecycle is a fact even when the surrounding
                        # notification turn is not shown in this chat.
                        stream.publish(mapped_task)
                        continue
```

改为：

```python
                    if mapped_task is not None:
                        # Task lifecycle is a fact even when the surrounding
                        # notification turn is not shown in this chat.
                        stream.publish(mapped_task)
                        if self._session_tasks is not None:
                            await self._session_tasks.publish(
                                turn.session_id, mapped_task
                            )
                        continue
```

- [ ] **Step 5: turn 结束 flush 并保留任务行**

在 `run()` 的 finally 中，把：

```python
            if lease_added and not keep_lease:
                try:
                    await turn_lease.remove(turn.session_id)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        logger.warning(
                            "turn lease removal cancelled session=%s", turn.session_id
                        )
                    else:
                        logger.warning(
                            "failed to remove turn lease session=%s",
                            turn.session_id,
                            exc_info=True,
                        )
```

改为（**flush 与 end_turn 必须分开 try**；flush 失败不能跳过 end_turn，否则已完成的 turn 会留下 has_turn=True 并在重启时被误恢复）：

```python
            if lease_added and not keep_lease:
                if self._session_tasks is not None:
                    try:
                        await self._session_tasks.flush(turn.session_id)
                    except BaseException as exc:
                        if isinstance(exc, asyncio.CancelledError):
                            logger.warning(
                                "task flush cancelled session=%s", turn.session_id
                            )
                        else:
                            logger.warning(
                                "failed to flush session tasks session=%s",
                                turn.session_id,
                                exc_info=True,
                            )
                try:
                    await turn_lease.end_turn(turn.session_id)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        logger.warning(
                            "turn lease end cancelled session=%s", turn.session_id
                        )
                    else:
                        logger.warning(
                            "failed to end turn lease session=%s",
                            turn.session_id,
                            exc_info=True,
                        )
```

- [ ] **Step 6: 逐出时标记中断**

在 `_evict_key()` 的 `await self._disconnect(cached.client, session_id)` 之后加入：

```python
        if self._session_tasks is not None:
            try:
                await self._session_tasks.mark_interrupted(
                    session_id, "SDK client 已断开"
                )
            except Exception:
                logger.exception(
                    "failed to mark background tasks interrupted session=%s",
                    session_id,
                )
```

在 `_disconnect_for_shutdown()` 的 `client.disconnect()` 结束后（`except` 分支之后）加入：

```python
        if self._session_tasks is not None:
            try:
                await self._session_tasks.mark_interrupted(
                    session_id, "服务关机，SDK client 已断开"
                )
            except Exception:
                logger.exception(
                    "failed to mark background tasks interrupted session=%s",
                    session_id,
                )
```

- [ ] **Step 7: 空闲清扫跳过活动任务 client**

在 `sweep_idle_clients()` 中 `for key, cached in expired:` 之前加入：

```python
        now = datetime.now(timezone.utc)
```

在循环体开头加入：

```python
            if self._session_tasks is not None:
                deadline = await self._session_tasks.keep_alive_until(key[1])
                if deadline is not None and deadline > now:
                    continue
```

文件顶部补导入：

```python
from datetime import datetime, timezone
```

- [ ] **Step 8: 容量淘汰优先跳过活动任务 client**

把 `_enforce_capacity()` 替换为：

```python
    async def _enforce_capacity(self) -> None:
        overflow = len(self._clients) - max(1, self._max_clients)
        if overflow <= 0:
            return
        idle = sorted(
            (
                (cached.last_used, key, cached)
                for key, cached in self._clients.items()
                if key not in self._active
            ),
            key=lambda item: item[0],
        )
        now = datetime.now(timezone.utc)
        skipped: list[tuple[float, ClientKey, _CachedClient]] = []
        for item in idle:
            if len(self._clients) <= self._max_clients:
                return
            _, key, cached = item
            if self._session_tasks is not None:
                deadline = await self._session_tasks.keep_alive_until(key[1])
                if deadline is not None and deadline > now:
                    skipped.append(item)
                    continue
            await self._evict_key(key, expected=cached)
        # Background tasks may pin clients past the normal cap; allow a small
        # grace, then evict the oldest pinned client and mark its tasks.
        for _, key, cached in skipped:
            if len(self._clients) <= self._max_clients + 2:
                return
            await self._evict_key(key, expected=cached)
```

- [ ] **Step 9: main 创建、启动、停止服务**

在 `apps/api/app/main.py` 顶部导入：

```python
from app.services.session_tasks import SessionTaskService
```

在 `await init_db()` 之后新增：

```python
            session_tasks = SessionTaskService()
            await session_tasks.startup()
            app.state.session_tasks = session_tasks
```

在 `runner = ClaudeTurnRunner(...)` 中加入参数：

```python
                runner = ClaudeTurnRunner(
                    store,
                    deliver_callback=_enqueue_delivery,
                    session_tasks=session_tasks,
                )
```

在 `await recover_running_sessions(registry, store)` 之后新增：

```python
                await session_tasks.start()
```

在 teardown 中 `runner.close_all()` 之后、`knowledge.stop()` 之前新增：

```python
                    session_tasks = getattr(app.state, "session_tasks", None)
                    if session_tasks is not None:
                        try:
                            await session_tasks.stop()
                        except Exception:
                            logger.exception(
                                "session task service shutdown failed"
                            )
```

- [ ] **Step 10: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py apps/api/app/tests/test_app_boot.py -v
```

Expected: PASS。

- [ ] **Step 11: 提交**

```bash
git add apps/api/app/services/claude_runner.py apps/api/app/main.py apps/api/app/tests/test_claude_runner.py
git commit -m "Publish background task events from the runner

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: 会话任务 API

**Files:**
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/routers/sessions.py`
- Test: `apps/api/app/tests/test_session_tasks_api.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_session_tasks_api.py`：

```python
import asyncio
import json

from fastapi.testclient import TestClient

from app.main import create_app

SESSION_ID = "55555555-5555-5555-5555-555555555555"


class FakeTaskService:
    def __init__(self, *, snapshot=None, events=None):
        self.snapshot_value = snapshot or {
            "has_turn": True,
            "updated_at": "2026-11-16T12:00:00+00:00",
            "tasks": [],
        }
        self.events = list(events or [])
        self.queue = None
        self.subscribed = []
        self.unsubscribed = []

    async def snapshot(self, session_id):
        return self.snapshot_value

    def subscribe(self, session_id):
        self.subscribed.append(session_id)
        self.queue = asyncio.Queue()
        for event in self.events:
            self.queue.put_nowait(event)
        return self.queue

    def unsubscribe(self, session_id, queue):
        self.unsubscribed.append((session_id, queue))


def test_session_tasks_snapshot(client):
    service = FakeTaskService(
        snapshot={
            "has_turn": False,
            "updated_at": None,
            "tasks": [
                {
                    "task_id": "t1",
                    "tool_use_id": None,
                    "description": "回测",
                    "status": "running",
                    "summary": "",
                    "last_tool_name": "Bash",
                    "usage": {"tool_uses": 1},
                    "started_at": "2026-11-16T12:00:00+00:00",
                    "updated_at": "2026-11-16T12:00:01+00:00",
                    "completed_at": None,
                }
            ],
        }
    )
    client.app.state.session_tasks = service

    response = client.get(f"/api/sessions/{SESSION_ID}/tasks")

    assert response.status_code == 200
    body = response.json()
    assert body["tasks"][0]["task_id"] == "t1"
    assert body["tasks"][0]["status"] == "running"


def test_session_tasks_requires_auth():
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert (
            anonymous.get(f"/api/sessions/{SESSION_ID}/tasks").status_code
            == 401
        )


def test_session_tasks_without_service_is_503(client):
    client.app.state.session_tasks = None
    assert (
        client.get(f"/api/sessions/{SESSION_ID}/tasks").status_code == 503
    )


def test_session_events_sends_snapshot_then_events(client):
    service = FakeTaskService(
        snapshot={"has_turn": True, "updated_at": None, "tasks": []},
        events=[
            {"type": "task_started", "task_id": "t1"},
            None,
        ],
    )
    client.app.state.session_tasks = service

    with client.stream(
        "GET", f"/api/sessions/{SESSION_ID}/events"
    ) as response:
        lines = [
            line
            for line in response.iter_lines()
            if line and line.startswith("data: ")
        ]

    payloads = [json.loads(line[len("data: "):]) for line in lines]
    assert payloads[0]["type"] == "tasks_snapshot"
    assert payloads[0]["has_turn"] is True
    assert payloads[1]["type"] == "task_started"
    assert service.subscribed == [SESSION_ID]
    assert service.unsubscribed == [(SESSION_ID, service.queue)]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_session_tasks_api.py -v
```

Expected: FAIL，404。

- [ ] **Step 3: 增加响应模型**

在 `apps/api/app/schemas.py` 的会话类型附近新增：

```python
class SessionTaskOut(BaseModel):
    task_id: str
    tool_use_id: str | None = None
    description: str = ""
    status: str = "running"
    summary: str = ""
    last_tool_name: str = ""
    usage: dict | None = None
    started_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None


class SessionTasksOut(BaseModel):
    has_turn: bool
    updated_at: str | None = None
    tasks: list[SessionTaskOut]
```

- [ ] **Step 4: 增加路由**

在 `apps/api/app/routers/sessions.py` 顶部导入：

```python
from app.schemas import (
    SessionSummaryOut,
    SessionTasksOut,
    TranscriptMessageOut,
)
```

在 `_catalog()` 之后新增：

```python
def _session_tasks(request: Request):
    service = getattr(request.app.state, "session_tasks", None)
    if service is None:
        raise HTTPException(
            status_code=503, detail="session task service is not ready"
        )
    return service


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

在 `stream_session()` 之后新增：

```python
@router.get(
    "/sessions/{session_id}/tasks",
    response_model=SessionTasksOut,
)
async def session_tasks_snapshot(
    session_id: str, request: Request
) -> SessionTasksOut:
    service = _session_tasks(request)
    snapshot = await service.snapshot(session_id)
    return SessionTasksOut(**snapshot)


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: str, request: Request
) -> StreamingResponse:
    service = _session_tasks(request)
    queue = service.subscribe(session_id)
    try:
        snapshot = await service.snapshot(session_id)
    except Exception:
        service.unsubscribe(session_id, queue)
        raise

    async def generator():
        try:
            yield _sse({"type": "tasks_snapshot", **snapshot})
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event is None:
                    return
                yield _sse(event)
        finally:
            service.unsubscribe(session_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

在 `delete_session` 路由中，`catalog.delete` 成功之后加入：

```python
        service = getattr(request.app.state, "session_tasks", None)
        if service is not None:
            try:
                await service.forget(session_id)
            except Exception:
                logger.exception(
                    "session task cleanup failed session=%s", session_id
                )
```

如果该路由没有 `logger`，在文件顶部加：

```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_session_tasks_api.py apps/api/app/tests/test_chat_api.py -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/sessions.py apps/api/app/tests/test_session_tasks_api.py
git commit -m "Expose session task snapshot and events

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6: 前端任务状态与会话 SSE

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/taskFormat.ts`
- Modify: `apps/web/src/App.tsx`

- [ ] **Step 1: 增加类型**

在 `apps/web/src/types.ts` 中 `LiveTask` 增加时间字段，并新增会话任务类型：

```typescript
export type LiveTask = {
  id: string;
  toolUseId: string | null;
  description: string;
  status: string;
  summary: string;
  lastToolName: string;
  usage: LiveTaskUsage | null;
  startedAt?: string;
  updatedAt?: string;
  completedAt?: string | null;
};

export type SessionTask = {
  task_id: string;
  tool_use_id?: string | null;
  description?: string;
  status?: string;
  summary?: string;
  last_tool_name?: string | null;
  usage?: LiveTaskUsage | null;
  started_at?: string;
  updated_at?: string;
  completed_at?: string | null;
};

export type SessionTaskEvent =
  | {
      type: "tasks_snapshot";
      has_turn: boolean;
      updated_at: string | null;
      tasks: SessionTask[];
    }
  | { type: "task_removed"; task_id: string }
  | {
      type: "task_started";
      task_id: string;
      tool_use_id?: string | null;
      description?: string;
      task_type?: string | null;
    }
  | {
      type: "task_progress";
      task_id: string;
      tool_use_id?: string | null;
      description?: string;
      last_tool_name?: string | null;
      usage?: LiveTaskUsage | null;
    }
  | {
      type: "task_updated";
      task_id: string;
      status?: string | null;
    }
  | {
      type: "task_notification";
      task_id: string;
      tool_use_id?: string | null;
      status?: string;
      summary?: string;
      usage?: LiveTaskUsage | null;
    };
```

把 `FrozenLive` 中的 `tasks` 字段删除：

```typescript
export type FrozenLive = {
  streamText: string;
  streamReasoning: string;
  activity: LiveToolCall[];
  subagents: Record<string, LiveSubagent>;
  artifacts: MessageArtifact[];
};
```

- [ ] **Step 2: 增加 API URL**

在 `apps/web/src/api.ts` 的会话相关方法附近新增：

```typescript
  sessionTasksUrl: (sessionId: string) =>
    `/api/sessions/${encodeURIComponent(sessionId)}/tasks`,
  sessionEventsUrl: (sessionId: string) =>
    `/api/sessions/${encodeURIComponent(sessionId)}/events`,
```

- [ ] **Step 3: 新建任务格式与合并模块**

创建 `apps/web/src/taskFormat.ts`：

```typescript
import type { LiveTask, SessionTask, SessionTaskEvent } from "./types";

export function emptyTask(
  id: string,
  toolUseId: string | null,
  description = "",
): LiveTask {
  return {
    id,
    toolUseId,
    description,
    status: "running",
    summary: "",
    lastToolName: "",
    usage: null,
  };
}

export function sessionTaskToLive(task: SessionTask): LiveTask {
  return {
    id: task.task_id,
    toolUseId: task.tool_use_id ?? null,
    description: task.description ?? "",
    status: task.status || "running",
    summary: task.summary ?? "",
    lastToolName: task.last_tool_name ?? "",
    usage: task.usage ?? null,
    startedAt: task.started_at,
    updatedAt: task.updated_at,
    completedAt: task.completed_at ?? null,
  };
}

type LiveTaskEvent = Exclude<
  SessionTaskEvent,
  { type: "tasks_snapshot" } | { type: "task_removed" }
>;

export function applyTaskEvent(
  tasks: Record<string, LiveTask>,
  event: LiveTaskEvent,
): Record<string, LiveTask> {
  const id = event.task_id;
  const toolUseId =
    "tool_use_id" in event ? event.tool_use_id ?? null : null;
  const existing = tasks[id] ?? emptyTask(id, toolUseId);
  if (event.type === "task_started") {
    return {
      ...tasks,
      [id]: {
        ...existing,
        toolUseId: event.tool_use_id ?? existing.toolUseId,
        description: event.description || existing.description,
        status: "running",
      },
    };
  }
  if (event.type === "task_progress") {
    return {
      ...tasks,
      [id]: {
        ...existing,
        description: event.description || existing.description,
        lastToolName: event.last_tool_name || existing.lastToolName,
        usage: event.usage ?? existing.usage,
      },
    };
  }
  if (event.type === "task_updated") {
    return {
      ...tasks,
      [id]: {
        ...existing,
        status: event.status || existing.status,
      },
    };
  }
  return {
    ...tasks,
    [id]: {
      ...existing,
      status: event.status || existing.status,
      summary: event.summary || existing.summary,
      usage: event.usage ?? existing.usage,
    },
  };
}

export function sortTasks(tasks: Record<string, LiveTask>): LiveTask[] {
  const rank = (status: string) =>
    status === "running" || status === "in_progress" ? 0 : 1;
  return Object.values(tasks).sort((left, right) => {
    const byRank = rank(left.status) - rank(right.status);
    if (byRank !== 0) return byRank;
    return (right.updatedAt ?? "").localeCompare(left.updatedAt ?? "");
  });
}

export function taskStatusLabel(status: string): string {
  switch (status) {
    case "running":
    case "in_progress":
      return "运行中";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "stopped":
    case "killed":
      return "已停止";
    case "interrupted":
      return "已中断";
    default:
      return status || "未知";
  }
}

export function taskStatusClass(status: string): string {
  if (status === "completed") return "ok";
  if (status === "failed") return "failed";
  if (status === "stopped" || status === "killed") return "stopped";
  if (status === "interrupted") return "interrupted";
  return "running";
}
```

- [ ] **Step 4: 在 App 中增加任务状态**

在 `apps/web/src/App.tsx` 顶部从 `./taskFormat` 导入：

```typescript
import { applyTaskEvent, sessionTaskToLive } from "./taskFormat";
import type { SessionTaskEvent } from "./types";
```

把现有的 `emptyTask` 函数从 `App.tsx` 删除（已移到 `taskFormat.ts`），并删除 `SessionLive`/`EMPTY_LIVE` 中的 `tasks` 字段：

```typescript
type SessionLive = FrozenLive & {
  notice: string;
  tracking: boolean;
  frozen: FrozenLive | null;
};

const EMPTY_LIVE: SessionLive = {
  streamText: "",
  streamReasoning: "",
  activity: [],
  subagents: {},
  artifacts: [],
  notice: "",
  tracking: false,
  frozen: null,
};
```

在 `useState` 区域新增：

```typescript
  const [tasksBySession, setTasksBySession] = useState<
    Record<string, Record<string, LiveTask>>
  >({});
  const tasksBySessionRef = useRef<
    Record<string, Record<string, LiveTask>>
  >({});
```

在 `updateLive` 附近新增：

```typescript
  function updateTasks(
    id: string,
    patch: (current: Record<string, LiveTask>) => Record<string, LiveTask>,
  ) {
    const current = tasksBySessionRef.current[id] ?? {};
    const next = patch(current);
    if (next === current) return;
    tasksBySessionRef.current = {
      ...tasksBySessionRef.current,
      [id]: next,
    };
    setTasksBySession(tasksBySessionRef.current);
  }

  function replaceTasks(id: string, next: Record<string, LiveTask>) {
    tasksBySessionRef.current = {
      ...tasksBySessionRef.current,
      [id]: next,
    };
    setTasksBySession(tasksBySessionRef.current);
  }

  function removeTask(id: string, taskId: string) {
    updateTasks(id, (current) => {
      if (!current[taskId]) return current;
      const next = { ...current };
      delete next[taskId];
      return next;
    });
  }
```

在 `applyStreamEvent` 中把四个 `task_*` case 改为写 `updateTasks`：

```typescript
      case "task_started": {
        updateTasks(forSession, (current) =>
          applyTaskEvent(current, event),
        );
        break;
      }
      case "task_progress": {
        updateTasks(forSession, (current) =>
          applyTaskEvent(current, event),
        );
        break;
      }
      case "task_updated": {
        updateTasks(forSession, (current) =>
          applyTaskEvent(current, event),
        );
        break;
      }
      case "task_notification": {
        updateTasks(forSession, (current) =>
          applyTaskEvent(current, event),
        );
        break;
      }
```

- [ ] **Step 5: 移除 live/frozen 中的 tasks**

在 `App.tsx` 中搜索并删除以下位置的 `tasks`：

- `openStream()` 的新流初始化对象里的 `tasks: {}`。
- `refreshTranscript()` 的 `tasks: streaming ? current.tasks : {},`。
- `cancel()` 的 frozen 对象里的 `tasks: current.tasks,`。
- `finishTurn`/其他 `updateLive` 中引用 `current.tasks` 的地方。
- `emptyTask` 的旧实现。
- `FrozenLive` 相关的任务复制。

保留 `LiveTask` 类型和 `taskFormat.ts` 的辅助函数。

- [ ] **Step 6: 增加会话 SSE 订阅**

在 `App.tsx` 中新增 ref：

```typescript
  const sessionEventsRef = useRef<Record<string, EventSource>>({});
  const sessionEventRetryRef = useRef<Record<string, number>>({});
```

新增函数：

```typescript
  function applySessionTaskEvent(
    forSession: string,
    event: SessionTaskEvent,
  ) {
    if (event.type === "tasks_snapshot") {
      const next: Record<string, LiveTask> = {};
      for (const task of event.tasks) {
        next[task.task_id] = sessionTaskToLive(task);
      }
      replaceTasks(forSession, next);
      return;
    }
    if (event.type === "task_removed") {
      removeTask(forSession, event.task_id);
      return;
    }
    updateTasks(forSession, (current) => applyTaskEvent(current, event));
  }

  function closeSessionEvents(forSession: string) {
    const source = sessionEventsRef.current[forSession];
    if (source) {
      source.close();
      delete sessionEventsRef.current[forSession];
    }
    delete sessionEventRetryRef.current[forSession];
  }

  function openSessionEvents(forSession: string) {
    if (sessionEventsRef.current[forSession]) return;
    const source = new EventSource(api.sessionEventsUrl(forSession));
    sessionEventsRef.current[forSession] = source;
    source.onmessage = (message) => {
      let event: SessionTaskEvent;
      try {
        event = JSON.parse(message.data) as SessionTaskEvent;
      } catch {
        return;
      }
      applySessionTaskEvent(forSession, event);
    };
    source.onerror = () => {
      if (sessionEventsRef.current[forSession] !== source) return;
      source.close();
      delete sessionEventsRef.current[forSession];
      const attempt = (sessionEventRetryRef.current[forSession] ?? 0) + 1;
      sessionEventRetryRef.current[forSession] = attempt;
      const delay = Math.min(1000 * 2 ** Math.min(attempt, 4), 15000);
      window.setTimeout(() => {
        if (sessionIdRef.current === forSession) {
          openSessionEvents(forSession);
        }
      }, delay);
    };
  }
```

在 `sessionId` 变化的 `useEffect` 中调用：

```typescript
  useEffect(() => {
    if (!sessionId) return;
    openSessionEvents(sessionId);
    return () => closeSessionEvents(sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);
```

- [ ] **Step 7: 确认本任务不改 ChatPanel**

任务区的渲染和 `ChatPanel` 的 `tasks` 参数在 Task 7 一起加，避免 Task 6 构建时出现未定义 props。Task 6 只负责 `tasksBySession` 状态和会话 SSE，构建应通过。

- [ ] **Step 8: 构建前端**

```bash
cd apps/web && npm run build
```

Expected: 构建成功。

- [ ] **Step 9: 提交**

```bash
git add apps/web/src/types.ts apps/web/src/api.ts apps/web/src/taskFormat.ts apps/web/src/App.tsx apps/web/dist
git commit -m "Add session-level task state and events

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7: 会话任务区与移除每轮任务列表

**Files:**
- Create: `apps/web/src/components/SessionTaskPanel.tsx`
- Modify: `apps/web/src/components/ChatPanel.tsx`
- Modify: `apps/web/src/components/MessageList.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: 新建任务区组件**

创建 `apps/web/src/components/SessionTaskPanel.tsx`：

```tsx
import type { LiveTask } from "../types";
import {
  sortTasks,
  taskStatusClass,
  taskStatusLabel,
} from "../taskFormat";

export function SessionTaskPanel({
  tasks,
}: {
  tasks: Record<string, LiveTask>;
}) {
  const list = sortTasks(tasks);
  if (list.length === 0) return null;
  const running = list.filter(
    (task) => task.status === "running" || task.status === "in_progress",
  ).length;
  return (
    <details className="session-tasks" open>
      <summary>
        <span>后台任务</span>
        <span className="session-tasks-count">
          {running > 0 ? `${running} 个运行中` : `${list.length} 个`}
        </span>
      </summary>
      <div className="session-tasks-list">
        {list.map((task) => (
          <div
            className={`session-task ${taskStatusClass(task.status)}`}
            key={task.id}
          >
            <span className="session-task-dot" aria-hidden="true" />
            <span className="session-task-label">
              {task.description || task.id}
            </span>
            <span className="session-task-status">
              {taskStatusLabel(task.status)}
            </span>
            {task.lastToolName && (
              <span className="session-task-tool">{task.lastToolName}</span>
            )}
            {task.summary && (
              <small className="session-task-summary">{task.summary}</small>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
```

- [ ] **Step 2: ChatPanel 接收并渲染任务区**

在 `ChatPanel.tsx` 顶部导入：

```typescript
import { SessionTaskPanel } from "./SessionTaskPanel";
```

在 `ChatPanelProps` 中加入：

```typescript
  tasks: Record<string, LiveTask>;
```

在 `App.tsx` 渲染 `ChatPanel` 的位置加入：

```tsx
              tasks={sessionId ? tasksBySession[sessionId] ?? {} : {}}
```

在 `.composer` 内部、`props.notice` 之前加入：

```tsx
        <SessionTaskPanel tasks={props.tasks} />
```

把传给 `MessageList` 的 `tasks={props.tasks}` 删除。

- [ ] **Step 3: MessageList 移除任务渲染**

在 `MessageList.tsx` 中删除：

- `LiveProcess` 的 `tasks` 参数和 `taskList` 变量。
- `run-trace-tasks` 整段 JSX。
- `MessageList` 的 `tasks` 参数及传递。
- `LiveProcess` 调用处的 `tasks={...}`。
- `MessageList` 的 `FrozenReply`/`LiveProcess` 相关 `tasks` 参数。
- `taskStatusClass`/`taskStatusLabel` 如果只被任务列表使用，删除；`SessionTaskPanel` 使用 `taskFormat.ts` 的版本。

- [ ] **Step 4: 增加样式**

在 `apps/web/src/styles.css` 末尾追加：

```css
.session-tasks {
  border: 1px solid rgba(120, 130, 180, 0.18);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.45);
  margin: 0 0 8px;
  padding: 6px 10px;
}

.session-tasks summary {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 12px;
  color: var(--muted, #667085);
  list-style: none;
}

.session-tasks summary::-webkit-details-marker {
  display: none;
}

.session-tasks-count {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.session-tasks-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 6px;
}

.session-task {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  min-width: 0;
}

.session-task-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #98a2b3;
  flex: none;
}

.session-task.running .session-task-dot {
  background: #16a34a;
}

.session-task.failed .session-task-dot {
  background: #dc2626;
}

.session-task.interrupted .session-task-dot {
  background: #d97706;
}

.session-task-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-task-status {
  margin-left: auto;
  color: var(--muted, #667085);
  flex: none;
}

.session-task-tool {
  color: var(--muted, #667085);
  flex: none;
}

.session-task-summary {
  color: var(--muted, #667085);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

- [ ] **Step 5: 构建前端**

```bash
cd apps/web && npm run build
```

Expected: 构建成功。

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/components/SessionTaskPanel.tsx apps/web/src/components/ChatPanel.tsx apps/web/src/components/MessageList.tsx apps/web/src/styles.css apps/web/dist
git commit -m "Show background tasks above the composer

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 8: 文档同步、三次 review 与全量验证

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: 更新 `CLAUDE.md`**

在 `running_sessions`/聊天相关段落补充：

```markdown
`running_sessions` 现在两用：`has_turn=1` 表示有 turn 在跑，启动恢复只处理这种行；`has_turn=0` 表示只有后台任务或已完成任务保留。`tasks_json` 保存会话任务快照，`SessionTaskService` 负责实时广播、1 秒防抖写库、10 分钟保留和保活；前台通过 `GET /api/sessions/{id}/tasks` 与会话级 SSE `GET /api/sessions/{id}/events` 维护输入框上方的任务区。有运行中任务时 client 不按空闲 TTL 逐出，每个任务从开始时间起最多保活 10 分钟；逐出、超过保活或服务重启时任务标记「已中断」。每轮「执行过程」不再显示任务。
```

- [ ] **Step 2: 更新 `README.md`**

在聊天/运行时说明补充：

```markdown
- 后台任务使用会话级任务区：turn 结束后仍实时更新；`GET /api/sessions/{id}/tasks` 返回快照，`GET /api/sessions/{id}/events` 推送任务事件；任务状态保存在 `running_sessions.tasks_json`，已完成任务保留 10 分钟，服务重启后运行中任务显示「已中断」。
```

- [ ] **Step 3: 第一次自我 code review（数据与恢复）**

```bash
BASE=$(git log --format=%H --grep='Record the session-level background task requirements' -1)^
git diff "$BASE" -- apps/api/app/models.py apps/api/app/db.py apps/api/app/services/turn_lease.py apps/api/app/services/recovery.py
```

检查并记录：

- 旧库迁移后现有行 `has_turn=1`，`list_turn_leases` 不会漏掉恢复行。
- `end_turn` 只在 `tasks_json` 为空时删除行；有任务时保留。
- `is_running` 与 `_answer_orphan_deliveries` 的语义一致。
- `forget`/会话删除不会误删正在恢复的 turn 租约。

- [ ] **Step 4: 第二次自我 code review（任务服务与 runner）**

```bash
BASE=$(git log --format=%H --grep='Record the session-level background task requirements' -1)^
git diff "$BASE" -- apps/api/app/services/session_tasks.py apps/api/app/services/claude_runner.py apps/api/app/main.py
```

检查并记录：

- `publish` 在 turn 外创建行时显式 `has_turn=False`。
- `task_progress` 防抖、终态立即写、turn 结束 flush。
- `keep_alive_until` 按 `started_at` 而不是进度计算。
- 逐出/关机/重启都标记 `interrupted`。
- 清理不会删除 `has_turn=1` 行；运行中任务超过 10 分钟会被标记中断。
- 并发：`_lock` 不嵌套、不跨 await 持有过久；SSE 订阅先注册再取快照。

- [ ] **Step 5: 第三次自我 code review（API 与前端）**

```bash
BASE=$(git log --format=%H --grep='Record the session-level background task requirements' -1)^
git diff "$BASE" -- apps/api/app/routers/sessions.py apps/api/app/schemas.py apps/web/src
```

检查并记录：

- `/tasks` 与 `/events` 认证、503、空快照行为。
- SSE 首帧快照、ping、断线重连、`task_removed`。
- 前端 `turn_end`/新 turn/历史刷新不再清空任务。
- 每轮「执行过程」不再渲染任务，任务区不重复。
- `interrupted` 有独立颜色/文案。
- 前端类型与服务端 snake_case 字段一致。

发现问题立即修复并重跑相关测试。

- [ ] **Step 6: 运行全量后端测试**

```bash
.venv/bin/python -m pytest apps/api/app/tests
```

Expected: 全部通过。

- [ ] **Step 7: 构建前端**

```bash
cd apps/web && npm run build
```

Expected: 构建成功，`apps/web/dist` 为最新。

- [ ] **Step 8: 提交文档与最终产物**

```bash
git add CLAUDE.md README.md apps/api apps/web/dist docs
git status --short
git commit -m "Document the session-level background task channel

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

`git status --short` 只应包含本次改动；不要提交 `.super-personal-platform/`、`.env`、`data/`。

---

## Self-Review

**Spec coverage（设计文档 → 任务）：**

- `running_sessions` 三列与迁移 → Task 2。
- turn 开始/结束/恢复过滤/保留清理 → Task 2。
- `SessionTaskService` 事件合并、快照、订阅、保活、重启中断、清理、上限 → Task 3。
- runner 发布、保活跳过、逐出/关机标记中断、turn 结束 flush → Task 4。
- `/tasks` 快照、`/events` SSE、删除会话清理 → Task 5。
- 前端任务状态、会话 SSE、turn_end 不清空 → Task 6。
- 输入框上方任务区、每轮执行过程移除任务、样式 → Task 7。
- 文档同步、三次 review、全量测试/构建 → Task 8。

**Placeholder scan：** 无 TBD/TODO；所有代码步骤包含实际代码，测试步骤包含断言和预期失败原因。

**Type consistency：** 服务端任务字段为 snake_case（`task_id`、`tool_use_id`、`last_tool_name`、`started_at`、`updated_at`、`completed_at`）；前端 `SessionTask` 使用同名，`sessionTaskToLive` 统一转成 `LiveTask` 的 camelCase。`SessionTasksOut` 与 `service.snapshot()` 字段一致。`has_turn` 在模型、租约、API、前端快照中同名。

**已知取舍：** 任务数据复用 `running_sessions`，表语义变成 turn 租约 + 任务保留；靠 `has_turn` 过滤保持恢复安全。保活上限按任务开始时间计算，进度事件不会延长；超过上限或 client 被逐出时显示「已中断」。前端保留 turn SSE 的任务事件作为兜底，任务区以会话 SSE 为准。
