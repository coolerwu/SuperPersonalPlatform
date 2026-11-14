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

type ChatPanelProps = {
  messages: TranscriptMessage[];
  queuedMessages: TranscriptMessage[];
  queuedInfo: Record<
    string,
    { index: number; total: number; canCancel: boolean }
  >;
  cancellingQueued: Record<string, true>;
  onCancelQueued: (messageId: string) => void;
  loading: boolean;
  loadError: string;
  onReload: () => void;
  streamText: string;
  streamReasoning: string;
  activity: LiveToolCall[];
  subagents: Record<string, LiveSubagent>;
  tasks: Record<string, LiveTask>;
  liveArtifacts: MessageArtifact[];
  sending: boolean;
  tracking: boolean;
  pendingSend: boolean;
  error: string;
  notice: string;
  commandReply: { command: string; text: string } | null;
  claudeCommands: string[];
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


const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

export function ChatPanel(props: ChatPanelProps) {
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
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const toolsRef = useRef<HTMLDivElement | null>(null);
  const uploadSeqRef = useRef(0);
  const [expanded, setExpanded] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [attachError, setAttachError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [commandIndex, setCommandIndex] = useState(0);
  const [commandMenuDismissed, setCommandMenuDismissed] = useState(false);
  const throttledStreamText = useThrottledText(props.streamText);
  const throttledStreamReasoning = useThrottledText(props.streamReasoning);

  const uploading = uploads.length > 0;
  const commandQuery = slashCommandQuery(props.draft);
  const commandMatches =
    commandQuery === null
      ? []
      : filterSlashCommands(commandQuery, props.claudeCommands);
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

  function jumpToBottom() {
    follow();
  }

  const canSubmit =
    props.canSend &&
    !props.pendingSend &&
    (props.draft.trim().length > 0 ||
      props.attachments.length > 0 ||
      props.quote.trim().length > 0);

  async function submit() {
    if (!canSubmit) return;
    // Sending always follows the newest message, even if the user had scrolled up.
    follow();
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

  const quoteFromMessage = useCallback((text: string) => {
    onQuoteRef.current(text);
    textareaRef.current?.focus();
  }, []);

  const applySuggestion = useCallback((text: string) => {
    onDraftChangeRef.current(text);
    textareaRef.current?.focus();
  }, []);

  const reloadSession = useCallback(() => onReloadRef.current(), []);

  return (
    <section className="chat-panel">
      <div className="message-scroll" ref={scrollRef}>
        <div className="message-flow" ref={contentRef}>
          <MessageList
            messages={props.messages}
            queuedMessages={props.queuedMessages}
            queuedInfo={props.queuedInfo}
            cancellingQueued={props.cancellingQueued}
            onCancelQueued={props.onCancelQueued}
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
            onQuote={quoteFromMessage}
            scrollRef={scrollRef}
            pauseFollow={pauseFollow}
            focusMessageId={props.focusMessageId}
            focusMessageNonce={props.focusMessageNonce}
          />
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
