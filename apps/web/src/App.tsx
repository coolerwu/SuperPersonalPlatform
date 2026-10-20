import { useEffect, useRef, useState } from "react";

import { api, errorText } from "./api";
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
import { ChatPanel } from "./components/ChatPanel";
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
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [streamText, setStreamText] = useState("");
  const [error, setError] = useState("");
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("run");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [view, setView] = useState<"chat" | "settings" | "memory" | "skills" | "tools">("chat");
  const [focusSignal, setFocusSignal] = useState(0);
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<Session | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const sessionIdRef = useRef("");
  const messagesRequestRef = useRef(0);
  const sessionsRequestRef = useRef(0);
  const agentIdRef = useRef("");
  const draftByAgentRef = useRef<Record<string, string>>({});
  const draftAgentRef = useRef("");
  const draftRef = useRef("");
  const runSessionsRef = useRef<Record<string, string>>({});
  const finishedRuns = useRef<Set<string>>(new Set());

  const agent = agents.find((item) => item.id === agentId);
  const chatSending = sending && (!activeRun || activeRun.session_id === sessionId);
  const busyElsewhere = sending && !chatSending;
  const visibleRun = activeRun && activeRun.session_id === sessionId ? activeRun : null;
  const visibleStream = chatSending ? streamText : "";
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
            : (agent?.name ?? "Agent");

  function setAgentError(message: string, forAgent: string) {
    if (forAgent !== agentIdRef.current) return;
    setError(message);
  }

  function setSessionError(message: string, forSession: string | null | undefined) {
    if (forSession && forSession !== sessionIdRef.current) return;
    setError(message);
  }

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

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

  function openView(next: "chat" | "settings" | "memory" | "skills" | "tools") {
    setView(next);
    setSidebarOpen(false);
  }

  useEffect(() => {
    void bootstrap().catch((exc) => {
      setError(errorText(exc));
      setAuth(false);
    });
    return () => closeStream();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    agentIdRef.current = agentId;
    // Drop the previous agent's session before its view can appear under the
    // new agent. activeRun is kept on purpose: it keeps chatSending false for
    // the new agent while the previous agent's run is still streaming.
    sessionIdRef.current = "";
    setSessionId("");
    setMessages([]);
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
    if (!sessionId) {
      setMessages([]);
      return;
    }
    void loadMessages(sessionId).catch((exc) => setSessionError(errorText(exc), sessionId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) {
      setScreenshots([]);
      return;
    }
    let active = true;
    void api
      .listScreenshots(sessionId)
      .then((list) => {
        if (active && sessionId === sessionIdRef.current) setScreenshots(list);
      })
      .catch(() => undefined);
    return () => {
      active = false;
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
      // A run can still be streaming for another agent; keep it tracked so
      // this empty view is not mistaken for the sending session.
      if (!sending) {
        setActiveRun(null);
        setEvents([]);
        setStreamText("");
      }
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

  async function loadMessages(id: string, syncRun = true) {
    const requestId = ++messagesRequestRef.current;
    const list = await api.listMessages(id);
    if (requestId !== messagesRequestRef.current || id !== sessionIdRef.current) return;
    setMessages(list);
    if (!syncRun) return;
    setError("");

    const lastRunId = [...list].reverse().find((message) => message.run_id)?.run_id;
    if (!lastRunId) {
      setActiveRun(null);
      setEvents([]);
      return;
    }
    const [run, runEvents] = await Promise.all([api.getRun(lastRunId), api.getRunEvents(lastRunId)]);
    runSessionsRef.current[run.id] = run.session_id;
    setActiveRun(run);
    setEvents(runEvents);
    if (run.status === "failed") {
      setSessionError(run.error || "Run failed", run.session_id);
    }
    if ((run.status === "queued" || run.status === "running") && !finishedRuns.current.has(run.id)) {
      setSending(true);
      const lastQueuedId = runEvents.reduce(
        (latest, event) => (event.type === "run_queued" ? event.id : latest),
        0,
      );
      setStreamText(
        runEvents
          .filter((event) => event.type === "assistant_delta" && event.id > lastQueuedId)
          .map((event) => String(event.payload.delta ?? ""))
          .join(""),
      );
      subscribe(run.id, runEvents.at(-1)?.id ?? 0);
    }
  }

  function closePoll() {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function closeStream() {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    closePoll();
  }

  function subscribe(runId: string, after = 0) {
    closeStream();
    let lastId = after;
    const source = new EventSource(api.streamUrl(runId, after));
    eventSourceRef.current = source;
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as RunEvent;
        lastId = Math.max(lastId, event.id);
        applyEvent(event);
      } catch {
        // Ignore malformed frames.
      }
    };
    source.onerror = () => {
      closeStream();
      void pollRun(runId, lastId);
    };
  }

  function applyEvent(event: RunEvent) {
    setEvents((current) => (current.some((item) => item.id === event.id) ? current : [...current, event]));
    if (event.type === "assistant_delta") {
      setStreamText((current) => current + String(event.payload.delta ?? ""));
    }
    if (event.type === "browser_action" && event.payload.screenshot_id) {
      void refreshScreenshots();
    }
    if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_cancelled") {
      void finishRun(event.run_id);
    }
  }

  async function pollRun(runId: string, after = 0) {
    closePoll();
    let lastId = after;
    const tick = async () => {
      try {
        const newEvents = await api.getRunEvents(runId, lastId);
        for (const event of newEvents) {
          lastId = event.id;
          applyEvent(event);
        }
        const run = await api.getRun(runId);
        runSessionsRef.current[run.id] = run.session_id;
        setActiveRun(run);
        if (terminal(run.status)) {
          closePoll();
          void finishRun(runId);
        }
      } catch (exc) {
        closePoll();
        setSessionError(errorText(exc), runSessionsRef.current[runId]);
        setSending(false);
      }
    };
    pollTimerRef.current = window.setInterval(tick, 800);
    void tick();
  }

  async function finishRun(runId: string) {
    if (finishedRuns.current.has(runId)) return;
    finishedRuns.current.add(runId);
    closeStream();
    setSending(false);
    try {
      const [run, runEvents] = await Promise.all([api.getRun(runId), api.getRunEvents(runId)]);
      runSessionsRef.current[run.id] = run.session_id;
      setActiveRun(run);
      setEvents(runEvents);
      setStreamText("");
      if (run.session_id === sessionIdRef.current) {
        await loadMessages(run.session_id, false);
      }
      if (run.status === "failed") {
        setSessionError(run.error || "Run failed", run.session_id);
      }
      await refreshSessions();
      void refreshScreenshots();
    } catch (exc) {
      setSessionError(errorText(exc), runSessionsRef.current[runId]);
    }
  }

  async function send(attachments: Attachment[] = []): Promise<boolean> {
    const text = draft.trim();
    if ((!text && attachments.length === 0) || !agentId || sending) return false;
    const sendAgentId = agentId;
    const attachmentBlock = attachments
      .map((file) => `- ${file.name}（工作区路径：${file.path}）`)
      .join("\n");
    const content = attachmentBlock
      ? `附件已上传到工作区：\n${attachmentBlock}${text ? `\n\n${text}` : ""}`
      : text;
    setError("");
    setSending(true);
    setStreamText("");
    setEvents([]);
    setActiveRun(null);
    setDraft("");
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
    try {
      let targetSessionId = sessionIdRef.current;
      if (!targetSessionId) {
        const created = await api.createSession(sendAgentId);
        createdSessionId = created.id;
        targetSessionId = created.id;
        if (sendAgentId === agentIdRef.current) {
          sessionIdRef.current = created.id;
          setSessions((current) => [created, ...current]);
        }
      }
      const response = await api.sendMessage(targetSessionId, content);
      sent = true;
      if (createdSessionId && sendAgentId === agentIdRef.current) {
        setSessionId(createdSessionId);
      }
      try {
        await loadMessages(targetSessionId, false);
      } catch {
        // Keep the optimistic user message; the run is already created.
      }
      const run = await api.getRun(response.run_id);
      runSessionsRef.current[run.id] = run.session_id;
      setActiveRun(run);
      subscribe(response.run_id);
      void refreshSessions();
      return true;
    } catch (exc) {
      if (!sent) {
        if (sendAgentId === agentIdRef.current) {
          setMessages((current) => current.filter((message) => message.id !== optimisticId));
          setDraft(text);
        } else {
          draftByAgentRef.current[sendAgentId] = text;
        }
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
      setSending(false);
      return false;
    }
  }

  async function cancel() {
    if (!activeRun) return;
    try {
      await api.cancelRun(activeRun.id);
    } catch (exc) {
      setSessionError(errorText(exc), activeRun.session_id);
    }
  }

  function quote(text: string) {
    const quoted = text.split("\n").map((line) => `> ${line}`).join("\n");
    setDraft((current) => (current.trim() ? `${quoted}\n\n${current}` : `${quoted}\n\n`));
    setFocusSignal((current) => current + 1);
  }

  async function retry(runId: string) {
    if (sending) return;
    const retrySessionId = runSessionsRef.current[runId] ?? activeRun?.session_id ?? sessionIdRef.current;
    setError("");
    setSending(true);
    setStreamText("");
    setEvents([]);
    try {
      const response = await api.retryRun(runId);
      const run = await api.getRun(response.run_id);
      runSessionsRef.current[run.id] = run.session_id;
      setActiveRun(run);
      subscribe(response.run_id);
      void refreshSessions();
    } catch (exc) {
      setSessionError(errorText(exc), retrySessionId);
      setSending(false);
    }
  }

  async function deleteAgentPermanently(id: string) {
    const result = await api.deleteAgentPermanently(id);
    await loadWorkspace();
    if (id === agentId) {
      setSessionId("");
      setMessages([]);
      setActiveRun(null);
      setEvents([]);
      setStreamText("");
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
    // Keep the tracked run while one is still streaming so the composer does
    // not mistake this fresh view for the running session.
    if (!sending) {
      setActiveRun(null);
      setEvents([]);
      setStreamText("");
    }
    setError("");
    setDraft("");
  }

  async function deleteSession(id: string) {
    const forAgent = agentId;
    try {
      await api.deleteSession(id);
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
          setActiveRun(null);
          setEvents([]);
          setStreamText("");
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
    closeStream();
    await api.logout().catch(() => undefined);
    setAuth(false);
    setAgents([]);
    setProviders([]);
    setSkills([]);
    setSkillsErrors([]);
    setSessions([]);
    setMessages([]);
    setAgentId("");
    setSessionId("");
    setActiveRun(null);
    setEvents([]);
    setStreamText("");
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
        settingsActive={view === "settings"}
        memoryActive={view === "memory"}
        skillsActive={view === "skills"}
        toolsActive={view === "tools"}
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
              screenshots={screenshots}
              sessionId={sessionId}
              streamText={visibleStream}
              sending={chatSending}
              error={error}
              activeRun={visibleRun}
              draft={draft}
              canSend={Boolean(agentId)}
              sendBlocked={busyElsewhere}
              agentId={agentId}
              agentName={agent?.name ?? "Agent"}
              focusSignal={focusSignal}
              onDraftChange={setDraft}
              onSend={(attachments) => send(attachments)}
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
          events={events}
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
