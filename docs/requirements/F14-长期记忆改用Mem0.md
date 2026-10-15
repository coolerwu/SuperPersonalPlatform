# F14 长期记忆改用 Mem0

- 状态：待确认（存在必须先拍板的开放问题）
- 日期：2026-10-15
- 范围：Agent 长期记忆子系统；不涉及 Chat/Run/微信通道的既有契约
- 前置依赖：F13（架构面收敛）先落地；本需求会改动 `pyproject.toml` 依赖，
  与 F13「不改依赖约束」的非目标不冲突，但两者不宜放进同一个提交

## 背景与目标

DeepAgent 原生 `MemoryMiddleware` 通过单一 `/memories/AGENTS.md` 管理长期记忆，
只有"把文件读进来、把文件写回去"这一层能力，缺少自动提取、合并、去重和淘汰。
结果是记忆只增不减，索引文件越长越倾向于整体影响上下文，记忆越多、干扰越大。

目标：引入 Mem0 作为长期记忆的提取、检索、合并与 CRUD 层，把"什么时候读、
什么值得写、写到哪个范围"交给现有的 Agent Loop 判断，而不是新增一个专职维护
记忆的 Agent。判断的目标不是让 Agent 记住更多，而是让它在该用的时候找到正确的
记忆，并且能够持续更新和淘汰不再适用的信息。

## 用户故事

- 作为使用者，我跨会话、跨 Agent 表达过的偏好和背景能被记住，但不会因为记忆
  变多而让每次对话都被无关历史挤占。
- 作为使用者，同一个 Agent 记住关于我的专属信息，不会泄漏给其它 Agent；其它
  Agent 对我的一般性偏好仍然可见。
- 作为维护者，我不需要在平台上再挂一个"记忆管理 Agent"，记忆的读写时机由现有
  Agent Loop 自行决定。
- 作为维护者，失效或过时的记忆可以被更新和删除，不是只能追加。

## 功能点清单

1. **记忆只分两种**
   - User Memory：用户跨 Agent 的通用偏好、背景和长期经验。
   - Agent-User Memory：某个用户与特定 Agent 之间的专属记忆。
   - Agent 自身稳定的角色和行为规则继续由 `system_prompt` / Skills 负责，不新设
     一套公共 Agent Memory。
2. **统一 Mem0Middleware**
   - 基于 DeepAgents 的 `AgentMiddleware` 实现。
   - 使用 Mem0 Python SDK；两类记忆共用同一个 Mem0 实例，通过 scope 隔离。
   - 不引入独立的 Memory Classifier Agent。
3. **记忆由 Agent Loop 主动管理**
   - middleware 注入提示词，让现有 Agent Loop 判断：何时需要检索、什么内容值得
     保存、保存到 User 还是 Agent-User、是否需要同步等待写入。
   - 两个工具：`search_memory(query, scope)` 按需检索，不在每个 turn 无条件召回；
     `save_memory(content, scope, wait=False)` 默认异步提交，仅在需要立即生效时
     同步等待。
   - Mem0 负责提取、去重、更新和基础 CRUD，不额外增加一轮 LLM 做 scope 分类。
4. **合并与淘汰**
   - 相同 scope 内由 Mem0 负责去重、合并与更新。
   - 不同 scope 的记忆可以互相补充，但不得互相覆盖。
   - 历史记忆允许更新和删除，不无限累积。
   - 记忆的适用边界与权限由 middleware 控制。
5. **配置面**
   - 记忆相关的连接与开关进入 `config.yaml`，并在 `/config` 可见可改，不散落在
     Agent 私有文件里。
   - 未配置或连接不可用时，Agent 仍能正常对话，记忆能力降级而不是让 Run 失败。

## 验收标准

- 同一段对话里，Agent 能判断出「这条是跨 Agent 的通用偏好」和「这条只对当前
  Agent 成立」并写入不同 scope；跨 scope 检索互不串味。
- 明确要求记住的信息，在下一次会话（含新 session、新 Run）能被 `search_memory`
  找回；没有要求记住的闲聊内容不被无差别写入。
- 修改或删除一条已有记忆后，后续检索不再返回旧内容。
- 一个 Agent 的 Agent-User Memory 不会出现在另一个 Agent 的检索结果里。
- 记忆库规模增长时，单次 Run 的 system prompt 体积不随记忆条数线性增长（即不是
  全量注入）。
- `save_memory` 默认异步返回，不会因为写入慢而阻塞回答；显式 `wait=True` 时写入
  完成后才继续。
- 记忆后端不可用时，对话正常完成，只是 `search_memory` / `save_memory` 返回可
  恢复的失败观察。
- `.venv/bin/python -m pytest`、`cd web && npm test`、`cd web && npm run build`
  全绿；`pyproject.toml`、`config.example.yaml`、`docs/project-architecture.md`
  与 `CLAUDE.md` 同步更新。

## 约束边界

- 不引入独立的 Memory Classifier Agent，也不新增一轮专职 scope 分类的 LLM 调用。
- 不在每个 turn 无条件召回全部记忆。
- 记忆是概率性行为约束加检索结果，不是程序强制保障；不得在文档或提交说明里把
  「Agent 一定会记住」写成确定性承诺。
- 本平台当前是单 token 单用户形态，不存在多租户：User Memory 的主体是唯一的平台
  主人，"跨 Agent"指的是跨 Agent 共享，不是跨人。
- 任何跨 Agent 可见的记忆都必须来自 User Memory；Agent-User Memory 的隔离边界由
  middleware 在工具参数层保证，不依赖 Agent 自觉。
- 记忆的后端连接信息属于敏感配置，沿用现有配置读写通道，不回传密钥到前端。

## 非目标

- 不做记忆的可视化管理页面（浏览、编辑、删除记忆条目）；如需另开需求。
- 不改 `system_prompt` 契约、不改 Skills 机制、不改 Run/Chat/微信通道。
- 不为旧 `MemoryMiddleware` 保留运行时兼容分支或双写。
- 不迁移历史 `AGENTS.md` 之外的任何 workspace 数据。

## 开放问题

1. **向量后端**。方案原文写「复用现有 Milvus」，但核查结果是本平台不存在 Milvus：
   本地仓库无 `mem0` / `pymilvus` 依赖、无向量库引用；生产机 `192.168.1.3` 的
   Docker 只有 caddy / jenkins / gitea / portainer / natfrp / postgres:16，没有
   Milvus 容器或镜像；venv 里也没有任何向量库或 embedding 包。需要先定后端：
   本地嵌入式（Mem0 支持的 Chroma 持久化到 workspace 目录）、复用生产机已有的
   `postgres:16` 装 pgvector、新部署 Milvus，还是别的。
2. **embedding 模型**。当前 `config.yaml` 的 `llm.models[]` 只定义 chat 模型
   （`provider: openai_compatible`），没有 embedding 模型定义，而 Mem0 必须有
   embedding 才能建索引。需要定 provider、模型名和密钥，并决定它是否复用
   `llm.models[]` 结构还是新增一段配置。
3. **`/memories/` 目录的去留**。CLAUDE.md 与 `docs/project-architecture.md` 目前
   把 `/memories/` 定义为「长期记忆，详细规则由 MemoryMiddleware 提供」。改用 Mem0
   后该 middleware 退役，这个虚拟目录是保留为普通可写目录（不再自动注入），还是
   一并移除，需要明确。
4. **现有记忆数据的处置**。生产机上只有两个文件、合计约 8.5 KB：
   `agents/wife-agent/workspace/memories/AGENTS.md`（4117 字节）和
   `agents/default/workspace/memories/work.md`（4429 字节，注意它不是
   `AGENTS.md`，未被 MemoryMiddleware 加载）。按用户偏好是一次性人工整理，
   需要确认是录入 Mem0、留作普通文件，还是直接删除。
5. **「多 Pod 共用 Milvus」这条风险**在本平台当前形态下不成立：架构文档明确
   单进程 FastAPI + 用户级 systemd，不引入 Redis/Celery，也不拆独立 worker
   service。若未来真的多实例部署，再单独评估并发写入与冲突。
6. **与 F13 的交付顺序**。F13 仍是「进行中」，其改动尚未提交、也未上生产
   （生产机 HEAD 仍为 `4d38bb6`）。F14 依赖变更应排在 F13 之后，避免两个大改动
   混进同一个提交。
