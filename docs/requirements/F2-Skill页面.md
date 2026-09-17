# F2 Skill 页面

- 状态：已确认，实现中
- 日期：2026-09-17
- 范围：新增平台侧技能库页面；技能文件仍归属各 Agent 私有工作区
- 前置依赖：现有 Workspace 文件 API、`web/src/main.jsx` 侧栏与页面注册方式、Agent Skills frontmatter 规范

## 背景与目标

技能当前只存在于 `workspace/agents/{agent_id}/workspace/skills/{skill_id}/SKILL.md`，用户只能通过在
「工作目录」页逐层点进目录、或让 Agent 在对话里改。侧栏没有技能入口，也看不到技能列表、描述和
是否会被运行时加载。目标是在平台内提供一个按 Agent 组织的技能管理页面，让用户能直接查看、新建、
编辑和删除技能，并在保存前拦住不合规的 `SKILL.md`。

## 用户故事

- 作为用户，我打开侧栏的 Skills 页面就能看到每个 Agent 有哪些技能、各自干什么。
- 作为用户，我可以在页面上新建一个技能，填 ID 和描述后得到规范的 `SKILL.md` 骨架。
- 作为用户，我编辑技能时如果 frontmatter 写错（name 与目录名不一致、缺 description 等），保存会被拦住并说明原因。
- 作为用户，我删除一个技能时得到二次确认，整个技能目录（含辅助文件）一起删除。
- 作为用户，我能看到某个技能包含用户硬约束区块，并知道 Agent 自己不能改它。

## 功能点清单

1. 侧栏新增 Skills 入口（放在「工作目录」之后），页面路径 `/skills`。
2. 页面读取 `config.yaml` 的 Agent 列表，按 Agent 分组列出 `agents/{agent_id}/workspace/skills/` 下的技能。
3. 技能条目显示 frontmatter 的 `name`、`description` 摘要、修改时间，以及“不会加载”“规格警告”“契约”标记。
4. 右侧编辑 `SKILL.md` 文本，支持保存与删除；删除整个技能目录前二次确认。
5. 新建技能时收集 Agent、技能 ID、描述，生成带 frontmatter 的模板并创建文件。
6. 页面按运行时的真实口径分级：会让 SkillsMiddleware 跳过技能的问题阻止保存，只是规范警告的问题允许保存并提示。
7. 技能目录不存在时按“暂无技能”空态处理，不报错。
8. 技能包含 `<!-- BEGIN USER CONTRACT -->` … `<!-- END USER CONTRACT -->` 时给出硬约束提示。

## 验收标准

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

## 约束边界

- 页面只编辑 `SKILL.md`；技能的辅助文件（如 `helper.py`）只列出名称与大小，编辑仍到 `/workspace`。
- 技能仍按 Agent 私有工作区存放，不引入共享或全局技能目录，不做跨 Agent 复制。
- 不复活 `config.yaml` 的 `skills.definitions` 与 Agent 的 `skill_ids`：它们已不被后端解析。
- 页面标记只反映 SkillsMiddleware 的真实加载行为（跳过或仅告警），不强制用户把已有技能改写成规范命名。
- 页面不做技能启用/禁用开关，也不触发运行中 Run 的重新加载。
- 技能改动在下一次 Agent 运行时由 SkillsMiddleware 扫描生效。

## 非目标

- 不做技能的版本历史、回滚、导入导出或市场。
- 不做技能调试运行、单技能试跑或效果统计。
- 不做前端内嵌 Markdown 富文本编辑（仍以纯文本编辑 `SKILL.md`）。

## 开放问题

- 是否需要把技能复制到其它 Agent（会引入跨 Agent 同步语义）？
- 是否需要给「工作目录」页也开放新建文件/目录入口？当前只有 Skills 页使用创建语义。
