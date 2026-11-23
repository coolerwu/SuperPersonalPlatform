# 平台自带浏览器 MCP（进程内 Playwright）设计

- 日期：2026-11-23
- 状态：待评审
- 范围：`apps/api` 服务层、执行核、权限白名单；生产运行时 `web-browser` skill 文本；部署配置。无新依赖（`playwright` 已在 `apps/api/pyproject.toml`）、无数据库变更、无前端页面改动。

## 背景

浏览器能力目前完全依赖集市插件 `playwright@claude-plugins-official`：插件的 `.mcp.json` 是 `npx @playwright/mcp@latest`，由每个 SDK CLI 子进程拉起 Node MCP server。

2026-11-23 06:30 UTC 的会话 `0e1bd714-2246-41b3-91a1-5464e63174d3` 启动时，CLI 报 `plugin:playwright:playwright (CONNECTION_CLOSED)`，整场没有浏览器工具，Agent 只能退回 WebSearch。已复现的根因链：

1. systemd 服务环境的 PATH 没有 nvm，`npx` 解析到系统 `/usr/bin/npx`（Node 18.19.1）；
2. `@playwright/mcp@latest` 当前为 0.0.86（playwright-core 1.66.0-alpha-2026-11-18），声明 `engines: node >= 20`，在 Node 18 下启动即打印 `Playwright requires Node.js 20 or higher` 并退出；
3. MCP 进程退出 → CLI 标记该 server 连接失败 → 会话内浏览器工具全部不可用。

旁证与历史问题：

- 生产 transcript 中 2026-11-12 至 11-23 共 11 个会话出现同类 `CONNECTION_CLOSED`，另有 9 处 `CONNECT_TIMEOUT`（npx 每次启动都要查 registry，走代理可能超时）。
- 会话内的 Agent 把原因归为 Chromium profile 的残留 `SingletonLock`；删锁后新建会话仍然失败——锁不是本次根因，但“CLI 被异常杀掉留下锁”是插件形态的固有问题。
- 2026-11-11 的计划 `docs/superpowers/plans/2026-11-11-playwright-mcp-bundled-chromium.md`（注入 `PLAYWRIGHT_MCP_EXECUTABLE_PATH`）从未落地。
- 插件每次更新都可能改 `.mcp.json`（缓存里有 13 个版本，历史手改会被覆盖）。

用户确认的目标：**稳定可控、去掉 Node 依赖、统一并发与安全边界**；不做站点定制。据此决定由平台在进程内提供浏览器 MCP，复用仓库里已有但未接入的 `browser_tools.BrowserSession`。

## 目标与非目标

### 目标

1. 浏览器工具由平台进程内 SDK MCP server `browser` 提供（模型侧工具名 `mcp__browser__*`），不依赖集市插件、Node、npx、npm registry。
2. 工具面覆盖现有 `web-browser` skill 的使用方式：导航、快照、点击、输入、表单填写、按键、下拉选择、等待、后退、标签页、截图、控制台消息、关闭。
3. 会话级 tab 隔离：每个聊天会话使用自己的命名 tab，共享同一个浏览器与上下文，登录态经 `storage_state`（`data/browser_state.json`）跨会话、跨回收复用；tab 上限沿用 `BROWSER_CONCURRENCY`；不同会话可并行，同一会话操作串行。
4. 生命周期可控：懒启动、空闲回收、应用 shutdown 时优雅关闭浏览器并落 `storage_state`；不使用 Chromium `user-data-dir`，天然没有 `SingletonLock` 类残留锁问题。
5. 安全边界不低于现状：只允许 http/https、拒绝 localhost/内网/file、检测人机验证页、自动关闭弹窗、不支持下载。
6. 两个权限档位（`full` / `workspace`）都能使用；受限 Agent 通过显式白名单 `mcp__browser` 放行。
7. 文档与运行时 skill 同步更新；工具页无需改代码即可显示 `browser` server 状态。

### 非目标

- 不做 per-Agent 开关、不新增配置页 UI 或设置分区。
- 不实现 `@playwright/mcp` 的全部工具（如 `find`、PDF、下载、弹窗接管、iframe 内独立操作）。
- 不支持多浏览器实例或多 profile。
- 不保留插件作为并行回退来源；同一时间只有一套浏览器工具。
- 不修改 Provider、权限模型、SSE 事件协议。
- 不删除生产机上的 nvm/Node（其他用途可能需要），只是浏览器工具不再依赖它。

## 现状盘点（复用资产）

| 资产 | 位置 | 可复用内容 |
|---|---|---|
| `BrowserSession` | `apps/api/app/services/browser_tools.py` | 懒启动 Chromium（普通 launch + `new_context`，非 `user-data-dir`）、`storage_state` 持久化（`data/browser_state.json`，关闭时合并写回）、代理、UA/locale/时区/语言、localhost/内网解析拦截、快照脚本（ref 格式 `s<generation>e<n>`）、操作后自动快照、人机验证页检测、截图（PNG/base64/大小上限）、命名 tab 与上限、优雅关闭 |
| `settings.browser_*` | `apps/api/app/config.py` | 并发、导航/动作超时、settle、快照元素/正文/总长上限、截图字节上限、身份与状态路径、`no_sandbox` |
| SDK MCP 模式 | `apps/api/app/services/platform_mcp.py` | `create_sdk_mcp_server` + tool schema/annotation 适配 |
| 执行核注入点 | `apps/api/app/services/claude_runner.py` | `kwargs["mcp_servers"]` 同时支持 SDK server 与 http server |
| 权限白名单 | `apps/api/app/services/agent_guard.py` | `build_restricted_allowed_tools` 支持服务器级 `mcp__<server>` 条目 |
| 产物识别 | `apps/api/app/services/tool_artifacts.py` | `artifact_from_tool` 从工具入参的 `file_path` / `filename` 生成产物 |
| 生命周期 | `apps/api/app/main.py` | `lifespan` 关闭顺序（registry → runner → 各服务） |

需要清理的死代码：`browser_tools.py` 末尾的旧函数式包装 `browser_open/browser_snapshot/browser_click/browser_fill/browser_press/browser_screenshot/browser_close`，以及 `skill_tools.SkillToolContext.browser` 字段与 `_session_for`（v1 已无调用方）。

## 架构与数据流

```text
ClaudeTurnRunner（每个缓存 client 构建一次）
  → mcp_servers["browser"] = browser_mcp.build_server(
        service, agent_id, session_id, workspace)
  → SDK in-process MCP server "browser"，13 个工具
  → BrowserMcp 工具处理器
      → BrowserService（进程内单例，lifespan 创建/关闭）
          → 会话 tab 名：chat-<session_id 前 8 位>
          → BrowserSession（单浏览器、固定 profile）
          → Chromium headless
```

### BrowserService（`apps/api/app/services/browser_service.py`）

- 进程内单例，`main.py` 的 `lifespan` 启动时创建、关闭时调用 `shutdown()`（带超时，`save_storage_state` 后关闭 context/browser/playwright）。
- 懒启动：首次工具调用时才实例化 `BrowserSession` 并拉起 Chromium；启动失败转换为可读错误（提示 `playwright install chromium`）。
- 空闲回收：最后一次工具调用后超过 `browser_idle_seconds`（默认 300）自动关闭浏览器并落状态；下一次调用重新拉起。profile 中的 cookie/localStorage 跨回收保留。
- 崩溃恢复：`BrowserSession` 内部异常（浏览器退出、页面失效）后，服务层丢弃旧实例并在下一次调用重建；会话 tab 自动重建。
- 会话 tab：`tab_name = f"chat-{session_id[:8]}"`；服务层只允许操作属于本会话的 tab（拒绝其他会话名、拒绝未知名）。tab 上限沿用 `BrowserSession(concurrency=BROWSER_CONCURRENCY)`；同一 tab 的操作由既有 `tab.lock` 串行，不同 tab 天然并行。
- 不在服务层再加全局信号量：并发边界就是“tab 数上限 + 每 tab 串行”，与旧实现的语义一致。

### BrowserMcp（`apps/api/app/services/browser_mcp.py`）

- `build_server(service, *, agent_id, session_id, workspace)` 返回 `create_sdk_mcp_server("browser", tools=[...])`，与 `platform_mcp.build_server` 同款结构；工具 handler 捕获 `BrowserToolError` 与未预期异常，统一返回文本结果（带 `error:` 前缀），不向 SDK 抛异常。
- 工具上下文只携带 `agent_id/session_id/workspace` 用于 tab 命名、截图落盘和日志，不读写平台配置。
- 工具结果：默认 `[{"type": "text", "text": ...}]`；`take_screenshot` 额外返回 `{"type": "image", "data": <base64>, "mimeType": "image/png"}`。

### 执行核注册（`claude_runner.py`）

```python
mcp_servers: dict[str, Any] = {
    platform_mcp.SERVER_NAME: platform_mcp.build_server(tool_context),
    browser_mcp.SERVER_NAME: browser_mcp.build_server(
        browser_service, agent_id=agent.id,
        session_id=turn.session_id, workspace=workspace,
    ),
}
```

始终注册（与 `platform_tools` 一致），不引入新开关。

## 工具契约

服务器名 `browser`，模型侧完整名为 `mcp__browser__<tool>`。所有工具的 `tab` 参数可选，缺省为本会话默认 tab；`target` 接受快照 ref（`s1e3`）或唯一 CSS selector。

| 工具 | 参数 | 行为 |
|---|---|---|
| `navigate` | `url`、`tab?` | 校验 http/https 与主机可达性（拒绝 localhost/内网/file），打开或跳转，返回标题、最终 URL、新快照 |
| `snapshot` | `tab?` | 返回当前页新快照（ref 从 `s<generation>` 开始重新分配） |
| `click` | `target`、`tab?` | 点击 ref/selector，等待页面稳定后返回新快照 |
| `type` | `target`、`text`、`submit=false`、`tab?` | 填充并可选回车提交，返回新快照 |
| `fill_form` | `fields: [{target, value}]`、`tab?` | 逐字段填充后返回一次新快照 |
| `press_key` | `key`、`tab?` | 按键（沿用现有 `KEY_PATTERN` 校验），返回新快照 |
| `select_option` | `target`、`values: [str]`、`tab?` | `<select>` 选值，返回新快照 |
| `wait_for` | `text?`、`textGone?`、`time?`（毫秒）、`tab?` | 至少一个条件；等待后返回新快照 |
| `navigate_back` | `tab?` | 后退，返回新快照 |
| `tabs` | `action: list\|new\|select\|close`、`index?`、`url?` | 列出/新建/切换/关闭本会话 tab；序号只作用于本会话 tab 列表 |
| `take_screenshot` | `filename?`、`full_page=false`、`tab?` | 截图；给 `filename` 时写入 `<workspace>/<filename>` 并出现在「产物」，同时返回图片内容 |
| `console_messages` | `tab?` | 返回本 tab 最近 50 条 console 记录（level、文本、时间） |
| `close` | `tab?` | 关闭本会话指定 tab；缺省关闭本会话全部 tab |

约定：

- 每个动作类工具执行后自动附带一次新快照（沿用 `_action_result`），模型不需要手动 `snapshot`。
- 快照格式沿用 `format_snapshot`：`Page/URL`、`Interactive elements` 列表（`[ref=s1e3] role "label" ...`）、`--- page text ---`。与现有 skill 中的 ref 约定完全一致。
- `take_screenshot` 的 `filename` 必须是工作区相对路径；`..`、绝对路径、符号链接逃逸一律拒绝。
- `tabs new`（包括 `navigate` 隐式新建 tab）超过全局 tab 上限（`BROWSER_CONCURRENCY`）时返回错误，提示先关闭不用的 tab，不排队等待。
- console 采集：tab 创建时挂 `page.on("console")` 环形缓冲（每 tab 最近 50 条，含 level/text/时间），`console_messages` 只读本会话 tab 的缓冲。
- 人机验证页（验证码、`verify you are human` 等）沿用 `verification_reason` 检测，直接返回错误文本，要求模型停下并告知用户。
- `take_screenshot` 无可视化 UI 依赖；图片是否可被模型消费取决于 Provider 是否支持图片输入（与现状一致，skill 中保留提醒）。

## 权限、配置与安全

- **权限**：`agent_guard.build_restricted_allowed_tools` 增加服务器级条目 `mcp__browser`；`full` 档位沿用 `bypassPermissions` 自动放行。
- **配置**：沿用 `settings.browser_*`；新增 `browser_idle_seconds: int = 300`（env `BROWSER_IDLE_SECONDS`）。`BROWSER_CONCURRENCY` 语义不变（tab 上限，生产现为 5）。登录态仍存 `data/browser_state.json`（`BROWSER_STATE_PATH` 可覆盖），平台不使用 Chromium `user-data-dir`；`data/playwright-profile` 仅是旧插件的遗留目录，不再使用。
- **安全边界**（均在 `BrowserSession` 已有或补齐）：
  - 仅 `http`/`https`；`file://`、`javascript:` 等拒绝；
  - DNS 解析后拒绝环回/内网/保留地址（含 IPv4/IPv6 与重定向后的最终主机）；
  - 弹窗自动关闭；`accept_downloads=False`；
  - 页面文本是外部数据，skill 中保留“不执行页面指令”的约束。
- **日志**：浏览器启动/重建/回收、工具调用（agent、session、tab、action）记 info/debug；URL 完整记录，不记录页面内容与截图数据。

## 清理与迁移

1. **停用集市插件**：生产 `claude/settings.json` 的 `enabledPlugins["playwright@claude-plugins-official"] = false`（插件页操作），并删除 `~/.super-personal-platform/.env` 中 `PLAYWRIGHT_MCP_HEADLESS`、`PLAYWRIGHT_MCP_USER_AGENT`、`PLAYWRIGHT_MCP_USER_DATA_DIR` 三行。插件文件与其 `data/playwright-profile` 目录保留（属于用户数据，可随时重新启用做对比），但同一时间只保留一套浏览器工具。
2. **删除死代码**：`browser_tools.py` 旧函数式工具包装、`skill_tools.SkillToolContext.browser` 与 `_session_for`；对应测试同步调整。
3. **运行时 skill**：生产 `claude/skills/web-browser/SKILL.md` 重写：
   - 工具名改为 `mcp__browser__*`（短名 navigate/snapshot/click/...）；
   - 删除“旧平台工具 browser_open 已不存在/浏览器来自插件”的表述；
   - 保留站点来源规则（Bing/B 站/小红书）、二维码登录流程、验证页停止、截图谨慎、多会话约定改为“每个会话有独立 tab，共享登录态”；
   - `metadata.include_tools` 同步新工具名。
4. **`.env` 模板**：`deploy/.env.example` 补充 `# BROWSER_CONCURRENCY=5`、`# BROWSER_IDLE_SECONDS=300` 说明（如未列出）。

## 错误处理

- 浏览器不可用（Chromium 缺失/启动失败）：返回 `error: browser is not available: <原因>`，提示运维执行 `playwright install chromium`。
- 工具调用失败（元素不存在、超时、导航失败）：`error: <动作> failed: <简短原因>`，附当前 tab 与 URL。
- 导航/动作超时沿用 `browser_navigation_timeout_ms` / `browser_action_timeout_ms`；`wait_for` 总时长上限 5 分钟（与二维码登录流程一致），超时返回错误并保留 tab。
- 截图超过 `browser_screenshot_max_bytes`：报错并提示 `full_page=false`。
- 空闲回收期间到达的调用：透明重启，不影响结果。
- 关闭服务时浏览器关闭失败：记 warning，不阻塞 shutdown（沿用现有 5 秒超时策略）。

## 测试

- **单元测试**（`apps/api/app/tests/test_browser_mcp.py`，用 fake page/session）：
  - 13 个工具的参数校验与错误映射；
  - 会话 tab 命名空间：不能操作其他会话或被篡改的 tab 名；
  - `wait_for` 参数互斥（无参数报错）；
  - `take_screenshot` 落盘到 workspace、filename 相对路径校验、图片内容块结构；
  - `close` / `tabs close` 的边界；
  - 工具结果都是 SDK 可接受的 content 列表。
- **服务测试**（`test_browser_service.py`）：
  - 懒启动、空闲回收、shutdown 顺序与超时；
  - 崩溃后重建；
  - 每会话 tab 计数上限。
- **集成测试**（复用 `test_browser_integration.py`，无 Chromium 时 skip）：
  - navigate → snapshot ref → click/type → 截图；
  - 两个会话各自 tab、互不覆盖；
  - shutdown 后 profile 无 `SingletonLock/SingletonSocket/SingletonCookie` 残留。
- **执行核/权限测试**：
  - runner 构造的 `mcp_servers` 含 `browser`；
  - 受限 Agent 的 `allowed_tools` 含 `mcp__browser`；
  - 工具页 init frame 数据路径不变（`mcp_servers` 自动出现 `browser`）。
- 回归：`.venv/bin/python -m pytest apps/api/app/tests`、`cd apps/web && npm run build`。

## 文档同步

- `docs/intent.md`：
  - 平台业务工具段补“浏览器工具由进程内 `browser` SDK MCP server 提供”；
  - 删除/改写“平台自带的浏览器工具仍未接入”“浏览器使用官方集市 playwright 插件”；
  - 运行期截图推送相关表述（“等平台浏览器工具接入”）按新能力改写或保留为后续项；
  - 权限段补充 `mcp__browser`。
- `CLAUDE.md`：架构段（浏览器工具、插件集市、权限、运行时目录不变）、运行约定。
- `README.md`：部署说明（不再需要 `PLAYWRIGHT_MCP_*`；插件停用步骤；无新依赖）。
- 本 spec 与后续 plan 入 `docs/superpowers/`。

## 发布与验证

1. 代码合并后部署：`git pull --ff-only origin main` → `systemctl --user restart agent-command-center.service`（无新 pip 依赖；Chromium 已在 venv 的 Playwright 缓存里）。
2. 生产验证：
   - 新会话工具页出现 `browser` server 与 13 个工具；
   - 真实网页 navigate/snapshot/click 成功；
   - 两个会话各自 tab、共享登录态；
   - 重启服务后无残留 Chromium 进程；登录态保存在 `data/browser_state.json`（平台不使用 `user-data-dir`，不存在 `SingletonLock`）；
   - 受限（workspace）Agent 能调用浏览器工具。
3. 回退：重新启用集市插件并恢复 `.env` 三行；两套工具名不同（`mcp__browser__*` vs `mcp__plugin_playwright_playwright__*`），可并存但不建议长期保留。

## 风险与开放问题

- **快照质量决定成功率**：现有 `BrowserSession` 快照为旧版生产使用过的实现；如遇具体站点信息缺失（复杂控件、虚拟列表），按站点迭代，不在本期解决。
- **Provider 图片支持**：`take_screenshot` 返回图片内容块；若 Provider 不支持图片输入会报错。与现状一致，skill 保留“调用前确认 Provider 支持图片”的提示，必要时后续增加“仅保存文件”模式。
- **单浏览器多会话共享上下文与登录态**：cookie 共享是特性；tab 隔离靠命名前缀。极端情况下两个会话同时触发浏览器启动由服务层单例串行保证。
- **Chromium 升级**：由 `pip install -e apps/api` 的 Playwright 版本决定，部署流程已有 `playwright install chromium` 步骤，浏览器版本与 Python 包天然匹配，不再受 MCP 内部 revision 影响。

## 验收标准

1. 不启用集市 playwright 插件、不依赖 Node/npx 的前提下，Agent 能完成一次真实网页浏览（打开、快照、点击、输入、截图）。
2. 同一进程内两个会话同时浏览互不覆盖各自的 tab，登录态共享。
3. 平台重启/关机后不再出现残留 `SingletonLock` 导致的浏览器启动失败。
4. 工具页、日志、产物展示与现状等价。
5. `pytest` 全量与前端构建通过，文档三件套已同步。
