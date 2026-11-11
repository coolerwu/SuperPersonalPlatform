# 技能改为一次性初始化设计

- 日期：2026-11-11
- 状态：设计已确认，待评审后进入实现计划
- 范围：技能 seed 语义、初始化内容组织、技能 API 与页面展示；不改插件集市、Agent 配置、平台工具层

## 1. 背景与目标

现行实现把 5 个技能（`quant-backtest`、`chanlun`、`trade-journal`、`allocation`、`cron-scheduler`）当作「平台内置」：随版本更新、不可删除、平台移除时删除本地未修改的文件。这隐含了一种语义——技能是平台长期维护的资产。

新语义：技能只是一次性初始化参考。平台在首次初始化时把「有哪些工具、怎么组合、什么会用错」写进 SKILL.md；之后这份技能归 Agent 所有，Agent 或用户可以随时调整，平台不再把它当作自己的东西。

参考实现：Hermes（NousResearch/hermes-agent）的 bundled skills 机制——manifest 记录来源哈希；未修改才随版本更新；改过永久跳过；用户删除不复活；上游移除只在 manifest 退出记录、保留本地文件。

成功标准：

- 平台不再有「内置技能」概念，改为「初始化技能」。
- Agent 或用户修改过的技能永远不会被 seed 覆盖。
- 用户删除初始化技能后不会被种回来。
- 平台移除某个初始化技能时保留本地文件。
- 存量机器升级后，未修改的技能自动变为新模板，已修改的保持原样。

不做「知识全部下沉到 MCP 工具描述」的方案：工具描述只保留必要契约，技能继续承载操作说明，但它只是初始化内容，不是平台资产。

## 2. 术语与来源值

- `SKILL_SOURCE_BUILTIN`（`"builtin"`）改为 `SKILL_SOURCE_INITIAL`（`"initial"`）。
- API 的 `source` 取值变为 `initial` / `ai` / `manual`。
- 页面标签：`initial` 显示「初始化」；`ai` 显示「AI 创建」；`manual` 显示「手动创建」。
- `.seed_state.json` 保留，含义从「内置内容哈希」变为「初始化版本哈希」。
- 代码注释、文档、CLAUDE.md 中不再使用「内置技能」的说法。

## 3. Seed 规则

以「本地文件是否存在」「seed 记录是否存在」「当前哈希是否等于记录」「模板里是否还有该技能」判定：

| 本地文件 | seed 记录 | 内容 | 行为 |
|---|---|---|---|
| 无 | 无 | 模板有 | 写入模板，记录哈希（首次初始化） |
| 无 | 有 | 模板有 | 用户删除过，跳过，**保留记录作为墓碑** |
| 有 | 无 | 当前哈希 == 模板 | 开始跟踪，记录哈希（例如手动复制了模板） |
| 有 | 无 | 当前哈希 != 模板 | 不动，不跟踪 |
| 有 | 有 | 当前哈希 == 记录 == 模板 | 不动 |
| 有 | 有 | 当前哈希 == 记录 != 模板 | 用模板覆盖，更新记录（未修改，随版本更新） |
| 有 | 有 | 当前哈希 != 记录 | 视为已调整，永久跳过，保留记录 |
| 有 | 有 | 模板里没有该技能 | 只删记录，**保留本地文件** |
| 无 | 有 | 模板里没有该技能 | 删记录 |

要点：

- 「记录存在但文件不存在」是墓碑，后续任何版本都不会重新写入，除非模板里也删除了该技能（此时删记录）。
- 平台移除技能不再删除本地文件，也不再看内容是否修改；`_remove_retired_builtin_skills` 的删除逻辑收敛为「把不在模板里的记录从 state 移除」。
- seed 不区分修改来源：用户手工编辑、Agent 用文件工具编辑都走同一条哈希判定。

## 4. 删除

- `delete_skill` 允许删除初始化技能（移除现在的「不能删除内置技能」限制），删除整个技能目录。
- 删除时**保留** `.seed_state.json` 中的该条目，作为「曾经初始化过、用户不要了」的墓碑；后续 seed 依据第 3 节规则跳过。
- 技能页对所有技能显示删除按钮，确认文案不再写「系统技能不能删除」。

## 5. 初始化内容

- 模板从 `apps/api/app/services/skills.py` 的 `DEFAULT_SKILLS` dict 移到 `apps/api/app/seed_skills/<name>/SKILL.md`；`seed_default_skills` 读取目录内容。
- 5 个技能保留，统一为三段结构，砍掉汇报话术、重复建议和与工具描述重复的细节：
  - **工具清单**：本领域有哪些工具、各自一行用途、哪些是写操作。
  - **组合方式**：典型任务到调用序列。例如回测是 `market_probe` → 写策略 → `backtest_run` → 必要时 `backtest_status` / `backtest_result`。
  - **硬契约**：不看就会用错的规则。例如 `strategy(df)` 返回 `[-1, 1]`、cron 是 5 字段且默认 `Asia/Shanghai`、交易记录 `thesis` 必填、缠论是「老笔近似」不能宣称严格理论。
- frontmatter 只保留 `name` 和 `description`；description 写触发条件，保持简短（系统提示里的技能索引会截断长描述）。
- 新增测试：初始化技能正文中出现的平台工具名（`backtest_`、`market_`、`trade_journal_`、`allocation_`、`chan_`、`cron_` 前缀）必须存在于 `platform_mcp.BUSINESS_TOOL_NAMES`（运行时实际挂载的工具集），并且每个已挂载工具都至少被一个模板提到，防止工具改名或清单漏项。

## 6. 可观测性

- `SkillInfo`、`SkillDocument` 增加 `modified: bool`，对应 `SkillOut` 与 `SkillDetailOut`：技能被跟踪且当前哈希不等于记录哈希时为 true。
- 技能页与编辑器对 `modified` 的技能显示「已调整」标签，让用户看出 Agent 或手工改过哪些。
- 不做使用统计、版本树、一键恢复初始内容。

## 7. Agent 调整通道

- 本轮不新增技能 MCP 工具；`skill_save` 继续保持未挂载。Agent 使用已有文件工具读写 `claude/skills/`。
- 不限制修改来源；是否保留完全由哈希与墓碑规则决定。
- 技能变化对新会话生效，沿用 Claude Code 自动发现机制，不额外做热刷新。

## 8. 影响面

- `apps/api/app/services/skills.py`：来源常量、seed 逻辑、退役清理简化、`delete_skill` 限制、`scan_skills` / `_parse_skill` 计算 `modified`。
- `apps/api/app/schemas.py`、`apps/api/app/routers/skills.py`：`modified` 字段。
- `apps/api/app/tests/test_seed_skills.py`：按新规则重写；新增墓碑、上游移除保留文件、已调整跳过、`modified` 标记、删除初始化技能等用例。
- `apps/web/src/types.ts`、`SkillsPage.tsx`、`SkillEditorDialog.tsx`、`styles.css`：`source` 新值与标签、分组文案、删除按钮、已调整标签。
- `docs/intent.md` F8、`CLAUDE.md` 技能相关段落、`README.md` 如有提及。
- `apps/web/dist`：前端修改后重新构建并提交。

## 9. 迁移

- 存量机器的 `.seed_state.json` 里 5 个技能记录的是旧模板哈希；新模板哈希不同，下次启动按第 3 节规则：未修改的被替换为新模板，修改过的保留。
- 已退役的 `knowledge-base`、`memory-organizer` 等技能此前已被删除或失去跟踪；如有残留文件且无记录，按「有文件、无记录、内容 != 模板」处理，不动。
- 不备份旧技能内容，不回写 `.seed_state.json`。

## 10. 验收标准

- 启动 seed 不覆盖已修改（含 Agent 修改）的初始化技能。
- 用户删除初始化技能后，重启或升级都不会复活。
- 平台移除初始化技能时只删记录，本地文件保留。
- 存量未修改技能升级为新模板；已修改技能保持原样。
- 技能页正确显示「初始化 / 已调整 / AI 创建 / 手动创建」，初始化技能可删除。
- 后端测试与前端构建通过。

## 11. 明确不做

- 可选技能市场 / Skills Hub（插件集市承担扩展能力）。
- 技能启用开关、使用统计、版本管理、一键恢复初始内容。
- 新增技能 MCP 工具，或挂载 `skill_save`。
- 把技能内容迁到 Agent 默认 instructions 或 MCP 工具描述。
