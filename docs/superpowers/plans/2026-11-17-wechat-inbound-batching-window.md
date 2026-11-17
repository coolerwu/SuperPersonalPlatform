# 微信入站打包窗口调整实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 微信入站打包从「静默 5 秒、总上限 15 秒」改为「静默 6 秒、无总时长上限」，超长输入仍立即打包。

**Architecture:** 只改配置默认值与 `WeixinRuntime` 的延迟计算：`channel_inbound_batch_seconds` 默认 6.0，`channel_inbound_batch_max_seconds` 改为 `float | None` 默认 `None`（无上限）；把 `_schedule_flush` 里的延迟计算抽成可单测的 `_flush_delay`。不中断 turn、不改 registry / intake / 投递。

**Tech Stack:** FastAPI（Python 3.12）、pydantic-settings、pytest、asyncio。

**依据设计：** `docs/superpowers/specs/2026-11-17-wechat-inbound-batching-window-design.md`（已提交 `c5585ff`）

## 执行前

1. 这个 checkout 可能被并行会话共用。执行前确认 `git status --short` 干净，且当前分支是预期的 `main`。
2. 不要提交与本计划无关的改动。

## 文件结构

- `apps/api/app/config.py`：两个微信打包字段的默认值/类型（第 100–101 行）。
- `apps/api/app/services/weixin_runtime.py`：构造函数解析 `batch_max_seconds`（第 120–129 行）；新增 `_flush_delay`，`_schedule_flush` 改用它（第 267–286 行）。
- `apps/api/app/tests/test_config_defaults.py`（新建）：`Settings` 默认值契约。
- `apps/api/app/tests/test_weixin_runtime_sdk.py`（追加测试）：`_flush_delay` 与构造函数 wiring。
- 文档：`docs/intent.md`（第 37、177、192 行）、`CLAUDE.md`（第 25 行）、`README.md`（第 164、305–306 行）、本设计文档的状态行。

---

### Task 1: 配置默认值

**Files:**
- Modify: `apps/api/app/config.py:100-101`
- Create: `apps/api/app/tests/test_config_defaults.py`

- [ ] **Step 1: 写失败的测试**

新建 `apps/api/app/tests/test_config_defaults.py`：

```python
"""Default-value contracts for RuntimeSettings.

Read the field defaults instead of instantiating Settings with no env, so a
developer's local ``.env`` cannot change what this test asserts.
"""

from app.config import Settings


def test_inbound_batching_defaults():
    fields = Settings.model_fields
    assert fields["channel_inbound_batch_seconds"].default == 6.0
    assert fields["channel_inbound_batch_max_seconds"].default is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_config_defaults.py -v`
Expected: FAIL，断言 `5.0 == 6.0`。

- [ ] **Step 3: 修改配置默认值**

`apps/api/app/config.py` 第 100–101 行由：

```python
    channel_inbound_batch_seconds: float = 5.0
    channel_inbound_batch_max_seconds: float = 15.0
```

改为：

```python
    # A batch flushes after this many seconds without a new message from the
    # same contact. ``channel_inbound_batch_max_seconds`` optionally caps how
    # long repeated messages may keep postponing the flush; ``None`` means no
    # ceiling (an explicit 0 still flushes immediately).
    channel_inbound_batch_seconds: float = 6.0
    channel_inbound_batch_max_seconds: float | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_config_defaults.py -v`
Expected: PASS（1 passed）。

---

### Task 2: 运行时延迟计算与 `None` 上限

**Files:**
- Modify: `apps/api/app/services/weixin_runtime.py:120-129`（构造函数）
- Modify: `apps/api/app/services/weixin_runtime.py:267-286`（`_schedule_flush` 与新增 `_flush_delay`）
- Test: `apps/api/app/tests/test_weixin_runtime_sdk.py`（文件末尾追加）

- [ ] **Step 1: 写失败的测试**

在 `apps/api/app/tests/test_weixin_runtime_sdk.py` 末尾追加（文件顶部需要新增 `from types import SimpleNamespace`）：

```python
def _delay_batch(*, first_at: float, last_at: float) -> PendingBatch:
    return PendingBatch(
        texts=["hi"],
        media=[],
        ids=["delay-1"],
        sender_name="Alice",
        context_token=None,
        first_at=first_at,
        last_at=last_at,
    )


def test_flush_delay_waits_for_idle_window_without_ceiling():
    runtime = _runtime(
        FakeStore([_channel()]), FakeRegistry(), client=FakeClient(), batch_seconds=6.0
    )
    runtime.batch_max_seconds = None
    batch = _delay_batch(first_at=0.0, last_at=8.0)
    assert runtime._flush_delay(batch, now=10.0) == 4.0


def test_flush_delay_restarts_with_each_new_message():
    runtime = _runtime(
        FakeStore([_channel()]), FakeRegistry(), client=FakeClient(), batch_seconds=6.0
    )
    runtime.batch_max_seconds = None
    batch = _delay_batch(first_at=0.0, last_at=9.5)
    assert runtime._flush_delay(batch, now=10.0) == 5.5


def test_flush_delay_applies_explicit_ceiling():
    runtime = _runtime(
        FakeStore([_channel()]), FakeRegistry(), client=FakeClient(), batch_seconds=6.0
    )
    runtime.batch_max_seconds = 15.0
    batch = _delay_batch(first_at=0.0, last_at=13.5)
    assert runtime._flush_delay(batch, now=14.0) == 1.0


def test_flush_delay_zero_ceiling_is_immediate():
    runtime = _runtime(
        FakeStore([_channel()]), FakeRegistry(), client=FakeClient(), batch_seconds=6.0
    )
    runtime.batch_max_seconds = 0.0
    batch = _delay_batch(first_at=0.0, last_at=9.0)
    assert runtime._flush_delay(batch, now=10.0) == -10.0


def test_runtime_reads_unbounded_ceiling_from_settings(monkeypatch):
    monkeypatch.setattr(
        weixin_runtime_module,
        "settings",
        SimpleNamespace(
            channel_inbound_batch_seconds=6.0,
            channel_inbound_batch_max_seconds=None,
        ),
    )
    runtime = WeixinRuntime(
        store=FakeStore([_channel()]),
        registry=FakeRegistry(),
        client_factory=lambda **kwargs: FakeClient(),
    )
    assert runtime.batch_seconds == 6.0
    assert runtime.batch_max_seconds is None


def test_batch_enqueue_flushes_immediately_when_over_char_limit(monkeypatch):
    runtime = _runtime(
        FakeStore([_channel()]), FakeRegistry(), client=FakeClient(), batch_seconds=6.0
    )
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        runtime, "_schedule_flush", lambda key, delay=None: scheduled.append((key, delay))
    )
    channel = _channel()
    runtime._enqueue(
        _credentials(channel),
        sender_id="wx-1",
        message_id="big-1",
        text="字" * (weixin_runtime_module.settings.channel_inbound_max_chars + 1),
        media=[],
        context_token=None,
        sender_name="Alice",
    )
    assert scheduled and scheduled[0][1] == 0


def test_batch_enqueue_flushes_immediately_when_batching_disabled(monkeypatch):
    runtime = _runtime(
        FakeStore([_channel()]), FakeRegistry(), client=FakeClient(), batch_seconds=0.0
    )
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        runtime, "_schedule_flush", lambda key, delay=None: scheduled.append((key, delay))
    )
    channel = _channel()
    runtime._enqueue(
        _credentials(channel),
        sender_id="wx-1",
        message_id="off-1",
        text="hi",
        media=[],
        context_token=None,
        sender_name="Alice",
    )
    assert scheduled and scheduled[0][1] == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_weixin_runtime_sdk.py -k "flush_delay or unbounded_ceiling or batch_enqueue" -v`
Expected: FAIL，`AttributeError: 'WeixinRuntime' object has no attribute '_flush_delay'`（以及 `batch_max_seconds` 为 `15.0` 时的断言失败）。两个 `batch_enqueue` 测试此时应已通过（校验的是既有行为）。

- [ ] **Step 3: 修改构造函数**

`apps/api/app/services/weixin_runtime.py` 第 120–129 行由：

```python
        self.batch_seconds = (
            settings.channel_inbound_batch_seconds
            if batch_seconds is None
            else max(0.0, batch_seconds)
        )
        self.batch_max_seconds = (
            settings.channel_inbound_batch_max_seconds
            if batch_max_seconds is None
            else max(0.0, batch_max_seconds)
        )
```

改为：

```python
        self.batch_seconds = (
            settings.channel_inbound_batch_seconds
            if batch_seconds is None
            else max(0.0, batch_seconds)
        )
        # ``None`` from settings means no total-duration ceiling; the idle
        # window alone decides when the batch flushes.
        resolved_max = (
            settings.channel_inbound_batch_max_seconds
            if batch_max_seconds is None
            else max(0.0, batch_max_seconds)
        )
        self.batch_max_seconds = (
            None if resolved_max is None else max(0.0, resolved_max)
        )
```

- [ ] **Step 4: 新增 `_flush_delay` 并改 `_schedule_flush`**

在 `_schedule_flush` 方法之前插入：

```python
    def _flush_delay(self, batch: PendingBatch, now: float) -> float:
        """Seconds until this batch flushes.

        Every new message restarts the idle window; an optional total-duration
        ceiling bounds how long repeated messages may postpone the flush. A
        ceiling of 0 yields a non-positive delay (the caller clamps to 0).
        """
        idle_delay = self.batch_seconds - (now - batch.last_at)
        if self.batch_max_seconds is None:
            return idle_delay
        return min(idle_delay, self.batch_max_seconds - (now - batch.first_at))
```

`_schedule_flush` 内由：

```python
        if delay is None:
            now = time.monotonic()
            delay = min(
                self.batch_seconds - (now - batch.last_at),
                self.batch_max_seconds - (now - batch.first_at),
            )
```

改为：

```python
        if delay is None:
            delay = self._flush_delay(batch, time.monotonic())
```

- [ ] **Step 5: 运行新测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_weixin_runtime_sdk.py -k "flush_delay or unbounded_ceiling or batch_enqueue" -v`
Expected: PASS（7 passed）。

- [ ] **Step 6: 运行整个微信运行时测试文件确认无回归**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_weixin_runtime_sdk.py -v`
Expected: 全部 PASS。

---

### Task 3: 文档同步

**Files:**
- Modify: `docs/intent.md:37,177,192`
- Modify: `CLAUDE.md:25`
- Modify: `README.md:164,305-306`
- Modify: `docs/superpowers/specs/2026-11-17-wechat-inbound-batching-window-design.md`（状态行）

- [ ] **Step 1: 更新 `docs/intent.md`**

第 37 行把「5 秒打包」改为「入站打包」：

```markdown
6. 微信个人号渠道：扫码登录、iLink 长轮询、入站打包、命令、引用找回、图片/文件、回复投递与补发。
```

第 177 行改为：

```markdown
   - 同一联系人连续消息在 `CHANNEL_INBOUND_BATCH_SECONDS`（默认 6 秒静默，无总时长上限；`CHANNEL_INBOUND_BATCH_MAX_SECONDS` 显式配置时仍生效）内打包成一个 turn；累计超过 8000 字时立即打包。
```

第 192 行改为：

```markdown
- 微信入站 → 静默 6 秒打包 → 回复发回同一联系人；断网重放不重复处理。
```

- [ ] **Step 2: 更新 `CLAUDE.md` 第 25 行**

把「同一联系人 5 秒打包」改为「同一联系人静默 6 秒打包（无总时长上限）」。

- [ ] **Step 3: 更新 `README.md`**

第 164 行改为：

```markdown
- 同一联系人的连续消息在最后一条之后静默 6 秒合并成一个 turn（默认无总时长上限；累计超过 8000 字会立即打包）；打包窗口内不保存 `sync_buf`，服务重启时由 iLink 重放消息，重复消息按 `external_message_id` 幂等吸收。
```

第 305–306 行的环境变量表改为：

```markdown
| `CHANNEL_INBOUND_BATCH_SECONDS` | 同一联系人连续消息的打包窗口（秒，0 关闭） | `6` |
| `CHANNEL_INBOUND_BATCH_MAX_SECONDS` | 连续消息不断刷新时的最长打包时间（秒，留空不限制） | （不限制） |
```

- [ ] **Step 4: 更新设计文档状态行**

`docs/superpowers/specs/2026-11-17-wechat-inbound-batching-window-design.md` 中：

```markdown
- 状态：设计已确认，待实现
```

改为：

```markdown
- 状态：已实现（2026-11-17）
```

---

### Task 4: 全量验证与提交

- [ ] **Step 1: 运行后端全部测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests`
Expected: 全部 PASS；若有与本次无关的既有失败，先停下来报告，不要掩盖。

- [ ] **Step 2: 运行前端构建**

Run: `cd apps/web && npm run build`
Expected: 构建成功（本次不改前端，但按项目约定提交前必须跑；若 `dist` 无变化则不会出现在提交里）。

- [ ] **Step 3: 检查改动范围**

Run: `git status --short`
Expected: 只有 `apps/api/app/config.py`、`apps/api/app/services/weixin_runtime.py`、`apps/api/app/tests/test_config_defaults.py`、`apps/api/app/tests/test_weixin_runtime_sdk.py`、`docs/intent.md`、`CLAUDE.md`、`README.md`、设计文档、本计划文档。

- [ ] **Step 4: 按项目约定提交并推送**

执行 `.claude/skills/project-commit/SKILL.md` 的流程（`git add .`、简洁的祈使句提交信息、`git push origin HEAD`）。部署只在用户明确要求时进行。

## 验收

- 新消息在最后一条之后静默 6 秒触发打包；持续发送时不会因总时长被强制打包（直到出现 6 秒静默或累计超 8000 字）。
- `.env` 显式设置 `CHANNEL_INBOUND_BATCH_SECONDS` / `CHANNEL_INBOUND_BATCH_MAX_SECONDS` 仍按数值生效，`0` 仍是立即打包。
- 后端测试与前端构建通过；文档与行为一致。
