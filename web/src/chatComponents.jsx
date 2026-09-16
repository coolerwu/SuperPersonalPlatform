import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Check, Copy, TerminalSquare, ArrowUp, Quote, X, Clock3, XCircle, CheckCircle2 } from "lucide-react";

export function ChatMessageList({ messages, onDecision, onQuote, quoteDisabled = false, onError = () => {}, emptyTitle = "开始一次页面对话", emptyDescription = "消息会进入长期 session；运行中输出会在这里实时刷新。" }) {
  const [copiedMessageId, setCopiedMessageId] = useState("");
  const copyFeedbackTimerRef = useRef(0);
  const messagesRef = useRef(null);
  const followingRef = useRef(true);
  const [hasNew, setHasNew] = useState(false);
  const [selectedQuote, setSelectedQuote] = useState(null);
  useEffect(() => {
    function select() {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || !selection.rangeCount || quoteDisabled) { setSelectedQuote(null); return; }
      const range = selection.getRangeAt(0);
      const element = (node) => node.nodeType === 1 ? node : node.parentElement;
      const body = element(range.startContainer)?.closest("[data-quote-body]");
      if (!body || !messagesRef.current?.contains(body) || !body.contains(range.endContainer)) { setSelectedQuote(null); return; }
      const message = messages.find((item) => item.id === body.dataset.quoteBody);
      const content = selection.toString().trim();
      if (!message || !content || message.streaming || message.approval || !(message.seq || message.saved)) { setSelectedQuote(null); return; }
      setSelectedQuote({ ...message, content, excerpt: content });
    }
    document.addEventListener("selectionchange", select);
    return () => document.removeEventListener("selectionchange", select);
  }, [messages, quoteDisabled]);
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
    if (!followingRef.current) { setHasNew(true); return undefined; }
    setHasNew(false);
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
        <div className="chat-messages" ref={messagesRef} onScroll={(event) => {
          const node = event.currentTarget;
          followingRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
          if (followingRef.current) setHasNew(false);
        }}>
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
                {message.reply ? <QuoteCard quote={message.reply} /> : null}
                {message.speaker ? <div className="chat-speaker"><strong>{message.speaker}</strong>{message.run_id ? <a href={`/runs?run_id=${encodeURIComponent(message.run_id)}`}>Run ↗</a> : null}</div> : null}
                {message.role === "assistant" && message.thinking?.length ? (
                  <ThinkingPanel items={message.thinking} running={message.streaming} collapsed={message.thinkingCollapsed !== false} />
                ) : null}
                <div data-quote-body={message.id}>
                {message.role === "assistant" && message.content ? (
                  <MarkdownMessage content={message.content} />
                ) : (
                  <pre className={message.streaming ? "chat-answer-placeholder" : ""}>
                    {message.content || (message.streaming ? "正在生成正文..." : "")}
                  </pre>
                )}
                </div>
                {message.role === "assistant" && message.approval ? (
                  <ApprovalPanel
                    approval={message.approval}
                    compact
                    onDecision={(decision, reason, scope) => decideChatApproval(message.run_id, decision, reason, scope)}
                  />
                ) : null}
                {message.cancelled ? <small>已停止</small> : null}
                {!message.approval && message.streaming ? <small>streaming</small> : null}
                <div className="chat-message-actions">
                  {onQuote && message.content && !message.streaming && !message.approval && (message.seq || message.saved) ? <button type="button" className="chat-copy-button" title="引用" aria-label="引用消息" disabled={quoteDisabled} onClick={() => onQuote(message)}><Quote size={15} /></button> : null}
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
                </div>
              </div>
            </div>
          ))}
          {selectedQuote && onQuote ? <button className="chat-selection-quote" onPointerDown={(event) => event.preventDefault()} onClick={() => {
            onQuote(selectedQuote); setSelectedQuote(null); window.getSelection()?.removeAllRanges();
          }}><Quote size={15} />引用所选内容</button> : null}
          {hasNew ? <button className="chat-new-messages" onClick={() => {
            followingRef.current = true;
            messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
            setHasNew(false);
          }}>有新消息 · 回到底部</button> : null}
        </div>

  );
}

export function ChatComposer({ value, onChange, onSend, busy = false, disabled = false, children, quote, onCancelQuote, placeholder = "输入消息，Enter 发送，Shift+Enter 换行" }) {
  const composingRef = useRef(false);
  const textareaRef = useRef(null);
  useLayoutEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    function resize() {
      const style = window.getComputedStyle(node);
      const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      const limit = parseFloat(style.lineHeight) * 20 + padding;
      node.style.height = "0px";
      const required = node.scrollHeight;
      node.style.height = `${Math.min(required, limit)}px`;
      node.style.overflowY = required > limit ? "auto" : "hidden";
    }
    resize();
    let width = node.clientWidth;
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => {
      if (node.clientWidth !== width) { width = node.clientWidth; resize(); }
    }) : null;
    observer?.observe(node);
    window.addEventListener("resize", resize);
    return () => { observer?.disconnect(); window.removeEventListener("resize", resize); };
  }, [value]);
  useEffect(() => { if (quote) textareaRef.current?.focus(); }, [quote]);
  return <div className="chat-composer-region">{quote ? <div className="chat-quote-draft"><QuoteCard quote={quote} onCancel={onCancelQuote} /></div> : null}<div className="chat-composer">
    <textarea ref={textareaRef} rows={1} value={value} placeholder={placeholder} disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      onCompositionStart={() => { composingRef.current = true; }}
      onCompositionEnd={() => { composingRef.current = false; }}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !event.shiftKey && !composingRef.current && !event.nativeEvent?.isComposing && event.keyCode !== 229) {
          event.preventDefault();
          if (!busy && !disabled && value.trim()) onSend();
        }
      }} />
    <div className="chat-composer-actions">{children}
    <button className="primary chat-send-button" aria-label="发送" title="发送" onClick={onSend} disabled={!value.trim() || busy || disabled}><ArrowUp size={23} /></button></div>
  </div></div>;
}

export function QuoteCard({ quote, onCancel }) {
  return <aside className="chat-quote"><div><strong>{quote.speaker || (quote.role === "user" ? "你" : "助手")}</strong><p>{quote.content}</p></div>{onCancel ? <button type="button" aria-label="取消引用" onClick={onCancel}><X size={14} /></button> : null}</aside>;
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
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState("");
  const submittingRef = useRef(false);
  const approvalKey = JSON.stringify(approval);
  const interrupts = Array.isArray(approval?.interrupts) ? approval.interrupts : [];
  const actions = interrupts.flatMap((interrupt) => (Array.isArray(interrupt.actions) ? interrupt.actions : []));
  if (actions.length === 0) return null;

  const filePaths = actions.map((action) =>
    ["write_file", "edit_file"].includes(action.name) && typeof action.args?.file_path === "string"
      && action.args.file_path.startsWith("/webdav/") ? action.args.file_path : null);
  const filePath = filePaths.length && filePaths.every((path) => path && path === filePaths[0]) ? filePaths[0] : null;

  async function decide(decision, scope = "once") {
    if (!onDecision || submittingRef.current || submitted === approvalKey) return;
    submittingRef.current = true;
    setBusy(scope === "file_10min" ? scope : decision);
    setError("");
    try {
      await onDecision(decision, reason.trim(), scope);
      setSubmitted(approvalKey);
    } catch (exc) {
      setError(exc.message || "审批提交失败，请重试");
    } finally {
      submittingRef.current = false;
      setBusy("");
    }
  }

  if (submitted === approvalKey) return <p role="status">审批已提交，正在同步任务状态…</p>;

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
        disabled={Boolean(busy)}
      />
      {error ? <p className="approval-error" role="alert">{error}</p> : null}
      {filePath ? <p className="approval-file-hint">文件授权：<code>{filePath}</code> · 当前会话内 10 分钟有效</p> : null}
      <div className="approval-controls">
        <button className="danger" onClick={() => decide("reject")} disabled={Boolean(busy)}>
          <XCircle size={15} />
          {busy === "reject" ? "拒绝中…" : "拒绝并继续"}
        </button>
        {filePath ? <button onClick={() => decide("approve", "file_10min")} disabled={Boolean(busy)}>
          <CheckCircle2 size={15} />
          {busy === "file_10min" ? "批准中…" : "批准当前文件（10 分钟）"}
        </button> : null}
        <button className="primary" onClick={() => decide("approve")} disabled={Boolean(busy)}>
          <CheckCircle2 size={15} />
          {busy === "approve" ? "批准中…" : "仅批准本次"}
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
