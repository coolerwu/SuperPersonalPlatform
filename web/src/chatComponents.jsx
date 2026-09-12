import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Check, Copy, TerminalSquare, Send, Clock3, XCircle, CheckCircle2 } from "lucide-react";

export function ChatMessageList({ messages, onDecision, onError = () => {}, emptyTitle = "开始一次页面对话", emptyDescription = "消息会进入长期 session；运行中输出会在这里实时刷新。" }) {
  const [copiedMessageId, setCopiedMessageId] = useState("");
  const copyFeedbackTimerRef = useRef(0);
  const messagesRef = useRef(null);
  useEffect(
    () => () => {
      if (copyFeedbackTimerRef.current) window.clearTimeout(copyFeedbackTimerRef.current);
    },
    [],
  );

  async function copyMessage(message) {
    try {
      await copyTextToClipboard(message.content);
      setCopiedMessageId(message.id);
      if (copyFeedbackTimerRef.current) window.clearTimeout(copyFeedbackTimerRef.current);
      copyFeedbackTimerRef.current = window.setTimeout(() => setCopiedMessageId(""), 1_600);
    } catch (exc) {
      onError(exc.message || "复制失败，请重试");
    }
  }

  useLayoutEffect(() => {
    const node = messagesRef.current;
    if (!node) return undefined;
    function scrollToBottom() {
      node.scrollTop = node.scrollHeight;
    }
    scrollToBottom();
    const frame = window.requestAnimationFrame ? window.requestAnimationFrame(scrollToBottom) : 0;
    return () => {
      if (frame && window.cancelAnimationFrame) {
        window.cancelAnimationFrame(frame);
      }
    };
  }, [messages]);

  const decideChatApproval = onDecision;
  return (
        <div className="chat-messages" ref={messagesRef}>
          {messages.length === 0 ? (
            <div className="chat-empty">
              <TerminalSquare size={30} />
              <strong>{emptyTitle}</strong>
              <span>{emptyDescription}</span>
            </div>
          ) : null}
          {messages.map((message) => (
            <div
              key={message.id}
              className={`chat-message ${message.role === "user" ? "user" : "assistant"} ${message.failed ? "failed" : ""}`}
            >
              <div className={`chat-bubble ${message.content ? "copyable" : ""}`}>
                {message.speaker ? <div className="chat-speaker"><strong>{message.speaker}</strong>{message.run_id ? <a href={`/runs?run_id=${encodeURIComponent(message.run_id)}`}>Run ↗</a> : null}</div> : null}
                {message.content ? (
                  <button
                    type="button"
                    className={`chat-copy-button ${copiedMessageId === message.id ? "copied" : ""}`}
                    onClick={() => copyMessage(message)}
                    aria-label={`${copiedMessageId === message.id ? "已复制" : "复制"}${message.role === "user" ? "用户" : "助手"}消息`}
                    title={copiedMessageId === message.id ? "已复制" : "复制消息"}
                  >
                    {copiedMessageId === message.id ? <Check size={15} /> : <Copy size={15} />}
                  </button>
                ) : null}
                {message.role === "assistant" && message.thinking?.length ? (
                  <ThinkingPanel items={message.thinking} running={message.streaming} collapsed={message.thinkingCollapsed !== false} />
                ) : null}
                {message.role === "assistant" && message.content ? (
                  <MarkdownMessage content={message.content} />
                ) : (
                  <pre className={message.streaming ? "chat-answer-placeholder" : ""}>
                    {message.content || (message.streaming ? "正在生成正文..." : "")}
                  </pre>
                )}
                {message.role === "assistant" && message.approval ? (
                  <ApprovalPanel
                    approval={message.approval}
                    compact
                    onDecision={(decision, reason) => decideChatApproval(message.run_id, decision, reason)}
                  />
                ) : null}
                {message.approval ? <small>等待审批</small> : message.streaming ? <small>streaming</small> : null}
              </div>
            </div>
          ))}
        </div>

  );
}

export function ChatComposer({ value, onChange, onSend, busy = false, disabled = false, children, placeholder = "输入消息，Enter 发送，Shift+Enter 换行" }) {
  const composingRef = useRef(false);
  return <div className="chat-composer">
    <textarea value={value} placeholder={placeholder} disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      onCompositionStart={() => { composingRef.current = true; }}
      onCompositionEnd={() => { composingRef.current = false; }}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !event.shiftKey && !composingRef.current && !event.nativeEvent?.isComposing && event.keyCode !== 229) {
          event.preventDefault();
          if (!busy && !disabled && value.trim()) onSend();
        }
      }} />
    {children}
    <button className="primary chat-send-button" onClick={onSend} disabled={!value.trim() || busy || disabled}><Send size={18} /><span>发送</span></button>
  </div>;
}

async function copyTextToClipboard(text) {
  if (globalThis.navigator?.clipboard?.writeText) {
    await globalThis.navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("浏览器未允许复制");
}

export function ApprovalPanel({ approval, onDecision, compact = false }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const interrupts = Array.isArray(approval?.interrupts) ? approval.interrupts : [];
  const actions = interrupts.flatMap((interrupt) => (Array.isArray(interrupt.actions) ? interrupt.actions : []));
  if (actions.length === 0) return null;

  async function decide(decision) {
    if (!onDecision || busy) return;
    setBusy(true);
    try {
      await onDecision(decision, reason.trim());
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={`approval-panel ${compact ? "compact" : ""}`}>
      <div className="approval-heading">
        <Clock3 size={17} />
        <div>
          <strong>等待操作审批</strong>
          <span>DeepAgent 已暂停，确认后从当前 checkpoint 继续。</span>
        </div>
      </div>
      <div className="approval-actions-list">
        {actions.map((action, index) => (
          <div className="approval-action" key={`${action.name || "tool"}-${index}`}>
            <div>
              <code>{action.name || "unknown_tool"}</code>
              <span>{action.description || "该工具调用需要人工确认"}</span>
            </div>
            <pre>{JSON.stringify(action.args || {}, null, 2)}</pre>
          </div>
        ))}
      </div>
      <textarea
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="拒绝原因（可选，DeepAgent 会收到）"
        rows={2}
        disabled={busy}
      />
      <div className="approval-controls">
        <button className="danger" onClick={() => decide("reject")} disabled={busy}>
          <XCircle size={15} />
          拒绝并继续
        </button>
        <button className="primary" onClick={() => decide("approve")} disabled={busy}>
          <CheckCircle2 size={15} />
          批准并继续
        </button>
      </div>
    </section>
  );
}

export function ThinkingPanel({ items, running, collapsed }) {
  const visibleItems = Array.isArray(items) ? items.slice(-10) : [];
  if (visibleItems.length === 0) return null;
  return (
    <details className={`thinking-panel ${running ? "running" : ""}`} open={running || !collapsed}>
      <summary>
        <span>思考过程</span>
        <small>{running ? "运行中" : "已折叠"}</small>
      </summary>
      <div className="thinking-body">
        {visibleItems.map((item, index) => (
          <div className="thinking-row" key={`${index}-${item}`}>
            {item}
          </div>
        ))}
      </div>
    </details>
  );
}

export function MarkdownMessage({ content }) {
  return <div className="markdown-message">{renderMarkdownBlocks(content)}</div>;
}

function renderMarkdownBlocks(content) {
  const lines = String(content || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let list = null;
  let quote = [];
  let code = null;

  function flushParagraph() {
    if (paragraph.length === 0) return;
    const text = paragraph.join(" ").trim();
    if (text) {
      blocks.push(<p key={`p-${blocks.length}`}>{renderMarkdownInline(text, `p-${blocks.length}`)}</p>);
    }
    paragraph = [];
  }

  function flushList() {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={`list-${blocks.length}`}>
        {list.items.map((item, index) => (
          <li key={index}>{renderMarkdownInline(item, `li-${blocks.length}-${index}`)}</li>
        ))}
      </Tag>,
    );
    list = null;
  }

  function flushQuote() {
    if (quote.length === 0) return;
    blocks.push(<blockquote key={`quote-${blocks.length}`}>{renderMarkdownInline(quote.join(" "), `quote-${blocks.length}`)}</blockquote>);
    quote = [];
  }

  function flushCode() {
    if (!code) return;
    blocks.push(
      <pre className="markdown-code" key={`code-${blocks.length}`}>
        <code>{code.lines.join("\n")}</code>
      </pre>,
    );
    code = null;
  }

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const rawLine = lines[lineIndex];
    const line = rawLine.trimEnd();
    const fenceMatch = line.match(/^```(\w+)?\s*$/);
    if (fenceMatch) {
      if (code) {
        flushCode();
      } else {
        flushParagraph();
        flushList();
        flushQuote();
        code = { language: fenceMatch[1] || "", lines: [] };
      }
      continue;
    }
    if (code) {
      code.lines.push(rawLine);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      flushQuote();
      continue;
    }
    if (isMarkdownHorizontalRule(line)) {
      flushParagraph();
      flushList();
      flushQuote();
      blocks.push(<hr key={`hr-${blocks.length}`} />);
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      flushQuote();
      const Tag = `h${heading[1].length + 2}`;
      blocks.push(<Tag key={`h-${blocks.length}`}>{renderMarkdownInline(heading[2], `h-${blocks.length}`)}</Tag>);
      continue;
    }
    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      flushQuote();
      const orderedList = Boolean(ordered);
      if (!list || list.ordered !== orderedList) {
        flushList();
        list = { ordered: orderedList, items: [] };
      }
      list.items.push((unordered?.[1] || ordered?.[1] || "").trim());
      continue;
    }
    const quoted = line.match(/^\s*>\s?(.+)$/);
    if (quoted) {
      flushParagraph();
      flushList();
      quote.push(quoted[1].trim());
      continue;
    }
    const nextLine = lines[lineIndex + 1]?.trimEnd() || "";
    if (isMarkdownTableStart(line, nextLine)) {
      flushParagraph();
      flushList();
      flushQuote();
      const header = splitMarkdownTableRow(line);
      const rows = [];
      lineIndex += 2;
      while (lineIndex < lines.length) {
        const rowLine = lines[lineIndex].trimEnd();
        if (!rowLine.trim() || rowLine.match(/^```/) || !rowLine.includes("|")) {
          lineIndex -= 1;
          break;
        }
        rows.push(splitMarkdownTableRow(rowLine));
        lineIndex += 1;
      }
      if (lineIndex >= lines.length) {
        lineIndex = lines.length - 1;
      }
      blocks.push(renderMarkdownTable(header, rows, `table-${blocks.length}`));
      continue;
    }
    paragraph.push(line.trim());
  }
  flushCode();
  flushParagraph();
  flushList();
  flushQuote();
  return blocks.length ? blocks : <p>{content}</p>;
}

function isMarkdownHorizontalRule(line) {
  return /^\s{0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$/.test(String(line || ""));
}

function isMarkdownTableStart(line, nextLine) {
  const header = splitMarkdownTableRow(line);
  const separator = splitMarkdownTableRow(nextLine);
  return header.length >= 2 && separator.length === header.length && separator.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitMarkdownTableRow(line) {
  const trimmed = String(line || "").trim();
  if (!trimmed.includes("|")) return [];
  return trimmed.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function normalizeMarkdownTableRow(row, width) {
  const cells = Array.isArray(row) ? row.slice(0, width) : [];
  while (cells.length < width) {
    cells.push("");
  }
  return cells;
}

function renderMarkdownTable(header, rows, keyPrefix) {
  const width = header.length;
  return (
    <div className="markdown-table-wrap" key={keyPrefix}>
      <table>
        <thead>
          <tr>
            {header.map((cell, index) => (
              <th key={`${keyPrefix}-h-${index}`}>{renderMarkdownInline(cell, `${keyPrefix}-h-${index}`)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${keyPrefix}-r-${rowIndex}`}>
              {normalizeMarkdownTableRow(row, width).map((cell, cellIndex) => (
                <td key={`${keyPrefix}-r-${rowIndex}-${cellIndex}`}>
                  {renderMarkdownInline(cell, `${keyPrefix}-r-${rowIndex}-${cellIndex}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderMarkdownInline(text, keyPrefix) {
  const value = String(text || "");
  const matcher = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s]+)/g;
  const nodes = [];
  let cursor = 0;
  let match;
  while ((match = matcher.exec(value)) !== null) {
    if (match.index > cursor) {
      nodes.push(value.slice(cursor, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}-${nodes.length}`;
    const link = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    if (link) {
      nodes.push(
        <a key={key} href={link[2]} target="_blank" rel="noreferrer">
          {link[1]}
        </a>,
      );
    } else if (token.startsWith("http://") || token.startsWith("https://")) {
      nodes.push(
        <a key={key} href={token} target="_blank" rel="noreferrer">
          {token}
        </a>,
      );
    } else if (token.startsWith("**") && token.endsWith("**")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else {
      nodes.push(token);
    }
    cursor = match.index + token.length;
  }
  if (cursor < value.length) {
    nodes.push(value.slice(cursor));
  }
  return nodes;
}
