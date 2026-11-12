# 前端性能优化：消息列表虚拟化实施计划（阶段 2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 `@tanstack/react-virtual` 对聊天历史消息做窗口化，DOM 行数不再随会话长度增长，同时保持阶段 1 的跟随、定位、复制、引用和流式行为。

**Architecture:** `MessageList` 只虚拟化历史 `messages`；加载错误横幅、空状态、流式行、排队追问、命令回复和错误提示仍在虚拟列表之外。滚动容器仍是 `ChatPanel` 的 `.message-scroll`，`useAutoFollow` 继续观察 `.message-flow` 的总高度。定位消息从 DOM 查找改为 `scrollToIndex`。

**Tech Stack:** React 18、TypeScript、`@tanstack/react-virtual` ^3、Vite。

**对应设计文档：** `docs/superpowers/specs/2026-11-12-frontend-performance-design.md`（P2）。

---

### Task 1: 虚拟化消息列表

**Files:**
- Modify: `apps/web/src/components/MessageList.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: 引入虚拟化依赖与 ref**

在 `MessageList.tsx` 顶部加入：

```tsx
import { useVirtualizer } from "@tanstack/react-virtual";
import type { RefObject } from "react";
```

在 `MessageListProps` 中加入：

```tsx
  scrollRef: RefObject<HTMLDivElement | null>;
  pauseFollow: () => void;
  focusMessageId: string;
  focusMessageNonce: number;
```

- [ ] **Step 2: 在 MessageList 内建立虚拟器**

在 `MessageList` 组件开头加入：

```tsx
  const listRef = useRef<HTMLDivElement | null>(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  const [highlightId, setHighlightId] = useState("");
  const focusAppliedRef = useRef(0);
  const highlightTimerRef = useRef<number | null>(null);

  const virtualizer = useVirtualizer({
    count: props.messages.length,
    getScrollElement: () => props.scrollRef.current,
    estimateSize: () => 120,
    overscan: 8,
    getItemKey: (index) => props.messages[index]?.id ?? index,
    scrollMargin,
  });
```

- [ ] **Step 3: 测量列表相对滚动容器的偏移**

`loadError` 横幅在列表上方，需要把它的高度计入 `scrollMargin`：

```tsx
  useLayoutEffect(() => {
    const list = listRef.current;
    const scroll = props.scrollRef.current;
    if (!list || !scroll) return;
    const update = () => {
      const next =
        list.getBoundingClientRect().top -
        scroll.getBoundingClientRect().top -
        scroll.clientTop +
        scroll.scrollTop;
      setScrollMargin((current) => (Math.abs(current - next) < 1 ? current : next));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(list);
    if (list.parentElement) observer.observe(list.parentElement);
    return () => observer.disconnect();
  }, [props.scrollRef]);
```

需要在导入中加入 `useLayoutEffect`。

- [ ] **Step 4: 把历史消息渲染换成虚拟列表**

用下面的结构替换 `props.messages.map(...)`：

```tsx
      <div
        ref={listRef}
        className="message-virtual"
        style={{ height: virtualizer.getTotalSize() }}
      >
        {virtualizer.getVirtualItems().map((item) => {
          const message = props.messages[item.index];
          if (!message) return null;
          return (
            <div
              key={item.key}
              data-index={item.index}
              ref={virtualizer.measureElement}
              className="message-virtual-row"
              style={{ transform: `translateY(${item.start - scrollMargin}px)` }}
            >
              <MessageRow
                message={message}
                agentId={props.agentId}
                agentName={props.agentName}
                highlighted={highlightId === message.id}
                onQuote={props.onQuote}
              />
            </div>
          );
        })}
      </div>
```

- [ ] **Step 5: 抽出 `MessageRow`**

把原来地图里的 system/user/assistant 分支移进一个组件：

```tsx
function MessageRow({
  message,
  agentId,
  agentName,
  highlighted,
  onQuote,
}: {
  message: TranscriptMessage;
  agentId: string;
  agentName: string;
  highlighted: boolean;
  onQuote: (text: string) => void;
}) {
  if (message.role === "system") {
    return (
      <div className="message-row system" role="note">
        <span className="message-system">{message.content}</span>
      </div>
    );
  }
  if (message.role === "user") {
    return (
      <UserMessageRow
        message={message}
        agentId={agentId}
        highlighted={highlighted}
        onQuote={onQuote}
      />
    );
  }
  return (
    <AssistantMessageRow
      message={message}
      agentId={agentId}
      agentName={agentName}
      highlighted={highlighted}
      onQuote={onQuote}
    />
  );
}
```

- [ ] **Step 6: 把定位消息逻辑移进 MessageList**

从 `ChatPanel` 移除 `highlightId`、`focusAppliedRef`、`highlightTimerRef` 和对应 `useEffect`，在 `MessageList` 中加入：

```tsx
  useEffect(() => {
    const target = props.focusMessageId;
    if (!target) return;
    if (focusAppliedRef.current === props.focusMessageNonce) return;
    focusAppliedRef.current = props.focusMessageNonce;
    props.pauseFollow();
    const index = props.messages.findIndex((message) => message.id === target);
    if (index >= 0) {
      virtualizer.scrollToIndex(index, { align: "center" });
      window.requestAnimationFrame(() => {
        virtualizer.scrollToIndex(index, { align: "center" });
      });
    } else {
      document
        .getElementById(`message-${target}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    setHighlightId(target);
    if (highlightTimerRef.current !== null) {
      window.clearTimeout(highlightTimerRef.current);
    }
    highlightTimerRef.current = window.setTimeout(() => {
      setHighlightId("");
      highlightTimerRef.current = null;
    }, 2400);
  }, [
    props.focusMessageId,
    props.focusMessageNonce,
    props.messages,
    props.pauseFollow,
    virtualizer,
  ]);

  useEffect(
    () => () => {
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current);
      }
    },
    [],
  );
```

- [ ] **Step 7: 增加虚拟列表样式**

在 `apps/web/src/styles.css` 的 `.message-flow` 规则后加入：

```css
.message-virtual {
  position: relative;
  width: 100%;
}

.message-virtual-row {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  display: flow-root;
}

/* Virtual rows mount and unmount while scrolling; replaying the entry
   animation makes long sessions blink. */
.message-virtual-row .message-row {
  animation: none;
}
```

- [ ] **Step 8: 更新 ChatPanel 传参与清理**

`ChatPanel` 向 `MessageList` 传入：

```tsx
            scrollRef={scrollRef}
            pauseFollow={pauseFollow}
            focusMessageId={props.focusMessageId}
            focusMessageNonce={props.focusMessageNonce}
```

删除 `ChatPanel` 中不再使用的 `highlightId` 状态和 `highlightId={highlightId}` 传参。保留 `props.focusMessageId` / `props.focusMessageNonce` 作为透传。

- [ ] **Step 9: 类型检查与构建**

Run: `cd apps/web && npm run build`

Expected: PASS。

- [ ] **Step 10: 提交**

```bash
git add apps/web/src/components/MessageList.tsx apps/web/src/components/ChatPanel.tsx \
  apps/web/src/styles.css apps/web/package.json apps/web/package-lock.json
git commit -m "Virtualize long chat message lists"
```

---

### Task 2: 验证与文档

**Files:**
- Modify: `docs/intent.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 浏览器验证**

用临时测试页挂载真实 `MessageList`，造 1000 条消息，验证：

- DOM 中 `.message-virtual-row` 数量保持在窗口内（预期 10–30），不是 1000。
- 贴底追加消息仍自动跟随。
- 上滚后追加不移动视图，点「回到最新」恢复。
- 定位中间某条消息能滚动到它并高亮。
- 图片/代码块高度测量后滚动不跳。

验证完删除临时文件。

- [ ] **Step 2: 完整检查**

```bash
.venv/bin/python -m pytest apps/api/app/tests -q
cd apps/web && npm run build
```

- [ ] **Step 3: 更新文档**

在 `docs/intent.md` 的 F21 功能点中补一条：

```markdown
5. 聊天历史消息列表虚拟化，只渲染可视区附近的窗口；浏览器 Ctrl+F 只能命中已渲染的消息。
```

在 `CLAUDE.md` 的聊天跟随条目后补：

```markdown
- 长会话消息列表使用 `@tanstack/react-virtual` 窗口化；历史消息只渲染可视区附近，定位消息通过 `scrollToIndex`，DOM 查找只用于窗口外的排队消息。
```

- [ ] **Step 4: 提交并推送**

```bash
git add .
git commit -m "Document chat message virtualization"
git push origin HEAD
```

---

## 验收清单

- [ ] 1000 条消息的会话 DOM 行数保持在固定窗口内。
- [ ] 发送、流式、排队追问的跟随行为与阶段 1 一致。
- [ ] 上滚暂停、回到最新恢复。
- [ ] 定位消息、复制、引用、产物、图片、代码块、公式正常。
- [ ] 后端测试与前端构建通过。
- [ ] 视觉风格不变（虚拟化行不做入场动画，避免滚动时闪烁）。
