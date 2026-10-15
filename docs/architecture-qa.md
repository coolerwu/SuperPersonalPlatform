# 架构问答

本文件记录 DeepAgent 重构后的目标架构决策。主原则是：后端执行、磁盘落盘、前端轮询；Agent 只引用 Context，Context 是归属隔离边界并自带 Knowledge。

## 保留和删除边界

**Q: 这次重构保留什么？**

保留：

- deepagents 公开 API 的后端执行。
- 微信个人号接入。
- 坚果云 WebDAV。
- 单 token 登录、基础配置、基础日志、生产启动脚本。

删除或不再作为目标架构保留：

- 旧 Harness 严格状态机作为产品运行路径。
- Prompt/Agent 双模式产品概念。
- Portfolio 投资组合功能。
- Critique 多维批判功能。
- Proxy 嵌入站点功能。
- 旧 WebSocket 聊天接口。
- 旧 Session/Skill/Agent 管理产品模型。

## 架构面边界：只用 deepagents 公开 API

**Q: 平台允许在哪些地方调用 LangChain / LangGraph？**

按「架构面 / 实现面」划线，而不是「凡 LangChain 皆例外」，否则例外清单会长到失去意义。

**架构面（编排层）**：装配 Agent、决定「图长什么样」的地方，只走 deepagents 公开 API。不使用 `StateGraph` 手写节点与边作为产品编排方式；不使用 LangSmith 部署、LangGraph Platform 或其它 LangChain 平台组件。**唯一例外**：当 deepagents 公开 API 无法表达某个确定性编排时才自建 LangGraph 图。

**实现面（箱内内容）**：单个工具怎么实现、用哪个包的消息/模型适配器/middleware 回调基类，属于实现面。它们最终都以 `create_deep_agent` 的公开参数类型（`tools=`、`config=`、`permissions=`、middleware 基类、消息类型）注入，因此不算绕过 deepagents。`langchain-core` / `langchain-openai` / `langchain-anthropic` / `langchain-community` 都属于这一类。

**允许的运行期事实描述**：`create_deep_agent` 内部用 LangGraph checkpoint 保存会话状态、`astream(..., subgraphs=True)` 产出 LangGraph stream chunk、工具用 LangChain Community 的 wrapper 实现——这些说的是 deepagents 的实际行为，不是架构选择，文档里保持原样。

**登记在案的两条例外**（都是 deepagents 该给但当前没给或没公开的地方）：

- `TodoListMiddleware` 兼容降级（`server/infrastructure/deepagent_runtime.py`）：先用 `inspect.getsource` 探测 `create_deep_agent` 是否已自带 `TodoListMiddleware`，缺失才手动补一个。这是补 deepagents 默认 middleware 的缺口，耦合到 `langchain.agents.middleware` 命名空间。**退出条件**：确认目标 deepagents 版本稳定自带该 middleware 后删除这段 fallback。
- 用量采集 `UsageCallback(AsyncCallbackHandler)`（同文件）：deepagents 没有公开的用量 API，LangChain 回调是唯一可用路径，依赖上游把 `config` 里的 `callbacks` 透传到每次模型调用。**已知风险**：上游改回调协议时用量会被标为「未知」而**不报错**，属于静默降级点，排查用量缺失时先看这里。

**不登记**：`arxiv` / `yahoo_finance_news` 用 LangChain Community wrapper 只是工具实现，替换成自写 HTTP 客户端不会改变任何图结构；`workspace_middleware.py` 的 `AgentMiddleware` / `ModelRequest` / `ModelResponse`、各处 `StructuredTool` 同理。

依赖立场：上述 langchain* 与 `langgraph` / `langgraph-checkpoint-sqlite` 都保留，前者是 deepagents 公开参数类型的直接依赖，后者是运行期依赖与用户可见的运行契约。需求见 F13。

## 后端执行和前端轮询

**Q: 前端怎么拿 DeepAgent 结果？**

前端只轮询和查看结果，不保持 WebSocket，不执行 Agent。Run 创建由微信等渠道、自动化或后端集成调用 API 完成，Runs 页面不提供手动创建表单。

```text
POST /api/runs
GET /api/runs
GET /api/runs/{run_id}
GET /api/runs/{run_id}/events?after={seq}
```

后端每次 API 都从 `workspace/runs/` 读盘返回。前端不直接访问服务器磁盘。

## Run 落盘

**Q: 服务重启怎么办？**

每个任务都是磁盘目录，服务重启不会丢状态。

```text
workspace/
  runs/
    index.json
    {run_id}/
      input.json
      state.json
      events.jsonl
      result.json
      lock.json
      delivery.json
```

`workspace/runs/index.json` 维护所有任务摘要和当前状态。列表页、微信补偿、重启扫描优先读这个索引，不依赖扫目录。
每个 run 可以引用 `session_id`，但 run 本身不保存长期对话历史。

```json
{
  "schema_version": 1,
  "runs": [
    {
      "id": "run_20260820_001",
      "agent_id": "assistant",
      "agent_name": "个人助理",
      "source": "web",
      "status": "running",
      "title": "总结坚果云文档",
      "created_at": "...",
      "updated_at": "...",
      "finished_at": null
    }
  ]
}
```

重启处理：

- `queued`：可以重新入队。
- `running`：检查 `lock.json`，如果 pid 或 heartbeat 失效，标记为 `interrupted`。
- `completed` / `failed` / `cancelled` / `interrupted`：不自动重跑。

第一版不自动恢复 `running`，避免重复写坚果云或重复发送微信。未来工具具备幂等键后再做自动恢复。

## Agent、Context、Knowledge

**Q: 工具和知识怎么建模？**

统一放进 Context。

```text
Agent = 人格 + 模型 + 默认 Context 集
Context = 归属隔离边界 + 目录 roots + tools + knowledge
Knowledge = Context 内部资源
Run = 一次不可变执行快照
```

Agent 只引用 Context：

```json
{
  "id": "assistant",
  "name": "个人助理",
  "model_id": "default",
  "system_prompt": "你是我的个人助理。",
  "context_ids": ["ctx_nutstore_work", "ctx_personal_notes"]
}
```

Context 自带归属、隔离、目录和知识：

```json
{
  "id": "ctx_nutstore_work",
  "name": "坚果云工作区",
  "kind": "nutstore",
  "owner": {
    "type": "user",
    "id": "default"
  },
  "scope": {
    "visibility": "private",
    "allowed_agents": ["assistant", "operator"]
  },
  "roots": [
    {
      "id": "docs",
      "label": "文档",
      "path": "/工作/文档",
      "access": "read"
    },
    {
      "id": "drafts",
      "label": "草稿",
      "path": "/工作/草稿",
      "access": "readwrite"
    }
  ],
  "tools": {
    "allow": ["nutstore_list", "nutstore_read_text"]
  },
  "knowledge": {
    "enabled": true,
    "index_path": "knowledge/index.json",
    "files_path": "knowledge/files"
  }
}
```

## Workspace 目录

**Q: 最终 workspace 目录结构是什么？**

```text
workspace/
  config.yaml

  agents/
    index.json
    assistant/
      agent.json
      skills/
        {skill_id}/
          SKILL.md
      scratch/
      artifacts/
      memories/
    operator/
      agent.json
      skills/
        {skill_id}/
          SKILL.md
      scratch/
      artifacts/
      memories/

  context/
    knowledge/
      files/
        handbook.md
        profile.md
    state/
      cache/

  runs/
    index.json
    run_20260820_001/
      input.json
      state.json
      events.jsonl
      result.json
      lock.json
      delivery.json

  sessions/
    index.json
    active.json
    checkpoints.sqlite
    wechat_main_private_wxid_xxx_assistant_012345abcd/
      state.json
      messages.jsonl
      runs.jsonl
      artifacts/

  channels/
    wechat/
      accounts.json
      sessions/
        main.json

  logs/
    platform-YYYY-MM-DD.log
```

微信和未来渠道都把连续对话写入 `workspace/sessions/{session_id}/messages.jsonl`，当前活跃会话由 `workspace/sessions/active.json` 维护。微信按 `wechat + account + peer + agent` 生成稳定 active key，再取该 key 指向的 `session_id`；用户发送“清空上下文 / 清空会话 / 开启新会话 / 新会话 / /clear / /new”时，通道层归档旧 session 并切换到新 session。微信图片输入会兼容 iLink 的 base64/data URL、直接媒体 URL 和 `media.encrypt_query_param`/`aeskey` CDN 加密媒体，下载或解密失败时写入 `image_warning` 日志。带 `session_id` 的 DeepAgent run 使用 `workspace/sessions/{session_id}/checkpoints.sqlite` 作为 LangGraph SQLite checkpointer，并以 `session_id` 作为 `thread_id` 恢复运行上下文；运行时只传当前 run 消息，避免历史消息和 checkpoint 重复叠加。`messages.jsonl` 继续作为渠道历史、审计和 `search_session` 检索数据；需要引用更早历史时，Agent 通过 `search_session` 查询。`search_session` 默认只查当前 session，传 `scope="related"` 时用 jieba 分词和子串评分搜索同一渠道身份下的相关 session，包括清空上下文前归档的旧 session。

DeepAgent 原生 filesystem 通过 `AgentFilesystemBackend(root_dir=workspace/agents/{agent_id}/workspace, virtual_mode=True)` 锚定到单个 Agent 私有目录。Agent 看到的 `/` 就是自己的目录，可读写其中的 `scratch/`、`artifacts/`、`skills/`、`memories/` 等内容，不能直接访问其它 Agent、Context、Runs、Sessions、配置文件或项目源码；启用 WebDAV 后通过 `/webdav/` 虚拟映射访问授权范围内的共享文档。当前不启用 DeepAgent `LocalShellBackend`，因此不向 Agent 暴露非沙箱 shell `execute`。

每个 Agent 的私有 skill 放在 `workspace/agents/{agent_id}/workspace/skills/{skill_id}/SKILL.md`，运行时传入 `skills=["/skills/"]`。DeepAgent 会扫描包含 `SKILL.md` 的子目录；没有 skill 时只会提示 Agent 可以在 `/skills/` 创建，是否创建由 Agent 在具体任务中通过文件工具自行决定，新建 skill 通常在下一次执行开始时被重新扫描后生效。

## Run 快照

**Q: 为什么 Run 要固化 Agent + Context 快照？**

因为任务创建后，后续修改 Agent 或 Context 不应该改变旧任务语义。当前 Context 是单数 `workspace/context/`，不再按 `{context_id}` 拆多套目录。

`workspace/runs/{run_id}/input.json` 必须保存：

- `run_id`
- `source`
- `session_id`
- `message`
- `agent_snapshot`
- `context_snapshot`
- 创建时间

其中 `context_snapshots` 包含当时允许的权限规则、tools、knowledge 文档列表和归属隔离配置。

## 目录权限

**Q: 坚果云或本地目录怎么限制？**

早期方案是工具调用使用 `root_id + relative path`；当前已被单一 WebDAV 同步根目录 + 相对路径权限规则替代。

```json
{
  "absolute_path": "/webdav/00AgentInbox/日报/2026-08-20.md"
}
```

后端根据 Run 快照解析：

```text
nutstore.root_path + context.webdav_sync.root_path + /webdav/ 后保留原层级的相对路径
```

本地缓存必须保留同步根目录下的相对目录结构：

```text
远端 /notebook/96备忘录/OpenWrt.md
本地 workspace/context/webdav/files/96备忘录/OpenWrt.md
映射 / 时工具 /webdav/96备忘录/OpenWrt.md
映射 /96备忘录 时工具 /webdav/OpenWrt.md
```

不能把远端路径拍平成 `webdav__96备忘录__OpenWrt.md` 这类文件名；否则同名文件、目录语义和 Markdown 相对图片引用都会失真。

必须校验：

- `/webdav/...` 后的相对路径不包含 `..`。
- 当前 Run 的 Agent 快照保存 `webdav.enabled/directories[]`，每项包含 path、permission、description；write（默认）包含读取，read 只允许读取；子目录自动优先，未选择范围拒绝，主/子 Agent 使用 DeepAgent 原生 FilesystemPermission。
- 全局保护目录、`webdav_permissions` 和 `protected` 已删除；原生文件工具和 Context 工具共用 Agent 映射权限，访问不得越出映射目录。
- 任何操作都不能逃出 `context.webdav_sync.root_path`。

## 微信

**Q: 微信回复算 Agent 工具吗？**

默认不算。微信收到消息后由平台创建 `source=wechat` 的 Run；DeepAgent 完成后，平台投递结果给对应微信会话，并在 `delivery.json` 记录投递状态。

只有未来需要 Agent 主动发微信时，才增加 `wechat_send` 工具，并且只给特定 Context 授权。

## 文档清理规则

**Q: 删除旧代码时文档怎么处理？**

主架构文档只保留当前目标架构，不再描述 Portfolio、Critique、Proxy、旧 WebSocket 聊天、旧 Session/Skill 管理等待删除模块。

日期化 `docs/superpowers/*` 文件视为历史实现记录，不作为当前架构依据。后续真正删除对应代码模块时，必须同步删除或归档对应历史设计/计划文档，避免搜索结果把人带回旧架构。

WebDAV 可写目录通过原生 HITL 审批后才写回，读取无需审批；本地共享资料挂载在 `/files/`，统一使用原生文件工具。Context 搜索/写入工具和 `/notes/` 已移除。

## Chat 输入框为何采用文档编辑器？

Chat 需要在输入区域即时呈现 Markdown，不能依赖 textarea 加独立预览。统一使用 Tiptap/ProseMirror 管理选区、撤销和输入法，草稿与发送接口继续使用 Markdown；不自写 contenteditable DOM 重绘。详见项目架构中的“Chat 实时 Markdown 编辑”。
