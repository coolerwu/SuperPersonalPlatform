# 删除审计日志实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 `audit_log` 表及全部写入路径，把原先审计记录的信息改为已有页面字段与 warning 日志，使新库 `app.db` 只建五张表。

**Architecture:** 纯删除性改动，不新增接口、页面或依赖。顺序是「先移除写入方（定时、恢复），再移除模型」，保证每一步都能通过全量测试；文档在代码完成后一次性同步。已存在的库不做迁移，`audit_log` 空表原样保留（与「升级库保留未使用旧表」的既有约定一致）。

**Tech Stack:** FastAPI + SQLAlchemy (async, aiosqlite) + pytest/caplog；前端无改动。

**Spec:** `docs/superpowers/specs/2026-11-12-remove-audit-log-design.md`

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `apps/api/app/services/cron_service.py` | 定时任务调度；当前写 `cron_skipped` / `cron_trigger_failed` 审计 | 删除审计写入与 `_record_trigger_failure`，保留已有 `logger.warning` |
| `apps/api/app/services/recovery.py` | 重启恢复；当前写 `turn_lost_on_restart` 审计 | 删除 `_audit_lost_turn`，改为 `logger.warning` |
| `apps/api/app/models.py` | ORM 模型 | 删除 `AuditLog` 类 |
| `apps/api/app/tests/test_cron_service.py` | 定时任务服务测试 | 删审计断言、改测试名、清理 import |
| `apps/api/app/tests/test_recovery.py` | 恢复流程测试 | 丢轮测试改为 caplog 断言 warning |
| `apps/api/app/tests/test_app_boot.py` | 应用启动测试 | 新增「metadata 中无 audit_log」回归测试 |
| `docs/intent.md` | 需求唯一来源 | 修正既有措辞、追加 F18、更新编号水位 |
| `CLAUDE.md` | 协作约定 | 六张表 → 五张表，两处措辞 |
| `README.md` | 用户文档 | 表清单、重启恢复、定时任务、重建说明 |
| `.claude/skills/project-commit/SKILL.md` | 提交技能 | 表清单与「rebuild 丢失项」措辞 |

所有命令在仓库根目录 `/Users/wulang/Desktop/AI/SuperPersonalPlatform` 执行；测试统一用 `.venv/bin/python -m pytest`。

---

### Task 1: 移除定时任务的审计写入

**Files:**
- Modify: `apps/api/app/services/cron_service.py:26`（import）、`:421-440`（`_record_trigger_failure`）、`:476-490`（`cron_skipped` 写入）、`:458`（docstring）、`:563-570`（调度循环 submit 失败分支）、`:596-608`（`run_job_now` 注释与调用）
- Test: `apps/api/app/tests/test_cron_service.py:8,10`（import）、`:162-192`（`test_busy_job_is_skipped_and_audited`）、`:265-310`（`test_submit_failure_consumes_schedule_and_audits`）

说明：这是纯删除改动，「跳过」的可观察行为（`last_skipped_at`、不提交 turn）由现有断言覆盖，删除审计断言后测试应立即保持绿色；提交失败路径的 `logger.warning` 本来就存在，不需要新增行为测试。

- [ ] **Step 1: 更新测试——删除审计断言并改名**

把 `apps/api/app/tests/test_cron_service.py` 中的 `test_busy_job_is_skipped_and_audited` 整个函数替换为：

```python
async def test_busy_job_is_skipped(client) -> None:
    now = utcnow()
    session_id = str(uuid.uuid4())
    job_id = await _make_job(
        next_run_at=now - timedelta(minutes=5), session_id=session_id
    )
    store, registry = _store(), FakeRegistry()
    registry.busy_sessions.add(session_id)

    fired = await run_due_jobs(now, store=store, registry=registry)
    assert job_id not in fired
    assert [turn for turn in registry.turns if turn.session_id == session_id] == []

    job = await _job(job_id)
    assert job.last_skipped_at is not None
    assert job.last_run_at is None
    assert job.next_run_at is not None and job.next_run_at > now
```

把 `test_submit_failure_consumes_schedule_and_audits` 整个函数替换为：

```python
async def test_submit_failure_consumes_schedule(client) -> None:
    now = utcnow()
    session_id = str(uuid.uuid4())
    job_id = await _make_job(
        next_run_at=now - timedelta(minutes=1), session_id=session_id
    )
    store = _store()
    registry = FakeRegistry(submit_error=RuntimeError("registry closed"))

    fired = await run_due_jobs(now, store=store, registry=registry)
    assert job_id not in fired
    assert registry.turns == []

    job = await _job(job_id)
    assert job.last_run_at is not None
    assert job.next_run_at is not None and job.next_run_at > now

    # The schedule was consumed, so the same tick cannot retry (and duplicate
    # a channel push).
    await run_due_jobs(now, store=store, registry=registry)
    assert registry.turns == []
```

同时修改 import：
- 删除第 8 行 `from sqlalchemy import select`（该文件只剩审计查询用到 `select`）；
- 第 10 行改为 `from app.models import CronJob, utcnow`（去掉 `AuditLog`）。

- [ ] **Step 2: 运行受影响测试，确认仍是绿色**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_cron_service.py -q`
Expected: 全部 PASS。此步只删断言，不改变被测行为；若失败，说明改动引入了不期望的行为变化，先排查再继续。

- [ ] **Step 3: 删除 `_record_trigger_failure` 及两处调用**

删除 `apps/api/app/services/cron_service.py` 中整个函数（含其 docstring）：

```python
async def _record_trigger_failure(job_id: str) -> None:
    """Audit a consumed trigger whose turn could not be submitted."""
    try:
        async with SessionLocal() as db:
            db.add(
                AuditLog(
                    actor="system",
                    action="cron_trigger_failed",
                    entity="cron_job",
                    entity_id=job_id,
                )
            )
            await db.commit()
    except Exception:
        logger.exception(
            "cron job %s failed to record trigger failure", job_id
        )
```

调度循环中的失败分支删除 `await _record_trigger_failure(job_id)` 一行，保留其余：

```python
                try:
                    await registry.submit(turn)
                except Exception:
                    logger.warning(
                        "cron job %s failed to submit turn", job_id, exc_info=True
                    )
                    continue
```

`run_job_now` 中删除 `await _record_trigger_failure(job.id)` 一行，并更新注释：

```python
            # Consume the run before dispatch. A later submit failure is
            # reported (and never retried), matching the scheduler.
            await db.commit()
            await db.refresh(job)
            try:
                await registry.submit(turn)
            except Exception as exc:
                logger.warning(
                    "cron job %s failed to submit turn", job.id, exc_info=True
                )
                raise CronTriggerFailed("failed to submit cron turn") from exc
```

- [ ] **Step 4: 删除 `cron_skipped` 写入与 import**

`prepare_trigger` 中把忙时分支改为（删除 `db.add(...)` 块）：

```python
    if registry.is_busy(job.session_id):
        job.last_skipped_at = moment
        if immediate:
            raise CronBusy(job.session_id, "busy")
        _advance(job, moment)
        return None
```

docstring 中把：

```python
    duplicate a channel push. Scheduled triggers with a busy session are
    skipped (and audited); manual triggers raise :class:`CronBusy` instead.
```

改为：

```python
    duplicate a channel push. Scheduled triggers with a busy session are
    skipped; manual triggers raise :class:`CronBusy` instead.
```

第 26 行 import 改为 `from app.models import CronJob, utcnow`。`SessionLocal` 与 `select` 在本文件其他地方仍在使用，保留。

- [ ] **Step 5: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_cron_service.py apps/api/app/tests/test_cron_api.py -q`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add apps/api/app/services/cron_service.py apps/api/app/tests/test_cron_service.py
git commit -m "Remove cron audit writes"
```

---

### Task 2: 网页丢轮改为 warning 日志

**Files:**
- Modify: `apps/api/app/services/recovery.py:24-28`（模块 docstring）、`:37-38`（import）、`:51-66`（`_audit_lost_turn`）、`:70-76`（`_drop_lease` docstring）、`:94-95`（调用点）
- Test: `apps/api/app/tests/test_recovery.py:1-7`（import）、`:292-320`（`test_recovery_audits_a_lost_web_turn`）

这一任务有真实的新行为（warning 日志），按 TDD 先写测试。

- [ ] **Step 1: 改写测试为 caplog 断言**

`apps/api/app/tests/test_recovery.py` 顶部 import 改为：

```python
import logging

import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery
from app.services import channel_delivery, recovery, turn_lease
from app.services.claude_runner import transcript_path
```

（`select` 只在旧审计查询中使用，`AuditLog` 不再使用，两者都删。）

把 `test_recovery_audits_a_lost_web_turn` 整个函数替换为：

```python
@pytest.mark.asyncio
async def test_recovery_logs_a_lost_web_turn(tmp_path, caplog):
    await init_db()
    session_id = "bbbbbbbb-eeee-eeee-eeee-eeeeeeeeeeee"
    await turn_lease.add(session_id)
    try:
        with caplog.at_level(logging.WARNING, logger="app.services.recovery"):
            await recovery.recover_running_sessions(
                FakeRegistry(), FakeStore(), home=tmp_path
            )
        assert await turn_lease.is_running(session_id) is False
        assert any(
            "web turn lost on restart" in record.getMessage()
            and session_id in record.getMessage()
            for record in caplog.records
        )
    finally:
        await turn_lease.remove(session_id)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_recovery.py::test_recovery_logs_a_lost_web_turn -v`
Expected: FAIL——`caplog` 中没有任何 warning（当前实现只写 `audit_log`，不打日志）。

- [ ] **Step 3: 改造 recovery.py**

模块 docstring 中把：

```python
sweep can tell that contact their message was not processed, and a web turn
(which has no delivery row) leaves an ``audit_log`` entry.
```

改为：

```python
sweep can tell that contact their message was not processed, and a web turn
(which has no delivery row) is reported with a warning log entry.
```

删除 import 中的两行（`SessionLocal` 与 `AuditLog` 在本文件只为审计写入服务）：

```python
from app.db import SessionLocal
from app.models import AuditLog
```

删除整个 `_audit_lost_turn` 函数：

```python
async def _audit_lost_turn(session_id: str) -> None:
    try:
        async with SessionLocal() as db:
            db.add(
                AuditLog(
                    actor="system",
                    action="turn_lost_on_restart",
                    entity="session",
                    entity_id=session_id,
                )
            )
            await db.commit()
    except Exception:
        logger.exception(
            "recovery could not write audit entry session=%s", session_id
        )
```

`_drop_lease` docstring 中把：

```python
    Filled rows are left to the sender;
    a web turn has no delivery row, so its loss is recorded in ``audit_log``.
```

改为：

```python
    Filled rows are left to the sender;
    a web turn has no delivery row, so its loss is recorded as a warning log.
```

把调用改为：

```python
        if not has_delivery:
            logger.warning("web turn lost on restart session=%s", session_id)
    await turn_lease.remove(session_id)
```

（删除 `await _audit_lost_turn(session_id)`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_recovery.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/recovery.py apps/api/app/tests/test_recovery.py
git commit -m "Log lost web turns instead of auditing them"
```

---

### Task 3: 删除 AuditLog 模型

**Files:**
- Modify: `apps/api/app/models.py:78-87`（`AuditLog` 类）
- Test: `apps/api/app/tests/test_app_boot.py`（文件末尾新增测试）

- [ ] **Step 1: 写失败的回归测试**

在 `apps/api/app/tests/test_app_boot.py` 末尾追加：

```python
def test_database_metadata_has_no_audit_log_table():
    from app.db import Base

    assert "audit_log" not in Base.metadata.tables
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_app_boot.py::test_database_metadata_has_no_audit_log_table -v`
Expected: FAIL——`audit_log` 仍在 SQLAlchemy metadata 中。

- [ ] **Step 3: 删除模型**

删除 `apps/api/app/models.py` 中的整个类：

```python
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(120), default="user")
    action: Mapped[str] = mapped_column(String(120))
    entity: Mapped[str] = mapped_column(String(120))
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
```

- [ ] **Step 4: 确认没有残留引用并跑全量测试**

Run: `grep -rn "AuditLog\|audit_log" apps/api/app --include="*.py"`
Expected: 无输出。

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`
Expected: 全部 PASS（大约 770+ 通过、3 个跳过；数量比改动前少 0 个测试，只删断言）。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/models.py apps/api/app/tests/test_app_boot.py
git commit -m "Drop the audit_log model and table"
```

---

### Task 4: 文档同步

**Files:**
- Modify: `docs/intent.md:4-5`（更新时间与编号水位）、`:38`、`:80`、`:207-208`、`:215`、`:364`、`:366`，并在文件末尾追加 F18
- Modify: `CLAUDE.md:18`、`:20`、`:24`
- Modify: `README.md:9`、`:69`、`:172-173`、`:248-250`、`:386`
- Modify: `.claude/skills/project-commit/SKILL.md`（两处：app.db 表清单、「Rebuilding app.db loses ...」）

- [ ] **Step 1: `docs/intent.md` 头部**

第 4-5 行改为：

```markdown
- 最后更新：2026-11-12
- 编号水位：F18
```

- [ ] **Step 2: `docs/intent.md` 既有措辞（7 处）**

按顺序精确替换：

1. F1 功能点 7：
   `7. 定时任务：5 字段 cron、IANA 时区、专属会话、忙时跳过并审计、可选推送到微信联系人、停机后最多补跑一次。`
   → `7. 定时任务：5 字段 cron、IANA 时区、专属会话、忙时跳过并更新 last_skipped_at、可选推送到微信联系人、停机后最多补跑一次。`
2. F2 功能点 5：
   `网页来源没有投递行时写 `audit_log`；` → `网页来源没有投递行时写 warning 日志；`
3. F7 功能点 4：
   `4. 会话忙时跳过本次触发并写 `audit_log`（`cron_skipped`），不排队堆积；手动「立即运行」遇到忙返回 409。`
   → `4. 会话忙时跳过本次触发并更新 `cron_jobs.last_skipped_at`（定时任务页展示跳过提示），不排队堆积；手动「立即运行」遇到忙返回 409。`
4. F7 功能点 5：
   `提交失败写 `cron_trigger_failed` 审计，不重试。` → `提交失败写 warning 日志，不重试。`
5. F7 验收：
   `- 定时任务按时触发，会话忙时跳过并写审计；配置推送目标时送达微信。`
   → `- 定时任务按时触发，会话忙时跳过并更新 `last_skipped_at`；配置推送目标时送达微信。`
6. F15 功能点 3：
   `3. 新库的 `app.db` 只创建六张表：`audit_log`、`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`；`
   → `3. 新库的 `app.db` 只创建五张表：`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`；`
7. F15 功能点 5：
   `会丢失回测历史、定时任务、审计、未完成投递与入站去重记录；` → `会丢失回测历史、定时任务、未完成投递与入站去重记录；`

- [ ] **Step 3: `docs/intent.md` 末尾追加 F18**

```markdown
### F18 删除审计日志

- 状态：现行
- 日期：2026-11-12

#### 功能点

1. 删除 `audit_log` 表与全部写入路径（`AuditLog` 模型、定时任务跳过/提交失败写入、重启恢复丢轮写入）；新库 `app.db` 只创建五张表：`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`。
2. 原审计信息由现有可见性替代：定时任务忙时跳过只更新 `cron_jobs.last_skipped_at`（定时任务页展示「上次触发因会话忙被跳过」）；定时提交失败与网页丢轮各写一条 warning 日志。
3. 已存在的库不迁移：`audit_log` 空表原样保留，平台不再读写。

#### 验收标准

- 代码中没有 `AuditLog` 模型与审计写入，测试不再断言审计行。
- 新库只有五张表；定时任务页仍显示跳过提示；提交失败与网页丢轮在日志中有 warning。
- 后端测试与前端构建通过。
```

- [ ] **Step 4: `CLAUDE.md` 三处**

1. 第 18 行：
   `- 新库的 `app.db` 只创建六张表：`audit_log`、`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`；从旧版升级的库会保留未使用的旧表。`
   → `- 新库的 `app.db` 只创建五张表：`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`；从旧版升级的库会保留未使用的旧表。`
2. 第 20 行：
   `网页来源写 `audit_log`；` → `网页来源没有投递行时写 warning 日志；`
3. 第 24 行：
   `会话忙时跳过并写 `audit_log`；` → `会话忙时跳过并更新 `cron_jobs.last_skipped_at`；`

- [ ] **Step 5: `README.md` 六处**

1. 第 9 行：
   `新库的 `app.db` 只创建 `audit_log`、`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions` 六张表`
   → `新库的 `app.db` 只创建 `cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions` 五张表`
2. 第 69 行：
   `网页来源写 `audit_log`。` → `网页来源没有投递行时写 warning 日志。`
3. 第 172 行：
   `本次触发跳过并写 `audit_log`；` → `本次触发跳过并更新 `last_skipped_at`（页面显示「上次触发因会话忙被跳过」）；`
4. 第 173 行：
   `提交失败写审计，不重试。` → `提交失败写 warning 日志，不重试。`
5. 数据存储一节：
   `新库只创建六张表；` → `新库只创建五张表；`
   并删除整行：`  - `audit_log`：审计流水（定时跳过、触发失败等）；`
6. 第 386 行：
   `会丢失回测历史、定时任务、审计记录、未完成投递与入站去重记录；` → `会丢失回测历史、定时任务、未完成投递与入站去重记录；`

- [ ] **Step 6: `.claude/skills/project-commit/SKILL.md` 两处**

1. Runtime state 一节：
   `New installs create only `audit_log`, `cron_jobs`, `channel_inbounds`, `channel_deliveries`, `backtests`, `running_sessions`;`
   → `New installs create only `cron_jobs`, `channel_inbounds`, `channel_deliveries`, `backtests`, `running_sessions`;`
2. Upgrading 一节：
   `Rebuilding `app.db` loses backtest history, cron jobs, audit rows, pending deliveries and inbound dedup records,`
   → `Rebuilding `app.db` loses backtest history, cron jobs, pending deliveries and inbound dedup records,`

- [ ] **Step 7: 确认没有遗漏的审计措辞**

Run: `grep -rn "audit" docs/intent.md CLAUDE.md README.md .claude/skills/project-commit/SKILL.md`
Expected: 仅剩 `docs/intent.md` 中 F18 条目与 `docs/superpowers/` 下设计/计划文档对删除本身的描述；README/CLAUDE/skill 中不应再有 `audit_log` 作为现行数据表出现。

- [ ] **Step 8: 提交**

```bash
git add docs/intent.md CLAUDE.md README.md .claude/skills/project-commit/SKILL.md
git commit -m "Sync docs for audit log removal"
```

---

### Task 5: 全量验证

**Files:** 无（仅运行检查）

- [ ] **Step 1: 后端全量测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`
Expected: 全部 PASS（770+ passed，3 skipped）。

- [ ] **Step 2: 前端构建**

Run: `cd apps/web && npm run build`
Expected: `✓ built in ...`，`apps/web/dist` 内容无变化（本改动不涉及前端）。

- [ ] **Step 3: 验收 grep**

Run: `grep -rn "AuditLog\|audit_log" apps/api/app --include="*.py"`
Expected: 无输出。

- [ ] **Step 4: 确认工作区干净**

Run: `git status --short`
Expected: 无未提交改动（dist 若因构建产物时间戳变化，确认 `git diff` 无内容后无需提交）。

---

## Self-Review

- **Spec coverage**：规格 §3.1（三处代码删除）→ Task 1/2/3；§3.2（测试）→ Task 1 Step 1、Task 2 Step 1、Task 3 Step 1；§3.3（文档）→ Task 4；§4（数据与兼容）→ 不写迁移，Task 3 只删模型；§5（验收）→ Task 3 Step 4 与 Task 5。
- **Placeholder scan**：无 TBD/TODO；所有代码步骤给出完整替换代码。
- **Type consistency**：日志消息 `web turn lost on restart session=%s` 在 recovery.py 与测试断言中一致；测试函数名 `test_database_metadata_has_no_audit_log_table` 唯一。
- **依赖顺序**：Task 1/2 先移除全部 `AuditLog` 引用，Task 3 再删模型，避免任一步导入中断。
