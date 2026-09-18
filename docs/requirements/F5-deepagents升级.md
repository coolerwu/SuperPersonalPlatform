# F5 DeepAgents 依赖升级

- 状态：已确认，实现中
- 日期：2026-09-18
- 范围：仅升级 `deepagents` 依赖版本与锁定口径，不改变平台行为契约
- 前置依赖：`pyproject.toml`、`docs/project-architecture.md` 的 DeepAgent 依赖约定

## 需求一句话

把平台依赖的 `deepagents` 从 `0.7.13` 升级到 PyPI 当前最新的 `0.7.15`，并在升级后保证现有
DeepAgent 运行时行为不变。

## 改动点

1. `pyproject.toml` 的 `deepagents` 约束从 `>=0.7.13,<0.8` 调整为 `>=0.7.15,<0.8`。
2. 开发环境与生产 `.venv` 安装到 `0.7.15`。
3. 平台实际使用的 deepagents 接口（backends、middleware、permissions、skills、subagents、
   `_fs_interrupt`）在 `0.7.15` 下仍可用；如有接口变动，按新版本调整运行时封装，不改变对外行为。
4. `docs/project-architecture.md` 中的依赖版本说明同步为 `0.7.15`。

## 验收标准

- `pyproject.toml` 声明的 `deepagents` 约束为 `>=0.7.15,<0.8`，本地 `.venv` 安装版本为 `0.7.15`。
- `server/tests` 全量通过，尤其是 `test_deepagent_runtime_filesystem.py`、`test_webdav_backend.py`、
  `test_system_prompt_tool.py`、`test_chat_groups.py`。
- 运行时仍能加载 skills、长期记忆、私有文件系统、WebDAV 映射、子 Agent 和 HITL 审批。

## 边界不做的事

- 不升级 `langchain*`、`langgraph*` 等其它依赖到新的大版本。
- 不为 `0.8` 的新特性做提前适配，也不放宽 `<0.8` 上界。
- 不修改 Agent 配置结构、Run 执行流程或前端行为。

## 开放问题

- 后续 `0.7.x` 再有补丁版本时，是否每次都需要单独需求文档（当前口径：同一小版本的补丁升级可并入本文档）。
