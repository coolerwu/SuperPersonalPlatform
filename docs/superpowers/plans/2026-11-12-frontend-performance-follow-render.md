# 前端性能优化：跟随与渲染隔离实施计划（阶段 1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复发送消息后不自动跟随，并消除长会话里由轮询和流式刷新引起的整树重渲染。

**Architecture:** 新增 `useAutoFollow` 把"是否贴底"从滚动位置判断改为用户意图判断；把消息渲染从 `ChatPanel` 拆成 memo 化的 `MessageList` 和消息行组件；侧栏轮询结果做逐字段等值比较，未变化时不更新状态。视觉样式和后端接口不变。

**Tech Stack:** React 18 + TypeScript + Vite。项目当前没有前端测试框架，验证以 `tsc --noEmit`、`vite build` 和手工矩阵为准；本阶段不新增测试依赖。

**对应设计文档：** `docs/superpowers/specs/2026-11-12-frontend-performance-design.md`（P0 + P1）。

---

### Task 1: 复现发送后不跟随的问题

**Files:**
- 无代码改动

- [ ] **Step 1: 准备一个长会话**

启动本地后端与前端：

```bash
cd apps/api && ../../.venv/bin/uvicorn app.main:app --reload --port 8000
```

另开终端：

```bash
cd apps/web && npm run dev
```

用一个已有 Agent 的会话，或连续发送 20 条带代码块/图片的消息，使会话超过 40 条消息。若本机没有可用会话，记录"无法本地复现"，改在生产的同一会话上验证，后续步骤不变。

- [ ] **Step 2: 复现跟随失败**

在消息列表滚到底部后发送一条消息，观察新消息是否进入视口；再在流式输出期间缓慢滚动一点，观察是否停止跟随。

Expected before fix: 至少一种情况下视图停在原位，新消息或流式内容在视口下方，且右下角没有"回到最新"按钮（状态被误判为已贴底）。

- [ ] **Step 3: 记录现象**

把复现步骤和现象写进实现提交说明，供修复后对照。

---

### Task 2: 新增 `useAutoFollow` 跟随 Hook

**Files:**
- Create: `apps/web/src/useAutoFollow.ts`

- [ ] **Step 1: 创建 Hook 文件**

写入以下完整内容：

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

const BOTTOM_THRESHOLD = 96;
const INTENT_WINDOW_MS = 600;
const SHRINK_WINDOW_MS = 250;

function isScrollKey(key: string): boolean {
  return ["ArrowUp", "PageUp", "Home", " "].includes(key);
}

/**
 * Pins a chat scroll container to the newest content unless the user scrolls
 * away. Scroll events caused by content growth or programmatic scrolling never
 * release the pin; only real user input does.
 */
export function useAutoFollow(
  scrollRef: RefObject<HTMLElement | null>,
  contentRef: RefObject<HTMLElement | null>,
) {
  const stickRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const intentUntilRef = useRef(0);
  const shrinkUntilRef = useRef(0);
  const lastHeightRef = useRef(0);
  const lastTopRef = useRef(0);
  const frameRef = useRef<number | null>(null);

  const setStick = useCallback((next: boolean) => {
    if (stickRef.current === next) return;
    stickRef.current = next;
    setAtBottom(next);
  }, []);

  const scrollToBottom = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
    lastTopRef.current = element.scrollTop;
  }, [scrollRef]);

  const follow = useCallback(() => {
    setStick(true);
    scrollToBottom();
    window.requestAnimationFrame(() => {
      scrollToBottom();
      window.requestAnimationFrame(scrollToBottom);
    });
  }, [scrollToBottom, setStick]);

  const pauseFollow = useCallback(() => {
    setStick(false);
  }, [setStick]);

  const scheduleFollow = useCallback(() => {
    if (!stickRef.current || frameRef.current !== null) return;
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = null;
      if (stickRef.current) scrollToBottom();
    });
  }, [scrollToBottom]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;

    const markIntent = () => {
      intentUntilRef.current = performance.now() + INTENT_WINDOW_MS;
    };
    const onScroll = () => {
      const distance =
        element.scrollHeight - element.scrollTop - element.clientHeight;
      const top = element.scrollTop;
      const delta = top - lastTopRef.current;
      lastTopRef.current = top;
      if (distance < BOTTOM_THRESHOLD) {
        setStick(true);
        return;
      }
      if (performance.now() < shrinkUntilRef.current) return;
      if (performance.now() < intentUntilRef.current || delta < -8) {
        setStick(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }
      if (isScrollKey(event.key)) markIntent();
    };

    element.addEventListener("scroll", onScroll, { passive: true });
    element.addEventListener("wheel", markIntent, { passive: true });
    element.addEventListener("touchmove", markIntent, { passive: true });
    element.addEventListener("pointerdown", markIntent);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      element.removeEventListener("scroll", onScroll);
      element.removeEventListener("wheel", markIntent);
      element.removeEventListener("touchmove", markIntent);
      element.removeEventListener("pointerdown", markIntent);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [scrollRef, setStick]);

  useEffect(() => {
    const content = contentRef.current;
    const element = scrollRef.current;
    if (!content || !element) return;
    const observer = new ResizeObserver(() => {
      const height = element.scrollHeight;
      if (height < lastHeightRef.current) {
        shrinkUntilRef.current = performance.now() + SHRINK_WINDOW_MS;
      }
      lastHeightRef.current = height;
      if (stickRef.current) scheduleFollow();
    });
    observer.observe(content);
    observer.observe(element);
    return () => observer.disconnect();
  }, [contentRef, scrollRef, scheduleFollow]);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const onResize = () => {
      if (stickRef.current) scheduleFollow();
    };
    viewport.addEventListener("resize", onResize);
    return () => viewport.removeEventListener("resize", onResize);
  }, [scheduleFollow]);

  useEffect(
    () => () => {
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
    },
    [],
  );

  return { atBottom, follow, pauseFollow };
}
```

- [ ] **Step 2: 类型检查**

Run: `cd apps/web && npx tsc --noEmit`

Expected: PASS，无输出。

- [ ] **Step 3: 提交**

```bash
git add apps/web/src/useAutoFollow.ts
git commit -m "Add user-intent auto-follow hook for chat scrolling"
```

---

### Task 3: 新增消息列表组件 `MessageList`

**Files:**
- Create: `apps/web/src/components/MessageList.tsx`

- [ ] **Step 1: 创建完整组件文件**

写入以下内容。它把现有 `ChatPanel` 中的消息渲染搬过来，并对消息行做 memo：

```tsx
import { memo, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import type {
  LiveSubagent,
  LiveTask,
  LiveToolCall,
  MessageArtifact,
  TranscriptMessage,
} from "../types";
import { copyText } from "../clipboard";
import { truncate } from "../format";
import {
  formatBytes,
  isImageName,
  parseUserContent,
  userMessageText,
} from "../messageContent";
import { workspaceFileUrl } from "../workspaceUrl";
import { Markdown } from "./Markdown";

const SUGGESTIONS = [
  "帮我写一个 Python 脚本",
  "总结这段内容的要点",
  "解释一个技术概念",
];

type ProcessStep =
  | { kind: "thinking"; text: string }
  | { kind: "tool"; name: string; input: Record<string, unknown> | null };

function commandReplyLabel(command: string): string {
  switch (command) {
    case "/new":
    case "/clear":
      return "新会话";
    case "/help":
      return "命令说明";
    case "/status":
      return "会话状态";
    case "/stop":
      return "停止任务";
    default:
      return "命令";
  }
}

function CopyButton({ text, label = "复制" }: { text: string; label?: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    const copied = await copyText(text);
    setStatus(copied ? "copied" : "failed");
    window.setTimeout(() => setStatus("idle"), 1500);
  }

  return (
    <button type="button" className="message-action" onClick={() => void copy()}>
      {status === "copied" ? "已复制" : status === "failed" ? "复制失败" : label}
    </button>
  );
}

function FileIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

function ArtifactStrip({
  artifacts,
  agentId,
}: {
  artifacts: MessageArtifact[];
  agentId: string;
}) {
  if (artifacts.length === 0) return null;
  const images = artifacts.filter((artifact) => artifact.kind === "image");
  const files = artifacts.filter((artifact) => artifact.kind !== "image");
  return (
    <div className="message-artifacts">
      <div className="message-artifacts-label">产物</div>
      {images.length > 0 && (
        <div className="message-images">
          {images.map((artifact) => {
            const url = workspaceFileUrl(agentId, artifact.path) ?? "";
            return (
              <a
                className="message-image"
                key={artifact.path}
                href={url}
                target="_blank"
                rel="noreferrer"
                title={`打开 ${artifact.name}`}
              >
                <img src={url} alt={artifact.name} loading="lazy" />
              </a>
            );
          })}
        </div>
      )}
      {files.length > 0 && (
        <div className="message-files">
          {files.map((artifact) => (
            <a
              className="message-file"
              key={artifact.path}
              href={workspaceFileUrl(agentId, artifact.path) ?? ""}
              target="_blank"
              rel="noreferrer"
              title={artifact.path}
            >
              <FileIcon />
              <span className="message-file-name">{artifact.name}</span>
              {artifact.size !== null && (
                <span className="message-file-size">
                  {formatBytes(artifact.size)}
                </span>
              )}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

/** Raw Anthropic blocks from the SDK transcript, merged for display. */
function processSteps(blocks: TranscriptMessage["blocks"]): ProcessStep[] {
  const steps: ProcessStep[] = [];
  for (const block of blocks) {
    if (block.type === "thinking") {
      const text = String(block.thinking ?? "");
      if (!text.trim()) continue;
      const last = steps[steps.length - 1];
      if (last?.kind === "thinking") last.text = `${last.text}\n\n${text}`;
      else steps.push({ kind: "thinking", text });
    } else if (block.type === "tool_use") {
      const input =
        block.input && typeof block.input === "object"
          ? (block.input as Record<string, unknown>)
          : null;
      steps.push({ kind: "tool", name: String(block.name ?? "工具调用"), input });
    }
  }
  return steps;
}

function toolSummary(input: Record<string, unknown> | null): string {
  if (!input) return "";
  for (const key of ["path", "file_path", "command", "query", "url", "symbol", "name"]) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return truncate(value, 80);
  }
  return "";
}

function AssistantProcess({
  steps,
  agentId,
}: {
  steps: ProcessStep[];
  agentId: string;
}) {
  if (steps.length === 0) return null;
  const toolCount = steps.filter((step) => step.kind === "tool").length;
  return (
    <details className="message-thinking run-trace">
      <summary>
        <span>执行过程</span>
        <span className="run-trace-count">
          {steps.length} 步{toolCount > 0 ? ` · ${toolCount} 个工具` : ""}
        </span>
      </summary>
      <div className="run-trace-steps">
        {steps.map((step, index) =>
          step.kind === "thinking" ? (
            <div className="run-trace-reasoning" key={`thinking-${index}`}>
              <div className="run-trace-step-label">思考</div>
              <div className="message-thinking-body">
                <Markdown content={step.text} agentId={agentId} />
              </div>
            </div>
          ) : (
            <div className="run-trace-tool" key={`tool-${index}`}>
              <span className="run-trace-tool-dot" aria-hidden="true" />
              <span className="run-trace-tool-label">{step.name}</span>
              {toolSummary(step.input) && (
                <span className="run-trace-tool-name">{toolSummary(step.input)}</span>
              )}
            </div>
          ),
        )}
      </div>
    </details>
  );
}

function taskStatusLabel(status: string): string {
  switch (status) {
    case "running":
      return "运行中";
    case "pending":
      return "等待中";
    case "paused":
      return "已暂停";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "stopped":
    case "killed":
      return "已停止";
    default:
      return status || "进行中";
  }
}

function taskStatusClass(status: string): string {
  return ["completed", "failed", "stopped", "killed", "paused"].includes(status)
    ? status
    : "running";
}

function LiveProcess({
  reasoning,
  activity,
  subagents,
  tasks,
  streamingText,
  agentId,
}: {
  reasoning: string;
  activity: LiveToolCall[];
  subagents: Record<string, LiveSubagent>;
  tasks: Record<string, LiveTask>;
  streamingText: boolean;
  agentId: string;
}) {
  const [open, setOpen] = useState(true);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);
  const taskList = Object.values(tasks);
  const subagentList = Object.values(subagents);
  const subagentTextLength = subagentList.reduce(
    (total, item) => total + item.text.length,
    0,
  );
  const totalTools =
    activity.length +
    subagentList.reduce((total, item) => total + item.tools.length, 0);
  useEffect(() => {
    // Collapse once the answer starts, like the previous run trace.
    if (streamingText) setOpen(false);
  }, [streamingText]);
  useEffect(() => {
    const body = bodyRef.current;
    if (!body || !open || !followRef.current) return;
    body.scrollTop = body.scrollHeight;
  }, [reasoning, activity.length, taskList.length, subagentTextLength, open]);
  const hasReasoning = Boolean(reasoning.trim());
  if (
    !hasReasoning &&
    activity.length === 0 &&
    taskList.length === 0 &&
    subagentList.length === 0
  ) {
    return null;
  }
  return (
    <details
      className="message-thinking run-trace"
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        <span>执行过程</span>
        <span className="run-trace-count">
          {[
            totalTools > 0 ? `${totalTools} 个工具` : "",
            taskList.length > 0 ? `${taskList.length} 个任务` : "",
          ]
            .filter(Boolean)
            .join(" · ") || "思考"}
        </span>
      </summary>
      <div className="run-trace-steps">
        {taskList.length > 0 && (
          <div className="run-trace-tasks">
            {taskList.map((task) => (
              <div
                className={`run-trace-task ${taskStatusClass(task.status)}`}
                key={task.id}
              >
                <span className="run-trace-task-dot" aria-hidden="true" />
                <span className="run-trace-tool-label">
                  {task.description || task.id}
                </span>
                <span className="run-trace-task-status">
                  {taskStatusLabel(task.status)}
                </span>
                {task.lastToolName && (
                  <span className="run-trace-tool-name">{task.lastToolName}</span>
                )}
                {task.summary && (
                  <small className="run-trace-task-summary">{task.summary}</small>
                )}
              </div>
            ))}
          </div>
        )}
        {hasReasoning && (
          <div className="run-trace-reasoning">
            <div className="run-trace-step-label">思考</div>
            <div
              className="message-thinking-body"
              ref={bodyRef}
              onScroll={() => {
                const body = bodyRef.current;
                if (!body) return;
                followRef.current =
                  body.scrollHeight - body.scrollTop - body.clientHeight < 48;
              }}
            >
              <Markdown content={reasoning} agentId={agentId} />
            </div>
          </div>
        )}
        {activity.map((tool, index) => (
          <div className="run-trace-tool" key={`${tool.id || tool.name}-${index}`}>
            <span className="run-trace-tool-dot" aria-hidden="true" />
            <span className="run-trace-tool-label">{tool.name}</span>
            {toolSummary(tool.input) && (
              <span className="run-trace-tool-name">{toolSummary(tool.input)}</span>
            )}
          </div>
        ))}
        {subagentList.map((subagent) => {
          const parent = activity.find((tool) => tool.id === subagent.parent);
          return (
            <div className="run-trace-subagent" key={subagent.parent}>
              <div className="run-trace-subagent-head">
                <span className="run-trace-step-label">子 Agent</span>
                <span className="run-trace-tool-label">
                  {parent ? parent.name : "子 Agent"}
                </span>
                <span className="run-trace-tool-name">
                  {subagent.tools.length} 个工具
                </span>
              </div>
              {subagent.text && (
                <div className="run-trace-subagent-text">
                  <Markdown content={subagent.text} agentId={agentId} />
                </div>
              )}
              {subagent.tools.map((tool, index) => (
                <div
                  className="run-trace-tool nested"
                  key={`${tool.id || tool.name}-${index}`}
                >
                  <span className="run-trace-tool-dot" aria-hidden="true" />
                  <span className="run-trace-tool-label">{tool.name}</span>
                  {toolSummary(tool.input) && (
                    <span className="run-trace-tool-name">
                      {toolSummary(tool.input)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </details>
  );
}

function MessageSkeleton() {
  return (
    <div className="message-skeleton" role="status" aria-label="正在加载消息">
      {[0, 1, 2].map((index) => (
        <div
          key={index}
          className={`message-skeleton-row ${index % 2 === 1 ? "user" : "assistant"}`}
        >
          {index % 2 === 0 && <span className="skeleton-avatar" aria-hidden="true" />}
          <span className="skeleton-bubble" aria-hidden="true">
            <span className="skeleton-line w-80" />
            <span className="skeleton-line w-55" />
            <span className="skeleton-line w-35" />
          </span>
        </div>
      ))}
    </div>
  );
}

const UserMessageRow = memo(function UserMessageRow({
  message,
  agentId,
  highlighted,
  onQuote,
}: {
  message: TranscriptMessage;
  agentId: string;
  highlighted: boolean;
  onQuote: (text: string) => void;
}) {
  const parsed = useMemo(() => parseUserContent(message.content), [message.content]);
  const copyContent = useMemo(
    () => userMessageText(message.content),
    [message.content],
  );
  return (
    <div
      id={`message-${message.id}`}
      className={`message-row user ${highlighted ? "message-focus" : ""}`}
    >
      <div className="message-bubble user">
        {parsed.kind === "attachments" ? (
          <>
            {parsed.files.some((file) => isImageName(file.name)) && (
              <div className="message-images">
                {parsed.files
                  .filter((file) => isImageName(file.name))
                  .map((file, index) => {
                    const url = api.uploadUrl(agentId, file.path);
                    return (
                      <a
                        className="message-image"
                        key={`${file.path}-${index}`}
                        href={url}
                        target="_blank"
                        rel="noreferrer"
                        title={`打开 ${file.name}`}
                      >
                        <img src={url} alt={file.name} loading="lazy" />
                      </a>
                    );
                  })}
              </div>
            )}
            {parsed.files.some((file) => !isImageName(file.name)) && (
              <div className="message-files">
                {parsed.files
                  .filter((file) => !isImageName(file.name))
                  .map((file, index) => (
                    <span
                      className="message-file"
                      key={`${file.path}-${index}`}
                      title={file.path}
                    >
                      <FileIcon />
                      <span className="message-file-name">{file.name}</span>
                      {file.size !== null && (
                        <span className="message-file-size">
                          {formatBytes(file.size)}
                        </span>
                      )}
                    </span>
                  ))}
              </div>
            )}
            {parsed.text && <Markdown content={parsed.text} agentId={agentId} />}
          </>
        ) : (
          <Markdown content={message.content} agentId={agentId} />
        )}
      </div>
      <div className="message-footer user">
        <div className="message-actions">
          <CopyButton text={copyContent} />
          <button
            type="button"
            className="message-action"
            onClick={() => onQuote(copyContent)}
          >
            引用
          </button>
        </div>
      </div>
    </div>
  );
});

const AssistantMessageRow = memo(function AssistantMessageRow({
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
  const steps = useMemo(() => processSteps(message.blocks), [message.blocks]);
  return (
    <div
      id={`message-${message.id}`}
      className={`message-row assistant ${highlighted ? "message-focus" : ""}`}
    >
      <div className="message-bubble assistant">
        <div className="message-head">
          <span className="message-author">{agentName || "Agent"}</span>
        </div>
        <AssistantProcess steps={steps} agentId={agentId} />
        {message.content && <Markdown content={message.content} agentId={agentId} />}
        <ArtifactStrip artifacts={message.artifacts ?? []} agentId={agentId} />
      </div>
      <div className="message-footer assistant">
        <div className="message-actions">
          <CopyButton text={message.content} />
          <button
            type="button"
            className="message-action"
            onClick={() => onQuote(message.content)}
          >
            引用
          </button>
        </div>
      </div>
    </div>
  );
});

function StreamingRow({
  streamText,
  streamReasoning,
  activity,
  subagents,
  tasks,
  liveArtifacts,
  agentId,
  agentName,
}: {
  streamText: string;
  streamReasoning: string;
  activity: LiveToolCall[];
  subagents: Record<string, LiveSubagent>;
  tasks: Record<string, LiveTask>;
  liveArtifacts: MessageArtifact[];
  agentId: string;
  agentName: string;
}) {
  const latestActivity = activity[activity.length - 1]?.name ?? "";
  return (
    <div className="message-row assistant">
      <div className="message-bubble assistant streaming">
        <div className="message-head">
          <span className="message-author">{agentName || "Agent"}</span>
        </div>
        {latestActivity && (
          <div className="run-activity" role="status">
            <span className="run-activity-dot" aria-hidden="true" />
            <span className="run-activity-text">{latestActivity}</span>
          </div>
        )}
        <LiveProcess
          reasoning={streamReasoning}
          activity={activity}
          subagents={subagents}
          tasks={tasks}
          streamingText={Boolean(streamText)}
          agentId={agentId}
        />
        {streamText ? (
          <Markdown content={streamText} streaming agentId={agentId} />
        ) : (
          <div className="muted">正在生成…</div>
        )}
        <ArtifactStrip artifacts={liveArtifacts} agentId={agentId} />
        <span className="cursor" />
      </div>
    </div>
  );
}

export type MessageListProps = {
  messages: TranscriptMessage[];
  queuedMessages: TranscriptMessage[];
  loading: boolean;
  loadError: string;
  onReload: () => void;
  onUseSuggestion: (text: string) => void;
  streamText: string;
  streamReasoning: string;
  activity: LiveToolCall[];
  subagents: Record<string, LiveSubagent>;
  tasks: Record<string, LiveTask>;
  liveArtifacts: MessageArtifact[];
  sending: boolean;
  tracking: boolean;
  error: string;
  commandReply: { command: string; text: string } | null;
  agentId: string;
  agentName: string;
  highlightId: string;
  onQuote: (text: string) => void;
};

export const MessageList = memo(function MessageList(props: MessageListProps) {
  const showStreaming = props.tracking || Boolean(props.streamText);
  return (
    <>
      {props.loadError && (
        <div className="error-banner in-chat" role="alert">
          <span>{props.loadError}</span>
          <button type="button" className="ghost-button" onClick={props.onReload}>
            重新加载
          </button>
        </div>
      )}

      {props.loading && props.messages.length === 0 && <MessageSkeleton />}

      {!props.loading &&
        props.messages.length === 0 &&
        !props.sending &&
        !props.commandReply &&
        !props.loadError && (
          <div className="chat-empty">
            <div className="chat-empty-mark">ACC</div>
            <h2>和 {props.agentName || "Agent"} 开始对话</h2>
            <p>支持 Markdown、代码块和消息复制；流式输出会实时显示。</p>
            <div className="suggestion-list">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => props.onUseSuggestion(suggestion)}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

      {props.messages.map((message) => {
        if (message.role === "system") {
          return (
            <div key={message.id} className="message-row system" role="note">
              <span className="message-system">{message.content}</span>
            </div>
          );
        }
        if (message.role === "user") {
          return (
            <UserMessageRow
              key={message.id}
              message={message}
              agentId={props.agentId}
              highlighted={props.highlightId === message.id}
              onQuote={props.onQuote}
            />
          );
        }
        return (
          <AssistantMessageRow
            key={message.id}
            message={message}
            agentId={props.agentId}
            agentName={props.agentName}
            highlighted={props.highlightId === message.id}
            onQuote={props.onQuote}
          />
        );
      })}

      {showStreaming && (
        <StreamingRow
          streamText={props.streamText}
          streamReasoning={props.streamReasoning}
          activity={props.activity}
          subagents={props.subagents}
          tasks={props.tasks}
          liveArtifacts={props.liveArtifacts}
          agentId={props.agentId}
          agentName={props.agentName}
        />
      )}

      {props.queuedMessages.map((message) => (
        <UserMessageRow
          key={message.id}
          message={message}
          agentId={props.agentId}
          highlighted={props.highlightId === message.id}
          onQuote={props.onQuote}
        />
      ))}

      {props.commandReply && (
        <div className="message-row system">
          <div className="message-bubble system">
            <div className="message-command-reply-head">
              <span className="message-command-tag">
                {commandReplyLabel(props.commandReply.command)}
              </span>
              <span className="message-command-code">
                {props.commandReply.command}
              </span>
            </div>
            <div className="message-command-reply">{props.commandReply.text}</div>
          </div>
        </div>
      )}

      {props.error && (
        <div className="error-banner in-chat">
          <span>{props.error}</span>
        </div>
      )}
    </>
  );
});
```

- [ ] **Step 2: 检查导入与签名**

确认文件顶部第一行是：

```tsx
import { memo, useEffect, useMemo, useRef, useState } from "react";
```

确认 `AssistantMessageRow` 和 `StreamingRow` 都接收 `agentName`，`MessageList` 已经传入 `agentName={props.agentName}`，`commandReplyLabel` 已在文件中定义。

- [ ] **Step 3: 类型检查**

Run: `cd apps/web && npx tsc --noEmit`

Expected: 失败信息只来自 `ChatPanel.tsx` 仍引用旧组件（下一步处理），`MessageList.tsx` 本身无错误。

- [ ] **Step 4: 提交**

```bash
git add apps/web/src/components/MessageList.tsx
git commit -m "Extract memoized message list from chat panel"
```

---

### Task 4: 在 `ChatPanel` 接入跟随 Hook 与 `MessageList`

**Files:**
- Modify: `apps/web/src/components/ChatPanel.tsx`

- [ ] **Step 1: 更新导入**

把导入区替换为：

```tsx
import {
  ClipboardEvent,
  DragEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { api } from "../api";
import type {
  Attachment,
  LiveSubagent,
  LiveTask,
  LiveToolCall,
  MessageArtifact,
  TranscriptMessage,
} from "../types";
import {
  filterSlashCommands,
  formatBytes,
  isImageName,
  slashCommandQuery,
} from "../messageContent";
import { prepareImageForUpload } from "../imageUpload";
import { describeApiError } from "../errors";
import { useAutoFollow } from "../useAutoFollow";
import { useThrottledText } from "../useThrottledText";
import { MessageList } from "./MessageList";
```

删除不再使用的导入：`truncate`、`copyText`、`parseUserContent`、`userMessageText`、`workspaceFileUrl`、`Markdown`。

- [ ] **Step 2: 删除已搬走的组件**

删除 `ChatPanel.tsx` 中已经搬到 `MessageList.tsx` 的以下声明：

- `ProcessStep` 类型、`SUGGESTIONS`、`commandReplyLabel`
- `CopyButton`、`FileIcon`、`ArtifactStrip`、`processSteps`、`toolSummary`、`AssistantProcess`
- `taskStatusLabel`、`taskStatusClass`、`LiveProcess`、`MessageSkeleton`、`UserMessageRow`

`MAX_ATTACHMENT_BYTES` 夹在这些声明之间，保留不动；`ChatPanelProps`、`UploadItem`、`ChatPanel` 组件和输入区代码也保留。

- [ ] **Step 3: 替换滚动状态与效应**

在 `ChatPanel` 组件开头，把：

```tsx
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const stickRef = useRef(true);
```

替换为：

```tsx
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const { atBottom, follow, pauseFollow } = useAutoFollow(scrollRef, contentRef);
  const onQuoteRef = useRef(props.onQuote);
  onQuoteRef.current = props.onQuote;
  const onReloadRef = useRef(props.onReload);
  onReloadRef.current = props.onReload;
  const onDraftChangeRef = useRef(props.onDraftChange);
  onDraftChangeRef.current = props.onDraftChange;
```

删除组件内的 `const [atBottom, setAtBottom] = useState(true);`。

- [ ] **Step 4: 用稳定回调替换内联包装**

把现有的：

```tsx
  function useSuggestion(text: string) {
    props.onDraftChange(text);
    textareaRef.current?.focus();
  }

  function quoteFromMessage(text: string) {
    props.onQuote(text);
    textareaRef.current?.focus();
  }
```

替换为：

```tsx
  const quoteFromMessage = useCallback((text: string) => {
    onQuoteRef.current(text);
    textareaRef.current?.focus();
  }, []);

  const applySuggestion = useCallback((text: string) => {
    onDraftChangeRef.current(text);
    textareaRef.current?.focus();
  }, []);

  const reloadSession = useCallback(() => onReloadRef.current(), []);
```

- [ ] **Step 5: 删除旧滚动逻辑**

删除以下内容：

- `function scrollToBottom() { ... }`
- `function scheduleScrollToBottom() { ... }`
- `useEffect(() => { if (!stickRef.current) return; scheduleScrollToBottom(); }, [...])`
- `useEffect(() => { const container = scrollRef.current; ... ResizeObserver ... }, [])`
- `function handleScroll() { ... }`

把 `function jumpToBottom()` 替换为：

```tsx
  function jumpToBottom() {
    follow();
  }
```

- [ ] **Step 6: 修改定位消息的效应**

在 `props.focusMessageId` 的 `useEffect` 中，把：

```tsx
    stickRef.current = false;
    setAtBottom(false);
```

替换为：

```tsx
    pauseFollow();
```

并把依赖数组加上 `pauseFollow`：

```tsx
  }, [props.focusMessageId, props.focusMessageNonce, props.messages, pauseFollow]);
```

- [ ] **Step 7: 修改发送逻辑**

把 `submit()` 开头：

```tsx
    // Sending always follows the newest message, even if the user had scrolled up.
    stickRef.current = true;
    setAtBottom(true);
    scrollToBottom();
```

替换为：

```tsx
    // Sending always follows the newest message, even if the user had scrolled up.
    follow();
```

- [ ] **Step 8: 替换消息列表渲染**

把：

```tsx
      <div className="message-scroll" ref={scrollRef} onScroll={handleScroll}>
        <div className="message-flow" ref={contentRef}>
        ... 整段消息渲染 ...
        </div>
      </div>
```

替换为：

```tsx
      <div className="message-scroll" ref={scrollRef}>
        <div className="message-flow" ref={contentRef}>
          <MessageList
            messages={props.messages}
            queuedMessages={props.queuedMessages}
            loading={props.loading}
            loadError={props.loadError}
            onReload={reloadSession}
            onUseSuggestion={applySuggestion}
            streamText={throttledStreamText}
            streamReasoning={throttledStreamReasoning}
            activity={props.activity}
            subagents={props.subagents}
            tasks={props.tasks}
            liveArtifacts={props.liveArtifacts}
            sending={props.sending}
            tracking={props.tracking}
            error={props.error}
            commandReply={props.commandReply}
            agentId={props.agentId}
            agentName={props.agentName}
            highlightId={highlightId}
            onQuote={quoteFromMessage}
          />
        </div>
      </div>
```

- [ ] **Step 9: 清理未使用变量**

检查并删除因重构不再使用的局部变量，例如 `showStreaming`、`latestActivity` 等；保留 `atBottom` 供"回到最新"按钮使用。

Run: `cd apps/web && npx tsc --noEmit`

Expected: PASS，无未使用变量错误。

- [ ] **Step 10: 构建**

Run: `cd apps/web && npm run build`

Expected: `tsc --noEmit` 通过，`vite build` 成功。

- [ ] **Step 11: 手工验证**

按 Task 1 的复现步骤再走一遍：

- 发送消息后视图贴底，新消息可见。
- 流式期间持续跟随。
- 主动向上滚动后停止跟随，右下角出现"回到最新"。
- 点击"回到最新"恢复贴底。
- 点击消息的"引用"后输入框获得焦点。
- 点击"定位到消息"后滚动到对应消息并闪烁。

Expected: 全部通过。

- [ ] **Step 12: 提交**

```bash
git add apps/web/src/components/ChatPanel.tsx
git commit -m "Use auto-follow hook and memoized message list in chat"
```

---

### Task 5: 侧栏轮询等值比较

**Files:**
- Create: `apps/web/src/sessionSummary.ts`
- Modify: `apps/web/src/App.tsx`

- [ ] **Step 1: 新增比较函数**

创建 `apps/web/src/sessionSummary.ts`：

```ts
import type { SessionSummary } from "./types";

/** True when two sidebar snapshots would render identically. */
export function sessionSummariesEqual(
  a: SessionSummary[],
  b: SessionSummary[],
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    const left = a[i];
    const right = b[i];
    if (left === right) continue;
    if (
      left.id !== right.id ||
      left.title !== right.title ||
      left.last_modified !== right.last_modified ||
      left.channel_type !== right.channel_type ||
      left.channel_name !== right.channel_name ||
      left.busy !== right.busy
    ) {
      return false;
    }
  }
  return true;
}
```

- [ ] **Step 2: 在 App 中应用**

在 `apps/web/src/App.tsx` 的导入区加入：

```ts
import { sessionSummariesEqual } from "./sessionSummary";
```

把：

```ts
  function applySessions(list: SessionSummary[]) {
    sessionsRef.current = list;
    setSessions(list);
  }
```

替换为：

```ts
  function applySessions(list: SessionSummary[]) {
    sessionsRef.current = list;
    setSessions((current) =>
      sessionSummariesEqual(current, list) ? current : list,
    );
  }
```

- [ ] **Step 3: 类型检查与构建**

Run: `cd apps/web && npm run build`

Expected: PASS。

- [ ] **Step 4: 手工验证**

打开一个长会话，保持页面静止 10 秒以上，用 React DevTools Profiler 或浏览器 Performance 面板观察：

- 空闲时每 5 秒不再出现整棵树提交。
- 侧栏标题、忙碌状态变化时仍立即更新。
- 切换 Agent 后会话列表仍正确刷新。

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add apps/web/src/sessionSummary.ts apps/web/src/App.tsx
git commit -m "Skip sidebar re-renders when the session snapshot is unchanged"
```

---

### Task 6: 阶段验收与文档

**Files:**
- Modify: `docs/intent.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 跑完整检查**

```bash
.venv/bin/python -m pytest apps/api/app/tests -q
cd apps/web && npm run build
```

Expected: 后端全部通过；前端类型检查与构建通过。

- [ ] **Step 2: 记录性能数据**

在长会话里用 React Profiler 记录修复前后的：

- 空闲 10 秒内的提交次数。
- 流式输出 10 秒内的提交次数和最长提交耗时。
- 滚动 10 秒内的长任务数量。

把数字写进提交说明；没有数据就写"未测量"，不要编造。

- [ ] **Step 3: 更新文档**

在 `docs/intent.md` 新增一节（编号接当前水位 F21），并把文件开头的「编号水位：F20」改为「编号水位：F21」：

```markdown
### F21 前端流畅度与聊天跟随

- 状态：现行
- 日期：2026-11-12

#### 功能点

1. 聊天视图在发送、流式输出和排队追问期间自动跟随最新内容；只有用户主动上滚才暂停，点"回到最新"恢复。
2. 消息行与消息列表按引用 memo；历史消息不随流式刷新重新渲染。
3. 侧栏轮询结果逐字段比较，内容未变化时不触发页面重渲染。
4. 视觉风格（毛玻璃、极光、珍珠冷光）保持不变。

#### 验收标准

- 长会话（300 条消息以上）发送后视图贴底，流式持续跟随，主动上滚后出现"回到最新"。
- 空闲时侧栏轮询不再引起整树重渲染。
- 后端测试与前端构建通过。
```

在 `CLAUDE.md` 的「运行约定」里补一句：

```markdown
- 聊天滚动跟随按用户意图判定：只有主动上滚才暂停跟随，布局变化和程序性滚动不改变跟随状态；消息列表按行 memo，侧栏轮询结果等值时不更新状态。
```

- [ ] **Step 4: 提交**

```bash
git add docs/intent.md CLAUDE.md
git commit -m "Document chat auto-follow and render isolation behavior"
```

---

## 阶段完成后的验证清单

- [ ] 后端：`.venv/bin/python -m pytest apps/api/app/tests -q` 全部通过。
- [ ] 前端：`cd apps/web && npm run build` 通过。
- [ ] 300 条消息长会话：发送贴底、流式跟随、上滚暂停、回到最新恢复。
- [ ] 空闲 10 秒无整树周期性提交。
- [ ] 桌面 Chrome 与 iPhone Safari 各验证一次。
- [ ] 视觉观感与改动前一致。
- [ ] `git status --short` 干净，提交范围只包含本阶段文件。

## 后续阶段

P2（长会话虚拟化）与 P3（路由分包、空闲预取、页面数据缓存）在阶段 1 验收后分别编写独立计划，避免在跟随与渲染结构稳定前提前锁定虚拟化细节。
