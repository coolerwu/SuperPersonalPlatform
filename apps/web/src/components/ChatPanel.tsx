import { KeyboardEvent, useEffect, useRef, useState } from "react";

import { api, errorText } from "../api";
import type { Attachment, Message, Run, ScreenshotInfo } from "../types";
import { formatTime } from "../format";
import { copyText } from "../clipboard";
import { Markdown } from "./Markdown";

type ChatPanelProps = {
  messages: Message[];
  screenshots: ScreenshotInfo[];
  sessionId: string;
  streamText: string;
  streamReasoning: string;
  runReasoning: Record<string, string>;
  sending: boolean;
  error: string;
  activeRun: Run | null;
  draft: string;
  canSend: boolean;
  sendBlocked: boolean;
  agentId: string;
  agentName: string;
  focusSignal: number;
  onDraftChange: (value: string) => void;
  onSend: (attachments: Attachment[]) => Promise<boolean>;
  onCancel: () => void;
  onQuote: (text: string) => void;
  onRetry: (runId: string) => void;
};

const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

const SUGGESTIONS = [
  "帮我写一个 Python 脚本",
  "总结这段内容的要点",
  "解释一个技术概念",
];

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

export function ChatPanel(props: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const stickRef = useRef(true);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const toolsRef = useRef<HTMLDivElement | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [attachError, setAttachError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);

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
    stickRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
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
  const activeRunShown = Boolean(props.sending || props.streamText) && props.activeRun;
  const orphanScreenshots = props.screenshots.filter(
    (shot) =>
      !assistantRunIds.has(shot.run_id) &&
      (!activeRunShown || shot.run_id !== props.activeRun?.id),
  );

  async function submit() {
    if (!canSubmit) return;
    const sent = await props.onSend(attachments);
    if (sent) {
      setAttachments([]);
      setAttachError("");
    }
  }

  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setAttachError("");
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        if (file.size > MAX_ATTACHMENT_BYTES) {
          setAttachError(`${file.name} 超过 10 MB，未上传`);
          continue;
        }
        try {
          const uploaded = await api.uploadAttachment(props.agentId, file);
          setAttachments((current) => [...current, uploaded]);
        } catch (exc) {
          setAttachError(errorText(exc));
        }
      }
    } finally {
      setUploading(false);
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
        {props.messages.length === 0 && !props.sending && (
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

        {props.messages.map((message) =>
          message.role === "user" ? (
            <div key={message.id} className="message-row user">
              <div className="message-bubble user">
                <Markdown content={message.content} />
                <div className="message-meta">
                  <span>你 · {formatTime(message.created_at)}</span>
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
              </div>
            </div>
          ) : (
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
          ),
        )}

        {(props.sending || props.streamText) && (
          <div className="message-row assistant">
            <div className="message-avatar">ACC</div>
            <div className="message-bubble assistant streaming">
              <div className="message-head">
                <span className="message-author">{props.agentName || "Agent"}</span>
                <span className="streaming-label">{props.activeRun?.status ?? "running"}</span>
              </div>
              <ThinkingBlock
                text={props.streamReasoning}
                streaming={!props.streamText}
              />
              {props.streamText ? (
                <Markdown content={props.streamText} />
              ) : (
                <div className="muted">正在生成…</div>
              )}
              <span className="cursor" />
              {props.activeRun && (
                <ScreenshotStrip
                  sessionId={props.sessionId}
                  items={screenshotsByRun.get(props.activeRun.id) ?? []}
                />
              )}
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
            {props.activeRun?.status === "failed" && (
              <button
                type="button"
                className="ghost-button"
                onClick={() => props.onRetry(props.activeRun!.id)}
              >
                重试这个 Run
              </button>
            )}
          </div>
        )}
        </div>
      </div>

      <div className="composer">
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
              {attachments.map((file) => (
                <span className="composer-file" key={file.path}>
                  <span className="composer-file-name" title={`${file.name} · ${file.path}`}>
                    {file.name}
                  </span>
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
              {uploading && <span className="composer-upload-note">上传中…</span>}
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
