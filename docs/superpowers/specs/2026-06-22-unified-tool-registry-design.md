# 统一 Tool Registry 与 Agent 会话隔离设计

## 背景

当前 Agent 管理使用文本框手工输入 `skill_ids`，容易出现拼写错误，也无法直观看到可选 Skill。Agent 对话页一次加载全部会话，切换 Agent 后仍显示相同历史；选择历史会话时甚至会反向切换 Agent。Skill 管理右侧的工具能力由前端硬编码，后端又通过工具常量、Tool Profile 和 Skill frontmatter 分散维护，缺少统一的结构化能力目录。

本设计直接以统一 Tool Registry 替换旧工具模型，不提供旧配置兼容或自动迁移。

## 目标

- Agent 的 Skill 绑定改为可搜索多选，且允许不绑定任何 Skill。
- Agent 对话历史按 `agent_id` 完全隔离，并阻止跨 Agent 会话写入。
- 后端 Tool Registry 成为工具定义、发现、校验和运行时分派的唯一事实来源。
- 工具使用统一公共字段：`name`、`display_name`、`description`、`input`、`output`、`support_scene`。
- Skill 仅通过工具 `name` 绑定能力，不复制工具元数据。
- Skill 管理界面支持按 `MCP`、`DAG`、`AGENT` 场景筛选工具。

## 非目标

- 本阶段不实现 MCP Server 管理、DAG 编辑器或 Agent 委派运行器。
- 本阶段不实现工具版本管理、依赖图或自动编排。
- 不读取、迁移或兼容旧的 `common_skill_tools`、平台级 `tools`、`profile` 或 `deny` 配置。
- 不改变现有会话 JSON 文件的数据结构。

## Tool Registry

### 公共定义

每个工具必须提供以下公共定义：

```json
{
  "name": "market_quote",
  "display_name": "市场行情查询",
  "description": "查询指定资产的实时行情",
  "input": {
    "type": "object",
    "properties": {
      "symbol": { "type": "string" }
    },
    "required": ["symbol"],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "symbol": { "type": "string" },
      "price": { "type": "number" }
    },
    "required": ["symbol", "price"]
  },
  "support_scene": ["mcp", "agent"]
}
```

字段约定：

- `name` 是稳定、唯一、面向机器的标识，也是 Skill 绑定和运行时调用键。
- `display_name` 是面向操作员的名称。
- `description` 说明能力、边界和主要用途。
- `input` 和 `output` 使用 JSON Schema。
- `support_scene` 是非空去重数组，只允许 `mcp`、`dag`、`agent`；它表达适用场景，不引入额外 `type` 字段。

### 内部注册

公共定义不暴露执行函数。后端 Registry 内部维护 `name -> definition + handler` 映射，并负责：

- 注册时校验名称唯一性、必填字段、JSON Schema 和 `support_scene`。
- 返回稳定排序的公共工具目录。
- 根据工具 `name` 生成模型可调用的 function schema。
- 根据工具 `name` 分派执行函数。
- 在执行后使用 `output` Schema 校验结构化结果。

旧的工具常量、Tool Profile、平台级工具授权和兼容回退全部删除。Tool Registry 是唯一工具来源。

## Skill 绑定

Skill Markdown frontmatter 只保留显式工具绑定：

```yaml
tools:
  allow:
    - market_quote
    - portfolio_analysis_flow
```

Skill 服务从 Registry 校验 `allow` 中的工具名称。不存在的工具在管理界面标记为“能力不可用”，保存前必须移除。运行时仅解析当前 Agent 已绑定 Skill 的 `tools.allow`，不再合并平台工具、公共工具或 Profile。

## Agent Skill 选择

Agent 配置仍保存 `skill_ids: []`，但管理界面不再提供自由文本输入：

- 使用支持搜索和多选的 Skill 选择器。
- 零选择是有效状态。
- 候选项包括公共 Skill 和当前 Agent 所属的私有 Skill。
- 选项展示名称、Skill ID 和公共/私有范围。
- 删除已绑定 Skill 时，相关 Agent 的选择器立即显示失效状态，保存前要求处理。

## 会话隔离

### 查询和切换

`GET /api/sessions` 增加必填查询参数 `agent_id`，服务端只返回该 Agent 的会话摘要。前端按 Agent 维护当前会话状态：

```text
activeSessionIdByAgent[agentId] = sessionId | null
```

切换 Agent 时：

1. 清空当前消息视图和错误状态。
2. 请求目标 Agent 的会话列表。
3. 如果存在该 Agent 上次打开的有效会话，则恢复它。
4. 否则展示目标 Agent 的独立空白对话。

切换会话不会修改当前 Agent。生成期间禁用 Agent 和会话切换，避免未完成回复落入错误视图。

### 写入校验

WebSocket 消息携带 `agent_id` 与可选 `session_id`。保存用户或助手消息前，服务端必须读取会话并校验：

```text
session.agent_id == payload.agent_id
```

不匹配时返回明确错误，且不执行 Agent、不写入任何消息。REST 获取、修改和删除单个会话时同样要求 `agent_id`，避免通过已知会话 ID 跨 Agent 操作。

## API

### 工具目录

`GET /api/agents/tools`

响应：

```json
{
  "tools": [
    {
      "name": "market_quote",
      "display_name": "市场行情查询",
      "description": "查询指定资产的实时行情",
      "input": { "type": "object", "properties": {} },
      "output": { "type": "object", "properties": {} },
      "support_scene": ["mcp", "agent"]
    }
  ]
}
```

### 会话接口

- `GET /api/sessions?agent_id={agent_id}`：列出指定 Agent 会话。
- `POST /api/sessions`：继续使用请求体中的 `agent_id` 创建会话。
- `GET /api/sessions/{id}?agent_id={agent_id}`：读取并校验归属。
- `PUT /api/sessions/{id}?agent_id={agent_id}`：更新并校验归属。
- `DELETE /api/sessions/{id}?agent_id={agent_id}`：删除并校验归属。

## Skill 管理界面

右侧工具能力面板改为消费 Registry API：

- 搜索覆盖 `name`、`display_name` 和 `description`。
- 场景筛选提供“全部、MCP、DAG、AGENT”，匹配 `support_scene`。
- 每个工具行展示 `display_name`、`name`、`description` 和场景标签。
- `input`、`output` Schema 通过可折叠详情查看，不直接占据列表主层级。
- 选择状态只写入当前 Skill 的 `tools.allow`。
- Registry 请求失败时禁用工具选择区并展示重试入口，但不影响 Markdown 编辑。

页面继续沿用当前深色 Agent command center 视觉体系，不增加重复页面标题或说明 Header。

## 错误处理

- Registry 对重复 `name`、非法 Schema、空场景或未知场景拒绝注册，并输出包含工具名称的错误。
- 工具目录加载失败不清空已有 Skill 内容。
- Skill 引用不存在的工具时显示失效项，禁止保存，避免静默丢失配置。
- 会话归属不匹配返回明确的客户端错误，不执行或持久化消息。
- Agent 或会话切换请求使用请求序号或取消机制，忽略晚到的旧响应。

## 直接替换策略

实现完成后，新模型立即成为唯一有效模型：

- 删除 `common_skill_tools`、平台级 `tools`、`profile`、`deny` 及其解析和 UI。
- 删除前端 `AGENT_TOOL_OPTIONS`、`AGENT_TOOL_GROUPS`。
- 删除后端 `SUPPORTED_COMMON_SKILL_TOOLS`、`SUPPORTED_TOOL_PROFILES`、静态 `SUPPORTED_AGENT_TOOLS` 和 Profile 合并逻辑。
- 旧配置不会自动迁移；部署前必须把需要保留的 Skill 改为新的 `tools.allow` 格式。
- 不符合新格式的配置直接报告错误，不提供回退行为。

## 测试

### 后端

- Tool Registry 注册、唯一性、字段、Schema 和场景校验。
- 工具目录 API 的字段完整性和稳定排序。
- Skill `tools.allow` 解析、Registry 校验和运行时解析。
- 旧工具字段被拒绝，确认不存在隐式兼容路径。
- 会话列表按 Agent 过滤。
- 单会话 REST 操作拒绝错误 Agent。
- WebSocket 拒绝跨 Agent 会话且不产生消息。

### 前端

- Agent Skill 多选器支持搜索、添加、移除和空选择。
- Tool Registry 搜索、场景筛选、工具选择和 Schema 展开。
- Registry 加载失败时 Markdown 编辑仍可使用。
- Agent 切换后历史、消息和活动会话相互隔离。
- 晚到的上一 Agent 会话请求不会覆盖当前 Agent。
- 生成期间 Agent 和会话切换被禁用。

## 完成标准

- Agent 管理中不存在可自由输入的 Skill ID 控件。
- 任意两个 Agent 的历史列表和当前消息视图不会互相显示。
- Skill 管理工具列表完全来自 Tool Registry API。
- Tool 顶层定义只包含六个已确认字段，不包含顶层 `type`；`input`、`output` 内部仍使用标准 JSON Schema `type`。
- 代码与配置中不存在旧 Tool Profile 或工具兼容回退。
- 后端与前端测试覆盖上述关键行为并通过。
