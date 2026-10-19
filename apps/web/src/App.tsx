import { useEffect, useRef, useState } from "react";

import { api, errorText } from "./api";
import type { Agent, DrawerTab, Message, Provider, Run, RunEvent, Session } from "./types";
import { ChatPanel } from "./components/ChatPanel";
import { Drawer } from "./components/Drawer";
import { Login } from "./components/Login";
import { NewAgentDialog } from "./components/NewAgentDialog";
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
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const sessionIdRef = useRef("");
  const finishedRuns = useRef<Set<string>>(new Set());

  const agent = agents.find((item) => item.id === agentId);
  const session = sessions.find((item) => item.id === sessionId);
  const chatSending = sending && (!activeRun || activeRun.session_id === sessionId);
  const visibleRun = activeRun && activeRun.session_id === sessionId ? activeRun : null;
  const visibleStream = chatSending ? streamText : "";
  const statusLabel = chatSending ? "running" : visibleRun?.status ?? "idle";

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
      const created = await api.createSession(forAgent);
      setSessions([created]);
      setSessionId(created.id);
      return;
    }
    if (!list.some((item) => item.id === sessionIdRef.current)) {
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

    const lastRunId = [...list].reverse().find((message) => message.run_id)?.run_id;
    if (!lastRunId) {
      setActiveRun(null);
      setEvents([]);
      return;
    }
    const [run, runEvents] = await Promise.all([api.getRun(lastRunId), api.getRunEvents(lastRunId)]);
    setActiveRun(run);
    setEvents(runEvents);
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
    if (!content || !sessionId || sending) return;
    setError("");
    setSending(true);
    setStreamText("");
    setEvents([]);
    setActiveRun(null);
    try {
      const response = await api.sendMessage(sessionId, content);
      setDraft("");
      await loadMessages(sessionId, false);
      const run = await api.getRun(response.run_id);
      setActiveRun(run);
      subscribe(response.run_id);
      void refreshSessions();
    } catch (exc) {
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

  async function newSession() {
    if (!agentId) return;
    try {
      const created = await api.createSession(agentId);
      setSessions((current) => [created, ...current]);
      setSessionId(created.id);
      setMessages([]);
      setActiveRun(null);
      setEvents([]);
      setStreamText("");
      setError("");
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  async function deleteSession(id: string) {
    if (!window.confirm("删除这个会话？")) return;
    try {
      await api.deleteSession(id);
      const list = await api.listSessions(agentId);
      setSessions(list);
      if (id === sessionIdRef.current) {
        if (list.length > 0) {
          setSessionId(list[0].id);
        } else {
          const created = await api.createSession(agentId);
          setSessions([created]);
          setSessionId(created.id);
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

  async function saveAgent(payload: { name: string; instructions: string; provider_id: string }) {
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
  }

  if (auth === null) {
    return <div className="loading-screen">Loading Agent Command Center…</div>;
  }

  if (!auth) {
    return <Login onLogin={() => void bootstrap()} />;
  }

  return (
    <div className={`app-shell ${drawerOpen ? "with-drawer" : ""}`}>
      <Sidebar
        agents={agents}
        agentId={agentId}
        onAgentChange={setAgentId}
        sessions={sessions}
        sessionId={sessionId}
        onSelectSession={setSessionId}
        onNewSession={() => void newSession()}
        onDeleteSession={(id) => void deleteSession(id)}
        onNewAgent={() => setNewAgentOpen(true)}
        onLogout={() => void logout()}
      />

      <main className="chat-column">
        <header className="chat-header">
          <div className="chat-heading">
            <div className="eyebrow">AGENT</div>
            <div className="chat-title">{agent?.name ?? "No Agent"}</div>
            <div className="chat-subtitle">
              {agent ? `${agent.provider_name} · ${agent.model}` : "创建一个 Agent 开始"}
              {session ? ` · ${session.title}` : ""}
            </div>
          </div>
          <div className="header-actions">
            <span className={`status-pill ${statusLabel}`}>{statusLabel}</span>
            <button className="ghost-button" onClick={() => setDrawerOpen((current) => !current)}>
              {drawerOpen ? "收起详情" : "打开详情"}
            </button>
          </div>
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
            messages={messages}
            streamText={visibleStream}
            sending={chatSending}
            error={error}
            activeRun={visibleRun}
            draft={draft}
            canSend={Boolean(sessionId && agentId)}
            agentName={agent?.name ?? "Agent"}
            onDraftChange={setDraft}
            onSend={() => void send()}
            onCancel={() => void cancel()}
          />
        )}
      </main>

      {drawerOpen && (
        <Drawer
          tab={drawerTab}
          onTab={setDrawerTab}
          run={visibleRun}
          events={events}
          agent={agent}
          providers={providers}
          onSaveAgent={saveAgent}
          onSaveProvider={saveProvider}
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
