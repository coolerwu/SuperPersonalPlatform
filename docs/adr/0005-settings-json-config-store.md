# ADR-0005: 平台配置只写 data/settings.json

- 状态：Accepted
- 日期：2026-11-29（决定来自 2026-11-11 SDK 迁移）

## 背景

v0 把 Provider、Agent、渠道、知识库配置存在数据库表（`providers` / `agents` / `channels` / `app_configs` / `knowledge_configs`），每次改配置都要迁移或写表结构。配置本质上是单用户的一份小文档。

## 决策

- 平台配置只有一个事实来源：`~/.super-personal-platform/data/settings.json`，含 `runtime`、`knowledge`、`maps`、`asr`、`agents`、`channels`、`channel_conversations`。
- 写入：单 `asyncio.Lock` → 临时文件 → fsync → `os.replace`；写前保留 `.bak`；主文件损坏时回退 `.bak`。
- 密钥只存在后端文件里：接口只返回 `has_api_key` / `has_password` / `has_credentials`；日志与前端响应不出现明文。
- `.env` 只在首次种子时复制到 `settings.json`（默认 Agent、保留天数、知识库），文件存在后以文件为准，平台不回写 `.env`。
- `claude/settings.json` 是 Claude Code 自身配置（清理天数、插件启用状态），与平台配置无关；两者不要混用。

## 后果

- 配置不进数据库，没有迁移；新库 `app.db` 不创建配置表。
- 修改 Agent / Provider / 知识库 / 地图 / 语音后，按配置指纹在下一轮重建缓存 client。
- 渠道凭据与联系人绑定随 `settings.json` 一起备份；重建 `app.db` 不影响微信绑定。

## 被否方案

- 保留数据库配置表：单用户场景引入 schema 与迁移成本，且与「配置文件可手改、可备份」的使用习惯冲突。
