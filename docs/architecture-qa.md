# 架构问答

本文件记录 DeepAgent 重构后的目标架构决策。主原则是：后端执行、磁盘落盘、前端轮询；Agent 只引用 Context，Context 是归属隔离边界并自带 Knowledge。

## 保留和删除边界

**Q: 这次重构保留什么？**

保留：

- DeepAgent / LangGraph 后端执行。
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
      notes/
      artifacts/
      memories/
    operator/
      agent.json
      skills/
        {skill_id}/
          SKILL.md
      scratch/
      notes/
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

微信和未来渠道都把连续对话写入 `workspace/sessions/{session_id}/messages.jsonl`，当前活跃会话由 `workspace/sessions/active.json` 维护。微信按 `wechat + account + peer + agent` 生成稳定 active key，再取该 key 指向的 `session_id`；用户发送“清空上下文 / 清空会话 / 开启新会话 / 新会话 / /clear / /new”时，通道层归档旧 session 并切换到新 session。微信图片输入会兼容 iLink 的 base64/data URL、直接媒体 URL 和 `media.encrypt_query_param`/`aeskey` CDN 加密媒体，下载或解密失败时写入 `image_warning` 日志。带 `session_id` 的 DeepAgent run 使用 `workspace/sessions/checkpoints.sqlite` 作为 LangGraph SQLite checkpointer，并以 `session_id` 作为 `thread_id` 恢复运行上下文；运行时只传当前 run 消息，避免历史消息和 checkpoint 重复叠加。`messages.jsonl` 继续作为渠道历史、审计和 `search_session` 检索数据；需要引用更早历史时，Agent 通过 `search_session` 查询。`search_session` 默认只查当前 session，传 `scope="related"` 时用 jieba 分词和子串评分搜索同一渠道身份下的相关 session，包括清空上下文前归档的旧 session。

DeepAgent 原生 filesystem 通过 `FilesystemBackend(root_dir=workspace/agents/{agent_id}, virtual_mode=True)` 锚定到单个 Agent 私有目录。Agent 看到的 `/` 就是自己的目录，可读写其中的 `scratch/`、`notes/`、`artifacts/`、`skills/`、`memories/` 等内容，不能访问其它 Agent、Context、Runs、Sessions、配置文件或项目源码。当前不启用 DeepAgent `LocalShellBackend`，因此不向 Agent 暴露非沙箱 shell `execute`。

每个 Agent 的私有 skill 放在 `workspace/agents/{agent_id}/skills/{skill_id}/SKILL.md`，运行时传入 `skills=["/skills/"]`。DeepAgent 会扫描包含 `SKILL.md` 的子目录；没有 skill 时只会提示 Agent 可以在 `/skills/` 创建，是否创建由 Agent 在具体任务中通过文件工具自行决定，新建 skill 通常在下一次执行开始时被重新扫描后生效。

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
nutstore.root_path + context.webdav_sync.root_path + /webdav/ 后的相对路径
```

本地缓存必须保留同步根目录下的相对目录结构：

```text
远端 /notebook/96备忘录/OpenWrt.md
本地 workspace/context/webdav/files/96备忘录/OpenWrt.md
工具 /webdav/96备忘录/OpenWrt.md
```

不能把远端路径拍平成 `webdav__96备忘录__OpenWrt.md` 这类文件名；否则同名文件、目录语义和 Markdown 相对图片引用都会失真。

必须校验：

- `/webdav/...` 后的相对路径不包含 `..`。
- 写操作必须匹配当前 Run 的 Context 快照中 `writable=true` 且 `protected=false` 的最长前缀权限规则。
- 父级 `protected=true` 可以覆盖整个同步根，子目录可通过更具体的权限规则开放写入。
- 任何操作都不能逃出 `context.webdav_sync.root_path`。

## 微信

**Q: 微信回复算 Agent 工具吗？**

默认不算。微信收到消息后由平台创建 `source=wechat` 的 Run；DeepAgent 完成后，平台投递结果给对应微信会话，并在 `delivery.json` 记录投递状态。

只有未来需要 Agent 主动发微信时，才增加 `wechat_send` 工具，并且只给特定 Context 授权。

## 文档清理规则

**Q: 删除旧代码时文档怎么处理？**

主架构文档只保留当前目标架构，不再描述 Portfolio、Critique、Proxy、旧 WebSocket 聊天、旧 Session/Skill 管理等待删除模块。

日期化 `docs/superpowers/*` 文件视为历史实现记录，不作为当前架构依据。后续真正删除对应代码模块时，必须同步删除或归档对应历史设计/计划文档，避免搜索结果把人带回旧架构。
