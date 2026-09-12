# Project Architecture

本文件是当前项目架构入口。DeepAgent 重构的详细决策记录在 `docs/architecture-qa.md`，后续实现以该问答中的模型为准。

## Current Direction

- 项目仍是一个由 FastAPI 提供后端、React + Vite 提供前端、后端统一服务静态资源的单体应用。
- 后端 Python 版本目标为 3.12.x，默认监听端口为 `8888`。
- 运行入口仍是 `./run-dev.sh`、`./run-prod.sh` 和底层 `./run.sh`。
- 生产环境仍使用已提交的 `web/dist`，生产启动不现场构建前端。
- 工作区默认仍是项目目录下 `.super-personal-platform`，可通过 `--workspace` 覆盖。
- 目标产品边界收缩为：DeepAgent/LangGraph 后端任务执行、Web 单聊与多 Agent 群聊、微信个人号入口、坚果云 WebDAV Context、单 token 登录、基础配置、基础日志和生产更新。

## Architecture Q&A

`docs/architecture-qa.md` 是本轮重构的产品和技术问答来源，已确定：

- 前端 Chat 页面不直接执行 Agent，而是通过后端创建 `source=web_chat` 的 run；Runs 页面只查看落盘任务、状态、事件和结果，不提供手动创建 Run 入口。
- DeepAgent 在后端由进程内持久化 Run Executor 运行，状态、事件、租约、心跳、重试和结果全部落盘；Checkpoint 只保存 LangGraph/Agent 状态，不替代任务队列或 worker lease。
- DeepAgent 运行时优先使用 LangGraph/DeepAgent `astream(..., subgraphs=True)` 读取主图与 sub-agent 子图的模型增量和图更新；运行时先把 LangGraph stream chunk 转换成明确的 `DeepAgentStreamEvent` / `RunEventPayload` 对象，再追加到 `workspace/runs/{run_id}/events.jsonl`。主 Agent 增量正文以 `assistant_delta` 事件作为唯一实时入口，并节流写入 `workspace/runs/{run_id}/partial.json` 快照；sub-agent 的非空模型响应单独写为强类型 `subagent_response` 事件，记录 `namespace`、Agent 名、节点、正文和消息类，不混入主回答。`running`、`agent_update`、`subagent_response`、`stream_fallback`、`image_attachments_textified` 等公开运行事件会同步聚合成 `partial.thinking[]`，作为 Chat 页面刷新后的 run 级思考过程恢复来源。若当前 deepagents 版本或 fake agent 不支持 stream，则自动回退到 `ainvoke`，不影响最终结果。
- `workspace/runs/index.json` 维护所有 run 的摘要和当前状态。
- 工具人工确认沿用 DeepAgent/LangGraph 原生 HITL：`interrupt_on` 只开放 approve/reject，运行时把 `Interrupt.value` 转成 `RunApprovalRequest` 强类型对象；`approval_required` 和 `approval_resolved` 使用明确的事件 payload 类，不在运行链路传递无定义 dict。当前请求、决定和历次审批保存在 `workspace/runs/{run_id}/approval.json`，LangGraph 的实际恢复位置仍由 SQLite checkpoint 保存。
- 每个 run 使用 `workspace/runs/{run_id}/` 独立目录保存 `input.json`、`state.json`、`events.jsonl`、`result.json` 和 `delivery.json`；需要人工审批时额外保存 `approval.json`，记录强类型工具请求、当前决定和历史。run 由同一 FastAPI 进程内常驻 `RunWorkerService` 从落盘队列领取执行，而不是由 HTTP 请求内的临时 `asyncio.create_task` 执行；仅在 run 被 worker 领取后额外持有 `lock.json`，进入终态或 `waiting_approval` 后立即移除。`RunDeliveryService` 独立扫描 Run 索引，把审批通知和最终结果投递从微信接收协程、Scheduler 执行协程中解耦。
- 统一调度器使用 `workspace/schedules/` 落盘调度定义和状态；WebDAV Context 同步和未来 Agent 定时任务共用这一套调度机制。
- `workspace/sessions/index.json` 维护所有长期会话索引；长期 session 对微信和未来渠道默认开启。`workspace/sessions/active.json` 维护渠道身份到当前活跃会话的绑定；微信、API 和未来渠道共享 `workspace/sessions/{session_id}/`，每个 run 只引用 `session_id`；Agent 的 Checkpointer 固定开启，不再提供配置开关，开启时 DeepAgent/LangGraph 运行时状态写入 `workspace/sessions/checkpoints.sqlite`。
- 同一 `session_id` 同时只允许一个 `queued`、`running` 或 `waiting_approval` Run。RunService 在保存附件、追加消息或创建 Run 目录前强制检查，冲突通过 HTTP 409 返回当前 Run ID 和状态，防止多个 Run 覆盖同一个 LangGraph checkpoint。
- `Agent` 保存人格、模型、可选 Context 绑定和 DeepAgent 运行选项。
- Agent 的 DeepAgent 配置只保留 `max_iterations`、`todo_list` 和工具授权 `tools`。长期记忆、私有文件系统及会话 Checkpointer 固定开启；运行名使用 Agent 名称，debug 默认关闭。系统从工具注册表的 `approval_required` 生成 HITL `interrupt_on`；WebDAV 文件写入由原生 Permission interrupt 规则生成审批谓词，不再接受 Agent 自定义审批列表。原生文件工具按路径授权执行。移除 `name/debug/filesystem/use_longterm_memory/interrupt_on/subagents/response_format/context_schema/checkpointer/cache` 配置；系统继续管理通用子 Agent、Skills 和 SkillImprovement middleware。
- 平台工具定义在代码中，不放入 workspace 散落配置；Agent 的 `deepagent.tools` 只是授权选择。当前平台工具为 `send_attachment`、`search_session`、`arxiv`、`yahoo_finance_news`、`browser_extract`、`schedule` 和 `execute_code`。授权 `browser_extract` 时运行时会同时注入隐藏的 `browser_search` 工具；搜索引擎固定为 Bing，不提供 workspace 配置或 Agent 入参选择。
- 当前默认 Context 收敛为唯一的 `workspace/context/`；知识文件放在 `workspace/context/knowledge/files/`，作为工具读写的目录。
- Run 创建时必须固化 Agent + Context + Knowledge 快照。
- 微信收到消息后按 `wechat + account + peer + agent` 生成稳定 active key，通过 `workspace/sessions/active.json` 找到当前 `session_id`，再创建 `source=wechat` 的 run；用户在微信发送“清空上下文 / 清空会话 / 开启新会话 / 新会话 / /clear / /new”或 `/session new` 时，通道层会归档旧 session 并为同一渠道身份切换到新的 active session。微信通道还内置 `/session help`、`/session status`、`/session list` 和 `/session change <编号或 session_id>`；这些指令由通道层直接消费，不创建 DeepAgent run。`/session change` 只能切换同一个 `wechat + account + peer + agent` 身份下的历史 session，不能跨用户、跨群、跨微信账号或跨 Agent。带 `session_id` 的 DeepAgent run 使用同一个 SQLite checkpointer 恢复 LangGraph 状态，只把当前 run 消息作为本次输入。`messages.jsonl` 继续保存渠道历史、审计和 `search_session` 检索数据，完成后由平台投递微信回复。

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
      workspace/
        artifacts/
          exec_{id}/
        scratch/
          exec_{id}.py
          exec_{id}.sh
        skills/
          {skill_id}/
            SKILL.md
        memories/
          AGENTS.md
        improvements/
          reflections/
          reviews/
          changes/
        meditations/
          {timestamp}_{run_id}.json
        browser/
          profile.lock.json

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

  schedules/
    index.json
    {schedule_id}/
      definition.json
      state.json
      events.jsonl
      lock.json

  chat_groups/
    index.json
    {group_id}/
      state.json

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
POST /api/runs/{run_id}/cancel
POST /api/runs/{run_id}/rerun
POST /api/runs/{run_id}/approve
POST /api/runs/{run_id}/reject
POST /api/runs/{run_id}/resume
```

`POST /api/runs` 可接受可选 `session_id` 和 `attachments[]`，只负责创建 `queued` run 并写入落盘队列；执行由同一 FastAPI 进程内的 `RunWorkerService` 领取，不再绑定创建请求的生命周期。同一 session 已有活动 Run 时返回 409，响应 detail 包含 `message`、`active_run_id` 和 `status`。未传 `session_id` 时按独立一次性 run 处理；若当前 Agent 授权了系统标记需审批的工具，平台仍会用 run ID 建立可恢复 checkpoint。传入 `session_id` 时，运行时使用 `workspace/sessions/checkpoints.sqlite` 作为 LangGraph SQLite checkpointer，并把 `configurable.thread_id` 设为该 `session_id`，同时只传当前 run 消息。DeepAgent 原生 interrupt 会把 Run 切到 `waiting_approval` 并释放 worker lock；批准或拒绝会写入 `approval_resolved` 事件、重新入队，再通过同一 thread ID 的 `Command(resume=...)` 从中断点继续。三个审批接口中，`approve` / `reject` 是快捷入口，`resume` 接受结构化 `decision=approve|reject` 和可选拒绝理由。微信来源 Run 和带微信投递目标的定时 Run 会由 `RunDeliveryService` 发出审批通知；通知只提示回复 `approve` 或 `reject`，短命令选择同一账号、peer、Agent 下最新的待审批 Run，旧 `/approve <run_id>` 与 `/reject <run_id> <原因>` 继续兼容。该接口保留给渠道接入、自动化和后端集成使用。`cancel` 会把 queued/running/waiting_approval run 标记为 `cancelled` 并释放 lock；`rerun` 只允许作用于 `completed/failed/cancelled` 终态 run，保留原 `input.json` 和事件审计，清除结果、审批快照并重新入队。

Chat API：

```text
POST /api/chat/session
POST /api/chat/session/new
GET /api/chat/sessions?agent_id={agent_id}
POST /api/chat/session/change
GET /api/chat/sessions/{session_id}/messages?agent_id={agent_id}
POST /api/chat/messages
```

页面 Chat 使用 `channel=web`、`channel_account_id=default`、`peer_type=private`、`peer_id=browser` 和当前 `agent_id` 在 `workspace/sessions/active.json` 中维护页面当前选中的长期会话。`GET /api/chat/sessions` 列出当前 Agent 名下的全部长期 session，包括微信、Web 和未来渠道；`POST /api/chat/session/change` 可以把 Web Chat 绑定到其中任意一个 session，但只更新 Web Chat 的 active binding，不改写 session 原始的渠道、账号和 peer 身份，也不改变微信侧 active binding。读取消息、切换和发送消息都校验 session 必须属于当前 Agent。`POST /api/chat/session` 和 `POST /api/chat/session/change` 会在当前 session 的 `last_run_id` 仍处于 `queued/running/waiting_approval` 时额外返回 `active_run`，让页面刷新或切换回来后可以显示 `partial.json` 并重新轮询事件。`POST /api/chat/messages` 创建 `source=web_chat` 的普通 DeepAgent run 并唤醒 `RunWorkerService`；请求可携带 `client_message_id`，同一 session、Agent 和客户端消息 ID 的重复请求返回已有 run，不重复写消息或执行 Agent。前端随后只轮询 `/api/runs/{run_id}/events?after={seq}`，按 `assistant_delta` 事件增量更新 assistant 气泡，完成、失败或取消后再读取 run 详情和 session messages 对齐最终历史。

Chat 的 `active_run` 识别 `queued`、`running` 和 `waiting_approval`。页面刷新后会从 Run 的 `approval.json` 恢复审批卡片；批准或拒绝调用统一 `resume` API，Run 恢复执行前输入框保持锁定，避免向同一 session 并发追加新消息。

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

后台统一 Scheduler 每 5 秒扫描轻量调度索引，只判断 `next_run_at` 是否到期，不执行高频 WebDAV 同步。到期任务由独立 asyncio task 执行，一个等待审批的 Agent 定时任务不会阻塞其它 schedule 扫描。到期后按 `definition.type` 分发：`webdav_sync` 执行 WebDAV 文件同步并写调度事件；`maintenance_cleanup` 执行 15 天保留期清理并写调度事件；`agent_meditation` 每 86400 秒执行一次每日冥想，每个配置中的用户 Agent 都有独立的内置 `agent_meditation_{agent_id}` 任务、状态、事件和手动运行入口，在该 Agent 没有 queued/running run 或浏览器 profile lock 时，为该 Agent 创建 `source=system`、`metadata.kind=meditation` 的普通 DeepAgent run；`agent_run` 每次触发只创建一个普通 `workspace/runs/{run_id}/`，唤醒 `RunWorkerService`，等待该 run 终态及投递终态后处理调度完成。Schedule `lock.json` 用于避免重复执行，并记录 `pid`、`created_at` 和 `heartbeat_at`；执行期间每 15 秒刷新 heartbeat。服务重启后，如果 schedule 原先正在等待 `queued/running/waiting_approval/completed` Run，会保留并复用 `current_run_id`，不会为同一次触发重新创建 Run；不存在可恢复 Run 的陈旧 schedule 才按失败重试策略处理。调度执行失败后会进入 `retrying` 状态，默认 1 分钟后重试，最多 3 次；重试耗尽后才标记 `failed` 并进入下一次正式触发周期，成功后重试计数清零。普通 Run 固定最多执行 30 分钟，执行期间每 15 秒刷新 run `lock.json.heartbeat_at`；超过上限、执行 task 被取消，或读 `list/get/events` 时发现 active run 已超过上限/心跳超过 120 秒未刷新，会按 `attempts/max_attempts` 重新入队或最终标记为 `failed` 并释放 `lock.json`。服务启动时会把上一进程遗留的 `running` Run 按同一重试策略恢复，保留 `queued` 和 `waiting_approval` Run；普通 Run 后台任务运行在 FastAPI 进程内，项目不引入 Redis/Celery，也不拆独立 systemd worker service。

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

这些接口只允许访问 active workspace 内部路径，用于前端“工作目录”页面浏览、编辑 UTF-8 文本文件和删除非固定路径。`config.yaml` 通过写入入口保存时仍执行配置校验；`config.yaml` 和根层固定目录 `agents/`、`context/`、`runs/`、`schedules/`、`sessions/`、`channels/`、`logs/` 不能删除，其它 workspace 内文件或目录允许删除。

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

`/webdav-context/test` 可使用配置页当前草稿或已保存配置测试坚果云 WebDAV 连接，只返回目标 URL、HTTP 状态和是否成功，不回传账号密码；`/webdav-context/sync` 现读已保存的 `workspace/config.yaml`，手动执行一次 WebDAV 文件同步，用于配置变更后立即制作本地缓存，而不必等待后台间隔或重启服务。

`/maintenance/preview` 只计算清理计划不删除文件；`/maintenance/run` 执行清理。当前默认保留期统一为 15 天：删除超过保留期且已终态的 run、超过保留期未活跃且没有活动 run 引用、也没有被 `workspace/sessions/active.json` 指向的 session、这些已删 session 在 `workspace/sessions/checkpoints.sqlite` 里的 checkpoint/writes 行、旧调度事件、旧平台日志、Agent scratch 和 Context cache 里的旧文件，以及很旧的孤立 lock；同时会清理 `active.json` 中指向已不存在 session 的脏 binding。知识库、WebDAV 文本/图片缓存、微信登录态和 Agent 长期记忆不做自动删除。自动清理不再使用单独后台 loop，而是作为 `workspace/schedules/maintenance_cleanup/` 内置定时任务落盘并显示在 `/schedules`。

浏览器授权 API 用于后台管理员操作服务器上的 Playwright persistent browser profile。Profile 固定按 Agent 隔离在 `workspace/agents/{agent_id}/workspace/browser/`，不再按微信账号或单独 service 目录拆分；授权会话启动后前端通过截图、点击、键盘输入和跳转 API 操作同一个 headless browser context，完成或取消时关闭浏览器并释放 `profile.lock.json`。Agent 不能直接调用这些授权 API，也不能选择 profile 路径。

Code Execution 配置：

```yaml
code_execution:
  enabled: true
  runtime: docker_gvisor
  languages: ["python", "shell"]
  timeout_seconds: 20
  docker:
    runtime: runsc
    image: python:3.12-slim-bookworm
    network: none
```

## Frontend Routes

- `/chat-groups` 是多 Agent 群聊工作区，独立群列表、共享聊天区和成员面板；与单聊共用消息、Markdown、思考过程、审批和输入组件。
- `/chat` 是页面 Chat 工作区，提供 Agent 选择、该 Agent 全部长期 session 切换、新会话、文本输入和 assistant 流式气泡；session 列表展示微信/Web 等来源、渠道身份、消息数和更新时间，不展示其它 Agent 的 session。页面可以打开并续聊微信 session，但只改变 Web Chat 当前选择，不切换微信通道本身的活跃会话。消息进入长期 session，执行仍由后端 DeepAgent run 完成。Chat 输入框使用普通 `Enter` 发送、`Shift+Enter` 换行；中文/日文等输入法正在 composition 组词时不拦截 `Enter`，避免拼音选词直接发送。发送动作有同步 in-flight 锁，并由后端按 `client_message_id` 幂等创建 run，避免连续按键、双击或重复请求生成两条相同消息。用户和 assistant 消息都提供复制按钮，桌面端按钮位于气泡外侧操作位，移动端使用顶部操作位，不再通过贯穿全文的右内边距压缩正文；复制原始消息文本并短暂显示成功状态。Chat 的 assistant 气泡内置轻量 Markdown 渲染，支持标题、列表、引用、代码、链接、加粗、水平分隔线和 GitHub 风格表格；宽表格只在表格容器内横向滚动，不撑开聊天布局。Chat 气泡运行中会把后端 `running`、`agent_update`、`stream_fallback`、`image_attachments_textified` 等可公开运行事件聚合到“思考过程”区域并展开显示，`assistant_delta` 只作为正文增量；run 结束后正文保留为主内容，“思考过程”自动折叠并可手动展开查看。页面刷新或切换 session 后，Chat 先读 `workspace/sessions/{session_id}/messages.jsonl` 展示正文，再按 assistant 消息的 `run_id` 读取 `workspace/runs/{run_id}/partial.json` 恢复已折叠的思考过程；如果后端返回 `active_run`，页面会先显示该 run 的 `partial.json` 正文和思考过程，再从 `events.jsonl` 重新接上事件轮询，直到 run 完成或失败。`820px` 以下使用独立移动布局：全局侧栏收进顶部菜单控制的抽屉，Chat 占满剩余动态视口，会话诊断栏隐藏，Agent、session 和新会话操作保持在紧凑工具行，消息区独立滚动且输入框固定在工作区底部。
- `/`, `/runs`, `/agents` 都进入新的 Runs 工作区；`/agents` 只是旧入口跳转，不恢复旧 Agent 管理页面。Runs 工作区只承担运行记录查看、状态轮询、事件与结果展示，不提供 Prompt/Agent ID 表单或手动创建按钮；详情页支持取消 `queued/running/waiting_approval` run、审批或拒绝待确认工具调用，以及重跑 `completed/failed/cancelled` run。
- `/workspace` 展示真实 workspace 文件浏览器，可查看和编辑 UTF-8 文本文件，并可删除非固定路径；`config.yaml` 在这里按原生 YAML 文本展示和编辑，不承载专用配置表单；`config.yaml` 和根层固定骨架目录不可删除。
- 侧栏只保留一个 `/config` 配置主菜单，右侧用栏目切换基础配置、Providers 和 Agents；保存仍写回 `workspace/config.yaml` 并经后端配置校验。
- `/config` 基础配置栏目只承载访问 Token、服务监听和坚果云 WebDAV 等基础配置；访问 Token 按明文输入展示。
- `/config` 基础配置栏目还承载 `browser.proxy`、`browser.timeout_ms` 和 `browser.allow_private_hosts`，用于 `browser_extract` 的 Playwright headless browser。微信账号代理只服务微信 iLink 连接，不自动复用为浏览器代理；`allow_private_hosts` 只允许管理员显式信任的 hostname 或 `.domain` 后缀在解析到内网/私有 IP 时继续访问。
- `/config` 基础配置栏目还维护 `maintenance` 清理配置，默认启用、保留 15 天、每天运行一次；也可设置为 dry run 只预览不删除。
- `/config` 的 Agent 栏目提供 WebDAV 开关、映射目录、read/write 权限与目录说明；原运行时参数区仅保留最大执行步数。全局页面移除 WebDAV 权限规则。
- `/config` 的 WebDAV 文件同步区域提供“测试连接”和“立即同步”操作；测试连接使用当前表单草稿测试 WebDAV，不保存配置且不回传 secret；立即同步调用后端手动同步接口读取已保存的 `workspace/config.yaml`，立刻把坚果云远端文件缓存到 `workspace/context/webdav/` 并返回文本/图片资源数量；保存配置本身仍只负责校验并写回 `config.yaml`。
- `/providers` 是配置页内的模型 Provider 栏目直达入口，维护 `llm.default_model_id` 和 `llm.models[]`，包括 provider 类型、base URL、API key、模型名、temperature 和图片能力；Provider 至少保留一个，删除被引用的 Provider 时前端会把默认模型和 Agent 引用迁移到剩余模型。
- `/agent-config` 是配置页内的 Agent 栏目直达入口，默认显示 Agent 列表摘要（名称、ID、模型、工具数量和 WebDAV 状态）。点击列表项或新增打开一个原生模态弹窗，使用独立草稿编辑，取消/关闭/Escape 丢弃本次编辑；保存直接调用配置写入 API，成功后关闭并刷新列表，失败保留内容并在弹窗显示错误。工具选择内嵌同一弹窗，不再嵌套工具弹窗；字段编辑不以可变 ID 为 React key。列表删除仍进入页面草稿，由页面保存按钮落盘。该页面维护 `agents.definitions[]`，包括人格提示词、模型选择、Context 绑定和 DeepAgent 运行选项；Agent 工具通过弹窗里的可视化卡片选择，当前写入 `agents.definitions[].deepagent.tools`，平台工具包括 会话历史检索工具 `search_session`、学术检索工具 `arxiv`、轻量财经新闻工具 `yahoo_finance_news`、浏览器能力 `browser_extract`、用于对话式创建定时任务的 `schedule` 和 Docker/gVisor 沙箱代码执行器 `execute_code`；授权 `browser_extract` 会同时提供固定 Bing 的 `browser_search`，前端不单独展示搜索引擎或搜索工具选择；不再展示可手填的 `Tool IDs` 输入框；`/agents` 仍跳转 Runs，不作为配置页路径。
- `/schedules` 是定时任务管理页面，读取 `workspace/schedules/index.json` 和每个任务详情，支持查看内置 WebDAV 同步任务和维护清理任务、创建/编辑/删除 Agent 定时任务、启用/停用、立即运行和查看调度事件；任务创建表单只暴露 `prompt + agent + trigger` 等必要字段，不在前端执行 Agent。
- `/browser` 是浏览器授权页，读取 `config.yaml` 中的 Agent 列表，允许管理员按 Agent 启动一个截图式 Playwright 授权会话，profile 路径固定为 `workspace/agents/{agent_id}/workspace/browser/`；授权页提供 Agent/profile 列表、目标 URL、截图点击、文本输入、按键、完成和取消操作，不放入 `/config` 或 `/system`。
- `/wechat` 展示微信账号列表、当前账号详情、二维码、运行态、绑定 Agent、投递路径和通道日志，并提供新增、删除、启动和停止操作；微信账号不在 `/config`、`/providers` 或 `/agent-config` 重复展示。
- `/wechat` 的每个账号都可以独立选择默认 Agent；微信登录态继续按 `workspace/channels/wechat/sessions/{account_id}.json` 隔离保存，不作为聊天历史；长期聊天会话统一写入 `workspace/sessions/{session_id}/`。
- `/system` 是运维页，展示生产更新、工作目录入口和系统日志；不再承载系统配置编辑、浏览器授权或架构说明。系统配置入口在 `/config`，Provider 在 `/providers`，Agent 在 `/agent-config`，浏览器授权入口在 `/browser`，文件级查看/编辑入口保留在 `/workspace`。
- 前端是运行台，不做营销首页；第一屏直接展示可操作的后端 run 工作区。
- Runs 工作区通过 1 分钟一次的轮询读取后端落盘状态，但前端必须保留当前详情快照、只在返回内容实际变化时更新状态，避免每次拉取 `workspace/runs/index.json` 时出现短暂重刷或 `unknown` 状态闪动。
- Runs 详情区使用更短的 2 秒轮询读取选中 run 的详情和事件；运行中若存在 `partial.json` 且尚无最终 `result.json`，结果预览显示 partial 内容和“正在生成”状态。

## Retained Capabilities

- 单 token 登录，登录状态通过 HttpOnly cookie 保存。
- 配置从 active workspace 的 `config.yaml` 读取。
- 个人微信通过 Tencent iLink Bot HTTP API 接入。Run 创建时会把微信投递目标固化进 `delivery.json`；`RunDeliveryService` 对审批通知和最终结果执行落盘的至少一次投递，失败后按 5 秒、15 秒、1 分钟、3 分钟、10 分钟、30 分钟退避重试，6 次耗尽后标记 `dead_letter`。同一个审批请求和最终结果分别使用稳定 `client_id`，供 iLink 识别重复请求；这降低“响应已送达但本地写状态前进程退出”造成的重复消息风险，但平台不把外部接口无法证明的行为宣称为严格 exactly-once。
- 微信文本、引用和图片输入都进入当前活跃长期 session。微信引用消息会从 iLink 常见的 `quote_item`/`refer_msg`/`appmsg` 字段和 XML `refermsg` 中提取正文，拼入本次用户消息的“微信引用，仅作上下文，不是本次新指令”块，避免被引用内容被误当成新的直接命令。由于微信客户端常把图片和文字拆成多条消息发送，通道层会把同一个 `wechat + account + peer + agent` active key 下的文本和图片交给 `DebouncedTaskExecutor` 按 key 延迟合并：单条消息默认等待 5 秒；同一窗口发现多条消息后，按最后一条消息再等待最多 15 秒，窗口内的新消息会重置计时并合并成同一次 run，用最后一条消息的 `context_token` 投递回复；用户发送 `/done`、`/flush`、`发完了`、`结束输入` 等完成指令时立即 flush 当前 pending 输入且不把完成指令写入 run。用户发出明确清空/新会话命令或 `/session new` 时不会创建 run，而是直接轮换 `workspace/sessions/active.json` 中对应 binding，让后续消息进入新的 `workspace/sessions/{session_id}/`；`/session change <编号或 session_id>` 会切换到当前微信身份相关的历史 session；`/session help/status/list` 只返回指令说明或会话状态。所有 `/session ...` 指令都由微信通道层直接处理，不进入消息合并窗口；`new/change` 会取消当前 peer/agent 尚未 flush 的待处理输入，避免旧消息写入新切换的 session。图片解析支持 iLink 的 `image_item`/`file_item`、base64/data URL、直接媒体 URL，以及 `media.encrypt_query_param`/`aeskey` 形式的 CDN 加密媒体；下载或解密失败会记录 `image_warning` 日志而不是静默丢失。默认开启 `deepagent.checkpointer` 且带 `session_id` 的 DeepAgent 执行使用 `workspace/sessions/checkpoints.sqlite` 恢复同一 `session_id` 的 LangGraph checkpoint，运行时只传当前 run 消息，避免把 `messages.jsonl` 历史和 checkpoint 状态重复叠加；显式关闭 checkpointer 时仍把最近会话历史作为显式上下文；需要引用更早历史时，Agent 通过 `search_session` 查询 `messages.jsonl`。模型未在 Provider 中启用 `supports_images` 时，后端不会把图片二进制或 `image_url` 传给 DeepAgent，而是把图片附件文件名、MIME、大小和 workspace 路径追加为文本说明后继续调用当前主模型；该降级不读取图片画面内容。
- 坚果云通过 WebDAV 接入，默认 endpoint 为 `https://dav.jianguoyun.com/dav/`。
- DeepAgent 依赖 `deepagents>=0.7.13,<0.8`、LangGraph 和 `langgraph-checkpoint-sqlite`；后端任务执行结果必须落盘，带 `session_id` 且 Agent 未显式关闭 `deepagent.checkpointer` 的任务还会把 LangGraph checkpoint 写入统一 SQLite 文件。生产依赖同时固定 `cryptography>=38,<49`，避免部署时走不兼容本机 Rust 工具链的源码构建路径。
- `search_session(query, top_k, role, scope)` 检索会话历史，`scope` 默认为 `current`，只查当前 run 的 `session_id` 对应 `workspace/sessions/{session_id}/messages.jsonl`；传 `scope="related"` 时，按当前 session 的 `active_key` 或 `channel + channel_account_id + peer_type + peer_id + agent_id` 搜索同一渠道身份下的相关 session，包括清空上下文前归档的旧 session。Agent 不能传任意 `session_id`。搜索使用 jieba 对中文 query 分词，并结合精确子串命中评分；返回 session 元数据、是否当前 active、消息序号、角色、时间、run ID、片段和附件元数据。该工具用于用户引用“刚才/前面/之前/那张图/那个链接”等同一微信或 API 长期会话中的历史消息，也用于用户给关键词要求找相关旧会话。
- `arxiv(query, top_k)` 使用 LangChain Community 的 arXiv wrapper 检索论文，依赖 `arxiv` 包，运行时内置全局 3 秒请求间隔，作为免费学术/知识工具授权给需要的 Agent。
- `yahoo_finance_news(ticker, top_k)` 使用 LangChain Community 的 Yahoo Finance News 工具和 `yfinance` 获取公开股票代码相关新闻，定位为轻量财经新闻上下文，不作为交易级行情或完整市场数据源。
- 平台工具面向外部下游的可恢复异常默认返回结构化 `ok=false` JSON 给 DeepAgent 处理，包括浏览器导航/检索失败、公开数据源超时、调度工具参数校验或权限边界失败；Agent 应基于该观察结果换工具、换来源、询问用户或解释限制。只有平台自身无法继续执行的错误，例如 run 落盘失败、配置加载失败、运行时初始化崩溃，才应向上抛出并标记整个 run failed。
- Context 可配置一个坚果云 WebDAV 同步根目录 `context.webdav_sync.root_path`，远端实际路径按 `nutstore.root_path + context.webdav_sync.root_path` 解析；本地文件缓存固定落在 active workspace 的 `workspace/context/webdav/files/`，索引固定写入 `workspace/context/webdav/index.json`。
- WebDAV 同步触发已纳入统一调度器：启动时后端会根据 `nutstore.enabled`、`context.webdav_sync.enabled`、`context.webdav_sync.interval_seconds` 生成/更新内置 `workspace/schedules/context_webdav_sync/definition.json`；调度轮询间隔固定为 5 秒，但实际同步间隔仍由 `context.webdav_sync.interval_seconds` 控制，且配置校验要求不少于 60 秒。
- 全局 `context.webdav_permissions`、`protected` 和历史 `webdav_roots` 已移除。全局连接与同步不决定 Agent 权限；已有配置在部署前做受控一次性整理，不添加运行时迁移或旧字段回退。
- `agents.definitions[].webdav` 配置包含 `enabled=false` 和 `directories=[]`。每个目录包含相对于全局同步根的 `path`、`permission="write"`（或 `read`）和 `description`；write 包含读取。Agent 列表点击弹窗配置，支持添加/移除多个目录、浏览已同步目录或手工填写未同步路径，每项独立权限和用途说明。空列表不授权任何目录，规范化后重复路径拒绝保存。运行快照保存完整列表。
- `WebDAVPathPolicy` 将目录配置编译为 DeepAgent 原生 `FilesystemPermission`，按路径深度降序让子目录覆盖父目录，支持父只读/子读写及父读写/子只读，用户无需排序。规则以 `/webdav/**` 默认拒绝收尾，不影响私有工作区；路径中的 glob 特殊字符按字面转义。`create_deep_agent(permissions=...)` 传给主 Agent，通用子 Agent 继承同一规则。文件后端复用原生 `_check_fs_permission`（私有 API 接口由回归测试约束），不另造匹配语义。未选择的祖先只允许目录导航，不能读取同层文件、无关兄弟或被替换成文件的祖先。
- `CompositeBackend` 将 `/webdav/` 路由至共享缓存根的 WebDAVFilesystemBackend，默认后端仍为私有 AgentFilesystemBackend；不复制缓存、不使用软链接。目录结构保持不变，例如 `/项目/草稿` 暴露为 `/webdav/项目/草稿/`。旧单目录配置仅在目标机器受控一次性转换成列表，保留原范围、权限、开关和说明，不加入运行时迁移。
- WebDAV 同步会解析 Markdown 里的 `![...](...)` 和 `<img src="...">`，把被引用的 `.png`、`.jpg`、`.jpeg`、`.gif`、`.webp`、`.svg` 按相对目录结构作为二进制资源缓存到 `workspace/context/webdav/files/`；这些资源不进入文本检索索引，目前不支持上传。
- `browser_extract(url, include_links, max_chars)` 使用 Playwright headless browser 打开公开 `http/https` 页面，提取渲染后的文本和链接；对 `raw.githubusercontent.com`、`gist.githubusercontent.com` 和常见源码/文本扩展名 URL，会先用 HTTP 客户端按文本资源直接读取，避免纯文本文件因 Chromium SSL/导航问题失败，只有文本直取失败时才回退到浏览器导航。授权该浏览器能力时还会注入 `browser_search(query, top_k)`，它固定用同一个 Playwright 浏览器打开 Bing 搜索页并提取公开结果 URL、标题和片段，不新增 `web_search` provider、搜索引擎配置或 Agent 可选 `engine` 参数。浏览器工具的导航超时、页面提取失败、DNS/私网拦截、profile 占用等下游异常不再向上抛出导致整个 run failed，而是返回 `ok=false` 的 JSON 观察结果，交给 DeepAgent 改用其它搜索词、其它来源或向用户解释限制；真正的 RunService/落盘/配置加载等平台级异常仍会让 run failed。后端封装会拒绝 URL 主机本身为 localhost、私有网段、内网地址或非 `http/https` URL；未配置浏览器代理时，本机 DNS 若把公开 hostname 解析到私网/内网地址也会拦截，但公开 hostname 被 DNS 污染成 `0.0.0.0` 不作为私网拦截处理，而是交给文本直取或浏览器实际导航返回结果/错误；配置 `browser.proxy` 或进程代理环境变量时，不做本机 DNS 私网预解析，由浏览器代理负责解析。若 `browser.allow_private_hosts` 显式列出目标 hostname，或用 `.wulang.vip` 这类后缀匹配目标 hostname，则允许该 host 解析到内网/私有 IP 后继续访问。浏览器启动优先使用 `browser.proxy`，未配置时回退到进程环境变量 `HTTPS_PROXY`、`HTTP_PROXY` 或 `ALL_PROXY`，导航超时由 `browser.timeout_ms` 控制，默认 60000ms。带 `tool_context` 的 Agent run 会自动复用 `workspace/agents/{agent_id}/workspace/browser/` 的 Playwright persistent profile，并用 `profile.lock.json` 避免授权会话和后台抓取并发占用；同一个 Agent 的后台 `browser_extract`/`browser_search` 会先等待 profile lock，按任务串行排队，最多等待 `browser.timeout_ms`，不同 Agent 仍使用各自 profile 并行。profile lock 记录持有进程 pid，pid 不存在时会立即清理；旧版无 pid lock 才继续使用 1 小时兜底清理。授权、搜索和抓取使用同一组桌面 Chrome UA、中文语言、上海时区和基础自动化隐藏参数。工具参数仍只有网页读取所需的 `url/include_links/max_chars` 和搜索所需的 `query/top_k`，Agent 不能传 profile ID、路径或搜索引擎。没有 tool context 时保持一次性无状态浏览器。
- `schedule(action, ...)` 是单一调度管理工具，支持 `create/list/get/update/delete`。创建时只能使用当前 Agent、当前长期 session 和当前渠道投递上下文，触发器支持 `once`、`interval` 和 `cron`；`list/get/update/delete` 只能作用于 `metadata.created_by.type="agent_tool"` 且 `agent_id/session_id` 与当前 run 一致的任务，避免 Agent 删除页面或其它会话创建的定时任务。每次触发只运行一个 Agent run；微信来源任务执行完成后，ScheduleService 读取该 run 的完整 `result.json`，调用微信通道投递最终结果一次，并更新 run 的 `delivery.json`。
- `execute_code(language, code, files)` 是可选平台工具，`code_execution.enabled` 默认开启，但仍只有 Agent 授权 `execute_code` 时才注入。它只支持 `language="python"` 和 `language="shell"`，通过 Docker 运行配置镜像，强制 `--runtime=runsc`、`--network=none`、`--read-only`、`--cap-drop=ALL`、`--security-opt no-new-privileges`、CPU/内存/pids 限制和只读/读写的临时目录挂载；默认镜像为 Docker Hub 官方 `python:3.12-slim-bookworm`，Python 使用 `python /workspace/work/main.py`，shell 使用 `/bin/sh /workspace/work/script.sh`。执行器不允许 Agent 指定镜像、runtime、volume、env 或 Docker 参数；缺 Docker、缺 `runsc` 或缺镜像时返回 `ok=false` 工具观察，不降级为宿主机 subprocess。脚本保留为当前 Agent `/scratch/exec_{id}.py` 或 `.sh`，工具返回 `script_path`；每次调用使用独立系统临时目录挂载 `/workspace/input`（只读）、`/workspace/work` 和 `/workspace/output`，不挂载整个 Agent 工作区。成果收集到 `/artifacts/exec_{id}/` 后清理临时目录和输入副本，原始输入不删除。失败仍保留脚本；超时、取消先终止容器再清理，容器终止失败或成果收集失败则保留临时目录并返回 `recovery_path` 供恢复。没有长期 `code_runs/` 或额外 `execution.json`，执行观察沿用 Run 事件。`scratch/` 内脚本沿用现有保留期清理规则。
- DeepAgent 内置 `ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep` 等工具由 `deepagents` 默认 middleware 提供；`deepagent.todo_list` 默认开启，`write_todos` 由运行时接入 LangChain `TodoListMiddleware`，只有 Agent 显式配置 `todo_list=false` 时关闭。当前不启用 DeepAgent `LocalShellBackend`，因此不向 Agent 暴露非沙箱 shell `execute`。
- DeepAgent 原生 filesystem 使用受限的 `AgentFilesystemBackend(root_dir=workspace/agents/{agent_id}/workspace, virtual_mode=True)`。Agent 看到的 `/` 就是自己的私有目录；私有文件后端只允许修改 `scratch/`、`artifacts/`、`skills/`、`memories/`、`improvements/` 和 `meditations/`，并保护这些固定顶层目录本身不被删除。`browser/` 只由浏览器服务访问，Agent 文件工具的同步/异步读取、列举、搜索、下载和修改都不能触及其内容；不允许通过符号链接访问。启用的 `/webdav/` 由独立映射后端访问共享缓存，可写路径通过原生 HITL 审批后写回远端；不能删除挂载根目录。Agent 不应创建第二层 `/workspace/`，不能访问其它未声明顶层目录；越界写入、编辑、删除和上传返回带允许目录列表的 permission 诊断，作为可恢复工具观察交给 Agent 改用正确路径，不让整个 run 失败。历史目录由部署时受控的一次性文件操作整理，没有运行时兼容路径或自动迁移。Agent 不能直接访问 `workspace/config.yaml`、`workspace/context`、`workspace/runs`、`workspace/sessions`、其它 Agent 目录或项目源码。旧的 run 前加载 `files` state、run 后同步回磁盘机制及辅助函数已删除。
- 每个 Agent 的私有 skill 固定放在 `workspace/agents/{agent_id}/workspace/skills/{skill_id}/SKILL.md`，运行时传给主 DeepAgent 的 `skills` 参数固定为 `["/skills/"]`。平台会复制 DeepAgents 原版同名 `general-purpose` subagent 配置来覆盖自动生成版本，保留原版 description 和 system prompt，只在其提示词末尾追加“派发任务明确指定 skill 时才访问，否则不访问任何 skill”的约束。该显式 subagent 未声明 `skills`，因此不安装 `SkillsMiddleware`、不自动发现或激活主 Agent 的 skill；它继续继承主 Agent 的模型和工具，避免 workflow skill 派发 subagent 后再次命中自身形成递归。DeepAgent 主 Agent 会扫描该目录下包含 `SKILL.md` 的子目录并用 progressive disclosure 暴露 metadata；不再维护产品级 Skill index，也不需要在 `config.yaml` 里配置 Skill 列表。
- DeepAgent 运行时默认注入 `SkillImprovementMiddleware`，通过 LangChain `wrap_model_call` / `awrap_model_call` 生命周期钩子在每次同步或异步模型调用前把技能维护规则追加到模型请求；该 middleware 不负责 memory，长期记忆仍由 DeepAgent 原生 `MemoryMiddleware` 维护 `/memories/AGENTS.md`。`SkillImprovementMiddleware` 只管 Agent 自己的 `/skills/` 和 `/improvements/`：Agent 可以自动创建或更新 `/skills/{skill_id}/SKILL.md` 来沉淀可复用能力，并在 `/improvements/reflections/{run_id}.md`、`/improvements/reviews/{run_id}.md` 或 `/improvements/changes/{timestamp}_{change_id}.json` 记录原因、来源和变更摘要。已有 Skill 的触发条件、必需步骤、输出契约、工具/subagent 拓扑、串并行顺序和审批边界视为用户工作流，不能被自进化自行改写；历史审计中的平台判断必须按当前 runtime 重新验证。Skill 可用 `<!-- BEGIN USER CONTRACT -->` / `<!-- END USER CONTRACT -->` 标出硬约束，`AgentFilesystemBackend` 会在 write/edit/upload/delete 入口逐字保护该区块及包含它的 Skill 文件/目录；建议变更只能写入 `/improvements/`，用户仍可通过平台文件编辑入口修改正式契约。`/improvements/` 是审计材料，不是 active skill；只有 `/skills/{skill_id}/SKILL.md` 会在下一次 Agent 执行开始时作为 skill metadata 被扫描。
- Agent 长期记忆固定开启。运行时通过 DeepAgent 原生 `memory=["/memories/AGENTS.md"]` 启用 `MemoryMiddleware` 加载和维护这一个长期记忆索引文件，不预创建或填充模板；文件不存在时 `MemoryMiddleware` 按空记忆处理，首次持久化时由 Agent 使用内置文件工具创建。其它 `/memories/...` 细节文件不自动注入，Agent 需要时可用内置文件工具自行查找和读取。本地共享知识通过原生文件工具访问 `/files/`，对应 `workspace/context/knowledge/files/`。
- 运行时默认通过 `WorkspaceMiddleware` 向主 Agent 和使用文件工具的 general-purpose 子 Agent 的每次模型请求追加工作区目录用途、权限、脚本/成果位置及容器路径区别；同步和异步均生效，不重复累积，不写聊天历史或人格配置。目录定义、初始化及 Agent 文件权限共用 `agent_workspace.py`，实际物理路径经统一入口解析，Agent ID 显式传入运行时。长期记忆和浏览器研究细则不重复注入。Agent 特定记忆由 DeepAgent 原生 `MemoryMiddleware` 的 memory guidelines 负责；用户笔记、同步文档与共享知识统一使用原生文件工具；浏览器的搜索、正文提取和失败恢复流程由 `browser_search`、`browser_extract` 工具 description 负责；私有虚拟文件系统的目录认知由 `WorkspaceMiddleware` 负责，权限强制执行由 `AgentFilesystemBackend` 负责。 启用 WebDAV 时逐项追加 `/webdav/` 下目录路径、用户说明（缺省为用户文档与共享知识库）、权限、子目录优先规则和远端写回提示，主 Agent 与通用子 Agent 都接收；私有脚本、成果和记忆继续使用对应私有目录。
- 历史 `workspace/agents/{agent_id}/memory/store.json` 是旧版 DeepAgent store 遗留路径，不由运行时代码或迁移脚本自动处理。按用户偏好，旧 workspace 数据收敛直接在目标机器上做一次性文件操作；配置页只展示新版 `workspace/agents/{agent_id}/workspace/memories/`。
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
- 使用平台工具和运行时能力需要安装对应依赖：会话 checkpoint 依赖 `langgraph-checkpoint-sqlite`；`search_session` 中文关键词分词依赖 `jieba`；`browser_extract`/`browser_search` 依赖 `langchain-community`、`playwright`、`beautifulsoup4` 和 `lxml`；`arxiv` 依赖 `langchain-community` 和 `arxiv`；`yahoo_finance_news` 依赖 `langchain-community` 和 `yfinance`；`execute_code` 依赖生产机 Docker、已注册的 gVisor `runsc` runtime，以及已拉取的 `python:3.12-slim-bookworm` 或配置中的等价镜像。`run.sh dev/prod` 会在依赖安装后检查并执行 `python -m playwright install chromium` 准备浏览器二进制。
- 当前生产 systemd unit 直接运行 `.venv/bin/python -m server`，单独 `systemctl restart` 不会安装新依赖；提交后远端部署必须在 pull 和 HEAD 校验之后执行 `.venv/bin/python -m pip install .`，并确保 Playwright Chromium 已安装，再重启服务。
- `config.yaml` 属于本地 workspace 数据，不提交。
- 开发启动使用 `./run-dev.sh` 或 `./run.sh dev`。
- 生产启动使用 `./run-prod.sh` 或 `./run.sh prod`。
- `./run.sh setup-sudo` 仍用于安装受限 sudoers 规则，使生产服务能无密码执行受限的 `systemctl restart/status/is-active super-personal-platform.service`。
- `run.sh prod` 生成 systemd unit 时只使用系统临时文件并安装到 systemd 路径，不再把临时 service 文件写入 workspace。
- 提交项目前必须执行 `.codex/skills/project-commit` 工作流。

## Run Usage Visibility

- 每次模型调用通过 LangChain `AsyncCallbackHandler` 的 `on_llm_end/on_llm_error` 采集，回调随 invoke config 传递给主图和子 Agent；OpenAI-compatible 模型启用 `stream_usage`。不扫描最终 messages/checkpoint 统计，避免历史消息和流式重复累计。优先读取标准 `usage_metadata`，兼容 `llm_output.token_usage`；供应商未返回完整输入/输出用量或调用失败时标为未知，不推算为零。
- 强类型 `model_usage` 事件保存调用 ID、实际返回模型、输入/输出 tokens、缓存命中和错误标记；`workspace/runs/{run_id}/usage.json` 持久化按调用 ID 去重的明细、每次调用使用的 Provider ID/单价/币种以及执行片段。Run 详情和轻量索引包含 `usage` 汇总。失败、自动重试、审批恢复和手动重跑均保留已发生消耗；重启不清零。执行耗时在每个执行片段结束时累计，排队和等待审批不计入；进程中断或正在执行的片段有明确不完整标记，不声称是完整耗时。
- Providers 可配置 `input_price_per_million`、`output_price_per_million`（有限非负数，缺省未配置）和 `price_currency`（USD/CNY，默认 USD）。零单价有效；输入/输出两项单价及用量完整时，才按 `input_tokens * input_price / 1e6 + output_tokens * output_price / 1e6` 估算。每次调用保存执行时的价格，不随后续配置修改重算。缓存命中包含在输入总量中，当前估算不单独处理缓存折扣、缓存写入溢价、阶梯价、工具费用或税费，不替代供应商账单；不同币种分开汇总。
- Runs 页面沿用一分钟轮询和详情快照合并，新增全部/单 Agent、全部保留任务/近 24 小时/近 7 天创建任务的消耗汇总，以及 Run 消耗详情。时间筛选按 Run 创建时间，包含所选 Run 的全部执行消耗。旧任务不做历史迁移或补算，显示“未记录”；未知调用、未估价调用和有用量任务覆盖数分别显示。统计仅覆盖仍保留的 Run，15 天维护清理后的 Run 不再计入，不是长期财务账本。
- Runs 和共享聊天审批面板在提交期间显示批准中/拒绝中并禁止重复点击；成功后立即移除操作面板、显示恢复提示并更新本地 Run 状态，服务端轮询继续负责最终校正。Runs 状态使用中文短标签，避免状态列与 Agent 列重叠。

## Agent Workspace Directory Contract

| Agent 路径 | 用途 | Agent 文件工具权限 |
| --- | --- | --- |
| `/artifacts/` | 最终交付文件 | 读写，顶层不能删除 |
| `/scratch/` | 草稿与保留的执行脚本；按现有 scratch 保留期清理 | 读写，顶层不能删除 |
| `/skills/` | 可复用技能 | 读写，详细规则由技能 middleware 提供 |
| `/memories/` | 长期记忆 | 读写，详细规则由 MemoryMiddleware 提供 |
| `/improvements/` | 技能反思、评审、变更记录 | 读写，顶层不能删除 |
| `/meditations/` | 每日冥想记录 | 读写，顶层不能删除 |
| `/browser/` | 浏览器登录态、缓存、占用锁 | 仅浏览器服务访问 |
| `/webdav/` | Agent 配置映射的坚果云共享文档，虚拟挂载 | 默认 write，可配置 read；文本写回远端，挂载根不能删除 |

生产目录收敛须停止相关运行进程后直接执行一次性迁移，并核验文件清单和内容；同名冲突不覆盖，未知归属数据保留迁移备份。旧路径不参与运行时读取或回退。


### 文件入口收敛与 WebDAV HITL

- `/notes/` 已移除，不再初始化、不加入白名单、不注入提示词；临时笔记用 `/scratch/`，长期信息用 `/memories/`，成品用 `/artifacts/`。已有目录在目标机器一次性备份检查后移除，没有自动迁移。
- `search_context`、`write_context` 已删除，旧配置和历史 run 快照的工具 ID 在部署时一次性清理。原有本地知识无搬迁地挂载为 `/files/`，主/子 Agent 使用 ls/glob/grep/read_file/write_file/edit_file；本地文本写入无需审批，禁止越界、符号链接、删除和二进制上传。中文相关度排序及最近笔记的 Context 聚合查询不再提供；search_session 保留。
- WebDAV 可写目录生成 `FilesystemPermission(mode="interrupt", operations=["write"])`，只读目录仍 deny，读取 allow；保留子目录优先。原生 HITL 在工具执行之前暂停，后端允许经批准的 interrupt 写入但仍检查范围。复用 DeepAgent 原生条件谓词，并限定 approve/reject；主/子 Agent 继承一致规则，无用户开关。没有 session_id 的可写 WebDAV run 也使用 run_id + SQLite checkpoint 保存审批，确保能恢复。
- 原生文件写入先更新远端再更新缓存；远端编辑在共享 sync.lock 内读取最新内容。定时和手动 WebDAV 同步保持，删除及二进制上传仍不支持。
- Agent 弹窗显示全局坚果云来源及同步根，每项显示远端实际目录 → Agent 虚拟路径，以及“读写 · 写入需审批”。目录选择器显示远端当前位置；全局连接或同步关闭时显示挂载不可用。全局页面称为“WebDAV 文件同步”。


### 微信图片与附件发送

- Agent 授权 `send_attachment(file_path, kind="auto")` 后可将现有文件发回当前 run 固化的微信目标；不接受收件人、账号、URL 或宿主机路径。不生成图片。文件来源走同一个原生文件后端路径校验，支持 Agent 私有目录、`/files/` 和授权的 `/webdav/`；浏览器目录、越界及符号链接拒绝，WebDAV 读取不触发写入 HITL。
- `auto` 按内容标记识别 PNG/JPEG/GIF/WebP，按图片消息发送；其它格式作为普通文件。`kind=file` 可发送图片原文件，`kind=image` 要求支持的图片格式。单文件 1 字节至 20 MiB、每轮最多 10 个；工具返回 queued，不能宣称已经送达。无微信目标的 Web Chat/API run 返回可恢复错误；带微信投递目标的定时任务可使用同一工具。
- 每个成功排队的文件固化到 `runs/{run_id}/outgoing/{rerun_count}/`，index.json 保存文件名、来源虚拟路径、种类、大小和 SHA-256；同名同内容同种类去重，文件快照和索引原子替换，文件不会因源文件随后编辑而变化。明确重跑使用新的 generation，不发送旧一轮的附件；outgoing 随 run 保留期清理。
- RunDeliveryService 在成功 run 的最终文本之后逐个投递附件；失败/取消的 run 只发状态文本。delivery.json 的 delivered_parts 持久化每个成功部分，重试/服务重启跳过已确认成功部分；每部分稳定 client_id。网络响应不确定时仍可能重复，沿用至少一次投递语义；所有部分完成才标记 delivered，失败沿用退避及 dead_letter。
- ILinkClient 依据腾讯官方 openclaw-weixin 的 [upload](https://github.com/Tencent/openclaw-weixin/blob/main/src/cdn/upload.ts)、[CDN](https://github.com/Tencent/openclaw-weixin/blob/main/src/cdn/cdn-upload.ts) 和 [send](https://github.com/Tencent/openclaw-weixin/blob/main/src/messaging/send.ts) 实现：AES-128-ECB/PKCS7 加密，getuploadurl 申请上传位置（no_need_thumb），密文 POST 到 CDN，读取 x-encrypted-param，发送 IMAGE=2 或 FILE=4 item；AES key 使用官方 hex 文本的 base64 编码，文件 len 使用明文长度字符串。CDN 请求不携带 bot token，不跟随重定向。HTTP 200 中非零 ret/errcode 同样判定失败，避免误报发送成功。


## 多 Agent 群聊

- `/chat-groups` 支持 Web 文本群聊。成员引用已有 Agent，设置群内名称及可选追加 prompt；主持必须是成员。成员继承基础 Agent 的模型、工具权限、私有文件、Skills 和长期记忆；不会生成独立 Agent 或跨成员挂载私有文件。群名称/成员名称非空，成员 ID 稳定，群内名称唯一，最多 20 名成员。更换基础 Agent 必须移除旧成员并以新 ID 添加，移除不删除历史署名。
- `@` 选择器将可读名称映射到稳定成员 ID，发送请求按正文首次提及顺序传递 `mentions[]`；去重后依次执行，后一名成员看到前一名结果。普通消息不点名时由主持正常回复，Agent 回答里的 @ 不自动派工。显式“开始协作”才启动多轮分工。
- 自动协作由主持调用强类型 `group_decision(action, tasks, summary)` 工具，选择 `dispatch` 或 `finish`。每轮最多调度 4 名不同成员顺序执行，然后主持评估；最多 3 轮，允许提前结束。达到上限时只接受 finish，必须总结成果与未完成项；用户显式继续会创建新的最多 3 轮协作。控制工具只注入当前主持控制步骤，不提供给 general-purpose 子 Agent，不成为可配置平台工具。
- `ChatGroupService` 随 FastAPI lifespan 每秒推进持久化步骤，复用现有 RunWorker，不在 worker 内等待子任务。每群同时只有一条回复链/协作，暂停与审批等待也占用该位置。执行期间禁发新消息和编辑成员；审批复用现有 approve/reject/resume，审批恢复后继续队列。Run 重试耗尽、取消或主持未提交合法决定时群执行暂停；用户可重试当前步骤或停止。停止先落盘 stopping 意图，再取消当前 Run、清空待执行步骤；重启可继续完成停止动作。
- 权威数据位于 `chat_groups/{group_id}/state.json`，以原子替换同时保存定义、带序号与稳定 ID 的群消息、成员 session 映射/历史游标、历次协作及当前步骤；`chat_groups/index.json` 是可重建索引，读取列表以权威状态文件为准。群消息与协作保留，不套用 15 天自动删除；根目录列入 Workspace 保护目录。无历史迁移或兼容路径。
- 每个 `group + member + base_agent` 创建专属 `channel=chat_group` 内部长期 session，仍落在 `sessions/{session_id}/`，使用同一个 SQLite Checkpointer。内部 session 不出现在普通 Chat 会话选择中，普通 Chat/API Run 不能直接向其发送。维护服务显式保护群引用 session/checkpoint；当前未结束步骤引用的 Run 也受保护，其他 Run 沿用 15 天清理。
- 每个执行固化成员人格、prompt、模型选择及非敏感模型参数/价格，凭据仍从当前配置读取；基础 Agent/模型不可用时明确失败，不替换为默认 Agent。成员文件及外部资源内容继续读取执行时现状。内部 `GroupRunContext` 保存角色与模型快照、结构化输入消息及控制权限，不由公开 Run API 接收。运行时继续维持 `instructions/messages/options` 边界。
- 当前步骤在创建 Run 前固化确定性的 `run_group_{execution_id}_{step_number}`，Run 创建可幂等恢复，session 消息/Run 关联不重复追加。每次执行传递未接收群记录和当前任务，群记录作为带发言人的对话材料而非 system prompt；每条群记录与任务都有稳定 LangGraph message ID，失败重试、重启或停止后继续不会重复追加相同群记录。平台游标及发布结果在一次原子状态更新中推进；模型/工具外部副作用仍沿用 Run 原有重试语义，不声称 exactly-once。
- 前端 `chatComponents.jsx` 为单聊与群聊共用的消息列表、复制、Markdown、思考过程、审批及 IME 输入组件；`chatRuntime.js` 共用事件说明转换，群页按 Run 事件序号增量轮询，落盘 partial 用于刷新恢复。群页在切换时取消旧轮询，输入被锁定时仍可查看结果和处理审批。群消息的 Run 链接支持 `/runs?run_id=...` 定位；普通 Run 重跑接口拒绝群步骤，须在群内重试或继续。

认证 API：

```text
GET/POST /api/chat-groups
GET/PUT /api/chat-groups/{group_id}
GET /api/chat-groups/{group_id}/messages?after={seq}
POST /api/chat-groups/{group_id}/messages
POST /api/chat-groups/{group_id}/collaborations
POST /api/chat-groups/{group_id}/collaborations/{execution_id}/continue
POST /api/chat-groups/{group_id}/collaborations/{execution_id}/retry
POST /api/chat-groups/{group_id}/collaborations/{execution_id}/stop
```

创建/更新接收 `name/members[]/host_member_id/archived`；成员字段是 `id/agent_id/name/prompt`。发送和开始协作接收 `content/mentions[]/client_message_id`；同群相同客户端消息 ID 幂等返回已有执行。继续协作要求新的 `client_message_id`。详情返回共享历史、执行状态及当前 `active_run`；归档和恢复通过 PUT 的 archived 字段完成。第一版没有群附件上传、群定时协作或微信接入，保留原有单聊能力。群上下文调用 schedule 创建任务会返回可恢复说明，避免创建无法执行的内部 session 定时任务；请通过单聊或定时任务页面创建。
