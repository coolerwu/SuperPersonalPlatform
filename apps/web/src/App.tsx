import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { api } from "./api";
import type {
  Agent,
  AgentSavePayload,
  Attachment,
  DrawerTab,
  FrozenLive,
  LiveSubagent,
  LiveTask,
  MessageArtifact,
  Provider,
  QueuedInfo,
  SessionSummary,
  SkillInfo,
  TranscriptMessage,
  TurnStreamEvent,
} from "./types";
import { isBareSlashCommand } from "./messageContent";
import { describeApiError } from "./errors";
import { DEFAULT_PATH, parsePath, pathForView, type View } from "./routes";
import { sessionSummariesEqual } from "./sessionSummary";
import { ChatPanel } from "./components/ChatPanel";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { Drawer } from "./components/Drawer";
import { Login } from "./components/Login";
import { NewAgentDialog } from "./components/NewAgentDialog";
import { RenameSessionDialog } from "./components/RenameSessionDialog";
import { Sidebar } from "./components/Sidebar";

const PAGE_LOADERS = {
  settings: () =>
    import("./components/SettingsPage").then((module) => ({
      default: module.SettingsPage,
    })),
  memory: () =>
    import("./components/MemoryPage").then((module) => ({
      default: module.MemoryPage,
    })),
  skills: () =>
    import("./components/SkillsPage").then((module) => ({
      default: module.SkillsPage,
    })),
  tools: () =>
    import("./components/ToolsPage").then((module) => ({
      default: module.ToolsPage,
    })),
  plugins: () =>
    import("./components/PluginsPage").then((module) => ({
      default: module.PluginsPage,
    })),
  channels: () =>
    import("./components/ChannelsPage").then((module) => ({
      default: module.ChannelsPage,
    })),
  cron: () =>
    import("./components/CronPage").then((module) => ({
      default: module.CronPage,
    })),
  backtest: () =>
    import("./components/BacktestPage").then((module) => ({
      default: module.BacktestPage,
    })),
  journal: () =>
    import("./components/TradeJournalPage").then((module) => ({
      default: module.TradeJournalPage,
    })),
  chan: () =>
    import("./components/ChanPage").then((module) => ({
      default: module.ChanPage,
    })),
  allocation: () =>
    import("./components/AllocationPage").then((module) => ({
      default: module.AllocationPage,
    })),
} as const;

const SettingsPage = lazy(PAGE_LOADERS.settings);
const MemoryPage = lazy(PAGE_LOADERS.memory);
const SkillsPage = lazy(PAGE_LOADERS.skills);
const ToolsPage = lazy(PAGE_LOADERS.tools);
const PluginsPage = lazy(PAGE_LOADERS.plugins);
const ChannelsPage = lazy(PAGE_LOADERS.channels);
const CronPage = lazy(PAGE_LOADERS.cron);
const BacktestPage = lazy(PAGE_LOADERS.backtest);
const TradeJournalPage = lazy(PAGE_LOADERS.journal);
const ChanPage = lazy(PAGE_LOADERS.chan);
const AllocationPage = lazy(PAGE_LOADERS.allocation);

function PageFallback() {
  return (
    <div className="page-loading" role="status" aria-label="页面加载中">
      正在打开…
    </div>
  );
}

/** A slash-command reply shown once in the current chat lane, never persisted. */
type CommandReply = {
  key: string;
  command: string;
  text: string;
};

type CommandOutcome =
  | { outcome: "handled" }
  | { outcome: "forward"; command: string }
  | { outcome: "error" };

/** Live turn state is kept per session so switching away never drops it. */
type SessionLive = FrozenLive & {
  notice: string;
  tracking: boolean;
  frozen: FrozenLive | null;
};

const EMPTY_LIVE: SessionLive = {
  streamText: "",
  streamReasoning: "",
  activity: [],
  subagents: {},
  tasks: {},
  artifacts: [],
  notice: "",
  tracking: false,
  frozen: null,
};

function emptySubagent(parent: string): LiveSubagent {
  return { parent, text: "", tools: [] };
}

function emptyTask(
  id: string,
  toolUseId: string | null,
  description = "",
): LiveTask {
  return {
    id,
    toolUseId,
    description,
    status: "running",
    summary: "",
    lastToolName: "",
    usage: null,
  };
}

function mergeArtifact(
  list: MessageArtifact[],
  artifact: MessageArtifact,
): MessageArtifact[] {
  const index = list.findIndex((item) => item.path === artifact.path);
  if (index < 0) return [...list, artifact];
  const next = list.slice();
  next[index] = artifact;
  return next;
}

type FocusTarget = {
  sessionId: string;
  messageId: string;
  nonce: number;
};

/** A user message sent by this tab that has not reached the transcript yet. */
type PendingUser = {
  id: string;
  content: string;
  turnId: string | null;
  /** True while another turn is still streaming in this session. */
  queued: boolean;
};

const QUEUED_NOTICE = "已排队，前面的任务完成后会自动执行";
const RECONNECT_NOTICE = "连接中断，正在重连…";
const RECONNECT_LOST_NOTICE = "连接已断开，已刷新为最新历史";
const MAX_STREAM_RETRIES = 8;
const SIDEBAR_POLL_MS = 5000;

function queuedNotice(count: number): string {
  return count > 0
    ? `已排队 ${count} 条，前面的任务完成后依次执行`
    : QUEUED_NOTICE;
}

const NEW_SESSION_KEY = "__new__";
const AGENT_STORAGE_KEY = "acc.selectedAgent";

function readStoredAgent(): string {
  try {
    return window.localStorage.getItem(AGENT_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function storeAgent(id: string): void {
  if (!id) return;
  try {
    window.localStorage.setItem(AGENT_STORAGE_KEY, id);
  } catch {
    // Private mode or full storage: routing still works without the hint.
  }
}

/** Drafts and attachments belong to one conversation (or one pending new one). */
function sessionKey(agentId: string, sessionId: string): string {
  return `${agentId}:${sessionId || NEW_SESSION_KEY}`;
}

/** `crypto.randomUUID` needs a secure context; LAN HTTP still needs ids. */
function newSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const value = (Math.random() * 16) | 0;
    const nibble = char === "x" ? value : (value & 0x3) | 0x8;
    return nibble.toString(16);
  });
}

function transcriptMessage(
  role: "user" | "assistant",
  content: string,
  id: string,
): TranscriptMessage {
  return { id, role, content, blocks: [] };
}

/** Whether the transcript contains a non-empty assistant reply after the last user question. */
function transcriptHasReply(list: TranscriptMessage[]): boolean {
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const message = list[index];
    if (message.role === "user") return false;
    if (message.role === "assistant" && message.content.trim()) return true;
  }
  return false;
}

/** Whether the interrupted partial already landed in the transcript. */
function frozenReplyPersisted(
  list: TranscriptMessage[],
  frozen: FrozenLive,
): boolean {
  const text = frozen.streamText.trim();
  if (!text) return transcriptHasReply(list);
  const normalize = (value: string) => value.replace(/\s+/g, "");
  const head = normalize(text).slice(0, 120);
  return list.some((message) => {
    if (message.role !== "assistant") return false;
    const content = normalize(message.content);
    if (!content) return false;
    return (
      content.startsWith(head) ||
      (content.length >= 20 && head.startsWith(content))
    );
  });
}

export default function App() {
  const [initialRoute] = useState(() => parsePath(window.location.pathname));
  const [auth, setAuth] = useState<boolean | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillsErrors, setSkillsErrors] = useState<string[]>([]);
  const [agentId, setAgentId] = useState("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState(initialRoute?.sessionId ?? "");
  const [messagesBySession, setMessagesBySession] = useState<Record<string, TranscriptMessage[]>>({});
  const [loadingBySession, setLoadingBySession] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState("");
  const [claudeCommands, setClaudeCommands] = useState<string[]>([]);
  const [commandReply, setCommandReply] = useState<CommandReply | null>(null);
  const [attachmentsByKey, setAttachmentsByKey] = useState<Record<string, Attachment[]>>({});
  const [quotesByKey, setQuotesByKey] = useState<Record<string, string>>({});
  const [pendingSessions, setPendingSessions] = useState<Record<string, true>>({});
  const [pendingUserBySession, setPendingUserBySession] = useState<
    Record<string, PendingUser[]>
  >({});
  const [loadedSessions, setLoadedSessions] = useState<Record<string, true>>({});
  const [loadErrorBySession, setLoadErrorBySession] = useState<Record<string, string>>({});
  const [liveBySession, setLiveBySession] = useState<Record<string, SessionLive>>({});
  const [error, setError] = useState("");
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("run");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [view, setView] = useState<View>(initialRoute?.view ?? "chat");
  const [focusSignal, setFocusSignal] = useState(0);
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<SessionSummary | null>(null);
  const [sessionToRename, setSessionToRename] = useState<SessionSummary | null>(
    null,
  );
  const [focus, setFocus] = useState<FocusTarget | null>(null);
  const [cancellingQueued, setCancellingQueued] = useState<Record<string, true>>({});
  const [unreadBySession, setUnreadBySession] = useState<
    Record<string, "done" | "failed">
  >({});

  const streamsRef = useRef<Map<string, EventSource>>(new Map());
  const retryTimersRef = useRef<Map<string, number>>(new Map());
  const deferredRefreshRef = useRef<Map<string, number>>(new Map());
  const transcriptRetryRef = useRef<Map<string, number>>(new Map());
  const sessionsRef = useRef<SessionSummary[]>([]);
  const sessionAgentRef = useRef<Record<string, string>>({});
  const transcriptRequestRef = useRef<Record<string, number>>({});
  const skipRefreshRef = useRef<Set<string>>(new Set());
  const followUpRef = useRef<Set<string>>(new Set());
  const pendingUsersRef = useRef<Record<string, PendingUser[]>>({});
  const consumedUsersRef = useRef<Record<string, Record<string, number>>>({});
  // Explicit queued turn ids make the next subscription deterministic instead
  // of racing the registry with a no-turn-id attach.
  const queuedTurnsRef = useRef<Record<string, string[]>>({});
  const activeTurnRef = useRef<Record<string, string>>({});
  const loadedSessionsRef = useRef<Set<string>>(new Set());
  // Last loaded transcript counts, kept across agent switches so a new send of
  // text that already exists in history gets a correct matching baseline.
  const transcriptUserCountsRef = useRef<Record<string, Record<string, number>>>({});
  const sessionIdRef = useRef("");
  const sessionsRequestRef = useRef(0);
  const agentIdRef = useRef("");
  const draftsRef = useRef<Record<string, string>>({});
  const draftKeyRef = useRef("");
  const pendingSessionsRef = useRef<Record<string, true>>({});
  const commandPendingRef = useRef(false);
  // A /chat/<id> deep link is applied once the agent's sessions load.
  const restoreSessionRef = useRef(initialRoute?.sessionId ?? "");
  const lastEventIndexRef = useRef<Record<string, number>>({});
  const reconnectAttemptsRef = useRef<Record<string, number>>({});
  const streamGenerationRef = useRef(0);
  const cancelQueuedRef = useRef(cancelQueued);
  cancelQueuedRef.current = cancelQueued;
  const cancelQueuedMessage = useCallback((messageId: string) => {
    void cancelQueuedRef.current(sessionIdRef.current, messageId);
  }, []);

  const activeKey = sessionKey(agentId, sessionId);
  const visibleCommandReply =
    commandReply && commandReply.key === activeKey ? commandReply : null;
  const attachments = attachmentsByKey[activeKey] ?? [];
  const currentSession = sessions.find((item) => item.id === sessionId) ?? null;
  const agent = agents.find((item) => item.id === agentId);
  const live = liveBySession[sessionId] ?? EMPTY_LIVE;
  const pendingHere = Boolean(pendingSessions[activeKey]);
  const tracking = live.tracking;
  const chatSending = pendingHere || tracking;
  const sessionLoaded = !sessionId || Boolean(loadedSessions[sessionId]);
  const messages = useMemo(
    () => [
      ...(messagesBySession[sessionId] ?? []),
      ...(pendingUserBySession[sessionId] ?? [])
        .filter((item) => !item.queued)
        .map((item) => transcriptMessage("user", item.content, item.id)),
    ],
    [messagesBySession, pendingUserBySession, sessionId],
  );
  const queuedMessages = useMemo(
    () =>
      (pendingUserBySession[sessionId] ?? [])
        .filter((item) => item.queued)
        .map((item) => transcriptMessage("user", item.content, item.id)),
    [pendingUserBySession, sessionId],
  );
  const queuedInfo = useMemo(() => {
    const items = (pendingUserBySession[sessionId] ?? []).filter(
      (item) => item.queued,
    );
    const info: Record<string, QueuedInfo> = {};
    items.forEach((item, index) => {
      info[item.id] = {
        index: index + 1,
        total: items.length,
        canCancel: Boolean(item.turnId),
      };
    });
    return info;
  }, [pendingUserBySession, sessionId]);
  const messagesLoading = loadingBySession[sessionId] ?? false;
  const sessionTitle = currentSession?.title || "新对话";
  const viewTitle =
    view === "settings"
      ? "配置"
      : view === "memory"
        ? "记忆"
        : view === "skills"
          ? "技能"
          : view === "tools"
            ? "工具"
            : view === "plugins"
              ? "插件"
              : view === "channels"
              ? "渠道"
              : view === "cron"
                ? "定时"
                : view === "backtest"
                  ? "回测"
                  : view === "journal"
                    ? "交易记录"
                    : view === "chan"
                      ? "缠论图"
                      : view === "allocation"
                        ? "资产配置"
                        : (agent?.name ?? "Agent");

  function setAgentError(message: string, forAgent: string) {
    if (forAgent !== agentIdRef.current) return;
    setError(message);
  }

  function setSessionError(message: string, forSession: string | null | undefined) {
    if (forSession && forSession !== sessionIdRef.current) return;
    setError(message);
  }

  function updateLive(id: string, patch: (current: SessionLive) => SessionLive) {
    if (!id) return;
    setLiveBySession((current) => ({ ...current, [id]: patch(current[id] ?? EMPTY_LIVE) }));
  }

  function clearLiveNotice(id: string) {
    if (!id) return;
    setLiveBySession((current) => {
      const session = current[id];
      if (!session?.notice) return current;
      return { ...current, [id]: { ...session, notice: "" } };
    });
  }

  function applySessions(list: SessionSummary[]) {
    sessionsRef.current = list;
    setSessions((current) =>
      sessionSummariesEqual(current, list) ? current : list,
    );
  }

  function markUnread(id: string, status: "done" | "failed") {
    setUnreadBySession((current) =>
      current[id] === status ? current : { ...current, [id]: status },
    );
  }

  function clearUnread(id: string) {
    setUnreadBySession((current) => {
      if (!(id in current)) return current;
      const next = { ...current };
      delete next[id];
      return next;
    });
  }

  function shouldMarkUnread(id: string): boolean {
    return (
      id !== sessionIdRef.current ||
      document.visibilityState !== "visible" ||
      !document.hasFocus()
    );
  }

  function setMessagesFor(id: string, list: TranscriptMessage[]) {
    setMessagesBySession((current) => ({ ...current, [id]: list }));
  }

  function updatePendingUsers(next: Record<string, PendingUser[]>) {
    pendingUsersRef.current = next;
    setPendingUserBySession(next);
  }

  function pushPendingUser(id: string, item: PendingUser) {
    // Baseline the match counter on what the transcript already contains: a
    // reloaded history with the same text must not swallow the new message.
    const known =
      transcriptUserCountsRef.current[id]?.[item.content] ??
      (messagesBySession[id] ?? []).filter(
        (message) => message.role === "user" && message.content === item.content,
      ).length;
    const consumed = { ...(consumedUsersRef.current[id] ?? {}) };
    consumed[item.content] = Math.max(consumed[item.content] ?? 0, known);
    consumedUsersRef.current[id] = consumed;
    updatePendingUsers({
      ...pendingUsersRef.current,
      [id]: [...(pendingUsersRef.current[id] ?? []), item],
    });
  }

  function bindPendingTurn(id: string, messageId: string, turnId: string) {
    const list = pendingUsersRef.current[id];
    if (!list) return;
    updatePendingUsers({
      ...pendingUsersRef.current,
      [id]: list.map((item) =>
        item.id === messageId ? { ...item, turnId } : item,
      ),
    });
  }

  function setPendingQueued(id: string, messageId: string, queued: boolean) {
    const list = pendingUsersRef.current[id];
    if (!list) return;
    let changed = false;
    const nextList = list.map((item) => {
      if (item.id !== messageId || item.queued === queued) return item;
      changed = true;
      return { ...item, queued };
    });
    if (changed) updatePendingUsers({ ...pendingUsersRef.current, [id]: nextList });
  }

  /** Its turn is now the live one: render the question before the answer. */
  function markPendingTurnActive(id: string, turnId: string) {
    const list = pendingUsersRef.current[id];
    if (!list) return;
    let changed = false;
    const nextList = list.map((item) => {
      if (item.turnId !== turnId || !item.queued) return item;
      changed = true;
      return { ...item, queued: false };
    });
    if (changed) updatePendingUsers({ ...pendingUsersRef.current, [id]: nextList });
  }

  /** A failed/cancelled turn will never write its row; remove the ghost. */
  function dropPendingForTurn(id: string, turnId: string) {
    const list = pendingUsersRef.current[id];
    if (!list) return;
    const remaining = list.filter((item) => item.turnId !== turnId);
    if (remaining.length === list.length) return;
    const next = { ...pendingUsersRef.current };
    if (remaining.length > 0) next[id] = remaining;
    else delete next[id];
    updatePendingUsers(next);
  }

  function removePendingUser(id: string, messageId: string) {
    const list = pendingUsersRef.current[id];
    if (!list) return;
    const remaining = list.filter((item) => item.id !== messageId);
    const next = { ...pendingUsersRef.current };
    if (remaining.length > 0) next[id] = remaining;
    else delete next[id];
    updatePendingUsers(next);
  }

  function dropPendingUsers(id: string) {
    if (!pendingUsersRef.current[id]) return;
    const next = { ...pendingUsersRef.current };
    delete next[id];
    updatePendingUsers(next);
  }

  /** The session is gone; drop both pending rows and the match counters. */
  function forgetPendingUsers(id: string) {
    delete consumedUsersRef.current[id];
    delete transcriptUserCountsRef.current[id];
    dropPendingUsers(id);
    forgetQueuedTurns(id);
    unmarkSessionLoaded(id);
    clearTranscriptRetry(id);
  }

  function markSessionLoaded(id: string) {
    loadedSessionsRef.current.add(id);
    setLoadedSessions((current) => (current[id] ? current : { ...current, [id]: true }));
  }

  function setLoadErrorFor(id: string, message: string) {
    setLoadErrorBySession((current) => {
      if (!message) {
        if (!(id in current)) return current;
        const next = { ...current };
        delete next[id];
        return next;
      }
      if (current[id] === message) return current;
      return { ...current, [id]: message };
    });
  }

  function unmarkSessionLoaded(id: string) {
    loadedSessionsRef.current.delete(id);
    setLoadedSessions((current) => {
      if (!current[id]) return current;
      const next = { ...current };
      delete next[id];
      return next;
    });
  }

  function sessionQueueKey(id: string): string {
    return sessionKey(sessionAgentRef.current[id] ?? agentIdRef.current, id);
  }

  function enqueueTurn(id: string, turnId: string) {
    const key = sessionQueueKey(id);
    queuedTurnsRef.current = {
      ...queuedTurnsRef.current,
      [key]: [...(queuedTurnsRef.current[key] ?? []), turnId],
    };
  }

  function shiftQueuedTurn(id: string): string | null {
    const key = sessionQueueKey(id);
    const list = queuedTurnsRef.current[key];
    if (!list?.length) return null;
    const [next, ...rest] = list;
    const queued = { ...queuedTurnsRef.current };
    if (rest.length > 0) queued[key] = rest;
    else delete queued[key];
    queuedTurnsRef.current = queued;
    return next;
  }

  function clearQueuedTurns(id: string) {
    const key = sessionQueueKey(id);
    if (!queuedTurnsRef.current[key]) return;
    const queued = { ...queuedTurnsRef.current };
    delete queued[key];
    queuedTurnsRef.current = queued;
  }

  async function cancelQueued(sessionId: string, messageId: string) {
    const item = (pendingUsersRef.current[sessionId] ?? []).find(
      (entry) => entry.id === messageId,
    );
    if (!item?.turnId) return;
    const turnId = item.turnId;
    setCancellingQueued((current) => ({ ...current, [messageId]: true }));
    try {
      const result = await api.cancelQueuedTurn(sessionId, turnId);
      if (result.cancelled) {
        removePendingUser(sessionId, messageId);
        const key = sessionQueueKey(sessionId);
        const remaining = (queuedTurnsRef.current[key] ?? []).filter(
          (id) => id !== turnId,
        );
        if (remaining.length > 0) {
          queuedTurnsRef.current = {
            ...queuedTurnsRef.current,
            [key]: remaining,
          };
        } else {
          clearQueuedTurns(sessionId);
        }
        const pending = pendingUsersRef.current[sessionId] ?? [];
        const waiting = pending.filter((entry) => entry.queued).length;
        updateLive(sessionId, (current) => ({
          ...current,
          notice:
            current.notice && !current.notice.startsWith("已排队")
              ? current.notice
              : waiting > 0
                ? queuedNotice(waiting)
                : "",
        }));
        void refreshSessions();
      } else {
        await refreshTranscript(sessionId);
        updateLive(sessionId, (current) => ({
          ...current,
          notice:
            current.notice && !current.notice.startsWith("已排队")
              ? current.notice
              : "这条已经开始或已经结束，队列已刷新",
        }));
      }
    } catch (exc) {
      setSessionError(describeApiError(exc), sessionId);
    } finally {
      setCancellingQueued((current) => {
        const next = { ...current };
        delete next[messageId];
        return next;
      });
    }
  }

  function forgetQueuedTurns(id: string) {
    const queued = { ...queuedTurnsRef.current };
    for (const key of Object.keys(queued)) {
      if (key.endsWith(`:${id}`)) delete queued[key];
    }
    queuedTurnsRef.current = queued;
    delete activeTurnRef.current[id];
  }

  /**
   * A queued turn's user message only reaches the transcript when that turn
   * starts, so keep unmatched sends visible until their text appears.
   *
   * Known edge: a queued turn cancelled outside this tab (another client or a
   * command path we do not observe) can leave an unmatched pending row until a
   * later transcript refresh or session reopen reconciles it.
   */
  function settlePendingUsers(id: string, transcript: TranscriptMessage[]) {
    const pending = pendingUsersRef.current[id];
    if (!pending?.length) return;
    const counts: Record<string, number> = {};
    for (const message of transcript) {
      if (message.role === "user") {
        counts[message.content] = (counts[message.content] ?? 0) + 1;
      }
    }
    const consumed = { ...(consumedUsersRef.current[id] ?? {}) };
    const remaining: PendingUser[] = [];
    for (const item of pending) {
      const total = counts[item.content] ?? 0;
      const used = consumed[item.content] ?? 0;
      if (total > used) {
        consumed[item.content] = used + 1;
        continue;
      }
      remaining.push(item);
    }
    consumedUsersRef.current[id] = consumed;
    const next = { ...pendingUsersRef.current };
    if (remaining.length > 0) next[id] = remaining;
    else delete next[id];
    updatePendingUsers(next);
  }

  function setLoadingFor(id: string, value: boolean) {
    setLoadingBySession((current) => {
      if ((current[id] ?? false) === value) return current;
      return { ...current, [id]: value };
    });
  }

  /** Keep a freshly created session in the sidebar until its transcript lands. */
  function mergeLiveSession(list: SessionSummary[]): SessionSummary[] {
    const current = sessionIdRef.current;
    if (!current) return list;
    if (list.some((item) => item.id === current)) return list;
    if (!streamsRef.current.has(current)) return list;
    const known = sessionsRef.current.find((item) => item.id === current);
    const placeholder: SessionSummary = known ?? {
      id: current,
      title: "新会话",
      last_modified: Date.now(),
      channel_type: null,
      channel_name: null,
      busy: true,
    };
    return [{ ...placeholder, busy: true }, ...list];
  }

  function changeDraft(value: string | ((current: string) => string)) {
    const key = draftKeyRef.current;
    setDraft((current) => {
      const next = typeof value === "function" ? value(current) : value;
      draftsRef.current[key] = next;
      return next;
    });
  }

  function setDraftFor(key: string, value: string) {
    draftsRef.current[key] = value;
    if (draftKeyRef.current === key) setDraft(value);
  }

  // A send request is pending per session, so turns in other sessions (and in
  // other agents' view of the same "new chat" lane) never lock this composer.
  function markPending(key: string) {
    if (pendingSessionsRef.current[key]) return;
    pendingSessionsRef.current = { ...pendingSessionsRef.current, [key]: true };
    setPendingSessions(pendingSessionsRef.current);
  }

  function clearPending(key: string) {
    if (!pendingSessionsRef.current[key]) return;
    const next = { ...pendingSessionsRef.current };
    delete next[key];
    pendingSessionsRef.current = next;
    setPendingSessions(next);
  }

  function addAttachmentFor(key: string, file: Attachment) {
    setAttachmentsByKey((current) => {
      const list = current[key] ?? [];
      if (list.some((item) => item.path === file.path)) return current;
      return { ...current, [key]: [...list, file] };
    });
  }

  function removeAttachmentFor(key: string, path: string) {
    setAttachmentsByKey((current) => {
      const list = current[key];
      if (!list) return current;
      return { ...current, [key]: list.filter((item) => item.path !== path) };
    });
  }

  function clearAttachmentsFor(key: string) {
    setAttachmentsByKey((current) => {
      if (!current[key]) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function clearRetryTimer(id: string) {
    const timer = retryTimersRef.current.get(id);
    if (timer === undefined) return;
    window.clearTimeout(timer);
    retryTimersRef.current.delete(id);
  }

  function clearTranscriptRetry(id: string) {
    const timer = transcriptRetryRef.current.get(id);
    if (timer === undefined) return;
    window.clearTimeout(timer);
    transcriptRetryRef.current.delete(id);
  }

  function closeStream(id: string) {
    clearRetryTimer(id);
    const deferred = deferredRefreshRef.current.get(id);
    if (deferred !== undefined) {
      window.clearTimeout(deferred);
      deferredRefreshRef.current.delete(id);
    }
    const source = streamsRef.current.get(id);
    if (!source) return;
    streamsRef.current.delete(id);
    source.close();
    // The live stream was this session's active turn.
    delete activeTurnRef.current[id];
  }

  function closeAllStreams() {
    streamGenerationRef.current += 1;
    lastEventIndexRef.current = {};
    reconnectAttemptsRef.current = {};
    const ids = new Set([
      ...streamsRef.current.keys(),
      ...retryTimersRef.current.keys(),
      ...deferredRefreshRef.current.keys(),
      ...transcriptRetryRef.current.keys(),
    ]);
    for (const id of Array.from(ids)) {
      closeStream(id);
      clearTranscriptRetry(id);
    }
  }

  function attachIfBusy(id: string) {
    if (!id || streamsRef.current.has(id)) return;
    const row = sessionsRef.current.find((item) => item.id === id);
    if (row?.busy) openStream(id);
  }

  function applyStreamEvent(
    forSession: string,
    source: EventSource,
    event: TurnStreamEvent,
  ) {
    switch (event.type) {
      case "assistant_delta":
        updateLive(forSession, (current) => ({
          ...current,
          streamText: current.streamText + event.delta,
          notice: "",
        }));
        break;
      case "reasoning_delta":
        updateLive(forSession, (current) => ({
          ...current,
          streamReasoning: current.streamReasoning + event.delta,
          notice: "",
        }));
        break;
      case "tool_call":
        updateLive(forSession, (current) => ({
          ...current,
          activity: [
            ...current.activity,
            {
              id: event.id ?? "",
              name: event.name,
              input: event.input ?? null,
            },
          ],
          artifacts: event.artifact
            ? mergeArtifact(current.artifacts, event.artifact)
            : current.artifacts,
          notice: "",
        }));
        break;
      case "subagent_text": {
        const parent = event.parent_tool_use_id;
        updateLive(forSession, (current) => {
          const existing = current.subagents[parent] ?? emptySubagent(parent);
          return {
            ...current,
            subagents: {
              ...current.subagents,
              [parent]: { ...existing, text: existing.text + event.text },
            },
            notice: "",
          };
        });
        break;
      }
      case "subagent_tool_call": {
        const parent = event.parent_tool_use_id;
        updateLive(forSession, (current) => {
          const existing = current.subagents[parent] ?? emptySubagent(parent);
          return {
            ...current,
            subagents: {
              ...current.subagents,
              [parent]: {
                ...existing,
                tools: [
                  ...existing.tools,
                  {
                    id: event.id ?? "",
                    name: event.name,
                    input: event.input ?? null,
                  },
                ],
              },
            },
            artifacts: event.artifact
              ? mergeArtifact(current.artifacts, event.artifact)
              : current.artifacts,
            notice: "",
          };
        });
        break;
      }
      case "task_started": {
        const id = event.task_id;
        updateLive(forSession, (current) => ({
          ...current,
          tasks: {
            ...current.tasks,
            [id]: emptyTask(
              id,
              event.tool_use_id ?? null,
              event.description ?? "",
            ),
          },
          notice: "",
        }));
        break;
      }
      case "task_progress": {
        const id = event.task_id;
        updateLive(forSession, (current) => {
          const existing =
            current.tasks[id] ??
            emptyTask(id, event.tool_use_id ?? null, event.description ?? "");
          return {
            ...current,
            tasks: {
              ...current.tasks,
              [id]: {
                ...existing,
                description: event.description || existing.description,
                lastToolName: event.last_tool_name || existing.lastToolName,
                usage: event.usage ?? existing.usage,
              },
            },
            notice: "",
          };
        });
        break;
      }
      case "task_updated": {
        const id = event.task_id;
        updateLive(forSession, (current) => {
          const existing = current.tasks[id];
          if (!existing) return current;
          return {
            ...current,
            tasks: {
              ...current.tasks,
              [id]: {
                ...existing,
                status: event.status || existing.status,
              },
            },
            notice: "",
          };
        });
        break;
      }
      case "task_notification": {
        const id = event.task_id;
        updateLive(forSession, (current) => {
          const existing =
            current.tasks[id] ?? emptyTask(id, event.tool_use_id ?? null);
          return {
            ...current,
            tasks: {
              ...current.tasks,
              [id]: {
                ...existing,
                status: event.status || existing.status,
                summary: event.summary || existing.summary,
                usage: event.usage ?? existing.usage,
              },
            },
            notice: "",
          };
        });
        break;
      }
      case "turn_end": {
        if (streamsRef.current.get(forSession) !== source) return;
        const endedTurnId = activeTurnRef.current[forSession] ?? null;
        delete activeTurnRef.current[forSession];
        closeStream(forSession);
        if (event.status === "failed") {
          updateLive(forSession, (current) => ({
            ...current,
            notice: event.error || "本轮执行失败。",
          }));
        } else if (event.status === "cancelled") {
          updateLive(forSession, (current) => ({ ...current, notice: "已停止当前任务。" }));
        }
        void finishTurn(forSession, event.status ?? null, endedTurnId);
        const owner = sessionAgentRef.current[forSession] ?? agentIdRef.current;
        if (owner) refreshClaudeCommands(owner);
        break;
      }
    }
  }

  function bindStreamSource(forSession: string, source: EventSource) {
    source.onmessage = (message) => {
      let event: TurnStreamEvent;
      try {
        event = JSON.parse(message.data) as TurnStreamEvent;
      } catch {
        return;
      }
      if (message.lastEventId !== "") {
        const index = Number(message.lastEventId);
        if (Number.isFinite(index)) {
          lastEventIndexRef.current[forSession] = index;
          reconnectAttemptsRef.current[forSession] = 0;
        }
      }
      applyStreamEvent(forSession, source, event);
    };
    source.onopen = () => {
      updateLive(forSession, (current) =>
        current.notice === RECONNECT_NOTICE ||
        current.notice === RECONNECT_LOST_NOTICE
          ? { ...current, notice: "" }
          : current,
      );
    };
  }

  function openStream(forSession: string, turnId?: string, after = 0) {
    // One subscription per turn: re-subscribing replays the stream buffer and
    // would append the same deltas twice.
    if (streamsRef.current.has(forSession)) return;
    closeStream(forSession);
    if (!sessionAgentRef.current[forSession]) {
      sessionAgentRef.current[forSession] = agentIdRef.current;
    }
    const resuming = after > 0;
    lastEventIndexRef.current[forSession] = resuming ? after - 1 : -1;
    const source = new EventSource(
      api.sessionStreamUrl(forSession, turnId, after),
    );
    streamsRef.current.set(forSession, source);
    if (turnId) activeTurnRef.current[forSession] = turnId;
    else delete activeTurnRef.current[forSession];
    updateLive(forSession, (current) =>
      resuming
        ? { ...current, tracking: true }
        : {
            ...current,
            tracking: true,
            streamText: "",
            streamReasoning: "",
            activity: [],
            subagents: {},
            tasks: {},
            artifacts: [],
          },
    );
    bindStreamSource(forSession, source);

    source.onerror = () => {
      if (streamsRef.current.get(forSession) !== source) return;
      const reconnectTurnId = activeTurnRef.current[forSession] ?? null;
      const attachMode = reconnectTurnId === null;
      const attempt = (reconnectAttemptsRef.current[forSession] ?? 0) + 1;
      reconnectAttemptsRef.current[forSession] = attempt;
      closeStream(forSession);
      void reconnectStream(forSession, reconnectTurnId, attachMode, attempt);
    };
  }

  async function reconnectStream(
    forSession: string,
    turnId: string | null,
    attachMode: boolean,
    attempt: number,
  ) {
    if (streamsRef.current.has(forSession)) return;
    let status: {
      turn_id: string | null;
      busy: boolean;
      alive: boolean;
    } | null = null;
    const generation = streamGenerationRef.current;
    try {
      status = await api.getSessionTurn(forSession, turnId ?? undefined);
    } catch {
      status = null;
    }
    if (generation !== streamGenerationRef.current) return;
    if (status && attachMode) {
      if (status.busy) {
        if (attempt > MAX_STREAM_RETRIES) {
          abandonStream(forSession);
          return;
        }
        scheduleStreamAttach(forSession, attempt);
      } else {
        reconnectAttemptsRef.current[forSession] = 0;
        updateLive(forSession, (current) => ({
          ...current,
          tracking: false,
          notice: "",
        }));
        void refreshTranscript(forSession);
      }
      return;
    }
    if (status && turnId && status.alive) {
      if (attempt > MAX_STREAM_RETRIES) {
        abandonStream(forSession);
        return;
      }
      scheduleStreamReconnect(forSession, turnId, attempt);
      return;
    }
    if (status && turnId) {
      // The turn ended (or its stream was evicted) while disconnected.
      reconnectAttemptsRef.current[forSession] = 0;
      updateLive(forSession, (current) => ({
        ...current,
        tracking: false,
        notice: current.notice === RECONNECT_NOTICE ? "" : current.notice,
      }));
      void refreshTranscript(forSession).then(() => {
        if (generation !== streamGenerationRef.current) return;
        if (status?.busy && !streamsRef.current.has(forSession)) {
          scheduleStreamAttach(forSession, attempt);
        }
      });
      return;
    }
    if (attempt > MAX_STREAM_RETRIES) {
      abandonStream(forSession);
      return;
    }
    scheduleStreamProbe(forSession, turnId, attachMode, attempt + 1);
  }

  function clearStreamRetry(forSession: string) {
    const timer = retryTimersRef.current.get(forSession);
    if (timer === undefined) return;
    window.clearTimeout(timer);
    retryTimersRef.current.delete(forSession);
  }

  function scheduleStreamRetry(
    forSession: string,
    attempt: number,
    run: () => void,
  ) {
    updateLive(forSession, (current) => ({
      ...current,
      notice: RECONNECT_NOTICE,
    }));
    clearStreamRetry(forSession);
    const delay = Math.min(500 * 2 ** Math.min(attempt, 4), 5000);
    const timer = window.setTimeout(() => {
      if (retryTimersRef.current.get(forSession) === timer) {
        retryTimersRef.current.delete(forSession);
      }
      if (streamsRef.current.has(forSession)) return;
      run();
    }, delay);
    retryTimersRef.current.set(forSession, timer);
  }

  function scheduleStreamAttach(forSession: string, attempt: number) {
    scheduleStreamRetry(forSession, attempt, () => openStream(forSession));
  }

  function scheduleStreamReconnect(
    forSession: string,
    turnId: string,
    attempt: number,
  ) {
    scheduleStreamRetry(forSession, attempt, () => {
      const after = (lastEventIndexRef.current[forSession] ?? -1) + 1;
      openStream(forSession, turnId, after);
    });
  }

  function scheduleStreamProbe(
    forSession: string,
    turnId: string | null,
    attachMode: boolean,
    attempt: number,
  ) {
    scheduleStreamRetry(forSession, attempt, () => {
      void reconnectStream(forSession, turnId, attachMode, attempt);
    });
  }

  function abandonStream(forSession: string) {
    reconnectAttemptsRef.current[forSession] = 0;
    updateLive(forSession, (current) => ({
      ...current,
      tracking: false,
      notice: RECONNECT_LOST_NOTICE,
    }));
    void refreshTranscript(forSession);
  }

  /**
   * A queued turn may not be the registry's current turn the instant the
   * previous one ends, so attaching without a turn id can 409. Retry briefly
   * while keeping the lane marked as tracking.
   */
  function openStreamWithRetry(forSession: string, attempts = 10) {
    if (streamsRef.current.has(forSession)) return;
    closeStream(forSession);
    if (!sessionAgentRef.current[forSession]) {
      sessionAgentRef.current[forSession] = agentIdRef.current;
    }
    updateLive(forSession, (current) => ({ ...current, tracking: true }));
    let remaining = attempts - 1;

    const connect = () => {
      if (streamsRef.current.has(forSession)) return;
      const source = new EventSource(api.sessionStreamUrl(forSession));
      streamsRef.current.set(forSession, source);
      // Keep the lane marked as tracking across retry gaps.
      updateLive(forSession, (current) => ({ ...current, tracking: true }));
      let opened = false;
      // A listener (not `onopen`) so bindStreamSource's own open handler keeps
      // working alongside this one.
      source.addEventListener("open", () => {
        opened = true;
      });
      bindStreamSource(forSession, source);
      source.onerror = () => {
        if (streamsRef.current.get(forSession) !== source) return;
        closeStream(forSession);
        if (opened) {
          void refreshTranscript(forSession);
          return;
        }
        if (remaining <= 0) {
          updateLive(forSession, (current) => ({ ...current, tracking: false }));
          void refreshTranscript(forSession);
          return;
        }
        remaining -= 1;
        updateLive(forSession, (current) => ({ ...current, tracking: true }));
        const timer = window.setTimeout(() => {
          retryTimersRef.current.delete(forSession);
          connect();
        }, 500);
        retryTimersRef.current.set(forSession, timer);
      };
    };

    connect();
  }

  async function refreshTranscript(forSession: string, attempt = 0): Promise<void> {
    if (!forSession) return;
    // A manual reload supersedes any pending automatic transcript retry.
    clearTranscriptRetry(forSession);
    const requestId = (transcriptRequestRef.current[forSession] ?? 0) + 1;
    transcriptRequestRef.current[forSession] = requestId;
    setLoadingFor(forSession, true);
    let failure = "";
    let loaded: TranscriptMessage[] | null = null;
    try {
      const owner = sessionAgentRef.current[forSession] ?? agentIdRef.current;
      const list = await api.listSessionMessages(forSession, owner);
      if (transcriptRequestRef.current[forSession] !== requestId) return;
      const counts: Record<string, number> = {};
      for (const message of list) {
        if (message.role === "user") {
          counts[message.content] = (counts[message.content] ?? 0) + 1;
        }
      }
      transcriptUserCountsRef.current[forSession] = counts;
      settlePendingUsers(forSession, list);
      loaded = list;
      setMessagesFor(forSession, list);
      setLoadErrorFor(forSession, "");
      // Only a session owned by this agent (new, opened from its list, or
      // confirmed by that list) counts as loaded; an unknown deep link is not.
      const recordedOwner = sessionAgentRef.current[forSession];
      if (
        recordedOwner === agentIdRef.current ||
        sessionsRef.current.some((item) => item.id === forSession)
      ) {
        markSessionLoaded(forSession);
      }
    } catch (exc) {
      if (transcriptRequestRef.current[forSession] === requestId && attempt === 0) {
        // One quick automatic retry; a persistent failure exposes a manual
        // reload action instead of leaving the composer locked forever.
        const timer = window.setTimeout(() => {
          transcriptRetryRef.current.delete(forSession);
          void refreshTranscript(forSession, 1);
        }, 1000);
        transcriptRetryRef.current.set(forSession, timer);
      } else {
        failure = describeApiError(exc);
        setLoadErrorFor(forSession, failure);
      }
    } finally {
      if (transcriptRequestRef.current[forSession] !== requestId) return;
      setLoadingFor(forSession, false);
      const streaming = streamsRef.current.has(forSession);
      const followUp = followUpRef.current.has(forSession);
      updateLive(forSession, (current) => ({
        ...current,
        // A live stream owns these fields; reopening a busy session must not
        // wipe text the stream has already accumulated.
        streamText: streaming ? current.streamText : "",
        streamReasoning: streaming ? current.streamReasoning : "",
        activity: streaming ? current.activity : [],
        subagents: streaming ? current.subagents : {},
        tasks: streaming ? current.tasks : {},
        artifacts: streaming ? current.artifacts : [],
        notice: failure || current.notice,
        // Keep the lane marked while a queued turn is about to be attached.
        tracking: streaming || followUp,
        frozen:
          current.frozen && loaded && frozenReplyPersisted(loaded, current.frozen)
            ? null
            : current.frozen,
      }));
    }
  }

  async function finishTurn(
    forSession: string,
    status: "completed" | "failed" | "cancelled" | null,
    endedTurnId: string | null,
  ) {
    const pending = pendingUsersRef.current[forSession] ?? [];
    const hasQueuedTurn =
      (queuedTurnsRef.current[sessionQueueKey(forSession)] ?? []).length > 0;
    if (pending.length > 0 || hasQueuedTurn) followUpRef.current.add(forSession);
    try {
      await refreshTranscript(forSession);
      await refreshSessions();
    } finally {
      followUpRef.current.delete(forSession);
    }
    if ((status === "failed" || status === "cancelled") && endedTurnId) {
      // The turn never wrote a transcript row, so its optimistic question is
      // a ghost; the error itself stays in the notice.
      dropPendingForTurn(forSession, endedTurnId);
      // Known edge: a no-turn-id fallback attach cannot attribute its pending
      // rows here; they are reconciled by a later transcript refresh.
    }
    if (status === "cancelled") {
      // Interrupt drains queued turns too, so their ids will never run.
      const key = sessionQueueKey(forSession);
      for (const queuedId of queuedTurnsRef.current[key] ?? []) {
        dropPendingForTurn(forSession, queuedId);
      }
      clearQueuedTurns(forSession);
    }
    const nextTurnId = shiftQueuedTurn(forSession);
    if (nextTurnId) {
      markPendingTurnActive(forSession, nextTurnId);
      openStream(forSession, nextTurnId);
      return;
    }
    const stillQueued = (pendingUsersRef.current[forSession] ?? []).some(
      (item) => item.queued,
    );
    if (stillQueued && status !== "failed" && status !== "cancelled") {
      // A queued send whose POST response (and turn id) has not landed yet.
      openStreamWithRetry(forSession);
      return;
    }
    if (!streamsRef.current.has(forSession)) {
      // A poll/attach may have opened the next stream while this function was
      // awaiting; never mark that live lane as not tracking.
      updateLive(forSession, (current) => ({ ...current, tracking: false }));
    }
    if (
      (status === "completed" || status === "failed") &&
      shouldMarkUnread(forSession)
    ) {
      markUnread(forSession, status === "completed" ? "done" : "failed");
    }
    if (forSession === sessionIdRef.current) attachIfBusy(forSession);
  }

  useEffect(() => {
    if (!sessionId) return;
    if (document.visibilityState === "visible" && document.hasFocus()) {
      clearUnread(sessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    const clearActive = () => {
      const id = sessionIdRef.current;
      if (id) clearUnread(id);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") clearActive();
    };
    window.addEventListener("focus", clearActive);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", clearActive);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const count = sessions.reduce(
      (total, session) => total + (unreadBySession[session.id] ? 1 : 0),
      0,
    );
    document.title =
      count > 0 ? `(${count}) Agent Command Center` : "Agent Command Center";
  }, [sessions, unreadBySession]);

  useEffect(() => {
    const key = sessionKey(agentId, sessionId);
    draftKeyRef.current = key;
    setDraft(draftsRef.current[key] ?? "");
  }, [agentId, sessionId]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sidebarOpen]);

  useEffect(() => {
    const isEditable = (el: Element | null): boolean =>
      el instanceof HTMLElement &&
      (el.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
    // iOS 26 can leave the page scrolled and the visual viewport stuck at the
    // keyboard size after dismissal. Scrolling back forces the browser to
    // settle the viewport again.
    const resetViewport = () => {
      window.scrollTo(0, 0);
      window.setTimeout(() => window.scrollTo(0, 0), 120);
      window.setTimeout(() => window.scrollTo(0, 0), 400);
    };
    // 点输入框以外的地方收起键盘并复位视口。
    const onPointerDown = (event: PointerEvent) => {
      const active = document.activeElement;
      if (!(active instanceof HTMLElement) || !isEditable(active)) return;
      const composer = active.closest(".composer");
      if (!composer) return;
      const target = event.target as Node | null;
      if (target && composer.contains(target)) return;
      active.blur();
      resetViewport();
    };
    const onFocusOut = () => {
      window.setTimeout(() => {
        if (!isEditable(document.activeElement)) resetViewport();
      }, 0);
    };
    const onVisibilityChange = () => {
      if (
        document.visibilityState === "visible" &&
        !isEditable(document.activeElement)
      ) {
        resetViewport();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("focusout", onFocusOut);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("focusout", onFocusOut);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  function pushPath(path: string) {
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path);
    }
  }

  function navigate(next: View, nextSessionId: string = sessionIdRef.current) {
    setView(next);
    setSessionId(nextSessionId);
    setSidebarOpen(false);
    pushPath(pathForView(next, nextSessionId));
  }

  function openView(next: View) {
    navigate(next);
  }

  /** Switch agents without ever pairing an old session id with the new agent. */
  function changeAgent(next: string) {
    if (!next || next === agentId) return;
    sessionIdRef.current = "";
    setSessionId("");
    setFocus(null);
    setCommandReply(null);
    if (view === "chat") {
      const path = pathForView("chat", "");
      if (window.location.pathname !== path) {
        window.history.replaceState(null, "", path);
      }
    }
    setAgentId(next);
  }

  // Keep the address bar in step with derived state (session auto-selection,
  // deep-link fallback) without stacking a history entry for every change.
  useEffect(() => {
    if (!auth) return;
    const path = pathForView(view, sessionId);
    if (window.location.pathname !== path) {
      window.history.replaceState(null, "", path);
    }
  }, [auth, view, sessionId]);

  useEffect(() => {
    const onPopState = () => {
      const route = parsePath(window.location.pathname);
      if (!route) {
        window.history.replaceState(null, "", DEFAULT_PATH);
        setView("chat");
        setSessionId("");
        return;
      }
      setSidebarOpen(false);
      if (route.view === "chat") {
        setFocus(null);
        setCommandReply(null);
        setSessionId(route.sessionId);
      }
      setView(route.view);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function reloadSession(id: string) {
    if (!id) return;
    void refreshTranscript(id).then(() => {
      if (sessionIdRef.current === id) attachIfBusy(id);
    });
  }

  function refreshClaudeCommands(forAgent: string) {
    void api
      .getSlashCommands(forAgent)
      .then((result) => {
        if (agentIdRef.current === forAgent) setClaudeCommands(result.commands);
      })
      .catch(() => {
        // The palette keeps the platform commands; commands fill in after a turn.
      });
  }

  function openSession(id: string) {
    clearUnread(id);
    setLiveBySession((current) => {
      const session = current[id];
      if (!session?.frozen) return current;
      return { ...current, [id]: { ...session, frozen: null } };
    });
    setFocus(null);
    setCommandReply(null);
    if (!id) {
      navigate("chat", "");
      void refreshSessions();
      return;
    }
    // The cron page can open a session the sidebar has not listed yet.
    sessionAgentRef.current[id] = agentId;
    if (id === sessionIdRef.current && view === "chat") {
      // Clicking the current session is the retry affordance for a transcript
      // that failed to load (setSessionId would be a no-op).
      reloadSession(id);
    } else {
      navigate("chat", id);
    }
    void refreshSessions();
  }

  useEffect(() => {
    void bootstrap().catch((exc) => {
      setError(describeApiError(exc));
      setAuth(false);
    });
    return () => closeAllStreams();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    agentIdRef.current = agentId;
    storeAgent(agentId);
    // Drop the previous agent's session before its view can appear under the
    // new agent. Live turns of other sessions keep streaming in the background.
    sessionIdRef.current = "";
    setSessionId("");
    setSessions([]);
    sessionsRef.current = [];
    setMessagesBySession({});
    setLoadingBySession({});
    setError("");
    setCommandReply(null);
    setFocus(null);
    pendingSessionsRef.current = {};
    setPendingSessions({});
    if (!auth || !agentId) {
      sessionsRequestRef.current += 1;
      setClaudeCommands([]);
      return;
    }
    setClaudeCommands([]);
    void loadSessions(agentId).catch((exc) => setAgentError(describeApiError(exc), agentId));
    refreshClaudeCommands(agentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, agentId]);

  useEffect(() => {
    sessionIdRef.current = sessionId;
    setError("");
    if (!agentId || !sessionId) return;
    if (
      !sessionAgentRef.current[sessionId] &&
      sessionsRef.current.some((item) => item.id === sessionId)
    ) {
      // Only record ownership once the agent's session list confirms it; a
      // deep link can name a session of another agent.
      sessionAgentRef.current[sessionId] = agentId;
    }
    const owner = sessionAgentRef.current[sessionId];
    if (owner && owner !== agentId) return;
    if (skipRefreshRef.current.delete(sessionId)) {
      // A brand-new turn already streams; keep its optimistic user message
      // rather than replacing the lane with a possibly empty transcript.
      setLoadingFor(sessionId, false);
      return;
    }
    void refreshTranscript(sessionId).then(() => {
      if (sessionIdRef.current === sessionId) attachIfBusy(sessionId);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, agentId]);

  async function bootstrap() {
    const me = await api.me();
    if (!me.authenticated) {
      setAuth(false);
      return;
    }
    setAuth(true);
    await loadWorkspace();
    await loadSkills().catch(() => undefined);
  }

  async function loadSkills() {
    try {
      const catalog = await api.listSkills();
      setSkills(catalog.skills);
      setSkillsErrors(catalog.errors);
    } catch (exc) {
      setSkillsErrors([describeApiError(exc)]);
      throw exc;
    }
  }

  function refreshSkills() {
    return Promise.all([
      loadSkills().catch(() => undefined),
      loadWorkspace().catch(() => undefined),
    ]).then(() => undefined);
  }

  async function loadWorkspace() {
    const [agentList, providerList] = await Promise.all([api.listAgents(), api.listProviders()]);
    setAgents(agentList);
    setProviders(providerList);
    if (agentList.length === 0) {
      setNewAgentOpen(true);
      return;
    }
    const storedAgent = readStoredAgent();
    setAgentId((current) => {
      if (current && agentList.some((item) => item.id === current)) return current;
      if (storedAgent && agentList.some((item) => item.id === storedAgent)) return storedAgent;
      return agentList[0].id;
    });
  }

  function registerSessionOwner(list: SessionSummary[], forAgent: string) {
    const current = sessionIdRef.current;
    if (current && list.some((item) => item.id === current)) {
      sessionAgentRef.current[current] = forAgent;
    }
  }

  async function loadSessions(forAgent: string) {
    const requestId = ++sessionsRequestRef.current;
    const list = await api.listSessions(forAgent);
    if (requestId !== sessionsRequestRef.current || forAgent !== agentIdRef.current) return;
    registerSessionOwner(list, forAgent);
    applySessions(mergeLiveSession(list));
    if (list.length === 0) {
      restoreSessionRef.current = "";
      sessionIdRef.current = "";
      setSessionId("");
      return;
    }
    if (list.some((item) => item.id === sessionIdRef.current)) {
      restoreSessionRef.current = "";
      return;
    }
    const restore = restoreSessionRef.current;
    restoreSessionRef.current = "";
    const target = restore && list.some((item) => item.id === restore) ? restore : list[0].id;
    sessionAgentRef.current[target] = forAgent;
    sessionIdRef.current = target;
    setSessionId(target);
  }

  async function refreshSessions() {
    const forAgent = agentIdRef.current;
    if (!forAgent) return;
    try {
      const list = await api.listSessions(forAgent);
      if (forAgent !== agentIdRef.current) return;
      registerSessionOwner(list, forAgent);
      applySessions(mergeLiveSession(list));
    } catch {
      // Keep the current list on a transient refresh failure.
    }
  }

  useEffect(() => {
    if (!auth || !agentId) return;
    let cancelled = false;
    let inFlight = false;

    const tick = async () => {
      if (cancelled || inFlight || document.visibilityState === "hidden") return;
      const forAgent = agentIdRef.current;
      if (!forAgent) return;
      inFlight = true;
      try {
        const list = await api.listSessions(forAgent);
        if (cancelled || forAgent !== agentIdRef.current) return;
        const current = sessionIdRef.current;
        const wasBusy = sessionsRef.current.find((item) => item.id === current)?.busy ?? false;
        registerSessionOwner(list, forAgent);
        applySessions(mergeLiveSession(list));
        if (!current) return;
        const row = list.find((item) => item.id === current);
        if (row?.busy) {
          attachIfBusy(current);
        } else if (wasBusy && !streamsRef.current.has(current)) {
          // The turn finished while this tab was not attached; sync the lane.
          void refreshTranscript(current);
        }
      } catch {
        // A transient failure keeps the existing sidebar and live view.
      } finally {
        inFlight = false;
      }
    };

    const timer = window.setInterval(() => void tick(), SIDEBAR_POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, agentId]);

  // Warm the lazy page chunks after the chat is interactive so switching
  // pages does not wait on a download.
  useEffect(() => {
    if (!auth) return;
    const connection = (
      navigator as Navigator & { connection?: { saveData?: boolean } }
    ).connection;
    if (connection?.saveData) return;
    const prefetch = () => {
      for (const load of Object.values(PAGE_LOADERS)) {
        void load().catch(() => undefined);
      }
    };
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(prefetch, { timeout: 3000 });
      return () => window.cancelIdleCallback(id);
    }
    const timer = window.setTimeout(prefetch, 2000);
    return () => window.clearTimeout(timer);
  }, [auth]);

  async function runCommand(
    command: string,
    sendKey: string,
  ): Promise<CommandOutcome> {
    if (commandPendingRef.current) return { outcome: "error" };
    const targetAgentId = agentId;
    const targetSessionId = sessionIdRef.current;
    commandPendingRef.current = true;
    setError("");
    clearLiveNotice(targetSessionId);
    setCommandReply(null);
    setDraftFor(sendKey, "");
    try {
      const response = await api.runChatCommand({
        agent_id: targetAgentId,
        session_id: targetSessionId || null,
        command,
      });
      if (targetAgentId !== agentIdRef.current) return { outcome: "error" };
      if (response.forward) {
        return { outcome: "forward", command: response.command };
      }
      const startsNew = response.command === "/new" || response.command === "/clear";
      if (startsNew) newSession();
      setCommandReply({
        key: sessionKey(targetAgentId, startsNew ? "" : targetSessionId),
        command: response.command,
        text: response.reply,
      });
      if (response.command === "/stop") {
        // The runtime drops queued turns; their optimistic user rows and ids
        // must go too.
        if (targetSessionId) {
          dropPendingUsers(targetSessionId);
          clearQueuedTurns(targetSessionId);
        }
        void refreshSessions();
      }
      return { outcome: "handled" };
    } catch (exc) {
      setSessionError(describeApiError(exc), targetSessionId);
      setDraftFor(sendKey, command);
      return { outcome: "error" };
    } finally {
      commandPendingRef.current = false;
    }
  }

  async function send(attachments: Attachment[] = []): Promise<boolean> {
    let text = draft.trim();
    const sendAgentId = agentIdRef.current || agentId;
    const sendKey = sessionKey(sendAgentId, sessionIdRef.current);
    const quoteText = (quotesByKey[sendKey] ?? "").trim();
    if ((!text && attachments.length === 0 && !quoteText) || !sendAgentId) {
      return false;
    }
    if (isBareSlashCommand(text)) {
      const result = await runCommand(text, sendKey);
      if (result.outcome !== "forward") return result.outcome === "handled";
      // Use Claude Code's canonical command spelling for the turn.
      text = result.command;
    }
    const wasNew = !sessionIdRef.current;
    if (!wasNew && !loadedSessionsRef.current.has(sessionIdRef.current)) {
      // A send before the first transcript load would baseline at 0 and let a
      // same-text history row swallow the new pending message.
      setError("会话加载中，请稍候");
      return false;
    }
    if (pendingSessionsRef.current[sendKey]) return false;
    setCommandReply(null);
    const attachmentBlock = attachments
      .map((file) => `- ${file.name}（工作区路径：${file.path}，大小：${file.size} 字节）`)
      .join("\n");
    const quoteBlock = quoteText
      ? quoteText.split("\n").map((line) => `> ${line}`).join("\n")
      : "";
    // The attachment header must stay first so the UI can parse files on reload.
    const body = [quoteBlock, text].filter(Boolean).join("\n\n");
    const content = attachmentBlock
      ? `附件已上传到工作区：\n${attachmentBlock}${body ? `\n\n${body}` : ""}`
      : body;
    const targetSessionId = sessionIdRef.current || newSessionId();
    const newKey = sessionKey(sendAgentId, targetSessionId);
    const alreadyStreaming = streamsRef.current.has(targetSessionId);
    sessionAgentRef.current[targetSessionId] = sendAgentId;
    setError("");
    clearLiveNotice(targetSessionId);
    markPending(sendKey);
    if (wasNew) markPending(newKey);
    setDraftFor(sendKey, "");
    const optimisticId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    pushPendingUser(targetSessionId, {
      id: optimisticId,
      content,
      turnId: null,
      queued: alreadyStreaming,
    });
    if (wasNew) {
      // Show the new session, its URL and its optimistic question immediately;
      // the POST only fills in the turn id.
      skipRefreshRef.current.add(targetSessionId);
      markSessionLoaded(targetSessionId);
      sessionIdRef.current = targetSessionId;
      setSessionId(targetSessionId);
      applySessions([
        {
          id: targetSessionId,
          title: "新会话",
          last_modified: Date.now(),
          channel_type: null,
          channel_name: null,
          busy: true,
        },
        ...sessionsRef.current.filter((item) => item.id !== targetSessionId),
      ]);
    }
    if (!alreadyStreaming) {
      updateLive(targetSessionId, () => ({ ...EMPTY_LIVE, tracking: true }));
    }
    try {
      const response = await api.sendMessage({
        agent_id: sendAgentId,
        session_id: targetSessionId,
        content,
      });
      bindPendingTurn(targetSessionId, optimisticId, response.turn_id);
      clearAttachmentsFor(sendKey);
      clearQuoteFor(sendKey);
      const stillCurrentAgent = sendAgentId === agentIdRef.current;
      if (!stillCurrentAgent) {
        // The user switched agents while this send was in flight. The turn
        // belongs to the original agent; never surface it in the new view.
        skipRefreshRef.current.delete(targetSessionId);
        if (streamsRef.current.has(targetSessionId)) {
          enqueueTurn(targetSessionId, response.turn_id);
        }
        return true;
      }
      if (streamsRef.current.has(targetSessionId)) {
        // Another turn is still streaming in this lane; its turn_end will
        // subscribe to this explicit turn id, so keep the current stream.
        setPendingQueued(targetSessionId, optimisticId, true);
        enqueueTurn(targetSessionId, response.turn_id);
        const waiting = (pendingUsersRef.current[targetSessionId] ?? []).filter(
          (item) => item.queued,
        ).length;
        updateLive(targetSessionId, (current) => ({
          ...current,
          notice: queuedNotice(waiting),
        }));
      } else {
        markPendingTurnActive(targetSessionId, response.turn_id);
        openStream(targetSessionId, response.turn_id);
      }
      void refreshSessions();
      return true;
    } catch (exc) {
      removePendingUser(targetSessionId, optimisticId);
      setDraftFor(sendKey, text);
      skipRefreshRef.current.delete(targetSessionId);
      if (!alreadyStreaming) {
        updateLive(targetSessionId, (current) => ({ ...current, tracking: false }));
      }
      if (wasNew && sessionIdRef.current === targetSessionId) {
        // Roll back only if this new session is still the one on screen.
        sessionIdRef.current = "";
        setSessionId("");
        delete sessionAgentRef.current[targetSessionId];
        forgetPendingUsers(targetSessionId);
        setLiveBySession((current) => {
          if (!current[targetSessionId]) return current;
          const next = { ...current };
          delete next[targetSessionId];
          return next;
        });
        applySessions(
          sessionsRef.current.filter((item) => item.id !== targetSessionId),
        );
      }
      setAgentError(describeApiError(exc), sendAgentId);
      return false;
    } finally {
      clearPending(sendKey);
      if (wasNew) clearPending(newKey);
    }
  }

  async function cancel() {
    const id = sessionIdRef.current;
    if (!id) return;
    try {
      const result = await api.interruptSession(id);
      if (result.interrupted) {
        updateLive(id, (current) => ({
          ...current,
          tracking: false,
          notice: "已请求停止当前任务。",
          frozen: {
            streamText: current.streamText,
            streamReasoning: current.streamReasoning,
            activity: current.activity,
            subagents: current.subagents,
            tasks: current.tasks,
            artifacts: current.artifacts,
          },
          streamText: "",
          streamReasoning: "",
          activity: [],
          subagents: {},
          tasks: {},
          artifacts: [],
        }));
      }
      closeStream(id);
      delete activeTurnRef.current[id];
      if (result.interrupted) {
        // Drop only the questions whose turns the server really cancelled.
        // A turn that raced in after the queue drain keeps running; its
        // message must stay visible, so the lane keeps following it.
        const cancelled = new Set(result.cancelled_turn_ids ?? []);
        for (const item of pendingUsersRef.current[id] ?? []) {
          if (item.turnId && cancelled.has(item.turnId)) {
            dropPendingForTurn(id, item.turnId);
          }
        }
        const key = sessionQueueKey(id);
        const surviving = (queuedTurnsRef.current[key] ?? []).filter(
          (turnId) => !cancelled.has(turnId),
        );
        if (surviving.length > 0) {
          queuedTurnsRef.current = { ...queuedTurnsRef.current, [key]: surviving };
        } else {
          clearQueuedTurns(id);
        }
      }
      void refreshSessions();
      // The idle path reconciles the frozen partial against history; when a
      // queued turn survives, its finishTurn refresh does that instead.
      // Let the server publish terminal events and flush the transcript
      // before the lane is rebuilt from history.
      const timer = window.setTimeout(() => {
        deferredRefreshRef.current.delete(id);
        if (
          sessionIdRef.current === id &&
          sessionsRef.current.some((item) => item.id === id)
        ) {
          void refreshTranscript(id);
        }
      }, 600);
      deferredRefreshRef.current.set(id, timer);
      // A surviving queued turn keeps the lane: subscribe to it and let its
      // turn_end hand off to anything queued after it.
      const nextTurnId = shiftQueuedTurn(id);
      if (nextTurnId) {
        markPendingTurnActive(id, nextTurnId);
        updateLive(id, (current) => ({
          ...EMPTY_LIVE,
          tracking: true,
          notice: "已请求停止当前任务。",
          frozen: current.frozen,
        }));
        openStream(id, nextTurnId);
        return;
      }
      updateLive(id, (current) => ({
        ...current,
        tracking: false,
        notice: result.interrupted ? "已请求停止当前任务。" : "当前没有正在执行的任务。",
      }));
    } catch (exc) {
      setSessionError(describeApiError(exc), id);
    }
  }

  function quote(text: string) {
    const value = text.trim();
    if (!value) return;
    const key = draftKeyRef.current;
    setQuotesByKey((current) => ({ ...current, [key]: value }));
    setFocusSignal((current) => current + 1);
  }

  function clearQuoteFor(key: string) {
    setQuotesByKey((current) => {
      if (!(key in current)) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function deleteAgentPermanently(id: string) {
    const result = await api.deleteAgentPermanently(id);
    for (const key of Object.keys(draftsRef.current)) {
      if (key.startsWith(`${id}:`)) delete draftsRef.current[key];
    }
    for (const [session, owner] of Object.entries(sessionAgentRef.current)) {
      if (owner !== id) continue;
      closeStream(session);
      forgetPendingUsers(session);
      setLoadErrorFor(session, "");
      delete sessionAgentRef.current[session];
      clearUnread(session);
    }
    setAttachmentsByKey((current) => {
      const next = { ...current };
      for (const key of Object.keys(next)) {
        if (key.startsWith(`${id}:`)) delete next[key];
      }
      return next;
    });
    await loadWorkspace();
    if (id === agentId) {
      closeAllStreams();
      setLiveBySession({});
      pendingSessionsRef.current = {};
      setPendingSessions({});
      setSessionId("");
      setMessagesBySession({});
      setLoadingBySession({});
    }
    return result;
  }

  function newSession() {
    if (!agentId) return;
    setView("chat");
    setSidebarOpen(false);
    setCommandReply(null);
    sessionIdRef.current = "";
    setSessionId("");
    setError("");
    pushPath(DEFAULT_PATH);
  }

  async function renameSession(sessionId: string, title: string) {
    const owner = sessionAgentRef.current[sessionId] ?? agentId;
    await api.renameSession(sessionId, owner, title);
    applySessions(
      sessionsRef.current.map((item) =>
        item.id === sessionId ? { ...item, title } : item,
      ),
    );
    setSessionToRename((current) => (current?.id === sessionId ? null : current));
    void refreshSessions();
  }

  async function deleteSession(id: string) {
    const forAgent = agentId;
    const key = sessionKey(forAgent, id);
    try {
      await api.deleteSession(id, forAgent);
      closeStream(id);
      delete draftsRef.current[key];
      delete sessionAgentRef.current[id];
      forgetPendingUsers(id);
      setLoadErrorFor(id, "");
      clearUnread(id);
      setAttachmentsByKey((current) => {
        if (!current[key]) return current;
        const next = { ...current };
        delete next[key];
        return next;
      });
      setLiveBySession((current) => {
        if (!current[id]) return current;
        const next = { ...current };
        delete next[id];
        return next;
      });
      setMessagesBySession((current) => {
        if (!current[id]) return current;
        const next = { ...current };
        delete next[id];
        return next;
      });
      setLoadingBySession((current) => {
        if (!(id in current)) return current;
        const next = { ...current };
        delete next[id];
        return next;
      });
      if (forAgent !== agentIdRef.current) return;
      const list = await api.listSessions(forAgent);
      if (forAgent !== agentIdRef.current) return;
      applySessions(list);
      if (id === sessionIdRef.current) {
        const target = list[0]?.id ?? "";
        sessionIdRef.current = target;
        setSessionId(target);
      }
    } catch (exc) {
      setAgentError(describeApiError(exc), forAgent);
    }
  }

  async function createAgent(payload: { name: string; instructions: string; provider_id: string }) {
    setCreatingAgent(true);
    try {
      const created = await api.createAgent(payload);
      await loadWorkspace();
      changeAgent(created.id);
      setNewAgentOpen(false);
    } finally {
      setCreatingAgent(false);
    }
  }

  async function saveAgent(payload: AgentSavePayload) {
    if (!agent) return;
    const updated = await api.updateAgent(agent.id, payload);
    setAgents((current) => current.map((item) => (item.id === updated.id ? updated : item)));
  }

  async function saveProvider(
    providerId: string,
    payload: { name: string; base_url: string; model: string; api_key?: string },
  ) {
    const updated = await api.updateProvider(providerId, payload);
    setProviders((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    await loadWorkspace();
  }

  async function createProvider(payload: {
    name: string;
    base_url: string;
    model: string;
    api_key?: string;
  }): Promise<Provider> {
    const created = await api.createProvider(payload);
    setProviders((current) => [...current, created]);
    await loadWorkspace();
    return created;
  }

  async function logout() {
    closeAllStreams();
    await api.logout().catch(() => undefined);
    setAuth(false);
    setAgents([]);
    setProviders([]);
    setSkills([]);
    setSkillsErrors([]);
    setSessions([]);
    sessionsRef.current = [];
    setMessagesBySession({});
    setLoadingBySession({});
    setLiveBySession({});
    pendingSessionsRef.current = {};
    setPendingSessions({});
    pendingUsersRef.current = {};
    consumedUsersRef.current = {};
    transcriptUserCountsRef.current = {};
    queuedTurnsRef.current = {};
    activeTurnRef.current = {};
    loadedSessionsRef.current.clear();
    setLoadedSessions({});
    setLoadErrorBySession({});
    setPendingUserBySession({});
    setUnreadBySession({});
    followUpRef.current.clear();
    draftsRef.current = {};
    sessionAgentRef.current = {};
    setAttachmentsByKey({});
    setAgentId("");
    setSessionId("");
    setDraft("");
    setCommandReply(null);
    setSidebarOpen(false);
    setView("chat");
  }

  if (auth === null) {
    return <div className="loading-screen">Loading Agent Command Center…</div>;
  }

  if (!auth) {
    return <Login onLogin={() => void bootstrap()} />;
  }

  return (
    <div
      className={["app-shell", "pearl", view === "chat" && drawerOpen ? "with-drawer" : ""]
        .filter(Boolean)
        .join(" ")}
    >
      <header className="mobile-bar">
        <button
          type="button"
          className="mobile-bar-button"
          onClick={() => setSidebarOpen(true)}
          aria-label="打开菜单"
          aria-expanded={sidebarOpen}
          aria-controls="app-sidebar"
          title="打开菜单"
        >
          <svg
            viewBox="0 0 24 24"
            width="18"
            height="18"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
        <div className="mobile-bar-title">
          <span className="mobile-bar-name">{viewTitle}</span>
          {view === "chat" && <span className="mobile-bar-sub">{sessionTitle}</span>}
        </div>
        {view === "chat" ? (
          <button
            type="button"
            className={`mobile-bar-button ${drawerOpen ? "active" : ""}`}
            onClick={() => setDrawerOpen((current) => !current)}
            aria-label={drawerOpen ? "收起详情" : "打开详情"}
            title={drawerOpen ? "收起详情" : "打开详情"}
          >
            <svg
              viewBox="0 0 24 24"
              width="18"
              height="18"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M15 4v16" />
            </svg>
          </button>
        ) : (
          <span className="mobile-bar-spacer" />
        )}
      </header>

      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        workspace={view === "backtest" || view === "journal" || view === "chan" || view === "allocation" ? "features" : "agent"}
        onWorkspaceChange={(workspace) =>
          openView(workspace === "features" ? "backtest" : "chat")
        }
        agents={agents}
        agentId={agentId}
        onAgentChange={changeAgent}
        sessions={sessions}
        unread={unreadBySession}
        sessionId={sessionId}
        onSelectSession={openSession}
        onNewSession={newSession}
        newSessionActive={view === "chat" && !sessionId}
        onDeleteSession={(id) => {
          setSessionToDelete(sessions.find((item) => item.id === id) ?? null);
        }}
        onRenameSession={(id) => {
          setSidebarOpen(false);
          setSessionToRename(sessions.find((item) => item.id === id) ?? null);
        }}
        onOpenSettings={() => openView("settings")}
        onOpenMemory={() => openView("memory")}
        onOpenSkills={() => openView("skills")}
        onOpenTools={() => openView("tools")}
        onOpenPlugins={() => openView("plugins")}
        onOpenChannels={() => openView("channels")}
        onOpenCron={() => openView("cron")}
        onOpenBacktest={() => openView("backtest")}
        onOpenJournal={() => openView("journal")}
        onOpenChan={() => openView("chan")}
        onOpenAllocation={() => openView("allocation")}
        settingsActive={view === "settings"}
        memoryActive={view === "memory"}
        skillsActive={view === "skills"}
        toolsActive={view === "tools"}
        pluginsActive={view === "plugins"}
        channelsActive={view === "channels"}
        cronActive={view === "cron"}
        backtestActive={view === "backtest"}
        journalActive={view === "journal"}
        chanActive={view === "chan"}
        allocationActive={view === "allocation"}
        onLogout={() => void logout()}
      />

      <Suspense fallback={<PageFallback />}>
      {view === "memory" ? (
        <MemoryPage
          agent={agent}
          agentId={agentId}
          onClose={() => openView("chat")}
        />
      ) : view === "skills" ? (
        <SkillsPage
          skills={skills}
          skillsErrors={skillsErrors}
          onRefresh={refreshSkills}
          onClose={() => openView("chat")}
        />
      ) : view === "tools" ? (
        <ToolsPage agentId={agentId} onClose={() => openView("chat")} />
      ) : view === "plugins" ? (
        <PluginsPage onClose={() => openView("chat")} />
      ) : view === "channels" ? (
        <ChannelsPage agent={agent} agentId={agentId} onClose={() => openView("chat")} />
      ) : view === "cron" ? (
        <CronPage
          agent={agent}
          agentId={agentId}
          onOpenSession={openSession}
          onClose={() => openView("chat")}
        />
      ) : view === "backtest" ? (
        <BacktestPage onClose={() => openView("chat")} />
      ) : view === "journal" ? (
        <TradeJournalPage onClose={() => openView("chat")} />
      ) : view === "chan" ? (
        <ChanPage />
      ) : view === "allocation" ? (
        <AllocationPage />
      ) : view === "settings" ? (
        <SettingsPage
          agents={agents}
          agentId={agentId}
          providers={providers}
          onSaveAgent={saveAgent}
          onSaveProvider={saveProvider}
          onCreateProvider={createProvider}
          onSelectAgent={changeAgent}
          onNewAgent={() => setNewAgentOpen(true)}
          onDeleteAgent={deleteAgentPermanently}
          onClose={() => openView("chat")}
        />
      ) : (
        <main className="chat-column">
          <header className="chat-topbar">
            <div className="chat-topbar-main">
              <span className="chat-topbar-title">{agent?.name ?? "Agent"}</span>
              <span className="chat-topbar-sub">{sessionTitle}</span>
            </div>
            <button
              type="button"
              className={`topbar-button ${drawerOpen ? "active" : ""}`}
              title={drawerOpen ? "收起详情" : "打开详情"}
              aria-label={drawerOpen ? "收起详情" : "打开详情"}
              aria-pressed={drawerOpen}
              onClick={() => setDrawerOpen((current) => !current)}
            >
              <svg
                viewBox="0 0 24 24"
                width="15"
                height="15"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <rect x="3" y="4" width="18" height="16" rx="2" />
                <path d="M15 4v16" />
              </svg>
              <span>{drawerOpen ? "收起详情" : "详情"}</span>
            </button>
          </header>
          {agents.length === 0 ? (
            <div className="chat-empty full">
              <div className="chat-empty-mark">ACC</div>
              <h2>还没有 Agent</h2>
              <p>创建一个 Agent，选择 Provider，然后开始对话。</p>
              <button className="primary" onClick={() => setNewAgentOpen(true)}>新建 Agent</button>
            </div>
          ) : (
            <ChatPanel
              key={sessionId}
              messages={messages}
              loading={messagesLoading}
              loadError={loadErrorBySession[sessionId] ?? ""}
              onReload={() => reloadSession(sessionId)}
              streamText={live.streamText}
              streamReasoning={live.streamReasoning}
              activity={live.activity}
              subagents={live.subagents}
              tasks={live.tasks}
              liveArtifacts={live.artifacts}
              frozen={live.frozen}
              sending={chatSending}
              tracking={tracking}
              pendingSend={pendingHere}
              error={error}
              notice={live.notice}
              commandReply={visibleCommandReply}
              claudeCommands={claudeCommands}
              draft={draft}
              quote={quotesByKey[activeKey] ?? ""}
              attachments={attachments}
              canSend={Boolean(agentId) && sessionLoaded}
              queuedMessages={queuedMessages}
              queuedInfo={queuedInfo}
              cancellingQueued={cancellingQueued}
              onCancelQueued={cancelQueuedMessage}
              agentId={agentId}
              agentName={agent?.name ?? "Agent"}
              channelType={currentSession?.channel_type ?? ""}
              channelName={currentSession?.channel_name ?? ""}
              focusSignal={focusSignal}
              focusMessageId={focus?.sessionId === sessionId ? focus.messageId : ""}
              focusMessageNonce={focus?.nonce ?? 0}
              onDraftChange={changeDraft}
              onClearQuote={() => clearQuoteFor(activeKey)}
              onAddAttachment={(file) => addAttachmentFor(activeKey, file)}
              onRemoveAttachment={(path) => removeAttachmentFor(activeKey, path)}
              onSend={(sent) => send(sent)}
              onCancel={() => void cancel()}
              onQuote={quote}
            />
          )}
        </main>
      )}
      </Suspense>

      {view === "chat" && drawerOpen && (
        <Drawer
          tab={drawerTab}
          onTab={setDrawerTab}
          activity={live.activity.map((item) => item.name)}
          reasoning={live.streamReasoning}
          agent={agent}
          providers={providers}
          onSaveAgent={saveAgent}
          onClose={() => setDrawerOpen(false)}
        />
      )}

      <ConfirmDialog
        open={sessionToDelete !== null}
        title="删除这个会话？"
        description={`「${sessionToDelete?.title || "新会话"}」的会话记录会一起删除，无法恢复。`}
        confirmLabel="删除"
        danger
        onClose={() => setSessionToDelete(null)}
        onConfirm={() => {
          const target = sessionToDelete;
          setSessionToDelete(null);
          if (target) void deleteSession(target.id);
        }}
      />

      <RenameSessionDialog
        key={sessionToRename?.id ?? "none"}
        open={sessionToRename !== null}
        title={sessionToRename?.title || "新会话"}
        onClose={() => setSessionToRename(null)}
        onRename={(title) =>
          sessionToRename
            ? renameSession(sessionToRename.id, title)
            : Promise.resolve()
        }
      />

      <NewAgentDialog
        open={newAgentOpen}
        providers={providers}
        busy={creatingAgent}
        onClose={() => setNewAgentOpen(false)}
        onCreate={createAgent}
      />

    </div>
  );
}
