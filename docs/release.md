# 版本、发布与回滚

本文约定项目如何编号、如何发布到生产、以及出问题时如何回滚。运行命令与工作区
约定见 `docs/project-architecture.md`，变更记录见 `CHANGELOG.md`。

## 版本号

- 版本号写在 `pyproject.toml` 的 `project.version`，形式为 `MAJOR.MINOR.PATCH`。
- 尚未发布时版本号表示"开发线"，不对应任何 tag；`CHANGELOG.md` 顶部保持
  `[Unreleased]` 段落记录已合并但未发布的改动。
- 发布时在 `CHANGELOG.md` 把 `[Unreleased]` 内容落成 `[X.Y.Z] - YYYY-MM-DD`，
  同步更新 `pyproject.toml` 版本，再打 tag。
- 版本判定：破坏既有工作区数据或公共接口 → `MAJOR`；新增功能 → `MINOR`；
  修复与内部调整 → `PATCH`。
- tag 命名为 `vX.Y.Z`，与 `pyproject.toml` 版本一致；tag 只标注已成功部署到生产的提交。

## 发布流程

生产是"单机 + systemd + 已提交 `web/dist`"的单体部署，版本即 `main` 的提交：

1. 本地完成实现与文档同步，执行项目 `$project-commit` 流程要求的检查：
   `.venv/bin/python -m pytest`、`cd web && npm test`、`cd web && npm run build`。
2. `git add .` 后提交，`git push origin HEAD`，记录 `git rev-parse HEAD`。
3. 远端 `SuperPersonalPlatform/` 执行 `git -c http.version=HTTP/1.1 pull`，确认远端
   `git rev-parse HEAD` 与本地刚推送的 HEAD 完全一致。
4. 远端同步运行时依赖：`.venv/bin/python -m pip install .`，必要时执行
   `PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright} .venv/bin/python -m playwright install chromium`。
5. `sudo -n systemctl restart super-personal-platform.service`，再用非 sudo
   `systemctl is-active/status` 验证。
6. 需要留痕的发布按"版本号"一节打 tag 并推送。

前端只在开发与构建阶段需要 Node.js 20+；生产不现场构建，直接使用仓库中已提交的
`web/dist`。

## 回滚

回滚 = 把生产检出切到上一个可用版本，再用同一套依赖安装与重启流程重新部署。

```bash
# 生产主机
cd SuperPersonalPlatform
./run.sh rollback v0.1.0 --workspace /path/to/workspace
# 或指定提交
./run.sh rollback <commit-sha> --workspace /path/to/workspace
```

行为与边界：

- 目标必须是本地已存在的 tag、分支或提交；脚本会先 `git fetch --tags`，解析不到目标
  时不做任何改动直接失败。
- 工作区必须干净；未提交改动会让脚本拒绝执行。
- 脚本切到目标版本（detached HEAD）、跳过 `git pull`，然后安装该版本的依赖并重启服务。
- 不需要重新构建前端：回滚到旧提交会同时回滚 `web/dist`。
- 回到主线只需再执行一次 `./run-prod.sh`：`update_git` 检测到 detached HEAD 会先
  切回生产分支再拉取。
- 回滚不会回退工作区数据。`workspace/` 中的 Run、session、checkpoint 和群聊状态是
  向前兼容的运行时数据，回滚代码前必须确认目标版本能读取现有数据；做不到时先备份
  `workspace/` 再回滚。

## 已知缺口

以下内容尚未实现，属于已登记的架构缺口，不是"已经具备"：

- 没有自动回滚：回滚是人工执行的运维动作，没有部署失败自动回切。
- 没有灰度或蓝绿发布：一次只有一份生产进程，重启期间服务中断。
- 没有真浏览器端到端测试和并发/压力测试；前端测试跑在 jsdom，后端没有覆盖率门禁。
- 没有 `workspace/` 备份与恢复流程，回滚前需要人工备份。
