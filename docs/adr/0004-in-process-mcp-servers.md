# ADR-0004: 平台工具与浏览器走进程内 MCP

- 状态：Accepted
- 日期：2026-11-29（浏览器决定来自 2026-11-23 平台浏览器 MCP 设计）

## 背景

- 平台业务能力（回测、行情、交易记录、资产配置、缠论）需要让 Agent 调用。
- 浏览器能力最初依赖集市插件 `playwright@claude-plugins-official`：每个 SDK CLI 子进程用 `npx` 拉起 Node MCP server。生产环境 systemd PATH 里没有 nvm，`npx` 落到 Node 18，插件要求 Node ≥ 20，会话内浏览器工具反复 `CONNECTION_CLOSED`；`npx` 每次还会访问 registry（超时），异常退出会留下 `SingletonLock`。

## 决策

- 平台在进程内用 SDK `create_sdk_mcp_server` 暴露两组工具：
  - `platform_tools`：19 个业务工具（`mcp__platform_tools__*`），配置 `timeout`（至少 180 秒）与 `disableAutoBackground`，覆盖 `backtest_run` 约 90 秒的等待。
  - `browser`：13 个浏览器工具（`mcp__browser__*`），复用 venv 的 Playwright + Chromium；每个会话独立 `chat-<id 前缀>` 标签页，共享 `data/browser_state.json` 登录态，懒启动、空闲 300 秒回收、关机时优雅关闭。
- 插件自带的 MCP 继续并存；集市 `playwright` 插件已卸载，浏览器能力只由平台提供。
- 受限 Agent 的白名单包含服务器级 `mcp__browser` 与 `mcp__amap`；工具是否可见以该 Agent 最近一轮 init frame 为准。

## 后果

- 浏览器工具不再依赖 Node / npx / npm registry，生命周期跟随 API 进程。
- 全局标签页上限沿用 `BROWSER_CONCURRENCY`；满额时按创建顺序驱逐其它会话的空闲标签页，忙碌标签页不驱逐。页面渲染进程崩溃只摘除该标签页，不重置共享浏览器。
- 工具以 `mcp__<server>__<tool>` 命名；工具页直接读 init frame 的 `tools` / `mcp_servers`，平台不维护 Claude 工具清单。
- `web_search` / `web_fetch` 与 `skill_save` 仍未接入；搜索走 Claude Code 自带的 WebSearch / WebFetch。

## 被否方案

- 继续使用集市插件：外部进程、Node 版本、registry 可达性与锁文件都不可控。
- 独立 MCP 服务进程：多一个部署单位与生命周期协调，单用户场景没有收益。
