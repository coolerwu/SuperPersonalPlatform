# Agent Command Center

v1：网页对话助手（Chat + 多 Agent + Subagent + Checkpoint/Runs）。

- `apps/api`：FastAPI + deepagents + LangGraph
- `apps/web`：React + Vite + TypeScript
- `deploy/`：Docker Compose（Postgres + API + Web）

## 本地开发

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e "apps/api[dev]"

cd apps/api
../../.venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
cd apps/web
npm install
npm run dev
```

打开 `http://localhost:5173`，默认 token 见 `APP_AUTH_TOKEN`（默认 `dev-token`）。

本地聊天需要有效模型密钥：通过 `export LLM_API_KEY=...` 或在 `apps/api/.env` 配置；不要提交。也可以登录后在右侧 Settings 里填写。

## Docker Compose

```bash
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env，填入 APP_AUTH_TOKEN 和 LLM_API_KEY
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up --build
```

打开 `http://localhost:8080`。

## 配置

| 环境变量 | 说明 | 默认 |
| --- | --- | --- |
| `APP_AUTH_TOKEN` | 单用户访问 token | `dev-token` |
| `APP_DATABASE_URL` | SQLAlchemy 数据库 URL | `sqlite+aiosqlite:///./data/app.db` |
| `APP_DATA_DIR` | 数据/checkpoint 目录 | `./data` |
| `LLM_BASE_URL` | OpenAI-compatible 端点 | `https://api.deepseek.com` |
| `LLM_API_KEY` | 模型密钥 | 空 |
| `LLM_MODEL` | 模型名 | `deepseek-v4-flash` |

## 前端构建产物

`apps/web/dist` 提交到仓库。修改前端后先构建再提交：

```bash
cd apps/web && npm run build
```

生产 Web 镜像只把已提交的 `dist` 复制进 nginx，不在服务器上安装 Node/npm。

## V1 生产部署（GitHub pull）

v1 部署以 GitHub 的 `rewrite/v2` 分支为准，不从本地工作目录直接复制。

```bash
git push origin rewrite/v2
```

在目标机新建 v1 目录并拉取（不要覆盖旧的 legacy checkout）：

```bash
git clone --branch rewrite/v2 git@github.com:coolerwu/SuperPersonalPlatform.git ~/SuperPersonalPlatform-v1
```

```bash
cd ~/SuperPersonalPlatform-v1 && git rev-parse HEAD && git status --short
```

确认 HEAD 等于本地刚 push 的 SHA、工作区干净后：

```bash
cp deploy/.env.example deploy/.env
# 填 APP_AUTH_TOKEN、LLM_API_KEY、POSTGRES_PASSWORD、WEB_PORT
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
curl --noproxy '*' http://127.0.0.1:8080/api/health
```

旧生产服务不要动；v1 在 8080 端口验证通过后再切。

## 测试

```bash
.venv/bin/python -m pytest apps/api/app/tests
cd apps/web && npm run build
```

详细需求见 `docs/intent.md`；协作约定见 `CLAUDE.md`。
