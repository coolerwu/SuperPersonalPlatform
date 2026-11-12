# 前端性能优化：路由分包与页面缓存实施计划（阶段 3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低首屏脚本体积、让切页不再等待下载，并让已访问页面用缓存立即渲染、后台刷新。

**Architecture:** 聊天页保持同步加载，其余页面用 `React.lazy` 按路由分包；登录后空闲预取全部页面代码块；Vite 把 React 与 Markdown 渲染链拆成稳定 vendor chunk。新增 `src/pageCache.ts`，业务页在请求前同步读取缓存、请求后写回，写操作按前缀失效。

**Tech Stack:** React 18、TypeScript、Vite 6、Playwright（验证）。

**对应设计文档：** `docs/superpowers/specs/2026-11-12-frontend-performance-design.md`（P3）。

---

### Task 1: 路由懒加载与空闲预取

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/styles.css`
- Modify: `apps/web/vite.config.ts`

- [x] 把 11 个非聊天页面改为 `React.lazy`，通过 `PAGE_LOADERS` 表统一管理动态 import。
- [x] 页面区域包一层 `Suspense`，fallback 使用 `.page-loading`（grid 第 2 列居中）。
- [x] 登录后触发一次 `requestIdleCallback`（降级 2 秒定时）预取所有页面块；`navigator.connection.saveData` 时跳过。
- [x] Vite `manualChunks` 拆出 `react-vendor`、`markdown`、`charts`，页面代码自动按路由分块。

### Task 2: 页面数据 stale-while-revalidate 缓存

**Files:**
- Create: `apps/web/src/pageCache.ts`
- Modify: `apps/web/src/components/BacktestPage.tsx`
- Modify: `apps/web/src/components/TradeJournalPage.tsx`
- Modify: `apps/web/src/components/CronPage.tsx`
- Modify: `apps/web/src/components/ChannelsPage.tsx`
- Modify: `apps/web/src/components/PluginsPage.tsx`
- Modify: `apps/web/src/components/ToolsPage.tsx`
- Modify: `apps/web/src/components/ChanPage.tsx`
- Modify: `apps/web/src/components/AllocationPage.tsx`

- [x] `pageCache.ts` 提供同步 `peekPageCache`、`storePageCache`、`invalidatePageCache`，最多保留 60 条，超出按插入顺序淘汰。
- [x] 回测：缓存 meta 与最近 50 条历史，创建/取消后刷新并写回。
- [x] 交易记录：按 `状态|市场|搜索词` 缓存列表与统计，保存后使 `journal:` 前缀失效。
- [x] 定时：按 Agent 缓存任务列表；渠道列表与渠道页共用 `channels:<agent>` 缓存。
- [x] 渠道：按 Agent 缓存列表，扫码/启停后刷新写回。
- [x] 插件：缓存目录；安装/启停/卸载/集市操作返回的新目录直接写回。
- [x] 工具：按 Agent 缓存目录；无缓存时先清空旧 Agent 数据，避免串台。
- [x] 缠论：按 `symbol|granularity|start|end` 缓存分析结果。
- [x] 资产配置：缓存快照与每个账本的流水；增删改后使 `allocation:` 前缀失效。

### Task 3: 验证与文档

- [x] Playwright + 路由 mock 验证：
  - 首次进入插件页 1377ms（含 800ms 模拟接口延迟）。
  - 离开后二次进入 77ms 即显示缓存内容，随后后台刷新。
  - 空闲预取请求到全部页面模块。
  - 无 page error。
- [x] 构建产物：入口 `index-*.js` 96.31 kB（gzip 28.25 kB），`react-vendor` 178.54 kB，`markdown` 610.58 kB，11 个页面各自成块。
- [x] `docs/intent.md` F21 增加第 6 条，`CLAUDE.md` 运行约定补充页面懒加载与缓存。
- [x] 后端测试与前端构建。

## 已知取舍

- Markdown 渲染链（含 KaTeX 与 highlight.js common）仍在首屏加载，约 610 kB；它是聊天必需的，拆成独立 chunk 后可长期缓存。尝试只注册常用高亮语言反而因为 `rehype-highlight` 仍打包 common 集合而增大，已回退。
- 页面缓存是进程内 Map，刷新页面即清空；不持久化，避免跨版本读到旧结构。
- 缓存写入前不做过期判断；每次挂载都会后台刷新，旧数据只在首次渲染的极短时间内出现。
