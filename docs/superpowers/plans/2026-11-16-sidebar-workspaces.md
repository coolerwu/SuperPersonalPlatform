# 侧栏工作区重组（Agent / 管理）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把侧栏「功能」页签改名为「管理」，配置页移入管理，管理页签记住上次停留的页面。

**Architecture:** 只改前端。`apps/web/src/components/Sidebar.tsx` 调整页签文案与分组归属；`apps/web/src/App.tsx` 用模块级 `isFeaturesView()` 统一判定管理页，并新增 `lastFeaturesView` state 控制页签落地；`apps/web/src/styles.css` 补两处布局细节；文档同步后按 project-commit 技能提交。

**Tech Stack:** React 18 + TypeScript + Vite。项目没有前端测试框架，自动化门禁是 `npm run build`（`tsc --noEmit && vite build`），人工验收清单见 Task 5。

**当前工作区状态（已存在、未提交）：** 上一轮已把「Agent 能力」组移入原「功能」工作区、Agent 选择器改为两个工作区常驻、侧栏加 `overflow-y: auto`，并已按旧方案改过 intent.md / CLAUDE.md / README。本计划在现状基础上完成剩余部分：改名「管理」、配置迁移、落地记忆、文档按最终方案校正。

---

### Task 1: 页签改名「管理」并把配置入口迁入管理

**Files:**
- Modify: `apps/web/src/components/Sidebar.tsx`（页签文案；Agent 页签平台组；管理页签平台组）
- Modify: `apps/web/src/styles.css`（平台组单按钮占满整行）

- [ ] **Step 1: 页签文案 功能 → 管理**

`Sidebar.tsx` 第 84 行附近：

```tsx
            <button
              type="button"
              role="tab"
              aria-selected={props.workspace === "features"}
              className={`workspace-tab ${props.workspace === "features" ? "active" : ""}`}
              onClick={() => props.onWorkspaceChange("features")}
            >
              管理
            </button>
```

- [ ] **Step 2: Agent 页签平台组去掉「配置」按钮**

把 Agent 分支里的平台组改为只剩退出登录（保留「平台」组标题）：

```tsx
        <div className="sidebar-group sidebar-group-platform">
            <div className="sidebar-group-head">
              <span className="sidebar-group-label">平台</span>
            </div>
            <div className="sidebar-platform-actions">
              <button className="sidebar-action" onClick={props.onLogout}>
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                    <path d="m16 17 5-5-5-5" />
                    <path d="M21 12H9" />
                  </svg>
                </span>
                <span>退出登录</span>
              </button>
            </div>
        </div>
```

- [ ] **Step 3: 管理页签平台组加入「配置」**

管理（`props.workspace !== "agent"`）分支的平台组改为：

```tsx
            <div className="sidebar-group sidebar-group-platform">
              <div className="sidebar-group-head">
                <span className="sidebar-group-label">平台</span>
              </div>
              <div className="sidebar-platform-actions">
                <button
                  className={`sidebar-action ${props.settingsActive ? "active" : ""}`}
                  onClick={props.onOpenSettings}
                >
                  <span className="sidebar-action-icon">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                      <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
                      <circle cx="16" cy="7" r="2" />
                      <circle cx="8" cy="17" r="2" />
                    </svg>
                  </span>
                  <span>配置</span>
                </button>
                <button className="sidebar-action" onClick={props.onLogout}>
                  <span className="sidebar-action-icon">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                      <path d="m16 17 5-5-5-5" />
                      <path d="M21 12H9" />
                    </svg>
                  </span>
                  <span>退出登录</span>
                </button>
              </div>
            </div>
```

- [ ] **Step 4: 单按钮平台组占满整行**

`styles.css` 的 `.pearl .sidebar-capabilities` 规则附近追加：

```css
.pearl .sidebar-platform-actions .sidebar-action:only-child {
  grid-column: 1 / -1;
}
```

- [ ] **Step 5: 构建验证**

Run:

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform/apps/web && npm run build
```

Expected: 输出 `✓ built in ...`，无 TypeScript 报错。

- [ ] **Step 6: 提交**

（按项目约定，所有任务完成后统一用 project-commit 技能提交，见 Task 5；此处只做构建确认。）

---

### Task 2: 工作区判定加入 /config

**Files:**
- Modify: `apps/web/src/App.tsx`（模块级 helper + `featuresWorkspace`）

- [ ] **Step 1: 在 import 之后、组件之前加判定 helper**

`App.tsx` 顶部 `import` 块之后：

```tsx
const FEATURES_VIEWS = new Set<View>([
  "settings",
  "memory",
  "skills",
  "tools",
  "plugins",
  "channels",
  "cron",
  "backtest",
  "journal",
  "chan",
  "allocation",
]);

function isFeaturesView(next: View): boolean {
  return FEATURES_VIEWS.has(next);
}
```

- [ ] **Step 2: 用 helper 替换组件内的 `featuresWorkspace` 定义**

把第 417 行附近的：

```tsx
  const featuresWorkspace =
    view === "backtest" ||
    view === "journal" ||
    view === "chan" ||
    view === "allocation" ||
    view === "skills" ||
    view === "tools" ||
    view === "plugins" ||
    view === "channels" ||
    view === "cron" ||
    view === "memory";
```

替换为：

```tsx
  const featuresWorkspace = isFeaturesView(view);
```

- [ ] **Step 3: 构建验证**

Run:

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform/apps/web && npm run build
```

Expected: `✓ built in ...`。

---

### Task 3: 管理页签记住上次停留页面

**Files:**
- Modify: `apps/web/src/App.tsx`（state、effect、`onWorkspaceChange`）

- [ ] **Step 1: 新增 `lastFeaturesView` state**

紧挨 `const [view, setView] = useState<View>(initialRoute?.view ?? "chat");` 之后：

```tsx
  const [lastFeaturesView, setLastFeaturesView] = useState<View>(() => {
    const start = initialRoute?.view ?? "chat";
    return isFeaturesView(start) ? start : "backtest";
  });
```

- [ ] **Step 2: 进入管理页时更新记忆**

在既有「保持地址栏与派生状态一致」的 `useEffect`（`pathForView(view, sessionId)` 那段）之后追加：

```tsx
  useEffect(() => {
    if (isFeaturesView(view)) {
      setLastFeaturesView(view);
    }
  }, [view]);
```

- [ ] **Step 3: 页签切换改为「管理 = 回上次页面，已在管理页不跳转」**

把 `<Sidebar ... onWorkspaceChange={...}>` 的处理器改为：

```tsx
        onWorkspaceChange={(workspace) => {
          if (workspace === "agent") {
            openView("chat");
          } else if (!featuresWorkspace) {
            openView(lastFeaturesView);
          }
        }}
```

- [ ] **Step 4: 构建验证**

Run:

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform/apps/web && npm run build
```

Expected: `✓ built in ...`。

- [ ] **Step 5: 逐条核对行为（代码走查）**

- 首屏在 `/memory`：`lastFeaturesView` 初始为 `memory`。
- 首屏在 `/chat`：初始为 `backtest`。
- 从 `/chat` 点「管理」：`openView("backtest")`（或上次管理页）。
- 在 `/backtest`、`/memory` 等管理页点「管理」：`featuresWorkspace === true`，不跳转。
- 点「Agent」：`openView("chat")`，不变。

---

### Task 4: 文档按最终方案校正

**Files:**
- Modify: `docs/intent.md`（F14 第 3 条）
- Modify: `CLAUDE.md`（UI 条目）
- Modify: `README.md`（响应式条目、侧栏总述、技能/工具/插件页说明、记忆、渠道、定时、配置入口）

- [ ] **Step 1: `docs/intent.md` F14 第 3 条改为**

```markdown
3. 侧栏顶部「Agent / 管理」两个工作区；Agent 选择器在两个工作区都显示（全站 per-agent 配置的唯一来源）。Agent 工作区按「Agent 工作台 / 平台」分组，平台组只有退出登录；管理工作区放交易（回测、交易记录、资产配置、缠论图）、「Agent 能力」组（技能、工具、插件、渠道、定时、记忆）和平台组（配置、退出登录），不显示会话列表。点「管理」回到本次会话最后停留的管理页（初始取当前路由，否则回测）；已在管理页时不跳转。
```

- [ ] **Step 2: `CLAUDE.md` UI 条目中「侧栏分…」部分改为**

```markdown
侧栏分「Agent / 管理」两个工作区，Agent 选择器常驻；管理工作区放交易页面与「Agent 能力」组（技能、工具、插件、渠道、定时、记忆），配置入口也在管理工作区的平台组；Agent 工作区只留 Agent 工作台与退出登录。
```

- [ ] **Step 3: `README.md` 侧栏总述改为**

```markdown
侧栏顶部是「Agent / 管理」两个工作区 tab（默认 Agent），Agent 选择器在两个工作区都显示，是全站 per-agent 配置的唯一来源。Agent 工作区按「Agent 工作台 / 平台」分组：Agent 工作台放新对话和会话列表；「平台」组放退出登录。管理工作区放回测、交易记录、资产配置、缠论图，以及「Agent 能力」组（技能、工具、插件、渠道、定时、记忆）；「平台」组放配置和退出登录，不显示会话列表。点「管理」回到本次会话最后停留的管理页，已在管理页时不跳转。
```

- [ ] **Step 4: `README.md` 其余逐处替换**

- 响应式条目：`功能工作区（交易页面与 Agent 能力页）` → `管理工作区（交易页面与 Agent 能力页）`
- 配置入口：`侧栏「平台 → 配置」` → `侧栏「管理 → 平台 → 配置」`
- 记忆：`侧栏「功能 → Agent 能力 → 记忆」` → `侧栏「管理 → Agent 能力 → 记忆」`
- 渠道：`侧栏「功能 → Agent 能力 → 渠道」` → `侧栏「管理 → Agent 能力 → 渠道」`
- 定时：`侧栏「功能 → Agent 能力 → 定时」` → `侧栏「管理 → Agent 能力 → 定时」`

- [ ] **Step 5: 全文搜索确认无遗留**

Run:

```bash
grep -rn "功能工作区\|「功能」\|功能 → Agent 能力" /Users/wulang/Desktop/AI/SuperPersonalPlatform/README.md /Users/wulang/Desktop/AI/SuperPersonalPlatform/CLAUDE.md /Users/wulang/Desktop/AI/SuperPersonalPlatform/docs/intent.md
```

Expected: 无输出。设计文档 `docs/superpowers/specs/2026-11-16-sidebar-workspaces-design.md` 属于历史记录，命中可忽略。

---

### Task 5: 验收与提交

**Files:**
- Rebuild: `apps/web/dist/`（构建产物，随提交进入仓库）

- [ ] **Step 1: 后端测试（项目提交约定要求）**

Run:

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform && .venv/bin/python -m pytest apps/api/app/tests
```

Expected: 全部通过。

- [ ] **Step 2: 前端构建**

Run:

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform/apps/web && npm run build
```

Expected: `✓ built in ...`。

- [ ] **Step 3: 人工验收清单（需要登录本地服务；仅在用户同意启动本地 API 时执行）**

- 页签显示 `Agent` | `管理`。
- `/chat` 高亮 Agent；`/config`、`/memory`、`/skills`、`/tools`、`/plugins`、`/channels`、`/cron`、四个交易页高亮管理。
- 从 `/chat` 点管理 → 回测；进 `/memory`，点 Agent，再点管理 → 记忆。
- 在 `/config` 点「管理」不跳转。
- Agent 页签平台组只有退出登录；管理页签平台组有配置和退出登录，配置按钮可进入 `/config`。
- 窄屏抽屉里分组完整可滚动。

- [ ] **Step 4: 提交**

按项目约定执行 `.claude/skills/project-commit/SKILL.md`（同步文档 → pytest + npm build → `git add .` → 提交 → push → 生产更新）。提交信息示例：

```
Move sidebar capabilities into the management workspace
```

---

## Self-Review

- **Spec coverage:** 页签模型（两页签保留）无代码改动；改名 → Task 1；配置归属 → Task 1；管理页落地与页签点击 → Task 3；页面→页签映射 → Task 2；侧栏滚动与选择器常驻已在工作区现状中说明。文档同步 → Task 4；验证 → Task 5。设计文档中「不在范围」各项均未引入。
- **Placeholder scan:** 无 TBD/TODO；每个改码步骤都有完整 JSX/CSS/TS 代码与精确路径。
- **Type consistency:** `isFeaturesView` 在 Task 2 定义、Task 3 复用；`lastFeaturesView` 类型为 `View`，与 `openView(next: View)` 一致；`featuresWorkspace` 布尔值供 `Sidebar` 的 `workspace` 与切换逻辑共用。
