import { KeyboardEvent, useEffect, useRef } from "react";

import type { Message, Run } from "../types";
import { formatTime } from "../format";

type ChatPanelProps = {
  messages: Message[];
  streamText: string;
  sending: boolean;
  error: string;
  activeRun: Run | null;
  draft: string;
  canSend: boolean;
  agentName: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
};

export function ChatPanel(props: ChatPanelProps) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [props.messages, props.streamText, props.sending]);

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    props.onSend();
  }

  return (
    <section className="chat-panel">
      <div className="message-scroll">
        {props.messages.length === 0 && !props.sending && (
          <div className="chat-empty">
            <div className="chat-empty-mark">ACC</div>
            <h2>和 {props.agentName || "Agent"} 开始对话</h2>
            <p>每条消息都会创建一个 Run；工具调用、子 Agent 和用量会出现在右侧抽屉。</p>
          </div>
        )}

        {props.messages.map((message) => (
          <div key={message.id} className={`message-row ${message.role}`}>
            <div className={`message-bubble ${message.role}`}>
              <div className="message-content">{message.content}</div>
              <div className="message-meta">
                {message.role === "user" ? "你" : props.agentName || "Agent"} · {formatTime(message.created_at)}
              </div>
            </div>
          </div>
        ))}

        {(props.sending || props.streamText) && (
          <div className="message-row assistant">
            <div className="message-bubble assistant streaming">
              <div className="message-content">
                {props.streamText || <span className="muted">正在生成…</span>}
                <span className="cursor" />
              </div>
              <div className="message-meta">
                {props.activeRun?.status ?? "running"} · 流式输出
              </div>
            </div>
          </div>
        )}

        {props.error && <div className="error-banner in-chat">{props.error}</div>}
        <div ref={endRef} />
      </div>

      <div className="composer">
        <textarea
          value={props.draft}
          onChange={(event) => props.onDraftChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={props.canSend ? "发消息…  Enter 发送，Shift+Enter 换行" : "先选择一个 Agent 和会话"}
          rows={2}
          disabled={!props.canSend}
        />
        <div className="composer-bar">
          <div className="composer-hint">
            {props.activeRun && (
              <span className={`status-dot ${props.activeRun.status}`} />
            )}
            {props.activeRun ? props.activeRun.status : "idle"}
          </div>
          <div className="composer-actions">
            {props.sending && (
              <button className="ghost-button" type="button" onClick={props.onCancel}>
                取消 Run
              </button>
            )}
            <button
              className="primary send-button"
              type="button"
              onClick={props.onSend}
              disabled={!props.canSend || props.sending || !props.draft.trim()}
            >
              发送
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
