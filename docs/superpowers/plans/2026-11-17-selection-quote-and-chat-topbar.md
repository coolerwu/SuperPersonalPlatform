# 划词引用与聊天页顶部栏移除实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持在单条消息内选中一段文字后浮出「引用」按钮并引用该片段；同时移除聊天页顶部标题栏（桌面删除 `chat-topbar`，手机聊天视图只留菜单按钮）。

**Architecture:** 新增 `SelectionQuote` 组件，统一监听选区、判定是否落在同一条消息气泡内、用 `position: fixed` 浮出按钮，点击后复用现有 `onQuote → 引用区 → `> ` 引用块` 通路。顶部栏改动只涉及 `App.tsx` 的条件渲染与 `styles.css` 的规则删除。

**Tech Stack:** React 18、TypeScript、Vite；无新增依赖。前端没有单测运行器，spec 也约定不引入新依赖，因此每个任务的验证以 `npm run build`（含 `tsc --noEmit`）和浏览器断言为准，不写先失败的单元测试。

**Spec:** `docs/superpowers/specs/2026-11-17-selection-quote-and-chat-topbar-design.md`

---

## 文件结构

| 文件 | 责任 |
| --- | --- |
| `apps/web/src/components/SelectionQuote.tsx`（新建） | 选区判定、浮动按钮定位与显隐；对外只接收 `scrollRef` 与 `onQuote` |
| `apps/web/src/components/ChatPanel.tsx` | 挂载 `SelectionQuote`，把 `quoteFromMessage` 传给它 |
| `apps/web/src/components/MessageList.tsx` | 给用户、Agent、流式三种消息气泡加 `data-quoteable` 标记 |
| `apps/web/src/App.tsx` | 删除 `chat-topbar`；手机端聊天视图不渲染标题块；删除不再使用的 `sessionTitle` |
| `apps/web/src/styles.css` | 删除 topbar 全部规则；新增 `.selection-quote` 与 pearl 主题样式 |
| `docs/intent.md`、`README.md`、`CLAUDE.md` | 同步行为与界面描述 |

---

## Task 1: 新建划词引用组件

**Files:**
- Create: `apps/web/src/components/SelectionQuote.tsx`

- [ ] **Step 1: 创建组件文件**

写入完整内容：

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

type SelectionQuoteProps = {
  scrollRef: RefObject<HTMLDivElement | null>;
  onQuote: (text: string) => void;
};

type QuoteCandidate = {
  text: string;
  top: number;
  left: number;
};

const BUTTON_WIDTH = 60;
const BUTTON_HEIGHT = 30;
const GAP = 8;
const EDGE = 8;

function closestQuoteable(node: Node | null): HTMLElement | null {
  if (!node) return null;
  const element =
    node.nodeType === Node.ELEMENT_NODE
      ? (node as Element)
      : node.parentElement;
  return element?.closest<HTMLElement>("[data-quoteable]") ?? null;
}

export function SelectionQuote({ scrollRef, onQuote }: SelectionQuoteProps) {
  const [candidate, setCandidate] = useState<QuoteCandidate | null>(null);
  const candidateRef = useRef<QuoteCandidate | null>(null);
  const frameRef = useRef<number | null>(null);
  const draggingRef = useRef(false);

  const clear = useCallback(() => {
    candidateRef.current = null;
    setCandidate(null);
  }, []);

  const evaluate = useCallback(() => {
    // Mouse drags only offer quoting after the button is released.
    if (draggingRef.current) {
      clear();
      return;
    }
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      clear();
      return;
    }
    try {
      const range = selection.getRangeAt(0);
      const startBubble = closestQuoteable(range.startContainer);
      const endBubble = closestQuoteable(range.endContainer);
      if (!startBubble || startBubble !== endBubble) {
        clear();
        return;
      }
      const text = range.toString().trim();
      if (!text) {
        clear();
        return;
      }
      const rects = range.getClientRects();
      const anchor = rects[rects.length - 1] ?? range.getBoundingClientRect();
      if (anchor.width === 0 && anchor.height === 0) {
        clear();
        return;
      }
      const left = Math.min(
        Math.max(anchor.left + anchor.width / 2 - BUTTON_WIDTH / 2, EDGE),
        window.innerWidth - BUTTON_WIDTH - EDGE,
      );
      const top =
        anchor.top - BUTTON_HEIGHT - GAP >= EDGE
          ? anchor.top - BUTTON_HEIGHT - GAP
          : anchor.bottom + GAP;
      const next = { text, top, left };
      candidateRef.current = next;
      setCandidate(next);
    } catch {
      clear();
    }
  }, [clear]);

  const schedule = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = null;
      evaluate();
    });
  }, [evaluate]);

  useEffect(() => {
    const scroll = scrollRef.current;
    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType === "mouse") draggingRef.current = true;
    };
    const onPointerUp = (event: PointerEvent) => {
      if (event.pointerType !== "mouse") return;
      draggingRef.current = false;
      schedule();
    };
    document.addEventListener("selectionchange", schedule);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("pointerup", onPointerUp);
    document.addEventListener("touchend", schedule);
    window.addEventListener("resize", clear);
    scroll?.addEventListener("scroll", clear, { passive: true });
    return () => {
      document.removeEventListener("selectionchange", schedule);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("pointerup", onPointerUp);
      document.removeEventListener("touchend", schedule);
      window.removeEventListener("resize", clear);
      scroll?.removeEventListener("scroll", clear);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, [scrollRef, schedule, clear]);

  if (!candidate) return null;

  function quote() {
    const text = candidateRef.current?.text;
    window.getSelection()?.removeAllRanges();
    clear();
    if (text) onQuote(text);
  }

  return (
    <button
      type="button"
      className="selection-quote"
      style={{ top: candidate.top, left: candidate.left }}
      aria-label="引用选中内容"
      title="引用选中内容"
      onMouseDown={(event) => event.preventDefault()}
      onClick={quote}
    >
      引用
    </button>
  );
}
```

- [ ] **Step 2: 构建确认类型检查通过**

Run: `cd apps/web && npm run build`

Expected: `tsc --noEmit` 无报错，vite 构建成功；此组件尚未被引用，产物无功能变化。

- [ ] **Step 3: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add .
git commit -m "Add selection quote overlay component

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Expected: 提交包含 `SelectionQuote.tsx`（`apps/web/dist` 无变化也会被 `git add .` 吞掉，属正常）。

---

## Task 2: 接入划词引用并标记消息气泡

**Files:**
- Modify: `apps/web/src/components/ChatPanel.tsx`
- Modify: `apps/web/src/components/MessageList.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: 在 ChatPanel 中挂载组件**

在 import 区（`MessageList` 之后）加入：

```tsx
import { MessageList } from "./MessageList";
import { SelectionQuote } from "./SelectionQuote";
import { SessionTaskPanel } from "./SessionTaskPanel";
```

在 `message-scroll` 的结束标签与 `composer` 之间插入：

```tsx
      </div>

      <SelectionQuote scrollRef={scrollRef} onQuote={quoteFromMessage} />

      <div className="composer">
```

- [ ] **Step 2: 给三种消息气泡加标记**

`MessageList.tsx` 中三处气泡各加 `data-quoteable=""`：

```tsx
      <div className="message-bubble user" data-quoteable="">
```

```tsx
      <div className="message-bubble assistant" data-quoteable="">
```

```tsx
      <div className="message-bubble assistant streaming" data-quoteable="">
```

- [ ] **Step 3: 新增浮动按钮样式**

在 `styles.css` 的 `.chat-panel { ... }` 规则之后插入：

```css
.selection-quote {
  position: fixed;
  z-index: 30;
  height: 30px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--panel);
  color: var(--accent);
  font-size: 12px;
  font-weight: 650;
  line-height: 1;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
}

.selection-quote:hover {
  background: var(--panel-2);
}

.pearl .selection-quote {
  border-color: rgba(255, 255, 255, 0.95);
  background: rgba(255, 255, 255, 0.92);
  color: #5b4bf0;
  box-shadow: 0 8px 22px rgba(28, 34, 52, 0.14);
}
```

- [ ] **Step 4: 构建**

Run: `cd apps/web && npm run build`

Expected: 无 TypeScript 报错，构建成功。

- [ ] **Step 5: 浏览器验证划词引用**

启动后端与前端（两个终端）：

```bash
cd apps/api && ../../.venv/bin/uvicorn app.main:app --port 8000
```

```bash
cd apps/web && npm run dev
```

打开 `http://localhost:5173`，用本地 `.env` 的 `APP_AUTH_TOKEN` 登录，进入一个有历史消息的会话。然后逐项确认：

1. 选中文案浮出按钮：在浏览器控制台执行

```js
(() => {
  const bubble = document.querySelector(".message-bubble.assistant, .message-bubble.user");
  if (!bubble) return "no bubble";
  const target = bubble.querySelector("p") || bubble;
  const range = document.createRange();
  range.selectNodeContents(target);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
  return "selected";
})()
```

Expected: 页面出现「引用」按钮（`document.querySelector(".selection-quote")` 非空），`position` 为 `fixed`。

2. 跨消息不出现：在控制台执行

```js
(() => {
  const bubbles = document.querySelectorAll("[data-quoteable]");
  if (bubbles.length < 2) return "need two bubbles";
  const range = document.createRange();
  range.setStart(bubbles[0], 0);
  range.setEnd(bubbles[bubbles.length - 1], bubbles[bubbles.length - 1].childNodes.length);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
  return "cross selected";
})()
```

Expected: 下一帧后 `.selection-quote` 为 `null`。

3. 点击生效：重新执行第 1 步的单气泡选区脚本让按钮出现，然后点击「引用」按钮。Expected: `.composer-quote-text` 显示所选文字；浏览器选区被清空；输入框获得焦点。

4. 替换行为：已有引用时再划词并点「引用」。Expected: 引用区文字变为新选区，不叠加。

5. 滚动隐藏：先划词让按钮出现，再滚动 `.message-scroll`。Expected: 按钮消失。

6. 发送格式：有引用时点发送（可只发引用不带文字）。Expected: 发送后的用户消息中出现 `> ` 开头的引用块。

- [ ] **Step 6: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add .
git commit -m "Wire selection quoting into chat messages

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Expected: 提交包含三个源码文件与重新构建的 `apps/web/dist`。

---

## Task 3: 移除聊天页顶部栏

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: 删除桌面端 chat-topbar**

把：

```tsx
        <main className="chat-column">
          <header className="chat-topbar">
            <div className="chat-topbar-main">
              <span className="chat-topbar-title">{agent?.name ?? "Agent"}</span>
              <span className="chat-topbar-sub">{sessionTitle}</span>
            </div>
          </header>
          {agents.length === 0 ? (
```

改为：

```tsx
        <main className="chat-column">
          {agents.length === 0 ? (
```

- [ ] **Step 2: 手机端聊天视图不渲染标题块，并删除 sessionTitle**

把：

```tsx
        <div className="mobile-bar-title">
          <span className="mobile-bar-name">{viewTitle}</span>
          {view === "chat" && <span className="mobile-bar-sub">{sessionTitle}</span>}
        </div>
```

改为：

```tsx
        {view !== "chat" && (
          <div className="mobile-bar-title">
            <span className="mobile-bar-name">{viewTitle}</span>
          </div>
        )}
```

再把：

```tsx
  const sessionTitle = currentSession?.title || "新对话";
```

整行删除（`currentSession` 仍在 `ChatPanel` 的 `channelType` / `channelName` 处使用，不要删 `currentSession`）。

- [ ] **Step 3: 删除桌面端 topbar 样式**

在 `styles.css` 中整段删除：

```css
.chat-topbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 14px;
  height: 56px;
  padding: 0 max(clamp(18px, 4vw, 42px), calc((100% - var(--chat-lane)) / 2));
  border-bottom: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.45);
  backdrop-filter: blur(14px);
  z-index: 5;
}

.chat-topbar-main {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.chat-topbar-title {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.chat-topbar-sub {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--faint);
  font-size: 12px;
}

.chat-topbar-sub::before {
  content: "·";
  margin-right: 8px;
  color: var(--border-strong);
}
```

- [ ] **Step 4: 删除 ≤860px 的 topbar 隐藏规则**

在 `@media (max-width: 860px)` 中整段删除：

```css
  .chat-topbar {
    display: none;
  }
```

- [ ] **Step 5: 删除 pearl 主题 topbar 样式**

在 `styles.css` 中整段删除（保留其后的 `/* Messages */` 注释）：

```css
/* Topbar */

.pearl .chat-topbar {
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.86);
  backdrop-filter: none;
  box-shadow: 0 10px 30px rgba(28, 34, 52, 0.06);
}

.pearl .chat-topbar-title {
  font-size: 13.5px;
  letter-spacing: -0.01em;
}

.pearl .chat-topbar-sub {
  font-size: 11.5px;
}
```

- [ ] **Step 6: 构建**

Run: `cd apps/web && npm run build`

Expected: 无 TypeScript 报错，构建成功。

- [ ] **Step 7: 浏览器验证顶部栏**

1. 桌面宽度（≥861px）：进入聊天页。Expected: `document.querySelector(".chat-topbar") === null`；消息区顶部直接开始，无 56px 空条。
2. 窄屏（≤860px，可用开发者工具设备模拟或 `preview_resize` 移动预设）：聊天页。Expected: 没有标题文字（`.mobile-bar-title` 为 `null`），左上角汉堡菜单按钮可打开侧栏抽屉。
3. 窄屏进入配置页（地址 `/config`）。Expected: `.mobile-bar-title` 存在并显示「配置」，其余页面标题栏不受影响。
4. pearl 主题下聊天页无 topbar 残留边框或空白。

- [ ] **Step 8: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add .
git commit -m "Remove chat page top bar

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: 文档同步与整体验证

**Files:**
- Modify: `docs/intent.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新 `docs/intent.md` 功能点**

把：

```markdown
2. 网页 Chat：流式输出、历史会话、停止、运行中排队追问、Markdown/公式渲染、附件、引用；失败或停止后重新发送即可，不提供单条消息重试。
```

改为：

```markdown
2. 网页 Chat：流式输出、历史会话、停止、运行中排队追问、Markdown/公式渲染、附件、引用（整条与划词）；聊天页不显示顶部标题栏；失败或停止后重新发送即可，不提供单条消息重试。
```

把：

```markdown
9. 消息操作：复制、引用；失败或停止后显示提示，用户重新发送内容即可，没有单独的重试按钮。不做编辑、重新生成、删除单条消息。
```

改为：

```markdown
9. 消息操作：复制、引用（整条引用，及在单条消息内选中片段后的划词引用）；失败或停止后显示提示，用户重新发送内容即可，没有单独的重试按钮。不做编辑、重新生成、删除单条消息。
```

- [ ] **Step 2: 更新 `docs/intent.md` 验收标准**

把：

```markdown
- 附件上传后可被 Agent 读取；引用以 `> ` 块进入消息。
```

改为：

```markdown
- 附件上传后可被 Agent 读取；整条引用与划词引用都以 `> ` 块进入消息。
- 聊天页不显示顶部标题栏；手机端聊天视图顶栏只保留菜单按钮，其他页面标题栏不变。
```

- [ ] **Step 3: 更新 `README.md`**

把：

```markdown
- 消息操作：复制、引用；按钮位于气泡下方，引用内容显示在输入框上方并可单独取消，发送时以 `> ` 引用块写入消息；失败或停止后显示提示，重新发送内容即可，没有单独的重试按钮。不做编辑、重新生成或删除单条消息。
```

改为：

```markdown
- 消息操作：复制、引用；按钮位于气泡下方，引用内容显示在输入框上方并可单独取消，发送时以 `> ` 引用块写入消息。除整条引用外，在单条消息内选中一段文字会在选区旁浮出「引用」按钮，点击后只引用所选片段，已有引用会被替换；跨消息或纯空白选区不出现按钮。失败或停止后显示提示，重新发送内容即可，没有单独的重试按钮。不做编辑、重新生成或删除单条消息。
```

把：

```markdown
- 空状态提供建议提示词；顶部轻量栏显示当前 Agent 和会话标题。
```

改为：

```markdown
- 空状态提供建议提示词；聊天页不显示顶部标题栏（桌面与手机），手机端聊天视图顶栏只保留菜单按钮，其他页面标题栏不变。
```

- [ ] **Step 4: 更新 `CLAUDE.md` 网页聊天条目**

在「网页聊天：……」这一条末尾追加一句：

```markdown
消息操作支持整条复制/引用，也支持在单条消息内选中片段后划词引用；聊天页不显示顶部标题栏，手机端聊天视图顶栏只保留菜单按钮。
```

- [ ] **Step 5: 后端测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests`

Expected: 全部通过（本次无后端改动）。

- [ ] **Step 6: 前端最终构建**

Run: `cd apps/web && npm run build`

Expected: `tsc --noEmit` 与 vite 构建均成功，`apps/web/dist` 更新。

- [ ] **Step 7: 整体回归清单**

在浏览器中逐项确认：

1. 整条「引用」按钮仍工作：点击消息下方「引用」→ 引用区显示整条消息 → 发送后为 `> ` 块。
2. 划词引用：选中一段文字 → 浮出「引用」→ 点击 → 引用区只显示该段 → 发送后为 `> ` 块。
3. 跨消息选择、纯空白选择都不出现按钮。
4. 已有引用时再划词为替换；点 × 可取消引用。
5. 滚动消息区、缩放窗口后按钮消失。
6. 桌面聊天页无顶部栏；≤860px 聊天页只有菜单按钮，配置等其他页面标题栏正常。
7. 手机端长按选词后同样能浮出「引用」并点击生效。

- [ ] **Step 8: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add .
git commit -m "Document selection quoting and chat top bar removal

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Expected: 提交包含三份文档与最终 `apps/web/dist`。如需发布，再按项目约定执行 `git push origin HEAD` 与生产更新。

---

## Self-Review

- **Spec 覆盖**：划词判定与浮动按钮（Task 1、2）、同消息限制（Task 1 的 `closestQuoteable` 比较）、替换与取消（Task 2 验证、现有逻辑）、滚动/缩放/会话切换隐藏（Task 1 effect）、整条引用保留（Task 2 验证）、桌面顶栏删除（Task 3 Step 1、3）、手机标题块条件渲染（Task 3 Step 2、4、5）、文档同步（Task 4）。
- **占位符**：无 TBD/TODO；每个代码步骤都给了完整代码与原文。
- **类型一致性**：`SelectionQuoteProps.scrollRef` 与 `ChatPanel` 的 `useRef<HTMLDivElement | null>(null)` 一致；`onQuote` 与 `quoteFromMessage` 的 `(text: string) => void` 一致；`data-quoteable` 在 MessageList 与 SelectionQuote 中拼写一致。
