import { KeyboardEvent, useEffect, useRef, useState } from "react";

import { api, errorText } from "../api";
import type { Attachment, Message, Run, ScreenshotInfo } from "../types";
import { formatTime } from "../format";
import { copyText } from "../clipboard";
import { formatBytes, parseUserContent, userMessageText } from "../messageContent";
import { formatDuration } from "../runActivity";
import { Markdown } from "./Markdown";

type TerminalRun = {
  run: Run;
  text: string;
  reasoning: string;
};

type ChatPanelProps = {
  messages: Message[];
  loading: boolean;
  screenshots: ScreenshotInfo[];
  sessionId: string;
  streamText: string;
  streamReasoning: string;
  runReasoning: Record<string, string>;
  sending: boolean;
  activity: string;
  terminalRun: TerminalRun | null;
  error: string;
  notice: string;
  activeRun: Run | null;
  draft: string;
  canSend: boolean;
  sendBlocked: boolean;
  agentId: string;
  agentName: string;
  memorySkillEnabled: boolean;
  focusSignal: number;
  onDraftChange: (value: string) => void;
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
};

const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

const SUGGESTIONS = [
  "帮我写一个 Python 脚本",
  "总结这段内容的要点",
  "解释一个技术概念",
];

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
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const stickRef = useRef(true);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const toolsRef = useRef<HTMLDivElement | null>(null);
  const uploadSeqRef = useRef(0);
  const [expanded, setExpanded] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [attachError, setAttachError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [now, setNow] = useState(() => Date.now());

  const uploading = uploads.length > 0;

  useEffect(() => {
    if (props.focusSignal > 0) textareaRef.current?.focus();
  }, [props.focusSignal]);

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

  useEffect(() => {
    if (!stickRef.current) return;
    scrollToBottom();
    const frame = window.requestAnimationFrame(scrollToBottom);
    return () => window.cancelAnimationFrame(frame);
  }, [props.messages, props.streamText, props.sending]);

  useEffect(() => {
    const container = scrollRef.current;
    const content = contentRef.current;
    if (!container || !content) return;
    const observer = new ResizeObserver(() => {
      if (stickRef.current) scrollToBottom();
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
    !props.sending &&
    !props.sendBlocked &&
    (props.draft.trim().length > 0 || attachments.length > 0);

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
  const activityText = queued ? "排队中，等待其他会话完成" : props.activity;
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
    const sent = await props.onSend(attachments);
    if (sent) {
      setAttachments([]);
      setAttachError("");
    }
  }

  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setAttachError("");
    for (const file of Array.from(files)) {
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setAttachError(`${file.name} 超过 10 MB，未上传`);
        continue;
      }
      const id = `upload-${(uploadSeqRef.current += 1)}`;
      setUploads((current) => [...current, { id, name: file.name, size: file.size, percent: 0 }]);
      try {
        const uploaded = await api.uploadAttachment(props.agentId, file, (percent) => {
          setUploads((current) =>
            current.map((item) => (item.id === id ? { ...item, percent } : item)),
          );
        });
        setAttachments((current) => [...current, uploaded]);
      } catch (exc) {
        setAttachError(errorText(exc));
      } finally {
        setUploads((current) => current.filter((item) => item.id !== id));
      }
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    void submit();
  }

  function useSuggestion(text: string) {
    props.onDraftChange(text);
    textareaRef.current?.focus();
  }

  return (
    <section className="chat-panel">
      <div className="message-scroll" ref={scrollRef} onScroll={handleScroll}>
        <div className="message-flow" ref={contentRef}>
        {props.loading && props.messages.length === 0 && <MessageSkeleton />}

        {!props.loading && props.messages.length === 0 && !props.sending && (
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
              <div key={message.id} className="message-row user">
                <div className="message-bubble user">
                  {parsed.kind === "attachments" ? (
                    <>
                      <div className="message-files">
                        {parsed.files.map((file, index) => (
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
                  <div className="message-meta">
                    <span>你 · {formatTime(message.created_at)}</span>
                    <div className="message-actions">
                      <CopyButton text={copyContent} />
                      <button
                        type="button"
                        className="message-action"
                        onClick={() => props.onQuote(copyContent)}
                      >
                        引用
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            );
          }
          return (
            <div key={message.id} className="message-row assistant">
              <div className="message-avatar">ACC</div>
              <div className="message-bubble assistant">
                <div className="message-head">
                  <span className="message-author">{props.agentName || "Agent"}</span>
                  <span className="message-time">{formatTime(message.created_at)}</span>
                  <div className="message-actions">
                    <CopyButton text={message.content} />
                    <button
                      type="button"
                      className="message-action"
                      onClick={() => props.onQuote(message.content)}
                    >
                      引用
                    </button>
                  </div>
                </div>
                <ThinkingBlock
                  text={message.run_id ? props.runReasoning[message.run_id] ?? "" : ""}
                  streaming={false}
                />
                <Markdown content={message.content} />
                <ScreenshotStrip
                  sessionId={props.sessionId}
                  items={message.run_id ? screenshotsByRun.get(message.run_id) ?? [] : []}
                />
              </div>
            </div>
          );
        })}

        {showStreaming && !streamingSuperseded && (
          <div className="message-row assistant">
            <div className="message-avatar">ACC</div>
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
              <ThinkingBlock
                text={props.streamReasoning}
                streaming={!props.streamText}
              />
              {props.streamText ? (
                <Markdown content={props.streamText} />
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
            <div className="message-avatar">ACC</div>
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
              <ThinkingBlock text={props.terminalRun.reasoning} streaming={false} />
              {props.terminalRun.text ? (
                <Markdown content={props.terminalRun.text} />
              ) : (
                <div className="muted">这个 Run 没有产生回复。</div>
              )}
              <div className="run-terminal-footer">
                <span className="run-terminal-reason">
                  {props.terminalRun.run.status === "cancelled"
                    ? "已停止生成，上面的内容保留到停止时。"
                    : props.terminalRun.run.error || "Run 执行失败。"}
                </span>
                <button
                  type="button"
                  className="ghost-button"
                  disabled={props.sendBlocked}
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
            <div className="message-avatar">ACC</div>
            <div className="message-bubble assistant">
              <ScreenshotStrip sessionId={props.sessionId} items={orphanScreenshots} />
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
        {props.sendBlocked && (
          <div className="composer-busy-hint" role="status">
            另一个会话的 Run 正在运行，完成后再发送。
          </div>
        )}
        <div
          className={`composer-card ${expanded ? "expanded" : ""} ${
            attachments.length > 0 || uploading || attachError ? "has-files" : ""
          }`}
        >
          <div className="composer-tools" ref={toolsRef}>
            <button
              type="button"
              className={`composer-plus ${menuOpen ? "active" : ""}`}
              aria-label="添加附件"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              title="添加附件"
              onClick={() => setMenuOpen((current) => !current)}
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
                  disabled={!props.canSend || props.sending || props.sendBlocked}
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
          {attachments.length > 0 || uploading || attachError ? (
            <div className="composer-files">
              {uploads.map((item) => (
                <span className="composer-file uploading" key={item.id}>
                  <span className="composer-file-name" title={item.name}>
                    {item.name}
                  </span>
                  <span className="composer-file-size">{formatBytes(item.size)}</span>
                  <span className="composer-file-progress">{item.percent}%</span>
                </span>
              ))}
              {attachments.map((file) => (
                <span className="composer-file" key={file.path}>
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
                      setAttachments((current) =>
                        current.filter((item) => item.path !== file.path),
                      );
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
            onChange={(event) => props.onDraftChange(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={props.canSend ? "给 Agent 发消息…" : "先选择一个 Agent 和会话"}
            rows={1}
            disabled={!props.canSend}
          />
          <div className="composer-actions">
            {props.sending ? (
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
                <span>停止</span>
              </button>
            ) : (
              <button
                className="composer-send-button"
                type="button"
                onClick={() => void submit()}
                disabled={!canSubmit}
                aria-label={props.sendBlocked ? "另一个会话的 Run 正在运行" : "发送"}
                title={props.sendBlocked ? "另一个会话的 Run 正在运行，完成后再发送" : "发送"}
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
                <span>发送</span>
              </button>
            )}
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
