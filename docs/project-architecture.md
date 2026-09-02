# Project Architecture

本文件是当前项目架构入口。DeepAgent 重构的详细决策记录在 `docs/architecture-qa.md`，后续实现以该问答中的模型为准。

## Current Direction

- 项目仍是一个由 FastAPI 提供后端、React + Vite 提供前端、后端统一服务静态资源的单体应用。
- 后端 Python 版本目标为 3.12.x，默认监听端口为 `8888`。
- 运行入口仍是 `./run-dev.sh`、`./run-prod.sh` 和底层 `./run.sh`。
- 生产环境仍使用已提交的 `web/dist`，生产启动不现场构建前端。
- 工作区默认仍是项目目录下 `.super-personal-platform`，可通过 `--workspace` 覆盖。
- 目标产品边界收缩为：DeepAgent/LangGraph 后端任务执行、微信个人号入口、坚果云 WebDAV Context、单 token 登录、基础配置、基础日志和生产更新。

## Architecture Q&A

`docs/architecture-qa.md` 是本轮重构的产品和技术问答来源，已确定：

- 前端 Chat 页面不直接执行 Agent，而是通过后端创建 `source=web_chat` 的 run；Runs 页面只查看落盘任务、状态、事件和结果，不提供手动创建 Run 入口。
- DeepAgent 在后端运行，状态、事件、结果全部落盘。
- DeepAgent 运行时优先使用 LangGraph/DeepAgent `astream` 读取模型增量和图更新；运行时先把 LangGraph stream chunk 转换成明确的 `DeepAgentStreamEvent` / `RunEventPayload` 对象，再追加到 `workspace/runs/{run_id}/events.jsonl`。增量正文以 `assistant_delta` 事件作为唯一实时入口，并节流写入 `workspace/runs/{run_id}/partial.json` 快照；`running`、`agent_update`、`stream_fallback`、`image_attachments_textified` 等公开运行事件会同步聚合成 `partial.thinking[]`，作为 Chat 页面刷新后的 run 级思考过程恢复来源。若当前 deepagents 版本或 fake agent 不支持 stream，则自动回退到 `ainvoke`，不影响最终结果。
- `workspace/runs/index.json` 维护所有 run 的摘要和当前状态。
- 每个 run 使用 `workspace/runs/{run_id}/` 独立目录保存 `input.json`、`state.json`、`events.jsonl`、`result.json`、`lock.json` 和 `delivery.json`。
- 统一调度器使用 `workspace/schedules/` 落盘调度定义和状态；WebDAV Context 同步和未来 Agent 定时任务共用这一套调度机制。
- `workspace/sessions/index.json` 维护所有长期会话索引；长期 session 对微信和未来渠道默认开启。`workspace/sessions/active.json` 维护渠道身份到当前活跃会话的绑定；微信、API 和未来渠道共享 `workspace/sessions/{session_id}/`，每个 run 只引用 `session_id`，DeepAgent/LangGraph 运行时状态统一写入 `workspace/sessions/checkpoints.sqlite`。
- `Agent` 保存人格、模型、可选 Context 绑定和 DeepAgent 运行选项。
- `Agent` 还保存 DeepAgent 运行选项，包括 `max_iterations`、运行名、debug、Todo List、Agent 私有 filesystem、长期记忆开关、工具 ID、tool interrupt、middleware、subagents 和结构化输出等配置；当前后端实际执行已消费 `max_iterations`、`name`、`debug`、`todo_list`、`use_longterm_memory`、`interrupt_on`、`tools` 和 `middleware` 中的 `rhythmic_delivery`。`SkillImprovementMiddleware` 在运行时代码中默认启用，不需要 workspace 配置开关。`filesystem.enabled` 作为配置兼容字段保留，但 DeepAgent 运行时始终把原生 filesystem 锚定到当前 Agent 私有目录。
- 平台工具定义在代码中，不放入 workspace 散落配置；Agent 的 `deepagent.tools` 只是授权选择。当前平台工具为 `search_context`、`search_session`、`arxiv`、`yahoo_finance_news`、`write_context`、`browser_extract` 和 `schedule`。授权 `browser_extract` 时运行时会同时注入隐藏的 `browser_search` 工具；搜索引擎固定为 Bing，不提供 workspace 配置或 Agent 入参选择。
- 当前默认 Context 收敛为唯一的 `workspace/context/`；知识文件放在 `workspace/context/knowledge/files/`，作为工具读写的目录。
- Run 创建时必须固化 Agent + Context + Knowledge 快照。
- 微信收到消息后按 `wechat + account + peer + agent` 生成稳定 active key，通过 `workspace/sessions/active.json` 找到当前 `session_id`，再创建 `source=wechat` 的 run；用户在微信发送“清空上下文 / 清空会话 / 开启新会话 / 新会话 / /clear / /new”或 `/session new` 时，通道层会归档旧 session 并为同一渠道身份切换到新的 active session。微信通道还内置 `/session help`、`/session status`、`/session list` 和 `/session change <编号或 session_id>`；这些指令由通道层直接消费，不创建 DeepAgent run。`/session change` 只能切换同一个 `wechat + account + peer + agent` 身份下的历史 session，不能跨用户、跨群、跨微信账号或跨 Agent。带 `session_id` 的 DeepAgent run 使用同一个 SQLite checkpointer 恢复 LangGraph 状态，只把当前 run 消息作为本次输入；`messages.jsonl` 继续保存渠道历史、审计和 `search_session` 检索数据，完成后由平台投递微信回复。

## Target Workspace Layout

```text
workspace/
  config.yaml

  context/
    knowledge/
      files/
    webdav/
      files/
      index.json
    state/
      cache/

  agents/
    index.json
    {agent_id}/
      agent.json
      skills/
        {skill_id}/
          SKILL.md
      scratch/
      notes/
      artifacts/
      memories/
        AGENTS.md
      improvements/
        reflections/
          {run_id}.md
        reviews/
          {run_id}.md
        changes/
          {timestamp}_{change_id}.json
      meditations/
        {timestamp}_{run_id}.json

  browser_profiles/
    {agent_id}/

  runs/
    index.json
    {run_id}/
      input.json
      state.json
      events.jsonl
      partial.json
      result.json
      lock.json
      delivery.json

  deliveries/
    index.json
    {delivery_id}/
      definition.json
      state.json
      events.jsonl

  schedules/
    index.json
    {schedule_id}/
      definition.json
      state.json
      events.jsonl
      lock.json

  sessions/
    index.json
    active.json
    checkpoints.sqlite
    {session_id}/
      state.json
      messages.jsonl
      runs.jsonl
      attachments/
      artifacts/

  channels/
    wechat/
      accounts.json
      sessions/

  logs/
    platform-YYYY-MM-DD.log
```

## Target Backend Boundaries

- `server/adapter` 只保留薄 HTTP API：认证、run 创建/查询/事件轮询、微信账号管理、配置/日志/更新、静态资源。
- `server/app` 负责应用服务：DeepAgent run 服务、Agent/Context 工作区服务、坚果云服务、微信通道服务、系统日志/更新服务。
- `server/domain` 只保留框架无关的领域对象、配置规则、错误类型和 run/context/agent 数据约束；`server/domain/agent_config.py` 只描述 Agent/LLM/DeepAgent 选项配置，不封装 deepagents 运行时。
- `server/infrastructure` 负责配置加载、DeepAgent/LangChain 运行封装、坚果云 WebDAV、微信 iLink 客户端、FastAPI app 装配和 cookie session；DeepAgent 运行封装集中在 `server/infrastructure/deepagent_runtime.py`，其入口只接收 `instructions`、完整 `messages` 和结构化 `options`，不再同时接收“当前消息”和“历史消息”两套参数。

## Target API Shape

Run API：

```text
POST /api/runs
GET /api/runs
GET /api/runs/{run_id}
GET /api/runs/{run_id}/events?after={seq}
```

`POST /api/runs` 可接受可选 `session_id` 和 `attachments[]`。未传 `session_id` 时按独立一次性 run 处理，不启用 checkpoint；传入 `session_id` 时，运行时使用 `workspace/sessions/checkpoints.sqlite` 作为 LangGraph SQLite checkpointer，并把 `configurable.thread_id` 设为该 `session_id`。微信通道会传入全局长期会话 ID，并把图片等附件保存到 `workspace/sessions/{session_id}/attachments/` 后再执行 run。该接口保留给渠道接入、自动化和后端集成使用，当前前端 Runs 页面不暴露手动创建入口。

Chat API：

```text
POST /api/chat/session
POST /api/chat/session/new
GET /api/chat/sessions?agent_id={agent_id}
POST /api/chat/session/change
GET /api/chat/sessions/{session_id}/messages
POST /api/chat/messages
```

页面 Chat 使用 `channel=web`、`channel_account_id=default`、`peer_type=private`、`peer_id=browser` 和当前 `agent_id` 在 `workspace/sessions/active.json` 中维护活跃长期会话。`GET /api/chat/sessions` 只列出同一 web 身份和当前 Agent 下的相关 session；`POST /api/chat/session/change` 复用 `SessionService.switch_active` 切换 active session，不允许前端打开任意 session ID。`POST /api/chat/messages` 创建 `source=web_chat` 的普通 DeepAgent run 并后台执行；前端随后只轮询 `/api/runs/{run_id}/events?after={seq}`，按 `assistant_delta` 事件增量更新 assistant 气泡，完成或失败后再读取 run 详情和 session messages 对齐最终历史。

Schedule 落盘模型：

```text
GET /api/schedules
POST /api/schedules
GET /api/schedules/{schedule_id}
PUT /api/schedules/{schedule_id}
DELETE /api/schedules/{schedule_id}
POST /api/schedules/{schedule_id}/run-now

workspace/schedules/index.json
workspace/schedules/{schedule_id}/definition.json
workspace/schedules/{schedule_id}/state.json
workspace/schedules/{schedule_id}/events.jsonl
workspace/schedules/{schedule_id}/lock.json
```

后台统一 Scheduler 每 5 秒扫描轻量调度索引，只判断 `next_run_at` 是否到期，不执行高频 WebDAV 同步。到期后按 `definition.type` 分发：`webdav_sync` 执行 Context WebDAV 同步并写调度事件；`maintenance_cleanup` 执行 15 天保留期清理并写调度事件；`agent_meditation` 每 86400 秒执行一次每日冥想，每个配置中的用户 Agent 都有独立的内置 `agent_meditation_{agent_id}` 任务、状态、事件和手动运行入口，在该 Agent 没有 queued/running run、未完成投递队列或浏览器 profile lock 时，为该 Agent 创建 `source=system`、`metadata.kind=meditation` 的普通 DeepAgent run；`agent_run` 创建普通 `workspace/runs/{run_id}/` 并执行 DeepAgent。`lock.json` 用于避免重复执行，并记录 `pid`、`created_at` 和 `heartbeat_at`；执行期间每 15 秒刷新 heartbeat。服务崩溃或 worker 卡死后，如果状态停在 `running` 且 lock 持有进程已不存在，或 heartbeat 超过 120 秒未刷新，下一次 tick 会把当前 run 标记失败、清理 lock，并按同一重试策略继续。调度执行失败后会进入 `retrying` 状态，默认 1 分钟后重试，最多 3 次；重试耗尽后才标记 `failed` 并进入下一次正式触发周期，成功后重试计数清零。Delivery 队列由独立后台 loop 每 5 秒扫描 `workspace/deliveries/`，避免慢速 Agent run 阻塞已排队的消息投递。

`/api/schedules` 是定时任务管理页面使用的后端入口。前端只允许创建、编辑和删除 `agent_run` 类型任务，字段核心为 `prompt + agent_id + trigger`；内置 `context_webdav_sync`、`maintenance_cleanup` 和每个 Agent 各自的 `agent_meditation_{agent_id}` 由系统自动生成，只能查看状态和手动 `run-now`，不能通过页面编辑或删除。当前触发器支持 `interval`、5 字段 `cron` 和 `once`。Agent 也可以在被授权 `schedule` 平台工具后，通过同一个 ScheduleService 创建、查看、更新和删除定时任务；工具只允许管理由该工具在当前 `agent_id + session_id` 下创建的任务，并把微信来源 run 创建的定时任务结果回发到原微信会话。

微信 API 继续保留 `/api/channels/wechat/*` 账号管理和登录生命周期接口。

系统 API 只保留日志和生产更新能力；配置文件读写统一走 Workspace 文件 API，可视化配置页也复用同一个读写入口。

Workspace 文件 API：

```text
POST /api/workspace/list
POST /api/workspace/read
PUT /api/workspace/write
POST /api/workspace/delete
```

这些接口只允许访问 active workspace 内部路径，用于前端“工作目录”页面浏览、编辑 UTF-8 文本文件和删除非固定路径。`config.yaml` 通过写入入口保存时仍执行配置校验；`config.yaml` 和根层固定目录 `agents/`、`browser_profiles/`、`context/`、`runs/`、`schedules/`、`sessions/`、`channels/`、`logs/` 不能删除，其它 workspace 内文件或目录允许删除。

System API 额外保留：

```text
POST /api/system/webdav-context/test
POST /api/system/webdav-context/sync
POST /api/system/maintenance/preview
POST /api/system/maintenance/run
GET /api/system/browser-profiles
POST /api/system/browser-auth/sessions
GET /api/system/browser-auth/sessions/{session_id}
GET /api/system/browser-auth/sessions/{session_id}/screenshot
POST /api/system/browser-auth/sessions/{session_id}/navigate
POST /api/system/browser-auth/sessions/{session_id}/click
POST /api/system/browser-auth/sessions/{session_id}/type
POST /api/system/browser-auth/sessions/{session_id}/press
POST /api/system/browser-auth/sessions/{session_id}/finish
POST /api/system/browser-auth/sessions/{session_id}/cancel
```

`/webdav-context/test` 可使用配置页当前草稿或已保存配置测试坚果云 WebDAV 连接，只返回目标 URL、HTTP 状态和是否成功，不回传账号密码；`/webdav-context/sync` 现读已保存的 `workspace/config.yaml`，手动执行一次 Context WebDAV 同步，用于配置变更后立即制作本地缓存，而不必等待后台间隔或重启服务。

`/maintenance/preview` 只计算清理计划不删除文件；`/maintenance/run` 执行清理。当前默认保留期统一为 15 天：删除超过保留期且已终态的 run、超过保留期未活跃且没有活动 run 引用、也没有被 `workspace/sessions/active.json` 指向的 session、这些已删 session 在 `workspace/sessions/checkpoints.sqlite` 里的 checkpoint/writes 行、旧调度事件、旧平台日志、Agent scratch 和 Context cache 里的旧文件，以及很旧的孤立 lock；同时会清理 `active.json` 中指向已不存在 session 的脏 binding。知识库、WebDAV 文本/图片缓存、微信登录态和 Agent 长期记忆不做自动删除。自动清理不再使用单独后台 loop，而是作为 `workspace/schedules/maintenance_cleanup/` 内置定时任务落盘并显示在 `/schedules`。

浏览器授权 API 用于后台管理员操作服务器上的 Playwright persistent browser profile。Profile 固定按 Agent 隔离在 `workspace/browser_profiles/{agent_id}/`，不再按微信账号或单独 service 目录拆分；授权会话启动后前端通过截图、点击、键盘输入和跳转 API 操作同一个 headless browser context，完成或取消时关闭浏览器并释放 `profile.lock.json`。Agent 不能直接调用这些授权 API，也不能选择 profile 路径。

## Frontend Routes

- `/chat` 是页面 Chat 工作区，提供 Agent 选择、同身份历史 session 切换、新会话、文本输入和 assistant 流式气泡；消息进入长期 session，执行仍由后端 DeepAgent run 完成。Chat 气泡运行中会把后端 `running`、`agent_update`、`stream_fallback`、`image_attachments_textified` 等可公开运行事件聚合到“思考过程”区域并展开显示，`assistant_delta` 只作为正文增量；run 结束后正文保留为主内容，“思考过程”自动折叠并可手动展开查看。页面刷新或切换 session 后，Chat 先读 `workspace/sessions/{session_id}/messages.jsonl` 展示正文，再按 assistant 消息的 `run_id` 读取 `workspace/runs/{run_id}/partial.json` 恢复已折叠的思考过程。
- `/`, `/runs`, `/agents` 都进入新的 Runs 工作区；`/agents` 只是旧入口跳转，不恢复旧 Agent 管理页面。Runs 工作区只承担运行记录查看、状态轮询、事件与结果展示，不提供 Prompt/Agent ID 表单或手动创建按钮。
- `/workspace` 展示真实 workspace 文件浏览器，可查看和编辑 UTF-8 文本文件，并可删除非固定路径；`config.yaml` 在这里按原生 YAML 文本展示和编辑，不承载专用配置表单；`config.yaml` 和根层固定骨架目录不可删除。
- 侧栏只保留一个 `/config` 配置主菜单，右侧用栏目切换基础配置、Providers 和 Agents；保存仍写回 `workspace/config.yaml` 并经后端配置校验。
- `/config` 基础配置栏目只承载访问 Token、服务监听和坚果云 WebDAV 等基础配置；访问 Token 按明文输入展示。
- `/config` 基础配置栏目还承载 `browser.proxy`、`browser.timeout_ms` 和 `browser.allow_private_hosts`，用于 `browser_extract` 的 Playwright headless browser。微信账号代理只服务微信 iLink 连接，不自动复用为浏览器代理；`allow_private_hosts` 只允许管理员显式信任的 hostname 或 `.domain` 后缀在解析到内网/私有 IP 时继续访问。
- `/config` 基础配置栏目还维护 `maintenance` 清理配置，默认启用、保留 15 天、每天运行一次；也可设置为 dry run 只预览不删除。
- `/config` 的 Context WebDAV 同步区域提供“测试连接”和“立即同步”操作；测试连接使用当前表单草稿测试 WebDAV，不保存配置且不回传 secret；立即同步调用后端手动同步接口读取已保存的 `workspace/config.yaml`，立刻把坚果云远端文件缓存到 `workspace/context/webdav/` 并返回文本/图片资源数量；保存配置本身仍只负责校验并写回 `config.yaml`。
- `/providers` 是配置页内的模型 Provider 栏目直达入口，维护 `llm.default_model_id` 和 `llm.models[]`，包括 provider 类型、base URL、API key、模型名、temperature 和图片能力；Provider 至少保留一个，删除被引用的 Provider 时前端会把默认模型和 Agent 引用迁移到剩余模型。
- `/agent-config` 是配置页内的 Agent 栏目直达入口，维护 `agents.definitions[]`，包括人格提示词、模型选择、Context 绑定和 DeepAgent 运行选项；Agent 工具通过弹窗里的可视化卡片选择，当前写入 `agents.definitions[].deepagent.tools`，平台工具包括 `search_context`、会话历史检索工具 `search_session`、学术检索工具 `arxiv`、轻量财经新闻工具 `yahoo_finance_news`、需要确认的 `write_context`、浏览器能力 `browser_extract` 和用于对话式创建定时任务的 `schedule`；授权 `browser_extract` 会同时提供固定 Bing 的 `browser_search`，前端不单独展示搜索引擎或搜索工具选择；不再展示可手填的 `Tool IDs` 输入框；`/agents` 仍跳转 Runs，不作为配置页路径。
- `/schedules` 是定时任务管理页面，读取 `workspace/schedules/index.json` 和每个任务详情，支持查看内置 WebDAV 同步任务和维护清理任务、创建/编辑/删除 Agent 定时任务、启用/停用、立即运行和查看调度事件；任务创建表单只暴露 `prompt + agent + trigger` 等必要字段，不在前端执行 Agent。
- `/browser` 是浏览器授权页，读取 `config.yaml` 中的 Agent 列表，允许管理员按 Agent 启动一个截图式 Playwright 授权会话，profile 路径固定为 `workspace/browser_profiles/{agent_id}/`；授权页提供 Agent/profile 列表、目标 URL、截图点击、文本输入、按键、完成和取消操作，不放入 `/config` 或 `/system`。
- `/wechat` 展示微信账号列表、当前账号详情、二维码、运行态、绑定 Agent、投递路径和通道日志，并提供新增、删除、启动和停止操作；微信账号不在 `/config`、`/providers` 或 `/agent-config` 重复展示。
- `/wechat` 的每个账号都可以独立选择默认 Agent；微信登录态继续按 `workspace/channels/wechat/sessions/{account_id}.json` 隔离保存，不作为聊天历史；长期聊天会话统一写入 `workspace/sessions/{session_id}/`。
- `/system` 是运维页，展示生产更新、工作目录入口和系统日志；不再承载系统配置编辑、浏览器授权或架构说明。系统配置入口在 `/config`，Provider 在 `/providers`，Agent 在 `/agent-config`，浏览器授权入口在 `/browser`，文件级查看/编辑入口保留在 `/workspace`。
- 前端是运行台，不做营销首页；第一屏直接展示可操作的后端 run 工作区。
- Runs 工作区通过 1 分钟一次的轮询读取后端落盘状态，但前端必须保留当前详情快照、只在返回内容实际变化时更新状态，避免每次拉取 `workspace/runs/index.json` 时出现短暂重刷或 `unknown` 状态闪动。
- Runs 详情区使用更短的 2 秒轮询读取选中 run 的详情和事件；运行中若存在 `partial.json` 且尚无最终 `result.json`，结果预览显示 partial 内容和“正在生成”状态。

## Retained Capabilities

- 单 token 登录，登录状态通过 HttpOnly cookie 保存。
- 配置从 active workspace 的 `config.yaml` 读取。
- 个人微信通过 Tencent iLink Bot HTTP API 接入。
- 微信文本和图片输入都进入当前活跃长期 session。由于微信客户端常把图片和文字拆成多条消息发送，通道层会把同一个 `wechat + account + peer + agent` active key 下的文本和图片交给 `DebouncedTaskExecutor` 按 key 延迟合并：单条消息默认等待 5 秒；同一窗口发现多条消息后，按最后一条消息再等待最多 15 秒，窗口内的新消息会重置计时并合并成同一次 run，用最后一条消息的 `context_token` 投递回复；用户发送 `/done`、`/flush`、`发完了`、`结束输入` 等完成指令时立即 flush 当前 pending 输入且不把完成指令写入 run。用户发出明确清空/新会话命令或 `/session new` 时不会创建 run，而是直接轮换 `workspace/sessions/active.json` 中对应 binding，让后续消息进入新的 `workspace/sessions/{session_id}/`；`/session change <编号或 session_id>` 会切换到当前微信身份相关的历史 session；`/session help/status/list` 只返回指令说明或会话状态。所有 `/session ...` 指令都由微信通道层直接处理，不进入消息合并窗口；`new/change` 会取消当前 peer/agent 尚未 flush 的待处理输入，避免旧消息写入新切换的 session。图片解析支持 iLink 的 `image_item`/`file_item`、base64/data URL、直接媒体 URL，以及 `media.encrypt_query_param`/`aeskey` 形式的 CDN 加密媒体；下载或解密失败会记录 `image_warning` 日志而不是静默丢失。带 `session_id` 的 DeepAgent 执行使用 `workspace/sessions/checkpoints.sqlite` 恢复同一 `session_id` 的 LangGraph checkpoint，运行时只传当前 run 消息，避免把 `messages.jsonl` 历史和 checkpoint 状态重复叠加；需要引用更早历史时，Agent 通过 `search_session` 查询 `messages.jsonl`。模型未在 Provider 中启用 `supports_images` 时，后端不会把图片二进制或 `image_url` 传给 DeepAgent，而是把图片附件文件名、MIME、大小和 workspace 路径追加为文本说明后继续调用当前主模型；该降级不读取图片画面内容。
- 坚果云通过 WebDAV 接入，默认 endpoint 为 `https://dav.jianguoyun.com/dav/`。
- DeepAgent 依赖 `deepagents>=0.7.8,<0.8`、LangGraph 和 `langgraph-checkpoint-sqlite`；后端任务执行结果必须落盘，带 `session_id` 的任务还会把 LangGraph checkpoint 写入统一 SQLite 文件。生产依赖同时固定 `cryptography>=38,<49`，避免部署时走不兼容本机 Rust 工具链的源码构建路径。
- `search_context` 检索 `workspace/context/knowledge/files/` 中的 `.md`、`.txt`、`.json`、`.jsonl` 文本知识，返回 `/files/...` 工具路径、分数和片段。
- `search_session(query, top_k, role, scope)` 检索会话历史，`scope` 默认为 `current`，只查当前 run 的 `session_id` 对应 `workspace/sessions/{session_id}/messages.jsonl`；传 `scope="related"` 时，按当前 session 的 `active_key` 或 `channel + channel_account_id + peer_type + peer_id + agent_id` 搜索同一渠道身份下的相关 session，包括清空上下文前归档的旧 session。Agent 不能传任意 `session_id`。搜索使用 jieba 对中文 query 分词，并结合精确子串命中评分；返回 session 元数据、是否当前 active、消息序号、角色、时间、run ID、片段和附件元数据。该工具用于用户引用“刚才/前面/之前/那张图/那个链接”等同一微信或 API 长期会话中的历史消息，也用于用户给关键词要求找相关旧会话。
- `arxiv(query, top_k)` 使用 LangChain Community 的 arXiv wrapper 检索论文，依赖 `arxiv` 包，运行时内置全局 3 秒请求间隔，作为免费学术/知识工具授权给需要的 Agent。
- `yahoo_finance_news(ticker, top_k)` 使用 LangChain Community 的 Yahoo Finance News 工具和 `yfinance` 获取公开股票代码相关新闻，定位为轻量财经新闻上下文，不作为交易级行情或完整市场数据源。
- 平台工具面向外部下游的可恢复异常默认返回结构化 `ok=false` JSON 给 DeepAgent 处理，包括浏览器导航/检索失败、公开数据源超时、调度工具参数校验或权限边界失败；Agent 应基于该观察结果换工具、换来源、询问用户或解释限制。只有平台自身无法继续执行的错误，例如 run 落盘失败、配置加载失败、运行时初始化崩溃，才应向上抛出并标记整个 run failed。
- `write_context(type, absolute_path, content, mode)` 写入同一知识目录；当前只支持 `type="knowledge"`，`absolute_path` 必须是 `/files/...` 或可写 `/webdav/...` 工具路径，`mode` 支持 `append`、`overwrite` 和 `create`，工具说明要求 Agent 仅在用户明确确认后调用。权限、路径或模式错误时工具返回 `ok=false` 的结构化结果，让 Agent 继续解释失败原因，不应让整个 run 失败；WebDAV 写入失败会附带 `diagnostics`，包含 `reason`、`resolved_relative_path`、`matched_permission_path`、`matched_permission`、`sync_root_path`、`allowed_extensions`，以及发现工具路径误带同步根目录时的 `suggested_tool_path`。该工具只用于共享知识库、文档和参考资料，不用于“记住我”“存入记忆”“用户偏好”“后续对话规则”等请求。
- Context 可配置一个坚果云 WebDAV 同步根目录 `context.webdav_sync.root_path`，远端实际路径按 `nutstore.root_path + context.webdav_sync.root_path` 解析；本地文件缓存固定落在 active workspace 的 `workspace/context/webdav/files/`，索引固定写入 `workspace/context/webdav/index.json`。
- WebDAV 同步触发已纳入统一调度器：启动时后端会根据 `nutstore.enabled`、`context.webdav_sync.enabled`、`context.webdav_sync.interval_seconds` 和权限配置生成/更新内置 `workspace/schedules/context_webdav_sync/definition.json`；调度轮询间隔固定为 5 秒，但实际同步间隔仍由 `context.webdav_sync.interval_seconds` 控制，且配置校验要求不少于 60 秒。
- 历史配置 `context.webdav_roots` 已退役，不再由后端或前端运行时迁移；生产升级前必须一次性迁移为 `context.webdav_sync.root_path` 加 `context.webdav_permissions[]`。
- `context.webdav_permissions[]` 是同步根目录下的相对路径权限规则，使用最长前缀匹配。父级可设为 `readable=true, protected=true`，子目录可单独设为 `writable=true, protected=false`；这样只需要同步一次，检索结果不会因为父子 root 重叠而重复。
- `search_context` 合并本地 `/files/...` 与 WebDAV `/webdav/...` 缓存检索；WebDAV 工具路径不再包含 root ID。同步根目录下的远端相对路径会原样保留到本地，例如远端 `/notebook/96备忘录/OpenWrt.md` 会缓存为 `workspace/context/webdav/files/96备忘录/OpenWrt.md`，工具路径为 `/webdav/96备忘录/OpenWrt.md`，`index.json` 记录 remote path、tool path、cache path、etag、mtime、权限和类型。用户询问“最近笔记/最新文档/recent notes”时，`search_context` 会额外返回按 WebDAV `modified` 排序的 `recent_documents`，避免纯 BM25 因没有关键词命中而误判没有笔记。`write_context` 只能写匹配到 `writable=true` 且 `protected=false` 权限规则的 `/webdav/...` 路径；`protected=true` 路径可读可检索但不可写、覆盖或删除，Agent 不应尝试写回搜索命中的受保护原文，默认改写到 `/files/...` 或明确开放的 WebDAV inbox。
- WebDAV 同步会解析 Markdown 里的 `![...](...)` 和 `<img src="...">`，把被引用的 `.png`、`.jpg`、`.jpeg`、`.gif`、`.webp`、`.svg` 按相对目录结构作为二进制资源缓存到 `workspace/context/webdav/files/`；这些资源不进入 `search_context` 文本索引，当前也不通过 `write_context` 写入。
- `browser_extract(url, include_links, max_chars)` 使用 Playwright headless browser 打开公开 `http/https` 页面，提取渲染后的文本和链接；对 `raw.githubusercontent.com`、`gist.githubusercontent.com` 和常见源码/文本扩展名 URL，会先用 HTTP 客户端按文本资源直接读取，避免纯文本文件因 Chromium SSL/导航问题失败，只有文本直取失败时才回退到浏览器导航。授权该浏览器能力时还会注入 `browser_search(query, top_k)`，它固定用同一个 Playwright 浏览器打开 Bing 搜索页并提取公开结果 URL、标题和片段，不新增 `web_search` provider、搜索引擎配置或 Agent 可选 `engine` 参数。浏览器工具的导航超时、页面提取失败、DNS/私网拦截、profile 占用等下游异常不再向上抛出导致整个 run failed，而是返回 `ok=false` 的 JSON 观察结果，交给 DeepAgent 改用其它搜索词、其它来源或向用户解释限制；真正的 RunService/落盘/配置加载等平台级异常仍会让 run failed。后端封装会拒绝 URL 主机本身为 localhost、私有网段、内网地址或非 `http/https` URL；未配置浏览器代理时，本机 DNS 若把公开 hostname 解析到私网/内网地址也会拦截，但公开 hostname 被 DNS 污染成 `0.0.0.0` 不作为私网拦截处理，而是交给文本直取或浏览器实际导航返回结果/错误；配置 `browser.proxy` 或进程代理环境变量时，不做本机 DNS 私网预解析，由浏览器代理负责解析。若 `browser.allow_private_hosts` 显式列出目标 hostname，或用 `.wulang.vip` 这类后缀匹配目标 hostname，则允许该 host 解析到内网/私有 IP 后继续访问。浏览器启动优先使用 `browser.proxy`，未配置时回退到进程环境变量 `HTTPS_PROXY`、`HTTP_PROXY` 或 `ALL_PROXY`，导航超时由 `browser.timeout_ms` 控制，默认 60000ms。带 `tool_context` 的 Agent run 会自动复用 `workspace/browser_profiles/{agent_id}/` 的 Playwright persistent profile，并用 `profile.lock.json` 避免授权会话和后台抓取并发占用；同一个 Agent 的后台 `browser_extract`/`browser_search` 会先等待 profile lock，按任务串行排队，最多等待 `browser.timeout_ms`，不同 Agent 仍使用各自 profile 并行。profile lock 记录持有进程 pid，pid 不存在时会立即清理；旧版无 pid lock 才继续使用 1 小时兜底清理。授权、搜索和抓取使用同一组桌面 Chrome UA、中文语言、上海时区和基础自动化隐藏参数。工具参数仍只有网页读取所需的 `url/include_links/max_chars` 和搜索所需的 `query/top_k`，Agent 不能传 profile ID、路径或搜索引擎。没有 tool context 时保持一次性无状态浏览器。
- `schedule(action, ...)` 是单一调度管理工具，支持 `create/list/get/update/delete`。创建时只能使用当前 Agent、当前长期 session 和当前渠道投递上下文，触发器支持 `once`、`interval` 和 `cron`；`list/get/update/delete` 只能作用于 `metadata.created_by.type="agent_tool"` 且 `agent_id/session_id` 与当前 run 一致的任务，避免 Agent 删除页面或其它会话创建的定时任务。微信来源任务执行完成后，ScheduleService 会读取 run 的 `result.json` 并调用微信通道回发结果，同时更新 run 的 `delivery.json`。如果 schedule metadata 或 Agent `deepagent.middleware` 启用了 `rhythmic_delivery`，运行时会提示 Agent 用 `<delivery-item>...</delivery-item>` 包裹每条可独立投递消息；ScheduleService 不直接发送整段结果，而是拆分为 `workspace/deliveries/{delivery_id}/` 队列，按 `interval_seconds` 逐条投递微信，使原始 Agent run 可以立即进入 completed，不需要为了节奏推送长时间占用运行。
- DeepAgent 内置 `ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep` 等工具由 `deepagents` 默认 middleware 提供；`deepagent.todo_list` 默认开启，`write_todos` 由运行时接入 LangChain `TodoListMiddleware`，只有 Agent 显式配置 `todo_list=false` 时关闭。当前不启用 DeepAgent `LocalShellBackend`，因此不向 Agent 暴露非沙箱 shell `execute`。
- DeepAgent 原生 filesystem 使用 `FilesystemBackend(root_dir=workspace/agents/{agent_id}, virtual_mode=True)`。Agent 看到的 `/` 就是自己的私有目录，可读写 `workspace/agents/{agent_id}/` 下的 `scratch/`、`notes/`、`artifacts/`、`skills/`、`memories/`、`improvements/` 等内容；不能访问 `workspace/config.yaml`、`workspace/context`、`workspace/runs`、`workspace/sessions`、其它 Agent 目录或项目源码。旧的 run 前加载 `files` state、run 后同步回磁盘机制已停用。
- 每个 Agent 的私有 skill 固定放在 `workspace/agents/{agent_id}/skills/{skill_id}/SKILL.md`，运行时传给 DeepAgent 的 `skills` 参数固定为 `["/skills/"]`。DeepAgent 会扫描该目录下包含 `SKILL.md` 的子目录并用 progressive disclosure 暴露 metadata；不再维护产品级 Skill index，也不需要在 `config.yaml` 里配置 Skill 列表。
- DeepAgent 运行时默认注入 `SkillImprovementMiddleware`，用同步 middleware 方式把技能维护规则追加到模型请求；该 middleware 不负责 memory，长期记忆仍由 DeepAgent 原生 `MemoryMiddleware` 维护 `/memories/AGENTS.md`。`SkillImprovementMiddleware` 只管 Agent 自己的 `/skills/` 和 `/improvements/`：Agent 可以自动创建或更新 `/skills/{skill_id}/SKILL.md` 来沉淀可复用能力，并在 `/improvements/reflections/{run_id}.md`、`/improvements/reviews/{run_id}.md` 或 `/improvements/changes/{timestamp}_{change_id}.json` 记录原因、来源和变更摘要。`/improvements/` 是审计材料，不是 active skill；只有 `/skills/{skill_id}/SKILL.md` 会在下一次 Agent 执行开始时作为 skill metadata 被扫描。
- `agents.definitions[].deepagent.use_longterm_memory` 默认开启。开启后运行时会确保 `workspace/agents/{agent_id}/memories/AGENTS.md` 存在，并通过 DeepAgent 原生 `memory=["/memories/AGENTS.md"]` 启用 `MemoryMiddleware` 加载和维护这一个长期记忆索引文件；其它 `/memories/...` 细节文件不自动注入，Agent 需要时可用内置文件工具自行查找和读取。用户确认后的全局长期知识仍必须通过 `search_context`/`write_context` 写入 `workspace/context/knowledge/files/`。
- 运行时会在 Agent system prompt 中注入平台记忆边界：Agent 特定记忆按 DeepAgent `MemoryMiddleware` 注入的 memory guidelines 更新 `/memories/AGENTS.md`；只有用户明确要求保存到知识库、文档或共享资料时才调用 `write_context`。用户询问笔记、最近笔记、同步文档、WebDAV 文件、知识库内容或 notebook 条目时，必须先调用 `search_context`；`/memories/...` 只代表 Agent 自己的长期记忆，不代表用户的同步笔记。
- 历史 `workspace/agents/{agent_id}/memory/store.json` 是旧版 DeepAgent store 遗留路径，不由运行时代码或迁移脚本自动处理。按用户偏好，旧 workspace 数据收敛直接在目标机器上做一次性文件操作；配置页只展示新版 `workspace/agents/{agent_id}/memories/`。
- 系统日志继续写入 `workspace/logs/platform-YYYY-MM-DD.log`。
- 维护清理服务读取 `maintenance.enabled`、`maintenance.interval_seconds`、`maintenance.retention_days` 和 `maintenance.dry_run`；默认每 86400 秒运行一次，统一清理超过 15 天的可清理运行数据，但不会删除 `workspace/sessions/active.json` 仍指向的当前会话。删除过期 session 时，同步删除 `workspace/sessions/checkpoints.sqlite` 里该 `thread_id` 的 checkpoint/writes 行。自动执行由统一 Scheduler 的内置 `maintenance_cleanup` 任务负责，状态和事件落在 `workspace/schedules/maintenance_cleanup/`，立即清理可使用系统 API 或 `/schedules` 的立即运行按钮。
- 生产更新锁文件固定写入 `workspace/logs/update-service.lock`。历史 `workspace/.run/` 已退役，不再保存微信登录态或更新锁；生产升级前必须把旧 `workspace/.run/wechat_session*.json` 移到 `workspace/channels/wechat/sessions/`，再删除空 `.run` 目录。

## Removed From Target Architecture

以下旧功能不再作为目标架构保留；删除对应代码时必须同步清理文档、测试、前端入口和配置模板：

- 旧 Agent Chat WebSocket 聊天。
- 旧 Harness 严格状态机产品路径。
- Prompt/Agent 双模式产品概念。
- 旧 Session CRUD 产品页和旧 WebSocket 聊天会话模型。
- 旧 Skill 管理和 Skill 作为产品级概念。
- Portfolio 投资组合模块。
- Critique 多维批判模块。
- Proxy 嵌入站点模块。
- Agent command center 里围绕旧 Agent/Skill/Model 管理构建的复杂 UI。

## Documentation Cleanup Rule

- 主架构入口只描述当前目标架构，不再保存已决定删除模块的完整行为说明。
- `docs/architecture-qa.md` 保存本轮架构问答和设计决策。
- 旧 `docs/superpowers/*` 计划/规格和旧多维批判设计图已经删除；后续删除旧代码模块时，必须同步删除或归档对应文档，避免搜索结果继续指向旧架构。
- 如果实现改变架构、行为、命令、依赖、配置、公共接口、运维方式或长期协作规则，必须同步更新本文件和相关说明。

## Operating Notes

- `AGENTS.md` 是仓库级 Codex 指令入口。
- `config.example.yaml` 是 workspace 配置模板，不得放入真实密钥。
- 使用平台工具和运行时能力需要安装对应 Python 依赖：会话 checkpoint 依赖 `langgraph-checkpoint-sqlite`；`search_session` 中文关键词分词依赖 `jieba`；`browser_extract`/`browser_search` 依赖 `langchain-community`、`playwright`、`beautifulsoup4` 和 `lxml`；`arxiv` 依赖 `langchain-community` 和 `arxiv`；`yahoo_finance_news` 依赖 `langchain-community` 和 `yfinance`。`run.sh dev/prod` 会在依赖安装后检查并执行 `python -m playwright install chromium` 准备浏览器二进制。
- 当前生产 systemd unit 直接运行 `.venv/bin/python -m server`，单独 `systemctl restart` 不会安装新依赖；提交后远端部署必须在 pull 和 HEAD 校验之后执行 `.venv/bin/python -m pip install .`，并确保 Playwright Chromium 已安装，再重启服务。
- `config.yaml` 属于本地 workspace 数据，不提交。
- 开发启动使用 `./run-dev.sh` 或 `./run.sh dev`。
- 生产启动使用 `./run-prod.sh` 或 `./run.sh prod`。
- `./run.sh setup-sudo` 仍用于安装受限 sudoers 规则，使生产服务能无密码执行受限的 `systemctl restart/status/is-active super-personal-platform.service`。
- `run.sh prod` 生成 systemd unit 时只使用系统临时文件并安装到 systemd 路径，不再把临时 service 文件写入 workspace。
- 提交项目前必须执行 `.codex/skills/project-commit` 工作流。
