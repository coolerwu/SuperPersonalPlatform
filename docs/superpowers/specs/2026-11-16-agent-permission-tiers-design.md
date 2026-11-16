# Agent 权限档位（超级 / 受限）设计

- 日期：2026-11-16
- 状态：设计待确认，未实现
- 范围：Agent 运行时的工具权限；不改路由、会话入口与业务数据

## 1. 背景与目标

平台按「方案 A」支持多用户：同一个实例里多个 Agent 各用各的，共享单账号登录、全局规则、技能插件与业务数据。可接受，但需要保证「家人的 Agent 读不到我的工作区之外的文件」。

现状问题：

- 所有 Agent 共用一个全局 `permission_mode = "bypassPermissions"`；
- 没有沙箱，工作区只是 cwd，文件工具和 Bash 可以访问任意路径；
- `add_dirs` 从未设置，但那只是「默认不可见」，不是访问控制。

目标：

- `default`（Assistant）保持现有全权行为；
- 其他 Agent 的文件类工具默认只能读写自己的工作区；
- 越界在工具层被拒绝，行为可控、可测试、可解释。

## 2. 总体决策

| 决策 | 内容 | 理由 |
|---|---|---|
| 权限档位 | `agents[].access: "full" \| "workspace"`，缺省视为 `"full"`；`default` 固定 `"full"`；新建 Agent 默认 `"workspace"` | 兼容现有配置，新 Agent 默认安全 |
| 强制执行点 | 受限档：`permission_mode="dontAsk"` + 显式 `allowed_tools` 白名单（未列出的工具默认拒绝），另挂 `PreToolUse` hook 做路径精确校验；超级档：不挂 hook | 默认拒绝的权限姿态正确；hook 负责 `..`、绝对路径、符号链接等白名单规则难以表达的部分 |
| 审批模式 | 超级档保持 `bypassPermissions`；受限档用 `dontAsk`（不弹审批、未预先批准即拒绝） | 受限档不再建立在「默认全放行」之上；超级档维持现有免审批体验，不引入审批 UI |
| 受限工具集 | 文件类工具（按工具名列入白名单，路径由 hook 校验）、`WebSearch`/`WebFetch`、`Bash`、`Task`、`TodoWrite`、`Skill`、平台业务工具（精确工具名）、`mcp__amap`（如启用）、插件 MCP（按 server 名加白） | 保留对话、联网、命令与业务能力；未知工具由 `dontAsk` 默认拒绝 |
| 路径校验 | 相对路径按工作区解析；双方 `realpath` 后要求位于工作区根内；拒绝 `..`、绝对外部路径、符号链接逃逸 | 只做字符串前缀判断会被符号链接绕过 |
| WebFetch 校验 | 拒绝指向 `localhost`、`127.0.0.1`、`::1` 和私有网段的 URL | 防止受限 Agent 通过 HTTP 访问本机平台或内网服务 |
| 配置指纹 | `access` 纳入 client 配置指纹 | 切换档位后空闲缓存 client 重建，下一轮生效 |

被否掉的方案：

- 受限档继续用 `bypassPermissions` + hook 兜底：限制建立在「默认全放行」之上，安全姿态不成立。
- 解析 Bash 命令做路径判断：不可靠，首版不做；Bash 在白名单里，仍可绕过工作区限制，属于已知限制。
- OS 级隔离（独立用户/容器）：成本高，属于「方案 B」，不在本期。

## 3. 配置与界面

- `settings.json` 的 `agents[]` 新增 `access: "full" | "workspace"`，默认 `"full"`；已有的 `default` 保持 `"full"`，新建 Agent 由 API 默认写成 `"workspace"`。
- API：`AgentOut` 返回 `access`；创建接口允许缺省（`workspace`）；更新接口允许在非 `default` Agent 上切换，`default` 拒绝改成 `workspace`。
- 配置页 Agent 编辑区新增「完全访问（超级 Agent）」开关，说明：开启后不受工作区限制；关闭后文件类工具只能读写自己的工作区（Bash 与插件工具的精细限制后续再做）。Agent 列表显示档位徽标。
- Agent 档案指纹加入 `access`，与 instructions/provider 变更一样触发 client 重建。

## 4. 运行时门禁

新增 `apps/api/app/services/agent_guard.py`：

- `build_workspace_guard(workspace: Path) -> HookCallback`：闭包捕获工作区的 `realpath`。
- `build_restricted_allowed_tools(...) -> list[str]`：受限档的白名单，包含：
  - 文件类工具名：`Read`、`Write`、`Edit`、`MultiEdit`、`NotebookEdit`、`Glob`、`Grep`（路径限制由 hook 负责，不依赖 CLI 路径规则语法）；
  - `WebSearch`、`WebFetch`、`Bash`、`Task`、`TodoWrite`、`Skill`；
  - 平台业务工具的精确名（`platform_mcp.allowed_tool_names()`）；
  - 地图启用时加 `mcp__amap`；
  - 插件 MCP：按已安装插件的 server 名逐个加白；枚举失败时该插件工具被默认拒绝，并在日志提示。
- 仅在 `access == "workspace"` 时，runner 设置 `permission_mode="dontAsk"`、上述 `allowed_tools`，并挂 `options.hooks = {"PreToolUse": [HookMatcher(matcher=None, hooks=[guard], on_failure="block")]}`；超级档保持 `bypassPermissions`、不挂 hook、`allowed_tools` 维持现状。

判定规则（hook，按顺序）：

1. 文件类工具：`Read`/`Write`/`Edit`/`MultiEdit`（`file_path`）、`NotebookEdit`（`notebook_path`）、`Glob`/`Grep`（`path`）：取出所有路径字段，逐一按工作区解析并 `realpath`，全部位于工作区根内才放行；缺字段时按工作区处理；越界拒绝并说明原因。
2. `WebFetch`：解析 `tool_input.url`，http/https 且 host 不是 localhost/127.0.0.1/::1 或私有网段（10/8、172.16/12、192.168/16、169.254/16、fc00::/7）时放行，否则拒绝。
3. 其余工具：hook 返回空决策（不发表意见），由 `dontAsk` 的白名单决定——白名单内放行，白名单外拒绝。

失败策略：`on_failure="block"`，hook 异常时拒绝，文件工具调用按更保守的方向失败。

实施前验证：用一个真实受限 Agent 核对 `dontAsk` 对白名单外工具的实际拒绝行为、`mcp__server` 规则的可用性；把结果写进测试。

## 5. 边界与已知限制

- 同一 OS 用户、同一进程，没有沙箱；这是工具层门禁，不是系统级隔离。
- `Bash`、`Task` 与插件工具在白名单里，可以绕过工作区限制（例如 Bash 直接读任意路径）；首版只拦文件类工具的路径参数，命令与插件工具的精细限制后续再做。
- 受限档漏加白名单的工具会被 `dontAsk` 拒绝，模型会收到权限错误；因此实施前必须用 init frame 核对工具名，并在测试里覆盖。
- 插件 MCP 的 server 名依赖已安装插件枚举；枚举失败时该插件工具被默认拒绝，效果可见、可修。
- 首版只有固定两档，不做每 Agent 自定义白名单。
- 允许 `Skill` 加载：技能内容可能指导越界操作，文件工具仍会被门禁拦截，但通过 Bash 的指令不受拦。
- 业务数据（交易记录、资产配置、回测、行情）对所有 Agent 开放，已确认可接受。
- 记忆页「全局规则」对所有 Agent 生效，属于方案 A 的已知共享面。
- 真正的硬隔离仍是方案 B（独立实例/独立运行用户）。

## 6. 验证

- `apps/api/app/tests/test_agent_guard.py`：覆盖工作区内相对/绝对路径、`..` 逃逸、符号链接指向外部、缺失路径字段、`WebFetch` 的 localhost/私有网段与公网地址、hook 对白名单工具返回空决策（不越权放行）、hook 异常失败即拒绝。
- 白名单构建测试：包含文件工具名、平台业务工具精确名、amap（仅在启用时）、已安装插件的 server 名；禁用地图或插件时对应项不出现。
- runner 测试：`access="workspace"` 时 `permission_mode="dontAsk"`、挂 hooks、`allowed_tools` 为受限白名单；`access="full"` 时保持 `bypassPermissions`、无 hooks；`access` 变化使配置指纹变化。
- 用一个真实受限 Agent 的 init frame 验证未知工具被拒、白名单工具可用。
- 前端构建通过；配置页开关能保存并在列表显示档位。
- 人工验收：受限 Agent 用文件工具读 `knowledge/` 与另一工作区被拒；读写自己工作区、联网搜索、执行命令、调用回测工具正常；超级 Agent 行为不变。

## 7. 不在范围

- OS 级隔离或独立实例部署。
- Bash 沙箱、插件工具按需授权（后续精细化）。
- 每 Agent 自定义工具白名单与按需授权 UI。
- 业务数据按 Agent 隔离。
- 渠道、定时任务、会话的权限规则（沿用现状）。
