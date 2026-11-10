import { useEffect, useRef, useState } from "react";

import { api } from "./api";
import type {
  Agent,
  AgentSavePayload,
  Attachment,
  DrawerTab,
  MemoryItem,
  Provider,
  SessionSummary,
  SkillInfo,
  TranscriptMessage,
  TurnStreamEvent,
} from "./types";
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

/** A slash-command reply shown once in the current chat lane, never persisted. */
type CommandReply = {
  key: string;
  command: string;
  text: string;
};

/** Live turn state is kept per session so switching away never drops it. */
type SessionLive = {
  streamText: string;
  streamReasoning: string;
  activity: string[];
  notice: string;
  tracking: boolean;
};

const EMPTY_LIVE: SessionLive = {
  streamText: "",
  streamReasoning: "",
  activity: [],
  notice: "",
  tracking: false,
};

type FocusTarget = {
  sessionId: string;
  messageId: string;
  nonce: number;
};

const MEMORY_SKILL = "memory-organizer";
const QUEUED_NOTICE = "已排队，前面的任务完成后会自动执行";
const SIDEBAR_POLL_MS = 5000;
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
  const [commandReply, setCommandReply] = useState<CommandReply | null>(null);
  const [attachmentsByKey, setAttachmentsByKey] = useState<Record<string, Attachment[]>>({});
  const [quotesByKey, setQuotesByKey] = useState<Record<string, string>>({});
  const [pendingSessions, setPendingSessions] = useState<Record<string, true>>({});
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
  const [focus, setFocus] = useState<FocusTarget | null>(null);

  const streamsRef = useRef<Map<string, EventSource>>(new Map());
  const sessionsRef = useRef<SessionSummary[]>([]);
  const sessionAgentRef = useRef<Record<string, string>>({});
  const transcriptRequestRef = useRef<Record<string, number>>({});
  const skipRefreshRef = useRef<Set<string>>(new Set());
  const sessionIdRef = useRef("");
  const sessionsRequestRef = useRef(0);
  const agentIdRef = useRef("");
  const pendingMemoryFocus = useRef<{
    agentId: string;
    sessionId: string;
    messageId: string;
  } | null>(null);
  const draftsRef = useRef<Record<string, string>>({});
  const draftKeyRef = useRef("");
  const pendingSessionsRef = useRef<Record<string, true>>({});
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
  const tracking = live.tracking;
  const chatSending = pendingHere || tracking;
  const messages = messagesBySession[sessionId] ?? [];
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
    setSessions(list);
  }

  function setMessagesFor(id: string, list: TranscriptMessage[]) {
    setMessagesBySession((current) => ({ ...current, [id]: list }));
  }

  function appendMessage(id: string, message: TranscriptMessage) {
    setMessagesBySession((current) => ({
      ...current,
      [id]: [...(current[id] ?? []), message],
    }));
  }

  function removeMessage(id: string, messageId: string) {
    setMessagesBySession((current) => {
      const list = current[id];
      if (!list) return current;
      return { ...current, [id]: list.filter((item) => item.id !== messageId) };
    });
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

  // A send request is pending per session, so turns in other sessions never
  // lock this session's composer.
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

  function closeStream(id: string) {
    const source = streamsRef.current.get(id);
    if (!source) return;
    streamsRef.current.delete(id);
    source.close();
  }

  function closeAllStreams() {
    for (const id of Array.from(streamsRef.current.keys())) closeStream(id);
  }

  function attachIfBusy(id: string) {
    if (!id || streamsRef.current.has(id)) return;
    const row = sessionsRef.current.find((item) => item.id === id);
    if (row?.busy) openStream(id);
  }

  function openStream(forSession: string, turnId?: string) {
    closeStream(forSession);
    if (!sessionAgentRef.current[forSession]) {
      sessionAgentRef.current[forSession] = agentIdRef.current;
    }
    const source = new EventSource(api.sessionStreamUrl(forSession, turnId));
    streamsRef.current.set(forSession, source);
    updateLive(forSession, (current) => ({ ...current, tracking: true }));

    source.onmessage = (message) => {
      let event: TurnStreamEvent;
      try {
        event = JSON.parse(message.data) as TurnStreamEvent;
      } catch {
        return;
      }
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
            activity: [...current.activity, event.name],
            notice: "",
          }));
          break;
        case "turn_error":
          updateLive(forSession, (current) => ({ ...current, notice: event.error }));
          break;
        case "turn_end": {
          if (streamsRef.current.get(forSession) !== source) return;
          closeStream(forSession);
          if (event.status === "failed") {
            updateLive(forSession, (current) => ({
              ...current,
              notice: event.error || "本轮执行失败。",
            }));
          } else if (event.status === "cancelled") {
            updateLive(forSession, (current) => ({ ...current, notice: "已停止当前任务。" }));
          }
          void finishTurn(forSession);
          break;
        }
      }
    };

    source.onerror = () => {
      if (streamsRef.current.get(forSession) !== source) return;
      closeStream(forSession);
      void refreshTranscript(forSession);
    };
  }

  async function refreshTranscript(forSession: string) {
    if (!forSession) return;
    const requestId = (transcriptRequestRef.current[forSession] ?? 0) + 1;
    transcriptRequestRef.current[forSession] = requestId;
    setLoadingFor(forSession, true);
    let failure = "";
    try {
      const owner = sessionAgentRef.current[forSession] ?? agentIdRef.current;
      const list = await api.listSessionMessages(forSession, owner);
      if (transcriptRequestRef.current[forSession] !== requestId) return;
      setMessagesFor(forSession, list);
    } catch (exc) {
      failure = describeApiError(exc);
    } finally {
      if (transcriptRequestRef.current[forSession] !== requestId) return;
      setLoadingFor(forSession, false);
      const streaming = streamsRef.current.has(forSession);
      updateLive(forSession, (current) => ({
        ...current,
        // A live stream owns these fields; reopening a busy session must not
        // wipe text the stream has already accumulated.
        streamText: streaming ? current.streamText : "",
        streamReasoning: streaming ? current.streamReasoning : "",
        activity: streaming ? current.activity : [],
        notice: failure || current.notice,
        tracking: streaming,
      }));
    }
  }

  async function finishTurn(forSession: string) {
    await refreshTranscript(forSession);
    await refreshSessions();
    if (forSession === sessionIdRef.current) attachIfBusy(forSession);
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

  function openSession(id: string) {
    setFocus(null);
    setCommandReply(null);
    navigate("chat", id);
    void refreshSessions();
  }

  function openFocusSession(targetSessionId: string, messageId: string) {
    setCommandReply(null);
    setFocus({ sessionId: targetSessionId, messageId, nonce: Date.now() });
    navigate("chat", targetSessionId);
  }

  function openMemorySource(item: MemoryItem) {
    if (!item.source_session_id || !item.source_message_id) return;
    if (item.source_message_exists === false) return;
    const targetAgent = item.source_agent_id || agentIdRef.current;
    if (targetAgent && targetAgent !== agentIdRef.current) {
      pendingMemoryFocus.current = {
        agentId: targetAgent,
        sessionId: item.source_session_id,
        messageId: item.source_message_id,
      };
      setAgentId(targetAgent);
      return;
    }
    openFocusSession(item.source_session_id, item.source_message_id);
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
      return;
    }
    void loadSessions(agentId).catch((exc) => setAgentError(describeApiError(exc), agentId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, agentId]);

  useEffect(() => {
    // Consume a cross-agent memory link after the agent switch has reset the
    // previous agent's session state; otherwise the reset would clear the focus.
    const pending = pendingMemoryFocus.current;
    if (!pending || pending.agentId !== agentId) return;
    pendingMemoryFocus.current = null;
    openFocusSession(pending.sessionId, pending.messageId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

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

  async function loadSessions(forAgent: string) {
    const requestId = ++sessionsRequestRef.current;
    const list = await api.listSessions(forAgent);
    if (requestId !== sessionsRequestRef.current || forAgent !== agentIdRef.current) return;
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
    sessionIdRef.current = target;
    setSessionId(target);
  }

  async function refreshSessions() {
    const forAgent = agentIdRef.current;
    if (!forAgent) return;
    try {
      const list = await api.listSessions(forAgent);
      if (forAgent !== agentIdRef.current) return;
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

  async function send(attachments: Attachment[] = [], overrideText?: string): Promise<boolean> {
    const text = (overrideText ?? draft).trim();
    const sendAgentId = agentIdRef.current || agentId;
    const sendKey = sessionKey(sendAgentId, sessionIdRef.current);
    const quoteText =
      overrideText === undefined ? (quotesByKey[sendKey] ?? "").trim() : "";
    if ((!text && attachments.length === 0 && !quoteText) || !sendAgentId) {
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
    const wasNew = !sessionIdRef.current;
    const targetSessionId = sessionIdRef.current || newSessionId();
    const pendingKey = sessionIdRef.current;
    const alreadyStreaming = streamsRef.current.has(targetSessionId);
    sessionAgentRef.current[targetSessionId] = sendAgentId;
    setError("");
    clearLiveNotice(targetSessionId);
    markPending(pendingKey);
    if (overrideText === undefined) setDraftFor(sendKey, "");
    const optimisticId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    appendMessage(targetSessionId, transcriptMessage("user", content, optimisticId));
    if (!alreadyStreaming) {
      updateLive(targetSessionId, () => ({ ...EMPTY_LIVE, tracking: true }));
    }
    if (wasNew) skipRefreshRef.current.add(targetSessionId);
    try {
      const response = await api.sendMessage({
        agent_id: sendAgentId,
        session_id: targetSessionId,
        content,
      });
      if (overrideText === undefined) {
        clearAttachmentsFor(sendKey);
        clearQuoteFor(sendKey);
      }
      if (wasNew) {
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
        if (overrideText !== undefined) {
          // A command (整理记忆) consumes neither the draft nor the attachments,
          // so carry them from the pending new session to the created one.
          const toKey = sessionKey(sendAgentId, targetSessionId);
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
      if (alreadyStreaming) {
        // Another turn is still streaming in this lane; it will pick up the
        // queued turn when it ends, so keep the current stream attached.
        updateLive(targetSessionId, (current) => ({ ...current, notice: QUEUED_NOTICE }));
      } else {
        openStream(targetSessionId, response.turn_id);
      }
      void refreshSessions();
      return true;
    } catch (exc) {
      removeMessage(targetSessionId, optimisticId);
      if (overrideText === undefined) setDraftFor(sendKey, text);
      skipRefreshRef.current.delete(targetSessionId);
      if (!alreadyStreaming) {
        updateLive(targetSessionId, (current) => ({ ...current, tracking: false }));
      }
      if (wasNew) {
        // The session never appeared in the URL or sidebar; drop its traces.
        delete sessionAgentRef.current[targetSessionId];
        setMessagesBySession((current) => {
          if (!current[targetSessionId]) return current;
          const next = { ...current };
          delete next[targetSessionId];
          return next;
        });
        setLiveBySession((current) => {
          if (!current[targetSessionId]) return current;
          const next = { ...current };
          delete next[targetSessionId];
          return next;
        });
      }
      setAgentError(describeApiError(exc), sendAgentId);
      return false;
    } finally {
      clearPending(pendingKey);
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
    return send([], MEMORY_COMMAND);
  }

  async function cancel() {
    const id = sessionIdRef.current;
    if (!id) return;
    try {
      const result = await api.interruptSession(id);
      closeStream(id);
      updateLive(id, (current) => ({
        ...current,
        tracking: false,
        notice: result.interrupted ? "已请求停止当前任务。" : "当前没有正在执行的任务。",
      }));
      void refreshSessions();
      // Let the server publish the terminal event and flush the transcript
      // before the lane is rebuilt from history.
      window.setTimeout(() => {
        if (sessionIdRef.current === id) void refreshTranscript(id);
      }, 600);
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
      delete sessionAgentRef.current[session];
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

  async function deleteSession(id: string) {
    const forAgent = agentId;
    const key = sessionKey(forAgent, id);
    try {
      await api.deleteSession(id, forAgent);
      closeStream(id);
      delete draftsRef.current[key];
      delete sessionAgentRef.current[id];
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
        onAgentChange={setAgentId}
        sessions={sessions}
        sessionId={sessionId}
        onSelectSession={openSession}
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
          onOpenSource={openMemorySource}
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
              sessionId={sessionId}
              streamText={live.streamText}
              streamReasoning={live.streamReasoning}
              activity={live.activity}
              sending={chatSending}
              tracking={tracking}
              pendingSend={pendingHere}
              error={error}
              notice={live.notice}
              commandReply={visibleCommandReply}
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
              focusMessageId={focus?.sessionId === sessionId ? focus.messageId : ""}
              focusMessageNonce={focus?.nonce ?? 0}
              onDraftChange={changeDraft}
              onClearQuote={() => clearQuoteFor(activeKey)}
              onAddAttachment={(file) => addAttachmentFor(activeKey, file)}
              onRemoveAttachment={(path) => removeAttachmentFor(activeKey, path)}
              onSend={(sent) => send(sent)}
              onOrganizeMemory={organizeMemory}
              onCancel={() => void cancel()}
              onQuote={quote}
            />
          )}
        </main>
      )}

      {view === "chat" && drawerOpen && (
        <Drawer
          tab={drawerTab}
          onTab={setDrawerTab}
          activity={live.activity}
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
        description={`「${sessionToDelete?.title || "新会话"}」的消息和 checkpoint 会一起删除，无法恢复。`}
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

    </div>
  );
}
