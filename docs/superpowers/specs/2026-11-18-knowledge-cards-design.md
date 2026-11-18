# 知识卡片（随机知识库卡片 + 轻量复习）设计

- 日期：2026-11-18
- 状态：设计已确认，待实现
- 关联模块：`apps/api/app/services/knowledge_cards.py`（新增）、`apps/api/app/routers/knowledge_cards.py`（新增）、`apps/api/app/models.py`、`apps/api/app/schemas.py`、`apps/api/app/main.py`、`apps/api/app/config.py`、`apps/web/src/routes.ts`、`apps/web/src/components/Sidebar.tsx`、`apps/web/src/components/KnowledgeCardsPage.tsx`（新增）、`apps/web/src/App.tsx`、`apps/web/src/api.ts`、`apps/web/src/types.ts`、`apps/web/src/styles.css`
- 关联需求：`docs/intent.md` F28 知识卡片（新增）、F9 知识库镜像

## 背景

- 知识库现状：F9 的 `KnowledgeSyncRuntime` 在 API 进程内把 WebDAV 目录双向镜像到 `knowledge/`，平台没有浏览页面，也不挂载给 Agent。本地开发机目录可能为空，生产机有实际内容。
- 用户需求：做一个独立页面，从知识库随机抽内容做成问答卡片。目的三合一——回看复习、问答式学习、随机偶遇（灵感）。
- 已确认的关键决定：
  - 内容用 AI 实时加工（抽到内容后现场生成问答卡）；
  - 入口是独立新页面；
  - 复习机制用轻量复习池（不做 Anki 式四档评分 / SM-2 / FSRS）；
  - 抽取粒度是「随机文件 → 随机小节」；
  - **不做收藏**。
- 技术现状：应用单进程；Provider 按 Agent 配置（`settings.agents[].provider`，无全局 Provider），当前唯一 Agent `default/Assistant` 使用 `https://api.deepseek.com/anthropic`、模型 `deepseek-v4-flash`；后端已有 `httpx`；ASR 服务就是直接用 httpx 调外部接口的先例。
- `README.md` / `docs/intent.md` 约定：新库只建 `cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions` 五张表；本设计新增第六张表，属于 schema 变更，已在本轮讨论中确认。

## 目标

1. 新页面 `/knowledge`：每次抽一张来自 `knowledge/` 笔记的问答卡，正面看问题、点击翻面看答案，动作有「换一张 / 不会 / 会了」。
2. 卡片一次生成、落库复用：「会了」间隔按 3 → 8 → 20 → 50 天递增（上限 60 天），「不会」重置为 1 天；到期卡约 70% 概率优先出现，其余抽未见小节做新卡。
3. 页面顶部显示「待复习 / 未看 / 今日已复习」三个统计。

## 非目标

- 不做收藏、收藏列表、原文跳转/编辑、卡片手动编辑、导出。
- 不做 Anki 四档评分、SM-2 / FSRS 算法。
- 不做按目录/标签筛选、批量预生成、流式生成、多语言选择。
- 不把知识库挂载给 Agent，不改 F9 同步逻辑，不新增知识库相关 Agent 工具。
- 不新增 Python / npm 依赖，不新增环境变量，不改部署架构。

## 现有约束

- 平台配置只写 `data/settings.json`；密钥只存文件，不返回明文、不进日志。
- Provider 按 Agent 配置，没有全局 Provider；卡片生成取 `settings.agents` 中第一个 `status == "active"` 的 Agent 的 `provider`。
- 时间统一存 UTC ISO-8601 字符串，展示按服务器本地时区。
- 前端路由是自建 History API（`apps/web/src/routes.ts`）；非聊天页面用 `React.lazy` 分包；侧栏「Agent 能力」菜单当前含 技能 / 工具 / 插件 / 渠道 / 定时 / 记忆。
- 前端没有单元测试运行器，验证以 `tsc` / vite 构建和浏览器实测为准。
- `init_db()` 每次启动执行 `Base.metadata.create_all`，新增模型会自动在旧库补建表。

## 设计

### 1. 切节扫描（`services/knowledge_cards.py`）

- 根目录：`settings.knowledge_dir`（`APP_KNOWLEDGE_DIR` 可覆盖，默认 `~/.super-personal-platform/knowledge`）。
- 文件收集：
  - 只收 `.md` / `.txt`（扩展名不区分大小写）；
  - 跳过以 `.` 开头的文件和目录（`.obsidian`、`.git` 等）、符号链接、超过 2 MiB 的文件；
  - 读取失败记 warning 并跳过该文件。
- 小节切分：
  - 先剥离文件头 frontmatter（首行 `---` 到下一个 `---`）；
  - 以代码围栏（``` / ~~~）之外的 Markdown 标题行为边界；
  - 1 级标题记为文件标题，不单独切节；2–6 级标题切小节；
  - 文件没有 2–6 级标题时，整个正文为一个小节，标题取文件标题，没有则用文件名；
  - 小节正文（不含标题行）去掉首尾空白后少于 80 字符的丢弃；
  - 小节正文超过 4000 字符时，生成时截断到 4000（不丢弃）。
- 小节身份：`source_hash = sha1(正文折叠空白后的 UTF-8)`；`file_path` 为相对知识库根的 POSIX 路径（如 `交易/缠论笔记/背驰判断.md`）。
- 索引：进程内缓存 `{source_hash: Section}`，TTL 60 秒；根目录不存在或不可读时为空索引（不算错误）。

### 2. 数据表 `knowledge_cards`

`models.py` 增加 `KnowledgeCard`：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | UUID |
| `file_path` | TEXT | 相对知识库根路径 |
| `section_title` | TEXT | 小节标题 |
| `source_hash` | TEXT UNIQUE | 正文哈希，去重与身份 |
| `front` | TEXT | 问题；skipped 行为空串 |
| `back` | TEXT | 答案；skipped 行为空串 |
| `skipped` | INTEGER DEFAULT 0 | 1 = 模型判定该小节没有可做的知识点，不再重复生成 |
| `created_at` | TEXT | UTC ISO |
| `last_reviewed_at` | TEXT NULL | UTC ISO |
| `last_shown_at` | TEXT NULL | UTC ISO，抽卡展示时间 |
| `seen_count` | INTEGER DEFAULT 0 | 评分次数 |
| `interval_days` | INTEGER DEFAULT 0 | 当前间隔 |
| `due_at` | TEXT | 下次到期时间（UTC ISO） |

skipped 行与卡片行同表，用 `skipped` 区分，避免多开一张表；skipped 行的 `front` / `back` 为空串，`created_at` / `due_at` 填创建时间，不参与任何抽卡与统计。新库从五张表变六张，CLAUDE.md 与 README 同步。

### 3. 卡片生成

- Provider 解析：`settings.agents` 中第一个 `status == "active"` 的 Agent 的 `provider`。找不到，或 `base_url` / `model` / `api_key` 任一为空 → 400「没有可用的模型 Provider，请先在配置页填写」。
- 请求：`httpx.AsyncClient`，`POST {base_url.rstrip('/')}/v1/messages`，headers `x-api-key`、`anthropic-version: 2023-06-01`，body `{model, max_tokens: 800, system, messages: [{role: "user", content: <小节正文，截断 4000 字>}]}`，超时 30 秒。
- system prompt（原文）：

  > 你是知识卡片制作助手。用户会给出一段笔记，请从这段内容里提炼一个可以自测的知识点，输出一张卡片。
  > 规则：
  > 1. front 是一个具体问题，不超过 80 字，必须能仅凭这段笔记的内容回答；笔记是中文就用中文，否则沿用笔记语言。
  > 2. back 是简洁准确的答案要点，不超过 200 字，只能使用这段笔记里的信息，不得编造。
  > 3. 如果内容不足以形成有意义的知识点（如纯目录、纯链接、无信息的大标题、孤立代码片段），只输出 {"skip": true}。
  > 4. 只输出 JSON：{"front": "...", "back": "..."} 或 {"skip": true}，不要输出其他内容。

- 解析：取响应 `content` 中第一个 `type == "text"` 的文本，剥掉可能的 ```json 围栏后 `json.loads`。
  - `{"skip": true}` → 插入 skipped 行（front/back 空串）。
  - `front` / `back` 均为非空字符串 → 建卡。
  - 解析失败或字段不合规 → 本次尝试失败，换下一个小节重试。
- 失败与重试：一次抽卡最多尝试 3 个不同小节；全部失败、HTTP 非 2xx 或超时 → 502「卡片生成失败，请重试」，不落库，记 warning 日志（不记 Key）。
- 去重与并发：`source_hash` 唯一约束兜底，插入冲突时回滚并返回已有行（skipped 行按 skip 处理继续换小节）；同一进程内对同一 `source_hash` 的生成加 `asyncio.Lock`，防双击重复计费。

### 4. 抽卡与调度

抽卡（`GET /api/knowledge/cards/random`）：

1. 到期池：`skipped = 0`、`due_at <= now`、且今天（服务器本地日）尚未展示过的卡，按 `due_at` 升序。
2. 未看池：扫描索引中 `source_hash` 不在表里的小节。
3. 若到期池非空，且（未看池为空 或 `random() < 0.7`）→ 从到期池随机取一张（尽量避开请求里的 `exclude` 卡），更新 `last_shown_at`，直接返回。
4. 否则若未看池非空 → 先随机选文件（含未看小节的文件等概率），再在文件内随机选小节，生成新卡；新卡 `due_at = now + 1 天`、`interval_days = 0`、`seen_count = 0`，返回 `is_new = true`。生成期间模型返回 skip 或失败时，从未看池剩余小节继续，最多 3 个。
5. 两池都空但存在非 skipped 卡片 → 随机返回一张，`meta.notice` 为「今天的复习已完成，随机回顾一张」。
6. 一张卡都没有（知识库为空或全是 skipped）→ 404「知识库没有可用的笔记，先去配置页检查知识库同步」。

调度（`POST /api/knowledge/cards/{id}/review`，`result` 为 `known` / `unknown`）：

- `known`：`interval_days = 3`（首次，原值为 0）否则 `ceil(interval_days × 2.5)`，上限 60；`due_at = now + interval_days 天`。
- `unknown`：`interval_days = 1`，`due_at = now + 1 天`。
- 两种都 `seen_count += 1`、`last_reviewed_at = now`。
- 「换一张」不调用评分接口，不改调度；配合「今天已展示的卡不进到期池」的规则，跳过当天不会再出现，次日起回到到期池。
- 到期池「今天已展示」按服务器本地日的 [00:00, 次日 00:00) 换算成 UTC 比较，与「今日已复习」口径一致。

统计口径：

- 待复习 = `skipped = 0`、`due_at <= now`、今天未展示的卡片数；
- 未看 = 未看池小节数（用 60 秒缓存）；
- 今日已复习 = `last_reviewed_at` 落在今天（服务器本地日）的卡片数。

### 5. 接口

新建 `APIRouter(prefix="/api/knowledge", dependencies=[Depends(require_auth)])`（可在 `knowledge.py` 内追加，也可独立文件后同前缀注册）：

- `GET /api/knowledge/cards/random?exclude=<card_id>`：

```json
{
  "card": {
    "id": "…",
    "front": "…",
    "back": "…",
    "source_path": "交易/缠论笔记/背驰判断.md",
    "section_title": "MACD 与背驰",
    "created_at": "2026-11-18T09:00:00+00:00",
    "seen_count": 0,
    "interval_days": 0,
    "due_at": "2026-11-19T09:00:00+00:00",
    "is_new": true
  },
  "meta": {"due": 12, "unseen": 34, "reviewed_today": 5, "notice": ""}
}
```

- `POST /api/knowledge/cards/{card_id}/review`，body `{"result": "known" | "unknown"}`，返回 `{"card_id", "interval_days", "due_at", "meta"}`，`meta` 与 random 响应的 `meta` 同形（`notice` 为空串）；未知 id → 404。
- 错误：无可用 Provider → 400；生成失败 / 超时 → 502；知识库为空 → 404；未鉴权 → 401（沿用 `require_auth`）。
- 响应不返回任何密钥或 provider 内部字段。

### 6. 前端

- 路由：`routes.ts` 增加 `knowledge` → `/knowledge`；`App.tsx` 懒加载 `KnowledgeCardsPage`。
- 侧栏：「Agent 能力」菜单里「记忆」之后新增「知识卡片」，进入时自动展开该菜单（沿用现有机制）。
- 页面结构：顶部统计条（待复习 N · 未看 M · 今日已复习 K）；中央卡片（正面：来源路径 · 小节标题、问题；点击 3D 翻转，背面：答案 + 动作）；动作区（换一张 / 不会 / 会了）。
- 交互细节：
  - 翻面前只有「换一张」；翻面后才出现「不会 / 会了」；
  - 「换一张」发起新抽卡并带上当前卡 `id` 作为 `exclude`；请求中禁用所有动作并显示加载态：生成新卡时文案「正在从笔记里做卡…」（可能 3–10 秒），取缓存卡时只显示轻量骨架；
  - 评分后清空当前卡，自动抽下一张，并用响应的 `meta` 刷新统计；
  - 生成失败 → 页面内错误提示 +「重试」按钮，不清空统计；
  - 空态（404）→「知识库没有可用的笔记」+ 按钮跳转配置页知识库分区（复用侧栏分区直达机制）；
- 样式沿用珍珠冷光 + 极光 + 玻璃风格；≤860px 时卡片占满宽度、统计条可换行。
- 不做前端持久化：刷新页面即重新抽卡（抽卡会更新 `last_shown_at`，到期池里的卡当天不会再出现；两池都空后的「随机回顾」不受此限）。

### 7. 测试

后端（pytest）：

- 切节：frontmatter 剥离、代码围栏内的 `#` 不切、无小标题整文件一节、短小节丢弃、哈希对折叠空白稳定、非 md/隐藏目录/超限文件跳过。
- 调度：首次 known = 3 天、递增 3 → 8 → 20 → 50 → 60、unknown 重置 1 天、上限 60、`due_at` 计算。
- 抽卡：注入随机种子验证到期优先概率、`exclude` 生效、今天已展示排除、两池空时随机回顾、全空 404、skip 落 skipped 行并换小节、最多 3 次尝试。
- 路由：mock 生成器；无 Provider 400；生成失败 502 且不落库；review 未知 id 404；重复 hash 返回已有卡；响应不含密钥。

前端：`npm run build`；手动验收（抽新卡、翻面、会了/不会、换一张、空库态、无 Key 报错、移动端布局）。

### 8. 文档同步

- `docs/intent.md` 新增 F28（功能点 + 验收标准）。
- `CLAUDE.md`：新库六张表、页面路由与侧栏菜单、「知识卡片」段落、Provider 复用规则。
- `README.md`：页面清单与表结构与实际同步（如相关章节存在）。

## 风险与已知取舍

- 生成延迟 3–10 秒、每张约 1–2k token；只在做新卡时消耗，已生成的卡永久复用。
- 笔记修改后正文哈希变化会生成新卡，旧卡继续留在复习池，可能出现近似重复的两张卡；接受，不做版本合并或作废逻辑。
- 删除 / 改名笔记不影响已有卡片（卡片自包含，来源路径只做展示）。
- `deepseek-v4-flash` 的 JSON 输出偶尔不合规：按「换小节重试」处理，3 次全失败才报错。
- 单进程单用户设计；多实例部署本就不同时支持，不处理跨进程锁。

## 实现顺序（供 writing-plans 参考）

1. 切节扫描 + 索引缓存（含测试）
2. `knowledge_cards` 模型与建表
3. 生成器（Provider 解析、prompt、解析、失败重试）
4. 抽卡 / 调度 / review 接口（含测试）
5. 前端页面、路由、侧栏、样式
6. 文档同步与整体验收
