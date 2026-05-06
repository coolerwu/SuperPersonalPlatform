# 项目 Agent 指令

默认使用中文回复用户。

`docs/project-architecture.md` 是本项目的架构说明、长期记忆和运行约定索引。

开始实现任何变更前：

- 读取 `docs/project-architecture.md`。
- 执行 `git status --short`，确认当前未提交代码状态。
- 按用户当前偏好，本项目提交流程会提交所有未提交代码。

如果实现改变了架构、行为、命令、依赖、配置、公共接口或运维方式，必须同步更新 `docs/project-architecture.md`。

如果实现改变了 Agent 工作约定、提交流程、技能使用方式或长期协作规则，必须同步更新 `AGENTS.md`、对应 `.codex/skills/*/SKILL.md`，以及受影响的说明文件后，才能进入提交步骤。

提交项目前必须执行项目内 `$project-commit` skill。
