import {
  ClipboardEvent,
  DragEvent,
  KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import { api } from "../api";
import type { Attachment, TranscriptMessage } from "../types";
import { truncate } from "../format";
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
import { describeApiError } from "../errors";
import { useThrottledText } from "../useThrottledText";
import { Markdown } from "./Markdown";

type ChatPanelProps = {
  messages: TranscriptMessage[];
  queuedMessages: TranscriptMessage[];
  loading: boolean;
  loadError: string;
  onReload: () => void;
  streamText: string;
  streamReasoning: string;
  activity: string[];
  sending: boolean;
  tracking: boolean;
  pendingSend: boolean;
  error: string;
  notice: string;
  commandReply: { command: string; text: string } | null;
  draft: string;
  quote: string;
  attachments: Attachment[];
  canSend: boolean;
  agentId: string;
  agentName: string;
  channelType: string;
  channelName: string;
  focusSignal: number;
  focusMessageId: string;
  focusMessageNonce: number;
  onDraftChange: (value: string) => void;
  onClearQuote: () => void;
  onAddAttachment: (file: Attachment) => void;
  onRemoveAttachment: (path: string) => void;
  onSend: (attachments: Attachment[]) => Promise<boolean>;
  onCancel: () => void;
  onQuote: (text: string) => void;
};

type UploadItem = {
  id: string;
  name: string;
  size: number;
  percent: number;
  previewUrl?: string;
};

type ProcessStep =
  | { kind: "thinking"; text: string }
  | { kind: "tool"; name: string; input: Record<string, unknown> | null };

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

function AssistantProcess({ steps }: { steps: ProcessStep[] }) {
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
                <Markdown content={step.text} />
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

function LiveProcess({
  reasoning,
  activity,
  streamingText,
}: {
  reasoning: string;
  activity: string[];
  streamingText: boolean;
}) {
  const [open, setOpen] = useState(true);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);
  useEffect(() => {
    // Collapse once the answer starts, like the previous run trace.
    if (streamingText) setOpen(false);
  }, [streamingText]);
  useEffect(() => {
    const body = bodyRef.current;
    if (!body || !open || !followRef.current) return;
    body.scrollTop = body.scrollHeight;
  }, [reasoning, activity.length, open]);
  const hasReasoning = Boolean(reasoning.trim());
  if (!hasReasoning && activity.length === 0) return null;
  return (
    <details
      className="message-thinking run-trace"
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        <span>执行过程</span>
        <span className="run-trace-count">
          {activity.length > 0 ? `${activity.length} 个工具` : "思考"}
        </span>
      </summary>
      <div className="run-trace-steps">
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
              <Markdown content={reasoning} />
            </div>
          </div>
        )}
        {activity.map((name, index) => (
          <div className="run-trace-tool" key={`${name}-${index}`}>
            <span className="run-trace-tool-dot" aria-hidden="true" />
            <span className="run-trace-tool-label">{name}</span>
          </div>
        ))}
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

function UserMessageRow({
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
  const parsed = parseUserContent(message.content);
  const copyContent = userMessageText(message.content);
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
                        <span className="message-file-size">{formatBytes(file.size)}</span>
                      )}
                    </span>
                  ))}
              </div>
            )}
            {parsed.text && <Markdown content={parsed.text} />}
          </>
        ) : (
          <Markdown content={message.content} />
        )}
      </div>
      <div className="message-footer user">
        <div className="message-actions">
          <CopyButton text={copyContent} />
          <button type="button" className="message-action" onClick={() => onQuote(copyContent)}>
            引用
          </button>
        </div>
      </div>
    </div>
  );
}

export function ChatPanel(props: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
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

  const showStreaming = props.tracking || Boolean(props.streamText);
  const latestActivity = props.activity[props.activity.length - 1] ?? "";

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

  function quoteFromMessage(text: string) {
    props.onQuote(text);
    textareaRef.current?.focus();
  }

  return (
    <section className="chat-panel">
      <div className="message-scroll" ref={scrollRef} onScroll={handleScroll}>
        <div className="message-flow" ref={contentRef}>
        {props.loadError && (
          <div className="error-banner in-chat" role="alert">
            <span>{props.loadError}</span>
            <button type="button" className="ghost-button" onClick={props.onReload}>
              重新加载
            </button>
          </div>
        )}

        {props.loading && props.messages.length === 0 && <MessageSkeleton />}

        {!props.loading && props.messages.length === 0 && !props.sending && !props.commandReply && !props.loadError && (
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
            return (
              <UserMessageRow
                key={message.id}
                message={message}
                agentId={props.agentId}
                highlighted={highlightId === message.id}
                onQuote={quoteFromMessage}
              />
            );
          }
          const steps = processSteps(message.blocks);
          return (
            <div
              key={message.id}
              id={`message-${message.id}`}
              className={`message-row assistant ${highlightId === message.id ? "message-focus" : ""}`}
            >
              <div className="message-bubble assistant">
                <div className="message-head">
                  <span className="message-author">{props.agentName || "Agent"}</span>
                </div>
                <AssistantProcess steps={steps} />
                {message.content && <Markdown content={message.content} />}
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

        {showStreaming && (
          <div className="message-row assistant">
            <div className="message-bubble assistant streaming">
              <div className="message-head">
                <span className="message-author">{props.agentName || "Agent"}</span>
              </div>
              {latestActivity && (
                <div className="run-activity" role="status">
                  <span className="run-activity-dot" aria-hidden="true" />
                  <span className="run-activity-text">{latestActivity}</span>
                </div>
              )}
              <LiveProcess
                reasoning={throttledStreamReasoning}
                activity={props.activity}
                streamingText={Boolean(props.streamText)}
              />
              {props.streamText ? (
                <Markdown content={throttledStreamText} streaming />
              ) : (
                <div className="muted">正在生成…</div>
              )}
              <span className="cursor" />
            </div>
          </div>
        )}

        {props.queuedMessages.map((message) => (
          <UserMessageRow
            key={message.id}
            message={message}
            agentId={props.agentId}
            highlighted={highlightId === message.id}
            onQuote={quoteFromMessage}
          />
        ))}

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
                ? props.loadError
                  ? "会话加载失败，点击重试"
                  : props.loading
                    ? "会话加载中，请稍候"
                    : "先选择一个 Agent 和会话"
                : props.tracking
                  ? "继续追问，发送后会排队…"
                  : "给 Agent 发消息…"
            }
            rows={1}
            disabled={!props.canSend}
          />
          <div className="composer-actions">
            {props.tracking && (
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
              aria-label={props.tracking ? "排队追问" : "发送"}
              title={props.tracking ? "排队追问" : "发送"}
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
