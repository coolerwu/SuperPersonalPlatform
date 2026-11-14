import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { RefObject } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { api } from "../api";
import type {
  FrozenLive,
  LiveSubagent,
  LiveTask,
  LiveToolCall,
  MessageArtifact,
  QueuedInfo,
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
import { ArtifactGrid } from "./ArtifactGrid";
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
  queueIndex = 0,
  queueTotal = 0,
  queueCanCancel = false,
  queueCancelling = false,
  onCancelQueued,
}: {
  message: TranscriptMessage;
  agentId: string;
  highlighted: boolean;
  onQuote: (text: string) => void;
  queueIndex?: number;
  queueTotal?: number;
  queueCanCancel?: boolean;
  queueCancelling?: boolean;
  onCancelQueued?: (messageId: string) => void;
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
      {queueTotal > 0 && (
        <div className="message-queue-state">
          <span className="message-queue-label">
            {queueCanCancel ? `排队中 ${queueIndex}/${queueTotal}` : "正在提交…"}
          </span>
          <button
            type="button"
            className="message-queue-cancel"
            disabled={!queueCanCancel || queueCancelling}
            aria-label={
              queueCanCancel
                ? `撤回第 ${queueIndex} 条排队消息`
                : "正在提交，暂不可撤回"
            }
            title={
              queueCanCancel
                ? `撤回第 ${queueIndex} 条排队消息`
                : "正在提交，暂不可撤回"
            }
            onClick={() => onCancelQueued?.(message.id)}
          >
            ×
          </button>
        </div>
      )}
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
        <ArtifactGrid artifacts={message.artifacts ?? []} agentId={agentId} />
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
  stopped = false,
}: {
  streamText: string;
  streamReasoning: string;
  activity: LiveToolCall[];
  subagents: Record<string, LiveSubagent>;
  tasks: Record<string, LiveTask>;
  liveArtifacts: MessageArtifact[];
  agentId: string;
  agentName: string;
  stopped?: boolean;
}) {
  const latestActivity = activity[activity.length - 1]?.name ?? "";
  return (
    <div className="message-row assistant">
      <div className="message-bubble assistant streaming">
        <div className="message-head">
          <span className="message-author">{agentName || "Agent"}</span>
          {stopped && (
            <span className="message-stopped" role="status">
              已停止
            </span>
          )}
        </div>
        {!stopped && latestActivity && (
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
        ) : stopped ? null : (
          <div className="muted">正在生成…</div>
        )}
        <ArtifactGrid artifacts={liveArtifacts} agentId={agentId} />
        {!stopped && <span className="cursor" />}
      </div>
    </div>
  );
}

export type MessageListProps = {
  messages: TranscriptMessage[];
  queuedMessages: TranscriptMessage[];
  queuedInfo: Record<string, QueuedInfo>;
  cancellingQueued: Record<string, true>;
  onCancelQueued: (messageId: string) => void;
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
  frozen: FrozenLive | null;
  sending: boolean;
  tracking: boolean;
  error: string;
  commandReply: { command: string; text: string } | null;
  agentId: string;
  agentName: string;
  onQuote: (text: string) => void;
  scrollRef: RefObject<HTMLDivElement | null>;
  pauseFollow: () => void;
  focusMessageId: string;
  focusMessageNonce: number;
};

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

export const MessageList = memo(function MessageList(props: MessageListProps) {
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
      setScrollMargin((current) =>
        Math.abs(current - next) < 1 ? current : next,
      );
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(list);
    if (list.parentElement) observer.observe(list.parentElement);
    return () => observer.disconnect();
  }, [props.scrollRef]);

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

      {props.frozen && (
        <StreamingRow
          streamText={props.frozen.streamText}
          streamReasoning={props.frozen.streamReasoning}
          activity={props.frozen.activity}
          subagents={props.frozen.subagents}
          tasks={props.frozen.tasks}
          liveArtifacts={props.frozen.artifacts}
          agentId={props.agentId}
          agentName={props.agentName}
          stopped
        />
      )}

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
          highlighted={highlightId === message.id}
          onQuote={props.onQuote}
          queueIndex={props.queuedInfo[message.id]?.index ?? 0}
          queueTotal={props.queuedInfo[message.id]?.total ?? 0}
          queueCanCancel={props.queuedInfo[message.id]?.canCancel ?? false}
          queueCancelling={Boolean(props.cancellingQueued[message.id])}
          onCancelQueued={props.onCancelQueued}
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

