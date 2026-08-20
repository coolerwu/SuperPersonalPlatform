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
  3 次），确认远端 `git rev-parse HEAD` 等于本地刚 push 的 HEAD，再执行
  `sudo -n systemctl restart super-personal-platform.service`，最后用非 sudo
  `systemctl is-active/status super-personal-platform.service` 验证。不要把
  SSH/sudo 密码写入仓库文件或命令文本；运行时使用用户输入、已认证 SSH 会话或本地未提交环境变量。

如果实现改变了架构、行为、命令、依赖、配置、公共接口或运维方式，必须同步更新 `docs/project-architecture.md`。

如果实现改变了 Agent 工作约定、提交流程、技能使用方式或长期协作规则，必须同步更新 `AGENTS.md`、对应 `.codex/skills/*/SKILL.md`，以及受影响的说明文件后，才能进入提交步骤。

提交项目前必须执行项目内 `$project-commit` skill。

## 前端设计约定

- 整体前端默认对齐主流 Hermes / OpenClaw 类 Agent command center 风格：深色运行台、清晰导航、工具/任务/日志/终端面板、明确状态标识和低噪声操作流。不只是调整颜色和间距，而要从信息组织、页面结构、操作入口和状态呈现上统一设计；避免临时后台式卡片堆砌。
- 菜单页右侧内容区不要添加重复的全局页面标题栏、面包屑式说明或“图标 + 页面名 + 描述”的 header，例如“运行概览 / 首页”这类块不需要。
- 侧栏已经表达当前位置；右侧内容应直接展示当前功能的实际工作区。只在功能内部保留必要的局部工具条、tab、状态栏或表单分组。

## 本轮重构易错点

- “session”一词要区分清楚：`workspace/channels/wechat/sessions/{account_id}.json` 只表示微信登录态；长期聊天历史统一放在 `workspace/sessions/{session_id}/`，run 只引用 `session_id`。
- `/workspace` 是原生文件浏览和文本编辑，不承载 `config.yaml` 的可视化表单；配置可视化只放在 `/config` 主菜单下的基础配置、Providers、Agents 栏目。
- Runs 页面通过 1 分钟轮询读取落盘状态；轮询更新必须保留当前详情快照，只在内容实际变化时替换，避免短暂重刷、`unknown` 闪动或结果预览丢失。
- React 表单列表不要用会随输入变化的字段作为 key，例如 Provider/Agent 的 `id`；否则输入一个字符会 remount 并丢焦点。
- 生产环境使用已提交的 `web/dist`，前端改动需要执行 `cd web && npm run build` 并提交新的 dist 产物。
- 后端命名要区分配置领域和运行时封装：`server/domain/agent_config.py` 只能放 Agent/LLM/DeepAgent 选项配置对象和校验；真正调用或封装 `deepagents`、LangChain 模型的代码只能放在 `server/infrastructure/deepagent_runtime.py` 或同层 infrastructure 模块。
- `DeepAgentRuntime.run()` 的入参必须保持清晰：`instructions` 是 system prompt，`messages` 是完整会话消息，`options` 是结构化运行选项；不要重新引入 `user_message`、`max_iterations`、`deepagent_options` 这种和 `messages/options` 重复的散参数。
