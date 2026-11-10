# 渠道、定时、清理与旧栈下线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在核心运行时迁移完成后，把微信渠道、定时任务、会话清理与配置/记忆接口切到 SDK 原生架构，并删除旧表、旧模块与旧依赖。

**Architecture:** 渠道入站直接解析 `settings.json` 的联系人绑定并提交 `Turn`；投递义务在轮次开始时写入 `channel_deliveries`，结束时回填并发送；定时任务只保留 `cron_jobs` 表与调度器；会话清理按 SDK transcript 的 `last_modified` 删除；旧数据库表与 LangGraph 相关模块整体移除。

**Tech Stack:** FastAPI、SQLAlchemy async、SQLite、claude-agent-sdk、pytest。

**前置:** `docs/superpowers/plans/2026-11-10-claude-runtime-core.md` 已完成。

**约定:** 每个任务提交前按项目要求做 3 次自我 code review；测试命令为 `.venv/bin/python -m pytest apps/api/app/tests -q`。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `apps/api/app/services/channel_delivery.py` | 投递行创建、完成、重试状态 |
| `apps/api/app/services/channel_intake.py` | 微信入站：幂等、会话绑定、命令、提交 Turn |
| `apps/api/app/services/claude_runner.py` | 增加投递义务与完成回调 |
| `apps/api/app/services/weixin_runtime.py` | 入站与投递改走新服务 |
| `apps/api/app/services/cron_service.py` | 定时触发改为提交 Turn |
| `apps/api/app/services/session_service.py` | 清理改为删除 transcript |
| `apps/api/app/routers/app_settings.py` | 运行环境配置读写 settings.json |
| `apps/api/app/routers/knowledge.py` | 知识库配置读写 settings.json |
| `apps/api/app/routers/agents.py` / `providers.py` | Agent 与 Provider 配置读写 settings.json |
| `apps/api/app/routers/memories.py` | 改为读写 `claude/CLAUDE.md` |
| `apps/api/app/models.py` | 精简为 6 张表 |

---

### Task 1: 投递表与新服务

**Files:**
- Modify: `apps/api/app/models.py`（替换 `ChannelDelivery`）
- Create: `apps/api/app/services/channel_delivery.py`
- Test: `apps/api/app/tests/test_channel_delivery_service.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest

from app.db import SessionLocal, init_db
from app.services import channel_delivery


@pytest.mark.asyncio
async def test_pending_to_delivered():
    await init_db()
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db,
            channel_id="ch1",
            external_id="wx-1",
            session_id="11111111-1111-1111-1111-111111111111",
        )
        await db.commit()
    row = await channel_delivery.get(delivery_id)
    assert row is not None and row.status == "pending"
    await channel_delivery.complete(delivery_id, "hello")
    row = await channel_delivery.get(delivery_id)
    assert row is not None and row.status == "sent" and row.content == "hello"


@pytest.mark.asyncio
async def test_pending_list_orders_by_id():
    await init_db()
    async with SessionLocal() as db:
        first = await channel_delivery.create_pending(
            db, channel_id="ch1", external_id="wx-1", session_id="s1"
        )
        second = await channel_delivery.create_pending(
            db, channel_id="ch1", external_id="wx-1", session_id="s1"
        )
        await db.commit()
    rows = await channel_delivery.list_pending()
    ids = [row.id for row in rows]
    assert ids.index(first) < ids.index(second)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_channel_delivery_service.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`models.py` 中把 `ChannelDelivery` 替换为：

```python
class ChannelDelivery(Base):
    __tablename__ = "channel_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(200), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
```

`apps/api/app/services/channel_delivery.py`：

```python
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import ChannelDelivery, utcnow


async def create_pending(
    db: AsyncSession, *, channel_id: str, external_id: str, session_id: str
) -> int:
    row = ChannelDelivery(
        channel_id=channel_id,
        external_id=external_id,
        session_id=session_id,
        status="pending",
    )
    db.add(row)
    await db.flush()
    return int(row.id)


async def get(delivery_id: int) -> ChannelDelivery | None:
    async with SessionLocal() as db:
        return await db.get(ChannelDelivery, delivery_id)


async def complete(delivery_id: int, content: str) -> None:
    async with SessionLocal() as db:
        row = await db.get(ChannelDelivery, delivery_id)
        if row is None or row.status == "sent":
            return
        row.content = content
        row.status = "pending"
        await db.commit()


async def mark_attempting(delivery_id: int) -> None:
    async with SessionLocal() as db:
        row = await db.get(ChannelDelivery, delivery_id)
        if row is None:
            return
        row.status = "attempting"
        row.attempts = int(row.attempts or 0) + 1
        row.last_error = None
        await db.commit()


async def mark_sent(delivery_id: int) -> None:
    async with SessionLocal() as db:
        row = await db.get(ChannelDelivery, delivery_id)
        if row is None:
            return
        row.status = "sent"
        row.last_error = None
        row.delivered_at = utcnow()
        await db.commit()


async def mark_pending(
    delivery_id: int, error: str, *, max_attempts: int
) -> None:
    async with SessionLocal() as db:
        row = await db.get(ChannelDelivery, delivery_id)
        if row is None or row.status == "sent":
            return
        row.last_error = error[:2000]
        row.status = "failed" if int(row.attempts or 0) >= max_attempts else "pending"
        await db.commit()


async def list_pending() -> list[ChannelDelivery]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(ChannelDelivery)
            .where(ChannelDelivery.status.in_(("pending", "attempting")))
            .order_by(ChannelDelivery.id)
        )
        return list(rows)
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_channel_delivery_service.py -q`
Expected: PASS

- [x] **Step 5: 三次自查并提交**

```bash
git add apps/api/app/models.py apps/api/app/services/channel_delivery.py apps/api/app/tests/test_channel_delivery_service.py
git commit -m "Replace channel delivery with session-keyed obligations"
```

> **质量审查修订（执行时已落实）：** `mark_attempting` 改为条件 UPDATE 的原子 claim（仅 pending/attempting，返回是否抢到）；`sent`/`failed` 为终态不可回退；`complete` 只回填 pending/attempting；`list_pending` 过滤空 content 与终态行；`init_db` 检测旧 `channel_deliveries`（缺 `id` 列）时直接 DROP 重建并 warning；启动顺序改为先恢复 `running_sessions` 再 `weixin.start()`（sweep 不会扫到空行）；测试补 rollback 不落库、claim 竞争、attempts/max_attempts、终态 no-op、过滤与清理。**后续 Task 6：** 删除会话时按 `session_id` 一并删除投递行，移除联系人绑定时按 `(channel_id, external_id)` 清理。**Task 5：** `cron_jobs.push_conversation_id` 现在按 `external_id` 语义理解（重建库后不再有旧 32 位 hex 数据）。

---

### Task 2: 轮次携带投递义务

**Files:**
- Modify: `apps/api/app/services/claude_runner.py`
- Modify: `apps/api/app/services/channel_delivery.py`
- Test: `apps/api/app/tests/test_claude_runner_delivery.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery, RunningSession
from app.services.claude_runner import ClaudeTurnRunner, DeliveryRequest, Turn
from app.services.settings_store import AgentSection, PlatformSettings, ProviderSection
from app.services.turn_stream import TurnStream


class FakeClient:
    async def connect(self):
        pass

    async def query(self, prompt):
        pass

    async def receive_response(self):
        yield ResultMessage()

    async def interrupt(self):
        pass

    async def disconnect(self):
        pass


class ResultMessage:
    subtype = "success"
    terminal_reason = "completed"
    result = "answer"
    usage = None
    errors = None
    api_error_status = None
    is_error = False


class FakeStore:
    async def get(self):
        return PlatformSettings(
            agents=[
                AgentSection(
                    id="default",
                    name="Assistant",
                    provider=ProviderSection(
                        base_url="https://api.deepseek.com/anthropic",
                        model="deepseek-flash[1m]",
                        api_key="sk-test",
                    ),
                )
            ]
        )


@pytest.mark.asyncio
async def test_delivery_created_and_completed(tmp_path):
    await init_db()
    sent = []

    async def deliver(delivery_id: int) -> None:
        sent.append(delivery_id)

    runner = ClaudeTurnRunner(
        store=FakeStore(),
        home=tmp_path,
        client_factory=lambda options: FakeClient(),
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id="22222222-2222-2222-2222-222222222222",
        agent_id="default",
        prompt="hi",
        source="wechat",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    outcome = await runner.run(turn, TurnStream())
    assert outcome.status == "completed"
    async with SessionLocal() as db:
        rows = list((await db.scalars(select(ChannelDelivery))).all())
        assert rows[0].content == "answer"
        lease = await db.get(RunningSession, turn.session_id)
        assert lease is None
    assert len(sent) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner_delivery.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`claude_runner.py` 修改：

```python
from app.db import SessionLocal
from app.models import RunningSession
from app.services import channel_delivery, turn_lease


@dataclass
class DeliveryRequest:
    channel_id: str
    external_id: str


@dataclass
class Turn:
    session_id: str
    agent_id: str
    prompt: str
    source: str = "manual"
    delivery: DeliveryRequest | None = None
```

构造函数增加：

```python
        deliver_callback: Callable[[int], Any] | None = None
```

`run()` 开头改为在一个事务里写租约与投递：

```python
        delivery_id: int | None = None
        async with SessionLocal() as db:
            await turn_lease.add_in(db, turn.session_id)
            if turn.delivery is not None:
                delivery_id = await channel_delivery.create_pending(
                    db,
                    channel_id=turn.delivery.channel_id,
                    external_id=turn.delivery.external_id,
                    session_id=turn.session_id,
                )
            await db.commit()
```

`finally` 中删除租约后：

```python
            if delivery_id is not None:
                await channel_delivery.complete(delivery_id, outcome.output)
                if self._deliver_callback is not None:
                    try:
                        await self._deliver_callback(delivery_id)
                    except Exception:
                        logger.exception("delivery send failed delivery=%s", delivery_id)
```

删除原来单独调用 `turn_lease.add/remove` 的代码。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner_delivery.py apps/api/app/tests/test_claude_runner.py -q`
Expected: PASS

- [x] **Step 5: 三次自查并提交**

```bash
git add apps/api/app/services/claude_runner.py apps/api/app/tests/test_claude_runner_delivery.py
git commit -m "Attach channel delivery to SDK turns"
```

> **质量审查修订：** 重启恢复要补投递归属——runner 在 `source == "resume"` 时用 `channel_delivery.find_unfilled_for_session(session_id)` 认领崩溃前创建、`content` 仍为空的投递行（**绝不新建行**，否则会重复）；空行只归 resume、非空行只归 sweep，这一分区写进模块 docstring。`complete` 只回填 pending/attempting，sent/failed 为终态。**终态语义：** `completed` 正常回填并发送（空输出用“（无回复）”）；`failed` 且输出为空时用固定文案“（任务执行失败，请稍后重试。）”；用户取消（aborted 结果或 interrupt 后未收到结果）用 `discard(delivery_id)` 删除投递行、不发送；优雅停机（`keep_lease`）跳过 complete/callback，保留空 pending 行供重启恢复；`complete` 返回 False（行已不存在/终态）时不调用发送回调。

---

### Task 3: 渠道入站服务

**Files:**
- Create: `apps/api/app/services/channel_intake.py`
- Test: `apps/api/app/tests/test_channel_intake.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest

from app.db import init_db
from app.services.channel_intake import HELP_TEXT, ingest
from app.services.settings_store import (
    AgentSection,
    ChannelSection,
    ConversationSection,
    PlatformSettings,
)


class FakeStore:
    def __init__(self):
        self.settings = PlatformSettings(
            agents=[AgentSection(id="default", name="Assistant")],
            channels=[
                ChannelSection(id="ch1", agent_id="default", status="active", account_id="a1", bot_token="t")
            ],
            channel_conversations={},
        )

    async def get(self):
        return self.settings

    async def update_conversation(self, external_id, conversation):
        self.settings.channel_conversations[external_id] = conversation


class FakeRegistry:
    def __init__(self):
        self.turns = []
        self.interrupted = []

    async def submit(self, turn):
        self.turns.append(turn)

    async def interrupt(self, session_id):
        self.interrupted.append(session_id)
        return True

    def is_busy(self, session_id):
        return False


@pytest.mark.asyncio
async def test_first_message_creates_conversation_and_turn():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    result = await ingest(
        store=store,
        registry=registry,
        channel_id="ch1",
        external_message_id="m1",
        external_id="wx-1",
        display_name="张三",
        text="你好",
        context_token="ctx",
    )
    assert result.session_id
    assert registry.turns[0].prompt == "你好"
    assert registry.turns[0].delivery.external_id == "wx-1"
    assert store.settings.channel_conversations["wx-1"].context_token == "ctx"


@pytest.mark.asyncio
async def test_duplicate_is_ignored():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    await ingest(store=store, registry=registry, channel_id="ch1",
                 external_message_id="m1", external_id="wx-1", display_name="张三",
                 text="你好", context_token="ctx")
    result = await ingest(store=store, registry=registry, channel_id="ch1",
                          external_message_id="m1", external_id="wx-1", display_name="张三",
                          text="你好", context_token="ctx")
    assert result.duplicate is True
    assert len(registry.turns) == 1


@pytest.mark.asyncio
async def test_help_command():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    result = await ingest(store=store, registry=registry, channel_id="ch1",
                          external_message_id="m2", external_id="wx-1", display_name="张三",
                          text="/help", context_token=None)
    assert result.reply == HELP_TEXT
    assert registry.turns == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_channel_intake.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`apps/api/app/services/channel_intake.py`：

```python
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import ChannelInbound
from app.services import chat_commands
from app.services.claude_runner import DeliveryRequest, Turn
from app.services.settings_store import ConversationSection

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "可用命令：\n"
    "/new 开始新会话（旧会话保留）\n"
    "/clear 同 /new\n"
    "/status 查看会话与执行状态\n"
    "/stop 停止当前任务\n"
    "/help 显示本说明\n\n"
    "直接发送其他内容即可与 Agent 对话。"
)
NEW_SESSION_TEXT = "已开始新会话，之前的对话不会带入；旧会话仍保留。"
STOP_EMPTY_TEXT = "当前没有正在执行的任务。"
STOP_DONE_TEXT = "已请求停止当前任务。"


@dataclass
class IngestResult:
    session_id: str | None = None
    duplicate: bool = False
    reply: str | None = None


async def _claim_inbound(channel_id: str, external_message_id: str) -> ChannelInbound | None:
    async with SessionLocal() as db:
        row = ChannelInbound(channel_id=channel_id, external_message_id=external_message_id)
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return None
        return row


async def _record_inbound(
    channel_id: str, external_message_id: str, session_id: str | None
) -> None:
    async with SessionLocal() as db:
        row = await db.scalar(
            select(ChannelInbound).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id == external_message_id,
            )
        )
        if row is not None:
            row.session_id = session_id
            await db.commit()


def _new_conversation(channel_id: str, external_id: str, display_name: str,
                      context_token: str | None) -> ConversationSection:
    return ConversationSection(
        session_id=str(uuid.uuid4()),
        channel_id=channel_id,
        display_name=display_name,
        context_token=context_token,
    )


async def _command_reply(
    command: str,
    *,
    conversation: ConversationSection,
    agent_id: str,
    registry: Any,
) -> str:
    if command in ("/new", "/clear"):
        return NEW_SESSION_TEXT
    if command == "/help":
        return HELP_TEXT
    if command == "/status":
        busy = registry.is_busy(conversation.session_id)
        return (
            f"Agent：{agent_id}\n会话：{conversation.session_id}\n"
            f"状态：{'执行中' if busy else '空闲'}"
        )
    if command == "/stop":
        stopped = await registry.interrupt(conversation.session_id)
        return STOP_DONE_TEXT if stopped else STOP_EMPTY_TEXT
    return f"未知命令。\n\n{HELP_TEXT}"


async def ingest(
    *,
    store: Any,
    registry: Any,
    channel_id: str,
    external_message_id: str,
    external_id: str,
    display_name: str,
    text: str,
    context_token: str | None,
) -> IngestResult:
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    claimed = await _claim_inbound(channel_id, external_message_id)
    if claimed is None:
        return IngestResult(duplicate=True)

    settings = await store.get()
    channel = next((item for item in settings.channels if item.id == channel_id), None)
    if channel is None or channel.status != "active":
        raise ValueError("channel is not active")
    agent = next((item for item in settings.agents if item.id == channel.agent_id), None)
    if agent is None or agent.status != "active":
        raise ValueError("agent is not available")

    conversation = settings.channel_conversations.get(external_id)
    if conversation is None:
        conversation = _new_conversation(channel_id, external_id, display_name, context_token)
    else:
        conversation.display_name = display_name or conversation.display_name
        if context_token:
            conversation.context_token = context_token
    await store.update_conversation(external_id, conversation)

    command = chat_commands.parse_command(text)
    if command is not None:
        if command in ("/new", "/clear"):
            conversation = _new_conversation(
                channel_id, external_id, conversation.display_name, context_token
            )
            await store.update_conversation(external_id, conversation)
        reply = await _command_reply(
            command, conversation=conversation, agent_id=agent.id, registry=registry
        )
        await _record_inbound(channel_id, external_message_id, conversation.session_id)
        return IngestResult(session_id=conversation.session_id, reply=reply)

    await registry.submit(
        Turn(
            session_id=conversation.session_id,
            agent_id=agent.id,
            prompt=text,
            source="wechat",
            delivery=DeliveryRequest(channel_id=channel_id, external_id=external_id),
        )
    )
    await _record_inbound(channel_id, external_message_id, conversation.session_id)
    return IngestResult(session_id=conversation.session_id)
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_channel_intake.py -q`
Expected: PASS

- [x] **Step 5: 三次自查并提交**

```bash
git add apps/api/app/services/channel_intake.py apps/api/app/tests/test_channel_intake.py
git commit -m "Add settings-backed WeChat intake"
```

> **质量审查修订（执行时已落实）：** 入站校验（文本、external_id、消息 id 列表、channel/agent active、会话 UUID 与 transcript 存在）全部前置于幂等占位；占位后任何异常删除本次占位行再抛出，保证运行时重试。`ingest` 接收 `external_message_ids: list[str]` 并在同一事务批量占位（打包窗口的其余 id 也被吸收）。`channel_conversations` 的 key 改为 `f"{channel_id}:{external_id}"`，跨渠道不复用绑定。**绑定有效性 = canonical UUID + 本渠道 +（`registry.is_busy(session_id)` 或 transcript 存在）**，保证排队窗口内追问/`/stop`/`/status` 不换会话。复用前要求 canonical UUID；`/new` 的 context_token 回退到旧值；`IntegrityError` 复查行存在才算重复；`ChannelInbound.session_id` 用 `String(36)`；`_record_inbound` 在 submit 之后 best-effort（失败只 warning，不补偿、不丢消息）。Task 4 传 `batch.ids`、命令拦截前先 flush、保留 `_handle_message` 的逐条 `inbound_seen` 检查；Task 5/7 读取绑定使用新 key。

---

### Task 4: 微信运行时改造

**Files:**
- Modify: `apps/api/app/services/weixin_runtime.py`
- Modify: `apps/api/app/main.py`（注入 store 与 registry）
- Test: `apps/api/app/tests/test_channels.py`（重写受影响用例）

- [ ] **Step 1: 写失败测试（替换旧用例）**

新增 `apps/api/app/tests/test_weixin_runtime_sdk.py`：

```python
import pytest

from app.db import init_db
from app.services.settings_store import (
    AgentSection,
    ChannelSection,
    PlatformSettings,
)
from app.services.weixin_runtime import PendingBatch, WeixinRuntime


class FakeStore:
    def __init__(self):
        self.settings = PlatformSettings(
            agents=[AgentSection(id="default", name="Assistant")],
            channels=[
                ChannelSection(
                    id="ch1",
                    agent_id="default",
                    status="active",
                    account_id="a1",
                    bot_token="t",
                )
            ],
            channel_conversations={},
        )

    async def get(self):
        return self.settings

    async def update_conversation(self, external_id, conversation):
        self.settings.channel_conversations[external_id] = conversation


class FakeRegistry:
    def __init__(self):
        self.turns = []

    async def submit(self, turn):
        self.turns.append(turn)

    async def interrupt(self, session_id):
        return True

    def is_busy(self, session_id):
        return False


@pytest.mark.asyncio
async def test_flush_pending_submits_turn():
    await init_db()
    registry = FakeRegistry()
    runtime = WeixinRuntime(store=FakeStore(), registry=registry)
    key = ("ch1", "wx-1")
    runtime._pending[key] = PendingBatch(
        texts=["hi"],
        media=[],
        ids=["m1"],
        sender_name="张三",
        context_token="ctx",
        first_at=0.0,
        last_at=0.0,
    )
    await runtime.flush_pending(key)
    assert registry.turns[0].source == "wechat"
    assert registry.turns[0].prompt == "hi"
    assert registry.turns[0].delivery.external_id == "wx-1"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_weixin_runtime_sdk.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`WeixinRuntime` 构造函数增加：

```python
        store: Any = None,
        registry: Any = None,
```

删除 `worker` 依赖（保留参数兼容可暂时不删）。

`flush_pending` 改为：

```python
        settings = await self.store.get()
        channel = next((item for item in settings.channels if item.id == channel_id), None)
        if channel is None or channel.status != "active":
            self._inflight.discard(key)
            return
        result = await channel_intake.ingest(
            store=self.store,
            registry=self.registry,
            channel_id=channel_id,
            external_message_id=batch.ids[0],
            external_id=sender_id,
            display_name=batch.sender_name,
            text=content,
            context_token=batch.context_token,
        )
        if result.reply:
            credentials = await self._load_credentials(channel_id)
            if credentials is not None:
                client = self._clients.get(channel_id) or self._build_client(credentials)
                await self._send_chunk(
                    client,
                    channel_id=channel_id,
                    to_user_id=sender_id,
                    text=result.reply,
                    context_token=batch.context_token,
                )
```

删除 `channel_service.ingest_inbound` 与 `record_inbound_ids` 调用。

`deliver_run` 改为 `deliver(delivery_id: int, *, recovered: bool = False)`：

- 用 `channel_delivery.get(delivery_id)` 取行；
- 状态 `sent` 直接返回；`attempting` 且非 recovered 返回；
- 渠道/联系人信息从 `store.get()` 的 channels 与 channel_conversations 中查；
- 发送前 `mark_attempting`，成功后 `mark_sent`，失败 `mark_pending(delivery_id, error, max_attempts=settings.channel_delivery_max_attempts)`；
- 分块、重试、`RECOVERED_PREFIX` 逻辑保留。

`sweep_deliveries` 改为：

```python
        for delivery in await channel_delivery.list_pending():
            await self.deliver(delivery.id, recovered=delivery.status == "attempting")
```

`_load_credentials` / `_save_sync_buf` / `_touch_last_seen` / `_mark_expired` 改为读写 `self.store` 的 `ChannelSection`：

```python
    async def _save_sync_buf(self, channel_id: str, sync_buf: str) -> None:
        settings = await self.store.get()
        channel = next((item for item in settings.channels if item.id == channel_id), None)
        if channel is None:
            return
        channel.sync_buf = sync_buf
        await self.store.update_channel(channel)
```

`_touch_last_seen` 做 60 秒节流，避免每条消息重写 settings.json。

命令拦截 `_handle_message`：若 `chat_commands.parse_command(text)` 非空，直接调用 `channel_intake.ingest` 并发送 `result.reply`，不再走 `_apply_command`/`worker.cancel`。

`main.py` 装配改为：

```python
        weixin = WeixinRuntime(store=store, registry=registry)
        app.state.weixin = weixin
        cron = CronRuntime(store=store, registry=registry)
        app.state.cron = cron
```

`ClaudeTurnRunner` 构造时注入发送回调：

```python
            runner = ClaudeTurnRunner(
                store,
                deliver_callback=lambda delivery_id: weixin.deliver(delivery_id),
            )
```

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_weixin_runtime_sdk.py apps/api/app/tests/test_channel_images.py -q`
Expected: PASS

- [x] **Step 5: 三次自查并提交**

```bash
git add apps/api/app/services/weixin_runtime.py apps/api/app/main.py apps/api/app/tests/test_weixin_runtime_sdk.py
git commit -m "Move WeChat runtime to SDK sessions"
```

> **质量审查修订：** `mark_attempting` 允许对 `attempting` 行重抢（重启 sweep 需要），发送方必须在进程内用 `_delivering: set[int]` 按 delivery_id 去重，保证同一行同一进程内至多一个发送者；启动/渠道激活 sweep 与轮次结束回调共享该集合。**接入入站服务时：** `flush_pending` 传 `batch.ids`（列表）；命令拦截前先 `flush_pending(key)`，避免命令前缓冲的文本进入新会话；读取联系人绑定用 `f"{channel_id}:{external_id}"`。**引用找回：** `ChannelInbound` 增加 `text` 与 `external_id` 列并写入本批正文/联系人；`channel_intake.resolve_quoted_text` 按 msg_id、时间窗口（-2s/+30s）**且限定同一 external_id** 依次查入站文本与投递内容；`_render_quote` 调它，查不到保留“（引用原文不可用）”。**逐条幂等：** `_handle_message` 在打包前用 `inbound_seen` 剔除已占位 id，避免“旧 id + 新 id 同批”被整体判重丢弃。纯语音/视频等 unsupported 消息在回复前用 `claim_inbound` 占位，防重放。`channel_text` 先复制自 `channel_service`，Task 8 删除旧模块时收口。**质量审查修订（执行时已落实）：** 机器人回复引用用 `coalesce(delivered_at, created_at)` 且取时间窗口内最近一条；永久性 ingest 错误（渠道/Agent 不可用）丢弃批次但保留 `sync_buf`，瞬时错误有限次重试；`SettingsStore.update_channel_fields` 做字段级写入，`_mutate_channel` 不再整对象覆盖；`_inflight` 在 flush 的首个 await 前加入；投递由 `schedule_delivery` 后台任务执行，不占用轮次收尾与并发槽；命令语义按 intent：`/new`、`/clear` 先 flush，`/stop` 丢弃待打包消息，其他命令不 flush。**补充：** `update_channel_fields` 经 `ChannelSection` 校验；会话绑定写回用 compare-and-set（`update_conversation_if_unchanged`，失败重读重试），避免在途 flush 覆盖 `/new`；sweep 通过 `schedule_delivery` 后台发送；`stop_channel` 与 start 共用锁。`sync_buf` 在 Agent 归档但渠道仍 active 时保留旧游标属 best-effort，命令 ingest 失败会丢该命令（用户重发），两点写入方法注释。

---

### Task 5: 定时任务改造

**Files:**
- Modify: `apps/api/app/models.py`（`CronJob.session_id` 改字符串，删 `last_run_id`）
- Modify: `apps/api/app/services/cron_service.py`
- Test: `apps/api/app/tests/test_cron_service.py`（重写）

- [ ] **Step 1: 写失败测试**

```python
import pytest

from app.db import SessionLocal, init_db
from app.models import CronJob
from app.services.cron_service import trigger_job
from app.services.settings_store import AgentSection, PlatformSettings


class FakeStore:
    async def get(self):
        return PlatformSettings(agents=[AgentSection(id="default", name="Assistant")])


class FakeRegistry:
    def __init__(self, busy=False):
        self.turns = []
        self._busy = busy

    async def submit(self, turn):
        self.turns.append(turn)

    def is_busy(self, session_id):
        return self._busy


@pytest.mark.asyncio
async def test_trigger_submits_turn():
    await init_db()
    async with SessionLocal() as db:
        job = CronJob(
            agent_id="default",
            name="daily",
            prompt="盘前简报",
            schedule="0 8 * * *",
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        registry = FakeRegistry()
        fired = await trigger_job(db, job, store=FakeStore(), registry=registry)
        await db.commit()
        assert fired is True
        assert job.session_id is not None
        assert registry.turns[0].source == "cron"
        assert registry.turns[0].prompt == "盘前简报"


@pytest.mark.asyncio
async def test_busy_job_is_skipped():
    await init_db()
    async with SessionLocal() as db:
        job = CronJob(
            agent_id="default",
            name="daily-busy",
            prompt="x",
            schedule="0 8 * * *",
            session_id="33333333-3333-3333-3333-333333333333",
        )
        db.add(job)
        await db.commit()
        registry = FakeRegistry(busy=True)
        fired = await trigger_job(db, job, store=FakeStore(), registry=registry)
        await db.commit()
        assert fired is False
        assert registry.turns == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_cron_service.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`CronJob`：

```python
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
```

删除 `last_run_id` 字段与所有引用。

`trigger_job` 改为接收 `store` 与 `registry`：

```python
async def trigger_job(
    db: AsyncSession,
    job: CronJob,
    *,
    store: Any,
    registry: Any,
    now: datetime | None = None,
    immediate: bool = False,
) -> bool:
    moment = now or utcnow()
    settings = await store.get()
    agent = next((item for item in settings.agents if item.id == job.agent_id), None)
    if agent is None or agent.status != "active":
        if not immediate:
            _advance(job, moment)
        return False

    if not job.session_id:
        job.session_id = str(uuid.uuid4())
    transcript = transcript_path(job.session_id, job.agent_id)
    if not transcript.is_file():
        job.session_id = str(uuid.uuid4())

    if registry.is_busy(job.session_id):
        job.last_skipped_at = moment
        db.add(AuditLog(actor="system", action="cron_skipped",
                        entity="cron_job", entity_id=job.id))
        if immediate:
            raise CronBusy(job.session_id, "running")
        _advance(job, moment)
        return False

    delivery = None
    if job.push_channel_id and job.push_conversation_id:
        conversation = settings.channel_conversations.get(job.push_conversation_id)
        channel = next((item for item in settings.channels if item.id == job.push_channel_id), None)
        if (
            channel is not None
            and channel.agent_id == job.agent_id
            and conversation is not None
            and conversation.channel_id == channel.id
        ):
            delivery = DeliveryRequest(channel_id=channel.id, external_id=job.push_conversation_id)

    await registry.submit(
        Turn(
            session_id=job.session_id,
            agent_id=job.agent_id,
            prompt=job.prompt,
            source="cron",
            delivery=delivery,
        )
    )
    job.last_run_at = moment
    job.last_skipped_at = None
    if not immediate:
        _advance(job, moment)
    return True
```

`create_job` 删除创建 `Session` 的代码，改为直接生成 `session_id=str(uuid.uuid4())`（不落 sessions 表）。

`delete_job` / `detach_session` 删除对 Session 的引用；删除任务不删 transcript。

`run_due_jobs` / `run_job_now` 传入 `store` 与 `registry`；`CronRuntime` 改为接收 runner、store、registry。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_cron_service.py apps/api/app/tests/test_cron_api.py -q`
Expected: PASS（按新语义重写断言）

- [x] **Step 5: 三次自查并提交**

```bash
git add apps/api/app/models.py apps/api/app/services/cron_service.py apps/api/app/routers/cron.py apps/api/app/tests/test_cron_service.py apps/api/app/tests/test_cron_api.py
git commit -m "Move cron jobs to SDK turns"
```

> **质量审查修订：** `cron_jobs.push_conversation_id` 存联系人 `external_id`；读取绑定时用 `f"{channel_id}:{external_id}"`，并校验 `conversation.channel_id == channel.id` 与 Agent 归属。触发时用 `DeliveryRequest(channel_id, external_id)` 交给 runner 建投递行。**质量审查修订（执行时已落实）：** 调度状态先 commit 再 `registry.submit`，submit 失败写 `cron_trigger_failed` 审计且不重试（避免重复推送）；暂停 Agent 的 create/run 返回 400（409 仅用于 busy）而非 404；推送目标要求 `status == "active"` 且 `type == "wechat"`；`push_conversation_id` 迁移 DDL 用 VARCHAR(200)；inactive-agent 分支清空 `last_skipped_at`；`main.py` 暂时不注册 `cron_tools`（工具层在 Task 8 下线）。停机顺序：cron/weixin 先停，再关 registry。

---

### Task 6: 会话清理

**Files:**
- Modify: `apps/api/app/services/session_service.py`
- Modify: `apps/api/app/services/session_registry.py`（增加 busy 查询或复用）
- Test: `apps/api/app/tests/test_session_cleanup.py`（重写）

- [ ] **Step 1: 写失败测试**

```python
import os
import time

import pytest

from app.services import session_service, turn_lease
from app.services.claude_runner import transcript_path
from app.db import SessionLocal  # only if the test needs the lease DB


@pytest.mark.asyncio
async def test_cleanup_deletes_old_transcript_only(tmp_path):
    old = transcript_path("old", "default", home=tmp_path)
    old.parent.mkdir(parents=True)
    old.write_text("{}", "utf-8")
    fresh = transcript_path("fresh", "default", home=tmp_path)
    fresh.write_text("{}", "utf-8")
    running = transcript_path("running", "default", home=tmp_path)
    running.write_text("{}", "utf-8")
    await turn_lease.add("running")
    old_time = time.time() - 400 * 86400
    os.utime(old, (old_time, old_time))
    os.utime(running, (old_time, old_time))
    await session_service.purge_expired_transcripts(
        agent_id="default", retention_days=30, home=tmp_path
    )
    assert not old.exists()
    assert fresh.exists()
    assert running.exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_session_cleanup.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`session_service.py` 保留 `SessionCleanupRuntime` 外壳，新增：

```python
async def purge_expired_transcripts(
    *, agent_id: str, retention_days: int, home: Path | None = None
) -> int:
    if retention_days <= 0:
        return 0
    cutoff = utcnow().timestamp() - retention_days * 86400
    running = set(await turn_lease.list_all())
    project_dir = transcript_dir(agent_id, home=home)
    removed_ids: list[str] = []
    if project_dir.is_dir():
        for path in project_dir.glob("*.jsonl"):
            session_id = path.stem
            if session_id in running:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            path.unlink()
            removed_ids.append(session_id)
    if removed_ids:
        store = get_settings_store()

        def unbind(data: dict) -> None:
            conversations = data.get("channel_conversations") or {}
            for key in list(conversations):
                if conversations[key].get("session_id") in removed_ids:
                    del conversations[key]

        await store.mutate_raw(unbind)
    return len(removed_ids)
```

先在 `apps/api/app/services/claude_runner.py` 增加：

```python
def transcript_dir(agent_id: str, *, home: Path | None = None) -> Path:
    return claude_paths(home)["config"] / "projects" / agent_id


def transcript_path(session_id: str, agent_id: str, *, home: Path | None = None) -> Path:
    return transcript_dir(agent_id, home=home) / f"{session_id}.jsonl"
```

并在 `SettingsStore` 上补一个公开方法：

```python
    async def mutate_raw(self, fn: Callable[[dict[str, Any]], None]) -> None:
        await self._json.update(fn)
```

`SessionCleanupRuntime` 的每轮扫描：

```python
    settings = await store.get()
    days = settings.runtime.session_retention_days
    for agent in settings.agents:
        await purge_expired_transcripts(agent_id=agent.id, retention_days=days)
```

删除旧的 DB 会话删除逻辑、`unlink_session` 的数据库部分（改为删除 `channel_conversations` 中指向该会话的条目）。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_session_cleanup.py -q`
Expected: PASS

- [x] **Step 5: 三次自查并提交**

```bash
git add apps/api/app/services/session_service.py apps/api/app/tests/test_session_cleanup.py
git commit -m "Clean up SDK transcripts by retention"
```

> **质量审查修订（执行时已落实）：** `purge_expired_transcripts` 校验 agent id；先清理产物（截图/投递/解绑，共用 `cleanup_session_artifacts`，每步独立保护）再删 transcript，删除失败可下轮重试；FileNotFound 回退包 try 并清子 Agent 目录；非 UUID 文件名跳过；绕过 `registry.is_busy` 的会话，成功后 `registry.close_session` 释放缓存 client。测试用真实 UUID，并有一条指向临时 `CLAUDE_CONFIG_DIR` 的真实 `delete_session` 集成测试。

---

### Task 7: 配置与记忆接口

> **前置（Task 6 遗留）：** 把旧 `app_configs.session_retention_days` 的值迁移进 `settings.json` 的 `runtime.session_retention_days`（尤其是 0），再切换 `GET/PUT /api/settings` 到 settings store；否则用户此前设置的 0 会静默变成 30。

**Files:**
- Modify: `apps/api/app/routers/app_settings.py`
- Modify: `apps/api/app/routers/knowledge.py`
- Modify: `apps/api/app/routers/agents.py`
- Modify: `apps/api/app/routers/providers.py`
- Modify: `apps/api/app/routers/memories.py`
- Modify: `apps/api/app/routers/skills.py`
- Modify: `apps/api/app/routers/channels.py`
- Modify: `apps/api/app/routers/cron.py`
- Modify: `apps/api/app/routers/system.py`
- Modify: `apps/api/app/routers/tools.py`
- Modify: `apps/api/app/services/knowledge_sync.py`（配置来源）
- Test: `apps/api/app/tests/test_app_settings.py`、`test_knowledge_api.py`、`test_api.py`

- [ ] **Step 1: 写失败测试**

```python
def test_settings_roundtrip(client):
    response = client.put("/api/settings", json={"session_retention_days": 7})
    assert response.status_code == 200
    assert client.get("/api/settings").json()["session_retention_days"] == 7


def test_memory_file_roundtrip(client):
    response = client.put("/api/memories", json={"content": "# 我\n- 偏好中文"})
    assert response.status_code == 200
    assert client.get("/api/memories").json()["content"].startswith("# 我")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_app_settings.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

- `app_settings.py`：`GET/PUT /api/settings` 读写 `store.runtime`，响应字段保持 `session_retention_days`。
- `knowledge.py`：`GET/PUT /api/knowledge` 读写 `store.knowledge`；密码只返回 `has_password`；`POST /api/knowledge/sync` 不变。
- `knowledge_sync.py`：删除 `seed_knowledge_config` 的 DB 逻辑，改为从 store 读取；状态文件不变。
- `agents.py`：列表/创建/更新/删除读写 `store.agents`；创建 Agent 时先 `workspace_for(agent.id).mkdir(parents=True, exist_ok=True)`；`DELETE /api/agents/{id}/permanent` 删除 Agent 配置、workspace 目录与 `claude/projects/<id>`。
- `providers.py`：`GET/PUT /api/providers` 映射到默认 Agent 的 `provider` 节；`api_key` 只返回 `has_api_key`。
- `memories.py`：改为读写 `claude/CLAUDE.md`：

```python
@router.get("", response_model=dict)
async def read_memory(request: Request) -> dict:
    path = claude_paths()["config"] / "CLAUDE.md"
    return {"content": path.read_text("utf-8") if path.is_file() else ""}

@router.put("", response_model=dict)
async def write_memory(payload: dict, request: Request) -> dict:
    path = claude_paths()["config"] / "CLAUDE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(payload.get("content") or ""), "utf-8")
    os.replace(tmp, path)
    return {"saved": True}
```

- `skills.py`：技能的增删与启用列表读写 `store.agents[].enabled_skills`。
- `channels.py`：渠道列表/创建/更新/删除读写 `store.channels`；二维码登录状态来自 `WeixinRuntime` 的内存 `_login_sessions`；`GET /api/channels/{id}/conversations` 从 `store.channel_conversations` 过滤 `channel_id` 等于该渠道的条目；删除渠道只删配置与联系人绑定，不删 transcript。`message_preview` 从 `channel_text` 导入。
- `cron.py`：读取任务时 Agent 与推送目标从 settings 校验；不再创建/查询 Session 行；响应里的 `session_id` 直接返回 UUID。
- `system.py`：渠道数、Agent 数、版本等从 settings store 与运行内存读取，不再统计旧表。
- `tools.py`：平台工具列表改为静态声明（工具层迁移前只有 `session` 相关能力暂缺），不查询 Agent 表。

- [ ] **Step 4: 运行测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_app_settings.py apps/api/app/tests/test_knowledge_api.py apps/api/app/tests/test_api.py -q`
Expected: PASS（按新语义调整断言）

- [x] **Step 5: 三次自查并提交**

```bash
git add apps/api/app/routers apps/api/app/services/knowledge_sync.py apps/api/app/tests
git commit -m "Move settings, knowledge and memory APIs to files"
```

> **质量审查修订：** 渠道联系人接口读取 `settings.channel_conversations` 时使用 `f"{channel_id}:{external_id}"` 新 key，并返回 `external_id`（`push_conversation_id` 用它）；不要假设 key 就是 external_id。
>
> **质量审查修订（执行时已落实）：** 前端 CronPage 改用 `external_id` 并补端到端契约测试；记忆页改为 `claude/CLAUDE.md` 单文档编辑器；知识库 env 种子恢复 `enabled` 与 `KB_SYNC_INTERVAL_SECONDS`；`runtime.legacy_import_done` 保证迁移只发生一次（文件已存在也生效）；`channel_intake` 写入/刷新绑定 `updated_at`；记忆写入要求 `content`、保留 `.bak`、线程化 IO；永久删除 Agent 纳入 cron 会话的 busy/close 与实际删除渠道集合。已知 Minor：迁移标记无法区分「旧表缺失」与「读取错误」（低风险，记录）；MemoryPage 离开页面不提示未保存修改（后续前端清理）。
>
> **质量审查修订（后续）：** 旧 `Run` 表与 `channel_run_links` 的级联清理不在本任务补代码。本分支仍启动旧 `RunWorker`，永久删除 Agent 只清 settings、workspace、`claude/projects/<id>`、渠道与定时任务；旧表孤儿随 Task 8 整表删除一并消失。因此部署必须完成 Task 8，不能在本分支中途上线。Task 8 的删除清单同时加入 `apps/api/app/services/app_config.py` 与 `schemas.py` 的 `MemoryCreate` / `MemoryUpdate` / `MemoryOut`。

---

### Task 8: 旧栈下线

**Files:**
- Modify: `apps/api/app/models.py`、`apps/api/app/db.py`、`apps/api/app/main.py`、`apps/api/pyproject.toml`
- Delete: `executor.py`、`skill_middleware.py`、`rubric.py`、`model_retry.py`、`memory_service.py`、`memory_tools.py`、`memory_organizer.py`、`session_tools.py`、`session_search.py`、`channel_service.py`、`run_service.py`、`app_config.py`
- Delete schemas: `MemoryCreate` / `MemoryUpdate` / `MemoryOut`（`schemas.py` 内删除；`MemoryFileOut` / `MemoryFileUpdate` 保留）
- Delete routers: `runs.py`（已在核心计划移除注册，文件删除）
- Delete tests: `test_executor.py`、`test_executor_resume.py`、`test_model_retry.py`、`test_rubric.py`、`test_memory_tools.py`、`test_memory_retrieval.py`、`test_memory_organizer.py`、`test_session_search.py`、`test_messages_page.py`、`test_channel_delivery.py`（由新测试替代）

- [ ] **Step 1: 精简模型**

`models.py` 只保留：`AuditLog`、`CronJob`、`ChannelInbound`、`ChannelDelivery`（新结构）、`Backtest`、`RunningSession`。

- [ ] **Step 2: 精简 db.py**

删除 `messages_fts` 与触发器的创建/回填代码；`init_db` 只 `create_all` 保留模型。

- [ ] **Step 3: 删除模块与测试**

删除前先做两处搬迁：

1. 把 `channel_service.split_reply` 与 `message_preview` 移到新文件 `apps/api/app/services/channel_text.py`，`weixin_runtime.py` 的调用改为 `from app.services.channel_text import split_reply`。
2. 把 `chat_commands.py` 精简为只保留 `is_command_text` 与 `parse_command`（删除 `execute_command`、DB 导入与 memory 相关实现），`routers/chat.py` 只使用这两个函数。

然后执行：

```bash
git rm apps/api/app/services/executor.py apps/api/app/services/skill_middleware.py \
  apps/api/app/services/rubric.py apps/api/app/services/model_retry.py \
  apps/api/app/services/memory_service.py apps/api/app/services/memory_tools.py \
  apps/api/app/services/memory_organizer.py apps/api/app/services/session_tools.py \
  apps/api/app/services/session_search.py apps/api/app/services/channel_service.py \
  apps/api/app/services/run_service.py apps/api/app/routers/runs.py
git rm apps/api/app/tests/test_executor.py apps/api/app/tests/test_executor_resume.py \
  apps/api/app/tests/test_model_retry.py apps/api/app/tests/test_rubric.py \
  apps/api/app/tests/test_memory_tools.py apps/api/app/tests/test_memory_retrieval.py \
  apps/api/app/tests/test_memory_organizer.py apps/api/app/tests/test_session_search.py \
  apps/api/app/tests/test_messages_page.py apps/api/app/tests/test_channel_delivery.py \
  apps/api/app/tests/test_worker.py apps/api/app/tests/test_weixin_runtime.py \
  apps/api/app/tests/test_channel_images.py apps/api/app/tests/test_cron_push.py \
  apps/api/app/tests/test_cron_tools.py apps/api/app/tests/test_skill_authoring.py \
  apps/api/app/tests/test_workspace.py apps/api/app/tests/test_chat_commands.py \
  apps/api/app/tests/test_migrations.py
```

工具层测试（覆盖仍保留但本版未接入 Agent 的模块）一并删除，待工具层计划重写：
`test_agent_skills.py`、`test_allocation_tools.py`、`test_backtest_tools.py`、`test_browser_tools.py`、`test_chan_tools.py`、`test_skill_tools.py`、`test_skills_integration.py`、`test_tools.py`、`test_fetch_tool.py`、`test_search_tool.py`、`test_trade_journal_tools.py`。

```bash
git rm apps/api/app/tests/test_agent_skills.py apps/api/app/tests/test_allocation_tools.py \
  apps/api/app/tests/test_backtest_tools.py apps/api/app/tests/test_browser_tools.py \
  apps/api/app/tests/test_chan_tools.py apps/api/app/tests/test_skill_tools.py \
  apps/api/app/tests/test_skills_integration.py apps/api/app/tests/test_tools.py \
  apps/api/app/tests/test_fetch_tool.py apps/api/app/tests/test_search_tool.py \
  apps/api/app/tests/test_trade_journal_tools.py
```

若仍有测试因导入已删除模块而失败，以 `pytest --collect-only -q` 的输出为准继续删除，并在提交信息中列出。

- [ ] **Step 4: 清理依赖与导入**

`pyproject.toml` 删除：

```toml
  "deepagents>=0.7.32,<0.8",
  "langgraph>=1.2.11,<1.3",
  "langgraph-checkpoint-sqlite>=3.1,<4",
  "langchain-openai>=1.0,<2",
  "langchain-quickjs>=0.3.9,<0.4",
```

保留 `langchain-core`（工具层迁移前，工具模块仍导入它，但不再注册到 Agent）。`main.py` 删除 `allocation_tools`/`backtest_tools`/`chan_tools`/`cron_tools`/`trade_journal_tools` 的导入与旧 worker/cron 装配，同时删除：

- `seed_default_provider`、`seed_default_agent`、`sync_default_agent_instructions`；
- `RunWorker`、`DeepAgentExecutor`、`memory_service`、`session_search` 的导入。

（`seed_knowledge_config` 已在 Task 7 随 `knowledge_sync.py` 一并删除。）

路由注册保留：`auth`、`agents`、`providers`、`app_settings`、`channels`、`sessions`、`chat`、`cron`、`knowledge`、`memories`、`skills`、`tools`、`system`、`market`、`backtests`、`chan`、`trade_journal`、`allocation`；不再注册 `runs`。

**已知延迟项：** 渠道运行期的截图实时推送依赖 `browser_screenshot` 工具，工具层未迁移前该能力不可用；待工具层恢复时接回 `channel_images`。

- [ ] **Step 5: 导入清扫**

```bash
grep -rn "from app.models import" apps/api/app --include="*.py"
grep -rn "app.services.run_service\|app.services.memory_service\|app.services.session_search\|app.services.channel_service\|app.services.executor" apps/api/app --include="*.py"
```

所有仍引用已删除模型或服务的 import 必须改为 settings store / 新服务；若有路由遗漏，按 Task 7 的模式移植并补测试。

- [ ] **Step 6: 运行测试并提交**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`
Expected: PASS

```bash
git add -A
git commit -m "Remove deepagents/LangGraph stack and legacy tables"
```

> **质量审查修订：** `ChannelInbound` 保留，但必须去掉指向 `channels` 的 FK 以及 `conversation_id/message_id/run_id` 旧列（`create_all` 在删除 `Channel` 模型后会因悬空 FK 抛 `NoReferencedTableError`）；`session_id` 使用 `String(36)`。`channel_deliveries` 同样不再引用旧表。
>
> **质量审查修订（执行时已落实）：** `channel_inbounds` 增加 stale-schema 检测与重建（旧库缺 `text`/`external_id` 时自愈）；`agents[].instructions` 以 `{"type":"preset","preset":"claude_code","append":...}` 注入 SDK 系统提示词；`close_agent_clients` 在 Agent/Provider 更新后淘汰空闲会话 client；`/api/tools` 如实声明 SDK 内置工具与未接入工具；默认提示词同步当前能力并恢复 shell 安全提醒；删除整理记忆残留（seed、`/memory` 命令、前端入口）。全量 571 passed / 3 skipped。
>
> **遗留（Task 9 / 工具层）：** 升级后旧 home 的 `memory-organizer` 技能不会被 seed 清理（Task 9 处理）；`close_agent_clients` 无超时预算、busy 会话要等下次设置变更；`_session_agents` 条目持续到会话删除；提示词仍提到 `/skills/<name>/SKILL.md` 路径；少量死样式。

---

### Task 9: 文档与最终验证

**Files:**
- Modify: `docs/intent.md`、`CLAUDE.md`、`README.md`
- Modify: `apps/web/src/types.ts`（CronJob 去掉 `last_run_id`/`last_run_status`）、`apps/web/src/api.ts`（cron 创建/删除/手动运行的新响应类型）、`apps/web/src/components/CronPage.tsx`（去掉 `last_run_status` 展示，按新字段适配）

- [x] **Step 1: 同步文档**

写明渠道、定时、清理、配置接口的新形态；删除记忆系统、Run 历史、搜索、rubric、eval 的描述；更新数据表清单与运行时目录。

- [x] **Step 2: 后端全量测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`
Expected: PASS

- [x] **Step 3: 前端构建**

Run: `cd apps/web && npm run build`
Expected: PASS

- [x] **Step 4: 验收清单**

逐条执行核心 spec 第 13 节 10 条验收项，记录结果。

- [x] **Step 5: 三次自查并提交**

```bash
git add docs/intent.md CLAUDE.md README.md apps/web/dist
git commit -m "Document SDK-native channels and cron"
```

**验收结果（核心 spec 第 13 节）：**

| # | 结果 | 本地验证 / 需生产验证步骤 |
|---|---|---|
| 1 | 本地通过；真实模型需生产验证 | 本地：`test_chat_api.py`（send/interrupt/stream/messages）、`test_session_catalog.py`（list/messages）、`test_claude_runner.py::test_runner_maps_stream_and_completes`。生产：登录 → 新对话 → 发送“回复 OK” → 观察流式 → 刷新后历史可读。 |
| 2 | 本地通过 | `test_session_registry.py::test_interrupt_*`、`test_claude_runner.py::test_interrupt_*` 与租约断言；取消后 `running_sessions` 无该会话。 |
| 3 | 本地通过；真实 restart 需生产验证 | `test_recovery.py`（11 项）、`test_turn_lease.py`、`test_claude_runner.py::test_shutdown_cancellation_keeps_lease_for_recovery`。生产：发长任务 → `systemctl --user restart agent-command-center.service` → 会话自动续跑 → `running_sessions` 清空。 |
| 4 | 本地通过；真实 iLink 需生产验证 | `test_weixin_runtime_sdk.py`、`test_channel_intake.py`、`test_channel_delivery_service.py`。生产：扫码 → 微信发消息 → 5 秒内收到回复 → 断网重放不重复处理。 |
| 5 | 本地通过；真实微信需生产验证 | `test_channel_intake.py::test_help_command` / `test_new_command_rebinds_conversation` / `test_clear_command_rebinds_conversation` / `test_status_command_reports_busy_and_idle` / `test_stop_command_delegates_to_registry`；`test_weixin_runtime_sdk.py::test_new_command_flushes_pending_and_sends_reply_without_a_turn` / `test_stop_command_discards_pending_without_flushing` / `test_help_and_status_commands_leave_the_batch_window_alone`。生产：微信发送 `/new`、`/status`、`/stop` 各一次。 |
| 6 | 本地通过；真实调度/推送需生产验证 | `test_cron_service.py`（due/busy/audit/session-id/push）、`test_cron_api.py`（CRUD/run/busy/push contract）。生产：创建 1 分钟后任务 → 到点检查会话与 `audit_log` → 配置微信推送并确认送达。 |
| 7 | 本地通过；真实到期清理需生产验证 | `test_session_cleanup.py`（old/fresh/running/screenshots/deliveries/bindings/retention=0/non-UUID/artifacts-before-transcript）。生产：把保留天数设为 1 → 构造旧 mtime 会话 → 运行清理 → 确认 transcript 删除且 `claude/projects/*/memory/` 保留。 |
| 8 | 本地通过 | `test_backtests_api.py`、`test_chan_api.py`、`test_allocation_api.py`、`test_trade_journal_api.py`、`test_knowledge_api.py` 全通过；`npm run build` 通过。生产：依次打开回测、交易记录、资产配置、缠论、知识库页面并做一次读写/刷新。 |
| 9 | 通过 | `grep -rn "deepagents\|langgraph\|langchain_openai" apps/api` → 无输出（exit 1）；`git grep` 同样无结果。本地旧 egg-info 曾残留，已重新 editable 安装刷新。 |
| 10 | 需生产验证（真实 DeepSeek key） | ① 记忆页保存 `claude/CLAUDE.md` → 新建会话问个人偏好，回答体现画像；② 让 Agent 记录工作笔记 → 新建会话询问，验证 auto memory 跨会话；③ **Auto Dream 单独验证**：观察 `claude/projects/<project>/memory/` 是否生成/整理，确认 Auto Dream 在 DeepSeek 端点下可运行；若不可运行，记录现象并降级为不依赖它，只保留 auto memory 的普通读写。 |

> **质量审查结果（Task 9）：**
> - 文档按当前实现重写：`docs/intent.md` 重新编号为 F1–F16（旧 F1–F90 归档在 git 历史），`CLAUDE.md`、`README.md` 与 `deploy/.env.example` 同步渠道、定时、清理、配置接口、六张表与运行时目录；删除记忆系统、Run 历史、搜索、rubric、eval 描述，写明本版不包含项与升级损失。
> - 代码小修：`seed_default_skills` 清理已移除且未被用户修改的内置技能（`memory-organizer`），用户改过的保留并停止跟踪；默认提示词不再提 `/skills/<name>/SKILL.md`；删除 `.message-command-*` 死样式；前端 CronJob 去掉 `last_run_id`/`last_run_status`，`api.ts` 的删除/立即运行响应改为 `CronJob`；顺带删除未使用的 `session_title` 常量与函数。
> - 三次自查发现并修正：提示词测试断言、删除 Agent 会连工作区一起删除（原文档误写“保留”）、前端没有单条消息重试按钮（改为“重新发送”）、`/api/settings` 与 `/api/cron-jobs` 的存储位置表述、CronPage 的 Run 措辞。规格复审后补充：文档与前端删除 `turn_error`、修正“删 app.db 会丢微信绑定”、写明本版不做数据迁移（旧编号至 F90）、统一“新库只创建六张表”、区分渠道 `has_credentials` 与 `has_api_key`/`has_password`、补全 attempt 补发文案、删除 `data/checkpoints` 死目录代码与 `.run-trace-rubric*` 死样式、升级损失清单与推送目标删除语义。质量复审后补充：技能 UI/工具页改为当前事实（启用列表不门控工具、`skill_save` 未接入）、退役技能只删被跟踪的 `SKILL.md` 并保留用户附加文件、补齐 seed 边界测试、删除前端 `turn_error` 死代码与 `detach_session` 桩、`project-commit` 技能更新为新栈、`/api/system` 的 browser 能力改为未接入、核实默认工具集会随 bundled CLI/插件环境漂移（本地 21 个，另一环境 23 个含 DesignSync/ToolSearch），API 用已知超集并注明以运行时 init frame 为准、文档只举例不再复制完整列表、补充退役技能 symlink 边界测试、收口多 Agent 与密钥种子措辞。
> - 验证：后端 579 passed / 3 skipped；前端构建通过；`grep -rn "deepagents\|langgraph\|langchain_openai" apps/api` 无输出（exit 1），`git grep` 同样无结果。真实 DeepSeek 流式/resume/auto memory/Auto Dream 与微信投递、定时推送需生产验证。
> - 遗留（下一版/工具层）：
>   - `/api/system` 仍返回 `counts.messages` / `counts.runs`（恒为 0）与 `max_history_messages` 兼容字段，前端已不展示；
>   - `attachments.build_model_content` 与图片压缩路径生产无调用方（仅测试覆盖），待图片输入方案或工具层确定后处理；
>   - `browser_tools.playwright_installed()` 已无调用方，随工具层接入时一并处理；
>   - 除 cron-scheduler 外的技能正文仍描述待工具层接入的能力；
>   - `langchain-core` 暂留；
>   - `close_agent_clients` 无超时预算；
>   - `.claude/skills/project-commit/SKILL.md` 已按当前 SDK 栈更新（settings.json、六张表、升级注意事项、dist 提交流程）。

---

## 自审记录

- 覆盖 spec：渠道（Task 1/3/4）、定时（Task 5）、清理（Task 6）、配置与记忆（Task 7）、旧表与依赖（Task 8）、文档（Task 9）。
- 与新核心计划的接口一致：`Turn` 增加 `delivery`，`ClaudeTurnRunner` 增加 `deliver_callback`，其余签名不变。
- 未覆盖：工具层（`add_dirs`、技能工具到 MCP、QuickJS eval、rubric）——按 spec 第 12 节不在本版。
