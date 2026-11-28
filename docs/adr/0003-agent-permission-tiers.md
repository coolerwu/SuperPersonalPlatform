# ADR-0003: Agent 权限档位 full / workspace

- 状态：Accepted
- 日期：2026-11-29（决定来自 2026-11-16 权限档位设计）

## 背景

「方案 A」的多个 Agent 共享单账号、全局规则、技能插件与业务数据，需要保证「家人的 Agent 读不到我的工作区之外的文件」。此前所有 Agent 共用 `bypassPermissions`，工作区只是 cwd，没有访问控制。

## 决策

- `settings.json` 的 `agents[].access` 取 `"full"` 或 `"workspace"`；`default` 固定 `full`，新建 Agent 默认 `workspace`。
- 受限档：`permission_mode="dontAsk"` + 显式 `allowed_tools` 白名单（未列出即拒绝）+ `PreToolUse` hook（`on_failure="block"`）做路径校验。
- 路径规则：文件类工具的 `file_path` / `notebook_path` / `path` 按工作区解析并 `realpath`，拒绝 `..`、绝对越界与符号链接逃逸；`WebFetch` 拒绝 localhost、回环与私有网段。
- 白名单保留文件工具、WebSearch/WebFetch、Bash、Task、TodoWrite、Skill、平台业务工具、启用时的 `mcp__amap` 与已安装插件的 MCP server；浏览器工具按服务器级 `mcp__browser` 放行，URL 校验由浏览器工具自身负责。
- `access` 纳入配置指纹，切换档位后下一轮重建 client。

## 后果

- 默认拒绝的权限姿态；受限 Agent 遇到白名单外工具会收到权限错误，不弹审批。
- Bash、Task 与插件工具在白名单内，仍可越界；首版接受这一已知限制。
- 业务数据（交易记录、资产配置、回测、行情）对所有 Agent 开放；全局规则对所有 Agent 生效。
- 真正的硬隔离仍是独立实例 / 独立运行用户（方案 B），不在本期。

## 被否方案

- 受限档继续用 `bypassPermissions` + hook 兜底：限制建立在「默认全放行」之上，安全姿态不成立。
- 解析 Bash 命令做路径判断：不可靠，首版不做。
- OS 级隔离（独立用户 / 容器）：成本高，属于方案 B。
