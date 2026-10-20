import { KeyboardEvent, useEffect, useRef, useState } from "react";

import type { Message, Run } from "../types";
import { formatTime } from "../format";
import { copyText } from "../clipboard";
import { Markdown } from "./Markdown";

type ChatPanelProps = {
  messages: Message[];
  streamText: string;
  sending: boolean;
  error: string;
  activeRun: Run | null;
  draft: string;
  canSend: boolean;
  agentName: string;
  focusSignal: number;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  onQuote: (text: string) => void;
  onRetry: (runId: string) => void;
};

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

export function ChatPanel(props: ChatPanelProps) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const stickRef = useRef(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (props.focusSignal > 0) textareaRef.current?.focus();
  }, [props.focusSignal]);

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
    if (stickRef.current) {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [props.messages, props.streamText, props.sending]);

  function handleScroll() {
    const element = scrollRef.current;
    if (!element) return;
    stickRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    props.onSend();
  }

  function useSuggestion(text: string) {
    props.onDraftChange(text);
    textareaRef.current?.focus();
  }

  return (
    <section className="chat-panel">
      <div className="message-scroll" ref={scrollRef} onScroll={handleScroll}>
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
                <Markdown content={message.content} />
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
              {props.streamText ? (
                <Markdown content={props.streamText} />
              ) : (
                <div className="muted">正在生成…</div>
              )}
              <span className="cursor" />
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
        <div ref={endRef} />
      </div>

      <div className="composer">
        <div className={`composer-card ${expanded ? "expanded" : ""}`}>
          <div className="composer-hint" title={props.activeRun ? props.activeRun.status : undefined}>
            {props.activeRun && <span className={`status-dot ${props.activeRun.status}`} />}
            <span className="composer-shortcut">
              <span className="composer-shortcut-mini">Enter 发送</span>
              <span className="composer-shortcut-full">Enter 发送 · Shift+Enter 换行 · Markdown</span>
            </span>
          </div>
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
                onClick={props.onSend}
                disabled={!props.canSend || !props.draft.trim()}
                aria-label="发送"
                title="发送"
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
      </div>
    </section>
  );
}
