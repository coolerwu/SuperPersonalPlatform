# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

默认使用中文回复用户。

`docs/project-architecture.md` 是本项目的架构说明、长期记忆和运行约定索引。

## 实现变更前的准备工作

开始实现任何变更前：

- 读取 `docs/project-architecture.md`。
- 执行 `git status --short`，确认当前未提交代码状态。
- 按用户当前偏好，本项目提交流程会提交所有未提交代码。

如果实现改变了架构、行为、命令、依赖、配置、公共接口或运维方式，必须同步更新 `docs/project-architecture.md`。

如果实现改变了 Agent 工作约定、提交流程、技能使用方式或长期协作规则，必须同步更新 `AGENTS.md`、对应技能文件，以及受影响的说明文件后，才能进入提交步骤。

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

Agent Chat uses LangGraph for execution and LangChain for OpenAI-compatible LLM adapters.

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
1. Reads `AGENTS.md` and `docs/project-architecture.md`
2. Runs `git status --short`
3. Updates documentation if behavior/architecture changed
4. Runs tests: `.venv/bin/python -m pytest`, `cd web && npm test`, `cd web && npm run build`
5. Stages all with `git add .`
6. Commits and pushes

## Frontend Design Notes

- 整体前端默认对齐主流 Hermes / OpenClaw 类 Agent command center 风格：深色运行台、清晰导航、工具/任务/日志/终端面板、明确状态标识和低噪声操作流。不只是调整颜色和间距，而要从信息组织、页面结构、操作入口和状态呈现上统一设计；避免临时后台式卡片堆砌。
- 菜单页右侧内容区不要添加重复的全局页面标题栏、面包屑式说明或"图标 + 页面名 + 描述"的 header，例如"运行概览 / 首页"这类块不需要。
- 侧栏已经表达当前位置；右侧内容应直接展示当前功能的实际工作区。只在功能内部保留必要的局部工具条、tab、状态栏或表单分组。
- Workbench pages use 100% width with `100dvh` fallbacks
- Production uses committed `web/dist/` build output; run scripts do not build frontend assets
