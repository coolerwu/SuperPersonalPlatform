# Project Architecture

本文件是当前项目架构入口。DeepAgent 重构的详细决策记录在 `docs/architecture-qa.md`，后续实现以该问答中的模型为准。

## Current Direction

- 项目仍是一个由 FastAPI 提供后端、React + Vite 提供前端、后端统一服务静态资源的单体应用。
- 后端 Python 版本目标为 3.12.x，默认监听端口为 `8888`。
- 运行入口仍是 `./run-dev.sh`、`./run-prod.sh` 和底层 `./run.sh`。
- 生产环境仍使用已提交的 `web/dist`，生产启动不现场构建前端。
- 工作区默认仍是项目目录下 `.super-personal-platform`，可通过 `--workspace` 覆盖。
- 目标产品边界收缩为：DeepAgent/LangGraph 后端任务执行、微信个人号入口、坚果云 WebDAV Context、单 token 登录、基础配置、基础日志和生产更新。

## Architecture Q&A

`docs/architecture-qa.md` 是本轮重构的产品和技术问答来源，已确定：

- 前端不使用 WebSocket 聊天，不执行 Agent，只创建任务并轮询后端 API。
- DeepAgent 在后端运行，状态、事件、结果全部落盘。
- `workspace/runs/index.json` 维护所有 run 的摘要和当前状态。
- 每个 run 使用 `workspace/runs/{run_id}/` 独立目录保存 `input.json`、`state.json`、`events.jsonl`、`result.json`、`lock.json` 和 `delivery.json`。
- `Agent` 只保存人格、模型和绑定的 Context 列表。
- `Context` 是隔离边界，内部包含 roots、tools、knowledge、owner、scope 等配置。
- `Knowledge` 是 Context 内部资源，不作为全局散放目录。
- Run 创建时必须固化 Agent + Context + Knowledge 快照。
- 微信收到消息后创建 `source=wechat` 的 run；DeepAgent 完成后由平台投递微信回复。

## Target Workspace Layout

```text
workspace/
  config.yaml

  agents/
    index.json
    {agent_id}/
      agent.json

  contexts/
    index.json
    {context_id}/
      context.json
      knowledge/
        index.json
        files/
      state/
        embeddings/
        cache/

  runs/
    index.json
    {run_id}/
      input.json
      state.json
      events.jsonl
      result.json
      lock.json
      delivery.json

  channels/
    wechat/
      accounts.json
      sessions/

  logs/
    platform-YYYY-MM-DD.log
```

## Target Backend Boundaries

- `server/adapter` 只保留薄 HTTP API：认证、run 创建/查询/事件轮询、微信账号管理、配置/日志/更新、静态资源。
- `server/app` 负责应用服务：DeepAgent run 服务、Agent/Context 工作区服务、坚果云服务、微信通道服务、系统日志/更新服务。
- `server/domain` 只保留框架无关的领域对象、配置规则、错误类型和 run/context/agent 数据约束。
- `server/infrastructure` 负责配置加载、DeepAgent/LangChain 模型运行、坚果云 WebDAV、微信 iLink 客户端、FastAPI app 装配和 cookie session。

## Target API Shape

Run API：

```text
POST /api/runs
GET /api/runs
GET /api/runs/{run_id}
GET /api/runs/{run_id}/events?after={seq}
```

微信 API 继续保留 `/api/channels/wechat/*` 账号管理和登录生命周期接口。

系统 API 只保留日志和生产更新能力；配置文件读写统一走 Workspace 文件 API，可视化配置页也复用同一个读写入口。

Workspace 文件 API：

```text
POST /api/workspace/list
POST /api/workspace/read
PUT /api/workspace/write
POST /api/workspace/delete
```

这些接口只允许访问 active workspace 内部路径，用于前端“工作目录”页面浏览、编辑 UTF-8 文本文件和删除非固定路径。`config.yaml` 通过写入入口保存时仍执行配置校验；`config.yaml` 和根层固定目录 `agents/`、`contexts/`、`runs/`、`channels/`、`logs/` 不能删除，其它 workspace 内文件或目录允许删除。

## Frontend Routes

- `/`, `/runs`, `/agents` 都进入新的 Runs 工作区；`/agents` 只是旧入口兼容，不恢复旧 Agent Chat/Agent 管理页面。
- `/workspace` 展示真实 workspace 文件浏览器，可查看和编辑 UTF-8 文本文件，并可删除非固定路径；`config.yaml` 在这里按原生 YAML 文本展示和编辑，不承载专用配置表单；`config.yaml` 和根层固定骨架目录不可删除。
- `/config` 是 `config.yaml` 的可视化配置菜单，读取 active workspace 的同一份 YAML，保存仍写回 `workspace/config.yaml` 并经后端配置校验。
- `/wechat` 展示微信账号列表、当前账号详情、二维码、运行态、绑定 Agent、投递路径和通道日志，并提供启动/停止操作。
- `/system` 是运维页，只展示生产更新、工作目录入口和系统日志；不再承载系统配置编辑或架构说明。配置表单入口收敛到 `/config`，文件级查看/编辑入口保留在 `/workspace`。
- 前端是运行台，不做营销首页；第一屏直接展示可操作的后端 run 工作区。
- Runs 工作区通过 1 分钟一次的轮询读取后端落盘状态，但前端必须保留当前详情快照、只在返回内容实际变化时更新状态，避免每次拉取 `workspace/runs/index.json` 时出现短暂重刷或 `unknown` 状态闪动。

## Retained Capabilities

- 单 token 登录，登录状态通过 HttpOnly cookie 保存。
- 配置从 active workspace 的 `config.yaml` 读取。
- 个人微信通过 Tencent iLink Bot HTTP API 接入。
- 坚果云通过 WebDAV 接入，默认 endpoint 为 `https://dav.jianguoyun.com/dav/`。
- DeepAgent 依赖 `deepagents` 和 LangGraph；后端任务执行结果必须落盘。
- 系统日志继续写入 `workspace/logs/platform-YYYY-MM-DD.log`。

## Removed From Target Architecture

以下旧功能不再作为目标架构保留；删除对应代码时必须同步清理文档、测试、前端入口和配置模板：

- 旧 Agent Chat WebSocket 聊天。
- 旧 Harness 严格状态机产品路径。
- Prompt/Agent 双模式产品概念。
- 旧 Session CRUD 和 `workspace/sessions/*` 会话模型。
- 旧 Skill 管理和 Skill 作为产品级概念。
- Portfolio 投资组合模块。
- Critique 多维批判模块。
- Proxy 嵌入站点模块。
- Agent command center 里围绕旧 Agent/Skill/Model 管理构建的复杂 UI。

## Documentation Cleanup Rule

- 主架构入口只描述当前目标架构，不再保存已决定删除模块的完整行为说明。
- `docs/architecture-qa.md` 保存本轮架构问答和设计决策。
- 旧 `docs/superpowers/*` 计划/规格和旧多维批判设计图已经删除；后续删除旧代码模块时，必须同步删除或归档对应文档，避免搜索结果继续指向旧架构。
- 如果实现改变架构、行为、命令、依赖、配置、公共接口、运维方式或长期协作规则，必须同步更新本文件和相关说明。

## Operating Notes

- `AGENTS.md` 是仓库级 Codex 指令入口。
- `config.example.yaml` 是 workspace 配置模板，不得放入真实密钥。
- `config.yaml` 属于本地 workspace 数据，不提交。
- 开发启动使用 `./run-dev.sh` 或 `./run.sh dev`。
- 生产启动使用 `./run-prod.sh` 或 `./run.sh prod`。
- `./run.sh setup-sudo` 仍用于安装受限 sudoers 规则，使生产服务能无密码执行受限的 `systemctl restart/status/is-active super-personal-platform.service`。
- 提交项目前必须执行 `.codex/skills/project-commit` 工作流。
