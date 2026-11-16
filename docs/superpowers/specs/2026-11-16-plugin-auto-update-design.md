# 插件每周自动更新设计

- 日期：2026-11-16
- 状态：待实施
- 关联需求：F25 插件每周自动更新
- 关联实现：`apps/api/app/services/plugins.py`、新增 `apps/api/app/services/plugin_updates.py`

## 背景

插件页目前只能手动更新官方集市和单个插件。用户希望「每周自动更新已有插件」，减少手动操作。

调研 Claude Code 2.1.149 后确认：

1. Claude Code 有内置的插件自动更新（marketplace 条目的 `autoUpdate`），但它在**交互式会话**发出第一条消息后随机等待最多 10 分钟执行一次，不是 cron 式的周调度；SDK/非交互会话不保证触发。
2. `claude plugin update` 只能一次更新一个插件，没有 `--all`。
3. `claude plugin marketplace update` 只刷新集市清单，不会升级已安装插件。
4. 更新在下一轮会话或 `/reload-plugins` 后生效。

因此「每周」这个节奏只能由平台自己排程；平台也不应同时打开 Claude 自带的 `autoUpdate`，否则会出现两套更新路径。

## 目标

- 插件页提供一个全局开关：每周自动刷新官方集市并更新其下已安装插件。
- 默认关闭；失败可观测、可重试；不影响正在执行的 turn。
- 不新增依赖、不改数据库、不引入外部定时器（systemd timer 等）。

## 非目标

- 不按 Agent 配置，不做自定义星期/时间。
- 不更新非官方集市（平台本来也只接入官方集市）。
- 不实现「立即更新」按钮以外的批量操作界面；复用现有单插件「更新」按钮。
- 不启用 Claude 自带的 marketplace auto-update。

## 方案

### 调度

新增进程内 `PluginAutoUpdateRuntime`，与 cron、知识库同步、渠道轮询并列，在 API 进程 lifespan 中启动和停止。

- 每 `plugin_auto_update_tick_seconds`（默认 3600 秒）检查一次。
- 判定「到期」只依赖 `settings.json` 中持久化的 `plugin_updates` 节，因此重启不会丢节奏，也不会每次重启都跑一次。
- 启用且从未执行：立即到期。
- 上次尝试成功：7 天后到期。
- 上次尝试有错误：1 天后重试。
- 本轮到期但插件锁被页面操作占用（`PluginBusyError`）：不记录尝试，等下一次检查。

### 执行

新增 `plugins.auto_update_installed()`，在同一个配置锁内依次执行：

1. 读取当前插件目录；没有官方集市时抛 `PluginNotFoundError`。
2. `claude plugin marketplace update <official>` 刷新清单。
3. 重新读取已安装列表，逐个对官方集市下的插件执行
   `claude plugin update <id> --scope user`。
4. 单个插件失败只记录，不中断其他插件。
5. 至少有一个插件更新成功时写 `.plugins-revision`，让下一轮对话重建 client。

已停用的插件也更新：它们仍属于「已有插件」，重新启用时应当是新的。

### 状态与 API

`settings.json` 新增 `plugin_updates` 节：

```json
{
  "plugin_updates": {
    "enabled": false,
    "last_attempt_at": "",
    "last_success_at": "",
    "last_summary": "",
    "last_error": ""
  }
}
```

- 时间一律 ISO 8601 UTC 字符串。
- `last_summary` / `last_error` 截断到 500 字符，避免 JSON 膨胀。
- 关闭开关保留历史值。

API：

- `GET /api/plugins/auto-update` → 状态 + `interval_days: 7` + 计算出的 `next_attempt_at`。
- `PUT /api/plugins/auto-update` body `{"enabled": bool}`；开启时要求官方集市已存在（否则 409），并唤醒运行时立即检查。

### 前端

插件页在「官方集市」卡片之后增加「自动更新」卡片：

- 开关（默认关）。
- 启用后显示上次尝试时间、结果摘要、下次计划时间；有错误时显示错误摘要。
- 开关通过单独请求即时保存，不要求点击保存按钮。

### 并发与生效

- 自动更新与页面插件操作共用 `config_lock`，互斥。
- 更新成功后 `.plugins-revision` 变化，运行器在下一轮按插件指纹重建缓存 client；正在执行的 turn 不受影响。

## 备选方案

### A. 直接打开 Claude 的 `autoUpdate`

- 优点：零平台代码。
- 缺点：每次交互式会话启动都可能刷新 GitHub；SDK 会话是否触发没有保证；没有「每周」语义；无法在平台页面展示状态和错误。
- 结论：不采用；平台保留自有调度，并保持 `autoUpdate` 关闭。

### B. systemd timer 调脚本

- 优点：不依赖 API 进程。
- 缺点：状态无法回到页面；部署多一台机器多一份配置；与现有进程内 runtime 模式不一致。
- 结论：不采用。

### C. 复用 Agent 定时任务

- 缺点：cron_jobs 是 Agent turn，插件更新不是 Agent 工具；需要给 Agent 新增工具或平台特殊任务类型，侵入更大。
- 结论：不采用。

## 风险

- 自动更新会在无人值守时替换插件代码（含 hooks/MCP），平台没有沙箱。默认关闭，并在页面保留现有安全提示。
- 批量更新可能持续数分钟并持有插件锁，期间页面插件操作返回 409；错误文案沿用现有「另一个插件操作正在进行」。
- GitHub 不可达时本轮失败，1 天后重试；不影响聊天与已有插件版本。

## 验收

见 `docs/intent.md` F25 与 `docs/superpowers/plans/2026-11-16-plugin-auto-update.md`。
