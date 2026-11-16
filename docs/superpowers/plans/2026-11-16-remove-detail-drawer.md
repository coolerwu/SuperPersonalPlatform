# Remove the Chat Detail Drawer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除聊天页右上角「详情」按钮、它打开的详情抽屉（`Drawer`）以及配套的样式和类型，保留后端不动。

**Architecture:** 「详情」按钮是 `App.tsx` 中 `drawerOpen` 状态的唯一入口；打开后渲染 `components/Drawer.tsx`，内含「执行过程」和「Agent」两个 tab。「执行过程」的数据（`live.activity`、`live.streamReasoning`）来自聊天 SSE 流，与消息区内联的执行过程展示共用；「Agent」tab 内嵌的 `AgentEditor` 同时被配置页使用。后端没有任何接口、路由或字段专属于这个抽屉，删后端代码会破坏内联执行过程，因此本次只删前端。

**Tech Stack:** React 19 + TypeScript + Vite（`apps/web`）；FastAPI 后端（`apps/api`）本次不改。前端无单元测试，验证手段是 `npm run build`；后端测试照常跑一遍确认无回归。

## Findings（后端为什么不动）

- `apps/web/src/App.tsx` 里 `drawerOpen` 是详情抽屉的唯一开关，桌面 `chat-topbar` 与移动端 `mobile-bar` 各有一个按钮打开它；删掉按钮后抽屉不可达，所以整个组件一并删除。
- 抽屉「执行过程」tab 只消费 `live.activity` 与 `live.streamReasoning`，这两个值来自 `GET /api/sessions/{uuid}/stream` 的 `tool_call` / `reasoning_delta` 事件，与消息区内联执行过程（`ChatPanel` → `MessageList`）完全共用。
- `apps/api` 内没有以详情抽屉为唯一消费者的路由、字段或后台任务；`reasoning_delta`、`tool_call` 删了会让聊天页失去实时执行过程。因此本次没有可删的后端代码，后端只做回归测试。
- 抽屉「Agent」tab 内嵌的 `AgentEditor` 仍被 `SettingsPage` 使用，组件保留。

---

## 文件结构

| 动作 | 文件 | 职责 |
|---|---|---|
| 删除 | `apps/web/src/components/Drawer.tsx` | 详情抽屉组件（执行过程 + Agent 编辑） |
| 修改 | `apps/web/src/App.tsx` | 删除 `drawerOpen` / `drawerTab` 状态、两处入口按钮、抽屉渲染与相关 import |
| 修改 | `apps/web/src/types.ts` | 删除 `DrawerTab` 类型 |
| 修改 | `apps/web/src/styles.css` | 删除抽屉布局、按钮、执行过程行、响应式与珍珠主题覆盖样式 |
| 修改 | `docs/intent.md` | F14 第 4 点删除「详情变全屏面板」 |
| 修改 | `apps/web/dist/` | 按项目约定重新构建并提交 |

不改：`apps/api/**`（无专属后端代码）；`ChatPanel` / `MessageList`（内联执行过程仍用 `activity`、`streamReasoning`）。

---

### Task 1: 删除抽屉组件与 App 中的接线

**Files:**
- Delete: `apps/web/src/components/Drawer.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/types.ts`

- [x] **Step 1: 删除 `DrawerTab` 类型**

`apps/web/src/types.ts` 中删除这一行（含其后的空行）：

```ts
export type DrawerTab = "run" | "agent";
```

删除后 `BacktestStatus` 类型声明应紧跟在 `MapSettings` 类型后面，文件仍能通过类型检查。

- [x] **Step 2: 删除 App.tsx 的 import**

`apps/web/src/App.tsx` 顶部 `import type { ... } from "./types"` 列表中删除 `DrawerTab,` 一行；并删除组件 import：

```ts
import { Drawer } from "./components/Drawer";
```

- [x] **Step 3: 删除抽屉状态**

`apps/web/src/App.tsx` 中删除这两行：

```ts
const [drawerTab, setDrawerTab] = useState<DrawerTab>("run");
const [drawerOpen, setDrawerOpen] = useState(false);
```

- [x] **Step 4: 删除 app-shell 的 `with-drawer` 类**

把：

```tsx
    <div
      className={["app-shell", "pearl", view === "chat" && drawerOpen ? "with-drawer" : ""]
        .filter(Boolean)
        .join(" ")}
    >
```

改为：

```tsx
    <div className="app-shell pearl">
```

- [x] **Step 5: 删除移动端顶部栏的详情按钮**

把 `mobile-bar` 里这段三元表达式：

```tsx
        {view === "chat" ? (
          <button
            type="button"
            className={`mobile-bar-button ${drawerOpen ? "active" : ""}`}
            onClick={() => setDrawerOpen((current) => !current)}
            aria-label={drawerOpen ? "收起详情" : "打开详情"}
            title={drawerOpen ? "收起详情" : "打开详情"}
          >
            <svg
              viewBox="0 0 24 24"
              width="18"
              height="18"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M15 4v16" />
            </svg>
          </button>
        ) : (
          <span className="mobile-bar-spacer" />
        )}
```

替换为：

```tsx
        <span className="mobile-bar-spacer" />
```

- [x] **Step 6: 删除桌面聊天顶部栏的详情按钮**

把 `chat-topbar` 里这段：

```tsx
            {!drawerOpen && (
              <button
                type="button"
                className="topbar-button"
                title="打开详情"
                aria-label="打开详情"
                onClick={() => setDrawerOpen(true)}
              >
                <svg
                  viewBox="0 0 24 24"
                  width="15"
                  height="15"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <rect x="3" y="4" width="18" height="16" rx="2" />
                  <path d="M15 4v16" />
                </svg>
                <span>详情</span>
              </button>
            )}
```

整段删除，`<header className="chat-topbar">` 内只保留 `<div className="chat-topbar-main">...</div>`。

- [x] **Step 7: 删除抽屉渲染块**

`apps/web/src/App.tsx` 中删除：

```tsx
      {view === "chat" && drawerOpen && (
        <Drawer
          tab={drawerTab}
          onTab={setDrawerTab}
          activity={live.activity.map((item) => item.name)}
          reasoning={live.streamReasoning}
          agent={agent}
          providers={providers}
          onSaveAgent={saveAgent}
          onClose={() => setDrawerOpen(false)}
        />
      )}
```

- [x] **Step 8: 删除 Drawer.tsx**

```bash
rm apps/web/src/components/Drawer.tsx
```

- [x] **Step 9: 构建确认没有残留引用**

Run: `cd apps/web && npm run build`
Expected: 构建成功，输出 `✓ built in ...`；若报 `Drawer` / `DrawerTab` / `drawerOpen` 未定义，说明有漏删的引用，按报错位置清理。

- [x] **Step 10: 提交**

```bash
git add apps/web/src/App.tsx apps/web/src/types.ts apps/web/src/components/Drawer.tsx
git commit -m "Remove the chat detail drawer"
```

---

### Task 2: 删除抽屉相关样式

**Files:**
- Modify: `apps/web/src/styles.css`

说明：以下样式块删完后不应有其它使用方（已核对：`status-pill`、`event-*`、`run-summary`、`run-id`、`events-heading`、`topbar-button`、`mobile-bar-button.active` 只被 `Drawer.tsx` 或详情按钮使用；`.empty-note`、`.eyebrow`、`.metric-grid` 被其它页面使用，必须保留）。

- [x] **Step 1: 删除布局变量与三列网格**

`apps/web/src/styles.css` `:root` 内删除：

```css
  --drawer-w: 380px;
```

把 `.app-shell` 的网格改为两列：

```css
  grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
```

并整块删除：

```css
.app-shell:not(.with-drawer) {
  --chat-lane: 1200px;
  grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
}
```

- [x] **Step 2: 删除按钮的 active 状态**

把：

```css
.mobile-bar-button:hover,
.mobile-bar-button.active {
```

改为：

```css
.mobile-bar-button:hover {
```

并在 `@media (hover: none)` 内删除：

```css
  .mobile-bar-button.active:hover {
    background: var(--accent-soft);
    color: var(--accent);
  }
```

- [x] **Step 3: 删除 `.topbar-button` 样式**

删除 `.chat-topbar-sub::before` 之后的两块：

```css
.topbar-button {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: #fff;
  color: var(--muted);
  font-size: 12px;
  box-shadow: var(--shadow-sm);
  transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
}

.topbar-button:hover {
  color: var(--text);
  border-color: var(--border-strong);
}
```

以及珍珠主题里的：

```css
.pearl .topbar-button {
  border-color: rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.6);
  box-shadow: 0 3px 10px rgba(28, 34, 52, 0.05);
}

.pearl .topbar-button:hover {
  border-color: rgba(255, 255, 255, 1);
  background: rgba(255, 255, 255, 0.85);
}
```

- [x] **Step 4: 删除 `.status-pill` 样式**

删除从 `.status-pill {` 到 `.status-pill.cancelled { ... }` 的完整五块（`.status-pill`、`.running,.queued`、`.completed`、`.failed`、`.cancelled`），位置在 `.topbar-button:hover` 与 `.chat-panel` 之间。

- [x] **Step 5: 删除抽屉基础样式**

删除整个抽屉样式段（`.drawer` 到 `.drawer-body`，含 `.drawer-header`、`.drawer-tabs`、`.drawer-tabs button`、`.drawer-tabs button.active`）。

- [x] **Step 6: 删除执行过程行样式**

删除 `.run-summary`、`.run-id`、`.events-heading`、`.event-list`、`.event-row`、`.event-row.assistant_delta`、`.event-row.model_usage`、`.event-row.run_failed`、`.event-row.subagent_response`、`.event-row.browser_action`、`.event-top`、`.event-type`、`.event-time`、`.event-text` 全部样式块。中间的 `.metric-grid` 属于回测页，必须保留。

- [x] **Step 7: 删除抽屉动画**

删除：

```css
@keyframes drawer-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
```

- [x] **Step 8: 清理 1180px 响应式**

把：

```css
  .app-shell,
  .app-shell:not(.with-drawer) {
    grid-template-columns: 236px minmax(0, 1fr);
  }
```

改为：

```css
  .app-shell {
    grid-template-columns: 236px minmax(0, 1fr);
  }
```

并在同一媒体查询内删除 `.drawer` 的 fixed 覆盖层规则（`position: fixed; top: 0; right: 0; bottom: 0; width: min(400px, 92vw); z-index: 30; box-shadow: var(--shadow-lg);`）。

- [x] **Step 9: 清理 860px 响应式**

把：

```css
  .app-shell,
  .app-shell:not(.with-drawer) {
    display: flex;
    flex-direction: column;
    height: 100vh;
    height: 100dvh;
    min-height: 0;
  }
```

改为只保留 `.app-shell` 选择器。

并在同一媒体查询内删除 `.drawer-header .icon-button`、`.drawer`、`.drawer-header`、`.drawer-body` 四块样式。

- [x] **Step 10: 清理珍珠主题的抽屉覆盖**

删除注释 `/* Detail drawer lives inside the chat view, so it follows the same surface. */` 及紧随其后的 `.pearl .drawer` 块；在 `@media (max-width: 1180px)` 内删除 `.pearl .drawer` 块；在 `@media (max-width: 860px)` 内删除 `.pearl .drawer` 块。

- [x] **Step 11: 构建确认样式删除没有破坏引用**

Run: `cd apps/web && npm run build`
Expected: 构建成功。再运行 `grep -n "drawer\|topbar-button\|status-pill\|event-" apps/web/src/styles.css`，Expected: 无输出（或只剩与侧栏抽屉无关且确实仍在使用的选择器；逐条确认）。

- [x] **Step 12: 提交**

```bash
git add apps/web/src/styles.css apps/web/dist
git commit -m "Drop detail drawer styles"
```

---

### Task 3: 同步文档

**Files:**
- Modify: `docs/intent.md:347`

- [x] **Step 1: 更新 F14 响应式描述**

把：

```
4. ≤860px 使用顶部栏 + 汉堡菜单；侧栏变抽屉、详情变全屏面板；输入区适配 `visualViewport` 与安全区；管理页表格改卡片。
```

改为：

```
4. ≤860px 使用顶部栏 + 汉堡菜单；侧栏变抽屉；输入区适配 `visualViewport` 与安全区；管理页表格改卡片。
```

- [x] **Step 2: 核查 CLAUDE.md / README.md**

Run: `grep -n "详情" CLAUDE.md README.md docs/intent.md`
Expected: 无输出。两处的「抽屉」都指移动端侧栏抽屉（保留），不涉及详情抽屉，不需要改。

- [x] **Step 3: 提交**

```bash
git add docs/intent.md
git commit -m "Sync intent for the removed detail drawer"
```

---

### Task 4: 全量验证并收尾

**Files:** 无新改动（只验证）

- [x] **Step 1: 后端测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests`
Expected: 全部通过（本次不改后端，作为回归确认）。

- [x] **Step 2: 前端构建**

Run: `cd apps/web && npm run build`
Expected: 构建成功，`apps/web/dist` 更新并已随前面的提交进入仓库；若 dist 有变更，补一次 `git add apps/web/dist && git commit -m "Rebuild web dist"`。

- [x] **Step 3: 确认改动范围**

Run: `git status --short && git log --oneline -3`
Expected: 工作区干净；提交只包含 `App.tsx`、`types.ts`、`Drawer.tsx`（删除）、`styles.css`、`docs/intent.md`、`apps/web/dist`，没有 `.env`、`data/`、`.super-personal-platform/`。

- [x] **Step 4: 按项目约定提交/推送（如需要）**

执行 `.claude/skills/project-commit/SKILL.md` 的流程；只 push `origin`。

---

## Self-Review

**1. Spec coverage**
- 「删除右上角详情按钮」→ Task 1 Step 6（桌面）、Step 5（移动端同款按钮）。
- 「后端代码考虑删除」→ 计划开头 Findings：后端无专属代码，删除会破坏内联执行过程，因此不动后端；Task 4 Step 1 用后端测试确认无回归。
- 抽屉整体不可达后的死代码 → Task 1 Step 1/7/8、Task 2 全部步骤。
- 文档同步 → Task 3。
- 构建产物提交约定 → Task 2 Step 12、Task 4 Step 2。

**2. Placeholder scan**
- 所有删除/替换都给出了完整代码块和文件位置；没有 TBD / 「类似 Task N」。
- CSS 删除按选择器名定位，不依赖易漂移的行号。

**3. Type consistency**
- `DrawerTab` 只在 `types.ts`、`App.tsx`、`Drawer.tsx` 三处出现，Step 1–3/8 同步删除。
- `drawerOpen` 只在 `App.tsx` 内出现（className、两处按钮、渲染块），Step 3–7 全部覆盖。
- 保留 `live.activity` / `live.streamReasoning`，因为 `ChatPanel` 仍在用（`App.tsx` 中传给 `ChatPanel` 的 props 不改）。
