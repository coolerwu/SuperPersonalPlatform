import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "./api";
import type {
  Agent,
  AgentSavePayload,
  Attachment,
  DrawerTab,
  Message,
  Provider,
  Run,
  RunEvent,
  ScreenshotInfo,
  Session,
  SessionSearchHit,
  SkillInfo,
} from "./types";
import {
  activityFromEvents,
  appendReasoningStep,
  attemptText,
  buildRunTrace,
  describeToolCall,
  toolTraceStep,
  type RunTraceStep,
} from "./runActivity";
import { MEMORY_COMMAND, isSlashCommand } from "./messageContent";
import { describeApiError } from "./errors";
import { DEFAULT_PATH, parsePath, pathForView, type View } from "./routes";
import { ChatPanel } from "./components/ChatPanel";
import { AllocationPage } from "./components/AllocationPage";
import { BacktestPage } from "./components/BacktestPage";
import { ChanPage } from "./components/ChanPage";
import { TradeJournalPage } from "./components/TradeJournalPage";
import { ChannelsPage } from "./components/ChannelsPage";
import { CronPage } from "./components/CronPage";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { Drawer } from "./components/Drawer";
import { Login } from "./components/Login";
import { MemoryPage } from "./components/MemoryPage";
import { NewAgentDialog } from "./components/NewAgentDialog";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";
import { SkillsPage } from "./components/SkillsPage";
import { ToolsPage } from "./components/ToolsPage";

function terminal(status: string): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

function active(status: string): boolean {
  return status === "queued" || status === "running";
}

function isActiveRun(run: Run | null | undefined): boolean {
  return Boolean(run && active(run.status));
}

const MESSAGE_PAGE_SIZE = 50;
const MAX_FOCUS_PAGES = 20;

function mergeMessages(current: Message[], incoming: Message[]): Message[] {
  if (incoming.length === 0) return current;
  const byId = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort(
    (a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
}

/** The session list is ordered by conversation activity, not run housekeeping. */
function sessionActivity(session: Session): string {
  return session.last_message_at || session.updated_at;
}

function sortSessionsByActivity(list: Session[]): Session[] {
  return [...list].sort(
    (a, b) => sessionActivity(b).localeCompare(sessionActivity(a)) || b.id.localeCompare(a.id),
  );
}

/** A slash-command reply shown once in the current chat lane, never persisted. */
type CommandReply = {
  key: string;
  command: string;
  text: string;
};

/** Live run state is kept per session so switching away never drops it. */
type SessionLive = {
  activeRun: Run | null;
  events: RunEvent[];
  streamText: string;
  streamReasoning: string;
  trace: RunTraceStep[];
  activity: string;
  notice: string;
  tracking: boolean;
};

const EMPTY_LIVE: SessionLive = {
  activeRun: null,
  events: [],
  streamText: "",
  streamReasoning: "",
  trace: [],
  activity: "",
  notice: "",
  tracking: false,
};

type RunSubscription = {
  runId: string;
  source: EventSource | null;
  timer: number | null;
};

const MEMORY_SKILL = "memory-organizer";
const POLL_INTERVAL_MS = 800;
const POLL_MAX_BACKOFF_MS = 5000;
const MAX_POLL_RETRIES = 120;
const RECONNECT_NOTICE = "服务连接中断，正在重试…";
const RESTART_NOTICE = "服务重启，Run 已重新排队";
const ATTACH_NOTICE = "这个会话已有 Run 在运行，已接入它的实时进度；刚才的消息没有发送。";
const RETRY_ATTACH_NOTICE = "这个会话已有 Run 在运行，已接入它的实时进度。";
const QUEUED_NOTICE = "已排队，前面的任务完成后会自动执行";
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

function transientPollError(error: unknown): boolean {
  if (error instanceof ApiError) return error.status >= 500;
  return true;
}

/** A 409 from chat/retry carries the Run the UI should attach to instead. */
function conflictRunId(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail as { detail?: { active_run_id?: unknown } } | undefined;
  const runId = detail?.detail?.active_run_id;
  return typeof runId === "string" && runId ? runId : null;
}

function ViewportDebug() {
  const [text, setText] = useState("");
  useEffect(() => {
    const root = document.documentElement;
    const ios =
      navigator.userAgent.match(/OS (\d+)_(\d+)/)?.slice(1).join(".") ?? "?";
    let last = "";
    const tick = () => {
      const viewport = window.visualViewport;
      const active = document.activeElement as HTMLElement | null;
      const next = [
        `ios ${ios} f${document.hasFocus() ? 1 : 0}`,
        `vv ${viewport ? Math.round(viewport.height) : "—"}/${viewport ? Math.round(viewport.offsetTop) : "—"} s${viewport ? viewport.scale.toFixed(2) : "—"}`,
        `in ${window.innerHeight} doc ${root.clientHeight} y ${Math.round(window.scrollY)}`,
        `var ${root.style.getPropertyValue("--app-height") || "—"}/${root.style.getPropertyValue("--app-offset-top") || "—"}`,
        `focus ${active?.tagName ?? "—"}`,
      ].join("  ");
      if (next !== last) {
        last = next;
        setText(next);
      }
    };
    tick();
    const timer = window.setInterval(tick, 500);
    return () => window.clearInterval(timer);
  }, []);
  return <div className="viewport-debug">{text}</div>;
}

export default function App() {
  const [initialRoute] = useState(() => parsePath(window.location.pathname));
  const [viewportDebug] = useState(() =>
    new URLSearchParams(window.location.search).has("viewport-debug"),
  );
  const [auth, setAuth] = useState<boolean | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillsErrors, setSkillsErrors] = useState<string[]>([]);
  const [agentId, setAgentId] = useState("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState(initialRoute?.sessionId ?? "");
  const [screenshots, setScreenshots] = useState<ScreenshotInfo[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesHasMore, setMessagesHasMore] = useState(false);
  const [messagesNextBefore, setMessagesNextBefore] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState("");
  const [draft, setDraft] = useState("");
  const [commandReply, setCommandReply] = useState<CommandReply | null>(null);
  const [attachmentsByKey, setAttachmentsByKey] = useState<Record<string, Attachment[]>>({});
  const [quotesByKey, setQuotesByKey] = useState<Record<string, string>>({});
  const [pendingSessions, setPendingSessions] = useState<Record<string, true>>({});
  const [liveBySession, setLiveBySession] = useState<Record<string, SessionLive>>({});
  const [runReasoning, setRunReasoning] = useState<Record<string, string>>({});
  const [runTraces, setRunTraces] = useState<Record<string, RunTraceStep[]>>({});
  const [error, setError] = useState("");
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("run");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [view, setView] = useState<View>(initialRoute?.view ?? "chat");
  const [focusSignal, setFocusSignal] = useState(0);
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<Session | null>(null);
  const [searchFocus, setSearchFocus] = useState<{
    sessionId: string;
    messageId: string;
    nonce: number;
  } | null>(null);

  const subscriptionsRef = useRef<Map<string, RunSubscription>>(new Map());
  const appliedEventsRef = useRef<Map<string, number>>(new Map());
  const finishingRunsRef = useRef<Set<string>>(new Set());
  const finishedRunsRef = useRef<Set<string>>(new Set());
  const suppressSessionResetRef = useRef<string | null>(null);
  const sessionIdRef = useRef("");
  const messagesRequestRef = useRef(0);
  const messagesSessionRef = useRef("");
  const olderRequestRef = useRef(0);
  const loadingOlderRef = useRef(false);
  const sessionsRequestRef = useRef(0);
  const agentIdRef = useRef("");
  const draftsRef = useRef<Record<string, string>>({});
  const draftKeyRef = useRef("");
  const pendingSessionsRef = useRef<Record<string, true>>({});
  const sessionCountsRef = useRef<Record<string, number>>({});
  const runSessionsRef = useRef<Record<string, string>>({});
  const commandPendingRef = useRef(false);
  // A /chat/<id> deep link is applied once the agent's sessions load.
  const restoreSessionRef = useRef(initialRoute?.sessionId ?? "");

  const activeKey = sessionKey(agentId, sessionId);
  const visibleCommandReply =
    commandReply && commandReply.key === activeKey ? commandReply : null;
  const attachments = attachmentsByKey[activeKey] ?? [];
  const currentSession = sessions.find((item) => item.id === sessionId) ?? null;
  const agent = agents.find((item) => item.id === agentId);
  const live = liveBySession[sessionId] ?? EMPTY_LIVE;
  const pendingHere = Boolean(pendingSessions[sessionId || NEW_SESSION_KEY]);
  const runActive = live.tracking && isActiveRun(live.activeRun);
  const chatSending = pendingHere || runActive;
  const visibleRun = live.activeRun;
  const visibleStream = live.streamText;
  const visibleReasoning = live.streamReasoning;
  const visibleTrace = live.trace;
  const visibleEvents = live.events;
  const visibleActivity = live.activity;
  const visibleNotice = live.notice;
  const terminalRun =
    visibleRun && (visibleRun.status === "failed" || visibleRun.status === "cancelled")
      ? { run: visibleRun, text: visibleStream, reasoning: visibleReasoning }
      : null;
  const sessionTitle = sessions.find((item) => item.id === sessionId)?.title || "新对话";
  const viewTitle =
    view === "settings"
      ? "配置"
      : view === "memory"
        ? "记忆"
        : view === "skills"
          ? "技能"
          : view === "tools"
            ? "工具"
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

  function rememberEvents(events: RunEvent[]) {
    for (const event of events) {
      const known = appliedEventsRef.current.get(event.run_id) ?? 0;
      if (event.id > known) appliedEventsRef.current.set(event.run_id, event.id);
    }
  }

  function acceptEvent(event: RunEvent): boolean {
    const known = appliedEventsRef.current.get(event.run_id) ?? 0;
    if (event.id <= known) return false;
    appliedEventsRef.current.set(event.run_id, event.id);
    return true;
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

  // A send/retry request is pending per session, so Runs in other sessions
  // never lock this session's composer.
  function markPending(session: string) {
    const key = session || NEW_SESSION_KEY;
    if (pendingSessionsRef.current[key]) return;
    pendingSessionsRef.current = { ...pendingSessionsRef.current, [key]: true };
    setPendingSessions(pendingSessionsRef.current);
  }

  function clearPending(session: string) {
    const key = session || NEW_SESSION_KEY;
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

  function rememberSessionCounts(list: Session[]) {
    const next = { ...sessionCountsRef.current };
    for (const session of list) next[session.id] = session.message_count;
    sessionCountsRef.current = next;
  }

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
    // 点输入框以外的地方收起键盘；外壳高度不再跟着键盘变化，由浏览器平移
    // 可视视口来露出 composer，键盘收起后自动恢复。
    const onPointerDown = (event: PointerEvent) => {
      const active = document.activeElement;
      if (!(active instanceof HTMLElement) || !isEditable(active)) return;
      const composer = active.closest(".composer");
      if (!composer) return;
      const target = event.target as Node | null;
      if (target && composer.contains(target)) return;
      active.blur();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
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
        setSearchFocus(null);
        setCommandReply(null);
        setSessionId(route.sessionId);
      }
      setView(route.view);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function openCronSession(id: string) {
    setSearchFocus(null);
    setCommandReply(null);
    navigate("chat", id);
    void refreshSessions();
  }

  function openSearchHit(hit: SessionSearchHit) {
    setCommandReply(null);
    setSearchFocus({
      sessionId: hit.session_id,
      messageId: hit.message_id,
      nonce: Date.now(),
    });
    setSessions((current) => {
      if (current.some((item) => item.id === hit.session_id)) return current;
      return [
        {
          id: hit.session_id,
          agent_id: agentId,
          title: hit.session_title || "新会话",
          origin: "chat",
          message_count: 0,
          active_run_id: null,
          active_run_status: null,
          channel_type: null,
          channel_name: null,
          created_at: hit.created_at,
          updated_at: hit.created_at,
          last_message_at: hit.created_at,
        },
        ...current,
      ];
    });
    navigate("chat", hit.session_id);
    if (hit.session_id === sessionIdRef.current) {
      void loadMessages(hit.session_id, false, hit.message_id).catch((exc) => {
        setSessionError(describeApiError(exc), hit.session_id);
      });
    }
  }

  useEffect(() => {
    void bootstrap().catch((exc) => {
      setError(describeApiError(exc));
      setAuth(false);
    });
    return () => closeAllSubscriptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    agentIdRef.current = agentId;
    storeAgent(agentId);
    // Drop the previous agent's session before its view can appear under the
    // new agent. Live runs of other sessions keep streaming in the background.
    sessionIdRef.current = "";
    setSessionId("");
    setMessages([]);
    setMessagesLoading(false);
    messagesSessionRef.current = "";
    olderRequestRef.current += 1;
    loadingOlderRef.current = false;
    setMessagesHasMore(false);
    setMessagesNextBefore(null);
    setLoadingOlder(false);
    setOlderError("");
    setSessions([]);
    setError("");
    setCommandReply(null);
    setSearchFocus(null);
    if (!auth || !agentId) {
      sessionsRequestRef.current += 1;
      return;
    }
    void loadSessions(agentId).catch((exc) => setAgentError(describeApiError(exc), agentId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, agentId]);

  useEffect(() => {
    sessionIdRef.current = sessionId;
    const keepMessages = suppressSessionResetRef.current === sessionId;
    suppressSessionResetRef.current = null;
    messagesSessionRef.current = "";
    olderRequestRef.current += 1;
    loadingOlderRef.current = false;
    setMessagesHasMore(false);
    setMessagesNextBefore(null);
    setLoadingOlder(false);
    setOlderError("");
    if (!keepMessages) {
      // Never show the previous session's messages under the new one while its
      // own messages are still loading.
      messagesRequestRef.current += 1;
      setMessages([]);
      setMessagesLoading(Boolean(sessionId));
    }
    setError("");
    setScreenshots([]);
    if (!sessionId) {
      setMessagesLoading(false);
      return;
    }
    const focusId =
      searchFocus?.sessionId === sessionId ? searchFocus.messageId : undefined;
    void loadMessages(sessionId, true, focusId).catch((exc) => {
      if (sessionId !== sessionIdRef.current) return;
      setMessagesLoading(false);
      setSessionError(describeApiError(exc), sessionId);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) {
      setScreenshots([]);
      return;
    }
    let mounted = true;
    void api
      .listScreenshots(sessionId)
      .then((list) => {
        if (mounted && sessionId === sessionIdRef.current) setScreenshots(list);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, [sessionId]);

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

  async function loadSessions(forAgent: string) {
    const requestId = ++sessionsRequestRef.current;
    const list = await api.listSessions(forAgent);
    if (requestId !== sessionsRequestRef.current || forAgent !== agentIdRef.current) return;
    rememberSessionCounts(list);
    setSessions(list);
    if (list.length === 0) {
      restoreSessionRef.current = "";
      sessionIdRef.current = "";
      setSessionId("");
      setMessages([]);
      setMessagesLoading(false);
      messagesSessionRef.current = "";
      olderRequestRef.current += 1;
      loadingOlderRef.current = false;
      setMessagesHasMore(false);
      setMessagesNextBefore(null);
      setLoadingOlder(false);
      setOlderError("");
      return;
    }
    if (!list.some((item) => item.id === sessionIdRef.current)) {
      const restore = restoreSessionRef.current;
      restoreSessionRef.current = "";
      const target =
        restore && list.some((item) => item.id === restore) ? restore : list[0].id;
      sessionIdRef.current = target;
      setSessionId(target);
    } else {
      restoreSessionRef.current = "";
    }
  }

  async function refreshSessions() {
    if (!agentId) return;
    const forAgent = agentId;
    try {
      const list = await api.listSessions(forAgent);
      if (forAgent !== agentIdRef.current) return;
      rememberSessionCounts(list);
      setSessions(list);
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
        const currentItem = list.find((item) => item.id === current);
        const previousCount = current ? sessionCountsRef.current[current] : undefined;
        rememberSessionCounts(list);
        setSessions(list);
        if (!current || !currentItem) return;
        if (subscriptionsRef.current.has(current) || pendingSessionsRef.current[current || NEW_SESSION_KEY]) return;
        if (currentItem.active_run_id) {
          await loadMessages(current, true);
          return;
        }
        if (previousCount !== undefined && currentItem.message_count > previousCount) {
          await loadMessages(current, true);
        }
      } catch {
        // A transient failure keeps the existing sidebar and live view.
      } finally {
        inFlight = false;
      }
    };

    const timer = window.setInterval(() => void tick(), 5000);
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

  async function refreshScreenshots() {
    const forSession = sessionIdRef.current;
    if (!forSession) return;
    try {
      const list = await api.listScreenshots(forSession);
      if (forSession === sessionIdRef.current) setScreenshots(list);
    } catch {
      // Keep the current list on a transient refresh failure.
    }
  }

  function closeSubscription(id: string) {
    const subscription = subscriptionsRef.current.get(id);
    if (!subscription) return;
    subscriptionsRef.current.delete(id);
    subscription.source?.close();
    if (subscription.timer !== null) window.clearTimeout(subscription.timer);
  }

  function closeAllSubscriptions() {
    for (const id of Array.from(subscriptionsRef.current.keys())) closeSubscription(id);
  }

  function subscribe(id: string, runId: string, after = 0) {
    closeSubscription(id);
    const subscription: RunSubscription = { runId, source: null, timer: null };
    subscriptionsRef.current.set(id, subscription);
    let lastId = after;
    const source = new EventSource(api.streamUrl(runId, after));
    subscription.source = source;
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as RunEvent;
        lastId = Math.max(lastId, event.id);
        applyEvent(id, event);
      } catch {
        // Ignore malformed frames.
      }
    };
    source.onerror = () => {
      if (subscriptionsRef.current.get(id) !== subscription) return;
      source.close();
      subscription.source = null;
      void pollRun(id, runId, lastId);
    };
  }

  function applyEvent(id: string, event: RunEvent) {
    if (!acceptEvent(event)) return;
    if (event.type === "reasoning_delta") {
      const delta = String(event.payload.delta ?? "");
      setRunReasoning((current) => ({
        ...current,
        [event.run_id]: (current[event.run_id] ?? "") + delta,
      }));
    }
    setLiveBySession((current) => {
      const session = current[id] ?? EMPTY_LIVE;
      const next: SessionLive = { ...session, events: [...session.events, event] };
      if (
        next.activeRun?.status === "queued" &&
        next.activeRun.id === event.run_id &&
        (event.type === "assistant_delta" ||
          event.type === "reasoning_delta" ||
          event.type === "tool_call")
      ) {
        // Queued Runs have no "started" event; the first output marks the
        // transition so the live bubble stops saying "排队中".
        next.activeRun = {
          ...next.activeRun,
          status: "running",
          started_at: next.activeRun.started_at ?? event.created_at,
        };
      }
      if (event.type === "run_queued") {
        if (event.payload.reason === "service_restart") {
          // The checkpoint keeps the interrupted attempt; the requeued run will
          // stream a fresh answer, so drop the previous attempt's live text.
          next.streamText = "";
          next.streamReasoning = "";
          next.trace = [];
          next.activity = "";
          next.notice = RESTART_NOTICE;
        } else {
          next.notice = "";
        }
      } else if (event.type === "assistant_delta") {
        next.streamText = session.streamText + String(event.payload.delta ?? "");
        next.activity = "";
        if (session.notice === RECONNECT_NOTICE) next.notice = "";
      } else if (event.type === "reasoning_delta") {
        const delta = String(event.payload.delta ?? "");
        next.streamReasoning = session.streamReasoning + delta;
        next.trace = appendReasoningStep(session.trace, event.id, delta);
        next.activity = "";
        if (session.notice === RECONNECT_NOTICE) next.notice = "";
      } else if (event.type === "tool_call") {
        next.trace = [...session.trace, toolTraceStep(event)];
        next.activity = describeToolCall(event.payload);
      } else if (event.type === "browser_action") {
        // The tool finished; the next model step decides the next activity.
        next.activity = "";
      }
      return { ...current, [id]: next };
    });
    if (event.type === "browser_action" && event.payload.screenshot_id && id === sessionIdRef.current) {
      void refreshScreenshots();
    }
    if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_cancelled") {
      void finishRun(id, event.run_id);
    }
  }

  async function pollRun(id: string, runId: string, after = 0) {
    closeSubscription(id);
    const subscription: RunSubscription = { runId, source: null, timer: null };
    subscriptionsRef.current.set(id, subscription);
    let lastId = after;
    let failures = 0;

    const alive = () => subscriptionsRef.current.get(id) === subscription;

    const schedule = (delay: number) => {
      if (!alive()) return;
      subscription.timer = window.setTimeout(() => {
        subscription.timer = null;
        void tick();
      }, delay);
    };

    const tick = async () => {
      if (!alive()) return;
      try {
        const newEvents = await api.getRunEvents(runId, lastId);
        if (!alive()) return;
        let sawRestart = false;
        for (const event of newEvents) {
          lastId = event.id;
          if (event.type === "run_queued" && event.payload.reason === "service_restart") {
            sawRestart = true;
          }
          applyEvent(id, event);
        }
        const run = await api.getRun(runId);
        if (!alive()) return;
        runSessionsRef.current[run.id] = run.session_id;
        updateLive(id, (session) => ({
          ...session,
          activeRun: run,
          tracking: !terminal(run.status),
        }));
        if (failures > 0) {
          failures = 0;
          if (!sawRestart) {
            updateLive(id, (session) =>
              session.notice === RECONNECT_NOTICE ? { ...session, notice: "" } : session,
            );
          }
        }
        if (terminal(run.status)) {
          closeSubscription(id);
          void finishRun(id, runId);
          return;
        }
        schedule(POLL_INTERVAL_MS);
      } catch (exc) {
        if (!alive()) return;
        if (transientPollError(exc) && failures < MAX_POLL_RETRIES) {
          failures += 1;
          updateLive(id, (session) => ({ ...session, notice: RECONNECT_NOTICE }));
          schedule(
            Math.min(
              POLL_INTERVAL_MS * 2 ** Math.min(failures, 3),
              POLL_MAX_BACKOFF_MS,
            ),
          );
          return;
        }
        closeSubscription(id);
        updateLive(id, (session) => ({ ...session, tracking: false, notice: "" }));
        setSessionError(describeApiError(exc), id);
      }
    };
    void tick();
  }

  async function finishRun(id: string, runId: string) {
    if (finishedRunsRef.current.has(runId) || finishingRunsRef.current.has(runId)) return;
    finishingRunsRef.current.add(runId);
    closeSubscription(id);
    try {
      const [run, runEvents] = await Promise.all([api.getRun(runId), api.getRunEvents(runId)]);
      rememberEvents(runEvents);
      runSessionsRef.current[run.id] = run.session_id;
      const reasoning = attemptText(runEvents, "reasoning_delta");
      const text = attemptText(runEvents, "assistant_delta");
      const trace = buildRunTrace(runEvents);
      if (run.session_id === sessionIdRef.current && run.status === "completed") {
        // Persist the final assistant message before dropping the stream text,
        // so the completed answer never blinks out of the transcript.
        await loadMessages(run.session_id, false).catch(() => undefined);
      }
      setRunReasoning((current) => ({ ...current, [run.id]: reasoning }));
      setRunTraces((current) => ({ ...current, [run.id]: trace }));
      // Keep the streamed text as a fallback until the persisted assistant
      // message is in the transcript (ChatPanel hides the live bubble then).
      // Background completed Runs do not need the fallback when reopening them.
      const keepText = run.status !== "completed" || run.session_id === sessionIdRef.current;
      updateLive(id, (session) => ({
        ...session,
        activeRun: run,
        events: runEvents,
        streamText: keepText ? text : "",
        streamReasoning: keepText ? reasoning : "",
        trace,
        activity: "",
        notice: "",
        tracking: false,
      }));
      // Channel sessions can queue several Runs; pick up the next one.
      const next = await api.getSessionActiveRun(id).catch(() => null);
      if (next && isActiveRun(next)) {
        const nextEvents = await api.getRunEvents(next.id).catch(() => [] as RunEvent[]);
        rememberEvents(nextEvents);
        runSessionsRef.current[next.id] = next.session_id;
        const nextTrace = buildRunTrace(nextEvents);
        setRunTraces((current) => ({ ...current, [next.id]: nextTrace }));
        updateLive(id, (session) => ({
          ...session,
          activeRun: next,
          events: nextEvents,
          streamText: attemptText(nextEvents, "assistant_delta"),
          streamReasoning: attemptText(nextEvents, "reasoning_delta"),
          trace: nextTrace,
          activity: activityFromEvents(nextEvents),
          notice: "",
          tracking: true,
        }));
        subscribe(id, next.id, nextEvents.at(-1)?.id ?? 0);
      }
      if (run.session_id === sessionIdRef.current) {
        void refreshScreenshots();
      }
      finishedRunsRef.current.add(runId);
      void refreshSessions();
    } catch (exc) {
      updateLive(id, (session) => ({ ...session, tracking: false, notice: "" }));
      setSessionError(describeApiError(exc), id);
      void refreshSessions();
    } finally {
      finishingRunsRef.current.delete(runId);
    }
  }

  async function fetchMessageWindow(id: string, focusId?: string) {
    const latest = await api.listMessagePage(id, { limit: MESSAGE_PAGE_SIZE });
    let items = latest.items;
    let hasMore = latest.has_more;
    let nextBefore = latest.next_before;
    if (focusId && !items.some((message) => message.id === focusId)) {
      let pages = 0;
      while (hasMore && nextBefore && pages < MAX_FOCUS_PAGES) {
        const older = await api.listMessagePage(id, {
          limit: MESSAGE_PAGE_SIZE,
          before: nextBefore,
        });
        items = [...older.items, ...items];
        hasMore = older.has_more;
        nextBefore = older.next_before;
        pages += 1;
        if (items.some((message) => message.id === focusId)) break;
      }
      if (!items.some((message) => message.id === focusId)) {
        items = await api.listMessages(id);
        hasMore = false;
        nextBefore = null;
      }
    }
    return { items, hasMore, nextBefore };
  }

  async function loadMessages(id: string, syncRun = true, focusId?: string) {
    const requestId = ++messagesRequestRef.current;
    const result = await fetchMessageWindow(id, focusId);
    if (requestId !== messagesRequestRef.current || id !== sessionIdRef.current) return;
    const isRefresh = !focusId && messagesSessionRef.current === id;
    setMessages((current) =>
      isRefresh ? mergeMessages(current, result.items) : result.items,
    );
    messagesSessionRef.current = id;
    if (!isRefresh) {
      setMessagesHasMore(result.hasMore);
      setMessagesNextBefore(result.nextBefore);
    }
    setMessagesLoading(false);
    setOlderError("");
    if (!syncRun) return;
    setError("");
    if (subscriptionsRef.current.has(id)) {
      // This session's Run is already streaming in the background; reusing its
      // live state avoids rewinding text to whatever the DB had at load time.
      return;
    }

    // Channel sessions can have several queued Runs; the active one is not
    // necessarily the newest message's Run.
    const activeRun = await api.getSessionActiveRun(id);
    if (requestId !== messagesRequestRef.current || id !== sessionIdRef.current) return;
    const lastRunId =
      activeRun?.id ?? [...result.items].reverse().find((message) => message.run_id)?.run_id;
    if (!lastRunId) {
      setLiveBySession((current) => {
        if (current[id]?.tracking) return current;
        return { ...current, [id]: { ...EMPTY_LIVE } };
      });
      return;
    }
    const [run, runEvents] = await Promise.all([api.getRun(lastRunId), api.getRunEvents(lastRunId)]);
    if (requestId !== messagesRequestRef.current || id !== sessionIdRef.current) return;
    runSessionsRef.current[run.id] = run.session_id;
    rememberEvents(runEvents);
    const reasoning = attemptText(runEvents, "reasoning_delta");
    const text = attemptText(runEvents, "assistant_delta");
    const trace = buildRunTrace(runEvents);
    const running = isActiveRun(run);
    const streamText = run.status === "completed" ? "" : text;
    const streamReasoning = run.status === "completed" ? "" : reasoning;
    setRunReasoning((current) => ({ ...current, [run.id]: reasoning }));
    setRunTraces((current) => ({ ...current, [run.id]: trace }));
    setLiveBySession((current) => {
      const existing = current[id];
      if (existing && existing.activeRun?.id === run.id) {
        const known = appliedEventsRef.current.get(run.id) ?? 0;
        if (known > (runEvents.at(-1)?.id ?? 0)) {
          return current;
        }
      }
      if (existing?.tracking && existing.activeRun && existing.activeRun.id !== run.id) {
        return current;
      }
      return {
        ...current,
        [id]: {
          activeRun: run,
          events: runEvents,
          streamText,
          streamReasoning,
          trace,
          activity: running ? activityFromEvents(runEvents) : "",
          notice: "",
          tracking: running,
        },
      };
    });
    if (running && !subscriptionsRef.current.has(id)) {
      subscribe(id, run.id, runEvents.at(-1)?.id ?? 0);
    }
  }

  async function loadOlder(): Promise<boolean> {
    const id = sessionIdRef.current;
    if (!id || !messagesHasMore || !messagesNextBefore || loadingOlderRef.current) {
      return false;
    }
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    setOlderError("");
    const requestId = ++olderRequestRef.current;
    try {
      const page = await api.listMessagePage(id, {
        limit: MESSAGE_PAGE_SIZE,
        before: messagesNextBefore,
      });
      if (requestId !== olderRequestRef.current || id !== sessionIdRef.current) return false;
      setMessages((current) => mergeMessages(current, page.items));
      messagesSessionRef.current = id;
      setMessagesHasMore(page.has_more);
      setMessagesNextBefore(page.next_before);
      return page.items.length > 0;
    } catch (exc) {
      if (requestId === olderRequestRef.current && id === sessionIdRef.current) {
        setOlderError(describeApiError(exc));
      }
      return false;
    } finally {
      if (requestId === olderRequestRef.current) {
        loadingOlderRef.current = false;
        setLoadingOlder(false);
      }
    }
  }

  async function attachRun(id: string, runId: string, notice: string) {
    if (subscriptionsRef.current.get(id)?.runId === runId) {
      clearPending(id);
      return;
    }
    try {
      const [run, runEvents] = await Promise.all([api.getRun(runId), api.getRunEvents(runId)]);
      if (run.session_id !== id) return;
      runSessionsRef.current[run.id] = run.session_id;
      rememberEvents(runEvents);
      const reasoning = attemptText(runEvents, "reasoning_delta");
      const text = attemptText(runEvents, "assistant_delta");
      const trace = buildRunTrace(runEvents);
      const running = isActiveRun(run);
      setRunReasoning((current) => ({ ...current, [run.id]: reasoning }));
      setRunTraces((current) => ({ ...current, [run.id]: trace }));
      updateLive(id, () => ({
        activeRun: run,
        events: runEvents,
        streamText: run.status === "completed" ? "" : text,
        streamReasoning: run.status === "completed" ? "" : reasoning,
        trace,
        activity: running ? activityFromEvents(runEvents) : "",
        notice,
        tracking: running,
      }));
      if (running && !subscriptionsRef.current.has(id)) {
        subscribe(id, run.id, runEvents.at(-1)?.id ?? 0);
      } else if (!running && run.session_id === sessionIdRef.current) {
        await loadMessages(id, false).catch(() => undefined);
      }
    } catch (exc) {
      setSessionError(describeApiError(exc), id);
    } finally {
      clearPending(id);
    }
  }

  async function runCommand(command: string, sendKey: string): Promise<boolean> {
    if (commandPendingRef.current) return false;
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
      if (targetAgentId !== agentIdRef.current) return false;
      const startsNew = response.command === "/new" || response.command === "/clear";
      if (startsNew) newSession();
      setCommandReply({
        key: sessionKey(targetAgentId, startsNew ? "" : targetSessionId),
        command: response.command,
        text: response.reply,
      });
      if (response.command === "/stop") void refreshSessions();
      return true;
    } catch (exc) {
      setSessionError(describeApiError(exc), targetSessionId);
      setDraftFor(sendKey, command);
      return false;
    } finally {
      commandPendingRef.current = false;
    }
  }

  async function send(
    attachments: Attachment[] = [],
    overrideText?: string,
    sessionTitle?: string,
  ): Promise<boolean> {
    const text = (overrideText ?? draft).trim();
    const sendAgentId = agentId;
    const sendKey = sessionKey(sendAgentId, sessionIdRef.current);
    const quoteText =
      overrideText === undefined ? (quotesByKey[sendKey] ?? "").trim() : "";
    if (
      (!text && attachments.length === 0 && !quoteText) ||
      !agentId ||
      pendingSessionsRef.current[sessionIdRef.current || NEW_SESSION_KEY]
    ) {
      return false;
    }
    if (overrideText === undefined && isSlashCommand(text)) {
      return runCommand(text, sendKey);
    }
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
    setError("");
    clearLiveNotice(sessionIdRef.current);
    let createdSessionId = "";
    let sent = false;
    let targetSessionId = sessionIdRef.current;
    markPending(targetSessionId);
    if (overrideText === undefined) setDraftFor(sendKey, "");
    const optimisticId = `local-${Date.now()}`;
    setMessages((current) => [
      ...current,
      {
        id: optimisticId,
        session_id: sessionIdRef.current,
        role: "user",
        content,
        run_id: null,
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      if (!targetSessionId) {
        const created = await api.createSession(sendAgentId, sessionTitle ?? "新会话");
        createdSessionId = created.id;
        clearPending(targetSessionId);
        targetSessionId = created.id;
        markPending(targetSessionId);
        if (sendAgentId === agentIdRef.current) {
          sessionIdRef.current = created.id;
          suppressSessionResetRef.current = created.id;
          setSessions((current) => [created, ...current]);
          setSessionId(created.id);
          setMessagesLoading(false);
        }
      }
      const response = await api.sendMessage(targetSessionId, content);
      sent = true;
      if (overrideText === undefined) {
        clearAttachmentsFor(sendKey);
        clearQuoteFor(sendKey);
      }
      if (overrideText !== undefined && createdSessionId) {
        // A command (整理记忆) consumes neither the draft nor the attachments,
        // so carry them from the pending new session to the created one.
        const toKey = sessionKey(sendAgentId, createdSessionId);
        if (toKey !== sendKey) {
          const carriedDraft = draftsRef.current[sendKey] ?? "";
          if (carriedDraft) {
            draftsRef.current[toKey] = carriedDraft;
            delete draftsRef.current[sendKey];
            setDraftFor(toKey, carriedDraft);
          }
          setAttachmentsByKey((current) => {
            const carried = current[sendKey];
            if (!carried?.length) return current;
            const next = { ...current };
            delete next[sendKey];
            next[toKey] = carried;
            return next;
          });
        }
      }
      try {
        await loadMessages(targetSessionId, false);
        setMessages((current) =>
          current.filter((message) => message.id !== optimisticId),
        );
      } catch {
        // Keep the optimistic user message; the run is already created.
      }
      const activeRunNow = await api
        .getSessionActiveRun(targetSessionId)
        .catch(() => undefined);
      if (activeRunNow?.id === response.run_id) {
        await attachRun(targetSessionId, response.run_id, "");
      } else {
        clearPending(targetSessionId);
        const stillTracked = Boolean(
          live.tracking && live.activeRun && live.activeRun.id !== response.run_id,
        );
        if (activeRunNow || (activeRunNow === undefined && stillTracked)) {
          updateLive(targetSessionId, (session) =>
            session.activeRun?.id === response.run_id
              ? session
              : { ...session, notice: QUEUED_NOTICE },
          );
        }
      }
      void refreshSessions();
      return true;
    } catch (exc) {
      if (!sent) {
        if (sendAgentId === agentIdRef.current) {
          setMessages((current) => current.filter((message) => message.id !== optimisticId));
        }
        if (overrideText === undefined) setDraftFor(sendKey, text);
      }
      const conflict = conflictRunId(exc);
      if (conflict) {
        // Another tab or a channel Run owns this session. Show it instead of
        // making the user guess what the 409 means; the message was not sent.
        if (createdSessionId && !sent) {
          await api.deleteSession(createdSessionId).catch(() => undefined);
          if (sendAgentId === agentIdRef.current) {
            sessionIdRef.current = "";
            setSessions((current) => current.filter((item) => item.id !== createdSessionId));
            setSessionId("");
          }
        }
        await attachRun(targetSessionId, conflict, ATTACH_NOTICE);
        void refreshSessions();
        return false;
      }
      if (createdSessionId && !sent) {
        await api.deleteSession(createdSessionId).catch(() => undefined);
        if (sendAgentId === agentIdRef.current) {
          sessionIdRef.current = "";
          setSessions((current) => current.filter((item) => item.id !== createdSessionId));
          setSessionId("");
        }
      }
      setAgentError(describeApiError(exc), sendAgentId);
      clearPending(targetSessionId);
      return false;
    }
  }

  async function organizeMemory(): Promise<boolean> {
    const target = agents.find((item) => item.id === agentIdRef.current);
    if (!target || chatSending) return false;
    if (!target.enabled_skills.includes(MEMORY_SKILL)) {
      try {
        const updated = await api.updateAgent(target.id, {
          enabled_skills: [...target.enabled_skills, MEMORY_SKILL],
        });
        setAgents((current) =>
          current.map((item) => (item.id === updated.id ? updated : item)),
        );
      } catch (exc) {
        setAgentError(describeApiError(exc), target.id);
        return false;
      }
    }
    return send([], MEMORY_COMMAND, "整理记忆");
  }

  async function cancel() {
    if (!visibleRun || !active(visibleRun.status)) return;
    try {
      await api.cancelRun(visibleRun.id);
    } catch (exc) {
      setSessionError(describeApiError(exc), visibleRun.session_id);
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

  async function retry(runId: string) {
    if (chatSending) return;
    const retrySessionId =
      runSessionsRef.current[runId] ?? visibleRun?.session_id ?? sessionIdRef.current;
    if (!retrySessionId || pendingSessionsRef.current[retrySessionId]) return;
    setError("");
    clearLiveNotice(retrySessionId);
    markPending(retrySessionId);
    try {
      const response = await api.retryRun(runId);
      await attachRun(retrySessionId, response.run_id, "");
      void refreshSessions();
    } catch (exc) {
      const conflict = conflictRunId(exc);
      if (conflict) {
        await attachRun(retrySessionId, conflict, RETRY_ATTACH_NOTICE);
        void refreshSessions();
        return;
      }
      setSessionError(describeApiError(exc), retrySessionId);
      clearPending(retrySessionId);
    }
  }

  async function deleteAgentPermanently(id: string) {
    const result = await api.deleteAgentPermanently(id);
    for (const key of Object.keys(draftsRef.current)) {
      if (key.startsWith(`${id}:`)) delete draftsRef.current[key];
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
      closeAllSubscriptions();
      setLiveBySession({});
      pendingSessionsRef.current = {};
      setPendingSessions({});
      setSessionId("");
      setMessages([]);
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
    setMessages([]);
    setMessagesLoading(false);
    setError("");
    pushPath(DEFAULT_PATH);
  }

  async function deleteSession(id: string) {
    const forAgent = agentId;
    const key = sessionKey(forAgent, id);
    try {
      await api.deleteSession(id);
      closeSubscription(id);
      delete draftsRef.current[key];
      delete sessionCountsRef.current[id];
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
      if (forAgent !== agentIdRef.current) return;
      const list = await api.listSessions(forAgent);
      if (forAgent !== agentIdRef.current) return;
      rememberSessionCounts(list);
      setSessions(list);
      if (id === sessionIdRef.current) {
        if (list.length > 0) {
          sessionIdRef.current = list[0].id;
          setSessionId(list[0].id);
        } else {
          sessionIdRef.current = "";
          setSessionId("");
          setMessages([]);
          setMessagesLoading(false);
        }
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
      setAgentId(created.id);
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

  async function saveEnabledSkills(names: string[]) {
    if (!agent) return;
    const updated = await api.updateAgent(agent.id, { enabled_skills: names });
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
    closeAllSubscriptions();
    await api.logout().catch(() => undefined);
    setAuth(false);
    setAgents([]);
    setProviders([]);
    setSkills([]);
    setSkillsErrors([]);
    setSessions([]);
    setMessages([]);
    setMessagesLoading(false);
    setLiveBySession({});
    pendingSessionsRef.current = {};
    setPendingSessions({});
    draftsRef.current = {};
    sessionCountsRef.current = {};
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
        onAgentChange={setAgentId}
        sessions={sortSessionsByActivity(sessions)}
        sessionId={sessionId}
        onSelectSession={(id) => {
          setSearchFocus(null);
          setCommandReply(null);
          navigate("chat", id);
        }}
        onSelectSearchHit={openSearchHit}
        onNewSession={newSession}
        newSessionActive={view === "chat" && !sessionId}
        onDeleteSession={(id) => {
          setSessionToDelete(sessions.find((item) => item.id === id) ?? null);
        }}
        onOpenSettings={() => openView("settings")}
        onOpenMemory={() => openView("memory")}
        onOpenSkills={() => openView("skills")}
        onOpenTools={() => openView("tools")}
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
        channelsActive={view === "channels"}
        cronActive={view === "cron"}
        backtestActive={view === "backtest"}
        journalActive={view === "journal"}
        chanActive={view === "chan"}
        allocationActive={view === "allocation"}
        onLogout={() => void logout()}
      />

      {view === "memory" ? (
        <MemoryPage
          agents={agents}
          agentId={agentId}
          onClose={() => openView("chat")}
        />
      ) : view === "skills" ? (
        <SkillsPage
          agent={agent}
          skills={skills}
          skillsErrors={skillsErrors}
          onRefresh={refreshSkills}
          onSaveEnabled={saveEnabledSkills}
          onClose={() => openView("chat")}
        />
      ) : view === "tools" ? (
        <ToolsPage onClose={() => openView("chat")} />
      ) : view === "channels" ? (
        <ChannelsPage agent={agent} agentId={agentId} onClose={() => openView("chat")} />
      ) : view === "cron" ? (
        <CronPage
          agent={agent}
          agentId={agentId}
          onOpenSession={openCronSession}
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
          onSelectAgent={setAgentId}
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
              hasMore={messagesHasMore}
              loadingOlder={loadingOlder}
              loadOlderError={olderError}
              onLoadOlder={loadOlder}
              screenshots={screenshots}
              sessionId={sessionId}
              streamText={visibleStream}
              streamReasoning={visibleReasoning}
              trace={visibleTrace}
              runReasoning={runReasoning}
              runTraces={runTraces}
              sending={chatSending}
              runActive={runActive}
              pendingSend={pendingHere}
              activity={visibleActivity}
              terminalRun={terminalRun}
              error={error}
              notice={visibleNotice}
              commandReply={visibleCommandReply}
              activeRun={visibleRun}
              draft={draft}
              quote={quotesByKey[activeKey] ?? ""}
              attachments={attachments}
              canSend={Boolean(agentId)}
              agentId={agentId}
              agentName={agent?.name ?? "Agent"}
              channelType={currentSession?.channel_type ?? ""}
              channelName={currentSession?.channel_name ?? ""}
              memorySkillEnabled={agent?.enabled_skills.includes(MEMORY_SKILL) ?? false}
              focusSignal={focusSignal}
              focusMessageId={
                searchFocus?.sessionId === sessionId ? searchFocus.messageId : ""
              }
              focusMessageNonce={searchFocus?.nonce ?? 0}
              onDraftChange={changeDraft}
              onClearQuote={() => clearQuoteFor(activeKey)}
              onAddAttachment={(file) => addAttachmentFor(activeKey, file)}
              onRemoveAttachment={(path) => removeAttachmentFor(activeKey, path)}
              onSend={(attachments) => send(attachments)}
              onOrganizeMemory={organizeMemory}
              onCancel={() => void cancel()}
              onQuote={quote}
              onRetry={(runId) => void retry(runId)}
            />
          )}
        </main>
      )}

      {view === "chat" && drawerOpen && (
        <Drawer
          tab={drawerTab}
          onTab={setDrawerTab}
          run={visibleRun}
          events={visibleEvents}
          agent={agent}
          providers={providers}
          onSaveAgent={saveAgent}
          onClose={() => setDrawerOpen(false)}
        />
      )}

      <ConfirmDialog
        open={sessionToDelete !== null}
        title="删除这个会话？"
        description={`「${sessionToDelete?.title || "新会话"}」的消息、Run 和 checkpoint 会一起删除，无法恢复。`}
        confirmLabel="删除"
        danger
        onClose={() => setSessionToDelete(null)}
        onConfirm={() => {
          const target = sessionToDelete;
          setSessionToDelete(null);
          if (target) void deleteSession(target.id);
        }}
      />

      <NewAgentDialog
        open={newAgentOpen}
        providers={providers}
        busy={creatingAgent}
        onClose={() => setNewAgentOpen(false)}
        onCreate={createAgent}
      />

      {viewportDebug && <ViewportDebug />}
    </div>
  );
}
