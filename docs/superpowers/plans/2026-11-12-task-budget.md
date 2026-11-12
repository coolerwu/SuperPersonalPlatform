# 单轮任务预算（TaskBudget）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给每个 turn 可选地注入 SDK `task_budget`，让模型知道本轮剩余 token 预算并主动收敛，而不是只能等 900 秒硬超时被杀。

**Architecture:** 在 `data/settings.json` 的 `runtime` 增加 `task_budget_tokens`（0 = 关闭，非 0 时 20000–1000000）。`ClaudeTurnRunner.run()` 每轮读设置，把值传给 `_client()`；`_client()` 在构建 `ClaudeAgentOptions` 时写入 `task_budget={"total": N}`，并把值纳入 client 配置指纹，使设置变更后下一轮自动重建缓存 client。前端配置页「运行环境」表单增加一个输入框，与会话保留天数一起保存。

**Tech Stack:** FastAPI + Pydantic v2（apps/api）、Claude Agent SDK 0.2.180（`ClaudeAgentOptions.task_budget` → CLI `--task-budget`）、React + Vite + TypeScript（apps/web）。

**背景与风险:** 该能力经 Anthropic beta `task-budgets-2026-03-13` 下发。默认 Provider 是 DeepSeek 的 Anthropic 兼容端点，是否接受该 beta 未验证，因此默认关闭（0）。上线后启用前先发一条短消息验证；若 turn 因 400/beta 报错失败，把值改回 0 即可恢复。

---

## 文件结构

| 文件 | 职责 | 改动 |
| --- | --- | --- |
| `docs/intent.md` | 需求唯一来源 | 新增 F22 |
| `apps/api/app/services/settings_store.py` | 平台设置模型与持久化 | `RuntimeSection` 增加字段与范围校验 |
| `apps/api/app/schemas.py` | HTTP 入参模型 | `AppSettingsUpdate` 增加字段与范围校验 |
| `apps/api/app/routers/app_settings.py` | `GET/PUT /api/settings` | 输出与写入新字段 |
| `apps/api/app/services/claude_runner.py` | 执行核 | 指纹与 `ClaudeAgentOptions` 接入预算 |
| `apps/api/app/tests/test_app_settings.py` | 设置 API 测试 | 新字段的默认值/范围/落盘 |
| `apps/api/app/tests/test_claude_runner.py` | 执行核测试 | 预算选项、默认关闭、变更重建、指纹 |
| `apps/api/app/tests/test_settings_store.py` | 设置存储测试 | 非法规避与持久化 |
| `apps/web/src/types.ts` | 前端类型 | `AppSettings` 新字段 |
| `apps/web/src/api.ts` | 前端请求 | `updateSettings` 载荷 |
| `apps/web/src/components/SettingsPage.tsx` | 配置页「运行环境」 | 输入框、读取、保存 |
| `CLAUDE.md` / `README.md` | 架构与使用说明 | 同步行为变化 |
| `apps/web/dist` | 前端构建产物 | `npm run build` 后提交 |

---

### Task 1: 设置模型与 `/api/settings`（TDD）

**Files:**
- Modify: `apps/api/app/services/settings_store.py:27-31`
- Modify: `apps/api/app/schemas.py:467-468`
- Modify: `apps/api/app/routers/app_settings.py`
- Test: `apps/api/app/tests/test_app_settings.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_app_settings.py` 中替换 `test_settings_api_default_and_update`，并追加新测试：

```python
def test_settings_api_default_and_update(client) -> None:
    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 0},
    )

    body = client.get("/api/settings").json()
    assert body == {"session_retention_days": 30, "task_budget_tokens": 0}

    response = client.put(
        "/api/settings",
        json={"session_retention_days": 7, "task_budget_tokens": 32000},
    )
    assert response.status_code == 200
    assert response.json() == {
        "session_retention_days": 7,
        "task_budget_tokens": 32000,
    }
    assert client.get("/api/settings").json()["task_budget_tokens"] == 32000

    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": -1, "task_budget_tokens": 0},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": 3651, "task_budget_tokens": 0},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": 30, "task_budget_tokens": -1},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": 30, "task_budget_tokens": 19999},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": 30, "task_budget_tokens": 1_000_001},
        ).status_code
        == 422
    )

    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 0},
    )


def test_task_budget_persists_to_settings_file(client) -> None:
    store = client.app.state.settings_store
    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 50000},
    )
    data = json.loads(store.path.read_text("utf-8"))
    assert data["runtime"]["task_budget_tokens"] == 50000
    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 0},
    )
```

同时把该文件其余 `client.put("/api/settings", json={"session_retention_days": ...})` 的调用统一改成带 `"task_budget_tokens": 0`（PUT 是整对象替换，缺字段会写回默认 0，显式写出来可避免测试相互影响）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_app_settings.py -x -q`
Expected: FAIL —— `GET /api/settings` 返回缺少 `task_budget_tokens`（`KeyError`/断言失败）。

- [ ] **Step 3: 实现设置模型**

`apps/api/app/services/settings_store.py` 顶部导入补 `model_validator`：

```python
from pydantic import BaseModel, Field, ValidationError, model_validator
```

`RuntimeSection` 改为：

```python
class RuntimeSection(BaseModel):
    session_retention_days: int = Field(default=30, ge=0)
    # Sent to the SDK as ``ClaudeAgentOptions.task_budget`` so the model can
    # pace a long turn and wrap up before the wall-clock timeout. 0 disables
    # it; the API rejects totals below 20000 tokens.
    task_budget_tokens: int = Field(default=0, ge=0, le=1_000_000)
    # One-way marker: the legacy app_configs retention value has been imported
    # (or the legacy table was absent) on a previous boot.
    legacy_import_done: bool = False

    @model_validator(mode="after")
    def _check_task_budget(self) -> "RuntimeSection":
        if 0 < self.task_budget_tokens < 20000:
            raise ValueError("task_budget_tokens must be 0 or at least 20000")
        return self
```

- [ ] **Step 4: 实现 HTTP 入参模型**

`apps/api/app/schemas.py` 的 `AppSettingsUpdate` 改为：

```python
class AppSettingsUpdate(BaseModel):
    session_retention_days: int = Field(ge=0, le=3650)
    task_budget_tokens: int = Field(default=0, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def _check_task_budget(self) -> "AppSettingsUpdate":
        if 0 < self.task_budget_tokens < 20000:
            raise ValueError("task_budget_tokens must be 0 or at least 20000")
        return self
```

（`model_validator` 已在 `schemas.py` 导入，无需新增。）

- [ ] **Step 5: 实现路由输出与写入**

`apps/api/app/routers/app_settings.py` 改为：

```python
from app.services.settings_store import RuntimeSection, SettingsStore, get_settings_store


def _settings_out(runtime: RuntimeSection) -> dict[str, int]:
    return {
        "session_retention_days": int(runtime.session_retention_days),
        "task_budget_tokens": int(runtime.task_budget_tokens),
    }


@router.get("")
async def get_settings(request: Request) -> dict[str, int]:
    runtime = (await _store(request).get()).runtime
    return _settings_out(runtime)


@router.put("")
async def update_settings(
    payload: AppSettingsUpdate, request: Request
) -> dict[str, int]:
    store = _store(request)
    await store.update_runtime(
        session_retention_days=payload.session_retention_days,
        task_budget_tokens=payload.task_budget_tokens,
    )
    runtime = (await store.get()).runtime
    try:
        await sync_cleanup_period(runtime.session_retention_days)
    except ClaudeSettingsError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"保留天数已保存，但写入 Claude 设置失败：{exc}",
        ) from exc
    return _settings_out(runtime)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_app_settings.py -q`
Expected: PASS（全部）。

- [ ] **Step 7: 加存储层测试**

在 `apps/api/app/tests/test_settings_store.py` 顶部导入补：

```python
from pydantic import ValidationError
```

在文件末尾追加：

```python
@pytest.mark.asyncio
async def test_update_runtime_rejects_small_task_budget(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    with pytest.raises(ValidationError):
        await store.update_runtime(task_budget_tokens=100)
    await store.update_runtime(task_budget_tokens=20000)
    assert (await store.get()).runtime.task_budget_tokens == 20000
```

- [ ] **Step 8: 跑测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_settings_store.py apps/api/app/tests/test_app_settings.py -q`
Expected: PASS。

---

### Task 2: 执行核接入预算（TDD）

**Files:**
- Modify: `apps/api/app/services/claude_runner.py:140-160`（`_config_fingerprint`）
- Modify: `apps/api/app/services/claude_runner.py:372-480`（`_client` 与 options 构建）
- Modify: `apps/api/app/services/claude_runner.py:636`（调用点）
- Test: `apps/api/app/tests/test_claude_runner.py`

- [ ] **Step 1: 扩展测试用的 FakeStore**

`apps/api/app/tests/test_claude_runner.py` 顶部导入补 `RuntimeSection`：

```python
from app.services.settings_store import (
    AgentSection,
    PlatformSettings,
    ProviderSection,
    RuntimeSection,
)
```

`FakeStore` 改为：

```python
class FakeStore:
    def __init__(self, *agents, runtime=None):
        self._agents = [agent for agent in agents if agent is not None]
        self._runtime = runtime or RuntimeSection()

    async def get(self):
        return PlatformSettings(
            runtime=self._runtime,
            agents=self._agents,
            channels=[],
            channel_conversations={},
        )
```

`SlowStore` 不需要改（它调用 `super().__init__` 与 `super().get()`）。

- [ ] **Step 2: 写失败测试**

在 `apps/api/app/tests/test_claude_runner.py` 追加：

```python
@pytest.mark.asyncio
async def test_task_budget_option_is_set_from_runtime_settings(tmp_path):
    await init_db()
    captured = []
    runner = ClaudeTurnRunner(
        store=FakeStore(
            make_agent(), runtime=RuntimeSection(task_budget_tokens=32000)
        ),
        home=tmp_path,
        client_factory=lambda options: captured.append(options)
        or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())
    assert captured[0].task_budget == {"total": 32000}


@pytest.mark.asyncio
async def test_task_budget_option_is_absent_when_disabled(tmp_path):
    await init_db()
    captured = []
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: captured.append(options)
        or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())
    assert captured[0].task_budget is None


@pytest.mark.asyncio
async def test_changed_task_budget_rebuilds_a_cached_client(tmp_path):
    await init_db()
    captured = []
    clients = [FakeClient([ResultMessage()]), FakeClient([ResultMessage()])]

    def factory(options):
        captured.append(options)
        return clients[len(captured) - 1]

    store = FakeStore(
        make_agent(), runtime=RuntimeSection(task_budget_tokens=20000)
    )
    runner = ClaudeTurnRunner(store=store, home=tmp_path, client_factory=factory)
    turn = make_turn(session_id="19191919-1919-1919-1919-191919191919")
    await runner.run(turn, TurnStream())
    store._runtime = RuntimeSection(task_budget_tokens=40000)
    await runner.run(turn, TurnStream())

    assert len(captured) == 2
    assert clients[0].disconnected is True
    assert captured[1].task_budget == {"total": 40000}


def test_config_fingerprint_includes_task_budget():
    agent = make_agent()
    assert claude_runner._config_fingerprint(
        agent, "memory", "plugins", "agent-memory", 20000
    ) != claude_runner._config_fingerprint(
        agent, "memory", "plugins", "agent-memory", 40000
    )
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py -k task_budget -q`
Expected: FAIL —— `ClaudeAgentOptions` 上 `task_budget` 恒为 `None`，且 `_config_fingerprint` 不接受第 5 个位置参数。

- [ ] **Step 4: 实现指纹**

`_config_fingerprint` 改为：

```python
def _config_fingerprint(
    agent: Any,
    memory_stamp: str = "",
    plugin_stamp: str = "",
    agent_memory_stamp: str = "",
    task_budget_tokens: int = 0,
) -> str:
    """Hash the settings that are baked into a connected SDK client."""
    provider = agent.provider
    payload = json.dumps(
        {
            "instructions": agent.instructions or "",
            "base_url": provider.base_url,
            "model": provider.model,
            "api_key": provider.api_key,
            "memory": memory_stamp,
            "agent_memory": agent_memory_stamp,
            "plugins": plugin_stamp,
            "task_budget_tokens": task_budget_tokens,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: 实现 `_client` 与 options**

`_client` 签名改为：

```python
    async def _client(self, turn: Turn, agent: Any, task_budget_tokens: int = 0) -> Any:
```

把该方法内两处 `_config_fingerprint(...)` 调用都补上第 5 个参数：

```python
                fingerprint = _config_fingerprint(
                    agent,
                    memory_stamp,
                    plugin_stamp,
                    agent_memory_stamp,
                    task_budget_tokens,
                )
```

（创建完 client 后 `fingerprint=_config_fingerprint(...)` 的那处同样补 `task_budget_tokens`。）

在 `kwargs` 字典构建之后、`system_prompt` 之前（位置不影响逻辑）加：

```python
            # An API-side token budget lets the model pace tool use and wrap
            # up on its own; the wall-clock timeout stays the hard stop.
            if task_budget_tokens > 0:
                kwargs["task_budget"] = {"total": task_budget_tokens}
```

- [ ] **Step 6: 传入运行时设置**

`run()` 中 `client = await self._client(turn, agent)` 改为：

```python
                client = await self._client(
                    turn, agent, settings.runtime.task_budget_tokens
                )
```

- [ ] **Step 7: 跑测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py -k task_budget -q`
Expected: PASS。

- [ ] **Step 8: 跑整套后端测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`
Expected: PASS（无回归）。

---

### Task 3: 前端配置字段

**Files:**
- Modify: `apps/web/src/types.ts:383-385`
- Modify: `apps/web/src/api.ts:310-316`
- Modify: `apps/web/src/components/SettingsPage.tsx`

- [ ] **Step 1: 类型与请求**

`apps/web/src/types.ts`：

```ts
export type AppSettings = {
  session_retention_days: number;
  task_budget_tokens: number;
};
```

`apps/web/src/api.ts`：

```ts
  updateSettings: (payload: {
    session_retention_days: number;
    task_budget_tokens: number;
  }) =>
```

- [ ] **Step 2: 配置页状态与读取**

`SettingsPage.tsx` 在 `retentionDays` 状态旁新增：

```tsx
  const [taskBudgetTokens, setTaskBudgetTokens] = useState(0);
```

`getSettings` 的 `.then` 里补：

```tsx
        setTaskBudgetTokens(next.task_budget_tokens);
```

- [ ] **Step 3: 保存逻辑**

在文件模块作用域（`SettingsPage` 组件函数之外、`saveRuntimeSettings` 所属组件之上）加一个纯函数：

```tsx
function clampTaskBudget(value: number): number {
  const rounded = Math.max(0, Math.min(1_000_000, Math.round(value || 0)));
  return rounded > 0 && rounded < 20000 ? 20000 : rounded;
}
```

`saveRuntimeSettings` 改为：

```tsx
      const next = await api.updateSettings({
        session_retention_days: Math.max(
          0,
          Math.min(3650, Math.round(retentionDays || 0)),
        ),
        task_budget_tokens: clampTaskBudget(taskBudgetTokens),
      });
      setRetentionDays(next.session_retention_days);
      setTaskBudgetTokens(next.task_budget_tokens);
      setRetentionNotice("已保存运行环境设置。");
```

- [ ] **Step 4: 输入框**

在保留天数输入框之后、错误提示之前插入：

```tsx
              <label className="field-label" htmlFor="task-budget">
                单轮任务预算（tokens）
              </label>
              <input
                id="task-budget"
                type="number"
                min={0}
                max={1_000_000}
                step={1000}
                value={taskBudgetTokens}
                onChange={(event) =>
                  setTaskBudgetTokens(Number(event.target.value) || 0)
                }
              />
              <p className="hint">
                告知模型本轮还剩多少 token，让它自行收敛；0 表示不设置，非 0 时最小
                20000。该预算依赖 Anthropic beta，当前 Provider 不支持时请保持 0。
              </p>
```

- [ ] **Step 5: 构建前端**

Run: `cd apps/web && npm run build`
Expected: 构建成功，`dist` 更新。

---

### Task 4: 文档同步

**Files:**
- Modify: `docs/intent.md`（在 F21 之后追加 F22）
- Modify: `CLAUDE.md`（「当前架构」执行核段落）
- Modify: `README.md`（「运行环境」条目）

- [ ] **Step 1: 写 F22**

`docs/intent.md` 追加：

```markdown
### F22 单轮任务预算（TaskBudget）

- 状态：现行
- 日期：2026-11-12
- 来源：Claude Agent SDK TaskBudget 接入

#### 功能点

1. `runtime.task_budget_tokens`（`data/settings.json`，默认 0）控制每个 turn 传给 SDK 的 `task_budget.total`。0 表示不设置；非 0 时允许 20000–1000000，网页与接口都在保存时校验。
2. 预算在创建或重建缓存 client 时写入 `ClaudeAgentOptions.task_budget`，并纳入 client 配置指纹；修改设置后，空闲 client 在下一轮重建，忙碌会话在下一轮开始时重建。
3. SDK 把预算作为 `output_config.task_budget`（beta `task-budgets-2026-03-13`）下发给模型；模型据此安排工具调用并主动收尾。它不替代 `run_timeout_seconds` 的硬超时：预算负责优雅收敛，超时仍是最终兜底。
4. 该预算依赖 Anthropic beta，第三方兼容端点可能忽略或拒绝。启用前先用一条短消息验证 Provider 兼容性；若 turn 因 beta/参数被拒而失败，把 `task_budget_tokens` 改回 0 即可恢复。
5. 配置页「运行环境」的会话与记忆维护表单提供输入框，与保留天数一起保存；该设置全局生效，不按 Agent 或来源区分。

#### 验收标准

- 默认值为 0 时，`ClaudeAgentOptions.task_budget` 为 `None`，行为与改动前一致。
- 设为 20000 以上的值后，新建 client 的 options 带 `task_budget={"total": N}`；改动该值后下一轮重建 client 并带上新值。
- 保存 1–19999、负数或超过 1000000 的值返回 422。
- 保存后刷新页面仍显示同一个值，且 `settings.json` 的 `runtime.task_budget_tokens` 已更新。
```

- [ ] **Step 2: 同步 CLAUDE.md**

在「当前架构」执行核段落（`SessionRegistry` + `ClaudeTurnRunner` 那一串）末尾补一句：

```markdown
每个 turn 可按 `runtime.task_budget_tokens`（默认 0，非 0 时 20000–1000000）向 SDK 传入 `task_budget`，让模型按剩余 token 预算自行收敛；该值纳入 client 配置指纹，与 900 秒硬超时互补。
```

- [ ] **Step 3: 同步 README**

「运行环境」条目补：

```markdown
另可设置单轮任务预算 `task_budget_tokens`（默认 0 关闭，非 0 时 20000–1000000），作为 `task_budget` 传给模型；它依赖 Anthropic beta，Provider 不支持时保持 0。
```

---

### Task 5: 验证与提交

- [ ] **Step 1: 后端测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`
Expected: PASS。

- [ ] **Step 2: 前端构建**

Run: `cd apps/web && npm run build`
Expected: 成功。

- [ ] **Step 3: 三次自我 code review**

逐项检查并记录发现（按项目约定）：
1. 正确性：0 与 20000–1000000 的边界、PUT 整对象替换语义、指纹变更是否真的触发重建、`_client` 默认参数是否影响其它调用点。
2. 一致性：intent F22 / CLAUDE.md / README / 配置页文案 / 测试断言是否一致；`settings.json` 旧文件无该字段时能否回落到 0。
3. 安全与回归：是否泄露密钥（无新密钥）、400 时用户能否通过改回 0 恢复、错误提示是否可理解。

- [ ] **Step 4: 提交**

按项目约定执行 `.claude/skills/project-commit/SKILL.md`（同步文档 → 后端测试 + 前端构建 → `git add .` → push），提交信息建议：

```text
Add an optional per-turn task budget for graceful wrap-up
```

---

## 上线后的兼容性验证（人工）

1. 在配置页「运行环境」把 `task_budget_tokens` 设为 32000 并保存。
2. 发一条普通短消息；若 turn 正常完成，说明当前 Provider 接受 beta，可继续使用。
3. 若 turn 失败且错误提到 `task_budget` / beta header / 400，把值改回 0；下一轮会重建 client，无需重启服务。
