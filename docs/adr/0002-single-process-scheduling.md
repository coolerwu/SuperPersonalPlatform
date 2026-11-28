# ADR-0002: 单进程调度

- 状态：Accepted
- 日期：2026-11-29

## 背景

单用户自托管。网页、微信、定时三条入口共用 `SessionRegistry`；定时 tick、微信长轮询、回测 worker、知识库同步与浏览器服务都运行在 API 进程内，状态存 SQLite 与内存。

## 决策

- 只启动一个 uvicorn 进程（生产 8888），不启用多 worker。
- 定时调度（`CronRuntime`，默认 20 秒 tick）、微信 `getupdates` 长轮询、`BacktestWorker` 与 `BrowserService` 都在 API 进程内。
- 同一 session 串行、不同 session 并发，并发上限 `BROWSER_CONCURRENCY`（默认 5，同时是浏览器标签页上限）。
- 启动时从 `running_sessions` 恢复持有 turn 租约（`has_turn=1`）的会话，其他记录只保留不续跑。

## 后果

- 部署多 worker 会重复触发定时任务、重复轮询微信、重复出队 turn；这是硬约束。
- 没有水平扩展；单机重启会短暂中断正在跑的 turn（靠租约 + resume 续跑）。
- 关机顺序必须是 registry → runner → browser → 其他服务，systemd `TimeoutStopSec=90` 覆盖整体预算。

## 被否方案

- 独立调度进程 / Celery / 消息队列：单用户场景引入跨进程协调与额外部署面，收益不成立。
