# F12 生产服务改由用户级 systemd 管理

- 状态：已完成
- 日期：2026-10-15
- 范围：生产服务的进程管理方式、`run.sh` 的 systemd 集成、部署与回滚流程
- 前置依赖：现有系统级 unit、运行用户 `qiuqiu` 的 user systemd（`Linger=yes` 已开启）

## 背景与目标

当前生产服务由**系统级** systemd 管理：`run.sh prod` 把单元生成并安装到
`/etc/systemd/system/super-personal-platform.service`，重启与查状态依赖
`run.sh setup-sudo` 预先写进 sudoers 的三条受限规则。同一台主机上，Hermes 的
`hermes-dashboard`、`hermes-gateway` 和坚果云 `nutstore-mount` 都跑在 **user 级**
systemd（`~/.config/systemd/user/`）下，靠 `Linger=yes` 实现开机自启，日常管理完全不需要 sudo。

目标是让本项目的生产服务也迁到 user 级 systemd：重启、状态与日志查看不再需要 sudo，
并且与同机其它服务的管理方式一致。

另有一条已发生的事实需要一并修正：该单元目前是 `disabled` 且 `inactive`——9 月 21 日停机时被停用，
主机 10 月 11 日重启后没能自动恢复，导致服务静默下线约 24 天。迁移后由 linger 与
`default.target.wants/` 保证开机自启，不再依赖人工登录拉起。

## 用户故事

- 作为运维，我执行 `systemctl --user restart super-personal-platform` 就能重启服务，不需要 sudo，也不需要事先跑 `./run.sh setup-sudo`。
- 作为运维，主机重启后服务自动恢复，不需要有人登录才能把它带起来。
- 作为使用者，我在 web 的“生产更新”页面触发更新时，不再依赖主机上预置的受限 sudoers 规则。

## 功能点清单

1. `run.sh prod` 把服务单元生成并安装到 `~/.config/systemd/user/${SERVICE_NAME}`，不再写 `/etc/systemd/system/`。
2. 单元内容按 user 级调整：去掉 `User=` / `Group=`，`WantedBy=default.target`，`After=network-online.target` 并配 `Wants=`，保留 `Restart=always`、`RestartSec=5`、`KillMode=process` 与三个 `SUPER_PERSONAL_*` 环境变量。
3. 所有 `systemctl` 调用改为 `systemctl --user`；安装单元、`daemon-reload`、`enable`、`restart`、`status` 都不再需要 sudo。
4. 删除 `setup-sudo` 子命令与 `install_restart_sudoers()`；原来的 no-TTY sudo 预检改为 user systemd 管理器的可达性预检，`trigger_restart_by_service_exit()` 兜底路径一并移除——user systemd 下重启始终可用，该兜底不可能成功。
5. 首次启用时确保 user systemd 的 linger 已打开（`loginctl enable-linger`）；这是整个流程里唯一可能仍需要 sudo 的一次性动作。
6. 迁移时一次性停用并移除系统级单元，避免两个实例同时抢占 8888；该动作在目标机器上人工执行一次，`run.sh` 不保留系统级单元的检测或迁移代码。
7. `run-prod.sh` 与 `run-dev.sh` 保持不变，它们只是 `run.sh` 的薄壳。

## 验收标准

- 执行 `./run-prod.sh` 后，`systemctl --user is-active super-personal-platform` 为 `active`，且 `/etc/systemd/system/` 下不再存在本项目单元。
- `systemctl --user restart` 与 `systemctl --user status --no-pager` 全程不触发 sudo。
- 主机重启后服务自动恢复（linger 生效），不需要登录会话。
- 生产更新页触发的更新仍能完成重启，并分别汇报代码拉取、依赖安装、重启与服务状态结果。
- `./run.sh rollback <ref>` 与 `./run-prod.sh --no-pull` 的既有行为不变。

## 边界不做的事

- 不改成 Docker 或其它容器化方案，不引入新的进程管理器。
- 不改服务监听端口、工作区路径、环境变量语义与依赖安装流程。
- 不为历史系统级单元保留运行时兼容分支；按项目既有偏好做一次性迁移。

## 开放问题

- 迁移动作里移除系统级单元需要一次 sudo。2026-10-15 已按"人工在 TTY 下完成首次迁移"执行：在目标机器上停用并移除了系统级单元，之后所有路径都不再需要 sudo。
