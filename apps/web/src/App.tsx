import { useEffect, useRef, useState } from "react";

import { api, errorText } from "./api";
import type { Agent, DrawerTab, Message, Provider, Run, RunEvent, Session } from "./types";
import { ChatPanel } from "./components/ChatPanel";
import { Drawer } from "./components/Drawer";
import { Login } from "./components/Login";
import { MemoryPage } from "./components/MemoryPage";
import { NewAgentDialog } from "./components/NewAgentDialog";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";

function terminal(status: string): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export default function App() {
  const [auth, setAuth] = useState<boolean | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [agentId, setAgentId] = useState("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [streamText, setStreamText] = useState("");
  const [error, setError] = useState("");
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("run");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [view, setView] = useState<"chat" | "settings" | "memory">("chat");
  const [focusSignal, setFocusSignal] = useState(0);
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const sessionIdRef = useRef("");
  const finishedRuns = useRef<Set<string>>(new Set());

  const agent = agents.find((item) => item.id === agentId);
  const chatSending = sending && (!activeRun || activeRun.session_id === sessionId);
  const visibleRun = activeRun && activeRun.session_id === sessionId ? activeRun : null;
  const visibleStream = chatSending ? streamText : "";

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    void bootstrap().catch((exc) => {
      setError(errorText(exc));
      setAuth(false);
    });
    return () => closeStream();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!auth || !agentId) {
      setSessions([]);
      setSessionId("");
      return;
    }
    void loadSessions(agentId).catch((exc) => setError(errorText(exc)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, agentId]);

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    void loadMessages(sessionId).catch((exc) => setError(errorText(exc)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  async function bootstrap() {
    const me = await api.me();
    if (!me.authenticated) {
      setAuth(false);
      return;
    }
    setAuth(true);
    await loadWorkspace();
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
    const list = await api.listSessions(forAgent);
    setSessions(list);
    if (list.length === 0) {
      sessionIdRef.current = "";
      setSessionId("");
      setMessages([]);
      setActiveRun(null);
      setEvents([]);
      setStreamText("");
      return;
    }
    if (!list.some((item) => item.id === sessionIdRef.current)) {
      sessionIdRef.current = list[0].id;
      setSessionId(list[0].id);
    }
  }

  async function refreshSessions() {
    if (!agentId) return;
    try {
      setSessions(await api.listSessions(agentId));
    } catch {
      // Keep the current list on a transient refresh failure.
    }
  }

  async function loadMessages(id: string, syncRun = true) {
    const list = await api.listMessages(id);
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
    setActiveRun(run);
    setEvents(runEvents);
    if (run.status === "failed") {
      setError(run.error || "Run failed");
    }
    if ((run.status === "queued" || run.status === "running") && !finishedRuns.current.has(run.id)) {
      setSending(true);
      setStreamText(
        runEvents
          .filter((event) => event.type === "assistant_delta")
          .map((event) => String(event.payload.delta ?? ""))
          .join(""),
      );
      subscribe(run.id);
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

  function subscribe(runId: string) {
    closeStream();
    const source = new EventSource(api.streamUrl(runId));
    eventSourceRef.current = source;
    source.onmessage = (message) => {
      try {
        applyEvent(JSON.parse(message.data) as RunEvent);
      } catch {
        // Ignore malformed frames.
      }
    };
    source.onerror = () => {
      closeStream();
      void pollRun(runId);
    };
  }

  function applyEvent(event: RunEvent) {
    setEvents((current) => (current.some((item) => item.id === event.id) ? current : [...current, event]));
    if (event.type === "assistant_delta") {
      setStreamText((current) => current + String(event.payload.delta ?? ""));
    }
    if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_cancelled") {
      void finishRun(event.run_id);
    }
  }

  async function pollRun(runId: string) {
    closePoll();
    let lastId = 0;
    const tick = async () => {
      try {
        const newEvents = await api.getRunEvents(runId, lastId);
        for (const event of newEvents) {
          lastId = event.id;
          applyEvent(event);
        }
        const run = await api.getRun(runId);
        setActiveRun(run);
        if (terminal(run.status)) {
          closePoll();
          void finishRun(runId);
        }
      } catch (exc) {
        closePoll();
        setError(errorText(exc));
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
      setActiveRun(run);
      setEvents(runEvents);
      setStreamText("");
      if (run.session_id === sessionIdRef.current) {
        await loadMessages(run.session_id, false);
      }
      if (run.status === "failed") {
        setError(run.error || "Run failed");
      }
      await refreshSessions();
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  async function send() {
    const content = draft.trim();
    if (!content || !agentId || sending) return;
    setError("");
    setSending(true);
    setStreamText("");
    setEvents([]);
    setActiveRun(null);
    let createdSessionId = "";
    let sent = false;
    try {
      let targetSessionId = sessionIdRef.current;
      if (!targetSessionId) {
        const created = await api.createSession(agentId);
        createdSessionId = created.id;
        targetSessionId = created.id;
        sessionIdRef.current = created.id;
        setSessions((current) => [created, ...current]);
        setSessionId(created.id);
      }
      const response = await api.sendMessage(targetSessionId, content);
      sent = true;
      setDraft("");
      await loadMessages(targetSessionId, false);
      const run = await api.getRun(response.run_id);
      setActiveRun(run);
      subscribe(response.run_id);
      void refreshSessions();
    } catch (exc) {
      if (createdSessionId && !sent) {
        await api.deleteSession(createdSessionId).catch(() => undefined);
        sessionIdRef.current = "";
        setSessions((current) => current.filter((item) => item.id !== createdSessionId));
        setSessionId("");
      }
      setError(errorText(exc));
      setSending(false);
    }
  }

  async function cancel() {
    if (!activeRun) return;
    try {
      await api.cancelRun(activeRun.id);
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  function quote(text: string) {
    const quoted = text.split("\n").map((line) => `> ${line}`).join("\n");
    setDraft((current) => (current.trim() ? `${quoted}\n\n${current}` : `${quoted}\n\n`));
    setFocusSignal((current) => current + 1);
  }

  async function retry(runId: string) {
    if (sending) return;
    setError("");
    setSending(true);
    setStreamText("");
    setEvents([]);
    try {
      const response = await api.retryRun(runId);
      const run = await api.getRun(response.run_id);
      setActiveRun(run);
      subscribe(response.run_id);
      void refreshSessions();
    } catch (exc) {
      setError(errorText(exc));
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
    sessionIdRef.current = "";
    setSessionId("");
    setMessages([]);
    setActiveRun(null);
    setEvents([]);
    setStreamText("");
    setError("");
    setDraft("");
  }

  async function deleteSession(id: string) {
    if (!window.confirm("删除这个会话？")) return;
    try {
      await api.deleteSession(id);
      const list = await api.listSessions(agentId);
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
      setError(errorText(exc));
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

  async function saveAgent(payload: {
    name: string;
    instructions: string;
    provider_id: string;
    auto_memory: boolean;
  }) {
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
    closeStream();
    await api.logout().catch(() => undefined);
    setAuth(false);
    setAgents([]);
    setProviders([]);
    setSessions([]);
    setMessages([]);
    setAgentId("");
    setSessionId("");
    setActiveRun(null);
    setEvents([]);
    setStreamText("");
    setDraft("");
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
      <Sidebar
        agents={agents}
        agentId={agentId}
        onAgentChange={setAgentId}
        sessions={sessions}
        sessionId={sessionId}
        onSelectSession={(id) => {
          setSessionId(id);
          setView("chat");
        }}
        onNewSession={newSession}
        newSessionActive={view === "chat" && !sessionId}
        onDeleteSession={(id) => void deleteSession(id)}
        onOpenSettings={() => setView("settings")}
        onOpenMemory={() => setView("memory")}
        settingsActive={view === "settings"}
        memoryActive={view === "memory"}
        onLogout={() => void logout()}
      />

      {view === "memory" ? (
        <MemoryPage
          agents={agents}
          agentId={agentId}
          onClose={() => setView("chat")}
        />
      ) : view === "settings" ? (
        <SettingsPage
          agents={agents}
          agentId={agentId}
          providers={providers}
          onSelectAgent={setAgentId}
          onSaveAgent={saveAgent}
          onSaveProvider={saveProvider}
          onCreateProvider={createProvider}
          onNewAgent={() => setNewAgentOpen(true)}
          onDeleteAgent={deleteAgentPermanently}
          onClose={() => setView("chat")}
        />
      ) : (
        <main className="chat-column">
          {!drawerOpen && (
            <button
              className="drawer-fab"
              type="button"
              title="打开详情"
              aria-label="打开详情"
              onClick={() => setDrawerOpen(true)}
            >
              <svg
                viewBox="0 0 24 24"
                width="16"
                height="16"
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
          )}
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
              streamText={visibleStream}
              sending={chatSending}
              error={error}
              activeRun={visibleRun}
              draft={draft}
              canSend={Boolean(agentId)}
              agentName={agent?.name ?? "Agent"}
              focusSignal={focusSignal}
              onDraftChange={setDraft}
              onSend={() => void send()}
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
