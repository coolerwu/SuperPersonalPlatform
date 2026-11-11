# Playwright 插件脱离系统 Chrome Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让官方集市 `playwright` 插件的 MCP server 在只有 Playwright 自带 Chromium、没有 Google Chrome、也没有显示器的机器上可用。

**Architecture:** 新增 `playwright_runtime.py`：进程内首次创建 SDK client 时，用本 venv 的 Playwright 异步解析 Chromium 可执行文件路径（工作线程里跑同步 API，只探测一次），并生成 `PLAYWRIGHT_MCP_*` 环境变量；`ClaudeTurnRunner` 把这些变量合并进 `ClaudeAgentOptions.env`，SDK 启动的 Claude CLI 及其插件 MCP 子进程继承。

**Tech Stack:** Python 3.12、claude-agent-sdk、playwright、pytest。

**Spec:** `docs/intent.md` F17（本次补充浏览器 MCP 的运行环境约定）。

---

## 背景：问题与根因

生产机（Linux，systemd 用户服务，无显示器）启用官方集市 `playwright` 插件后，Agent 调用浏览器工具报错：`Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`。

根因链：

1. 插件自带的 MCP server 是 `npx @playwright/mcp@latest`（见集市里的 `.mcp.json`，平台不修改它）。
2. `@playwright/mcp` 的参数 `--browser` 默认是 `chrome`，即 Google Chrome 通道；Linux 上该通道固定查找 `/opt/google/chrome/chrome`。
3. 生产机只有 `.venv/bin/playwright install chromium` 装的 Chromium（`~/.cache/ms-playwright/chromium-<rev>/`），没有 Google Chrome。
4. MCP 默认还是有头模式；即使装了 Chrome，无显示器环境也会因找不到 X server 失败。

已验证的 MCP 行为（`@playwright/mcp` 0.0.85 / playwright-core 打包代码）：

- `PLAYWRIGHT_MCP_BROWSER=chromium` → `browserName=chromium`、`channel=chrome-for-testing`，即使用 Playwright 自己的 Chromium，而不是系统 Chrome；Linux 上自动关闭 Chromium sandbox。
- `PLAYWRIGHT_MCP_EXECUTABLE_PATH=<path>` 会直接使用给定可执行文件（存在性检查通过后不再查 registry），因此不受 MCP 自带 playwright 版本与浏览器 revision 是否匹配的影响。
- `PLAYWRIGHT_MCP_HEADLESS=1` 开启无头模式（`envToBoolean` 接受 `1`/`true`）。
- SDK 启动 CLI 时会合并继承环境与 `ClaudeAgentOptions.env`（`subprocess_cli.py`），MCP 子进程继承 CLI 环境，因此把变量放进 `options.env` 即可到达 MCP。

设计取舍：

- **不装 Google Chrome**：宿主机多一个系统级依赖，且仍然要解决无头问题；插件升级后还得维护。
- **不让 MCP 自己下载 Chromium**（`npx @playwright/mcp install-browser`）：`@latest` 每次升级都可能要求新 revision，部署脚本和插件更新都要跟着跑；直接复用 venv 的 Chromium 没有版本漂移问题。
- **不在 systemd unit 里写死路径**：路径随 Playwright 升级变化；运行期探测更可靠。
- **不改集市插件的 `.mcp.json`**：集市更新会覆盖，且平台约定插件内容归 CLI 管理。

范围外（本计划不做）：

- 把平台自带的 `browser_tools`（`BrowserSession`）接入 SDK（工具层迁移）。
- 「看不见工作区文件」的问题：现象未确认，另行排查。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `apps/api/app/services/playwright_runtime.py`（新建） | 探测 venv 的 Chromium；生成插件 MCP 的 `PLAYWRIGHT_MCP_*` 环境变量 |
| `apps/api/app/tests/test_playwright_runtime.py`（新建） | 上面模块的单元测试 |
| `apps/api/app/services/claude_runner.py`（修改） | 创建 client 前触发一次探测，把 MCP 环境合并进 `options.env` |
| `apps/api/app/tests/test_claude_runner.py`（修改） | 断言 `options.env` 带上 MCP 环境 |
| `docs/intent.md`、`CLAUDE.md`、`README.md`（修改） | 同步运行约定与部署说明 |
| `.claude/skills/project-commit/SKILL.md`（修改） | 部署步骤注明 Chromium 同时供插件 MCP 使用 |

**约定：** 后端测试从仓库根目录运行 `.venv/bin/python -m pytest apps/api/app/tests -q`；每个任务结束提交一次。

---

### Task 1: 新增 Chromium 探测与 MCP 环境模块

**Files:**
- Create: `apps/api/app/services/playwright_runtime.py`
- Test: `apps/api/app/tests/test_playwright_runtime.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_playwright_runtime.py`：

```python
import pytest

from app.services import playwright_runtime


@pytest.fixture(autouse=True)
def _fresh_detection(monkeypatch):
    monkeypatch.setattr(
        playwright_runtime, "_chromium_path", playwright_runtime._UNDETECTED
    )
    for key in (
        playwright_runtime.MCP_BROWSER_ENV,
        playwright_runtime.MCP_HEADLESS_ENV,
        playwright_runtime.MCP_EXECUTABLE_ENV,
    ):
        monkeypatch.delenv(key, raising=False)


def test_mcp_env_points_at_bundled_chromium():
    playwright_runtime.set_chromium_executable("/opt/chromium/chrome")
    assert playwright_runtime.mcp_env() == {
        "PLAYWRIGHT_MCP_BROWSER": "chromium",
        "PLAYWRIGHT_MCP_HEADLESS": "1",
        "PLAYWRIGHT_MCP_EXECUTABLE_PATH": "/opt/chromium/chrome",
    }


def test_mcp_env_without_detected_executable():
    playwright_runtime.set_chromium_executable(None)
    assert playwright_runtime.mcp_env() == {
        "PLAYWRIGHT_MCP_BROWSER": "chromium",
        "PLAYWRIGHT_MCP_HEADLESS": "1",
    }


def test_mcp_env_keeps_operator_overrides(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_MCP_BROWSER", "firefox")
    monkeypatch.setenv("PLAYWRIGHT_MCP_EXECUTABLE_PATH", "/custom/chrome")
    playwright_runtime.set_chromium_executable("/opt/chromium/chrome")
    env = playwright_runtime.mcp_env()
    assert "PLAYWRIGHT_MCP_BROWSER" not in env
    assert "PLAYWRIGHT_MCP_EXECUTABLE_PATH" not in env
    assert env["PLAYWRIGHT_MCP_HEADLESS"] == "1"


def test_existing_file_requires_a_real_file(tmp_path):
    assert playwright_runtime._existing_file("") is None
    executable = tmp_path / "chrome"
    executable.write_text("")
    assert playwright_runtime._existing_file(str(executable)) == str(executable)
    assert playwright_runtime._existing_file(str(tmp_path / "missing")) is None


@pytest.mark.asyncio
async def test_ensure_detects_only_once(monkeypatch):
    calls = []

    def detect():
        calls.append(1)
        return "/opt/chromium/chrome"

    monkeypatch.setattr(playwright_runtime, "_detect_sync", detect)
    assert await playwright_runtime.ensure_chromium_executable() == "/opt/chromium/chrome"
    assert await playwright_runtime.ensure_chromium_executable() == "/opt/chromium/chrome"
    assert calls == [1]


@pytest.mark.asyncio
async def test_ensure_caches_a_detection_failure(monkeypatch):
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("driver missing")

    monkeypatch.setattr(playwright_runtime, "_detect_sync", broken)
    assert await playwright_runtime.ensure_chromium_executable() is None
    assert await playwright_runtime.ensure_chromium_executable() is None
    assert calls == [1]


@pytest.mark.asyncio
async def test_ensure_respects_the_operator_override(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_MCP_EXECUTABLE_PATH", "/custom/chrome")

    def must_not_run():
        pytest.fail("detection must not run when the operator set a path")

    monkeypatch.setattr(playwright_runtime, "_detect_sync", must_not_run)
    assert await playwright_runtime.ensure_chromium_executable() == "/custom/chrome"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_playwright_runtime.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.playwright_runtime'`。

- [ ] **Step 3: 写最小实现**

创建 `apps/api/app/services/playwright_runtime.py`：

```python
"""Environment for the official Playwright plugin's MCP server.

``@playwright/mcp`` defaults to the Google Chrome channel, so on a host that
only has Playwright's own Chromium it fails with "Chromium distribution
'chrome' is not found at /opt/google/chrome/chrome". The service also has no
display. Point the MCP server at this venv's Chromium and run it headless.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MCP_BROWSER_ENV = "PLAYWRIGHT_MCP_BROWSER"
MCP_HEADLESS_ENV = "PLAYWRIGHT_MCP_HEADLESS"
MCP_EXECUTABLE_ENV = "PLAYWRIGHT_MCP_EXECUTABLE_PATH"

_UNDETECTED = object()
_chromium_path: Any = _UNDETECTED
_detect_lock: asyncio.Lock | None = None
_detect_loop: asyncio.AbstractEventLoop | None = None


def _lock() -> asyncio.Lock:
    global _detect_lock, _detect_loop
    loop = asyncio.get_running_loop()
    if _detect_lock is None or _detect_loop is not loop:
        _detect_lock = asyncio.Lock()
        _detect_loop = loop
    return _detect_lock


def chromium_executable() -> str | None:
    """The Chromium binary detected for this process, if any."""
    value = _chromium_path
    return value if isinstance(value, str) else None


def set_chromium_executable(path: str | None) -> None:
    global _chromium_path
    _chromium_path = path


async def ensure_chromium_executable() -> str | None:
    """Resolve this venv's Chromium once per process; never raises."""
    override = os.environ.get(MCP_EXECUTABLE_ENV, "").strip()
    if override:
        return override
    if _chromium_path is not _UNDETECTED:
        return chromium_executable()
    async with _lock():
        if _chromium_path is _UNDETECTED:
            set_chromium_executable(await _detect())
    return chromium_executable()


async def _detect() -> str | None:
    try:
        path = await asyncio.to_thread(_detect_sync)
    except Exception:
        logger.warning(
            "Playwright Chromium could not be located; the browser plugin "
            "will not start",
            exc_info=True,
        )
        return None
    if path:
        logger.info("Playwright Chromium: %s", path)
    else:
        logger.warning(
            "No Playwright Chromium found; run: playwright install chromium"
        )
    return path


def _detect_sync() -> str | None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return _existing_file(playwright.chromium.executable_path)


def _existing_file(path: str) -> str | None:
    return path if path and Path(path).is_file() else None


def mcp_env() -> dict[str, str]:
    """Variables for the plugin MCP server; operators override per key."""
    defaults = {
        MCP_BROWSER_ENV: "chromium",
        MCP_HEADLESS_ENV: "1",
    }
    env = {key: value for key, value in defaults.items() if key not in os.environ}
    executable = chromium_executable()
    if executable and MCP_EXECUTABLE_ENV not in os.environ:
        env[MCP_EXECUTABLE_ENV] = executable
    return env
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_playwright_runtime.py -q`

Expected: PASS（7 passed）。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/playwright_runtime.py apps/api/app/tests/test_playwright_runtime.py
git commit -m "Resolve the Playwright plugin's bundled Chromium"
```

---

### Task 2: 在 runner 中注入插件 MCP 环境

**Files:**
- Modify: `apps/api/app/services/claude_runner.py:40`（导入）、`apps/api/app/services/claude_runner.py:283-315`（`_client`）
- Test: `apps/api/app/tests/test_claude_runner.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_claude_runner.py` 末尾追加（沿用文件里已有的 `FakeStore`、`make_agent`、`make_turn`、`FakeClient`、`init_db`）：

```python
@pytest.mark.asyncio
async def test_options_carry_playwright_mcp_env(monkeypatch, tmp_path):
    await init_db()

    async def no_detection():
        return None

    monkeypatch.setattr(claude_runner, "ensure_chromium_executable", no_detection)
    monkeypatch.setattr(
        claude_runner,
        "playwright_mcp_env",
        lambda: {
            "PLAYWRIGHT_MCP_BROWSER": "chromium",
            "PLAYWRIGHT_MCP_HEADLESS": "1",
            "PLAYWRIGHT_MCP_EXECUTABLE_PATH": "/opt/chromium/chrome",
        },
    )
    captured = []
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: captured.append(options)
        or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())

    assert captured[0].env["PLAYWRIGHT_MCP_BROWSER"] == "chromium"
    assert captured[0].env["PLAYWRIGHT_MCP_HEADLESS"] == "1"
    assert captured[0].env["PLAYWRIGHT_MCP_EXECUTABLE_PATH"] == "/opt/chromium/chrome"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py::test_options_carry_playwright_mcp_env -q`

Expected: FAIL — `AttributeError: <module 'app.services.claude_runner'> does not have the attribute 'ensure_chromium_executable'`（monkeypatch 找不到目标）。

- [ ] **Step 3: 写实现**

`apps/api/app/services/claude_runner.py` 顶部导入区，在 `from app.services.claude_env import ...` 之后加：

```python
from app.services.playwright_runtime import (
    ensure_chromium_executable,
    mcp_env as playwright_mcp_env,
)
```

`_client()` 里在构造 `kwargs` 之前（`valid_agent_id` 检查之后）加一次探测：

```python
            # The Playwright plugin's MCP server defaults to the system
            # Chrome channel and runs headed; point it at this venv's
            # Chromium and headless mode before the client connects.
            await ensure_chromium_executable()
```

把原来的 `"env": build_claude_env(agent, home=self._home),` 改成：

```python
                "env": {
                    **build_claude_env(agent, home=self._home),
                    **playwright_mcp_env(),
                },
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py -q`

Expected: PASS（原有测试不加改动也能通过；真实探测在测试进程里最多跑一次，失败会被吞掉并记 warning）。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/claude_runner.py apps/api/app/tests/test_claude_runner.py
git commit -m "Give the Playwright plugin MCP a headless bundled Chromium"
```

---

### Task 3: 文档同步

**Files:**
- Modify: `docs/intent.md`（F17 功能点与验收标准）
- Modify: `CLAUDE.md`（当前架构 · 插件集市条目）
- Modify: `README.md`（插件集市小节）
- Modify: `.claude/skills/project-commit/SKILL.md`（部署步骤第 3 步）

- [ ] **Step 1: 更新 `docs/intent.md` F17**

在「#### 功能点」第 5 条后追加第 6 条：

```markdown
6. playwright 插件依赖的 `@playwright/mcp` 默认走 Google Chrome 通道并有头运行；平台在 SDK 环境里注入 `PLAYWRIGHT_MCP_BROWSER=chromium`、`PLAYWRIGHT_MCP_HEADLESS=1`，探测到本 venv 的 Chromium 时再注入 `PLAYWRIGHT_MCP_EXECUTABLE_PATH`，使浏览器工具不依赖系统 Chrome 且无显示环境可用；操作者在 `.env` 中设置同名变量可覆盖平台值。部署前置：`.venv/bin/playwright install chromium`。
```

在「#### 验收标准」末尾追加：

```markdown
- 在未安装 Google Chrome 的宿主机上，启用 playwright 插件后 Agent 能打开网页并截图。
```

- [ ] **Step 2: 更新 `CLAUDE.md`**

「插件集市」条目末尾追加一句：

```markdown
平台向 SDK 环境注入 `PLAYWRIGHT_MCP_BROWSER=chromium`、`PLAYWRIGHT_MCP_HEADLESS=1` 和探测到的 venv Chromium 路径（`.env` 同名变量优先），playwright 插件不依赖系统 Chrome。
```

- [ ] **Step 3: 更新 `README.md`**

把「插件集市」小节最后一条改成：

```markdown
- 官方集市的 `playwright` 插件（Microsoft，`npx @playwright/mcp`）用于浏览器操作；启用后 CLI 会自动加载它自带的 MCP server，平台自带的 `browser_tools` 仍未接入 SDK。平台为它注入 `PLAYWRIGHT_MCP_BROWSER=chromium`、`PLAYWRIGHT_MCP_HEADLESS=1` 与探测到的 venv Chromium 路径（可在 `.env` 用同名变量覆盖），因此不依赖系统 Google Chrome；部署时需 `.venv/bin/playwright install chromium`。
```

- [ ] **Step 4: 更新部署技能说明**

`.claude/skills/project-commit/SKILL.md` 第 3 步里把 Playwright 那句改成：

```markdown
   If Playwright was added or upgraded, also run
   `.venv/bin/playwright install chromium` (prefix
   `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright` when the
   default download is slow; this Chromium is also what the official
   `playwright` plugin's MCP server launches). If Chromium fails to start for missing shared
   libraries, run `sudo .venv/bin/playwright install-deps chromium`.
```

- [ ] **Step 5: 提交**

```bash
git add docs/intent.md CLAUDE.md README.md .claude/skills/project-commit/SKILL.md
git commit -m "Document the Playwright plugin's bundled Chromium"
```

---

### Task 4: 全量回归与生产验证

**Files:** 无代码改动。

- [ ] **Step 1: 后端全量测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`

Expected: 全部 PASS；日志里最多出现一次 `Playwright Chromium: ...` 或 `No Playwright Chromium found`。

- [ ] **Step 2: 前端构建（仓库约定）**

Run: `cd apps/web && npm run build`

Expected: 构建成功；`dist` 无变化（本计划不改前端）。

- [ ] **Step 3: 本机冒烟（可选，只有本机装了 Playwright Chromium 才有意义）**

Run:

```bash
PYTHONPATH=apps/api .venv/bin/python - <<'PY'
import asyncio
from app.services.playwright_runtime import ensure_chromium_executable, mcp_env

print(asyncio.run(ensure_chromium_executable()))
print(mcp_env())
PY
```

Expected: 打印本机 Chromium 路径，`mcp_env()` 包含三个 `PLAYWRIGHT_MCP_*` 变量（没有 Chromium 时只有两个）。

- [ ] **Step 4: 部署与生产实测**

按 `.claude/skills/project-commit/SKILL.md` 提交、push，并在生产机执行部署（pull → 重启 `agent-command-center.service`）。部署后：

1. 确认 Chromium 在：`ls ~/.cache/ms-playwright | grep '^chromium-'`。缺失就运行 `.venv/bin/playwright install chromium`。
2. 在平台插件页启用 `playwright` 插件。
3. 对话里让 Agent「打开 https://www.baidu.com 并截图」。
4. 查看日志：`journalctl --user -u agent-command-center.service -n 300 | grep -i playwright`。
   - 预期出现 `Playwright Chromium: /home/qiuqiu/.cache/ms-playwright/chromium-<rev>/...`。
   - 不应再出现 `/opt/google/chrome/chrome` 或 `Chromium distribution 'chrome' is not found`。

- [ ] **Step 5: 排障（仅在 Step 4 失败时执行）**

1. 确认探测结果：在部署目录运行 Step 3 的脚本。若打印 `None`，先 `.venv/bin/playwright install chromium` 再重启服务。
2. 确认环境变量到达了 MCP 子进程：`pgrep -af '@playwright/mcp'` 找到进程号后运行 `tr '\0' '\n' < /proc/<pid>/environ | grep PLAYWRIGHT_MCP`。若没有变量，说明 CLI 没有把 `options.env` 传给插件 MCP，此时退回在 `deploy/agent-command-center.service` 里加同样三个 `Environment=` 行（路径用 Step 3 打印的值），再 `daemon-reload` + 重启。
3. 若 MCP 报协议或版本错误，核对 `npx @playwright/mcp@latest` 是否可执行、Node 是否可用。

---

## 自审清单

- 需求覆盖：F17 的浏览器 MCP 运行环境 → Task 1/2（实现）、Task 3（文档）、Task 4（验证）。
- 无占位符：每个任务都给出了完整代码、命令与预期输出。
- 类型/命名一致性：`playwright_runtime.mcp_env`、`ensure_chromium_executable`、`set_chromium_executable`、`_detect_sync`、`_existing_file`、`_UNDETECTED` 在各任务中用法一致；runner 里以 `playwright_mcp_env` 别名导入，测试 monkeypatch 的也是这个名字。
- 已知风险：`options.env` → CLI → MCP 子进程的环境继承未在单元测试覆盖，靠 Task 4 的生产实测兜底，并在 Step 5 给出回退方案。
