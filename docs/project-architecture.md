# Project Architecture

本文件是当前项目架构入口。DeepAgent 重构的详细决策记录在 `docs/architecture-qa.md`，后续实现以该问答中的模型为准。

## Current Direction

- 项目仍是一个由 FastAPI 提供后端、React + Vite 提供前端、后端统一服务静态资源的单体应用。
- 后端 Python 版本目标为 3.12.x，默认监听端口为 `8888`。
- 运行入口仍是 `./run-dev.sh`、`./run-prod.sh` 和底层 `./run.sh`。
- 生产环境仍使用已提交的 `web/dist`，生产启动不现场构建前端。
- 工作区默认仍是项目目录下 `.super-personal-platform`，可通过 `--workspace` 覆盖。
- 目标产品边界收缩为：deepagents 公开 API 的后端任务执行、Web 单聊、微信个人号入口、坚果云 WebDAV Context、单 token 登录、基础配置、基础日志和生产更新。
- 版本、发布与回滚约定见 `docs/release.md`，变更记录见 `CHANGELOG.md`；需求文档统一落在 `docs/requirements/`，命名 `F<n>-<名称>.md`，规则见该目录 `README.md`。

## 发布与回滚命令

- `./run.sh prod [--workspace PATH]`：拉取生产分支、安装依赖并重启服务，仍是默认发布路径。
- `./run.sh prod --no-pull`：跳过远端拉取，只安装当前检出的依赖并重启，用于离线重装或固定提交部署。
- `./run.sh rollback <tag|branch|commit> [--workspace PATH]`：要求工作区干净，`git fetch --tags` 后把检出切到目标版本（detached HEAD），跳过 pull，再走同一套依赖安装与重启流程；解析不到目标时不做任何改动直接失败。
- 回滚后执行一次 `./run-prod.sh` 即回到生产分支：`update_git` 检测到 detached HEAD 会先切回生产分支再拉取，`before_head` 在切回分支之后记录，保证 `CODE_UPDATED` 判定正确。
- 回滚不回退 `workspace/` 运行时数据；目标版本必须能读取现有 Run、session 和 checkpoint，否则先备份工作区。当前没有自动回滚、灰度发布，也没有 `workspace/` 备份流程。

## 测试与质量门禁

- `server/tests/test_api_contract.py` 双向核对本文档列出的 `/api` 路径与 `create_app()` 实际注册路由：文档写了必须有实现，实现了必须有文档。成族说明的接口用 `/api/channels/wechat/*` 这种带 `*` 的真实前缀声明，避免整族逐条枚举；成族前缀必须是真实存在的接口族，测试会把每个带 `*` 的前缀当下已实现路由校验，不要用占位族名举例。新增接口必须同时更新本文档，否则测试失败。
- `server/tests/test_run_worker_service.py` 固化 Run worker 契约：同一进程同时只执行一个 Run，且单次执行抛异常后 worker 仍会在下一轮领取后续 Run。改变并发模型必须先改这条测试并同步本文档。
- `server/tests/test_run_script.py` 约束 `run.sh` 的发布、依赖安装、systemd 与回滚行为。
- 尚未落地的门禁：没有 CI 流水线、没有覆盖率门禁（`pytest-cov` 只作为可选开发依赖）、没有真浏览器端到端测试和并发/压力测试。

## Architecture Q&A

`docs/architecture-qa.md` 是本轮重构的产品和技术问答来源，已确定：

- 前端 Chat 页面不直接执行 Agent，而是通过后端创建 `source=web_chat` 的 run；Runs 页面只查看落盘任务、状态、事件和结果，不提供手动创建 Run 入口。
- DeepAgent 在后端由进程内持久化 Run Executor 运行，状态、事件、租约、心跳、重试和结果全部落盘；Checkpoint 只保存 LangGraph/Agent 状态，不替代任务队列或 worker lease。
- DeepAgent 运行时优先使用 LangGraph/DeepAgent `astream(..., subgraphs=True)` 读取主图与 sub-agent 子图的模型增量和图更新；运行时先把 LangGraph stream chunk 转换成明确的 `DeepAgentStreamEvent` / `RunEventPayload` 对象，再追加到 `workspace/runs/{run_id}/events.jsonl`。主 Agent 增量正文以 `assistant_delta` 事件作为唯一实时入口，并节流写入 `workspace/runs/{run_id}/partial.json` 快照；sub-agent 的非空模型响应单独写为强类型 `subagent_response` 事件，记录 `namespace`、Agent 名、节点、正文和消息类，不混入主回答。`running`、`agent_update`、`subagent_response`、`stream_fallback`、`image_attachments_textified` 等公开运行事件会同步聚合成 `partial.thinking[]`，作为 Chat 页面刷新后的 run 级思考过程恢复来源；`agent_update` 事件记录节点名、最新消息正文（`preview`）、该消息类型（`message_type`，如 `ai`/`tool`）和该消息声明的工具名（`tool_calls`），思考过程只取工具轨迹（有工具调用记为 `调用工具：<名称>`，工具返回取正文），模型正文已由 `assistant_delta` 写进正文、中间件钩子节点（如 `HumanInTheLoopMiddleware.after_model`）的节点名属内部实现，两者只写入 `events.jsonl` 供排查；思考过程前后端统一保留最近 20 条（`THINKING_SNAPSHOT_LIMIT` / `CHAT_THINKING_LIMIT`）。若当前 deepagents 版本或 fake agent 不支持 stream，则自动回退到 `ainvoke`，不影响最终结果。
- `workspace/runs/index.json` 维护所有 run 的摘要和当前状态。
- 工具人工确认沿用 DeepAgent/LangGraph 原生 HITL：`interrupt_on` 只开放 approve/reject，运行时把 `Interrupt.value` 转成 `RunApprovalRequest` 强类型对象；`approval_required` 和 `approval_resolved` 使用明确的事件 payload 类，不在运行链路传递无定义 dict。当前请求、决定和历次审批保存在 `workspace/runs/{run_id}/approval.json`，LangGraph 的实际恢复位置仍由 SQLite checkpoint 保存。
- 每个 run 使用 `workspace/runs/{run_id}/` 独立目录保存 `input.json`、`state.json`、`events.jsonl`、`result.json` 和 `delivery.json`；需要人工审批时额外保存 `approval.json`，记录强类型工具请求、当前决定和历史。run 由同一 FastAPI 进程内常驻 `RunWorkerService` 从落盘队列领取执行，而不是由 HTTP 请求内的临时 `asyncio.create_task` 执行；仅在 run 被 worker 领取后额外持有 `lock.json`，进入终态或 `waiting_approval` 后立即移除。`RunDeliveryService` 独立扫描 Run 索引，把审批通知和最终结果投递从微信接收协程、Scheduler 执行协程中解耦。
- 统一调度器使用 `workspace/schedules/` 落盘调度定义和状态；WebDAV Context 同步和未来 Agent 定时任务共用这一套调度机制。
- `workspace/sessions/index.json` 维护所有长期会话索引；长期 session 对微信和未来渠道默认开启。`workspace/sessions/active.json` 维护渠道身份到当前活跃会话的绑定；微信、API 和未来渠道共享 `workspace/sessions/{session_id}/`，每个 run 只引用 `session_id`；Agent 的 Checkpointer 固定开启，不再提供配置开关，开启时 DeepAgent/LangGraph 运行时状态写入 `workspace/sessions/{session_id}/checkpoints.sqlite`。
- 同一 `session_id` 同时只允许一个 `queued`、`running` 或 `waiting_approval` Run。RunService 在保存附件、追加消息或创建 Run 目录前强制检查，冲突通过 HTTP 409 返回当前 Run ID 和状态，防止多个 Run 覆盖同一个 LangGraph checkpoint。
- `Agent` 保存人格、模型、可选 Context 绑定和 DeepAgent 运行选项。
- Agent 的 DeepAgent 配置只保留 `max_iterations`、`todo_list` 和工具授权 `tools`。长期记忆、私有文件系统及会话 Checkpointer 固定开启；运行名使用 Agent 名称，debug 默认关闭。系统从工具注册表的 `approval_required` 生成 HITL `interrupt_on`；WebDAV 文件写入由原生 Permission 规则生成审批谓词，只有 `approval_required=true` 的可写目录进入 HITL，免审批目录直接放行，不再接受 Agent 自定义审批列表。原生文件工具按路径授权执行。移除 `name/debug/filesystem/use_longterm_memory/interrupt_on/subagents/response_format/context_schema/checkpointer/cache` 配置；系统继续管理通用子 Agent 和 Skills。
- 平台工具定义在代码中，不放入 workspace 散落配置；Agent 的 `deepagent.tools` 只是授权选择。当前可选平台工具为 `send_attachment`、`search_session`、`arxiv`、`yahoo_finance_news`、`browser_extract`、`schedule` 和 `execute_code`。授权 `browser_extract` 时运行时会同时注入隐藏的 `browser_search` 工具；搜索引擎固定为 Bing，不提供 workspace 配置或 Agent 入参选择。
- `system_prompt` 是注册表里唯一的始终注入平台工具（`always_on=true`），不由 `deepagent.tools` 授权控制，也不进入通用子 Agent 的工具列表；它只作用于当前 Run 所属 Agent 的 `system_prompt`。工具提供 `action=read` 与 `action=update`：`read` 由平台直接读 `config.yaml`，返回该 Agent 的 `system_prompt` 配置原文与长度、`effective_system_prompt` 实际基础正文和 `source=default|custom`，因为 Agent 自身的文件工具访问不到 `config.yaml`，而每轮注入的 system message 还包含平台 middleware 追加的工作区契约、长期记忆、技能和子 Agent 段落，Agent 无法自行分辨边界；`read` 走 `interrupt_on` 的 `when` 谓词跳过审批，`update`（以及缺省或非法 `action`）仍始终进入 `interrupt_on`，只开放 `approve` / `reject`，没有免审批租约。批准后平台用 PyYAML 节点位置做最小范围文本替换，把该 Agent 的 `system_prompt` 统一写成 `|-` 字面块，其它字段、注释与排版不变，再按 `parse_settings` 完整校验后用临时文件原子替换；写失败或校验失败时 `config.yaml` 字节不变。新提示词在该 Agent 的下一次 Run 生效，当前 Run 继续使用创建时的快照，旧文本只能从 Run 的 `approval.json` 与事件日志追溯，不保留独立历史或回滚入口。
- 系统提示词审批的可见性由服务端在中断时生成的 `description` 提供，内含“拟修改为（新）”与“当前（旧）”完整对照，读取当前配置失败时退回摘要并提示到 Web 端核对；Web 审批卡片按该对照文本渲染（不再展示 `new_prompt` 原始 JSON），微信审批通知展示约 600 字符的截断预览并提示到 Web 端查看完整对照，其它工具的审批通知保持原样。
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
      checkpoints.sqlite  # 无 session 的审批 Run

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
    {session_id}/
      checkpoints.sqlite
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
- `server/infrastructure` 负责配置加载、deepagents 运行时封装与模型适配器、坚果云 WebDAV、微信 iLink 客户端、FastAPI app 装配和 cookie session；DeepAgent 运行封装集中在 `server/infrastructure/deepagent_runtime.py`，其入口只接收 `instructions`、完整 `messages` 和结构化 `options`，不再同时接收“当前消息”和“历史消息”两套参数。

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

`POST /api/runs` 可接受可选 `session_id` 和 `attachments[]`，只负责创建 `queued` run 并写入落盘队列；执行由同一 FastAPI 进程内的 `RunWorkerService` 领取，不再绑定创建请求的生命周期。同一 session 已有活动 Run 时返回 409，响应 detail 包含 `message`、`active_run_id` 和 `status`。未传 `session_id` 时按独立一次性 run 处理；由于运行时始终注入带审批闸口的 `system_prompt` 工具，未带 `session_id` 的 Run 也会用 run ID 在自身 Run 目录建立可恢复 checkpoint（`action=read` 不触发中断，仍复用同一 checkpoint）。传入 `session_id` 时，运行时使用 `workspace/sessions/{session_id}/checkpoints.sqlite` 作为 LangGraph SQLite checkpointer，并把 `configurable.thread_id` 设为该 `session_id`，同时只传当前 run 消息。DeepAgent 原生 interrupt 会把 Run 切到 `waiting_approval` 并释放 worker lock；批准或拒绝会写入 `approval_resolved` 事件、重新入队，再通过同一 thread ID 的 `Command(resume=...)` 从中断点继续。三个审批接口中，`approve` / `reject` 是快捷入口，`resume` 接受结构化 `decision=approve|reject` 和可选拒绝理由。微信来源 Run 和带微信投递目标的定时 Run 会由 `RunDeliveryService` 发出审批通知；通知支持整条回复 `approve`、`同意`、`批准`、`允许`、`通过` 表示批准，`reject`、`拒绝`、`不同意`、`不批准`、`不允许`、`不通过` 表示拒绝；忽略英文大小写、首尾空格和句末标点，不匹配普通句子或“好/可以/继续/确认/OK”，短命令选择同一账号、peer、Agent 下最新的待审批 Run，旧 `/approve <run_id>` 与 `/reject <run_id> <原因>` 继续兼容。该接口保留给渠道接入、自动化和后端集成使用。`cancel` 会把 queued/running/waiting_approval run 标记为 `cancelled` 并释放 lock；`rerun` 只允许作用于 `completed/failed/cancelled` 终态 run，保留原 `input.json` 和事件审计，清除结果、审批快照并重新入队。

Chat API：

```text
POST /api/chat/session
POST /api/chat/session/new
GET /api/chat/sessions?agent_id={agent_id}
POST /api/chat/session/change
POST /api/chat/sessions/{session_id}/rename
GET /api/chat/sessions/{session_id}/messages?agent_id={agent_id}
GET /api/chat/sessions/{session_id}/attachments?agent_id={agent_id}&path={session_path}
DELETE /api/chat/sessions/{session_id}?agent_id={agent_id}
POST /api/chat/messages
```

页面 Chat 使用 `channel=web`、`channel_account_id=default`、`peer_type=private`、`peer_id=browser` 和当前 `agent_id` 在 `workspace/sessions/active.json` 中维护页面当前选中的长期会话。`GET /api/chat/sessions` 列出当前 Agent 名下的全部长期 session，包括微信、Web 和未来渠道；`POST /api/chat/session/change` 可以把 Web Chat 绑定到其中任意一个 session，但只更新 Web Chat 的 active binding，不改写 session 原始的渠道、账号和 peer 身份，也不改变微信侧 active binding。会话标题写在 `workspace/sessions/{session_id}/state.json` 并镜像到 `sessions/index.json` 摘要的 `title`/`title_source` 字段：第一条用户消息落盘时自动取前 24 字作为标题（`title_source=auto`），也可以在 Chat 左栏手动重命名（`POST /api/chat/sessions/{session_id}/rename`，`title_source=manual`，此后不再被自动标题覆盖）；历史会话没有标题时按首条用户消息在读取时现推导、不写回文件。会话的 `updated_at` 只表示内容变化时间：只有追加消息、保存附件和关联 run 会刷新它；选择、切换、绑定身份、归档和重命名只改状态或标题，不刷新 `updated_at`，因此 Chat 左栏按内容时间排序，点开一个旧会话不会把它顶到最前面（`last_selected_at` 仅记录最近一次选中）。读取消息、切换、发送消息、重命名和删除都校验 session 必须属于当前 Agent。`DELETE /api/chat/sessions/{session_id}` 删除一个长期 session：先移除 `workspace/sessions/index.json` 条目和 `active.json` 中指向它的 binding，再删除 `workspace/sessions/{session_id}/`（含 `messages.jsonl`、附件、成果和 `checkpoints.sqlite`）；该 session 仍在 `queued/running/waiting_approval` 时返回 409 并带上当前 `active_run_id`，缺失 session 返回 404。删除当前 Web Chat 选中的 session 后，前端重新调用 `POST /api/chat/session` 新建会话；删除微信 session 后 binding 消失，微信下一条消息会新建会话。`POST /api/chat/session` 和 `POST /api/chat/session/change` 会在当前 session 的 `last_run_id` 仍处于 `queued/running/waiting_approval` 时额外返回 `active_run`，让页面刷新或切换回来后可以显示 `partial.json` 并重新轮询事件。`POST /api/chat/messages` 创建 `source=web_chat` 的普通 DeepAgent run 并唤醒 `RunWorkerService`；请求可携带 `client_message_id`，同一 session、Agent 和客户端消息 ID 的重复请求返回已有 run，不重复写消息或执行 Agent。前端随后只轮询 `/api/runs/{run_id}/events?after={seq}`，按 `assistant_delta` 事件增量更新 assistant 气泡，完成、失败或取消后再读取 run 详情和 session messages 对齐最终历史。

Chat 的 `active_run` 识别 `queued`、`running` 和 `waiting_approval`。页面刷新后会从 Run 的 `approval.json` 恢复审批卡片；批准或拒绝调用统一 `resume` API，Run 恢复执行前输入框保持锁定，避免向同一 session 并发追加新消息。

`POST /api/chat/messages` 可携带 `attachments[]` 上传图片：每个附件沿用 session 附件格式提交 `type`、`mime`、`filename` 与 `data_url`（`bytes` 或 base64 等价字段也接受），后端在创建 run 前逐条解码校验，一次最多 6 张、单张不超过 20MB，且字节头必须是 PNG/JPEG/GIF/WebP，违规直接返回 400，不写消息也不创建 run；通过校验后 `type` 固定为 `image`、`mime` 按真实字节头判定（不信任前端声明），再由 `SessionService.save_attachments` 落到 `workspace/sessions/{session_id}/attachments/{message_seq}/{filename}`。纯图片消息允许 `content` 为空，后端按“用户发送了图片。”补齐正文，模型不支持图片时仍走既有 `image_attachments_textified` 文本降级。`GET /api/chat/sessions/{session_id}/attachments` 只服务该 session `messages.jsonl` 里已记录的 `session_path`：解析后要求目标仍位于 session 目录内且不是符号链接，未记录或缺失的路径返回 404；响应按记录或后缀返回真实 `Content-Type`，并带 `Cache-Control: private, max-age=3600`，页面缩略图复用同一个登录 cookie，不做匿名访问。

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

后台统一 Scheduler 每 5 秒扫描轻量调度索引，只判断 `next_run_at` 是否到期，不执行高频 WebDAV 同步。到期任务由独立 asyncio task 执行，一个等待审批的 Agent 定时任务不会阻塞其它 schedule 扫描。到期后按 `definition.type` 分发：`webdav_sync` 执行 WebDAV 文件同步并写调度事件；`maintenance_cleanup` 执行 15 天保留期清理并写调度事件；`agent_run` 每次触发只创建一个普通 `workspace/runs/{run_id}/`，唤醒 `RunWorkerService`，等待该 run 终态及投递终态后处理调度完成。Schedule `lock.json` 用于避免重复执行，并记录 `pid`、`created_at` 和 `heartbeat_at`；执行期间每 15 秒刷新 heartbeat。服务重启后，如果 schedule 原先正在等待 `queued/running/waiting_approval/completed` Run，会保留并复用 `current_run_id`，不会为同一次触发重新创建 Run；不存在可恢复 Run 的陈旧 schedule 才按失败重试策略处理。调度执行失败后会进入 `retrying` 状态，默认 1 分钟后重试，最多 3 次；重试耗尽后才标记 `failed` 并进入下一次正式触发周期，成功后重试计数清零。普通 Run 固定最多执行 30 分钟，执行期间每 15 秒刷新 run `lock.json.heartbeat_at`；超过上限、执行 task 被取消，或读 `list/get/events` 时发现 active run 已超过上限/心跳超过 120 秒未刷新，会按 `attempts/max_attempts` 重新入队或最终标记为 `failed` 并释放 `lock.json`。服务启动时会把上一进程遗留的 `running` Run 按同一重试策略恢复，保留 `queued` 和 `waiting_approval` Run；普通 Run 后台任务运行在 FastAPI 进程内，项目不引入 Redis/Celery，也不拆独立 systemd worker service。

`/api/schedules` 是定时任务管理页面使用的后端入口。前端只允许创建、编辑和删除 `agent_run` 类型任务，字段核心为 `prompt + agent_id + trigger`；内置 `context_webdav_sync` 和 `maintenance_cleanup` 由系统自动生成，只能查看状态和手动 `run-now`，不能通过页面编辑或删除。当前触发器支持 `interval`、5 字段 `cron` 和 `once`。Agent 也可以在被授权 `schedule` 平台工具后，通过同一个 ScheduleService 创建、查看、更新和删除定时任务；工具只允许管理由该工具在当前 `agent_id + session_id` 下创建的任务，并把微信来源 run 创建的定时任务结果回发到原微信会话。

微信 API 继续保留 `/api/channels/wechat/*` 账号管理和登录生命周期接口。

系统 API 只保留日志和生产更新能力；配置文件读写统一走 Workspace 文件 API，可视化配置页也复用同一个读写入口。

Workspace 文件 API：

```text
POST /api/workspace/list
POST /api/workspace/read
PUT /api/workspace/write
POST /api/workspace/delete
```

这些接口只允许访问 active workspace 内部路径，用于前端“工作目录”和“技能库”页面浏览、编辑 UTF-8 文本文件和删除非固定路径。`PUT /api/workspace/write` 可选 `create`（默认 `false`）：`false` 时保持“文件必须已存在、否则 404”，`true` 时允许创建缺失的父目录并写入新文件，供 `/skills` 新建 `SKILL.md` 使用；已存在文件的覆盖、1MB 上限、可编辑扩展名白名单、路径穿越与根层保护规则不变。`config.yaml` 通过写入入口保存时仍执行配置校验；`config.yaml` 和根层固定目录 `agents/`、`context/`、`runs/`、`schedules/`、`sessions/`、`channels/`、`logs/` 不能删除，其它 workspace 内文件或目录允许删除。

System API 额外保留：

```text
POST /api/system/logs/list
POST /api/system/logs/read
POST /api/system/update-service
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

认证 API：

```text
POST /api/auth/login
POST /api/auth/logout
GET /api/auth/me
```

登录接口校验单 token，通过后写入 HttpOnly cookie，`Max-Age` 固定 30 天（`SESSION_MAX_AGE_SECONDS`），是持久化 cookie 而不是随浏览器退出即失效的会话 cookie，避免手机端浏览器、内置 WebView 或添加到主屏幕的应用每次打开都要求重新输入 token；`/api/auth/me` 供前端判断当前是否已登录，`/api/auth/logout` 只清除客户端 cookie。token 本身仍只写在这个 HttpOnly cookie 里，前端不把 token 存到 localStorage。`server/tests/test_api_contract.py` 会双向核对本文档列出的 `/api` 路径与 `create_app()` 实际注册的路由：文档写了必须有实现，实现了必须有文档；成族说明的接口用 `/api/channels/wechat/*` 这种带 `*` 的写法声明，避免整族逐条枚举。

`/webdav-context/test` 可使用配置页当前草稿或已保存配置测试坚果云 WebDAV 连接，只返回目标 URL、HTTP 状态和是否成功，不回传账号密码；`/webdav-context/sync` 现读已保存的 `workspace/config.yaml`，手动执行一次 WebDAV 文件同步，用于配置变更后立即制作本地缓存，而不必等待后台间隔或重启服务。后台 `context_webdav_sync` 调度任务同样按当前 `workspace/config.yaml` 取根目录与账号，两个入口不会出现"页面改了、后台还按旧根同步"的偏差。

`/maintenance/preview` 只计算清理计划不删除文件；`/maintenance/run` 执行清理。当前默认保留期统一为 15 天：删除超过保留期且已终态的 run、超过保留期未活跃且没有活动 run 引用、也没有被 `workspace/sessions/active.json` 指向的 session、随 session 和 Run 目录一并删除的所属 checkpoint 数据库、旧调度事件、旧平台日志、Agent scratch 和 Context cache 里的旧文件，以及很旧的孤立 lock；同时会清理 `active.json` 中指向已不存在 session 的脏 binding。知识库、WebDAV 文本/图片缓存、微信登录态和 Agent 长期记忆不做自动删除。自动清理不再使用单独后台 loop，而是作为 `workspace/schedules/maintenance_cleanup/` 内置定时任务落盘并显示在 `/schedules`。

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

Chat 输入区左侧是常驻“+”动作菜单：点开后当前只有“图片”一项，选中即打开图片选择器；菜单向上弹出并支持 `Esc`、点击外部关闭，运行中或达到 6 张上限时不可用。选择图片、直接粘贴剪贴板图片、把图片文件拖进聊天区三种入口共用同一套校验（`chatAttachments.js` 先按扩展名/声明 MIME 过滤并读成 data URL，最多 6 张、单张不超过 20MB），待发送图片以可单个移除的缩略图挂在输入框上方；只发图片不写文字也能发送，发送失败时文字和图片一起恢复到输入区。粘贴图片由输入区拦截并转为待发送图片，`MarkdownComposer` 只做“不要把剪贴板图片插进草稿”的拦截，草稿文本本身不变。用户消息气泡内按 `attachments` 渲染图片缩略图，点击在新标签页打开原图，历史消息刷新后通过 `GET /api/chat/sessions/{session_id}/attachments` 重新取图。移除待发送缩略图只丢弃浏览器里的待发送内容；图片一旦随消息发送就落在 `sessions/{session_id}/attachments/{message_seq}/`，随会话删除（`DELETE /api/chat/sessions/{session_id}` 会 `rmtree` 整个会话目录）或维护清理一并删除，没有单独的附件删除接口。技能审批沿用 Run 的审批卡片：Agent 改写 `/skills/**` 时会话进入 `waiting_approval`，页面上出现审批面板，批准或拒绝后才继续执行。

- `/chat` 是页面 Chat 工作区，采用左侧列表布局：左栏是当前 Agent 的 session 列表（顶部为新建会话按钮和 Agent 选择，列表项第一行是会话标题、第二行是微信/Web 等来源与渠道身份、消息数和更新时间，选中项高亮，右侧提供重命名和删除按钮），中间是消息与输入区，右侧保留当前会话状态栏；不展示其它 Agent 的 session。左栏只列出有内容的会话（`message_count > 0` 或已有标题）：Web Chat 的 active binding 在页面打开时就存在，但没发过消息的空会话不占列表行，也没有 0 条消息的 Web Chat 行；无会话时提示直接发消息即新建。会话标题存在 `workspace/sessions/{session_id}/state.json` 的 `title`/`title_source`，列表里没有标题时退回渠道标签（如“微信”“Web Chat”）；重命名打开只含标题字段的轻量弹窗，调用 `POST /api/chat/sessions/{session_id}/rename` 写入手动标题并刷新列表。删除会话点击后二次确认再调用 `DELETE /api/chat/sessions/{session_id}`；删除当前选中的会话后页面立即新建 Web 会话，删除其它会话后刷新列表，会话仍有活动 Run 时显示后端 409 提示并保留该 session。页面可以打开并续聊微信 session，但只改变 Web Chat 当前选择，不切换微信通道本身的活跃会话。消息进入长期 session，执行仍由后端 DeepAgent run 完成。Chat 输入框使用普通 `Enter` 发送、`Shift+Enter` 换行；中文/日文等输入法正在 composition 组词时不拦截 `Enter`，避免拼音选词直接发送。发送动作有同步 in-flight 锁，并由后端按 `client_message_id` 幂等创建 run，避免连续按键、双击或重复请求生成两条相同消息。用户和 assistant 消息都提供复制按钮，桌面端按钮位于气泡外侧操作位，移动端使用顶部操作位，不再通过贯穿全文的右内边距压缩正文；复制原始消息文本并短暂显示成功状态。Chat 的 assistant 气泡内置轻量 Markdown 渲染，支持标题、列表、引用、代码、链接、加粗、水平分隔线和 GitHub 风格表格；宽表格只在表格容器内横向滚动，不撑开聊天布局。Chat 气泡运行中会把后端 `running`、`agent_update`、`stream_fallback`、`image_attachments_textified` 等可公开运行事件聚合到“思考过程”区域并展开显示，`assistant_delta` 只作为正文增量；图更新事件只在工具轨迹上有意义时才聚合：带工具调用显示 `调用工具：<名称>`，工具返回显示返回正文，模型正文和中间件钩子节点名都不显示；思考过程前后端统一保留最近 20 条，前端 `runEventThinkingText`/`mergeChatThinking` 与后端 `_thinking_text`/`_merge_thinking` 使用同一口径，避免实时与刷新后的思考过程不一致；run 结束后正文保留为主内容，“思考过程”自动折叠并可手动展开查看。页面刷新或切换 session 后，Chat 先读 `workspace/sessions/{session_id}/messages.jsonl` 展示正文，再按 assistant 消息的 `run_id` 读取 `workspace/runs/{run_id}/partial.json` 恢复已折叠的思考过程；如果后端返回 `active_run`，页面会先显示该 run 的 `partial.json` 正文和思考过程，再从 `events.jsonl` 重新接上事件轮询，直到 run 完成或失败。`820px` 以下使用独立移动布局：全局侧栏收进顶部菜单控制的抽屉，session 列表变成顶部可横向滚动的会话条（含新建会话按钮与 Agent 选择），Chat 占满剩余动态视口，会话诊断栏隐藏，消息区独立滚动且输入框固定在工作区底部。
- `/`, `/runs`, `/agents` 都进入新的 Runs 工作区；`/agents` 只是旧入口跳转，不恢复旧 Agent 管理页面。Runs 工作区只承担运行记录查看、状态轮询、事件与结果展示，不提供 Prompt/Agent ID 表单或手动创建按钮；详情页支持取消 `queued/running/waiting_approval` run、审批或拒绝待确认工具调用，以及重跑 `completed/failed/cancelled` run。
- `/workspace` 展示真实 workspace 文件浏览器，可查看和编辑 UTF-8 文本文件，并可删除非固定路径；`config.yaml` 在这里按原生 YAML 文本展示和编辑，不承载专用配置表单；`config.yaml` 和根层固定骨架目录不可删除。
- `/skills` 是技能库页面，读取 `config.yaml` 的 Agent 列表后按 Agent 分组列出各自私有工作区里的技能，路径固定为 `workspace/agents/{agent_id}/workspace/skills/{skill_id}/SKILL.md`；左栏默认选中第一个 Agent、只展开该 Agent 的技能，行内显示 frontmatter 的 name、description 摘要、修改时间，以及“不会加载/规格警告/契约”标记（鼠标悬停给出具体原因），右栏是 `SKILL.md` 纯文本编辑器，支持新建、保存和删除整个技能目录（删除二次确认），技能的辅助文件只列出名称与大小、仍到 `/workspace` 维护。页面按 SkillsMiddleware 的真实口径分级：缺少 `---` frontmatter、frontmatter 未闭合或不是映射、缺 `name` 或 `description`、SKILL.md 缺失或超过 10MB 才会让运行时跳过该技能（标记“不会加载”并禁用保存）；`name` 与目录名不一致、`name` 不符合 Agent Skills 规范、`description` 超过 1024 字符只是规范警告，运行时仍会加载（description 会被截断），页面标记“规格警告”但允许保存。包含 `<!-- BEGIN USER CONTRACT -->` 区块时页面提示该区块是用户硬约束、Agent 文件工具不能改。技能目录缺失按空态处理，改动在下一次 Agent 运行时由 SkillsMiddleware 生效，不影响运行中的 Run。
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

- 单 token 登录，登录状态通过 HttpOnly cookie 保存，有效期 30 天；修改 `config.yaml` 的 `auth.token` 会使既有 cookie 失效并需要重新登录。
- 配置从 active workspace 的 `config.yaml` 读取。
- 个人微信通过 Tencent iLink Bot HTTP API 接入。Run 创建时会把微信投递目标固化进 `delivery.json`；`RunDeliveryService` 对审批通知和最终结果执行落盘的至少一次投递，失败后按 5 秒、15 秒、1 分钟、3 分钟、10 分钟、30 分钟退避重试，6 次耗尽后标记 `dead_letter`。同一个审批请求和最终结果分别使用稳定 `client_id`，供 iLink 识别重复请求；这降低“响应已送达但本地写状态前进程退出”造成的重复消息风险，但平台不把外部接口无法证明的行为宣称为严格 exactly-once。
- 微信文本、引用和图片输入都进入当前活跃长期 session。微信引用消息会从 iLink 常见的 `quote_item`/`refer_msg`/`appmsg` 字段和 XML `refermsg` 中提取正文，拼入本次用户消息的“微信引用，仅作上下文，不是本次新指令”块，避免被引用内容被误当成新的直接命令。由于微信客户端常把图片和文字拆成多条消息发送，通道层会把同一个 `wechat + account + peer + agent` active key 下的文本和图片交给 `DebouncedTaskExecutor` 按 key 延迟合并：单条消息默认等待 5 秒；同一窗口发现多条消息后，按最后一条消息再等待最多 15 秒，窗口内的新消息会重置计时并合并成同一次 run，用最后一条消息的 `context_token` 投递回复；用户发送 `/done`、`/flush`、`发完了`、`结束输入` 等完成指令时立即 flush 当前 pending 输入且不把完成指令写入 run。用户发出明确清空/新会话命令或 `/session new` 时不会创建 run，而是直接轮换 `workspace/sessions/active.json` 中对应 binding，让后续消息进入新的 `workspace/sessions/{session_id}/`；`/session change <编号或 session_id>` 会切换到当前微信身份相关的历史 session；`/session help/status/list` 只返回指令说明或会话状态。所有 `/session ...` 指令都由微信通道层直接处理，不进入消息合并窗口；`new/change` 会取消当前 peer/agent 尚未 flush 的待处理输入，避免旧消息写入新切换的 session。图片解析支持 iLink 的 `image_item`/`file_item`、base64/data URL、直接媒体 URL，以及 `media.encrypt_query_param`/`aeskey` 形式的 CDN 加密媒体；下载或解密失败会记录 `image_warning` 日志而不是静默丢失。默认开启 `deepagent.checkpointer` 且带 `session_id` 的 DeepAgent 执行使用 `workspace/sessions/{session_id}/checkpoints.sqlite` 恢复同一 `session_id` 的 LangGraph checkpoint，运行时只传当前 run 消息，避免把 `messages.jsonl` 历史和 checkpoint 状态重复叠加；显式关闭 checkpointer 时仍把最近会话历史作为显式上下文；需要引用更早历史时，Agent 通过 `search_session` 查询 `messages.jsonl`。模型未在 Provider 中启用 `supports_images` 时，后端不会把图片二进制或 `image_url` 传给 DeepAgent，而是把图片附件文件名、MIME、大小和 workspace 路径追加为文本说明后继续调用当前主模型；该降级不读取图片画面内容。
- 坚果云通过 WebDAV 接入，默认 endpoint 为 `https://dav.jianguoyun.com/dav/`。
- DeepAgent 依赖 `deepagents>=0.7.15,<0.8`、LangGraph 和 `langgraph-checkpoint-sqlite`；后端任务执行结果必须落盘，带 `session_id` 且 Agent 未显式关闭 `deepagent.checkpointer` 的任务还会把 LangGraph checkpoint 写入统一 SQLite 文件。生产依赖同时固定 `cryptography>=38,<49`，避免部署时走不兼容本机 Rust 工具链的源码构建路径。
- `search_session(query, top_k, role, scope)` 检索会话历史，`scope` 默认为 `current`，只查当前 run 的 `session_id` 对应 `workspace/sessions/{session_id}/messages.jsonl`；传 `scope="related"` 时，按当前 session 的 `active_key` 或 `channel + channel_account_id + peer_type + peer_id + agent_id` 搜索同一渠道身份下的相关 session，包括清空上下文前归档的旧 session。Agent 不能传任意 `session_id`。搜索使用 jieba 对中文 query 分词，并结合精确子串命中评分；返回 session 元数据、是否当前 active、消息序号、角色、时间、run ID、片段和附件元数据。该工具用于用户引用“刚才/前面/之前/那张图/那个链接”等同一微信或 API 长期会话中的历史消息，也用于用户给关键词要求找相关旧会话。
- `arxiv(query, top_k)` 使用 LangChain Community 的 arXiv wrapper 检索论文，依赖 `arxiv` 包，运行时内置全局 3 秒请求间隔，作为免费学术/知识工具授权给需要的 Agent。
- `yahoo_finance_news(ticker, top_k)` 使用 LangChain Community 的 Yahoo Finance News 工具和 `yfinance` 获取公开股票代码相关新闻，定位为轻量财经新闻上下文，不作为交易级行情或完整市场数据源。
- 平台工具面向外部下游的可恢复异常默认返回结构化 `ok=false` JSON 给 DeepAgent 处理，包括浏览器导航/检索失败、公开数据源超时、调度工具参数校验或权限边界失败；Agent 应基于该观察结果换工具、换来源、询问用户或解释限制。只有平台自身无法继续执行的错误，例如 run 落盘失败、配置加载失败、运行时初始化崩溃，才应向上抛出并标记整个 run failed。
- Context 可配置一个坚果云 WebDAV 同步根目录 `context.webdav_sync.root_path`，远端实际路径按 `nutstore.root_path + context.webdav_sync.root_path` 解析；本地文件缓存固定落在 active workspace 的 `workspace/context/webdav/files/`，索引固定写入 `workspace/context/webdav/index.json`。
- WebDAV 同步触发已纳入统一调度器：`ScheduleService` 每次 `bootstrap`（启动、5 秒轮询和调度 API 调用）都重新读取 `workspace/config.yaml`，按当前的 `nutstore.enabled`、`context.webdav_sync.enabled`、`context.webdav_sync.interval_seconds` 生成/更新内置 `workspace/schedules/context_webdav_sync/definition.json`，执行 `webdav_sync` 时再按当前配置构造一次 `WebDAVContextService`，因此改同步根、连接根或账号不需要重启服务，和手动 `/webdav-context/sync` 行为一致；同步完成事件会记录本次实际使用的 `sync_root_path`。配置读写失败时保留上一份可用设置并只在状态变化时记日志，避免配置写坏导致调度停摆或日志刷屏。调度轮询间隔固定为 5 秒，但实际同步间隔仍由 `context.webdav_sync.interval_seconds` 控制，且配置校验要求不少于 60 秒。内置 `maintenance_cleanup` 同样按当前配置执行，不再固化启动快照。
- 全局 `context.webdav_permissions`、`protected` 和历史 `webdav_roots` 已移除。全局连接与同步不决定 Agent 权限；已有配置在部署前做受控一次性整理，不添加运行时迁移或旧字段回退。
- `agents.definitions[].webdav` 配置包含 `enabled=false` 和 `directories=[]`。每个目录包含相对于全局同步根的 `path`、`permission="write"`（或 `read`）、`approval_required`（默认 `true`）和 `description`；write 包含读取，`approval_required=false` 表示该目录写入不再逐次审批，`permission="read"` 时该字段归一化为 `true`。Agent 列表点击弹窗配置，支持添加/移除多个目录、浏览已同步目录或手工填写未同步路径，每项独立权限、审批开关和用途说明。空列表不授权任何目录，规范化后重复路径拒绝保存。运行快照保存完整列表，缺省该字段的既有配置语义等同 `true`，不需要迁移。
- `WebDAVPathPolicy` 将目录配置编译为 DeepAgent 原生 `FilesystemPermission`，按路径深度降序让子目录覆盖父目录，支持父只读/子读写及父读写/子只读，用户无需排序。只读目录写规则为 `deny`；可写目录按 `approval_required` 生成 `interrupt`（写入需 HITL）或 `allow`（免审批直写）。规则以 `/webdav/**` 默认拒绝收尾，不影响私有工作区；路径中的 glob 特殊字符按字面转义。`create_deep_agent(permissions=...)` 传给主 Agent，通用子 Agent 继承同一规则。文件后端复用原生 `_check_fs_permission`（私有 API 接口由回归测试约束），不另造匹配语义。未选择的祖先只允许目录导航，不能读取同层文件、无关兄弟或被替换成文件的祖先。
- `CompositeBackend` 将 `/webdav/` 路由至共享缓存根的 WebDAVFilesystemBackend，默认后端仍为私有 AgentFilesystemBackend；不复制缓存、不使用软链接。目录结构保持不变，例如 `/项目/草稿` 暴露为 `/webdav/项目/草稿/`。旧单目录配置仅在目标机器受控一次性转换成列表，保留原范围、权限、开关和说明，不加入运行时迁移。
- WebDAV 同步会解析 Markdown 里的 `![...](...)` 和 `<img src="...">`，把被引用的 `.png`、`.jpg`、`.jpeg`、`.gif`、`.webp`、`.svg` 按相对目录结构作为二进制资源缓存到 `workspace/context/webdav/files/`；这些资源不进入文本检索索引，目前不支持上传。
- `browser_extract(url, include_links, max_chars)` 使用 Playwright headless browser 打开公开 `http/https` 页面，提取渲染后的文本和链接；对 `raw.githubusercontent.com`、`gist.githubusercontent.com` 和常见源码/文本扩展名 URL，会先用 HTTP 客户端按文本资源直接读取，避免纯文本文件因 Chromium SSL/导航问题失败，只有文本直取失败时才回退到浏览器导航。授权该浏览器能力时还会注入 `browser_search(query, top_k)`，它固定用同一个 Playwright 浏览器打开 Bing 搜索页并提取公开结果 URL、标题和片段，不新增 `web_search` provider、搜索引擎配置或 Agent 可选 `engine` 参数。浏览器工具的导航超时、页面提取失败、DNS/私网拦截、profile 占用等下游异常不再向上抛出导致整个 run failed，而是返回 `ok=false` 的 JSON 观察结果，交给 DeepAgent 改用其它搜索词、其它来源或向用户解释限制；真正的 RunService/落盘/配置加载等平台级异常仍会让 run failed。后端封装会拒绝 URL 主机本身为 localhost、私有网段、内网地址或非 `http/https` URL；未配置浏览器代理时，本机 DNS 若把公开 hostname 解析到私网/内网地址也会拦截，但公开 hostname 被 DNS 污染成 `0.0.0.0` 不作为私网拦截处理，而是交给文本直取或浏览器实际导航返回结果/错误；配置 `browser.proxy` 或进程代理环境变量时，不做本机 DNS 私网预解析，由浏览器代理负责解析。若 `browser.allow_private_hosts` 显式列出目标 hostname，或用 `.wulang.vip` 这类后缀匹配目标 hostname，则允许该 host 解析到内网/私有 IP 后继续访问。浏览器启动优先使用 `browser.proxy`，未配置时回退到进程环境变量 `HTTPS_PROXY`、`HTTP_PROXY` 或 `ALL_PROXY`，导航超时由 `browser.timeout_ms` 控制，默认 60000ms。带 `tool_context` 的 Agent run 会自动复用 `workspace/agents/{agent_id}/workspace/browser/` 的 Playwright persistent profile，并用 `profile.lock.json` 避免授权会话和后台抓取并发占用；同一个 Agent 的后台 `browser_extract`/`browser_search` 会先等待 profile lock，按任务串行排队，最多等待 `browser.timeout_ms`，不同 Agent 仍使用各自 profile 并行。profile lock 记录持有进程 pid，pid 不存在时会立即清理；旧版无 pid lock 才继续使用 1 小时兜底清理。授权、搜索和抓取使用同一组桌面 Chrome UA、中文语言、上海时区和基础自动化隐藏参数。工具参数仍只有网页读取所需的 `url/include_links/max_chars` 和搜索所需的 `query/top_k`，Agent 不能传 profile ID、路径或搜索引擎。没有 tool context 时保持一次性无状态浏览器。
- `schedule(action, ...)` 是单一调度管理工具，支持 `create/list/get/update/delete`。创建时只能使用当前 Agent、当前长期 session 和当前渠道投递上下文，触发器支持 `once`、`interval` 和 `cron`；`list/get/update/delete` 只能作用于 `metadata.created_by.type="agent_tool"` 且 `agent_id/session_id` 与当前 run 一致的任务，避免 Agent 删除页面或其它会话创建的定时任务。每次触发只运行一个 Agent run；微信来源任务执行完成后，ScheduleService 读取该 run 的完整 `result.json`，调用微信通道投递最终结果一次，并更新 run 的 `delivery.json`。
- `execute_code(language, code, files)` 是可选平台工具，`code_execution.enabled` 默认开启，但仍只有 Agent 授权 `execute_code` 时才注入。它只支持 `language="python"` 和 `language="shell"`，通过 Docker 运行配置镜像，强制 `--runtime=runsc`、`--network=none`、`--read-only`、`--cap-drop=ALL`、`--security-opt no-new-privileges`、CPU/内存/pids 限制和只读/读写的临时目录挂载；默认镜像为 Docker Hub 官方 `python:3.12-slim-bookworm`，Python 使用 `python /workspace/work/main.py`，shell 使用 `/bin/sh /workspace/work/script.sh`。执行器不允许 Agent 指定镜像、runtime、volume、env 或 Docker 参数；缺 Docker、缺 `runsc` 或缺镜像时返回 `ok=false` 工具观察，不降级为宿主机 subprocess。脚本保留为当前 Agent `/scratch/exec_{id}.py` 或 `.sh`，工具返回 `script_path`；每次调用使用独立系统临时目录挂载 `/workspace/input`（只读）、`/workspace/work` 和 `/workspace/output`，不挂载整个 Agent 工作区。成果收集到 `/artifacts/exec_{id}/` 后清理临时目录和输入副本，原始输入不删除。失败仍保留脚本；超时、取消先终止容器再清理，容器终止失败或成果收集失败则保留临时目录并返回 `recovery_path` 供恢复。没有长期 `code_runs/` 或额外 `execution.json`，执行观察沿用 Run 事件。`scratch/` 内脚本沿用现有保留期清理规则。
- DeepAgent 内置 `ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep` 等工具由 `deepagents` 默认 middleware 提供；`deepagent.todo_list` 默认开启，`write_todos` 由运行时接入 LangChain `TodoListMiddleware`，只有 Agent 显式配置 `todo_list=false` 时关闭。当前不启用 DeepAgent `LocalShellBackend`，因此不向 Agent 暴露非沙箱 shell `execute`。
- DeepAgent 原生 filesystem 使用受限的 `AgentFilesystemBackend(root_dir=workspace/agents/{agent_id}/workspace, virtual_mode=True)`。Agent 看到的 `/` 就是自己的私有目录；私有文件后端只允许修改 `scratch/`、`artifacts/`、`skills/` 和 `memories/`，并保护这些固定顶层目录本身不被删除。`browser/` 只由浏览器服务访问，Agent 文件工具的同步/异步读取、列举、搜索、下载和修改都不能触及其内容；不允许通过符号链接访问。启用的 `/webdav/` 由独立映射后端访问共享缓存；需要审批的可写路径在原生 HITL 批准后写回远端，配置为免审批的可写目录直接写回；不能删除挂载根目录。Agent 不应创建第二层 `/workspace/`，不能访问其它未声明顶层目录；越界写入、编辑、删除和上传返回带允许目录列表的 permission 诊断，作为可恢复工具观察交给 Agent 改用正确路径，不让整个 run 失败。历史目录由部署时受控的一次性文件操作整理，没有运行时兼容路径或自动迁移。Agent 不能直接访问 `workspace/config.yaml`、`workspace/context`、`workspace/runs`、`workspace/sessions`、其它 Agent 目录或项目源码。旧的 run 前加载 `files` state、run 后同步回磁盘机制及辅助函数已删除。
- 每个 Agent 的私有 skill 固定放在 `workspace/agents/{agent_id}/workspace/skills/{skill_id}/SKILL.md`，运行时传给主 DeepAgent 的 `skills` 参数固定为 `["/skills/"]`。平台会复制 DeepAgents 原版同名 `general-purpose` subagent 配置来覆盖自动生成版本，保留原版 description 和 system prompt，只在其提示词末尾追加“派发任务明确指定 skill 时才访问，否则不访问任何 skill”的约束。该显式 subagent 未声明 `skills`，因此不安装 `SkillsMiddleware`、不自动发现或激活主 Agent 的 skill；它继续继承主 Agent 的模型和工具，避免 workflow skill 派发 subagent 后再次命中自身形成递归。DeepAgent 主 Agent 会扫描该目录下包含 `SKILL.md` 的子目录并用 progressive disclosure 暴露 metadata；不再维护产品级 Skill index，也不需要在 `config.yaml` 里配置 Skill 列表。
- 运行时不注入 Skill 自进化 middleware：Agent 不会自动创建或改写 `/skills/`，也没有 `/improvements/` 审计目录；skill 只在用户明确要求时创建或更新，用户在对话里要求时 Agent 用原生文件工具写入 `/skills/{skill_id}/SKILL.md`，也可以直接通过平台文件入口维护。因为技能文件会被下一次运行当作提示词内容加载，`skill_write_permissions()` 在传给 `create_deep_agent` 的 permissions 末尾追加 `/skills` 与 `/skills/**` 的 write `interrupt` 规则：Agent 的 `write_file`、`edit_file` 和 `delete` 命中技能路径时走原生 HITL，run 进入 `waiting_approval` 并复用同一套审批事件、审批卡片和微信通知，主 Agent 与通用子 Agent 都继承该规则；读取、列举、`glob`、`grep` 不触发审批。技能审批没有“批准当前文件（10 分钟）”免审批租约，`FileApprovalStore.approval_file()` 只对 `/webdav/` 路径发放租约，因此每次技能写入都要单独确认；用户在 `/skills` 页面或在 `/workspace` 手动保存技能仍直接生效。Skill 可用 `<!-- BEGIN USER CONTRACT -->` / `<!-- END USER CONTRACT -->` 标出硬约束，`AgentFilesystemBackend` 会在 write/edit/upload/delete 入口逐字保护该区块及包含它的 Skill 文件/目录，正式契约只能由用户通过平台文件编辑入口修改。只有 `/skills/{skill_id}/SKILL.md` 会在下一次 Agent 执行开始时作为 skill metadata 被扫描。
- Agent 长期记忆固定开启。运行时通过 DeepAgent 原生 `memory=["/memories/AGENTS.md"]` 启用 `MemoryMiddleware` 加载和维护这一个长期记忆索引文件，不预创建或填充模板；文件不存在时 `MemoryMiddleware` 按空记忆处理，首次持久化时由 Agent 使用内置文件工具创建。其它 `/memories/...` 细节文件不自动注入，Agent 需要时可用内置文件工具自行查找和读取。本地共享知识通过原生文件工具访问 `/files/`，对应 `workspace/context/knowledge/files/`。
- 运行时默认通过 `WorkspaceMiddleware` 向主 Agent 和使用文件工具的 general-purpose 子 Agent 的每次模型请求追加工作区目录用途、权限、脚本/成果位置及容器路径区别；同步和异步均生效，不重复累积，不写聊天历史或人格配置。目录定义、初始化及 Agent 文件权限共用 `agent_workspace.py`，实际物理路径经统一入口解析，Agent ID 显式传入运行时。长期记忆和浏览器研究细则不重复注入。Agent 特定记忆由 DeepAgent 原生 `MemoryMiddleware` 的 memory guidelines 负责；用户笔记、同步文档与共享知识统一使用原生文件工具；浏览器的搜索、正文提取和失败恢复流程由 `browser_search`、`browser_extract` 工具 description 负责；私有虚拟文件系统的目录认知由 `WorkspaceMiddleware` 负责，权限强制执行由 `AgentFilesystemBackend` 负责。 启用 WebDAV 时逐项追加 `/webdav/` 下目录路径、用户说明（缺省为用户文档与共享知识库）、权限、该目录的审批语义（逐次 HITL 或免审批直写远端）、子目录优先规则和远端写回提示，主 Agent 与通用子 Agent 都接收；私有脚本、成果和记忆继续使用对应私有目录。
- 历史 `workspace/agents/{agent_id}/memory/store.json` 是旧版 DeepAgent store 遗留路径，不由运行时代码或迁移脚本自动处理。按用户偏好，旧 workspace 数据收敛直接在目标机器上做一次性文件操作；配置页只展示新版 `workspace/agents/{agent_id}/workspace/memories/`。
- 系统日志继续写入 `workspace/logs/platform-YYYY-MM-DD.log`。
- 维护清理服务读取 `maintenance.enabled`、`maintenance.interval_seconds`、`maintenance.retention_days` 和 `maintenance.dry_run`；默认每 86400 秒运行一次，统一清理超过 15 天的可清理运行数据，但不会删除 `workspace/sessions/active.json` 仍指向的当前会话。删除过期 session 时，同步删除 `workspace/sessions/{session_id}/checkpoints.sqlite` 里该 `thread_id` 的 checkpoint/writes 行。自动执行由统一 Scheduler 的内置 `maintenance_cleanup` 任务负责，状态和事件落在 `workspace/schedules/maintenance_cleanup/`，立即清理可使用系统 API 或 `/schedules` 的立即运行按钮。
- 生产更新锁文件固定写入 `workspace/logs/update-service.lock`。历史 `workspace/.run/` 已退役，不再保存微信登录态或更新锁；生产升级前必须把旧 `workspace/.run/wechat_session*.json` 移到 `workspace/channels/wechat/sessions/`，再删除空 `.run` 目录。

## Removed From Target Architecture

以下旧功能不再作为目标架构保留；删除对应代码时必须同步清理文档、测试、前端入口和配置模板：

- 旧 Agent Chat WebSocket 聊天。
- 旧 Harness 严格状态机产品路径。
- Prompt/Agent 双模式产品概念。
- 旧 Session CRUD 产品页和旧 WebSocket 聊天会话模型。
- 旧 Skill 管理和 Skill 作为产品级概念。
- Agent 每日冥想定时任务（`agent_meditation_{agent_id}`）与 `/meditations/` 工作区目录。
- Skill 自进化（`SkillImprovementMiddleware`）与 `/improvements/` 工作区目录。
- Portfolio 投资组合模块。
- Critique 多维批判模块。
- Proxy 嵌入站点模块。
- Agent command center 里围绕旧 Agent/Skill/Model 管理构建的复杂 UI。
- 多 Agent 群聊（`/chat-groups`、`ChatGroupService`、`group_decision` 控制工具、群成员库与群内部 session）。群聊权威状态目录、群 session 与 `run_group_*` Run 在目标机器上一次性清理，不保留运行时迁移或兼容分支。
- 架构层手写 LangGraph 图（`StateGraph` 节点与边）作为产品编排方式；例外是 deepagents 公开 API 无法表达时才自建图。LangChain 平台组件不作为架构面依赖，详见 `docs/architecture-qa.md`。

## Documentation Cleanup Rule

- 主架构入口只描述当前目标架构，不再保存已决定删除模块的完整行为说明。
- `docs/architecture-qa.md` 保存本轮架构问答和设计决策。
- 旧 `docs/superpowers/*` 计划/规格和旧多维批判设计图已经删除；后续删除旧代码模块时，必须同步删除或归档对应文档，避免搜索结果继续指向旧架构。
- 如果实现改变架构、行为、命令、依赖、配置、公共接口、运维方式或长期协作规则，必须同步更新本文件和相关说明。

## Operating Notes

- `CLAUDE.md` 是仓库级 Agent 指令入口。
- `config.example.yaml` 是 workspace 配置模板，不得放入真实密钥。
- 使用平台工具和运行时能力需要安装对应依赖：会话 checkpoint 依赖 `langgraph-checkpoint-sqlite`；`search_session` 中文关键词分词依赖 `jieba`；`browser_extract`/`browser_search` 依赖 `langchain-community`、`playwright`、`beautifulsoup4` 和 `lxml`；`arxiv` 依赖 `langchain-community` 和 `arxiv`；`yahoo_finance_news` 依赖 `langchain-community` 和 `yfinance`；`execute_code` 依赖生产机 Docker、已注册的 gVisor `runsc` runtime，以及已拉取的 `python:3.12-slim-bookworm` 或配置中的等价镜像。`run.sh dev/prod` 会在依赖安装后检查并执行 `python -m playwright install chromium` 准备浏览器二进制。
- 生产服务由用户级 systemd 管理（`~/.config/systemd/user/super-personal-platform.service`），unit 直接运行 `.venv/bin/python -m server`，单独 `systemctl --user restart` 不会安装新依赖；提交后远端部署必须在 pull 和 HEAD 校验之后执行 `.venv/bin/python -m pip install .`，并确保 Playwright Chromium 已安装，再重启服务。
- `config.yaml` 属于本地 workspace 数据，不提交。
- 开发启动使用 `./run-dev.sh` 或 `./run.sh dev`。
- 生产启动使用 `./run-prod.sh` 或 `./run.sh prod`。
- 服务单元安装在调用用户的 `~/.config/systemd/user/`，靠 `loginctl enable-linger` 实现开机自启；`run.sh prod` 在缺少 linger 时提示并执行一次，其余 `systemctl --user enable/restart/status` 都不需要 sudo。该机制取代了早期的系统级 unit 与受限 sudoers 规则；旧单元与 sudoers 规则在迁移时一次性人工清除，`run.sh` 不保留任何系统级单元的检测或迁移代码。
- `run.sh prod` 生成 systemd unit 时只使用系统临时文件，再安装到用户级 systemd 目录，不把临时 service 文件写入 workspace。
- 提交项目前必须执行 `.claude/skills/project-commit` 工作流。
- 需要按「需求 → 方案 → 实现 → 验证 → 发布」多角色协作时使用 `.claude/skills/omni-roles` 技能：需求文档落在 `docs/requirements/F<n>-<名称>.md`，角色规则、产物落点和确认闸口由该技能定义，不改变本文件的架构约定。

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
| `/browser/` | 浏览器登录态、缓存、占用锁 | 仅浏览器服务访问 |
| `/webdav/` | Agent 配置映射的坚果云共享文档，虚拟挂载 | 默认 write（逐次审批），可配置 read 或 write + `approval_required=false` 免审批；文本写回远端，挂载根不能删除 |

生产目录收敛须停止相关运行进程后直接执行一次性迁移，并核验文件清单和内容；同名冲突不覆盖，未知归属数据保留迁移备份。旧路径不参与运行时读取或回退。


### 文件入口收敛与 WebDAV HITL

- `/notes/` 已移除，不再初始化、不加入白名单、不注入提示词；临时笔记用 `/scratch/`，长期信息用 `/memories/`，成品用 `/artifacts/`。已有目录在目标机器一次性备份检查后移除，没有自动迁移。
- `search_context`、`write_context` 已删除，旧配置和历史 run 快照的工具 ID 在部署时一次性清理。原有本地知识无搬迁地挂载为 `/files/`，主/子 Agent 使用 ls/glob/grep/read_file/write_file/edit_file；本地文本写入无需审批，禁止越界、符号链接、删除和二进制上传。中文相关度排序及最近笔记的 Context 聚合查询不再提供；search_session 保留。
- WebDAV 可写目录生成 `FilesystemPermission(mode="interrupt", operations=["write"])`，只读目录仍 deny，读取 allow；`approval_required=false` 的可写目录改为 `mode="allow"`，写入直接落到缓存并写回远端，不进入 `waiting_approval`，也不产生审批租约；保留子目录优先。原生 HITL 在工具执行之前暂停，后端允许经批准的 interrupt 写入但仍检查范围。复用 DeepAgent 原生条件谓词，并限定 approve/reject；主/子 Agent 继承一致规则，无用户开关。没有 session_id 的需审批 WebDAV run 也使用 run_id + SQLite checkpoint 保存审批，确保能恢复；只有免审批目录的 run 不再为 WebDAV 审批额外建立 run 级 checkpoint。
- 原生文件写入先更新远端再更新缓存；远端编辑在共享 sync.lock 内读取最新内容。定时和手动 WebDAV 同步保持，删除及二进制上传仍不支持。
- Agent 弹窗显示全局坚果云来源及同步根，每项显示远端实际目录 → Agent 虚拟路径，以及三选一的访问权限（“读写 · 写入需审批”“读写 · 免审批”“只读”）；选中免审批时在目录卡片内提示写入会直接写回坚果云远端。Agent 列表摘要显示目录数量与其中免审批的数量。目录选择器显示远端当前位置；全局连接或同步关闭时显示挂载不可用。全局页面称为“WebDAV 文件同步”。


### 微信图片与附件发送

- Agent 授权 `send_attachment(file_path, kind="auto")` 后可将现有文件发回当前 run 固化的微信目标；不接受收件人、账号、URL 或宿主机路径。不生成图片。文件来源走同一个原生文件后端路径校验，支持 Agent 私有目录、`/files/` 和授权的 `/webdav/`；浏览器目录、越界及符号链接拒绝，WebDAV 读取不触发写入 HITL。
- `auto` 按内容标记识别 PNG/JPEG/GIF/WebP，按图片消息发送；其它格式作为普通文件。`kind=file` 可发送图片原文件，`kind=image` 要求支持的图片格式。单文件 1 字节至 20 MiB、每轮最多 10 个；工具返回 queued，不能宣称已经送达。无微信目标的 Web Chat/API run 返回可恢复错误；带微信投递目标的定时任务可使用同一工具。
- 每个成功排队的文件固化到 `runs/{run_id}/outgoing/{rerun_count}/`，index.json 保存文件名、来源虚拟路径、种类、大小和 SHA-256；同名同内容同种类去重，文件快照和索引原子替换，文件不会因源文件随后编辑而变化。明确重跑使用新的 generation，不发送旧一轮的附件；outgoing 随 run 保留期清理。
- RunDeliveryService 在成功 run 的最终文本之后逐个投递附件；失败/取消的 run 只发状态文本。delivery.json 的 delivered_parts 持久化每个成功部分，重试/服务重启跳过已确认成功部分；每部分稳定 client_id。网络响应不确定时仍可能重复，沿用至少一次投递语义；所有部分完成才标记 delivered，失败沿用退避及 dead_letter。
- ILinkClient 依据腾讯官方 openclaw-weixin 的 [upload](https://github.com/Tencent/openclaw-weixin/blob/main/src/cdn/upload.ts)、[CDN](https://github.com/Tencent/openclaw-weixin/blob/main/src/cdn/cdn-upload.ts) 和 [send](https://github.com/Tencent/openclaw-weixin/blob/main/src/messaging/send.ts) 实现：AES-128-ECB/PKCS7 加密，getuploadurl 申请上传位置（no_need_thumb），密文 POST 到 CDN，读取 x-encrypted-param，发送 IMAGE=2 或 FILE=4 item；AES key 使用官方 hex 文本的 base64 编码，文件 len 使用明文长度字符串。CDN 请求不携带 bot token，不跟随重定向。HTTP 200 中非零 ret/errcode 同样判定失败，避免误报发送成功。


## 流式审批 checkpoint 完整性

- 运行时收到主图或子图的 HITL interrupt 后必须继续消费 `astream` 至正常结束，等待 SQLite checkpoint 与 pending writes 保存完成，再将 Run 切为等待审批；不能在第一个 interrupt chunk 处提前返回或关闭 checkpointer。以 updates 事件收集本轮待审批中断，按 interrupt ID 合并，保留并行子图全部审批；忽略恢复时 values 快照中的旧审批。批准或拒绝后使用已保存的中断继续，不重新生成已审批的工具参数。

## Checkpoint 存储与切换

- RunService 按 session 定位 `sessions/{session_id}/checkpoints.sqlite`；无 session 的审批 Run 使用 `runs/{run_id}/checkpoints.sqlite`。thread_id 保持原 session ID 或 Run ID，同一会话持续复用文件，删除所属目录即释放数据库；不裁剪存活会话的历史快照。
- 公共旧库仅在停服窗口一次性按 thread_id 拆分，完整保留所有表数据、namespace、父链和 pending writes；备份、逐 thread 内容与数量核验、完整性检查通过后移出旧库再启动。未知归属保留备份、目标冲突不覆盖；不提供运行时迁移或旧路径回退。

## Chat 交互恢复

- Chat 事件轮询串行执行，按 seq 去重；完成、失败、取消均结束生成并释放输入，空事件时读取 Run 快照校正终态。终态详情读取失败保留重试。
- 发送未确认时保留原文和 client_message_id，提供原请求重试，确认前禁止新发送及切换会话；草稿可继续编辑。
- 审批提交错误由共享审批卡呈现；成功立即禁用旧审批，后续刷新失败不视为提交失败。新审批按请求内容重新呈现。
- 共享消息列表仅在接近底部时跟随输出，翻阅历史时保留位置并显示回到底部入口。

- Chat 共用正文下方的复制操作行，保留复制成功反馈；输入区统一为整体圆角容器，文字与发送按钮同排底部对齐，不为发送单独占一行；右侧 40px 圆形向上箭头发送。手机与桌面使用一致结构。

- 共享输入框按实际内容高度自动撑开，换行和宽度变化导致的折行均重新测量；最多显示约 20 行，超出后内部滚动，删除内容或发送清空后收回。
- Chat 长草稿使用细窄、圆角、深灰蓝原生滚动条，轨道透明，悬停或聚焦时适度提亮；保留拖动、滚轮、触控和键盘滚动。

## Chat 引用

- 共享消息操作区支持引用已保存的用户/助手文本，生成中或等待审批的消息不提供引用；输入区显示可取消的三行预览卡片，单次选一条，切换会话清除，保留草稿并聚焦。正文必须非空，成功后清除引用，失败保留，重试携带相同来源和消息 ID。
- Chat POST /api/chat/messages 接受 reply_to_seq；服务端只从当前会话读取原文，缺失目标拒绝。引用快照包含来源、发言人和完整文本，保存在消息与 Run metadata.reply。旧记录可缺省，无迁移。
- 引用在已发送消息上方恢复显示，复制仍只复制正文。Agent 收到明确标为对话材料的引用与本次正文。不回退或复制 checkpoint，不提供编辑和新会话分支。

- 支持单条已保存消息正文内选中文字，显示“引用所选内容”入口；跨消息、思考过程、引用卡片和审批内容不作为选区来源。发送接口可携带 reply_excerpt，服务端校验片段属于当前来源原文或其 Markdown 可见文字，保留来源标识，仅将选中片段保存和传入上下文。整条引用继续保留。

### Chat 终态恢复

- Chat 刷新或切换会话时，按当前已加载消息的 Run ID 分批读取任务快照（每批最多 8 个），包含只有用户消息的任务。失败和取消任务从已有 result/state 恢复原因与终态，并将状态消息放回对应用户消息之后；不依赖助手历史记录或 partial 是否存在，不写入 checkpoint 或伪造历史消息。

### 当前文件的 10 分钟审批授权

- Chat 和 Runs 共用审批卡，提供“仅批准本次”和“批准当前文件（10 分钟）”。仅当整批待审批调用均为同一个 WebDAV 文件的 write_file/edit_file 时显示文件授权；混合工具或多个文件仍逐批审批，避免误批其他目标。微信整条回复“批准当前文件”使用相同入口，原 approve/批准等仍只批准本次。
- `POST /api/runs/{run_id}/resume` 增加 `scope=once|file_10min`，默认 once；file_10min 仅允许 approve。后端从已保存的审批请求提取并验证精确文件路径和 WebDAV 写权限，不接受客户端指定文件、期限或目录授权。
- 文件授权按当前 session + Agent 隔离，存于 `sessions/{session_id}/file_approvals.json`，无 session 时存于 Run 目录；记录批准时间和固定 600 秒到期时间，审批 history 保存授权快照。刷新、重启和后续工具调用不会续期。
- 主/子 Agent 原生 HITL 条件在每次工具调用时检查持久化授权和实时到期时间；精确路径匹配，仅豁免该文件 write_file/edit_file 的重复确认，原生目录权限、只读限制和后端校验不变。到期自动恢复审批，不调整 checkpoint，不批量放行其他文件或工具。

### Chat 实时 Markdown 编辑

- Chat 页使用 `chatComponents.jsx` 的 ChatComposer，内部统一使用 `MarkdownComposer.jsx`（Tiptap/ProseMirror）。输入标题、列表、引用、加粗、斜体、行内代码和代码围栏时在原位呈现格式，不切换预览；支持撤销/重做、粘贴 Markdown 表格和任务列表。图片 Markdown 保留为文本，不在输入时自动加载远程图片。
- 编辑器文档管理光标、选区与中文输入法，React 草稿保存 Markdown 字符串；仅外部草稿替换时重新解析，正常输入不重建文档。序列化可能规范化空格、列表和表格排版，保持内容与格式语义；传给现有发送接口的仍是 Markdown 文本，无后端协议或 checkpoint 变更。
- Enter 发送，Shift+Enter 换行、列表续项或在代码块内换行；输入 ```language 后 Shift+Enter 建立代码块，代码块末行输入 ``` 后 Shift+Enter 退出。IME 确认候选不发送。引用聚焦、失败草稿恢复及原请求重试沿用现有流程。切换会话重建编辑器，避免遗留选区或格式。
- 输入框自然增高，普通正文约 20 行后内部滚动；格式块占用实际高度。保留细滚动条与右下发送按钮。长路径、表格及代码在窄屏内换行，不撑宽页面。
- 前端构建环境要求 Node.js 20+，依赖与锁文件一并提交；编辑器依赖拆成单独构建 chunk。生产继续使用已提交的 dist，不现场安装 Node 依赖。测试环境为 jsdom 补齐空布局 API，真实键盘、IME 防误发、滚动及桌面/手机布局用浏览器模拟 API 验证。


## Agent 默认系统提示词

- `server/domain/system_prompt.py` 统一维护完整默认规则，按 Hermes 式分节组织：身份段（分量匹配、去填充语、不旁白工具调用、独立判断）→ 按请求行事 → 任务完成 → 区分事实与推断 → 控制查询与追问 → 长期记忆 → 技能。`system_prompt` 缺省、空或纯空白使用默认；非空使用自定义内容，不叠加默认。DeepAgent 的权限、工作区、技能等 middleware 指令继续注入。记忆规则是模型行为约束，不是文件写入拦截。
- Agent 编辑允许留空，新建与配置示例默认空；通过认证接口 `GET /api/workspace/default-system-prompt` 获取 `{system_prompt: string}` 预览，前端不维护默认正文副本。
- 单聊创建 Run 时将实际正文固化为 `snapshot.agent.system_prompt`，另存 `system_prompt_source`。审批恢复、重试、重跑读取原快照，不重新解析默认值，不迁移旧 Run。
- `system_prompt(action=read)` 的 `system_prompt`/`length` 对应配置原文，`effective_system_prompt` 与 `source` 对应下一次 Run 的基础正文和来源，不包含 middleware 指令，也不代表当前 Run 的旧快照。`action=update` 必须显式提供字符串 `new_prompt`，空白字符串恢复默认；缺少/null 参数报错。更新仍逐次审批，拒绝不改文件，批准后下次 Run 生效。审批的新旧对照展示实际默认正文；配置缺少该字段时仅为目标 Agent 插入字段。
- 本次生产仅一次性清空 `default.system_prompt`，其余 Agent 与字段保持；旧会话历史不清除，效果验证使用新会话。需求见 F11。
