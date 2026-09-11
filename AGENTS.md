# 项目 Agent 指令

默认使用中文回复用户。

`docs/project-architecture.md` 是本项目的架构说明、长期记忆和运行约定索引。

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
  `sudo -n systemctl restart super-personal-platform.service`，最后用非 sudo
  `systemctl is-active/status super-personal-platform.service` 验证。不要把 SSH/sudo
  密码写入仓库文件或命令文本；运行时使用用户输入、已认证 SSH 会话或本地未提交环境变量。

如果实现改变了架构、行为、命令、依赖、配置、公共接口或运维方式，必须同步更新 `docs/project-architecture.md`。

如果实现改变了 Agent 工作约定、提交流程、技能使用方式或长期协作规则，必须同步更新 `AGENTS.md`、对应 `.codex/skills/*/SKILL.md`，以及受影响的说明文件后，才能进入提交步骤。

提交项目前必须执行项目内 `$project-commit` skill。

用户当前偏好：workspace 内已有数据的一次性整理或历史目录收敛，不写运行时代码、迁移脚本或自动迁移逻辑；直接在目标机器上执行受控的一次性文件操作，并在执行前后做非敏感状态检查。

## 前端设计约定

- 整体前端默认对齐主流 Hermes / OpenClaw 类 Agent command center 风格：深色运行台、清晰导航、工具/任务/日志/终端面板、明确状态标识和低噪声操作流。不只是调整颜色和间距，而要从信息组织、页面结构、操作入口和状态呈现上统一设计；避免临时后台式卡片堆砌。
- 菜单页右侧内容区不要添加重复的全局页面标题栏、面包屑式说明或“图标 + 页面名 + 描述”的 header，例如“运行概览 / 首页”这类块不需要。
- 侧栏已经表达当前位置；右侧内容应直接展示当前功能的实际工作区。只在功能内部保留必要的局部工具条、tab、状态栏或表单分组。

## 本轮重构易错点

- “session”一词要区分清楚：`workspace/channels/wechat/sessions/{account_id}.json` 只表示微信登录态；长期聊天历史统一放在 `workspace/sessions/{session_id}/`，run 只引用 `session_id`。`sessions/active.json` 的多个渠道 binding 可以指向同一个长期 session；Web Chat 选择微信 session 时只能更新 Web binding，不能改写该 session 原始的渠道、账号和 peer 身份。
- `/workspace` 是原生文件浏览和文本编辑，不承载 `config.yaml` 的可视化表单；配置可视化只放在 `/config` 主菜单下的基础配置、Providers、Agents 栏目。
- Runs 页面通过 1 分钟轮询读取落盘状态；轮询更新必须保留当前详情快照，只在内容实际变化时替换，避免短暂重刷、`unknown` 闪动或结果预览丢失。
- React 表单列表不要用会随输入变化的字段作为 key，例如 Provider/Agent 的 `id`；否则输入一个字符会 remount 并丢焦点。
- 生产环境使用已提交的 `web/dist`，前端改动需要执行 `cd web && npm run build` 并提交新的 dist 产物。
- 后端命名要区分配置领域和运行时封装：`server/domain/agent_config.py` 只能放 Agent/LLM/DeepAgent 选项配置对象和校验；真正调用或封装 `deepagents`、LangChain 模型的代码只能放在 `server/infrastructure/deepagent_runtime.py` 或同层 infrastructure 模块。
- `DeepAgentRuntime.run()` 的入参必须保持清晰：`instructions` 是 system prompt，`messages` 是完整会话消息，`options` 是结构化运行选项；不要重新引入 `user_message`、`max_iterations`、`deepagent_options` 这种和 `messages/options` 重复的散参数。
- Agent 的 Checkpointer、长期记忆和私有 filesystem 固定开启，不提供配置开关。带 `session_id` 的 run 只传当前 run 消息，由 LangGraph checkpoint 恢复状态；审批工具由系统工具注册表的 `approval_required` 决定，不读取 Agent 自定义 `interrupt_on`。

## Agent Workspace 约定

- Agent 私有工作数据统一放在 `workspace/agents/{agent_id}/workspace/`，`agent.json` 留在 Agent 目录外层；Agent 文件工具的 `/` 就是私有 workspace，虚拟路径不再嵌套 `/workspace/`。
- 路径和目录权限统一由 `server/infrastructure/agent_workspace.py` 定义。默认 `WorkspaceMiddleware` 给主 Agent 和文件工具子 Agent 解释目录用途，后端实际执行权限；浏览器登录态只能通过浏览器服务访问。
- 浏览器直接使用私有 `browser/`；代码脚本保留到 `scratch/`，成果放 `artifacts/`，不保留长期 `code_runs`、input/work/output 或额外执行元数据目录。
- 目录重构直接迁移目标机器数据，不加入兼容路径或自动迁移；停止相关进程、核验迁移前后清单再启用新代码，冲突不覆盖。

- WebDAV 全局只管理连接与同步；映射目录、read/write 权限（默认 write）和用途说明放在 Agent 的 `webdav` 配置中。使用 CompositeBackend 虚拟映射 `/webdav/`，不使用软链接；原生文件工具和 Context 工具共用工作区路径授权。WorkspaceMiddleware 同时向主 Agent 和文件工具子 Agent 说明目录用途、权限和远端写回行为。

- WebDAV 每个 Agent 配置 `enabled` 与 `directories[]`，每项独立 path/permission/description；原目录层级保留在 `/webdav/` 下。使用 DeepAgent 原生 FilesystemPermission，子目录规则自动优先，未选择目录拒绝；Context 和文件后端共用原生匹配器，祖先只用于导航。
