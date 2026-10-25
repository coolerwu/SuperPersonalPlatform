import { useEffect, useRef, useState } from "react";

import { api, ApiError, errorText } from "./api";
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
  SkillInfo,
} from "./types";
import { activityFromEvents, attemptText, describeToolCall } from "./runActivity";
import { MEMORY_COMMAND } from "./messageContent";
import { ChatPanel } from "./components/ChatPanel";
import { ChannelsPage } from "./components/ChannelsPage";
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

/** Live run state is kept per session so switching away never drops it. */
type SessionLive = {
  activeRun: Run | null;
  events: RunEvent[];
  streamText: string;
  streamReasoning: string;
  activity: string;
  notice: string;
  tracking: boolean;
};

const EMPTY_LIVE: SessionLive = {
  activeRun: null,
  events: [],
  streamText: "",
  streamReasoning: "",
  activity: "",
  notice: "",
  tracking: false,
};

type RunSubscription = {
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

export default function App() {
  const [auth, setAuth] = useState<boolean | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillsErrors, setSkillsErrors] = useState<string[]>([]);
  const [agentId, setAgentId] = useState("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [screenshots, setScreenshots] = useState<ScreenshotInfo[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [pendingSession, setPendingSession] = useState<string | null>(null);
  const [liveBySession, setLiveBySession] = useState<Record<string, SessionLive>>({});
  const [runReasoning, setRunReasoning] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("run");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [view, setView] = useState<"chat" | "settings" | "memory" | "skills" | "tools" | "channels">("chat");
  const [focusSignal, setFocusSignal] = useState(0);
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<Session | null>(null);

  const subscriptionsRef = useRef<Map<string, RunSubscription>>(new Map());
  const appliedEventsRef = useRef<Map<string, number>>(new Map());
  const finishingRunsRef = useRef<Set<string>>(new Set());
  const finishedRunsRef = useRef<Set<string>>(new Set());
  const suppressSessionResetRef = useRef<string | null>(null);
  const sessionIdRef = useRef("");
  const messagesRequestRef = useRef(0);
  const sessionsRequestRef = useRef(0);
  const agentIdRef = useRef("");
  const draftByAgentRef = useRef<Record<string, string>>({});
  const draftAgentRef = useRef("");
  const draftRef = useRef("");
  const runSessionsRef = useRef<Record<string, string>>({});

  const agent = agents.find((item) => item.id === agentId);
  const live = liveBySession[sessionId] ?? EMPTY_LIVE;
  const pendingHere = pendingSession !== null && pendingSession === sessionId;
  const chatSending = pendingHere || (live.tracking && isActiveRun(live.activeRun));
  const sending =
    pendingSession !== null ||
    Object.values(liveBySession).some((item) => item.tracking && isActiveRun(item.activeRun));
  const busyElsewhere = sending && !chatSending;
  const visibleRun = live.activeRun;
  const visibleStream = live.streamText;
  const visibleReasoning = live.streamReasoning;
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

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    // Drafts are per-agent and survive switching while this page stays open.
    const previousAgent = draftAgentRef.current;
    if (previousAgent && previousAgent !== agentId) {
      draftByAgentRef.current[previousAgent] = draftRef.current;
    }
    draftAgentRef.current = agentId;
    setDraft(draftByAgentRef.current[agentId] ?? "");
  }, [agentId]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sidebarOpen]);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const syncHeight = () => {
      document.documentElement.style.setProperty("--app-height", `${viewport.height}px`);
    };
    syncHeight();
    viewport.addEventListener("resize", syncHeight);
    return () => {
      viewport.removeEventListener("resize", syncHeight);
      document.documentElement.style.removeProperty("--app-height");
    };
  }, []);

  function openView(next: "chat" | "settings" | "memory" | "skills" | "tools" | "channels") {
    setView(next);
    setSidebarOpen(false);
  }

  useEffect(() => {
    void bootstrap().catch((exc) => {
      setError(errorText(exc));
      setAuth(false);
    });
    return () => closeAllSubscriptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    agentIdRef.current = agentId;
    // Drop the previous agent's session before its view can appear under the
    // new agent. Live runs of other sessions keep streaming in the background.
    sessionIdRef.current = "";
    setSessionId("");
    setMessages([]);
    setMessagesLoading(false);
    setSessions([]);
    setError("");
    if (!auth || !agentId) {
      sessionsRequestRef.current += 1;
      return;
    }
    void loadSessions(agentId).catch((exc) => setAgentError(errorText(exc), agentId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, agentId]);

  useEffect(() => {
    sessionIdRef.current = sessionId;
    const keepMessages = suppressSessionResetRef.current === sessionId;
    suppressSessionResetRef.current = null;
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
    void loadMessages(sessionId).catch((exc) => {
      if (sessionId !== sessionIdRef.current) return;
      setMessagesLoading(false);
      setSessionError(errorText(exc), sessionId);
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
      setSkillsErrors([errorText(exc)]);
      throw exc;
    }
  }

  function refreshSkills() {
    return loadSkills().catch(() => undefined);
  }

  async function loadWorkspace() {
    const [agentList, providerList] = await Promise.all([api.listAgents(), api.listProviders()]);
    setAgents(agentList);
    setProviders(providerList);
    if (agentList.length === 0) {
      setNewAgentOpen(true);
      return;
    }
    setAgentId((current) => (current && agentList.some((item) => item.id === current) ? current : agentList[0].id));
  }

  async function loadSessions(forAgent: string) {
    const requestId = ++sessionsRequestRef.current;
    const list = await api.listSessions(forAgent);
    if (requestId !== sessionsRequestRef.current || forAgent !== agentIdRef.current) return;
    setSessions(list);
    if (list.length === 0) {
      sessionIdRef.current = "";
      setSessionId("");
      setMessages([]);
      setMessagesLoading(false);
      return;
    }
    if (!list.some((item) => item.id === sessionIdRef.current)) {
      sessionIdRef.current = list[0].id;
      setSessionId(list[0].id);
    }
  }

  async function refreshSessions() {
    if (!agentId) return;
    const forAgent = agentId;
    try {
      const list = await api.listSessions(forAgent);
      if (forAgent !== agentIdRef.current) return;
      setSessions(list);
    } catch {
      // Keep the current list on a transient refresh failure.
    }
  }

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
    const subscription: RunSubscription = { source: null, timer: null };
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
      if (event.type === "run_queued") {
        if (event.payload.reason === "service_restart") {
          // The checkpoint keeps the interrupted attempt; the requeued run will
          // stream a fresh answer, so drop the previous attempt's live text.
          next.streamText = "";
          next.streamReasoning = "";
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
        next.streamReasoning = session.streamReasoning + String(event.payload.delta ?? "");
        next.activity = "";
        if (session.notice === RECONNECT_NOTICE) next.notice = "";
      } else if (event.type === "tool_call") {
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
    const subscription: RunSubscription = { source: null, timer: null };
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
        setSessionError(errorText(exc), id);
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
      if (run.session_id === sessionIdRef.current && run.status === "completed") {
        // Persist the final assistant message before dropping the stream text,
        // so the completed answer never blinks out of the transcript.
        await loadMessages(run.session_id, false).catch(() => undefined);
      }
      setRunReasoning((current) => ({ ...current, [run.id]: reasoning }));
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
        updateLive(id, (session) => ({
          ...session,
          activeRun: next,
          events: nextEvents,
          streamText: attemptText(nextEvents, "assistant_delta"),
          streamReasoning: attemptText(nextEvents, "reasoning_delta"),
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
      setSessionError(errorText(exc), id);
    } finally {
      finishingRunsRef.current.delete(runId);
    }
  }

  async function loadMessages(id: string, syncRun = true) {
    const requestId = ++messagesRequestRef.current;
    const list = await api.listMessages(id);
    if (requestId !== messagesRequestRef.current || id !== sessionIdRef.current) return;
    setMessages(list);
    setMessagesLoading(false);
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
      activeRun?.id ?? [...list].reverse().find((message) => message.run_id)?.run_id;
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
    const running = isActiveRun(run);
    const streamText = run.status === "completed" ? "" : text;
    const streamReasoning = run.status === "completed" ? "" : reasoning;
    setRunReasoning((current) => ({ ...current, [run.id]: reasoning }));
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

  async function attachRun(id: string, runId: string, notice: string) {
    try {
      const [run, runEvents] = await Promise.all([api.getRun(runId), api.getRunEvents(runId)]);
      if (run.session_id !== id) return;
      runSessionsRef.current[run.id] = run.session_id;
      rememberEvents(runEvents);
      const reasoning = attemptText(runEvents, "reasoning_delta");
      const text = attemptText(runEvents, "assistant_delta");
      const running = isActiveRun(run);
      setRunReasoning((current) => ({ ...current, [run.id]: reasoning }));
      updateLive(id, () => ({
        activeRun: run,
        events: runEvents,
        streamText: run.status === "completed" ? "" : text,
        streamReasoning: run.status === "completed" ? "" : reasoning,
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
      setSessionError(errorText(exc), id);
    } finally {
      setPendingSession(null);
    }
  }

  async function send(
    attachments: Attachment[] = [],
    overrideText?: string,
    sessionTitle?: string,
  ): Promise<boolean> {
    const text = (overrideText ?? draft).trim();
    if ((!text && attachments.length === 0) || !agentId || sending) return false;
    const sendAgentId = agentId;
    const attachmentBlock = attachments
      .map((file) => `- ${file.name}（工作区路径：${file.path}，大小：${file.size} 字节）`)
      .join("\n");
    const content = attachmentBlock
      ? `附件已上传到工作区：\n${attachmentBlock}${text ? `\n\n${text}` : ""}`
      : text;
    setError("");
    clearLiveNotice(sessionIdRef.current);
    setPendingSession(sessionIdRef.current);
    if (overrideText === undefined) setDraft("");
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
    let createdSessionId = "";
    let sent = false;
    let targetSessionId = sessionIdRef.current;
    try {
      if (!targetSessionId) {
        const created = await api.createSession(sendAgentId, sessionTitle ?? "新会话");
        createdSessionId = created.id;
        targetSessionId = created.id;
        if (sendAgentId === agentIdRef.current) {
          sessionIdRef.current = created.id;
          suppressSessionResetRef.current = created.id;
          setSessions((current) => [created, ...current]);
          setSessionId(created.id);
          setPendingSession(created.id);
          setMessagesLoading(false);
        }
      }
      const response = await api.sendMessage(targetSessionId, content);
      sent = true;
      try {
        await loadMessages(targetSessionId, false);
      } catch {
        // Keep the optimistic user message; the run is already created.
      }
      await attachRun(targetSessionId, response.run_id, "");
      void refreshSessions();
      return true;
    } catch (exc) {
      if (!sent) {
        if (sendAgentId === agentIdRef.current) {
          setMessages((current) => current.filter((message) => message.id !== optimisticId));
          if (overrideText === undefined) setDraft(text);
        } else if (overrideText === undefined) {
          draftByAgentRef.current[sendAgentId] = text;
        }
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
        setPendingSession(null);
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
      setAgentError(errorText(exc), sendAgentId);
      setPendingSession(null);
      return false;
    }
  }

  async function organizeMemory(): Promise<boolean> {
    const target = agents.find((item) => item.id === agentIdRef.current);
    if (!target || sending) return false;
    if (!target.enabled_skills.includes(MEMORY_SKILL)) {
      try {
        const updated = await api.updateAgent(target.id, {
          enabled_skills: [...target.enabled_skills, MEMORY_SKILL],
        });
        setAgents((current) =>
          current.map((item) => (item.id === updated.id ? updated : item)),
        );
      } catch (exc) {
        setAgentError(errorText(exc), target.id);
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
      setSessionError(errorText(exc), visibleRun.session_id);
    }
  }

  function quote(text: string) {
    const quoted = text.split("\n").map((line) => `> ${line}`).join("\n");
    setDraft((current) => (current.trim() ? `${quoted}\n\n${current}` : `${quoted}\n\n`));
    setFocusSignal((current) => current + 1);
  }

  async function retry(runId: string) {
    if (sending) return;
    const retrySessionId =
      runSessionsRef.current[runId] ?? visibleRun?.session_id ?? sessionIdRef.current;
    if (!retrySessionId) return;
    setError("");
    clearLiveNotice(retrySessionId);
    setPendingSession(retrySessionId);
    try {
      const response = await api.retryRun(runId);
      await attachRun(retrySessionId, response.run_id, "");
      void refreshSessions();
    } catch (exc) {
      const conflict = conflictRunId(exc);
      if (conflict) {
        setPendingSession(null);
        await attachRun(retrySessionId, conflict, RETRY_ATTACH_NOTICE);
        void refreshSessions();
        return;
      }
      setSessionError(errorText(exc), retrySessionId);
      setPendingSession(null);
    }
  }

  async function deleteAgentPermanently(id: string) {
    const result = await api.deleteAgentPermanently(id);
    await loadWorkspace();
    if (id === agentId) {
      closeAllSubscriptions();
      setLiveBySession({});
      setPendingSession(null);
      setSessionId("");
      setMessages([]);
    }
    return result;
  }

  function newSession() {
    if (!agentId) return;
    setView("chat");
    setSidebarOpen(false);
    sessionIdRef.current = "";
    setSessionId("");
    setMessages([]);
    setMessagesLoading(false);
    setError("");
    setDraft("");
  }

  async function deleteSession(id: string) {
    const forAgent = agentId;
    try {
      await api.deleteSession(id);
      closeSubscription(id);
      setLiveBySession((current) => {
        if (!current[id]) return current;
        const next = { ...current };
        delete next[id];
        return next;
      });
      if (forAgent !== agentIdRef.current) return;
      const list = await api.listSessions(forAgent);
      if (forAgent !== agentIdRef.current) return;
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
      setAgentError(errorText(exc), forAgent);
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
    setPendingSession(null);
    setAgentId("");
    setSessionId("");
    setDraft("");
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
    <div className={`app-shell ${view === "chat" && drawerOpen ? "with-drawer" : ""}`}>
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
        agents={agents}
        agentId={agentId}
        onAgentChange={setAgentId}
        sessions={sessions}
        sessionId={sessionId}
        onSelectSession={(id) => {
          setSessionId(id);
          openView("chat");
        }}
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
        settingsActive={view === "settings"}
        memoryActive={view === "memory"}
        skillsActive={view === "skills"}
        toolsActive={view === "tools"}
        channelsActive={view === "channels"}
        onLogout={() => void logout()}
      />

      {view === "memory" ? (
        <MemoryPage
          agents={agents}
          agentId={agentId}
          onClose={() => setView("chat")}
        />
      ) : view === "skills" ? (
        <SkillsPage
          agent={agent}
          skills={skills}
          skillsErrors={skillsErrors}
          onRefresh={refreshSkills}
          onSaveEnabled={saveEnabledSkills}
          onClose={() => setView("chat")}
        />
      ) : view === "tools" ? (
        <ToolsPage onClose={() => setView("chat")} />
      ) : view === "channels" ? (
        <ChannelsPage agent={agent} agentId={agentId} onClose={() => setView("chat")} />
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
          onClose={() => setView("chat")}
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
              screenshots={screenshots}
              sessionId={sessionId}
              streamText={visibleStream}
              streamReasoning={visibleReasoning}
              runReasoning={runReasoning}
              sending={chatSending}
              activity={visibleActivity}
              terminalRun={terminalRun}
              error={error}
              notice={visibleNotice}
              activeRun={visibleRun}
              draft={draft}
              canSend={Boolean(agentId)}
              sendBlocked={busyElsewhere}
              agentId={agentId}
              agentName={agent?.name ?? "Agent"}
              memorySkillEnabled={agent?.enabled_skills.includes(MEMORY_SKILL) ?? false}
              focusSignal={focusSignal}
              onDraftChange={setDraft}
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
    </div>
  );
}
