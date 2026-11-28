# ADR-0006: systemd + venv + SQLite 部署

- 状态：Accepted
- 日期：2026-11-29

## 背景

单用户自托管，生产是一台宿主机上的一个服务。早期版本使用 Docker Compose + nginx + Postgres，部署面与维护成本都高于收益。

## 决策

- 生产：宿主机用户级 systemd 服务（`agent-command-center.service`）+ Python venv + SQLite，单进程监听 8888。
- FastAPI 直接托管提交进仓库的 `apps/web/dist`（SPA fallback 到 `index.html`）；生产机不安装 Node/npm，也不构建 Web 镜像。
- 代码从 GitHub `main` 拉取；用户级服务依赖 linger，否则退出登录后进程被回收。
- 关机协议：`KillMode=mixed` + uvicorn `--timeout-graceful-shutdown 10`，`TimeoutStopSec=90`；SIGTERM 只发给 uvicorn，让应用先取消 turn、保留租约再退出。`close_all` 每个 client 限时 30 秒、总预算 35 秒。
- 数据在 `~/.super-personal-platform/`；升级前备份 `data/app.db`、`data/settings.json`、`claude/` 与 `workspaces/`。回滚以提交 SHA 为准：checkout + 重启服务，不回退 SQLite 数据。

## 后果

- 生产机需要 Python 3.12 venv、Playwright Chromium 与 ffmpeg（语音输入）。
- 不引入 Dockerfile / nginx / Postgres；不要再加回来。
- 单机故障没有高可用；重启期间正在跑的 turn 由租约 + resume 续跑。

## 被否方案

- Docker Compose + nginx + Postgres：对单用户单机是额外维护面，已移除。
