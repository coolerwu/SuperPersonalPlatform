import {
  ClipboardEvent,
  DragEvent,
  KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { api } from "../api";
import type { Attachment, Message, Run, ScreenshotInfo } from "../types";
import { formatTime } from "../format";
import { copyText } from "../clipboard";
import {
  filterSlashCommands,
  formatBytes,
  isImageName,
  parseUserContent,
  slashCommandQuery,
  userMessageText,
} from "../messageContent";
import { prepareImageForUpload } from "../imageUpload";
import { formatDuration, type RunTraceStep } from "../runActivity";
import { describeApiError, describeRunError } from "../errors";
import { useThrottledText } from "../useThrottledText";
import { Markdown } from "./Markdown";

type TerminalRun = {
  run: Run;
  text: string;
  reasoning: string;
};

type ChatPanelProps = {
  messages: Message[];
  loading: boolean;
  hasMore: boolean;
  loadingOlder: boolean;
  loadOlderError: string;
  onLoadOlder: () => Promise<boolean>;
  screenshots: ScreenshotInfo[];
  sessionId: string;
  streamText: string;
  streamReasoning: string;
  trace: RunTraceStep[];
  runReasoning: Record<string, string>;
  runTraces: Record<string, RunTraceStep[]>;
  sending: boolean;
  runActive: boolean;
  pendingSend: boolean;
  activity: string;
  terminalRun: TerminalRun | null;
  error: string;
  notice: string;
  commandReply: { command: string; text: string } | null;
  activeRun: Run | null;
  draft: string;
  quote: string;
  attachments: Attachment[];
  canSend: boolean;
  agentId: string;
  agentName: string;
  channelType: string;
  channelName: string;
  memorySkillEnabled: boolean;
  focusSignal: number;
  focusMessageId: string;
  focusMessageNonce: number;
  onDraftChange: (value: string) => void;
  onClearQuote: () => void;
  onAddAttachment: (file: Attachment) => void;
  onRemoveAttachment: (path: string) => void;
  onSend: (attachments: Attachment[]) => Promise<boolean>;
  onOrganizeMemory: () => Promise<boolean>;
  onCancel: () => void;
  onQuote: (text: string) => void;
  onRetry: (runId: string) => void;
};

type UploadItem = {
  id: string;
  name: string;
  size: number;
  percent: number;
  previewUrl?: string;
};

const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

const SUGGESTIONS = [
  "帮我写一个 Python 脚本",
  "总结这段内容的要点",
  "解释一个技术概念",
];

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

function elapsedSeconds(from: string | null | undefined, toMs?: number): number {
  if (!from) return 0;
  const start = Date.parse(from);
  const end = toMs ?? Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  return Math.max(0, Math.floor((end - start) / 1000));
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

function ScreenshotStrip({ sessionId, items }: { sessionId: string; items: ScreenshotInfo[] }) {
  if (items.length === 0) return null;
  return (
    <div className="message-screenshots">
      {items.map((item) => {
        const url = api.screenshotUrl(sessionId, item.run_id, item.id);
        return (
          <a
            key={item.id}
            className="message-screenshot"
            href={url}
            target="_blank"
            rel="noreferrer"
            title="打开原图"
          >
            <img src={url} alt="页面截图" loading="lazy" />
          </a>
        );
      })}
    </div>
  );
}

function ThinkingBlock({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(streaming);
  useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);
  if (!text.trim()) return null;
  return (
    <details
      className="message-thinking"
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>思考过程</summary>
      <div className="message-thinking-body">
        <Markdown content={text} />
      </div>
    </details>
  );
}

function RunTrace({
  steps,
  fallback,
  streaming,
  agentName,
}: {
  steps: RunTraceStep[];
  fallback: string;
  streaming: boolean;
  agentName: string;
}) {
  const [open, setOpen] = useState(streaming);
  useEffect(() => {
    setOpen(streaming);
  }, [streaming]);
  const lastStep = steps[steps.length - 1];
  const lastReasoningText =
    lastStep?.kind === "reasoning" ? lastStep.text : "";
  const throttledLastReasoning = useThrottledText(lastReasoningText);

  if (steps.length === 0) {
    return <ThinkingBlock text={fallback} streaming={streaming} />;
  }

  const toolCount = steps.filter((step) => step.kind === "tool").length;
  return (
    <details
      className="message-thinking run-trace"
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        <span>执行过程</span>
        <span className="run-trace-count">
          {steps.length} 步{toolCount > 0 ? ` · ${toolCount} 个工具` : ""}
        </span>
      </summary>
      <div className="run-trace-steps">
        {steps.map((step) => {
          if (step.kind === "reasoning") {
            const text =
              streaming && step === lastStep ? throttledLastReasoning : step.text;
            return (
              <div className="run-trace-reasoning" key={`reasoning-${step.id}`}>
                <div className="run-trace-step-label">思考</div>
                <div className="message-thinking-body">
                  <Markdown content={text} />
                </div>
              </div>
            );
          }
          return (
            <div className="run-trace-tool" key={`tool-${step.id}`}>
              <span className="run-trace-tool-dot" aria-hidden="true" />
              <span className="run-trace-tool-label">
                {step.label || step.tool || "工具调用"}
              </span>
              {step.agent && step.agent !== agentName && (
                <span className="run-trace-tool-agent">{step.agent}</span>
              )}
              {step.tool && <span className="run-trace-tool-name">{step.tool}</span>}
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
        <div key={index} className={`message-skeleton-row ${index % 2 === 1 ? "user" : "assistant"}`}>
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

export function ChatPanel(props: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const olderAnchorRef = useRef<{ height: number; top: number } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const stickRef = useRef(true);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const toolsRef = useRef<HTMLDivElement | null>(null);
  const uploadSeqRef = useRef(0);
  const [expanded, setExpanded] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [attachError, setAttachError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [commandIndex, setCommandIndex] = useState(0);
  const [commandMenuDismissed, setCommandMenuDismissed] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const [highlightId, setHighlightId] = useState("");
  const focusAppliedRef = useRef(0);
  const highlightTimerRef = useRef<number | null>(null);
  const throttledStreamText = useThrottledText(props.streamText);
  const throttledStreamReasoning = useThrottledText(props.streamReasoning);

  const uploading = uploads.length > 0;
  const commandQuery = slashCommandQuery(props.draft);
  const commandMatches = commandQuery === null ? [] : filterSlashCommands(commandQuery);
  const commandMenuOpen = commandMatches.length > 0 && !commandMenuDismissed;
  const activeCommandIndex =
    commandMatches.length > 0 ? Math.min(commandIndex, commandMatches.length - 1) : -1;
  const selectedCommand =
    activeCommandIndex >= 0 ? commandMatches[activeCommandIndex] : null;

  useEffect(() => {
    setCommandIndex(0);
  }, [commandQuery]);

  useEffect(() => {
    if (commandMenuOpen) setMenuOpen(false);
  }, [commandMenuOpen]);

  useEffect(() => {
    if (props.focusSignal > 0) textareaRef.current?.focus();
  }, [props.focusSignal]);

  useEffect(() => {
    return () => {
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!props.focusMessageId) return;
    const element = document.getElementById(`message-${props.focusMessageId}`);
    if (!element) return;
    if (focusAppliedRef.current === props.focusMessageNonce) return;
    focusAppliedRef.current = props.focusMessageNonce;
    stickRef.current = false;
    setAtBottom(false);
    element.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightId(props.focusMessageId);
    if (highlightTimerRef.current !== null) {
      window.clearTimeout(highlightTimerRef.current);
    }
    highlightTimerRef.current = window.setTimeout(() => {
      setHighlightId("");
      highlightTimerRef.current = null;
    }, 2400);
  }, [props.focusMessageId, props.focusMessageNonce, props.messages]);

  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!toolsRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  useEffect(() => {
    const syncHeight = () => {
      const element = textareaRef.current;
      if (!element) return;
      element.style.height = "auto";
      const nextHeight = Math.min(element.scrollHeight, 220);
      element.style.height = `${nextHeight}px`;
      setExpanded(element.scrollHeight > 50);
    };
    syncHeight();
    window.addEventListener("resize", syncHeight);
    return () => window.removeEventListener("resize", syncHeight);
  }, [props.draft]);

  useEffect(() => {
    if (!props.sending) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [props.sending]);

  function scrollToBottom() {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
  }

  function scheduleScrollToBottom() {
    if (scrollFrameRef.current !== null) return;
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      if (stickRef.current) scrollToBottom();
    });
  }

  useEffect(() => {
    if (!stickRef.current) return;
    scheduleScrollToBottom();
  }, [props.messages, throttledStreamText, props.sending, props.commandReply]);

  useLayoutEffect(() => {
    const anchor = olderAnchorRef.current;
    const element = scrollRef.current;
    if (!anchor || !element) return;
    const added = element.scrollHeight - anchor.height;
    if (added > 0) element.scrollTop = anchor.top + added;
    olderAnchorRef.current = null;
  }, [props.messages]);

  async function requestOlder() {
    if (!props.hasMore || props.loadingOlder || olderAnchorRef.current) return;
    const element = scrollRef.current;
    if (!element) return;
    const anchor = { height: element.scrollHeight, top: element.scrollTop };
    olderAnchorRef.current = anchor;
    const loaded = await props.onLoadOlder();
    if (!loaded && olderAnchorRef.current === anchor) olderAnchorRef.current = null;
  }

  useEffect(() => {
    const container = scrollRef.current;
    const content = contentRef.current;
    if (!container || !content) return;
    const observer = new ResizeObserver(() => {
      if (stickRef.current) scheduleScrollToBottom();
    });
    observer.observe(content);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  function handleScroll() {
    const element = scrollRef.current;
    if (!element) return;
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
    stickRef.current = nearBottom;
    setAtBottom((current) => (current === nearBottom ? current : nearBottom));
    const nearTop =
      element.scrollHeight > element.clientHeight + 160 && element.scrollTop < 80;
    if (nearTop && props.hasMore && !props.loadingOlder) {
      void requestOlder();
    }
  }

  function jumpToBottom() {
    stickRef.current = true;
    setAtBottom(true);
    scrollToBottom();
  }

  const canSubmit =
    props.canSend &&
    !props.pendingSend &&
    (props.draft.trim().length > 0 ||
      props.attachments.length > 0 ||
      props.quote.trim().length > 0);

  const screenshotsByRun = new Map<string, ScreenshotInfo[]>();
  for (const shot of props.screenshots) {
    const list = screenshotsByRun.get(shot.run_id);
    if (list) {
      list.push(shot);
    } else {
      screenshotsByRun.set(shot.run_id, [shot]);
    }
  }
  const assistantRunIds = new Set(
    props.messages
      .filter((message) => message.role === "assistant" && message.run_id)
      .map((message) => message.run_id as string),
  );
  const activeRunShown = Boolean(
    props.activeRun && (props.sending || props.streamText || props.terminalRun),
  );
  const orphanScreenshots = props.screenshots.filter(
    (shot) =>
      !assistantRunIds.has(shot.run_id) &&
      (!activeRunShown || shot.run_id !== props.activeRun?.id),
  );

  const queued = props.activeRun?.status === "queued";
  const liveElapsed = elapsedSeconds(
    props.activeRun?.started_at ?? props.activeRun?.created_at,
    now,
  );
  const elapsedLabel =
    props.sending && liveElapsed >= 10 ? `已用 ${formatDuration(liveElapsed)}` : "";
  const activityText = queued
    ? "排队中，等待前面的任务完成"
    : props.trace.some((step) => step.kind === "tool")
      ? ""
      : props.activity;
  const showActivity = props.sending && Boolean(activityText || elapsedLabel);
  const terminalSeconds = props.terminalRun
    ? elapsedSeconds(
        props.terminalRun.run.started_at ?? props.terminalRun.run.created_at,
        props.terminalRun.run.finished_at
          ? Date.parse(props.terminalRun.run.finished_at)
          : undefined,
      )
    : 0;
  const terminalDuration =
    terminalSeconds >= 10 ? `用时 ${formatDuration(terminalSeconds)}` : "";
  const showStreaming = !props.terminalRun && (props.sending || Boolean(props.streamText));
  // Once the persisted assistant message arrives, drop the live bubble even if
  // a state update is still in flight (prevents a duplicate or flicker).
  const streamingSuperseded = Boolean(
    props.activeRun && assistantRunIds.has(props.activeRun.id),
  );

  async function submit() {
    if (!canSubmit) return;
    // Sending always follows the newest message, even if the user had scrolled up.
    stickRef.current = true;
    setAtBottom(true);
    scrollToBottom();
    const sent = await props.onSend(props.attachments);
    if (sent) {
      setAttachError("");
    }
  }

  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setAttachError("");
    for (const original of Array.from(files)) {
      const file = await prepareImageForUpload(original);
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setAttachError(`${file.name} 超过 10 MB，未上传`);
        continue;
      }
      const id = `upload-${(uploadSeqRef.current += 1)}`;
      const previewUrl = file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined;
      setUploads((current) => [
        ...current,
        { id, name: file.name, size: file.size, percent: 0, previewUrl },
      ]);
      try {
        const uploaded = await api.uploadAttachment(props.agentId, file, (percent) => {
          setUploads((current) =>
            current.map((item) => (item.id === id ? { ...item, percent } : item)),
          );
        });
        props.onAddAttachment(uploaded);
      } catch (exc) {
        setAttachError(describeApiError(exc));
      } finally {
        setUploads((current) => current.filter((item) => item.id !== id));
        if (previewUrl) URL.revokeObjectURL(previewUrl);
      }
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = event.clipboardData?.files;
    if (!files || files.length === 0) return;
    event.preventDefault();
    void uploadFiles(files);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (event.dataTransfer.files.length === 0) return;
    event.preventDefault();
    void uploadFiles(event.dataTransfer.files);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (commandMenuOpen) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        setCommandIndex(
          (current) =>
            (current + delta + commandMatches.length) % commandMatches.length,
        );
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setCommandMenuDismissed(true);
        return;
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
        if (event.nativeEvent.isComposing) return;
        const picked = selectedCommand;
        if (picked) {
          const exact = picked.command === props.draft.trim().toLowerCase();
          if (event.key === "Tab" || !exact) {
            event.preventDefault();
            applyCommand(picked.command);
            return;
          }
          // Enter on the exact command sends it instead of only filling it.
          setCommandMenuDismissed(true);
        }
      }
    }
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    void submit();
  }

  function applyCommand(command: string) {
    props.onDraftChange(command);
    setCommandMenuDismissed(true);
    textareaRef.current?.focus();
  }

  function useSuggestion(text: string) {
    props.onDraftChange(text);
    textareaRef.current?.focus();
  }

  return (
    <section className="chat-panel">
      <div className="message-scroll" ref={scrollRef} onScroll={handleScroll}>
        <div className="message-flow" ref={contentRef}>
        {props.hasMore && (
          <div className="message-history" role="status">
            {props.loadOlderError ? (
              <button
                type="button"
                className="message-history-retry"
                onClick={() => void requestOlder()}
              >
                加载更早消息失败，点击重试
              </button>
            ) : props.loadingOlder ? (
              <span>正在加载更早的消息…</span>
            ) : (
              <span>上滑加载更早的消息</span>
            )}
          </div>
        )}
        {props.loading && props.messages.length === 0 && <MessageSkeleton />}

        {!props.loading && props.messages.length === 0 && !props.sending && !props.commandReply && (
          <div className="chat-empty">
            <div className="chat-empty-mark">ACC</div>
            <h2>和 {props.agentName || "Agent"} 开始对话</h2>
            <p>支持 Markdown、代码块和消息复制；流式输出会实时显示。</p>
            <div className="suggestion-list">
              {SUGGESTIONS.map((suggestion) => (
                <button key={suggestion} type="button" onClick={() => useSuggestion(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {props.messages.map((message) => {
          if (message.role === "user") {
            const parsed = parseUserContent(message.content);
            const copyContent = userMessageText(message.content);
            return (
              <div
                key={message.id}
                id={`message-${message.id}`}
                className={`message-row user ${highlightId === message.id ? "message-focus" : ""}`}
              >
                <div className="message-bubble user">
                  {parsed.kind === "attachments" ? (
                    <>
                      {parsed.files.some((file) => isImageName(file.name)) && (
                        <div className="message-images">
                          {parsed.files
                            .filter((file) => isImageName(file.name))
                            .map((file, index) => {
                              const url = api.uploadUrl(props.agentId, file.path);
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
                      {parsed.text && <Markdown content={parsed.text} />}
                    </>
                  ) : parsed.kind === "memory-command" ? (
                    <>
                      <div className="message-command">
                        <span className="message-command-mark" aria-hidden="true">
                          ✦
                        </span>
                        <span className="message-command-text">
                          <strong>整理记忆</strong>
                          <small>合并重复与失效的长期记忆</small>
                        </span>
                      </div>
                      <details className="message-command-raw">
                        <summary>查看指令</summary>
                        <pre>{message.content}</pre>
                      </details>
                    </>
                  ) : (
                    <Markdown content={message.content} />
                  )}
                </div>
                <div className="message-footer user">
                  <span className="message-footer-time">
                    你 · {formatTime(message.created_at)}
                  </span>
                  <div className="message-actions">
                    <CopyButton text={copyContent} />
                    <button
                      type="button"
                      className="message-action"
                      onClick={() => {
                        props.onQuote(copyContent);
                        textareaRef.current?.focus();
                      }}
                    >
                      引用
                    </button>
                  </div>
                </div>
              </div>
            );
          }
          return (
            <div
              key={message.id}
              id={`message-${message.id}`}
              className={`message-row assistant ${highlightId === message.id ? "message-focus" : ""}`}
            >
              <div className="message-bubble assistant">
                <div className="message-head">
                  <span className="message-author">{props.agentName || "Agent"}</span>
                  <span className="message-time">{formatTime(message.created_at)}</span>
                </div>
                <RunTrace
                  steps={message.run_id ? props.runTraces[message.run_id] ?? [] : []}
                  fallback={
                    message.run_id ? props.runReasoning[message.run_id] ?? "" : ""
                  }
                  streaming={false}
                  agentName={props.agentName}
                />
                <Markdown content={message.content} />
                <ScreenshotStrip
                  sessionId={props.sessionId}
                  items={message.run_id ? screenshotsByRun.get(message.run_id) ?? [] : []}
                />
              </div>
              <div className="message-footer assistant">
                <div className="message-actions">
                  <CopyButton text={message.content} />
                  <button
                    type="button"
                    className="message-action"
                    onClick={() => {
                      props.onQuote(message.content);
                      textareaRef.current?.focus();
                    }}
                  >
                    引用
                  </button>
                </div>
              </div>
            </div>
          );
        })}

        {showStreaming && !streamingSuperseded && (
          <div className="message-row assistant">
            <div className="message-bubble assistant streaming">
              <div className="message-head">
                <span className="message-author">{props.agentName || "Agent"}</span>
              </div>
              {showActivity && (
                <div className="run-activity" role="status">
                  {!queued && <span className="run-activity-dot" aria-hidden="true" />}
                  {activityText && <span className="run-activity-text">{activityText}</span>}
                  {activityText && elapsedLabel && (
                    <span className="run-activity-sep" aria-hidden="true">
                      ·
                    </span>
                  )}
                  {elapsedLabel && <span className="run-activity-elapsed">{elapsedLabel}</span>}
                </div>
              )}
              <RunTrace
                steps={props.trace}
                fallback={throttledStreamReasoning}
                streaming={!props.streamText}
                agentName={props.agentName}
              />
              {props.streamText ? (
                <Markdown content={throttledStreamText} streaming />
              ) : (
                <div className="muted">{queued ? "排队中…" : "正在生成…"}</div>
              )}
              {!queued && <span className="cursor" />}
              {props.activeRun && (
                <ScreenshotStrip
                  sessionId={props.sessionId}
                  items={screenshotsByRun.get(props.activeRun.id) ?? []}
                />
              )}
            </div>
          </div>
        )}

        {props.terminalRun && (
          <div className="message-row assistant">
            <div
              className={`message-bubble assistant terminal ${
                props.terminalRun.run.status === "cancelled" ? "cancelled" : "failed"
              }`}
            >
              <div className="message-head">
                <span className="message-author">{props.agentName || "Agent"}</span>
                <span
                  className={`run-status-pill ${
                    props.terminalRun.run.status === "cancelled" ? "cancelled" : "failed"
                  }`}
                >
                  {props.terminalRun.run.status === "cancelled" ? "已停止" : "失败"}
                </span>
                {terminalDuration && <span className="message-time">{terminalDuration}</span>}
              </div>
              <RunTrace
                steps={props.trace}
                fallback={props.terminalRun.reasoning}
                streaming={false}
                agentName={props.agentName}
              />
              {props.terminalRun.text ? (
                <Markdown content={props.terminalRun.text} />
              ) : (
                <div className="muted">这个 Run 没有产生回复。</div>
              )}
              <div className="run-terminal-footer">
                <span
                  className="run-terminal-reason"
                  title={props.terminalRun.run.error ?? undefined}
                >
                  {props.terminalRun.run.status === "cancelled"
                    ? "已停止生成，上面的内容保留到停止时。"
                    : describeRunError(props.terminalRun.run.error)}
                </span>
                <button
                  type="button"
                  className="ghost-button"
                  disabled={props.sending}
                  onClick={() => props.onRetry(props.terminalRun!.run.id)}
                >
                  重试
                </button>
              </div>
              <ScreenshotStrip
                sessionId={props.sessionId}
                items={screenshotsByRun.get(props.terminalRun.run.id) ?? []}
              />
            </div>
          </div>
        )}

        {orphanScreenshots.length > 0 && (
          <div className="message-row assistant">
            <div className="message-bubble assistant">
              <ScreenshotStrip sessionId={props.sessionId} items={orphanScreenshots} />
            </div>
          </div>
        )}

        {props.commandReply && (
          <div className="message-row system">
            <div className="message-bubble system">
              <div className="message-command-reply-head">
                <span className="message-command-tag">
                  {commandReplyLabel(props.commandReply.command)}
                </span>
                <span className="message-command-code">{props.commandReply.command}</span>
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
        </div>
      </div>

      <div className="composer">
        {!atBottom && (props.sending || props.streamText) && (
          <button
            type="button"
            className="scroll-bottom-button"
            onClick={jumpToBottom}
            aria-label="回到最新消息"
            title="回到最新消息"
          >
            <span aria-hidden="true">↓</span>
            <span>回到最新</span>
          </button>
        )}
        {props.notice && (
          <div className="composer-notice" role="status">
            {props.notice}
          </div>
        )}
        {props.channelType === "wechat" && (
          <div className="composer-channel-hint" role="note" title={props.channelName}>
            微信会话 · 网页里发送的消息不会发回微信
          </div>
        )}
        {props.quote && (
          <div className="composer-quote" role="note" aria-label="引用内容">
            <span className="composer-quote-label" aria-hidden="true">
              引用
            </span>
            <div className="composer-quote-text">{props.quote}</div>
            <button
              type="button"
              className="composer-quote-remove"
              aria-label="取消引用"
              title="取消引用"
              onClick={props.onClearQuote}
            >
              ×
            </button>
          </div>
        )}
        <div
          className={`composer-card ${expanded ? "expanded" : ""} ${
            props.attachments.length > 0 || uploading || attachError ? "has-files" : ""
          }`}
          onDrop={handleDrop}
          onDragOver={(event) => event.preventDefault()}
        >
          {commandMenuOpen && (
            <div
              className="composer-commands"
              id="composer-command-list"
              role="listbox"
              aria-label="可用命令"
            >
              <div className="composer-commands-head">输入 / 选择命令</div>
              {commandMatches.map((item, index) => (
                <button
                  key={item.command}
                  type="button"
                  role="option"
                  aria-selected={index === activeCommandIndex}
                  className={`composer-command ${index === activeCommandIndex ? "active" : ""}`}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseMove={() => setCommandIndex(index)}
                  onClick={() => applyCommand(item.command)}
                >
                  <span className="composer-command-name">{item.command}</span>
                  <span className="composer-command-label">{item.label}</span>
                  <span className="composer-command-desc">{item.description}</span>
                </button>
              ))}
              <div className="composer-commands-hint">
                ↑↓ 选择 · Enter/Tab 填入 · Esc 关闭
              </div>
            </div>
          )}
          <div className="composer-tools" ref={toolsRef}>
            <button
              type="button"
              className={`composer-plus ${menuOpen ? "active" : ""}`}
              aria-label="添加附件"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              title="添加附件"
              onClick={() => {
                setCommandMenuDismissed(true);
                setMenuOpen((current) => !current);
              }}
            >
              <svg
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M12 5v14M5 12h14" />
              </svg>
            </button>
            {menuOpen && (
              <div className="composer-menu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    fileInputRef.current?.click();
                  }}
                >
                  <span className="composer-menu-icon" aria-hidden="true">
                    <svg
                      viewBox="0 0 24 24"
                      width="15"
                      height="15"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M21 11.5 12.5 20a5.5 5.5 0 0 1-7.8-7.8l8.5-8.5a3.7 3.7 0 0 1 5.2 5.2l-8.5 8.5a1.8 1.8 0 0 1-2.6-2.6l7.8-7.8" />
                    </svg>
                  </span>
                  <span className="composer-menu-text">
                    <strong>上传附件</strong>
                    <small>保存到当前 Agent 工作区</small>
                  </span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  disabled={!props.canSend || props.sending}
                  onClick={() => {
                    setMenuOpen(false);
                    void props.onOrganizeMemory();
                  }}
                >
                  <span className="composer-menu-icon" aria-hidden="true">
                    <svg
                      viewBox="0 0 24 24"
                      width="15"
                      height="15"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="m12 3 1.9 4.6 4.6 1.9-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3Z" />
                      <path d="m18 14 .9 2.1 2.1.9-2.1.9L18 20l-.9-2.1-2.1-.9 2.1-.9L18 14Z" />
                    </svg>
                  </span>
                  <span className="composer-menu-text">
                    <strong>整理记忆</strong>
                    <small>
                      {props.memorySkillEnabled
                        ? "合并重复与失效的长期记忆"
                        : "首次使用会自动启用 memory-organizer 技能"}
                    </small>
                  </span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  disabled={!props.draft}
                  onClick={() => {
                    setMenuOpen(false);
                    props.onDraftChange("");
                  }}
                >
                  <span className="composer-menu-icon" aria-hidden="true">
                    <svg
                      viewBox="0 0 24 24"
                      width="15"
                      height="15"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                    >
                      <path d="M6 6l12 12M18 6 6 18" />
                    </svg>
                  </span>
                  <span className="composer-menu-text">
                    <strong>清空输入</strong>
                    <small>保留已上传的文件</small>
                  </span>
                </button>
              </div>
            )}
          </div>
          {props.attachments.length > 0 || uploading || attachError ? (
            <div className="composer-files">
              {uploads.map((item) => (
                <span className="composer-file uploading" key={item.id}>
                  {item.previewUrl && (
                    <img className="composer-file-thumb" src={item.previewUrl} alt="" />
                  )}
                  <span className="composer-file-name" title={item.name}>
                    {item.name}
                  </span>
                  <span className="composer-file-size">{formatBytes(item.size)}</span>
                  <span className="composer-file-progress">{item.percent}%</span>
                </span>
              ))}
              {props.attachments.map((file) => (
                <span className="composer-file" key={file.path}>
                  {isImageName(file.name) && (
                    <img
                      className="composer-file-thumb"
                      src={api.uploadUrl(props.agentId, file.path)}
                      alt=""
                    />
                  )}
                  <span className="composer-file-name" title={`${file.name} · ${file.path}`}>
                    {file.name}
                  </span>
                  <span className="composer-file-size">{formatBytes(file.size)}</span>
                  <button
                    type="button"
                    className="composer-file-remove"
                    aria-label={`移除 ${file.name}`}
                    title="移除附件"
                    onClick={() => {
                      setAttachError("");
                      props.onRemoveAttachment(file.path);
                    }}
                  >
                    ×
                  </button>
                </span>
              ))}
              {attachError && <span className="composer-upload-error">{attachError}</span>}
            </div>
          ) : null}
          <textarea
            ref={textareaRef}
            value={props.draft}
            onChange={(event) => {
              props.onDraftChange(event.target.value);
              setCommandMenuDismissed(false);
              setCommandIndex(0);
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onFocus={() => setCommandMenuDismissed(false)}
            onBlur={() => setCommandMenuDismissed(true)}
            aria-autocomplete="list"
            aria-expanded={commandMenuOpen}
            aria-controls={commandMenuOpen ? "composer-command-list" : undefined}
            placeholder={
              !props.canSend
                ? "先选择一个 Agent 和会话"
                : props.runActive
                  ? "继续追问，发送后会排队…"
                  : "给 Agent 发消息…"
            }
            rows={1}
            disabled={!props.canSend}
          />
          <div className="composer-actions">
            {props.runActive && (
              <button
                className="composer-send-button stop"
                type="button"
                onClick={props.onCancel}
                aria-label="停止生成"
                title="停止生成"
              >
                <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              </button>
            )}
            <button
              className="composer-send-button"
              type="button"
              onClick={() => void submit()}
              disabled={!canSubmit}
              aria-label={props.runActive ? "排队追问" : "发送"}
              title={props.runActive ? "排队追问" : "发送"}
            >
              <svg
                viewBox="0 0 24 24"
                width="15"
                height="15"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M22 2 11 13" />
                <path d="M22 2 15 22l-4-9-9-4Z" />
              </svg>
            </button>
          </div>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            void uploadFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </div>
    </section>
  );
}
