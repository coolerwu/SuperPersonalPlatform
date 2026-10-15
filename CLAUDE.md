# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

默认使用中文回复用户。

`docs/project-architecture.md` 是本项目的架构说明、长期记忆和运行约定索引。

## 实现变更前的准备工作

开始实现任何变更前：

- 读取 `docs/project-architecture.md`。
- 执行 `git status --short`，确认当前未提交代码状态。
- 按用户当前偏好，本项目提交流程会提交所有未提交代码。
- 按用户当前偏好，提交并 push 成功后，需要重启生产：通过
  `ssh qiuqiu@192.168.1.3` 进入远端 `SuperPersonalPlatform/`，使用
  `git -c http.version=HTTP/1.1 pull` 拉取 `main`（瞬时网络/TLS 失败最多重试
  3 次），确认远端 `git rev-parse HEAD` 等于本地刚 push 的 HEAD；随后同步生产
  `.venv` 依赖：执行 `.venv/bin/python -m pip install .`，并执行
  `PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright} .venv/bin/python -m playwright install chromium`
  确保浏览器工具运行时可用；再执行
  `systemctl --user restart super-personal-platform.service`，最后用
  `systemctl --user is-active/status super-personal-platform.service` 验证。服务单元装在
  调用用户的用户级 systemd 目录，重启、状态与日志都不需要 sudo。不要把 SSH/sudo
  密码写入仓库文件或命令文本；运行时使用用户输入、已认证 SSH 会话或本地未提交环境变量。

## 文档同步与需求

如果实现改变了架构、行为、命令、依赖、配置、公共接口或运维方式，必须同步更新 `docs/project-architecture.md`。

如果实现改变了 Agent 工作约定、提交流程、技能使用方式或长期协作规则，必须同步更新本文件、对应 `.claude/skills/*/SKILL.md`，以及受影响的说明文件后，才能进入提交步骤。

需求文档统一放在 `docs/requirements/`，命名 `F<n>-<名称>.md`，编号规则与模板见 `docs/requirements/README.md`。没有需求文档覆盖的改动不进入实现。

提交项目前必须执行项目内 `$project-commit` skill。

用户当前偏好：workspace 内已有数据的一次性整理或历史目录收敛，不写运行时代码、迁移脚本或自动迁移逻辑；直接在目标机器上执行受控的一次性文件操作，并在执行前后做非敏感状态检查。

## 版本、发布与回滚

版本、发布与回滚约定见 `docs/release.md`，变更记录写在 `CHANGELOG.md`。需要把生产退回旧版本时，在远端 `SuperPersonalPlatform/` 执行 `./run.sh rollback <tag|branch|commit>`（先确认工作区干净，脚本会切到目标版本、安装该版本依赖并重启服务）；再次执行 `./run-prod.sh` 即可回到生产分支。不要在仓库或命令文本里写入 SSH/sudo 密码。

## 多角色协作（omni-roles skill）

- 需要按「需求 → 方案 → 实现 → 验证 → 发布」多角色推进时，使用 `.claude/skills/omni-roles`：用户用 `$omni-roles` 调用，或在消息里用 `@req` / `@arch` / `@dev` / `@qa` / `@ops` 指定角色；该技能默认不隐式触发，普通问答和小改动不进入角色流程。
- 每条回复第一行带当前角色标记；角色在需求确认、方案确认、提交与发布三个闸口停下等用户拍板，不得自动跳过。
- 需求文档落在 `docs/requirements/F<n>-<名称>.md`。该技能不覆盖本文件的铁律，提交仍必须执行 `$project-commit`。
- 该技能不派发子 agent，架构 Review 与 qa 验证属于同一会话内的复核，结论必须如实标注独立性有限。

## 前端设计约定

- 整体前端默认对齐主流 Hermes / OpenClaw 类 Agent command center 风格：深色运行台、清晰导航、工具/任务/日志/终端面板、明确状态标识和低噪声操作流。不只是调整颜色和间距，而要从信息组织、页面结构、操作入口和状态呈现上统一设计；避免临时后台式卡片堆砌。
- 菜单页右侧内容区不要添加重复的全局页面标题栏、面包屑式说明或"图标 + 页面名 + 描述"的 header，例如"运行概览 / 首页"这类块不需要。
- 侧栏已经表达当前位置；右侧内容应直接展示当前功能的实际工作区。只在功能内部保留必要的局部工具条、tab、状态栏或表单分组。
- Workbench pages use 100% width with `100dvh` fallbacks
- Production uses committed `web/dist/` build output; run scripts do not build frontend assets

## 本轮重构易错点

- “session”一词要区分清楚：`workspace/channels/wechat/sessions/{account_id}.json` 只表示微信登录态；长期聊天历史统一放在 `workspace/sessions/{session_id}/`，run 只引用 `session_id`。`sessions/active.json` 的多个渠道 binding 可以指向同一个长期 session；Web Chat 选择微信 session 时只能更新 Web binding，不能改写该 session 原始的渠道、账号和 peer 身份。
- 同一长期 session 同时只能有一个 queued/running/waiting_approval Run；创建第二个 Run 必须在写附件、消息和 Run 目录前拒绝。微信等待审批时普通消息只提示先回复 `approve` 或 `reject`，避免覆盖 LangGraph checkpoint。
- `/workspace` 是原生文件浏览和文本编辑，不承载 `config.yaml` 的可视化表单；配置可视化只放在 `/config` 主菜单下的基础配置、Providers、Agents 栏目。
- Runs 页面通过 1 分钟轮询读取落盘状态；轮询更新必须保留当前详情快照，只在内容实际变化时替换，避免短暂重刷、`unknown` 闪动或结果预览丢失。
- 思考过程只聚合工具轨迹：`agent_update` 有工具调用记 `调用工具：<名称>`、工具返回取正文，模型正文与中间件钩子节点名只留在 `events.jsonl`；后端 `_thinking_text`/`_merge_thinking` 与前端 `runEventThinkingText`/`mergeChatThinking` 必须同口径，条数上限同为 20（`THINKING_SNAPSHOT_LIMIT` / `CHAT_THINKING_LIMIT`），任一侧单独改动都会让刷新前后的思考过程不一致。
- React 表单列表不要用会随输入变化的字段作为 key，例如 Provider/Agent 的 `id`；否则输入一个字符会 remount 并丢焦点。
- 前端开发、测试和构建使用 Node.js 20+（Markdown 编辑器依赖要求）。
- 生产环境使用已提交的 `web/dist`，前端改动需要执行 `cd web && npm run build` 并提交新的 dist 产物。
- 后端命名要区分配置领域和运行时封装：`server/domain/agent_config.py` 只能放 Agent/LLM/DeepAgent 选项配置对象和校验；真正调用或封装 `deepagents` 及其模型适配器的代码只能放在 `server/infrastructure/deepagent_runtime.py` 或同层 infrastructure 模块。
- `DeepAgentRuntime.run()` 的入参必须保持清晰：`instructions` 是 system prompt，`messages` 是完整会话消息，`options` 是结构化运行选项；不要重新引入 `user_message`、`max_iterations`、`deepagent_options` 这种和 `messages/options` 重复的散参数。
- Agent 的 Checkpointer、长期记忆和私有 filesystem 固定开启，不提供配置开关。带 `session_id` 的 run 只传当前 run 消息，由 LangGraph checkpoint 恢复状态；WebDAV 写入默认由原生 FilesystemPermission 的 interrupt 规则触发审批，只有显式配置 `approval_required=false` 的目录才免审批；其它平台工具审批由系统注册表的 `approval_required` 决定，不读取 Agent 自定义 `interrupt_on`。
- Checkpoint 按 session 存储于 `sessions/{session_id}/checkpoints.sqlite`；无 session 的审批 Run 存于自身 Run 目录。旧公共库需停服备份后按 thread_id 一次性拆分并核验，不加入自动迁移。
- WebDAV 文件审批可由用户显式选择“批准当前文件（10 分钟）”：仅当前 session + Agent 的精确文件写入免重复确认，持久化绝对到期时间；主/子 Agent 每次调用实时检查。原生权限和只读限制不变，禁止扩为目录、跨会话或永久授权，普通 approve 仍仅批准本次。发布前验证到期、重启恢复、不同文件/Agent/session 隔离及子 Agent checkpoint 恢复。

## Agent Workspace 约定

- Agent 私有工作数据统一放在 `workspace/agents/{agent_id}/workspace/`，`agent.json` 留在 Agent 目录外层；Agent 文件工具的 `/` 就是私有 workspace，虚拟路径不再嵌套 `/workspace/`。
- 路径和目录权限统一由 `server/infrastructure/agent_workspace.py` 定义。默认 `WorkspaceMiddleware` 给主 Agent 和文件工具子 Agent 解释目录用途，后端实际执行权限；浏览器登录态只能通过浏览器服务访问。
- 浏览器直接使用私有 `browser/`；代码脚本保留到 `scratch/`，成果放 `artifacts/`，不保留长期 `code_runs`、input/work/output 或额外执行元数据目录。
- 目录重构直接迁移目标机器数据，不加入兼容路径或自动迁移；停止相关进程、核验迁移前后清单再启用新代码，冲突不覆盖。

- WebDAV 全局只管理连接与同步；映射目录、read/write 权限（默认 write）、写入是否需审批（`approval_required`，默认 true）和用途说明放在 Agent 的 `webdav` 配置中。使用 CompositeBackend 虚拟映射 `/webdav/`，不使用软链接；文档统一通过原生文件工具访问。WorkspaceMiddleware 同时向主 Agent 和文件工具子 Agent 说明目录用途、权限、审批语义和远端写回行为。

- WebDAV 每个 Agent 配置 `enabled` 与 `directories[]`，每项独立 path/permission/approval_required/description；原目录层级保留在 `/webdav/` 下。使用 DeepAgent 原生 FilesystemPermission，子目录规则自动优先，未选择目录拒绝；文件后端使用原生匹配器，祖先只用于导航。

- `/notes/` 已删除；临时笔记用 `/scratch/`，长期信息用 `/memories/`，成品用 `/artifacts/`。本地共享知识挂载 `/files/`，不再提供 search_context/write_context。WebDAV 写入默认需 HITL 审批，主/子 Agent 一致；只有目录显式配置 `approval_required=false` 时才免审批直接写回远端，不扩散为目录级、跨会话或永久授权。没有 session_id 的需审批 WebDAV run 也必须保留审批 checkpoint。

- 微信图片/附件通过 send_attachment 显式选择已有文件，工具仅排队并返回 queued，成功 run 结束后由 RunDeliveryService 发送到固化的当前微信目标。不要让 Agent 自选收件人或把排队说成已发送。每个发送部分持久化进度和稳定 client_id；重试只处理未确认部分，重跑不复用旧轮次附件。
- Web Chat 支持上传图片作为多模态输入：输入区左侧“+”菜单是附件类动作的统一入口（当前只有“图片”，后续加文件/截图等只往菜单里加条目，发送按钮固定在最右侧）；页面通过 `POST /api/chat/messages` 的 `attachments[]` 提交 `data_url`，后端在创建 run 前校验数量（最多 6 张）、大小（单张 20MB）和字节头（PNG/JPEG/GIF/WebP），再落到 `workspace/sessions/{session_id}/attachments/{message_seq}/`；纯图片消息允许正文为空，粘贴的剪贴板图片只作为待发送附件、不插入草稿。历史图片只通过 `GET /api/chat/sessions/{session_id}/attachments` 读取已记录的附件路径，随会话删除或维护清理一起回收；Provider 不支持图片时沿用 `image_attachments_textified` 文本降级。

- 不再有 Skill 自进化：运行时不注入 SkillImprovement middleware，Agent 不会主动创建或改写 `/skills/`，也不会写 `/improvements/`（该目录已从工作区契约移除）；但用户在对话里明确要求时，Agent 仍可用原生文件工具创建或更新 `/skills/{skill_id}/SKILL.md`，新 skill 在下一次 Agent 执行开始时生效，历史 improvement 记录不再作为判断依据。Agent 对 `/skills` 与 `/skills/**` 的写入、编辑和删除都必须经过 HITL 审批：`skill_write_permissions()` 给 `create_deep_agent` 追加 write `interrupt` 规则，run 进入 `waiting_approval`，批准后才落盘，拒绝后技能文件不变；读取、列举和 `glob`/`grep` 不触发审批，技能审批也没有“批准当前文件（10 分钟）”免审批租约。用户也可以直接通过平台 `/skills` 技能库页面按 Agent 管理技能（新建/编辑/删除 `SKILL.md`，保存前校验 frontmatter 的 name 与目录名一致、description 必填），平台侧保存不需要审批；用户硬约束可放在 `<!-- BEGIN USER CONTRACT -->` / `<!-- END USER CONTRACT -->` 中，Agent 文件工具不得修改、覆盖或删除该区块及其所在 Skill，正式契约只能由用户通过 `/skills` 或工作目录等平台入口调整。
- 系统提示词只能通过始终注入的平台工具 `system_prompt` 在对话内读取或修改：`action=read` 免审批返回平台从 `config.yaml` 读到的该 Agent `system_prompt` 原文（Agent 文件工具读不到 config.yaml），`action=update` 必须逐次 HITL 审批，且只替换当前 Run 所属 Agent 的该字段，其它字段、注释与排版不变，下一次 Run 生效，当前 Run 继续用快照；通用子 Agent 不继承，审批卡片展示新旧对照，微信审批通知只给截断预览并提示到 Web 端查看完整对照。

## 默认系统提示词约定

- Agent `system_prompt` 缺省或空白使用后端统一完整默认规则，非空自定义替代默认；前端只从认证接口读取预览。默认规则按 Hermes 式分节组织：身份段 → 按请求行事 → 任务完成 → 区分事实与推断 → 控制查询与追问 → 长期记忆 → 技能。普通解释、会议材料与临时分析默认不主动写记忆，这只是提示词约束，不是新权限规则。
- 实际正文在 Run 创建时固化；审批恢复和重试不得重新解析默认值。系统提示词工具读取区分配置原文、下一次 Run 实际正文与来源；显式空字符串更新表示恢复默认，缺参数报错，仍逐次 HITL。

## Development Commands

```bash
# Development server (port 8888)
./run-dev.sh

# Production deployment (Linux systemd required)
./run-prod.sh

# Run backend tests
.venv/bin/python -m pytest

# Run single test
.venv/bin/python -m pytest server/tests/test_file.py::test_name -v

# Frontend tests and build
cd web && npm test
cd web && npm run build
```

## Architecture Overview

This is a full-stack personal platform with FastAPI backend (port 8888) and React+Vite frontend. The backend follows COLA architecture:

- `server/adapter/` - FastAPI routes, HTTP DTOs, static file serving
- `server/app/` - Application services and use-case orchestration
- `server/domain/` - Framework-free domain models (no FastAPI, httpx, or filesystem imports)
- `server/infrastructure/` - Config loading, HTTP clients, session implementation, app factory

Agent Chat 通过 `deepagents` 公开 API 执行（底层为 LangGraph，属实现细节）；模型经 LangChain 适配器接入，当前生产模型是 OpenAI 兼容网关上的 DeepSeek。

## Workspace and Configuration

The workspace (default: `.super-personal-platform/`) holds:
- `config.yaml` - Auth token, LLM models, agent definitions, proxy settings
- `logs/` - Unified platform logs (3-day retention)
- `.run/` - Runtime locks and generated service files

Key config sections:
- `auth.token` - Single-token login
- `llm.models` - OpenAI-compatible model configs (base_url, api_key, model, supports_images)
- `agents.definitions` - Agent personalities with system_prompt, model_id, skill_ids, tools
- `tools.profile/allow/deny` - Tool exposure control

Changing `auth.token` takes effect immediately; existing cookies with old token are invalidated.

## Agent Chat System

- Agents are configured in workspace `config.yaml`
- Skills live in `skills/common/{skill}/SKILL.md` or `skills/agents/{agent_id}/{skill}/SKILL.md`
- Skill IDs: `common:{stem}` or `private:{stem}`
- WebSocket at `/api/agents/chat/connect` for chat
- Per-message image support for models with `supports_images: true`

## Commit Workflow

Before committing changes, execute the local `$project-commit` skill which:
1. Reads `CLAUDE.md` and `docs/project-architecture.md`
2. Runs `git status --short`
3. Updates documentation if behavior/architecture changed
4. Runs tests: `.venv/bin/python -m pytest`, `cd web && npm test`, `cd web && npm run build`
5. Stages all with `git add .`
6. Commits and pushes
