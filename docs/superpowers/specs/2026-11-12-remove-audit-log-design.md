# 删除审计日志设计

- 日期：2026-11-12
- 状态：设计已确认，待评审后进入实现计划
- 范围：`audit_log` 表、三处写入点、相关测试与文档；不新增任何写入方或页面，不引入新依赖，不做数据迁移

## 1. 背景与目标

`audit_log` 是 v1 重建时保留的审计表，用于记录定时任务跳过/触发失败、重启时网页 turn 丢失三类系统事件。本轮盘点发现它是**全后端唯一的「只写不读」表**：没有任何 API 读取它，前端没有引用，三个相关测试只是断言它被写入。

这些信息要么已有更好的替代，要么可以直接进服务日志：

- 定时任务因会话忙跳过：`cron_jobs.last_skipped_at` 已由定时任务页展示（`apps/web/src/components/CronPage.tsx` 的「上次触发因会话忙被跳过」），审计行是重复记录；
- 定时触发提交失败：「立即运行」与调度循环两处调用点本来就有 `logger.warning(..., exc_info=True)`，审计行是重复记录；
- 网页 turn 在重启时丢失：渠道侧有「服务重启时这条消息尚未开始处理，请重新发送」的投递兜底文本；网页侧没有用户可见的补救，审计行只对开发者有追溯价值，改为日志即可。

目标：删除这张死表及其全部写入路径，让 `app.db` 只剩真正被读写的五张表，减少无消费方的代码与数据。

成功标准：

- 代码中不再存在 `AuditLog` 模型、审计写入与审计断言；
- 新库 `app.db` 只创建五张表；
- 定时任务跳过、触发失败的可见性不降低（前者看页面，后者看日志）；
- 后端测试与前端构建通过。

## 2. 现状核对

| 位置 | 内容 | 处理 |
|---|---|---|
| `apps/api/app/models.py` | `AuditLog` 模型（actor / action / entity / entity_id / created_at） | 删除 |
| `apps/api/app/services/cron_service.py` | `prepare_trigger` 中的 `cron_skipped` 写入、`_record_trigger_failure` 中的 `cron_trigger_failed` 写入 | 删除写入与 `_record_trigger_failure` 函数；两处调用点的 `logger.warning` 已存在，保留不动 |
| `apps/api/app/services/recovery.py` | `_audit_lost_turn` 与 `_drop_lease` 中的调用 | 删除函数，调用处改为 `logger.warning` |
| `apps/api/app/tests/test_recovery.py` | `test_recovery_audits_a_lost_web_turn` | 改名为「网页丢轮被记录为日志」，断言 `logger.warning`（`caplog`）出现且不产生数据库行 |
| `apps/api/app/tests/test_cron_service.py` | `test_busy_job_is_skipped_and_audited`、`test_submit_failure_consumes_schedule_and_audits` 中的审计断言 | 删除审计断言，保留跳过/消费计划/异常等行为断言；两个测试函数名去掉 `audited` |

确认无读取方：`grep -rn "AuditLog" apps/api/app` 仅命中模型、两处服务写入与测试；`apps/web/src` 无任何 `audit` 引用。

## 3. 变更内容

### 3.1 代码

1. 删除 `models.py` 的 `AuditLog` 类。
2. `cron_service.py`：删除 `cron_skipped` 的 `AuditLog(...)` 写入（跳过逻辑与 `last_skipped_at` 赋值不变）；删除 `_record_trigger_failure` 函数及两处调用（调度循环与 `run_job_now`），失败路径继续沿用现有 `logger.warning`；同步更新 `prepare_trigger` docstring 中「skipped (and audited)」与 `run_job_now` 中「A later submit failure is audited」的注释。
3. `recovery.py`：删除 `_audit_lost_turn`；`_drop_lease` 中「没有投递行」的分支（原调用点）改为 `logger.warning("web turn lost on restart session=%s", session_id)`，其余逻辑不变。

### 3.2 测试

- `test_cron_service.py`：两个测试去掉 `AuditLog` 查询断言并去掉函数名中的 `audited`；「忙时跳过」继续断言未提交 turn 且 `last_skipped_at` 被写入；「提交失败」继续断言计划被消费、`CronTriggerFailed` 抛出。
- `test_recovery.py`：改名为断言日志的测试，检查 `caplog` 中出现 `web turn lost on restart`，且 `_drop_lease` 后租约被删除；不再查询 `audit_log`。

### 3.3 文档

- `docs/intent.md`：末尾追加 F18（编号水位 F17 → F18）；修正既有措辞——F2 功能点 5、F7 功能点 4/5 与验收 215 行、F15 的「六张表」与重建说明中的「审计」。
- `CLAUDE.md`：第 18 行「六张表」改为五张表（`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`）；第 20 行「网页来源写 `audit_log`」改为写日志；第 24 行「会话忙时跳过并写 `audit_log`」改为「跳过并记录 `last_skipped_at`」。
- `README.md`：第 9 行表清单、第 69 行重启恢复、第 172–173 行定时任务、第 250 行表说明、第 386 行重建说明同步。

## 4. 数据与兼容

- 不写数据迁移脚本。新库只按 metadata 建五张表；已存在的库中 `audit_log` 空表原样保留，平台不再读写——与「升级库保留未使用的旧表」的既有约定一致。
- 删除 `data/app.db` 彻底重建时，丢失项从「回测历史、定时任务、审计、投递、入站去重」变为少一项审计，文档同步修正。
- 无前端改动，无 API 改动，无新依赖。

## 5. 验收标准

1. `grep -rn "AuditLog\|audit_log" apps/api/app` 不再命中模型、写入或测试断言（文档中的历史说明除外）。
2. `.venv/bin/python -m pytest apps/api/app/tests` 全部通过。
3. `cd apps/web && npm run build` 通过。
4. 新库启动后 `app.db` 只有五张表：`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`。
5. 定时任务忙时跳过仍更新 `last_skipped_at` 并在定时任务页可见；提交失败仍有 warning 日志；网页丢轮仍有 warning 日志。

## 6. 取舍与风险

- 唯一失去的能力：网页来源 turn 在重启时丢失后的持久记录。它的实际用途只是事后排查，改为日志后信息仍在，只是不落在数据库里；单用户场景可接受。
- 若将来需要「变更历史/审计」能力（例如记录配置变更、会话删除），应作为新需求（新的表与页面）重新设计，不复用这张死表——本设计不为此预留兼容层。
