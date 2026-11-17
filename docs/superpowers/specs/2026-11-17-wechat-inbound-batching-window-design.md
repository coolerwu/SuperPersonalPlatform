# 微信入站打包窗口调整设计

- 日期：2026-11-17
- 状态：已实现（2026-11-17）
- 关联模块：`apps/api/app/config.py`、`apps/api/app/services/weixin_runtime.py`
- 关联需求：`docs/intent.md` F6 微信个人号渠道

## 背景

入站打包的现状：

- 同一联系人的连续消息在静默窗口内合并成一个 turn：默认**静默 5 秒**触发 flush，**总时长上限 15 秒**。
- 微信里图片、文字、文件只能分条发送，用户连续发送时经常间隔数秒，5 秒窗口下容易拆成多轮，前一轮可能只收到半句话就开跑。

原需求曾考虑「新消息中断正在执行的旧任务」，经确认**不打断**：排队语义已由 `SessionRegistry` 提供（每会话串行、同一 transcript、新消息在上一轮结束后按序执行），不需要额外实现。本次只调整打包窗口：**改为静默 6 秒触发 flush，取消总时长上限**，让分条消息有足够时间合并成一轮。

## 目标

1. `channel_inbound_batch_seconds` 默认 5.0 → **6.0**（最后一条消息后静默 6 秒即 flush）。
2. `channel_inbound_batch_max_seconds` 类型改为 `float | None`，默认 **`None`（无总时长上限）**；显式配置数值时仍作为总时长上限生效，`0` 仍表示「立即 flush」。
3. 8K 字超长强制立即 flush、`batch_seconds <= 0` 关闭打包、命令不进打包，行为不变。

## 非目标

- 不中断任何正在执行的 turn；不新增 registry 方法，不改 `channel_intake`。
- 不加「排队中」回执。
- 不改投递、命令、网页聊天、重启恢复的行为。

## 现有约束

- 打包参数来自 pydantic-settings（`apps/api/app/config.py`）：环境变量未设置时取默认值；`.env` 的 `$APP_HOME/.env` 与启动目录 `.env` 可覆盖。
- `WeixinRuntime._flush_after` → `flush_pending` 负责把批次落成一个 turn；批次期间不持久化 `sync_buf`，崩溃后由 iLink 重放、入站幂等表吸收。
- 批次大小超过 `channel_inbound_max_chars`（默认 8000 字）时立即 flush。
- 每条新消息都会重置静默计时；`channel_inbound_batch_max_seconds` 是防止持续输入导致批次迟迟不发的总上限。

## 设计

### 1. 配置（`config.py`）

- `channel_inbound_batch_seconds: float = 5.0` → `6.0`。
- `channel_inbound_batch_max_seconds: float = 15.0` → `float | None = None`。
  - 环境变量未设置 → `None`（无上限）；
  - 显式配置数字 → 照旧生效（兼容既有 `.env`）；
  - 显式配置 `0` → 立即 flush（语义不变）。

### 2. 运行时（`weixin_runtime.py`）

- `__init__` 中 `batch_max_seconds` 的解析：settings 值为 `None` 时 `self.batch_max_seconds = None`；数值时保持 `max(0.0, value)`（`0` 仍为立即 flush）。构造参数维持现有语义：`None` 表示从 settings 读取，测试需要显式上限时传数值。
- `_schedule_flush`：无上限时 `delay = batch_seconds - (now - last_at)`；有上限时维持现有 `min(batch_seconds - since_last, max_seconds - since_first)`。
- 其它路径（超长立即 flush、`batch_seconds <= 0`、命令前先 `flush_pending`）不变。

### 3. 行为影响

- 单条消息的回复会比现在晚约 1 秒（6 秒静默 vs 5 秒）。
- 无总上限意味着用户持续以小于 6 秒的间隔发消息时，批次会一直等待，直到出现 6 秒静默或累计超过 8000 字才发出；8K 上限是唯一的强制兜底。

## 测试

- `test_weixin_runtime.py`：
  - 默认配置下静默 6 秒 flush；
  - 每条新消息重置计时（第 5 秒又来一条 → 从最后一条起再等 6 秒）；
  - 无总上限：持续发送超过原 15 秒也不强制 flush（直到出现静默或超长）；
  - 显式配置 `batch_max_seconds` 时仍在该上限强制 flush；
  - 超长（>8000 字）立即 flush；
  - `batch_seconds=0` 仍立即 flush。
- 既有依赖 `5.0` / `15.0` 默认值的测试同步更新。

## 文档同步

- `docs/intent.md` F6：打包窗口改为「静默 6 秒、无总时长上限」，并注明 8K 超长兜底。
- `CLAUDE.md`「渠道」条目同步上述变化。
- 前端与部署无变化，`README.md` 无需更新。

## 已知取舍

- 回复固定晚约 6 秒；这是打包合并分条消息的代价，用户已接受。
- 无总上限时持续输入可能让 turn 迟迟不触发；8K 字上限与用户最终会停止输入共同兜底。
