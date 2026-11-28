# ADR-0007: 需求走规格流，不再维护编号账本

- 状态：Accepted
- 日期：2026-11-29

## 背景

v1 用 `docs/intent.md` 维护 F1–F28 的需求账本（666 行），同时 README 与 CLAUDE.md 各写一份行为与架构。三份事实重叠并已经漂移：CLAUDE.md 还写着「F1–F17」，账本头部写「编号水位 F27」而实际已到 F28。另有 44 份按日期冻结的 plans / specs（29,117 行）与当前事实混放。

## 决策

- 需求唯一入口是规格文件：新改动先写 `.scratch/<feature-slug>/spec.md`（`/to-spec` 技能；本地 Markdown issue tracker 约定见 `docs/agents/issue-tracker.md`），没有规格覆盖的改动不进入实现。
- 每个事实只有一个 home：
  - 行为、验收与边界 → 规格（`.scratch/`）
  - 当前架构与不变量 → `CLAUDE.md`
  - 安装、配置、部署、升级 → `README.md`
  - 领域词汇 → `GLOSSARY.md`
  - 仍约束未来改动的决定 → `docs/adr/`
  - agent skills 的仓库配置 → `docs/agents/`
- 编号账本退役：F1–F28 与旧 plans / specs 删除，需要时从 git 历史查看（2026-11-29 之前）。
- 与既有 ADR 冲突的改动，先在规格里说明理由，并新增或更新 ADR，不静默覆盖。

## 后果

- 没有全局编号的验收清单；每个特性的验收写在自己的规格里。
- `CLAUDE.md`、`project-commit` 技能、`docs/agents/domain.md` 不再引用 `docs/intent.md`。
- 新术语先进 `GLOSSARY.md`，避免文档、规格与代码各自命名。

## 被否方案

- 保留编号账本、只补 ADR：三份手册仍然重叠，漂移问题不解决。
- 只保留 plans / specs：没有地方沉淀跨特性、仍约束未来改动的决定。
