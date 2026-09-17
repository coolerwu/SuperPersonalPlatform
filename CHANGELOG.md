# Changelog

本文件记录项目对用户可见或对运维有影响的变化。格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号规则与发布、
回滚流程见 `docs/release.md`。

当前开发线尚未打 tag；`pyproject.toml` 的版本号是开发线编号，不是已发布版本。

## [Unreleased]

### Added

- `./run.sh rollback <tag|branch|commit>`：把生产检出切到指定版本后走同一套依赖安装与
  重启流程；再次执行 `./run-prod.sh` 会自动回到生产分支。
- `./run.sh prod --no-pull`：跳过远端拉取，用于回滚、离线重装和固定提交部署。
- `server/tests/test_api_contract.py`：双向核对 `docs/project-architecture.md` 的
  `/api` 路径与 FastAPI 实际注册路由，防止接口与文档漂移。
- `server/tests/test_run_worker_service.py`：固化 Run worker 的单并发语义和崩溃后继续
  领取任务的契约。
- `docs/requirements/`：需求文档编号与分档约定。
- `docs/release.md`、本 `CHANGELOG.md`：版本号、发布与回滚约定。
- 群聊列表新增“重命名群聊”和“删除群聊”入口，对应
  `POST /api/chat-groups/{group_id}/rename` 与 `DELETE /api/chat-groups/{group_id}`；
  删除在群仍在协作中时返回 409，重命名在协作中也可用。
- 群聊成员库：可复用的群成员预设（群内名称 + 基础 Agent + 附加 prompt）存于
  `workspace/chat_groups/members.json`，通过 `GET/POST /api/chat-groups/members` 与
  `DELETE /api/chat-groups/members/{member_id}` 管理；新建/编辑群时可一键选入，
  也可把现有成员存回成员库。预设是编辑期模板，不影响已建群。
- 系统提示词对话内读写：新增始终注入的平台工具 `system_prompt`，`action=read`
  免审批返回平台从 `config.yaml` 读到的该 Agent `system_prompt` 原文，`action=update`
  提交完整新提示词后必须经人工审批才写入，只替换目标字段并在下一次 Run 生效；
  Web 审批卡片展示新旧对照，微信审批通知给截断预览并指向 Web 端，群聊内部 Run
  不提供该工具。需求见 `docs/requirements/F1-系统提示词对话内编辑.md`。
- Skills 页面：侧栏新增 `/skills` 技能库，按 Agent 分组列出各自私有工作区里的
  `skills/{skill_id}/SKILL.md`，默认选中第一个 Agent 并只展开它的技能，支持查看、新建、
  编辑、删除整个技能目录，并提示用户契约区块；为此 `PUT /api/workspace/write` 新增
  可选 `create` 语义用于创建新文件。技能状态按 SkillsMiddleware 的真实口径分级：
  缺 frontmatter/name/description 或 SKILL.md 缺失标记“不会加载”并禁用保存，
  name 与目录名不一致、name 不符合规范、description 超过 1024 字符只标记“规格警告”
  （运行时仍会加载，description 会被截断）并允许保存。需求见
  `docs/requirements/F2-Skill页面.md`。

### Changed

- Chat 页面改为与群聊一致的左侧列表布局：左栏是当前 Agent 的 session 列表（新建会话 +
  Agent 选择 + 来源/渠道身份/消息数/更新时间 + 删除），中间是消息与输入区，右侧保留状态栏；
  原来的会话下拉菜单移除，`820px` 以下会话列表变成顶部横向滚动的会话条。
- 工作目录页面的约定目录说明与当前工作区契约对齐，移除已删除的
  `search_context` / `write_context` 描述，补上 `sessions/active.json`、群聊状态和
  Agent 私有工作区。
- `docs/project-architecture.md` 补齐认证 API 与日志、生产更新接口的路径清单。

### Fixed

- 登录 cookie 由会话 cookie 改为 30 天持久化 cookie，手机端浏览器、内置 WebView
  和添加到主屏幕的应用不再每次打开都要求重新输入访问 token；登出仍立即失效。

## [0.1.0] - 开发线

当前生产代码的基线。此前累积的主要能力：

- DeepAgent/LangGraph 后端执行、落盘 Run 队列、Run 状态/事件/结果与审批恢复。
- Web 单聊与多 Agent 群聊，共用消息、Markdown、思考过程、审批与输入组件。
- 微信个人号接入：文本/引用/图片输入、会话轮换、附件投递与投递重试。
- 坚果云 WebDAV 同步、按 Agent 的目录映射与原生 HITL 写入审批。
- Agent 私有工作区、长期记忆、Skills，以及浏览器、调度、代码执行、会话检索等平台工具。
- 定时任务、维护清理、用量与成本估算、浏览器授权、生产更新页面。
