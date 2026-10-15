# Intent

- 状态：现行
- 最后更新：2026-10-15
- 编号水位：F17

本文件是本项目需求的唯一来源：要做什么、为什么做、做到什么算完成。`docs/project-architecture.md` 描述系统实际怎么运行，两者分工不变 —— 需求变了先改这里，实现落地后再同步架构文档。

## 怎么用

- **新需求**：在「需求记录」末尾追加一节，标题 `### F<n> <名称>`。`<n>` 取本文件已有最大编号 +1，不复用已删除的编号；名称用简短中文，不带空格和斜杠。
- **大需求**（新功能、跨模块改动，或涉及数据模型、接口、口径、迁移）：写文档头（状态、日期、范围、前置依赖）+ 背景与目标 / 用户故事 / 功能点清单 / 验收标准 / 约束边界 / 非目标 / 开放问题。
- **小需求**（文案、单点微调、不改变口径的小修复）：写需求一句话 / 改动点 / 验收标准 / 边界不做的事 / 开放问题（可选）。
- **口径变更**：先回写对应小节，再继续方案或实现。
- **没有本文件覆盖的改动不进入实现**，再小也一样。
- 小节只写意图、场景、边界和成功标准，不在里面定技术路线或写实现代码。

## 方案与提交

- 方案先定后做：用 Claude Code 原生 plan mode 给出计划再动手。涉及新依赖、破坏性接口变更、数据迁移或 schema 变更时，必须在动手前单独列出并等确认。
- 提交前先逐条列出将要执行的动作（测试、前端构建、`git add .`、commit、push、远端 pull 与 HEAD 核对、依赖与浏览器安装、重启、服务状态验证）再执行，然后走 `.claude/skills/project-commit`。
- 同会话复核（架构 Review、验证）独立性有限，结论要如实标注。

## 现行意图

- **F17 行情工作台**：已实现、已提交并发布（`7dd44c4`）。见下方 F17；当前状态以该小节的文档头为准。

## 与其他文档的关系

- `docs/project-architecture.md`：架构、运行约定与长期记忆索引。
- `docs/architecture-qa.md`：DeepAgent 重构的技术决策问答。
- `docs/release.md`：版本、发布与回滚流程。
- `docs/design/`：设计稿（当前为 F17 行情工作台的页面设计稿 HTML）。
- `CHANGELOG.md`：版本变更记录。

## 需求记录

### F1 系统提示词对话内读写

- 状态：已确认，实现中
- 日期：2026-09-16
- 范围：单聊（Web / 微信）和定时任务 Run 的 Agent 系统提示词读取与修改；不改动 Web 配置页编辑行为
- 前置依赖：现有平台工具注册表与 DeepAgent 原生 HITL 审批链路、`workspace/config.yaml`

#### 背景与目标

Agent 的人格由 `config.yaml` 的 `agents.definitions[].system_prompt` 决定。用户只能在
`/config` 或 `/workspace` 手工编辑这个字段，在对话里发现“回答风格不对”时必须离开对话去改配置。
目标是在对话内也能读取和修改本 Agent 的系统提示词，同时保证任何写入都必须经用户逐次人工确认。

#### 用户故事

- 作为用户，我在 Web 或微信对话里说“把系统提示词改成……”，Agent 能提交一个待确认的修改，
  我确认后才真正写入配置。
- 作为用户，我问“你的系统提示词是什么”，Agent 能给出 `config.yaml` 里的原文，而不是靠
  推测拼凑（Agent 的文件工具读不到 `config.yaml`）。
- 作为用户，我在确认前能看到“当前提示词”和“将要改成什么”，不会被要求盲批。
- 作为用户，我拒绝之后配置完全不变，Agent 知道被拒绝并继续当前任务。

#### 功能点清单

1. 新增始终可用的平台工具 `system_prompt`，提供 `action=read` 与 `action=update` 两个动作。
2. `action=read` 由平台直接读 `config.yaml`，返回该 Agent 的 `system_prompt` 原文与长度，不进审批。
3. `action=update` 入参是完整的新提示词文本和可选理由，进入 `waiting_approval`，只允许
   `approve` / `reject`；缺省或非法 `action` 按写入对待，同样走审批。
4. 审批前展示当前提示词与拟修改内容的对照；微信审批通知给出截断预览并指到 Web 端看完整对照。
5. 审批通过后只替换该 Agent 的 `system_prompt`，其它配置字段、注释与排版保持不变，且整体配置仍通过校验。
6. 修改对下一次 Run 生效，当前 Run 继续使用创建时的快照。
7. 只能读取或修改当前 Run 所属 Agent；群聊内部 Run 不提供该能力。

#### 验收标准

- 在对话里询问系统提示词时，Agent 调用 `action=read` 得到 `config.yaml` 中的原文，不产生审批卡片。
- Web 对话中要求改人格时，审批卡片显示新旧对照，批准后 `config.yaml` 中该 Agent 的
  `system_prompt` 变为新文本，下一次 Run 使用新提示词。
- 微信对话中要求改人格时，审批通知包含新提示词预览与“到 Web 端查看完整对照”的提示，
  回复 `approve` 后同样写入。
- 拒绝、停止或配置校验失败时文件字节不变，没有临时文件残留。
- 空文本、超过 20000 字符、非法 `action`、目标 Agent 不存在、配置文件损坏都会得到可解释的失败结果，
  不让整个 Run 崩溃。
- 群聊内部 Run 不注入该工具；通用子 Agent 不继承该工具。

#### 约束边界

- 写入范围只有当前 Agent 的 `system_prompt`，不能改模型、工具授权、WebDAV、其它 Agent。
- 审批写入是本能力的唯一闸口；只读路径免审批但不写文件，也不提供“10 分钟免审批”之类的租约。
- 不保留独立提示词版本历史，旧文本通过 Run 的 `approval.json` 与事件日志追溯。
- 群内成员 Run 与未携带 Agent 上下文的调用直接不提供该工具。

#### 非目标

- 不做 Web 配置页的二次确认或审批。
- 不做提示词多版本管理、对比历史、回滚入口。
- 不做自动优化提示词、从对话总结自动改写人格。
- 不改动群成员预设、定时任务 prompt 等其它提示词字段。

#### 开放问题

- 是否需要把“只能追加、不可整段重写”的模式作为可选项？当前实现允许整段替换。
- 是否需要在 `/config` 里展示每次修改的审计记录？当前依赖 Run 审批记录。

#### F11 补充

系统提示词支持缺省/空白使用平台默认规则；读取另返回实际正文与来源，显式空字符串更新经审批恢复默认，缺少参数报错。具体契约以 F11 为准。

### F2 Skill 页面

- 状态：已确认，实现中
- 日期：2026-09-17
- 范围：新增平台侧技能库页面；技能文件仍归属各 Agent 私有工作区
- 前置依赖：现有 Workspace 文件 API、`web/src/main.jsx` 侧栏与页面注册方式、Agent Skills frontmatter 规范

#### 背景与目标

技能当前只存在于 `workspace/agents/{agent_id}/workspace/skills/{skill_id}/SKILL.md`，用户只能通过在
「工作目录」页逐层点进目录、或让 Agent 在对话里改。侧栏没有技能入口，也看不到技能列表、描述和
是否会被运行时加载。目标是在平台内提供一个按 Agent 组织的技能管理页面，让用户能直接查看、新建、
编辑和删除技能，并在保存前拦住不合规的 `SKILL.md`。

#### 用户故事

- 作为用户，我打开侧栏的 Skills 页面就能看到每个 Agent 有哪些技能、各自干什么。
- 作为用户，我可以在页面上新建一个技能，填 ID 和描述后得到规范的 `SKILL.md` 骨架。
- 作为用户，我编辑技能时如果 frontmatter 写错（name 与目录名不一致、缺 description 等），保存会被拦住并说明原因。
- 作为用户，我删除一个技能时得到二次确认，整个技能目录（含辅助文件）一起删除。
- 作为用户，我能看到某个技能包含用户硬约束区块，并知道 Agent 自己不能改它。

#### 功能点清单

1. 侧栏新增 Skills 入口（放在「工作目录」之后），页面路径 `/skills`。
2. 页面读取 `config.yaml` 的 Agent 列表，按 Agent 分组列出 `agents/{agent_id}/workspace/skills/` 下的技能。
3. 技能条目显示 frontmatter 的 `name`、`description` 摘要、修改时间，以及“不会加载”“规格警告”“契约”标记。
4. 右侧编辑 `SKILL.md` 文本，支持保存与删除；删除整个技能目录前二次确认。
5. 新建技能时收集 Agent、技能 ID、描述，生成带 frontmatter 的模板并创建文件。
6. 页面按运行时的真实口径分级：会让 SkillsMiddleware 跳过技能的问题阻止保存，只是规范警告的问题允许保存并提示。
7. 技能目录不存在时按“暂无技能”空态处理，不报错。
8. 技能包含 `<!-- BEGIN USER CONTRACT -->` … `<!-- END USER CONTRACT -->` 时给出硬约束提示。

#### 验收标准

- 打开 `/skills` 能看到所有 Agent 的技能分组，没有技能的 Agent 显示空态。
- 新建技能后文件出现在 `workspace/agents/{agent_id}/workspace/skills/{skill_id}/SKILL.md`，
  frontmatter 的 `name` 与目录名一致、`description` 为填写内容。
- 缺 frontmatter、frontmatter 未闭合、缺 name、缺 description 时标记“不会加载”且保存被禁用。
- name 与目录名不一致、name 不符合规范、description 超过 1024 字符时标记“规格警告”，仍可保存；
  运行时仍会加载该技能（description 只取前 1024 字符）。
- 保存合法内容后文件内容更新，页面提示“下一次 Agent 运行时生效”。
- 删除确认后技能目录消失，列表刷新；取消确认则不删除。
- 包含用户契约区块的技能在编辑器上显示提示；页面本身允许用户修改该区块。
- 技能目录缺失、`SKILL.md` 缺失、列表为空都不会让页面报错崩溃；SKILL.md 缺失按“不会加载”处理。

#### 约束边界

- 页面只编辑 `SKILL.md`；技能的辅助文件（如 `helper.py`）只列出名称与大小，编辑仍到 `/workspace`。
- 技能仍按 Agent 私有工作区存放，不引入共享或全局技能目录，不做跨 Agent 复制。
- 不复活 `config.yaml` 的 `skills.definitions` 与 Agent 的 `skill_ids`：它们已不被后端解析。
- 页面标记只反映 SkillsMiddleware 的真实加载行为（跳过或仅告警），不强制用户把已有技能改写成规范命名。
- 页面不做技能启用/禁用开关，也不触发运行中 Run 的重新加载。
- 技能改动在下一次 Agent 运行时由 SkillsMiddleware 扫描生效。

#### 非目标

- 不做技能的版本历史、回滚、导入导出或市场。
- 不做技能调试运行、单技能试跑或效果统计。
- 不做前端内嵌 Markdown 富文本编辑（仍以纯文本编辑 `SKILL.md`）。

#### 开放问题

- 是否需要把技能复制到其它 Agent（会引入跨 Agent 同步语义）？
- 是否需要给「工作目录」页也开放新建文件/目录入口？当前只有 Skills 页使用创建语义。

### F3 会话标题

- 状态：已确认，实现中
- 日期：2026-09-17
- 范围：长期 session 的标题字段、自动生成与手动重命名；不改会话身份与会话隔离规则
- 前置依赖：`workspace/sessions/` 落盘模型、Chat 左侧会话列表、`POST /api/chat/sessions/{session_id}/rename`

#### 背景与目标

Chat 左栏的会话目前只能用渠道标签区分（“微信”“Web Chat” + 渠道身份 + 消息数 + 时间），
会话一多就分不清谁是谁。目标是让每个长期 session 带一个标题，在列表里作为主标识，
并且允许用户随时改名。

#### 用户故事

- 作为用户，我发完第一句话后就能在左栏看到这条会话大致是讲什么的，不用点进去确认。
- 作为用户，我可以把某个会话改成我认得的名字（例如“论文精读”），之后它不会自己变回去。
- 作为用户，我翻到以前的老会话时也能看到标题，而不是只有“微信 / 私聊 / wxid”。

#### 功能点清单

1. session 状态里新增 `title` 与 `title_source`（`auto` / `manual`），并镜像进 `sessions/index.json` 摘要。
2. 会话第一条用户消息落盘时，自动取该消息前 24 字作为标题（压缩空白、去掉 Markdown 前缀）。
3. 手动重命名走 `POST /api/chat/sessions/{session_id}/rename`，写入后 `title_source=manual`。
4. 手动标题一旦写入，后续消息不再自动改写；没有标题的历史会话在读取时按首条用户消息推导，
   不写回文件。
5. Chat 左栏列表第一行显示标题（无标题时退回渠道标签），第二行显示来源 / 渠道身份 / 消息数 / 时间。
6. 列表项提供重命名按钮，打开只含标题字段的轻量弹窗；删除按钮保持原样。

#### 验收标准

- 新会话发出第一条消息后，`state.json` 与 `index.json` 里出现 `title`（`title_source=auto`），
  Chat 左栏显示该标题。
- 标题超过 24 字自动截断并加省略号；空白、换行、`#`/`-`/`[ ]` 等前缀被清理。
- 手动重命名后列表立即更新，标题以 `manual` 记录；再发新消息不会被自动标题覆盖。
- 标题为空时重命名返回 400，前端不允许提交空标题。
- 历史会话（有消息、无标题）在列表里显示按首条用户消息推导出的标题，且不会因此改动 `state.json`。
- 不属于当前 Agent 的 session 不能重命名；会话标题最长 80 字。

#### 约束边界

- 标题只是展示字段：不参与会话身份、active binding、检索过滤或 run 快照。
- 不做模型生成标题（可后续在 `title_source=auto` 基础上追加），不引入额外模型调用。
- 群内部 session（`channel=chat_group`）不出现在 Chat 列表，不参与本需求的 UI 展示。
- 不做标题搜索排序等额外检索能力。

#### 非目标

- 不做会话分组、置顶、标签、收藏。
- 不做多语言标题模板或自定义标题格式。
- 不改微信侧会话命令（`/session list` 等仍按原有编号与会话 ID 展示）。

#### 开放问题

- 是否需要把自动标题升级为模型精炼（首个回复后异步生成一次）？
- 微信侧 `/session list` 是否也显示标题？

### F4 空会话不占列表

- 状态：已确认，实现中
- 日期：2026-09-17
- 范围：Chat 左栏会话列表的可见性规则

#### 需求一句话

没发过消息、也没有标题的会话不要出现在 Chat 左栏列表里（典型场景：删掉当前 Web Chat 会话后
平台会自动建一个空会话，不该因此多出一条“0 条消息”的 Web Chat）。

#### 改动点

- 左栏只渲染 `message_count > 0` 或已有 `title` 的会话；会话计数与“当前会话”状态栏同步使用该过滤结果。
- Web Chat 的 active binding 仍然在页面打开时就存在（保证直接输入就能发消息），只是空会话不占列表行。
- 列表为空时提示“直接发消息就会新建一条；也可以点右上角 + 再开一个”。
- 当前会话是空会话时，Chat 工具栏的摘要显示“新对话 · 未开始”，不再显示“0 条”。

#### 验收标准

- 打开 Chat 页面不会新增“0 条消息”的会话行。
- 删除当前会话后列表不出现新的空会话行；发第一条消息后该会话立即以标题形式出现。
- 只有有内容的会话才计入“会话 N”和“Agent 会话”统计。

#### 边界不做的事

- 不改变会话创建与绑定逻辑：空会话仍然存在并复用，只是不在列表展示。
- 不清理由此产生的空会话文件（仍按既有保留期规则处理）。
- 不改微信侧会话列表与会话命令。

#### 开放问题

- 是否需要在维护清理里顺带删除长期无内容的空会话？

### F5 DeepAgents 依赖升级

- 状态：已确认，实现中
- 日期：2026-09-18
- 范围：仅升级 `deepagents` 依赖版本与锁定口径，不改变平台行为契约
- 前置依赖：`pyproject.toml`、`docs/project-architecture.md` 的 DeepAgent 依赖约定

#### 需求一句话

把平台依赖的 `deepagents` 从 `0.7.13` 升级到 PyPI 当前最新的 `0.7.15`，并在升级后保证现有
DeepAgent 运行时行为不变。

#### 改动点

1. `pyproject.toml` 的 `deepagents` 约束从 `>=0.7.13,<0.8` 调整为 `>=0.7.15,<0.8`。
2. 开发环境与生产 `.venv` 安装到 `0.7.15`。
3. 平台实际使用的 deepagents 接口（backends、middleware、permissions、skills、subagents、
   `_fs_interrupt`）在 `0.7.15` 下仍可用；如有接口变动，按新版本调整运行时封装，不改变对外行为。
4. `docs/project-architecture.md` 中的依赖版本说明同步为 `0.7.15`。

#### 验收标准

- `pyproject.toml` 声明的 `deepagents` 约束为 `>=0.7.15,<0.8`，本地 `.venv` 安装版本为 `0.7.15`。
- `server/tests` 全量通过，尤其是 `test_deepagent_runtime_filesystem.py`、`test_webdav_backend.py`、
  `test_system_prompt_tool.py`、`test_chat_groups.py`。
- 运行时仍能加载 skills、长期记忆、私有文件系统、WebDAV 映射、子 Agent 和 HITL 审批。

#### 边界不做的事

- 不升级 `langchain*`、`langgraph*` 等其它依赖到新的大版本。
- 不为 `0.8` 的新特性做提前适配，也不放宽 `<0.8` 上界。
- 不修改 Agent 配置结构、Run 执行流程或前端行为。

#### 开放问题

- 后续 `0.7.x` 再有补丁版本时，是否每次都需要单独需求文档（当前口径：同一小版本的补丁升级可并入本文档）。

### F6 技能编辑审批

- 状态：已确认，实现中
- 日期：2026-09-18
- 范围：Agent 通过文件工具改写自身私有技能时必须人工审批
- 前置依赖：F2 Skill 页面、DeepAgent 原生 `FilesystemPermission` 与 HITL `interrupt_on`

#### 背景与目标

技能文件会被下一次 Agent 运行时当作提示词内容加载，等同于 Agent 的自我指令。当前 Agent 只要被用户
要求就可以用原生文件工具直接创建或改写 `skills/{skill_id}/SKILL.md`，过程没有人工闸口；而同类的
WebDAV 远端写入、`system_prompt` 修改都已经走 HITL。目标是把技能写入纳入同一套人工确认机制。

#### 用户故事

- 作为用户，Agent 想新建或改写一个技能时，我先看到审批卡片，确认内容后才真正落盘。
- 作为用户，我拒绝时 Agent 会收到拒绝结果并去询问或改用其它做法，技能文件保持不变。
- 作为用户，读取和列举技能不受影响，不会被无谓中断。

#### 功能点清单

1. `/skills` 下的文件写入、编辑、删除（含整个技能目录删除）在 Agent run 内触发 HITL 审批。
2. 审批沿用现有 `approval_required` / `approval_resolved` 事件、`approval.json` 快照、Web 审批卡片和
   微信审批通知，不引入第二套审批机制。
3. 技能审批不提供“批准当前文件（10 分钟）”免审批租约；每次写入都重新确认。
4. 用户自己在 `/skills` 页面保存、新建、删除技能仍然直接生效，不需要审批。
5. 用户硬约束区块 `<!-- BEGIN USER CONTRACT -->` / `<!-- END USER CONTRACT -->` 的保护规则不变，
   即使审批通过也不能改该区块或其所在技能文件。

#### 验收标准

- `write_file` / `edit_file` / 删除命中 `/skills/...` 时，run 进入 `waiting_approval` 并出现审批请求；
  批准后工具才执行，拒绝后技能文件内容不变。
- 读取 `skills/` 文件、列举目录、`glob` / `grep` 技能内容不会触发审批。
- 写入 `/scratch/`、`/artifacts/`、`/memories/` 不因此新增审批。
- WebDAV 写入的“批准当前文件（10 分钟）”行为不受影响，也不会覆盖技能写入的审批要求。
- 通用子 Agent 使用文件工具写入 `/skills/` 时同样需要审批。

#### 边界不做的事

- 不给技能增加启用/禁用开关、版本历史或审批免租约。
- 不改变技能目录结构、frontmatter 规范或 SkillsMiddleware 的加载口径。
- 不把审批范围扩大到所有私有文件写入。

#### 开放问题

- 是否需要在审批卡片里额外渲染 `SKILL.md` 的新旧对照（类似 `system_prompt` 审批）？当前先展示工具参数。

### F7 Chat 图片上传

- 状态：已确认，实现中
- 日期：2026-09-18
- 范围：Web Chat 支持上传图片并作为多模态输入发送
- 前置依赖：`POST /api/chat/messages` 的 attachments 参数、session 附件落盘、Provider `supports_images`

#### 背景与目标

后端已经能在 run 创建时接收并落盘附件，微信通道也能解析图片，但 Web Chat 输入区只有纯文本，用户
无法在页面上传图片。目标是在 Chat 页面补齐图片上传入口与展示，让页面和微信具备同等的图片输入能力。

#### 用户故事

- 作为用户，我在 Chat 输入区点附件按钮选图、直接粘贴截图或把图片拖进聊天区，都能作为本次消息发送。
- 作为用户，我可以只发图片不写文字，也可以图片加文字一起发。
- 作为用户，我发送后能在气泡里看到图片缩略图，刷新页面或重新打开会话后仍能看到。
- 作为使用不支持图片模型的用户，我发的图片会被降级为文本说明，页面给出对应提示而不是静默丢失。

#### 功能点清单

1. 输入区新增图片入口，支持选择本地图片；附件以缩略图形式挂在输入框上方，可单个移除。
2. 支持粘贴剪贴板图片和把图片文件拖入聊天区/输入区，行为与选择文件一致。
3. 图片随 `POST /api/chat/messages` 的 `attachments[]` 提交，字段沿用现有落盘格式
   （`type`、`mime`、`filename`、`data_url`）。
4. 只发图片不写文字时，消息正文留空也允许发送，由后端补默认说明。
5. 用户消息气泡展示图片附件缩略图；点击在新标签页查看原图。
6. 图片只能通过会话附件读取入口访问，且仅限该会话已落盘的附件路径。
7. 后端对上传附件做数量、大小与类型校验，超限返回 400 并说明原因；聊天消息文本仍照常落盘。

#### 验收标准

- 选择、粘贴、拖拽三种方式都能把图片挂到待发送区，并能在发送前移除。
- 发送图片后：session `messages.jsonl` 出现带 `attachments` 的用户消息，run 的 `input.attachments`
  指向 `sessions/{session_id}/attachments/...`，页面气泡显示缩略图。
- 图片 + 文字、纯图片、纯文字三种消息都能发送，且不产生重复 run。
- 刷新页面或切回该会话，历史消息里的图片仍能正常显示。
- 非图片文件、超过大小上限或超过数量上限的附件被拒绝并提示，不创建 run。
- 不支持图片的 Provider 下，run 仍能完成，事件里出现 `image_attachments_textified`，页面提示图片已降级为文本。

#### 约束边界

- 只支持图片（PNG/JPEG/GIF/WebP），不做通用文件上传、多文件批量管理或大文件分片。
- 附件只落在当前 session 的 `attachments/` 下，不写入 Agent 私有工作区，也不进入 `/workspace` 页面编辑范围。
- 沿用现有落盘格式，不引入对象存储、缩略图生成服务或图片编辑能力。
- 图片发送不改变 session 活动 Run 限制：同一 session 有 queued/running/waiting_approval 时仍返回 409。

#### 非目标

- 不做图片压缩、裁剪、旋转、OCR 或图片内容审核。
- 不做 assistant 侧图片输出（该能力由既有 `send_attachment` / 微信投递负责）。
- 不做上传进度条、断点续传和发送队列持久化。

#### 开放问题

- 单张图片大小上限取多少（当前按现有 20MiB 附件上限收敛，页面侧给出更小的默认建议值）？
- 是否需要在群聊页面复用同一上传入口（当前先只做单聊 Chat）？

### F8 输入区动作入口

- 状态：已确认，实现中
- 日期：2026-09-18
- 范围：Chat 输入区新增左侧“+”动作菜单，把附件类动作从发送按钮旁移走
- 前置依赖：F7 Chat 图片上传、`ChatComposer` 组件与 `chatAttachments.js`

#### 背景与目标

F7 把图片入口放在发送按钮旁，导致输入区右侧同时承载“附件”和“发送”两种语义；只要再加一个附件类
动作就会继续挤压发送位。目标是把附件类动作统一收到输入区左侧的一个“+”入口，右侧只保留发送。

#### 用户故事

- 作为用户，我在输入框左侧看到“+”，点开后选择要添加的内容，当前只有“图片”一项。
- 作为用户，我用键盘也能完成同样的操作：聚焦“+”打开菜单、Esc 关闭、Tab 正常走动。
- 作为用户，我粘贴截图或把图片拖进输入区，仍然直接作为待发送图片，不会在草稿里插入图片内容。
- 作为用户，我移除待发送图片时只影响这次发送，已经发出去的图片仍属于会话历史。

#### 功能点清单

1. 输入区左侧新增常驻“+”按钮，点击展开向上的轻量动作菜单；菜单当前只有“图片”一项，点击后打开图片
   选择器。
2. 菜单支持键盘与外部交互：`Enter`/`Space` 打开、方向键与 `Tab` 在菜单项间移动、`Esc` 关闭、点击
   菜单外部关闭；运行中或禁用时“+”不可用且菜单自动收起。
3. 发送按钮固定在最右侧，附件类动作不再出现在发送按钮左侧；`+` 达到图片上限时禁用并说明原因。
4. 图片选择、粘贴、拖拽三种入口保持不变，且都汇入同一套校验与待发送缩略图。
5. 粘贴图片时不把图片插入 Markdown 草稿：粘贴由输入区拦截并转为待发送图片。
6. 群聊输入区不渲染该“+”菜单（群聊暂无附件能力），保持现有“开始协作 + 发送”布局。

#### 验收标准

- 输入区从左到右是“+”、“输入区”、“发送”；桌面与移动布局下按钮都在输入框圆角内，不溢出。
- 点击“+”出现“图片”菜单项，选择后能选中图片并显示待发送缩略图，发送后图片正常落盘与展示。
- 键盘可达：`Esc` 关闭菜单，焦点不会被困在菜单里；菜单打开时点击其它区域会关闭。
- 运行中（含等待审批）或达到 6 张上限时“+”或菜单项不可用并有可见说明。
- 粘贴图片后草稿文本不变、待发送区出现该图片；粘贴文本行为与之前一致。
- 移除待发送图片只丢弃本地待发送内容，不改变已发送消息、会话文件或历史附件。

#### 约束边界

- 只为附件类动作提供入口，不把发送、停止、引用等主操作放进菜单。
- 菜单项复用现有校验规则（最多 6 张、单张 20MB、PNG/JPEG/GIF/WebP），不新增上传通道。
- 不引入二级菜单、分组标题、搜索或快捷键自定义。
- 群聊页面不提供附件能力，本轮也不为群聊增加图片上传。

#### 非目标

- 不做文件（非图片）、截图、历史引用等其它菜单项，只在结构上为它们留位置。
- 不做拖拽排序、批量编辑、图片预览大图弹层或上传进度。
- 不做菜单动画、主题定制或移动端底部抽屉。

#### 开放问题

- 后续加入“文件”菜单项时，是否需要同时放宽 `POST /api/chat/messages` 的非图片校验？
- “引用历史”是否应作为菜单项，还是继续沿用消息上的引用按钮？

### F9 WebDAV 目录免审批写入

- 状态：已确认，实现中
- 日期：2026-09-20
- 范围：Agent 对指定 WebDAV 映射目录可直接读写，不再逐次人工审批
- 前置依赖：WebDAV 映射目录配置、DeepAgent 原生 `FilesystemPermission` 与 HITL `interrupt_on`

#### 背景与目标

当前 `agents.definitions[].webdav.directories[]` 的 `permission=write` 一律生成 `interrupt` 规则，Agent
每次写入都要人工确认，适合远端正式文档，但对 `/llmwiki` 这类由 Agent 自己维护的知识库目录会造成
大量无意义审批。目标是在保留“默认写入需审批”的前提下，允许用户对指定目录显式关闭审批，让该目录
的写入直接落到本地缓存并写回坚果云远端。

#### 用户故事

- 作为用户，我把 `/llmwiki` 配成免审批后，Agent 读写该目录不再需要我逐次点批准。
- 作为用户，其它没有显式配置免审批的 WebDAV 目录仍然逐次审批，行为不回退。
- 作为用户，我在配置页能一眼看出哪些目录需要审批、哪些不需要。

#### 功能点清单

1. 每项 WebDAV 映射目录除 `permission`（`read` / `write`）外，新增审批开关 `approval_required`，
   缺省为 `true`；`permission=read` 时归一化为 `true`，只读目录没有审批语义。
2. 配置页“访问权限”下拉提供三个选项：只读、读写 · 写入需审批、读写 · 免审批；选中免审批时在目录
   卡片内明确提示写入会直接写回坚果云远端。
3. `approval_required=false` 的目录，`write_file` / `edit_file` 不生成 `interrupt`，run 不进入
   `waiting_approval`；需要审批的目录仍走同一套审批事件、审批卡片、微信通知和“批准当前文件（10
   分钟）”租约。
4. 子目录优先规则不变：父目录免审批、子目录需审批（或反向）时，以更深路径的规则为准。
5. 平台对主 Agent 和通用子 Agent 注入的工作区说明按目录分别表述审批要求，避免 Agent 误以为需要等待
   审批而空转或自称“等待批准”。
6. 只读目录、未选择目录、挂载根、WebDAV 删除与二进制上传的限制不变。

#### 验收标准

- 配置 `permission=write` + `approval_required=false` 的目录，Agent 写入直接生效并写回远端，run 不产生
  审批请求；同一 Agent 的需审批目录写入仍进入 `waiting_approval`，批准后生效、拒绝后文件不变。
- 不书写 `approval_required` 的既有配置语义等同 `true`，无需迁移即保持原行为。
- `permission=read` 的目录写入仍被拒绝，且不会产生审批请求。
- 只有免审批可写目录的 Agent run 不额外创建审批用的 run 级 checkpoint。
- 配置页三个选项可正常保存与读回，Agent 弹窗内显示与落盘语义一致。

#### 边界不做的事

- 不做目录级、跨会话或永久免审批的批量授权，也不为技能写入、`system_prompt` 修改等其它 HITL 场景
  开通免审批。
- 不给免审批目录增加“只允许新建、不允许覆盖”之类的细分权限，也不放开删除与二进制上传。
- 不改变 WebDAV 全局连接、同步调度、缓存位置和写回顺序。

#### 开放问题

- 是否需要在运行事件或 `/runs` 详情里对免审批写入增加更醒目的审计提示（例如标注“免审批直写远端”）？
  当前先不改事件模型。

### F10 定时任务按当前配置执行

- 状态：已确认，实现中
- 日期：2026-09-20
- 范围：内置调度的配置来源、WebDAV 同步根默认值
- 前置依赖：统一 Scheduler、`context_webdav_sync` 内置任务、WebDAV 映射目录配置

#### 背景与目标

用户在配置页把坚果云“根目录 / 同步根目录”从 `/notebook` 改成 `/` 后，页面与手动“立即同步”
按新配置执行，但后台 `context_webdav_sync` 仍按服务启动时读到的旧配置同步，把缓存和索引重写成了
旧根 `/notebook` 的内容（顶层变成 `00AgentInbox/`、`01知识卡片/` 等），用户期望的
`/webdav/notebook/`、`/webdav/llmwiki/` 反而消失。目标是让配置保存后立即对所有执行入口生效，
不再依赖重启，并且默认值与后端保持一致，避免同类偏差再次出现。

#### 用户故事

- 作为用户，我在配置页改完 WebDAV 根目录并保存后，后台定时同步立刻按新根同步，不需要重启服务。
- 作为用户，我一眼能在调度事件里看到这次同步实际用的同步根，出问题不用猜。
- 作为用户，`config.yaml` 缺少同步根字段时，页面默认值不会把 `/notebook` 写回我的配置。

#### 功能点清单

1. `ScheduleService` 在每次 `bootstrap`（启动、5 秒轮询、调度读接口）重新读取 `workspace/config.yaml`，
   内置 `context_webdav_sync`、`maintenance_cleanup` 的启用状态、间隔与元数据都按当前配置生成。
2. 执行 `webdav_sync` 时按当前配置构造 `WebDAVContextService`，与手动 `/api/system/webdav-context/sync`
   使用同一份配置；同步完成事件记录本次 `sync_root_path`。
3. `maintenance_cleanup` 同样按当前配置执行，不再固化启动时的保留期与 dry-run 快照。
4. 配置读取或校验失败时保留上一份可用设置，只在错误内容变化时写一条系统日志，不影响其它调度任务继续运行。
5. 前端配置编辑器的默认 `context.webdav_sync.root_path` 与后端默认、`config.example.yaml` 统一为 `/`。

#### 验收标准

- 同一进程内改写 `config.yaml` 的 `root_path` 与 `interval_seconds` 后，触发 `context_webdav_sync`：
  内置定义间隔、实际构造的同步服务根目录、完成事件里的 `sync_root_path` 都等于新配置。
- 手动“立即同步”和后台定时同步对同一份 `config.yaml` 得出相同同步根。
- 配置缺少或写坏时调度不中断，服务继续按上一份可用设置运行并留下可检索日志。
- 配置页在 `config.yaml` 没有该字段时显示 `/`，保存不会写入 `/notebook`。

#### 边界不做的事

- 不为历史 `/notebook` 缓存做运行时迁移或兼容路径；缓存与索引按新根重建。
- 不改变调度轮询间隔（固定 5 秒）、重试策略、内置任务只能查看与 `run-now` 的界面口径。
- 不新增配置项，也不把配置热加载扩展到其它服务实例（Agent run 已按当前配置构造）。

#### 开放问题

- 是否需要在调度页面直接展示每个内置任务“本次使用的同步根”？当前先落在事件与日志里。

### F11 Agent 默认系统提示词

- 状态：已实现
- 日期：2026-09-20
- 范围：Agent 配置、运行快照、系统提示词工具和生产 default 配置
- 前置依赖：F1 系统提示词对话内编辑

#### 背景与目标

默认批判型人格会把解释和会议整理扩展成无依据的评审。用户希望没有自定义提示词时使用统一完整规则，优先完成请求、区分事实与推断、控制查询与追问，并限制普通问答主动写记忆。

#### 用户故事与功能

- 用户可省略或清空 system_prompt 使用默认规则；非空自定义内容替代默认规则，不叠加。
- 新建 Agent 默认留空，配置界面可预览后端维护的默认正文。
- 单聊创建 Run、群聊创建成员执行快照时固化实际提示词；审批恢复和重试保持快照，群成员要求追加在基础提示词之后。
- system_prompt 读取返回配置原文、实际正文与来源；显式空字符串更新恢复默认，缺少参数报错，修改继续逐次审批，对照展示默认正文。
- 生产仅清空 default 的旧提示词，其他配置、历史和记忆保留。

#### 验收标准

- 缺省、空白、自定义配置及前后端行为一致，默认正文只有后端一个来源。
- 批准后下次 Run 生效，拒绝不修改；缺省字段也可通过工具设置。
- 快照不受后续默认或自定义配置变更影响，群成员角色要求保留。
- 新会话复测会议整理、词义解释与显式评审，记录真实模型结果；不得把概率性行为规则声称为程序强制保障。

#### 约束与非目标

保留 DeepAgent、当前模型及其工作区/权限/技能指令；不新增记忆写入拦截，不迁移旧 Run，不清除已有会话。

#### 开放问题

无。

### F12 生产服务改由用户级 systemd 管理

- 状态：已完成
- 日期：2026-10-15
- 范围：生产服务的进程管理方式、`run.sh` 的 systemd 集成、部署与回滚流程
- 前置依赖：现有系统级 unit、运行用户 `qiuqiu` 的 user systemd（`Linger=yes` 已开启）

#### 背景与目标

当前生产服务由**系统级** systemd 管理：`run.sh prod` 把单元生成并安装到
`/etc/systemd/system/super-personal-platform.service`，重启与查状态依赖
`run.sh setup-sudo` 预先写进 sudoers 的三条受限规则。同一台主机上，Hermes 的
`hermes-dashboard`、`hermes-gateway` 和坚果云 `nutstore-mount` 都跑在 **user 级**
systemd（`~/.config/systemd/user/`）下，靠 `Linger=yes` 实现开机自启，日常管理完全不需要 sudo。

目标是让本项目的生产服务也迁到 user 级 systemd：重启、状态与日志查看不再需要 sudo，
并且与同机其它服务的管理方式一致。

另有一条已发生的事实需要一并修正：该单元目前是 `disabled` 且 `inactive`——9 月 21 日停机时被停用，
主机 10 月 11 日重启后没能自动恢复，导致服务静默下线约 24 天。迁移后由 linger 与
`default.target.wants/` 保证开机自启，不再依赖人工登录拉起。

#### 用户故事

- 作为运维，我执行 `systemctl --user restart super-personal-platform` 就能重启服务，不需要 sudo，也不需要事先跑 `./run.sh setup-sudo`。
- 作为运维，主机重启后服务自动恢复，不需要有人登录才能把它带起来。
- 作为使用者，我在 web 的“生产更新”页面触发更新时，不再依赖主机上预置的受限 sudoers 规则。

#### 功能点清单

1. `run.sh prod` 把服务单元生成并安装到 `~/.config/systemd/user/${SERVICE_NAME}`，不再写 `/etc/systemd/system/`。
2. 单元内容按 user 级调整：去掉 `User=` / `Group=`，`WantedBy=default.target`，`After=network-online.target` 并配 `Wants=`，保留 `Restart=always`、`RestartSec=5`、`KillMode=process` 与三个 `SUPER_PERSONAL_*` 环境变量。
3. 所有 `systemctl` 调用改为 `systemctl --user`；安装单元、`daemon-reload`、`enable`、`restart`、`status` 都不再需要 sudo。
4. 删除 `setup-sudo` 子命令与 `install_restart_sudoers()`；原来的 no-TTY sudo 预检改为 user systemd 管理器的可达性预检，`trigger_restart_by_service_exit()` 兜底路径一并移除——user systemd 下重启始终可用，该兜底不可能成功。
5. 首次启用时确保 user systemd 的 linger 已打开（`loginctl enable-linger`）；这是整个流程里唯一可能仍需要 sudo 的一次性动作。
6. 迁移时一次性停用并移除系统级单元，避免两个实例同时抢占 8888；该动作在目标机器上人工执行一次，`run.sh` 不保留系统级单元的检测或迁移代码。
7. `run-prod.sh` 与 `run-dev.sh` 保持不变，它们只是 `run.sh` 的薄壳。

#### 验收标准

- 执行 `./run-prod.sh` 后，`systemctl --user is-active super-personal-platform` 为 `active`，且 `/etc/systemd/system/` 下不再存在本项目单元。
- `systemctl --user restart` 与 `systemctl --user status --no-pager` 全程不触发 sudo。
- 主机重启后服务自动恢复（linger 生效），不需要登录会话。
- 生产更新页触发的更新仍能完成重启，并分别汇报代码拉取、依赖安装、重启与服务状态结果。
- `./run.sh rollback <ref>` 与 `./run-prod.sh --no-pull` 的既有行为不变。

#### 边界不做的事

- 不改成 Docker 或其它容器化方案，不引入新的进程管理器。
- 不改服务监听端口、工作区路径、环境变量语义与依赖安装流程。
- 不为历史系统级单元保留运行时兼容分支；按项目既有偏好做一次性迁移。

#### 开放问题

- 迁移动作里移除系统级单元需要一次 sudo。2026-10-15 已按"人工在 TTY 下完成首次迁移"执行：在目标机器上停用并移除了系统级单元，之后所有路径都不再需要 sudo。

### F13 架构面收敛与群聊删除及默认提示词

- 状态：进行中
- 日期：2026-10-15
- 范围：架构面边界约定、多 Agent 群聊功能的删除、平台默认系统提示词重写
- 前置依赖：无（F1、F11 定义的系统提示词契约保持有效）

#### 背景与目标

平台重新设计架构，用户给出三项指示，合成一份需求文档。

1. **架构面收敛**。实现长期只在 `deepagents` 公开 API 这一层装配 Agent；不再把 LangChain 和 LangGraph 当作并列的架构选择。用户明确保留一个例外：deepagents 表达不了的场景允许自建 LangGraph 图做确定性编排。
2. **删除多 Agent 群聊**。用户判定现有群聊做得很丑，功能代码与线上数据一起清除。
3. **默认提示词对齐 Hermes**。平台默认提示词改为按 Hermes（Nous Research 的 `hermes-agent`）的分节结构组织，把现有四节的实质内容保留并在其前后补齐缺失的节。

#### 用户故事

- 作为维护者，我读 `docs/architecture-qa.md` 与 `docs/project-architecture.md` 时能明确知道：装配 Agent 的地方只走 deepagents 公开 API，哪些既有写法属于登记在案的例外。
- 作为使用者，侧栏不再出现「群聊」入口，`/chat` 单聊、Runs、配置页、技能页与微信通道不受影响。
- 作为运维，生产机上的群聊历史数据被一次性清理干净，不留无法从界面访问的残留目录。
- 作为使用者，新建 Agent 留空提示词时得到的默认人格更直接：回答分量匹配请求分量、不写填充语、不复述请求、不旁白工具调用；同时保住「区分事实与推断」「控制查询与追问」「长期记忆不主动写入」这些既有规则。

#### 功能点清单

1. 删除群聊的 7 个专用文件，并清理 15 个既有文件里的耦合点（路由、容器字段、`group_context` 参数、群 session 保护、`channel=chat_group` 分支、前端导航与页面、共享 CSS 中的群专用类）。
2. 同步删除 `docs/project-architecture.md` 的 `## 多 Agent 群聊` 整节并在同一提交内删除已注册的 `/api/chat-groups/*` 路由。
3. 群聊相关的测试与文档引用同步清理；`CHANGELOG.md` 只在 `[Unreleased]` 下记 Removed，不改历史条目。
4. 重写 `server/domain/system_prompt.py` 的 `DEFAULT_SYSTEM_PROMPT`，改为 7 节结构。
5. 在 `docs/architecture-qa.md` 追加一节，记录架构面/实现面的界线、允许的运行期事实与两条登记例外。
6. 同步 `docs/project-architecture.md` 与 `CLAUDE.md` 的架构层表述，运行期事实描述保持原样。
7. 生产机一次性清理群聊数据，不写迁移脚本或运行时兼容路径。

#### 验收标准

- `.venv/bin/python -m pytest` 全绿，其中 `test_api_contract.py` 的双向核对通过：文档里不再有群聊路径，注册路由里也没有。
- `cd web && npm test` 全绿；`cd web && npm run build` 成功且提交新的 `web/dist/`。
- 仓库中 `grep` 不到 `chat_group` / `chatGroups` / `group_context` / `group_decision` 的可执行代码引用。文档侧只允许保留三处：`docs/project-architecture.md` 与 `CHANGELOG.md` 的删除记录，以及 `web/src/main.test.jsx` 里断言约定目录不含 `chat_groups` 的负向断言。
- 侧栏无「群聊」入口；`/chat`、`/runs`、`/config`、`/skills`、`/wechat`、`/workspace` 正常。
- `/config` Agent 弹窗的「预览默认规则」显示新正文，且 `GET /api/workspace/default-system-prompt` 返回同一份文本。
- 引用默认正文的 7 个测试未做修改即通过（它们用符号断言而非硬编码文本）。
- 生产机 `workspace/chat_groups/`、`channel=chat_group` 的 session 与 `run_group_*` Run 清理后归零，服务重启正常。

#### 约束边界

- 架构面结论只约束**编排层**（装配 Agent、决定图长什么样的地方）。单个工具的内部实现、消息/模型适配器、middleware 回调基类属于实现面，允许继续使用 langchain-core / -openai / -anthropic / -community 与 deepagents 的公开参数类型。
- 保留现有四节的实质内容，特别是为修掉「批判型人格把解释请求扩展成方案评审」而写的规则（见 F11）。
- 默认提示词仍然是概率性行为约束，不是程序强制保障；不得在文档或提交说明里宣称成后者。
- 群聊删除后，既有群 session 与 `run_group_*` Run 失去维护服务保护，会被 15 天清理规则回收——这与「功能加数据一起清」的目标一致。
- 架构文档与需求文档的分工不变：F 文档说「要做什么、做到什么算完成」，`docs/project-architecture.md` 说「系统实际怎么运行」。

#### 非目标

- 不重构 `arxiv` / `yahoo_finance_news` 的工具实现，不移除 `langchain-community` 依赖。
- 不重写 `TodoListMiddleware` 兼容降级与用量采集回调（本次只登记为已知例外并写明退出条件）。
- 不改 `pyproject.toml` 的任何依赖约束。
- 不为群聊保留任何运行时兼容分支、自动迁移或旧路径回退。
- 不改动群聊以外的产品边界、前端导航结构或微信通道行为。

#### 开放问题

- 用例外的两条登记项都留有退出条件（deepagents 稳定自带 `TodoListMiddleware` 时删除 fallback；上游不再透传 `callbacks` 时用量静默标为未知），届时另开需求处理。

### F14 长期记忆改用 Mem0

- 状态：待确认（存在必须先拍板的开放问题）
- 日期：2026-10-15
- 范围：Agent 长期记忆子系统；不涉及 Chat/Run/微信通道的既有契约
- 前置依赖：F13（架构面收敛）先落地；本需求会改动 `pyproject.toml` 依赖，
  与 F13「不改依赖约束」的非目标不冲突，但两者不宜放进同一个提交

#### 背景与目标

DeepAgent 原生 `MemoryMiddleware` 通过单一 `/memories/AGENTS.md` 管理长期记忆，
只有"把文件读进来、把文件写回去"这一层能力，缺少自动提取、合并、去重和淘汰。
结果是记忆只增不减，索引文件越长越倾向于整体影响上下文，记忆越多、干扰越大。

目标：引入 Mem0 作为长期记忆的提取、检索、合并与 CRUD 层，把"什么时候读、
什么值得写、写到哪个范围"交给现有的 Agent Loop 判断，而不是新增一个专职维护
记忆的 Agent。判断的目标不是让 Agent 记住更多，而是让它在该用的时候找到正确的
记忆，并且能够持续更新和淘汰不再适用的信息。

#### 用户故事

- 作为使用者，我跨会话、跨 Agent 表达过的偏好和背景能被记住，但不会因为记忆
  变多而让每次对话都被无关历史挤占。
- 作为使用者，同一个 Agent 记住关于我的专属信息，不会泄漏给其它 Agent；其它
  Agent 对我的一般性偏好仍然可见。
- 作为维护者，我不需要在平台上再挂一个"记忆管理 Agent"，记忆的读写时机由现有
  Agent Loop 自行决定。
- 作为维护者，失效或过时的记忆可以被更新和删除，不是只能追加。

#### 功能点清单

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

#### 验收标准

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

#### 约束边界

- 不引入独立的 Memory Classifier Agent，也不新增一轮专职 scope 分类的 LLM 调用。
- 不在每个 turn 无条件召回全部记忆。
- 记忆是概率性行为约束加检索结果，不是程序强制保障；不得在文档或提交说明里把
  「Agent 一定会记住」写成确定性承诺。
- 本平台当前是单 token 单用户形态，不存在多租户：User Memory 的主体是唯一的平台
  主人，"跨 Agent"指的是跨 Agent 共享，不是跨人。
- 任何跨 Agent 可见的记忆都必须来自 User Memory；Agent-User Memory 的隔离边界由
  middleware 在工具参数层保证，不依赖 Agent 自觉。
- 记忆的后端连接信息属于敏感配置，沿用现有配置读写通道，不回传密钥到前端。

#### 非目标

- 不做记忆的可视化管理页面（浏览、编辑、删除记忆条目）；如需另开需求。
- 不改 `system_prompt` 契约、不改 Skills 机制、不改 Run/Chat/微信通道。
- 不为旧 `MemoryMiddleware` 保留运行时兼容分支或双写。
- 不迁移历史 `AGENTS.md` 之外的任何 workspace 数据。

#### 开放问题

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

### F15 思考过程过滤图节点更新

- 状态：已确认，已实现；聚合口径随后由 F16 扩展（思考过程只留工具轨迹、前后端统一 20 条）
- 日期：2026-10-15
- 范围：Chat「思考过程」聚合口径、`run.partial.thinking[]` 内容
- 前置依赖：DeepAgent `astream(updates)` 流、`agent_update` 公开运行事件

#### 背景与目标

Chat 的思考过程把 `agent_update` 事件聚合进 `partial.thinking[]`，当前规则是「有消息正文用正文，
没有正文就退化成节点名」，于是出现 `图节点更新：HumanInTheLoopMiddleware.after_model` 这类以图
节点名结尾的行。

按项目当前配置实测，主图节点为 `PatchToolCallsMiddleware.before_agent`、`SkillsMiddleware.before_agent`、
`MemoryMiddleware.before_agent`、`model`、`tools`、`HumanInTheLoopMiddleware.after_model`。其中
`model`、`tools` 的更新带消息正文，`model → after_model → tools → after_model` 每轮循环都会让
中间件节点产生一次没有正文的更新。这些行只说明某个内部钩子跑完了，不描述模型想了什么或做了什么；
`partial.thinking[]` 只保留最近 20 条，工具循环一长，噪声会挤掉真实进展，刷新后从 `partial.json`
恢复的思考过程尤其明显。

目标是让思考过程只呈现用户能理解的实际进展，同时不影响排查所需的完整事件留痕。

#### 用户故事

- 作为用户，我在 Chat 看到的思考过程只包含模型输出、工具返回、子 Agent 输出和审批提示，不出现中间件节点名。
- 作为用户，我刷新页面后恢复的思考过程与运行中看到的保持一致。
- 作为排查者，我仍能在 Runs 页面事件列表和 `events.jsonl` 里看到完整的 `agent_update` 事件（含节点名）。

#### 功能点清单

1. 只有携带消息正文（`preview` 非空）的 `agent_update` 才进入思考过程；正文为空时该事件照常写入
   `events.jsonl`，但不写 `partial.thinking[]`。（F16 把这条收紧为「只有工具轨迹类更新进入」，
   模型正文同样不再进入。）
2. 后端 `_thinking_text` 与前端 `runEventThinkingText` 使用同一口径，去掉 `图节点更新：<节点名>` 和
   `DeepAgent 状态已更新` 两条兜底文案。
3. `agent_update` 事件结构、`nodes`/`preview` 字段与 Runs 页面事件展示保持不变
   （F16 追加 `message_type` / `tool_calls` 两个补充字段）。

#### 验收标准

- 同一 run 内，`preview` 为空或不存在的 `agent_update` 不再产生思考过程条目；`preview` 非空的仍然产生。
- 事件流里仍能查到这些被过滤的 `agent_update` 事件及其 `nodes`。
- 同一条 `agent_update` 事件在运行中（前端事件轮询）与刷新后（`partial.json`）得到相同的保留或丢弃判定。
- 子 Agent 输出重复 preview 的抑制行为（`suppressed_previews`）不受影响。

#### 边界不做的事

- 不删除或改写 `agent_update` 事件，不改 `events.jsonl` 结构，不改 Runs 页面展示。
- 不引入中间件节点白名单或中文映射，过滤规则与 deepagents 版本解耦。
- 不改思考过程的折叠行为与样式。
- 模型正文与流式正文重复、以及前后端条数上限不一致这两项，已由本文 F16 接手，不在本文范围。

#### 开放问题

- 是否需要在思考过程里保留「正在调用工具」这类轻量进展信号？当前 `tools` 节点的工具返回内容已能体现。

### F16 思考过程工具轨迹与上限统一

- 状态：已确认，实现中
- 日期：2026-10-15
- 范围：Chat「思考过程」的内容口径与保留条数、`agent_update` 事件补充字段
- 前置依赖：F15（思考过程过滤图节点更新）已落地

#### 背景与目标

F15 之后，思考过程只保留带正文的图更新，但还有两个问题：

1. 模型解读。主模型每一步的 `model` 节点更新带的是该步模型输出原文，而同一段文字已经通过
   `assistant_delta` 流式写进正文，思考区把它再贴一遍没有新信息；反过来，工具调用这一步
   （模型只发工具调用、正文为空）在思考区完全看不到，用户只能看到随后出现的工具返回。
2. 条数上限不一致。前端 `mergeChatThinking` 只保留最近 10 条，后端 `partial.json` 保留 20 条，
   同一个长 run 在运行中和刷新后显示的思考条数不同。

目标是让思考过程成为「工具轨迹」：能看出调用了哪些工具、返回了什么，以及子 Agent 与审批事件，
而不是正文的副本；同时让运行中与刷新后的思考过程完全一致。

#### 用户故事

- 作为用户，我在运行中看到「调用工具：read_file」和随后的工具返回，而不是正文被重复贴一遍。
- 作为用户，我刷新页面后看到的思考过程条数与运行中一致。
- 作为排查者，我仍能在 `events.jsonl` 里看到每一步模型输出原文与节点名。

#### 功能点清单

1. `agent_update` 事件补充两个字段：`message_type`（该次更新里最新消息的 langchain 类型，`ai` /
   `tool` / 空）与 `tool_calls`（该消息声明的工具名列表）。原有 `nodes` / `preview` / `source_class`
   保持不变，事件仍保留模型输出原文供排查。
2. 思考过程渲染规则（后端 `_thinking_text` 与前端 `runEventThinkingText` 一致）：
   - 带工具调用 → `调用工具：<工具名, 工具名>`；
   - 最新消息是工具返回且有正文 → 显示该正文；
   - 其余（模型正文、中间件钩子节点、无正文更新）→ 不显示。
3. 思考过程保留条数前后端统一为 20 条（后端 `THINKING_SNAPSHOT_LIMIT`、前端 `CHAT_THINKING_LIMIT`），
   前端 `mergeChatThinking` 与后端 `_merge_thinking` 使用同一上限口径（去重规则仍按相邻/全量各自实现）。
4. 子 Agent 输出重复 preview 的抑制（`suppressed_previews`）保留：子 Agent 结论在 `subagent_response`
   事件里已单独呈现，同内容的工具返回不再重复进思考过程。

#### 验收标准

- 同一 run 里，模型只发工具调用的那一步在思考过程显示 `调用工具：<名称>`，模型正文不再出现在思考过程。
- 工具返回、子 Agent 输出、审批提示、运行开始/结束仍然出现在思考过程。
- `events.jsonl` 的 `agent_update` 事件仍带模型输出原文、`nodes`、新增的 `message_type` / `tool_calls`。
- 运行中与刷新后（`partial.json`）的思考过程条数一致，上限为 20。
- 前端单测覆盖渲染规则与上限；后端单测覆盖事件字段与思考条目。

#### 边界不做的事

- 不改工具返回 preview 取尾部 500 字的既有行为。
- 不在思考过程展示工具调用参数，只展示工具名。
- 不新增思考过程的展开层级、时间戳或折叠交互。
- 不改 Runs 页面的事件列表展示。

#### 开放问题

- 工具名是否需要在思考过程里翻译成中文动作描述（如 `read_file` → 「读取文件」）？当前按原样展示。

### F17 行情工作台

- 状态：已确认，已实现（快层 + 信号事件流 + Agent 慢层面板；K 线图不在本期）
- 日期：2026-10-15
- 范围：新增 `/market` 行情工作台菜单与页面、加密货币行情「快层」（采集 / 指标 / 信号 / 事件）、Agent「慢层」解读面板、Telegram 出站通知、`/api/market/*` 接口族、`market_snapshot` 平台工具
- 前置依赖：现网已有 Run 体系、统一调度器、维护清理、`$project-commit` 流程；加密货币行情需要新增交易所公开行情客户端

#### 背景与目标

用户要用交易 Agent 做行情分析，但当前 Agent 实际只能覆盖 1h 及以上周期。

原因不是模型不够聪明，而是**一轮 Run 的延迟和确定性**与 1h 以下周期的时效性不匹配：

- 一次 Run 从创建到出结论要经过 session/checkpoint 恢复、middleware 与提示词注入、多轮模型调用和工具调用，实际耗时数十秒到数分钟。
- 1m / 5m / 15m / 30m 的 K 线分别每 60 / 300 / 900 / 1800 秒换一根。等 Agent 的结论出来，它分析的那根 K 线已经过去一到数根。
- 同一份输入，Agent 两次 Run 可能给不同判断；交易决策要求同一份行情数据得到同一个结论。

所以本需求的目标不是「让 Agent 跑得更快」，而是**把 1h 以下的判断从 Agent 手里拿走**：

- 有时效性要求的部分（1m–30m 的行情、指标、信号）由平台的确定性代码产出，全链路不经过模型。
- Agent 只负责它真正擅长的慢思考：跨周期矛盾的解释、消息面与价格的关系、复盘，以及回答用户的追问。

具体目标：

1. 新增 `/market` 菜单和行情工作台页面，第一屏是多周期信号矩阵。
2. 快层（1m/5m/15m/30m）从「交易所最新已收盘 K 线」到「页面可见」的端到端延迟不超过 5 秒。
3. 慢层（Agent 解读）与快层解耦：Agent 在跑的时候，矩阵和事件流继续刷新，页面不阻塞。
4. Agent 读到的是与页面同源的结构化快照，而不是自己用浏览器去爬行情网站。

#### 用户故事

- 作为交易者，我打开 `/market` 就能看到自选标的在七个周期上的趋势、动量、波动、量能和信号，数据是秒级最新的，不需要等任何模型。
- 作为交易者，看到「5m 放量突破」这类事件时，我能一键派给 Agent 解读，解读在一到三分钟内异步贴回右侧；这期间我可以继续看别的标的、继续盯矩阵。
- 作为交易者，我要的 Agent 解读是「5m 破了，但 4h 还在下跌通道里，该不该跟」这种跨周期矛盾，不是复述一遍价格。
- 作为交易者，Agent 每次解读的完整过程和结论都能在 Runs 里回溯。
- 作为运维者，行情数据要按保留期自动清理，不能无限增长；行情采集出问题不能拖垮平台其它功能。

#### 功能点清单

1. 侧栏新增「行情」菜单，路径 `/market`，排在 Chat 之后。
2. 页面三栏：左栏自选标的列表，中栏多周期信号矩阵 + 信号事件流，右栏 Agent 解读面板。
3. 自选标的支持增删改，按「交易所 + 交易对」标识，可设中文别名。
4. 周期固定为 1m / 5m / 15m / 30m / 1h / 4h / 1d 七档，可在配置中调整。
5. 矩阵每行一个周期，列为：周期、K 线状态（进行中 / 已收盘及收盘时间）、趋势、动量、波动、量能、信号、迷你走势。
6. 1m–30m 行标记为「快层 · 秒级」，1h / 4h / 1d 行标记为「慢层」，两者在视觉上明确区分。
7. 数据陈旧时该行显式标注延迟，不静默显示旧值。
8. 信号事件流：规则命中时产生事件，倒序展示，未读高亮，每条可派给 Agent 解读。
9. 右侧解读面板：显示当前标的最近一次解读（时间、结论、置信度、要点、关联 Run 链接），下方是历史解读列表，底部是「让 Agent 分析一次」按钮，按钮明确标注预计耗时与运行中状态。
10. Agent 解读运行期间，矩阵刷新、事件流更新、标的切换全部保持可用。
11. 新增平台工具 `market_snapshot`，Agent 可读取指定标的指定周期的结构化快照，与页面矩阵同源。
12. 每个标的对应一个独立长期 session，不与用户的 Chat 会话抢锁；用户可以在 Chat 里接着追问这次解读。
13. 行情数据按保留期纳入现有维护清理。

#### 验收标准

- 快层：交易所最新已收盘 K 线到矩阵页面可见，端到端延迟不超过 5 秒（正常网络下）。
- 矩阵轮询不出现闪动、`unknown` 或结果丢失：内容未变化时不重绘。
- 快层全链路不产生任何模型调用；模型不可用时矩阵照常刷新，并明确标注数据时间。
- Agent 解读运行中的任意时刻，矩阵仍在刷新。
- 「让 Agent 分析一次」创建独立 Run，不占用用户 Chat 的 session；用户同时给该标的发消息不会返回 409。
- 同一份 bar 数据必然得到同一份指标与信号，输出完全可复现。
- `market_snapshot` 工具返回的数值与页面矩阵一致（同源，不是两套实现）。
- 行情数据文件按 `retention_days` 被维护清理回收，清理失败不影响页面展示和历史 Run。
- 新增接口全部登记在 `docs/project-architecture.md`，`server/tests/test_api_contract.py` 通过。

#### 约束边界

- 本需求只做分析与呈现，**不做下单、不做持仓、不做盈亏**。平台不接任何交易私有 API，不持有交易所 API key，不向交易所写入。
- 快层只读公开行情数据。
- 不引入第二套指标实现：页面、Agent 工具、信号规则共用同一个计算入口，数值不一致按缺陷处理。
- 事件触发的 Agent Run 不是必须项；第一版只做手动触发和定时触发。

#### 非目标

- 不恢复 `docs/project-architecture.md` 已删除的 Portfolio 投资组合模块；本需求不做组合、持仓和收益归因。
- 不做回测引擎。
- 不做 1m 以下的秒级 / tick 级 K 线。
- 不做信号的自动执行，也不向微信推送下单建议。
- 不改动现有 Chat / Runs / Schedules 的交互。

#### 决策记录

1. **数据源**：Binance 现货公开接口，**默认域名取官方只读行情域 `https://data-api.binance.vision`**。选它的理由是它在两条路上都通，而 `api.binance.com` 两条路都不通。在 192.168.1.3 上实测（各 3 轮，结果稳定）：

   | 域名 | 裸直连 | 经局域网代理 192.168.1.2:7893 |
   | --- | --- | --- |
   | `api.binance.com` | 连不上 | HTTP 451 |
   | `api-gcp.binance.com` | 连不上 | HTTP 451 |
   | `data-api.binance.vision` | 200 | 200 |

   451 的响应体是 Binance 的 `Service unavailable from a restricted location according to 'b. Eligibility'`，即代理出口节点位于受限地区。直连该域名时取到七个周期的 klines、最后一根 1m bar 起始距今 23 秒、连续 5 次全成功。`market.base_url` 保留为覆盖项，用于自建镜像或网关；`market.proxy` 默认留空，跟随进程代理环境变量——生产机的 systemd 单元没有设置代理变量，所以服务实际走直连，而直连恰好也只有该域名可用。
2. **高优先级信号推送**：只做 Telegram **出站**推送（`market.telegram`，按严重度阈值过滤）。它不接入平台的渠道、session 和 `RunDeliveryService` 投递体系，因此不影响微信通道和 Run 的 `delivery.json`。在 Telegram 里跟 Agent 双向对话是另一件事，需要接入渠道抽象，不在本需求范围内。
3. **慢层形态**：给一份整体综述（各周期当前状态 + 跨周期矛盾 + 关键位 + 观察条件），不是每个周期各给一条独立判断。
4. **K 线图**：本期不做。前端不引入 `lightweight-charts`，矩阵行尾用纯 SVG 迷你走势替代。留作下一期。
5. **多源兜底**：`market.sources` 是有序列表，按顺序尝试，第一个成功的接管并保持粘性（只有当前源真的失败才轮到下一个），全部失败时保留上一份快照并在页面上标注延迟与降级来源。粘性是必须的：不同交易所同一根 K 线有微小差异（实测 Binance 82476.01 vs Gate 82479.80），每轮随机换源会让指标抖动。一个源只要有一个周期取不到就整体换源，先前的数据不落盘，避免半份数据混进历史。
6. **默认开启**：`market.enabled` 默认 `true`。这是用户主动要的功能，让人先去配置页打开它是多余的；没有自选标的时它什么都不做（不建 feed、不发请求），所以默认开启是安全的。同理，慢层没配 `commentary.agent_id` 时页面直接把「让 Agent 分析一次」置灰并说明去哪里配，而不是让人点了才吃一个 400。
7. **代理是全局的**：新增 `network.proxy` 作为全局出口代理，`browser.proxy` 和 `market.proxy` 留空即跟随它，写 `direct` 强制直连。**微信通道刻意不跟随**——微信是境内服务，走境外出口既没有收益，还会让登录态被判成异常登录，所以微信账号的 `proxy` 空值就是直连。语法和理由见 `docs/project-architecture.md` 的「网络代理」一节。
8. **不引入 ccxt**：评估后决定自己写适配器。实测依据：① 在项目 venv 里 `pip install ccxt` 会失败（拉的 `cryptography` 新版本无 wheel，回落 Rust 源码编译，Cargo lock 版本不匹配），必须和已有的 `cryptography>=38,<49` 一起解析才能装上，净增 5 个包（aiodns / ccxt / coincurve / pycares / setuptools）；② ccxt 的 binance 连纯现货也会去拉 `dapi`/`fapi` 合约域名建市场表，而那两台在境内不通，必须显式设 `options.fetchMarkets=["spot"]`；③ 要让 binance 走 `.vision` 得手改 `urls['api']['public'/'private']`，用 `hostname` 参数反而会被引到 `dapi`；④ 实测 Bitget 的 `spot/public/symbols` 经代理要 15 秒以上，ccxt 的 `load_markets` 直接超时——即用户问到的 Bitget 在这套网络里不是可用来源。我们的实际需求是 2 个只读接口 × 2 家交易所，手写适配器（Gate 的 `candlesticks` 字段顺序特殊，已单独解析并测试）成本更低。**若以后要接 OKX/Bybit/Bitget 等第 3 家以上，再重新评估 ccxt**：适配器接口（`fetch_klines` / `fetch_ticker` / `aclose`）已经隔离好，换实现只动 `market_client.py` 一个文件。

#### 实现说明

- 指标与信号只有一份实现：`server/domain/market_indicators.py`。页面矩阵、信号事件、`market_snapshot` 工具和解读提示词全部调用它。
- 自选列表存在 `market.sqlite` 的 `watchlist` 表而不是 `config.yaml`，避免在代码里对用户配置文件做保注释的 YAML 手术；`market.symbols` 只在列表为空时作为一次性种子。
- 解读会话 id 由 `SessionService.get_or_create` 在首次解读时生成并记入 `market.sqlite` 的 `meta`；只读路径只查这份记录、不创建会话。`build_session_id` 返回的是渠道身份 active key，不是 session id，两者不能混用。
- 保留清理按「超过 `retention_days`」与「每个 series 至少保留 `history_limit` 根」取较大范围，否则 `1d` 会被删到只剩二三十根，而指标需要 200 根（EMA200）。
- 信号严重度按周期降级：1h 以下整体降一级，分钟级噪音不与小时级信号同级提醒。
- 多源兜底的粘性体现在 `MarketFeed._active`：每轮从当前源开始试，成功就继续用它，只有它失败才顺延到下一个；即使主源恢复了也不立刻切回去（Gate 仍在工作就继续用 Gate），避免每轮来回换源。一个源只要有一个周期取不到就整体换源，且先前的数据不写库。
- K 线按 `market_key`（固定 `"spot"`）存，不按来源交易所：否则降级切换会把同一标的的历史切成两段。
- 降级原因会去掉空消息：httpx 的连接类异常 `str()` 常为空，`market_client._get` 会退回异常类名（例如 `ConnectTimeout`），否则 `/api/market/overview` 的 `feed[].last_error` 只剩一句无用的前缀。

#### 尚未覆盖

- K 线图与更细的图表交互。
- 事件触发的 Agent Run（当前只有页面按钮手动触发）。
- Telegram 双向对话渠道。
- 加密货币现货以外的市场（合约、A 股、美股）。
- 第三家以上的交易所。当前只写了 `binance` 和 `gate` 两个适配器；真要加 OKX/Bybit/Bitget 时按决策 6 重新评估 ccxt，适配器接口已经隔离好，换实现只动 `market_client.py`。
- 每个标的单独指定来源（现在是一份全局有序列表，所有标的共用）。
