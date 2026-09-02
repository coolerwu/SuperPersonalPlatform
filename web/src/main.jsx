import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Cpu,
  ExternalLink,
  FileJson,
  FileText,
  FolderOpen,
  FolderTree,
  Globe2,
  Keyboard,
  LogOut,
  Play,
  Plus,
  RefreshCw,
  Save,
  Send,
  Settings,
  SlidersHorizontal,
  Smartphone,
  TerminalSquare,
  Trash2,
  XCircle,
} from "lucide-react";
import { AgentConfigEditor, ConfigVisualEditor, ProviderConfigEditor, parseConfigDraft } from "./configEditor.jsx";
import "./styles.css";

const NAV_ITEMS = [
  { id: "chat", path: "/chat", label: "Chat", icon: Send },
  { id: "runs", path: "/runs", label: "Runs", icon: Play },
  { id: "workspace", path: "/workspace", label: "工作目录", icon: FolderTree },
  { id: "config", path: "/config", label: "配置", icon: SlidersHorizontal },
  { id: "schedules", path: "/schedules", label: "定时任务", icon: Clock3 },
  { id: "browser", path: "/browser", label: "浏览器", icon: Globe2 },
  { id: "wechat", path: "/wechat", label: "微信", icon: Smartphone },
  { id: "system", path: "/system", label: "运维", icon: Settings },
];

const CONFIG_SECTIONS = [
  { id: "config", path: "/config", label: "基础配置", icon: SlidersHorizontal },
  { id: "providers", path: "/providers", label: "Providers", icon: Cpu },
  { id: "agent-config", path: "/agent-config", label: "Agents", icon: Bot },
];

const WORKSPACE_TREE = [
  ["workspace/sessions/index.json", "长期会话索引，微信和未来渠道共用"],
  ["workspace/sessions/{session_id}/state.json", "长期会话身份、Agent 和最近 run"],
  ["workspace/sessions/{session_id}/messages.jsonl", "对话历史，DeepAgent 执行前读取"],
  ["workspace/sessions/{session_id}/runs.jsonl", "该会话关联的 run 列表"],
  ["workspace/runs/index.json", "Run 摘要与状态索引"],
  ["workspace/runs/{run_id}/input.json", "创建时输入与 Agent/Context 快照"],
  ["workspace/runs/{run_id}/state.json", "当前状态、更新时间、事件序号"],
  ["workspace/runs/{run_id}/events.jsonl", "前端轮询读取的事件流"],
  ["workspace/runs/{run_id}/partial.json", "DeepAgent stream 运行中的聚合输出"],
  ["workspace/runs/{run_id}/result.json", "DeepAgent 最终输出"],
  ["workspace/runs/{run_id}/delivery.json", "微信等渠道投递状态"],
  ["workspace/schedules/index.json", "统一调度索引，WebDAV 同步和未来 Agent 定时任务共用"],
  ["workspace/schedules/{schedule_id}/definition.json", "调度定义：类型、触发器和任务参数"],
  ["workspace/schedules/{schedule_id}/state.json", "调度运行状态、下次触发时间和最近错误"],
  ["workspace/context/knowledge/files/", "默认 Context 知识目录，search_context / write_context 使用"],
  ["workspace/context/webdav/", "坚果云 WebDAV 单同步根目录的本地缓存"],
  ["workspace/channels/wechat/sessions/{account_id}.json", "微信登录态，不作为聊天历史"],
  ["workspace/logs/platform-YYYY-MM-DD.log", "系统日志"],
];

const RUN_POLL_INTERVAL_MS = 60_000;
const RUN_DETAIL_POLL_INTERVAL_MS = 2_000;

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || "请求失败");
    error.status = response.status;
    throw error;
  }
  return data;
}

function sameSnapshot(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function replaceWhenChanged(current, next) {
  return sameSnapshot(current, next) ? current : next;
}

function hasField(value, field) {
  return Object.prototype.hasOwnProperty.call(value || {}, field);
}

function mergeRunSnapshot(current, incoming) {
  if (!incoming) return current || null;
  if (!current || current.run_id !== incoming.run_id) return incoming;

  const merged = { ...current, ...incoming };
  for (const field of ["input", "state", "result", "delivery"]) {
    merged[field] = hasField(incoming, field) ? incoming[field] : current[field];
  }
  return replaceWhenChanged(current, merged);
}

function routeFromPath(pathname) {
  if (pathname === "/" || pathname === "/agents" || pathname === "/login") return "runs";
  const match = [...NAV_ITEMS, ...CONFIG_SECTIONS].find((item) => item.path === pathname);
  return match?.id || "runs";
}

function pathForPage(page) {
  return [...NAV_ITEMS, ...CONFIG_SECTIONS].find((item) => item.id === page)?.path || "/runs";
}

function navItemIsActive(item, page) {
  if (item.id === page) return true;
  return item.id === "config" && CONFIG_SECTIONS.some((section) => section.id === page);
}

function LoginPage({ onLogin }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      onLogin();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={submit}>
        <div className="cube-mark">
          <Bot size={28} />
        </div>
        <h1>DeepAgent Console</h1>
        <p>后端运行、落盘状态、前端轮询。</p>
        <label htmlFor="token">访问 Token</label>
        <input
          id="token"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          type="password"
          placeholder="config.yaml auth.token"
        />
        {error ? <p className="error">{error}</p> : null}
        <button className="primary" disabled={submitting || !token.trim()}>
          {submitting ? "进入中" : "进入控制台"}
          <Send size={16} />
        </button>
      </form>
    </main>
  );
}

function App() {
  const [authenticated, setAuthenticated] = useState(null);
  const [page, setPage] = useState(routeFromPath(window.location.pathname));

  useEffect(() => {
    api("/api/auth/me")
      .then((data) => setAuthenticated(Boolean(data.authenticated)))
      .catch(() => setAuthenticated(false));
  }, []);

  useEffect(() => {
    function onPopState() {
      setPage(routeFromPath(window.location.pathname));
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function navigate(nextPage) {
    setPage(nextPage);
    const nextPath = pathForPage(nextPage);
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, "", nextPath);
    }
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    setAuthenticated(false);
    window.history.replaceState({}, "", "/login");
  }

  if (authenticated === null) {
    return <div className="loading">Loading...</div>;
  }
  if (!authenticated) {
    return <LoginPage onLogin={() => setAuthenticated(true)} />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="cube-mark small">
            <Bot size={18} />
          </div>
          <span>DeepAgent</span>
        </div>
        <nav>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button key={item.id} className={navItemIsActive(item, page) ? "active" : ""} onClick={() => navigate(item.id)}>
                <Icon size={16} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <span>workspace</span>
          <strong>.super-personal-platform</strong>
        </div>
        <button className="logout" onClick={logout}>
          <LogOut size={16} />
          退出
        </button>
      </aside>
      <main className="content">
        {page === "chat" ? <ChatPage /> : null}
        {page === "runs" ? <RunsPage /> : null}
        {page === "workspace" ? <WorkspacePage /> : null}
        {page === "config" ? <ConfigPage onNavigate={navigate} /> : null}
        {page === "providers" ? <ProviderPage onNavigate={navigate} /> : null}
        {page === "agent-config" ? <AgentConfigPage onNavigate={navigate} /> : null}
        {page === "schedules" ? <SchedulesPage /> : null}
        {page === "browser" ? <BrowserProfilesPage /> : null}
        {page === "wechat" ? <WechatPage /> : null}
        {page === "system" ? <SystemPage onNavigate={navigate} /> : null}
      </main>
    </div>
  );
}

function ChatPage() {
  const [agents, setAgents] = useState([]);
  const [agentId, setAgentId] = useState("");
  const [session, setSession] = useState(null);
  const [chatSessions, setChatSessions] = useState([]);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [activeRunId, setActiveRunId] = useState("");
  const [error, setError] = useState("");
  const messagesRef = useRef(null);
  const chatEventSeqRef = useRef(0);
  const chatRunContentRef = useRef("");

  async function loadAgents() {
    const data = await api("/api/workspace/read", {
      method: "POST",
      body: JSON.stringify({ path: "config.yaml" }),
    });
    const parsed = parseConfigDraft(data.content || "");
    const nextAgents = parsed.config?.agents?.definitions || [];
    setAgents(nextAgents);
    if (!agentId && nextAgents[0]?.id) {
      setAgentId(nextAgents[0].id);
    }
  }

  async function loadSession(nextAgentId = agentId) {
    const data = await api("/api/chat/session", {
      method: "POST",
      body: JSON.stringify({ agent_id: nextAgentId || "" }),
    });
    const normalizedMessages = normalizeChatMessages(data.messages || []);
    setSession(data.session || null);
    setMessages(normalizedMessages);
    hydrateChatRunSnapshots(normalizedMessages)
      .then((hydratedMessages) => {
        setMessages((current) => mergeHydratedChatMessages(current, normalizedMessages, hydratedMessages));
      })
      .catch(() => {});
    if (data.session?.agent_id) {
      setAgentId(data.session.agent_id);
    }
    loadChatSessions(data.session?.agent_id || nextAgentId || "").catch(() => {});
  }

  async function loadChatSessions(nextAgentId = agentId) {
    const data = await api(`/api/chat/sessions?agent_id=${encodeURIComponent(nextAgentId || "")}`);
    setChatSessions(data.sessions || []);
  }

  useEffect(() => {
    loadAgents().catch((exc) => setError(exc.message));
    loadSession("").catch((exc) => setError(exc.message));
  }, []);

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
  }, [messages, activeRunId]);

  useEffect(() => {
    if (!activeRunId) return undefined;
    let cancelled = false;
    const runId = activeRunId;

    async function finalizeRun(status) {
      let content = "";
      let failed = status === "failed";
      try {
        const run = await api(`/api/runs/${runId}`);
        if (cancelled) return;
        failed = runStatus(run) === "failed";
        content = failed ? run.result?.error?.message || run.state?.error?.message || "运行失败" : run.result?.content || "";
      } catch (exc) {
        if (!cancelled) setError(exc.message);
      }
      if (cancelled) return;
      setMessages((current) =>
        upsertChatAssistantMessage(current, runId, {
          content: content || chatRunContentRef.current,
          streaming: false,
          failed,
          thinkingAppend: [failed ? "运行失败，已停止生成正文" : "已完成，正文已生成"],
          thinkingCollapsed: true,
        }),
      );
      setActiveRunId("");
      if (session?.session_id) {
        api(`/api/chat/sessions/${encodeURIComponent(session.session_id)}/messages`)
          .then((data) => {
            if (!cancelled) {
              setMessages((current) => carryChatAssistantRuntimeState(normalizeChatMessages(data.messages || []), current, runId));
            }
          })
          .catch(() => {});
      }
    }

    async function poll() {
      try {
        const data = await api(`/api/runs/${runId}/events?after=${chatEventSeqRef.current}`);
        if (cancelled) return;
        const events = data.events || [];
        let completedStatus = "";
        let nextContent = chatRunContentRef.current;
        const thinkingUpdates = [];
        for (const event of events) {
          chatEventSeqRef.current = Math.max(chatEventSeqRef.current, Number(event.seq || 0));
          const thinkingText = runEventThinkingText(event);
          if (thinkingText) {
            thinkingUpdates.push(thinkingText);
          }
          if (event.type === "assistant_delta") {
            const delta = String(event.payload?.delta || "");
            if (delta) {
              nextContent += delta;
            }
          }
          if (event.type === "failed" || event.type === "completed") {
            completedStatus = event.type;
          }
        }
        if (nextContent !== chatRunContentRef.current || thinkingUpdates.length > 0) {
          chatRunContentRef.current = nextContent;
          setMessages((current) =>
            upsertChatAssistantMessage(current, runId, {
              content: nextContent,
              streaming: true,
              thinkingAppend: thinkingUpdates,
              thinkingCollapsed: false,
            }),
          );
        }
        if (completedStatus) {
          await finalizeRun(completedStatus);
        }
      } catch (exc) {
        if (!cancelled) setError(exc.message);
      }
    }
    poll();
    const timer = window.setInterval(poll, 1_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeRunId, session?.session_id]);

  async function sendMessage() {
    const content = draft.trim();
    if (!content || activeRunId) return;
    setDraft("");
    setError("");
    setMessages((current) => [
      ...current,
      {
        id: `local_user_${Date.now()}`,
        role: "user",
        content,
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      const data = await api("/api/chat/messages", {
        method: "POST",
        body: JSON.stringify({
          content,
          agent_id: agentId || "",
          session_id: session?.session_id || "",
        }),
      });
      const runId = data.run?.run_id || "";
      setSession(data.session || session);
      loadChatSessions(data.session?.agent_id || agentId || "").catch(() => {});
      if (runId) {
        chatEventSeqRef.current = 0;
        chatRunContentRef.current = "";
        setMessages((current) =>
          upsertChatAssistantMessage(current, runId, {
            content: "",
            streaming: true,
            thinking: ["等待 DeepAgent 响应"],
            thinkingCollapsed: false,
          }),
        );
        setActiveRunId(runId);
      }
    } catch (exc) {
      setMessages((current) => [
        ...current,
        {
          id: `local_error_${Date.now()}`,
          role: "assistant",
          content: exc.message || "发送失败",
          failed: true,
          created_at: new Date().toISOString(),
        },
      ]);
      setError(exc.message);
    }
  }

  async function newSession() {
    if (activeRunId) return;
    setError("");
    const data = await api("/api/chat/session/new", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId || "" }),
    });
    chatEventSeqRef.current = 0;
    chatRunContentRef.current = "";
    setSession(data.session || null);
    setMessages([]);
    loadChatSessions(data.session?.agent_id || agentId || "").catch(() => {});
  }

  async function changeSession(selector) {
    if (!selector || activeRunId) return;
    setError("");
    const data = await api("/api/chat/session/change", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId || "", selector }),
    });
    chatEventSeqRef.current = 0;
    chatRunContentRef.current = "";
    const normalizedMessages = normalizeChatMessages(data.messages || []);
    setSession(data.session || null);
    setMessages(normalizedMessages);
    hydrateChatRunSnapshots(normalizedMessages)
      .then((hydratedMessages) => {
        setMessages((current) => mergeHydratedChatMessages(current, normalizedMessages, hydratedMessages));
      })
      .catch(() => {});
    loadChatSessions(data.session?.agent_id || agentId || "").catch(() => {});
  }

  function changeAgent(nextAgentId) {
    setAgentId(nextAgentId);
    setActiveRunId("");
    chatEventSeqRef.current = 0;
    chatRunContentRef.current = "";
    setChatSessions([]);
    loadSession(nextAgentId).catch((exc) => setError(exc.message));
  }

  return (
    <section className="console-screen chat-screen">
      <section className="panel chat-panel">
        <div className="chat-toolbar">
          <div>
            <span className="section-label">Web Chat</span>
            <h2>DeepAgent 对话</h2>
          </div>
          <div className="chat-actions">
            <select value={agentId} onChange={(event) => changeAgent(event.target.value)} disabled={Boolean(activeRunId)}>
              {agents.length === 0 ? <option value="">default</option> : null}
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name || agent.id}
                </option>
              ))}
            </select>
            <select
              value={session?.session_id || ""}
              onChange={(event) => changeSession(event.target.value).catch((exc) => setError(exc.message))}
              disabled={Boolean(activeRunId) || chatSessions.length === 0}
              title="切换历史会话"
            >
              {chatSessions.length === 0 ? <option value="">当前会话</option> : null}
              {chatSessions.map((item, index) => (
                <option key={item.session_id} value={item.session_id}>
                  {formatSessionOption(item, index)}
                </option>
              ))}
            </select>
            <button className="chat-secondary-button" onClick={newSession} disabled={Boolean(activeRunId)}>
              新会话
            </button>
          </div>
        </div>

        <div className="chat-messages" ref={messagesRef}>
          {messages.length === 0 ? (
            <div className="chat-empty">
              <TerminalSquare size={30} />
              <strong>开始一次页面对话</strong>
              <span>消息会进入长期 session；运行中输出会在这里实时刷新。</span>
            </div>
          ) : null}
          {messages.map((message) => (
            <div
              key={message.id}
              className={`chat-message ${message.role === "user" ? "user" : "assistant"} ${message.failed ? "failed" : ""}`}
            >
              <div className="chat-bubble">
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
                {message.streaming ? <small>streaming</small> : null}
              </div>
            </div>
          ))}
        </div>

        {error ? <div className="error chat-error">{error}</div> : null}
        <div className="chat-composer">
          <textarea
            value={draft}
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
              }
            }}
          />
          <button className="primary chat-send-button" onClick={sendMessage} disabled={!draft.trim() || Boolean(activeRunId)}>
            <Send size={18} />
            发送
          </button>
        </div>
      </section>

      <aside className="status-rail chat-rail">
        <RailCard title="当前会话" status={activeRunId ? "运行中" : "就绪"} tone={activeRunId ? "cyan" : "green"}>
          <RailRow label="Agent" value={agentId || "-"} />
          <RailRow label="Session" value={session?.session_id || "-"} />
          <RailRow label="消息数" value={session?.message_count ?? messages.length} />
          <RailRow label="当前 Run" value={activeRunId || "-"} />
        </RailCard>
        <RailCard title="落盘路径" status="Context" tone="blue">
          <RailRow label="会话" value={session?.session_id ? `sessions/${session.session_id}` : "sessions/"} />
          <RailRow label="检查点" value="sessions/checkpoints.sqlite" />
          <RailRow label="流式预览" value="runs/{run_id}/partial.json" />
        </RailCard>
      </aside>
    </section>
  );
}

function RunsPage() {
  const [runs, setRuns] = useState([]);
  const [activeRun, setActiveRun] = useState(null);
  const [events, setEvents] = useState([]);

  const counts = useMemo(() => {
    return runs.reduce(
      (acc, run) => {
        const status = runStatus(run);
        acc.total += 1;
        acc[status] = (acc[status] || 0) + 1;
        return acc;
      },
      { total: 0, queued: 0, running: 0, completed: 0, failed: 0 },
    );
  }, [runs]);

  async function load() {
    const data = await api("/api/runs");
    const nextRuns = data.runs || [];
    setRuns((current) => replaceWhenChanged(current, nextRuns));
    setActiveRun((current) => {
      if (nextRuns.length === 0) return current ? null : current;
      if (!current?.run_id) return nextRuns[0];
      const nextSummary = nextRuns.find((run) => run.run_id === current.run_id);
      return nextSummary ? mergeRunSnapshot(current, nextSummary) : nextRuns[0];
    });
  }

  useEffect(() => {
    load();
    const timer = window.setInterval(load, RUN_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const runId = activeRun?.run_id;
    if (!runId) {
      setEvents((current) => (current.length === 0 ? current : []));
      return undefined;
    }
    let cancelled = false;
    async function poll() {
      const [run, eventData] = await Promise.all([
        api(`/api/runs/${runId}`),
        api(`/api/runs/${runId}/events`),
      ]);
      if (cancelled) return;
      setActiveRun((current) => (current?.run_id === run.run_id ? mergeRunSnapshot(current, run) : current));
      setEvents((current) => replaceWhenChanged(current, eventData.events || []));
    }
    poll();
    const timer = window.setInterval(poll, RUN_DETAIL_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeRun?.run_id]);

  function selectRun(run) {
    if (run.run_id !== activeRun?.run_id) {
      setEvents([]);
    }
    setActiveRun((current) => mergeRunSnapshot(current, run));
  }

  return (
    <section className="console-screen runs-screen">
      <div className="metrics-row">
        <Metric label="全部 Run" value={counts.total} tone="blue" />
        <Metric label="运行中" value={counts.running} tone="cyan" />
        <Metric label="已完成" value={counts.completed} tone="green" />
        <Metric label="失败" value={counts.failed} tone="red" />
      </div>

      <div className="runs-grid">
        <RunIndex runs={runs} activeRunId={activeRun?.run_id} onSelect={selectRun} onRefresh={load} />
        <RunDetail run={activeRun} events={events} />
        <StatusRail runs={runs} />
      </div>
    </section>
  );
}

function Metric({ label, value, tone }) {
  return (
    <div className={`metric metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function RunIndex({ runs, activeRunId, onSelect, onRefresh }) {
  return (
    <section className="panel run-index">
      <div className="panel-title">
        <div>
          <span>Runs</span>
          <small>workspace/runs/index.json</small>
        </div>
        <button className="icon-button" onClick={onRefresh} title="刷新">
          <RefreshCw size={15} />
        </button>
      </div>
      <div className="run-table head">
        <span>Run ID</span>
        <span>状态</span>
        <span>Agent</span>
        <span>更新时间</span>
      </div>
      <div className="run-table-body">
        {runs.length === 0 ? <div className="empty-state">暂无 runs。收到任务后会写入 index.json。</div> : null}
        {runs.map((run) => (
          <button
            key={run.run_id}
            className={`run-table row ${activeRunId === run.run_id ? "selected" : ""}`}
            onClick={() => onSelect(run)}
          >
            <span className="mono">{run.run_id}</span>
            <Status status={runStatus(run)} />
            <span>{run.agent_id || "-"}</span>
            <span>{formatTime(run.updated_at || run.created_at)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function RunDetail({ run, events }) {
  if (!run) {
    return (
      <section className="panel run-detail empty-detail">
        <TerminalSquare size={32} />
        <strong>选择一个 run</strong>
        <span>前端不会执行 Agent，只轮询磁盘状态和事件。</span>
      </section>
    );
  }
  const input = run.input || {};
  const state = run.state || {};
  const partial = run.partial?.content || "";
  const result = run.result?.content || partial || run.result?.error?.message || "";
  const runId = run.run_id || input.run_id;
  const sessionId = input.session_id || state.session_id || run.session_id || "";
  const status = runStatus(run);
  const resultLabel = run.result?.content ? "结果预览" : partial ? "正在生成" : "结果预览";

  return (
    <section className="panel run-detail">
      <div className="detail-header">
        <div>
          <span className="section-label">Run 详情</span>
          <h2>{runId}</h2>
        </div>
        <Status status={status} />
      </div>
      <div className="kv-grid">
        <Kv label="Agent" value={input.agent_id || run.agent_id || "-"} />
        <Kv label="来源" value={input.source || run.source || "api"} />
        <Kv label="Session" value={sessionId || "-"} />
        <Kv label="创建时间" value={formatTime(input.created_at || run.created_at)} />
        <Kv label="事件序号" value={state.seq ?? run.seq ?? 0} />
      </div>
      <PathBox label="工作目录" value={`workspace/runs/${runId}/`} />
      <PathBox label="状态文件" value={`workspace/runs/${runId}/state.json`} />
      <PathBox label="事件文件" value={`workspace/runs/${runId}/events.jsonl`} />
      {sessionId ? <PathBox label="会话历史" value={`workspace/sessions/${sessionId}/messages.jsonl`} /> : null}

      <div className="tabs-line">
        <span className="active">事件</span>
        <span>结果</span>
        <span>状态</span>
      </div>
      <div className="event-list">
        {events.length === 0 ? <div className="empty-state">暂无事件。</div> : null}
        {events.map((event) => (
          <div key={event.seq} className="event-row">
            <span className="event-dot" />
            <time>{formatTime(event.created_at)}</time>
            <strong>{event.type}</strong>
            <code>{JSON.stringify(event.payload)}</code>
          </div>
        ))}
      </div>
      <div className="result-preview">
        <div>
          <span className="section-label">{resultLabel}</span>
          <small>
            {partial && !run.result?.content
              ? `workspace/runs/${runId}/partial.json`
              : `workspace/runs/${runId}/result.json`}
          </small>
        </div>
        <pre>{result || "暂无结果"}</pre>
      </div>
    </section>
  );
}

function Kv({ label, value }) {
  return (
    <div className="kv">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PathBox({ label, value }) {
  return (
    <div className="path-box">
      <span>{label}</span>
      <code>{value}</code>
    </div>
  );
}

function StatusRail({ runs }) {
  return (
    <aside className="status-rail">
      <RailCard title="后端服务" status="运行中" tone="green">
        <RailRow label="API" value="/api/runs" />
        <RailRow label="模式" value="polling" />
      </RailCard>
      <RailCard title="微信账号" status="按账号管理" tone="blue">
        <RailRow label="入口" value="/api/channels/wechat" />
        <RailRow label="登录态" value="channels/wechat/sessions" />
      </RailCard>
      <RailCard title="Nutstore WebDAV" status="Context" tone="green">
        <RailRow label="地址" value="dav.jianguoyun.com" />
        <RailRow label="根目录" value="config.yaml" />
      </RailCard>
      <RailCard title="运行概览" status={`${runs.length} runs`} tone="neutral">
        <RailRow label="索引" value="runs/index.json" />
        <RailRow label="会话" value="sessions/index.json" />
      </RailCard>
    </aside>
  );
}

function RailCard({ title, status, tone, children }) {
  return (
    <section className="rail-card">
      <div className="rail-title">
        <strong>{title}</strong>
        <span className={`rail-status ${tone}`}>{status}</span>
      </div>
      {children}
    </section>
  );
}

function RailRow({ label, value }) {
  return (
    <div className="rail-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function WorkspacePage() {
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState([]);
  const [activeFile, setActiveFile] = useState(null);
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function load(nextPath = path) {
    setError("");
    try {
      const data = await api("/api/workspace/list", {
        method: "POST",
        body: JSON.stringify({ path: nextPath }),
      });
      setPath(data.path || "");
      setEntries(data.entries || []);
    } catch (err) {
      setError(err.message);
    }
  }

  async function openEntry(entry) {
    setMessage("");
    setError("");
    if (entry.type === "directory") {
      setActiveFile(null);
      setDraft("");
      await load(entry.path);
      return;
    }
    try {
      const file = await api("/api/workspace/read", {
        method: "POST",
        body: JSON.stringify({ path: entry.path }),
      });
      setActiveFile(file);
      setDraft(file.content || "");
    } catch (err) {
      setError(err.message);
    }
  }

  async function openPath(nextPath) {
    await openEntry({ type: "file", path: nextPath });
  }

  async function saveFile() {
    if (!activeFile) return;
    setError("");
    setMessage("");
    try {
      const data = await api("/api/workspace/write", {
        method: "PUT",
        body: JSON.stringify({ path: activeFile.path, content: draft }),
      });
      setActiveFile(data.file);
      setDraft(data.file.content || "");
      setMessage(data.message || "已保存");
      await load(path);
    } catch (err) {
      setError(err.message);
    }
  }

  async function deleteEntry(entry) {
    if (!entry.deletable) return;
    const confirmed = window.confirm(`删除 workspace/${entry.path}？`);
    if (!confirmed) return;
    setError("");
    setMessage("");
    try {
      const data = await api("/api/workspace/delete", {
        method: "POST",
        body: JSON.stringify({ path: entry.path }),
      });
      if (activeFile?.path === entry.path || activeFile?.path?.startsWith(`${entry.path}/`)) {
        setActiveFile(null);
        setDraft("");
      }
      setMessage(data.message || "已删除");
      await load(path);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    load("");
  }, []);

  const breadcrumbs = path ? path.split("/") : [];
  const dirty = activeFile && draft !== activeFile.content;

  return (
    <section className="console-screen workspace-screen">
      <div className="workspace-header">
        <div>
          <span className="section-label">工作目录</span>
          <h1>workspace browser</h1>
        </div>
        <Status status="落盘运行" />
      </div>

      <div className="workspace-toolbar panel">
        <div className="workspace-breadcrumbs" aria-label="workspace path">
          <button className={path === "" ? "active-soft" : ""} onClick={() => load("")}>
            workspace
          </button>
          {breadcrumbs.map((part, index) => {
            const nextPath = breadcrumbs.slice(0, index + 1).join("/");
            return (
              <button key={nextPath} onClick={() => load(nextPath)}>
                <ChevronRight size={14} />
                {part}
              </button>
            );
          })}
        </div>
        <div className="workspace-quick-actions" aria-label="workspace shortcuts">
          <button onClick={() => openPath("config.yaml")}>
            <FileText size={15} />
            config.yaml
          </button>
          <button onClick={() => openPath("runs/index.json")}>
            <FileJson size={15} />
            runs/index.json
          </button>
          <button onClick={() => openPath("sessions/index.json")}>
            <FileJson size={15} />
            sessions/index.json
          </button>
          <button className="icon-button" onClick={() => load(path)} title="刷新目录">
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      <div className={`workspace-feedback ${message || error ? "has-feedback" : ""}`}>
        {message ? <p className="ok">{message}</p> : null}
        {error ? <p className="error">{error}</p> : null}
      </div>

      <div className="workspace-file-layout">
        <section className="panel file-browser">
          <div className="panel-title">
            <div>
              <span>文件</span>
              <small>{path ? `workspace/${path}` : "workspace/"}</small>
            </div>
          </div>
          <div className="file-list">
            {path ? (
              <button className="file-parent-row" onClick={() => load(path.split("/").slice(0, -1).join("/"))}>
                <FolderOpen size={16} />
                <span>..</span>
                <small>上级目录</small>
              </button>
            ) : null}
            {entries.length === 0 ? <div className="empty-state">当前目录为空。</div> : null}
            {entries.map((entry) => (
              <div
                key={entry.path}
                className={`file-row ${activeFile?.path === entry.path ? "selected" : ""}`}
              >
                <button className="file-open-button" onClick={() => openEntry(entry)}>
                  {entry.type === "directory" ? <FolderOpen size={16} /> : <FileText size={16} />}
                  <span>{entry.name}</span>
                  <small>{entry.type === "directory" ? "directory" : formatBytes(entry.size)}</small>
                  <time>{formatTime(entry.modified_at * 1000)}</time>
                </button>
                {entry.deletable ? (
                  <button className="icon-button delete-button" onClick={() => deleteEntry(entry)} title="删除">
                    <Trash2 size={14} />
                  </button>
                ) : (
                  <span className="protected-label">固定</span>
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="panel file-editor">
          <div className="panel-title">
            <div>
              <span>{activeFile?.path || "选择文件"}</span>
              <small>
                {activeFile ? `${formatBytes(activeFile.size)} · ${activeFile.editable ? "可编辑" : "只读"}` : "支持 UTF-8 文本文件"}
              </small>
            </div>
            <button className="primary" onClick={saveFile} disabled={!activeFile?.editable || !dirty}>
              <Save size={15} />
              保存
            </button>
          </div>
          {activeFile ? (
            <textarea
              className="workspace-editor"
              value={draft}
              readOnly={!activeFile.editable}
              onChange={(event) => setDraft(event.target.value)}
              spellCheck={false}
            />
          ) : (
            <div className="workspace-empty-editor">
              <TerminalSquare size={30} />
              <strong>打开 workspace 内的文件</strong>
              <span>Run 状态、事件、结果、Context 和 config.yaml 都可以在这里查看；可编辑文本会启用保存。</span>
            </div>
          )}
        </section>
      </div>

      <section className="panel workspace-map">
        <div className="panel-title">
          <div>
            <span>约定目录</span>
            <small>后端重启后从这些落盘文件恢复任务状态</small>
          </div>
        </div>
        <div className="tree-list">
          {WORKSPACE_TREE.map(([treePath, description]) => (
            <button className="tree-row" key={treePath} onClick={() => openPath(treePath.replace("workspace/", ""))}>
              <FileJson size={15} />
              <code>{treePath}</code>
              <span>{description}</span>
            </button>
          ))}
        </div>
      </section>
    </section>
  );
}

function ConfigPage({ onNavigate }) {
  return <ConfigBackedPage activeSection="config" panelTitle="系统配置" Editor={ConfigVisualEditor} onNavigate={onNavigate} />;
}

function ProviderPage({ onNavigate }) {
  return <ConfigBackedPage activeSection="providers" panelTitle="Provider 配置" Editor={ProviderConfigEditor} onNavigate={onNavigate} />;
}

function AgentConfigPage({ onNavigate }) {
  return <ConfigBackedPage activeSection="agent-config" panelTitle="Agent 配置" Editor={AgentConfigEditor} onNavigate={onNavigate} />;
}

function ConfigBackedPage({ activeSection, panelTitle, Editor, onNavigate }) {
  const [configFile, setConfigFile] = useState(null);
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function loadConfig() {
    setError("");
    try {
      const file = await api("/api/workspace/read", {
        method: "POST",
        body: JSON.stringify({ path: "config.yaml" }),
      });
      setConfigFile(file);
      setDraft(file.content || "");
    } catch (err) {
      setError(err.message);
    }
  }

  async function saveConfig() {
    setError("");
    setMessage("");
    try {
      const data = await api("/api/workspace/write", {
        method: "PUT",
        body: JSON.stringify({ path: "config.yaml", content: draft }),
      });
      setConfigFile(data.file);
      setDraft(data.file.content || "");
      setMessage(data.message || "config.yaml 已保存");
    } catch (err) {
      setError(err.message);
    }
  }

  async function syncWebdavContext() {
    setError("");
    setMessage("");
    try {
      const data = await api("/api/system/webdav-context/sync", { method: "POST" });
      setMessage(data.message || "WebDAV 已同步");
    } catch (err) {
      setError(err.message);
    }
  }

  async function testWebdavContext() {
    setError("");
    setMessage("");
    try {
      const data = await api("/api/system/webdav-context/test", {
        method: "POST",
        body: JSON.stringify({ content: draft }),
      });
      const detail = data.target_url ? `（${data.status_code} · ${data.target_url}）` : "";
      if (data.ok) {
        setMessage(`${data.message || "WebDAV 连接成功"}${detail}`);
      } else {
        setError(`${data.message || "WebDAV 连接失败"}${detail}`);
      }
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    loadConfig();
  }, []);

  const dirty = configFile && draft !== configFile.content;

  return (
    <section className="console-screen config-screen">
      <div className="workspace-header">
        <div>
          <span className="section-label">配置</span>
          <h1>config.yaml</h1>
        </div>
        <Status status="落盘运行" />
      </div>

      {message ? <p className="ok">{message}</p> : null}
      {error ? <p className="error">{error}</p> : null}

      <section className="panel file-editor config-page-panel">
        <div className="config-page-toolbar">
          <div className="config-tabs" role="tablist" aria-label="配置栏目">
            {CONFIG_SECTIONS.map((section) => {
              const Icon = section.icon;
              return (
                <button
                  key={section.id}
                  type="button"
                  role="tab"
                  aria-selected={activeSection === section.id}
                  className={activeSection === section.id ? "active" : ""}
                  onClick={() => onNavigate(section.id)}
                >
                  <Icon size={14} />
                  {section.label}
                </button>
              );
            })}
          </div>
          <div className="config-page-actions">
            <button className="icon-button" onClick={loadConfig} title="重新读取">
              <RefreshCw size={15} />
            </button>
            <button className="primary config-save-button" onClick={saveConfig} disabled={!configFile?.editable || !dirty}>
              <Save size={15} />
              保存
            </button>
          </div>
        </div>
        <div className="panel-title">
          <div>
            <span>{panelTitle}</span>
            <small>{configFile ? `${formatBytes(configFile.size)} · workspace/config.yaml` : "读取中"}</small>
          </div>
        </div>
        {configFile ? (
          <Editor
            draft={draft}
            onChange={setDraft}
            readOnly={!configFile.editable}
            onSyncWebdav={activeSection === "config" ? syncWebdavContext : undefined}
            onTestWebdav={activeSection === "config" ? testWebdavContext : undefined}
          />
        ) : (
          <div className="workspace-empty-editor">
            <TerminalSquare size={30} />
            <strong>正在读取 config.yaml</strong>
            <span>这里读取 active workspace 的 config.yaml，保存时仍走后端配置校验。</span>
          </div>
        )}
      </section>
    </section>
  );
}

function ModelLine({ title, text }) {
  return (
    <div className="model-line">
      <ChevronRight size={16} />
      <strong>{title}</strong>
      <span>{text}</span>
    </div>
  );
}

function WechatPage() {
  const [accounts, setAccounts] = useState([]);
  const [agents, setAgents] = useState([]);
  const [activeId, setActiveId] = useState("");
  const [creating, setCreating] = useState(false);
  const [newAccount, setNewAccount] = useState({
    id: "",
    name: "",
    default_agent_id: "",
    auto_start: false,
    proxy: "",
  });
  const [error, setError] = useState("");

  async function load() {
    try {
      const [data, configFile] = await Promise.all([
        api("/api/channels/wechat/accounts"),
        api("/api/workspace/read", {
          method: "POST",
          body: JSON.stringify({ path: "config.yaml" }),
        }),
      ]);
      const nextAccounts = data.accounts || [];
      const parsed = parseConfigDraft(configFile.content || "");
      setAccounts(nextAccounts);
      setAgents(parsed.config.agents.definitions || []);
      setActiveId((current) => (nextAccounts.some((account) => account.id === current) ? current : nextAccounts[0]?.id || ""));
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, []);

  async function action(accountId, name) {
    setError("");
    try {
      await api(`/api/channels/wechat/accounts/${accountId}/${name}`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function updateAccount(accountId, patch) {
    setError("");
    try {
      await api(`/api/channels/wechat/accounts/${accountId}`, {
        method: "PUT",
        body: JSON.stringify(patch),
      });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function createAccount() {
    setError("");
    const payload = {
      id: newAccount.id.trim(),
      name: newAccount.name.trim(),
      default_agent_id: newAccount.default_agent_id,
      auto_start: newAccount.auto_start,
      proxy: newAccount.proxy.trim(),
    };
    if (!payload.id) {
      setError("请输入账号 ID");
      return;
    }
    try {
      const data = await api("/api/channels/wechat/accounts", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setCreating(false);
      setNewAccount({ id: "", name: "", default_agent_id: "", auto_start: false, proxy: "" });
      setActiveId(data.account?.id || payload.id);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function deleteAccount(accountId) {
    if (!window.confirm(`删除微信账号 ${accountId}？登录态文件也会被移除。`)) return;
    setError("");
    try {
      await api(`/api/channels/wechat/accounts/${accountId}`, { method: "DELETE" });
      setActiveId("");
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section className="console-screen">
      <div className="workspace-header">
        <div>
          <span className="section-label">微信</span>
          <h1>wechat channel</h1>
        </div>
        <button onClick={load}>
          <RefreshCw size={15} />
          刷新
        </button>
      </div>
      {error ? <p className="error">{error}</p> : null}

      <WechatSummary accounts={accounts} />

      <div className="wechat-layout">
        <section className="panel wechat-list">
          <div className="panel-title">
            <div>
              <span>账号</span>
              <small>config.yaml channels.wechat_personal.accounts</small>
            </div>
            <div className="config-inline-actions">
              <button onClick={() => setCreating((value) => !value)}>
                <Plus size={15} />
                新增
              </button>
            </div>
          </div>
          {creating ? (
            <div className="wechat-create-form">
              <label>
                <span>账号 ID</span>
                <input
                  value={newAccount.id}
                  onChange={(event) => setNewAccount((draft) => ({ ...draft, id: event.target.value }))}
                  placeholder="例如 wife"
                />
              </label>
              <label>
                <span>显示名称</span>
                <input
                  value={newAccount.name}
                  onChange={(event) => setNewAccount((draft) => ({ ...draft, name: event.target.value }))}
                  placeholder="留空则使用账号 ID"
                />
              </label>
              <label>
                <span>默认 Agent</span>
                <select
                  value={newAccount.default_agent_id}
                  onChange={(event) => setNewAccount((draft) => ({ ...draft, default_agent_id: event.target.value }))}
                >
                  <option value="">未绑定</option>
                  {agents.map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agent.id || "未命名 Agent"}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>代理</span>
                <input
                  value={newAccount.proxy}
                  onChange={(event) => setNewAccount((draft) => ({ ...draft, proxy: event.target.value }))}
                  placeholder="可选，例如 http://127.0.0.1:7890"
                />
              </label>
              <label className="config-toggle">
                <input
                  type="checkbox"
                  checked={newAccount.auto_start}
                  onChange={(event) => setNewAccount((draft) => ({ ...draft, auto_start: event.target.checked }))}
                />
                自动启动
              </label>
              <div className="actions">
                <button className="primary" onClick={createAccount}>
                  <Save size={15} />
                  保存账号
                </button>
                <button onClick={() => setCreating(false)}>取消</button>
              </div>
            </div>
          ) : null}
          <div className="wechat-account-list">
            {accounts.length === 0 ? <div className="empty-state">暂无微信账号。</div> : null}
            {accounts.map((account) => (
              <button
                key={account.id}
                className={`wechat-account-row ${activeId === account.id ? "selected" : ""}`}
                onClick={() => setActiveId(account.id)}
              >
                <Smartphone size={16} />
                <span>{account.name || account.id}</span>
                <Status status={account.status?.login_state} />
                <small>{account.default_agent_id || "未绑定 Agent"}</small>
              </button>
            ))}
          </div>
        </section>

        <WechatAccountDetail
          account={accounts.find((account) => account.id === activeId)}
          agents={agents}
          onAction={action}
          onUpdate={updateAccount}
          onDelete={deleteAccount}
        />
      </div>
    </section>
  );
}

function WechatSummary({ accounts }) {
  const running = accounts.filter((account) => account.status?.running).length;
  const loggedIn = accounts.filter((account) => account.status?.login_state === "logged_in").length;
  const withError = accounts.filter((account) => account.status?.error).length;
  return (
    <div className="metrics-row wechat-metrics">
      <Metric label="账号" value={accounts.length} tone="blue" />
      <Metric label="运行中" value={running} tone="cyan" />
      <Metric label="已登录" value={loggedIn} tone="green" />
      <Metric label="异常" value={withError} tone="red" />
    </div>
  );
}

function WechatAccountDetail({ account, agents, onAction, onUpdate, onDelete }) {
  if (!account) {
    return (
      <section className="panel account-detail-empty">
        <Smartphone size={32} />
        <strong>选择一个微信账号</strong>
        <span>这里会显示登录态、二维码、绑定 Agent 和最近通道日志。</span>
      </section>
    );
  }
  const status = account.status || {};
  return (
    <section className="panel account-detail">
      <div className="account-hero">
        <div>
          <span className="section-label">{account.id}</span>
          <h2>{account.name || account.id}</h2>
        </div>
        <div className="account-hero-actions">
          <Status status={status.login_state} />
          <button className="delete-button icon-button" onClick={() => onDelete(account.id)} title="删除账号" aria-label="删除账号">
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      <div className="wechat-detail-grid">
        <div className="wechat-qr-zone">
          {status.qrcode_data_url ? (
            <img className="qr" src={status.qrcode_data_url} alt="微信登录二维码" />
          ) : (
            <div className="qr-placeholder">
              <Smartphone size={28} />
              <span>{status.login_state === "logged_in" ? "已登录" : "等待二维码"}</span>
            </div>
          )}
          <div className="actions">
            <button className="primary" onClick={() => onAction(account.id, "start")}>
              启动
            </button>
            <button onClick={() => onAction(account.id, "stop")}>停止</button>
          </div>
        </div>

        <div className="wechat-facts">
          <div className="kv-grid two">
            <label className="kv kv-field">
              <span>默认 Agent</span>
              <select value={account.default_agent_id || ""} onChange={(event) => onUpdate(account.id, { default_agent_id: event.target.value })}>
                <option value="">未绑定</option>
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.id || "未命名 Agent"}
                  </option>
                ))}
              </select>
            </label>
            <Kv label="自动启动" value={account.auto_start ? "是" : "否"} />
            <Kv label="运行进程" value={status.running ? "running" : "stopped"} />
            <Kv label="登录用户" value={status.user || "-"} />
            <Kv label="二维码状态" value={status.qrcode_status || "-"} />
            <Kv label="代理" value={account.proxy || "未配置"} />
          </div>
          {status.error ? <p className="error">{status.error}</p> : null}
        </div>
      </div>

      <div className="delivery-strip">
        <PathBox label="消息入口" value="channels.wechat_personal.accounts[]" />
        <PathBox label="会话落盘" value="workspace/sessions/{session_id}/messages.jsonl" />
        <PathBox label="任务创建" value="workspace/runs/{run_id}/input.json source=wechat" />
        <PathBox label="投递状态" value="workspace/runs/{run_id}/delivery.json" />
      </div>

      <div className="wechat-log">
        <span className="section-label">通道日志</span>
        <pre>{(status.logs || []).join("\n") || "暂无通道日志"}</pre>
      </div>
    </section>
  );
}

const EMPTY_SCHEDULE = {
  id: "",
  name: "",
  enabled: true,
  agent_id: "assistant",
  prompt: "",
  context_ids_text: "",
  session_id: "",
  metadata_text: "{}",
  trigger_kind: "interval",
  interval_minutes: 60,
  cron_expr: "0 9 * * *",
  cron_timezone: "Asia/Shanghai",
  once_at: "",
};

function SchedulesPage() {
  const [schedules, setSchedules] = useState([]);
  const [activeSchedule, setActiveSchedule] = useState(null);
  const [draft, setDraft] = useState(EMPTY_SCHEDULE);
  const [agents, setAgents] = useState([]);
  const [editingNew, setEditingNew] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setError("");
    try {
      const [scheduleData, configData] = await Promise.all([
        api("/api/schedules"),
        api("/api/workspace/read", {
          method: "POST",
          body: JSON.stringify({ path: "config.yaml" }),
        }),
      ]);
      const nextSchedules = scheduleData.schedules || [];
      const parsed = parseConfigDraft(configData.content || "");
      const nextAgents = parsed.config.agents.definitions || [];
      setSchedules(nextSchedules);
      setAgents(nextAgents);
      const activeId = activeSchedule?.definition?.id || activeSchedule?.summary?.id;
      const nextActive = nextSchedules.find((item) => scheduleId(item) === activeId) || nextSchedules[0] || null;
      if (nextActive && !editingNew) {
        await loadDetail(scheduleId(nextActive));
      } else if (!nextActive && !editingNew) {
        setActiveSchedule(null);
      }
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadDetail(scheduleIdValue) {
    const detail = await api(`/api/schedules/${encodeURIComponent(scheduleIdValue)}`);
    setActiveSchedule(detail);
    setDraft(scheduleToDraft(detail));
    setEditingNew(false);
  }

  useEffect(() => {
    load();
  }, []);

  function createNew() {
    const defaultAgent = agents[0]?.id || "assistant";
    setActiveSchedule(null);
    setDraft({
      ...EMPTY_SCHEDULE,
      id: `daily_${new Date().toISOString().slice(0, 10).replaceAll("-", "_")}`,
      agent_id: defaultAgent,
    });
    setEditingNew(true);
    setMessage("");
    setError("");
  }

  async function saveSchedule() {
    setMessage("");
    setError("");
    try {
      const payload = draftToSchedulePayload(draft);
      const method = editingNew ? "POST" : "PUT";
      const path = editingNew ? "/api/schedules" : `/api/schedules/${encodeURIComponent(payload.id)}`;
      const data = await api(path, { method, body: JSON.stringify(payload) });
      setMessage(editingNew ? "定时任务已创建" : "定时任务已保存");
      await load();
      await loadDetail(data.definition.id);
    } catch (err) {
      setError(err.message);
    }
  }

  async function deleteSchedule() {
    const id = draft.id;
    if (!id || !window.confirm(`删除定时任务 ${id}？`)) return;
    setMessage("");
    setError("");
    try {
      await api(`/api/schedules/${encodeURIComponent(id)}`, { method: "DELETE" });
      setMessage("定时任务已删除");
      setActiveSchedule(null);
      setEditingNew(false);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function runNow() {
    const id = activeSchedule?.definition?.id;
    if (!id) return;
    setMessage("");
    setError("");
    try {
      const detail = await api(`/api/schedules/${encodeURIComponent(id)}/run-now`, { method: "POST" });
      setActiveSchedule(detail);
      setDraft(scheduleToDraft(detail));
      setMessage("定时任务已执行");
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  const builtIn = Boolean(activeSchedule?.definition?.built_in);
  const selectedId = activeSchedule?.definition?.id || (editingNew ? draft.id : "");

  return (
    <section className="console-screen schedules-screen">
      {message ? <p className="ok">{message}</p> : null}
      {error ? <p className="error">{error}</p> : null}
      <div className="schedules-grid">
        <section className="panel schedule-index">
          <div className="panel-title">
            <div>
              <span>定时任务</span>
              <small>workspace/schedules/index.json</small>
            </div>
            <div className="config-inline-actions">
              <button className="icon-button" onClick={load} title="刷新">
                <RefreshCw size={15} />
              </button>
              <button onClick={createNew}>
                <Plus size={15} />
                新建
              </button>
            </div>
          </div>
          <div className="schedule-list">
            {schedules.length === 0 ? <div className="empty-state">暂无定时任务。</div> : null}
            {schedules.map((item) => {
              const summary = item.summary || item;
              const id = scheduleId(item);
              return (
                <button
                  key={id}
                  className={`schedule-row ${selectedId === id ? "selected" : ""}`}
                  onClick={() => loadDetail(id)}
                >
                  <div>
                    <strong>{summary.name || id}</strong>
                    <small>{formatScheduleTrigger(summary.trigger)}</small>
                  </div>
                  <Status status={summary.enabled ? summary.status : "disabled"} />
                </button>
              );
            })}
          </div>
        </section>

        <section className="panel schedule-detail">
          <div className="panel-title">
            <div>
              <span>{editingNew ? "新建 Agent 定时任务" : activeSchedule ? activeSchedule.definition.name : "任务详情"}</span>
              <small>{builtIn ? "系统内置任务 · 只读配置" : "prompt + agent + trigger"}</small>
            </div>
            <div className="config-inline-actions">
              <button onClick={runNow} disabled={!activeSchedule?.definition?.id}>
                <Play size={15} />
                立即运行
              </button>
              {!builtIn ? (
                <>
                  <button className="primary" onClick={saveSchedule}>
                    <Save size={15} />
                    保存
                  </button>
                  <button className="delete-button" onClick={deleteSchedule} disabled={editingNew || !activeSchedule}>
                    <Trash2 size={15} />
                  </button>
                </>
              ) : null}
            </div>
          </div>
          {builtIn ? (
            <BuiltInScheduleInfo detail={activeSchedule} />
          ) : (
            <ScheduleForm draft={draft} onChange={setDraft} agents={agents} readOnly={builtIn} lockId={!editingNew} />
          )}
          {activeSchedule ? <ScheduleStatePanel detail={activeSchedule} /> : null}
        </section>
      </div>
    </section>
  );
}

function BuiltInScheduleInfo({ detail }) {
  const definition = detail?.definition || {};
  return (
    <div className="builtin-schedule-info">
      <p className="muted-note">内置任务由系统配置驱动，只能在这里查看状态或立即执行。</p>
      <div className="schedule-facts">
        <ScheduleFact label="ID" value={definition.id || "-"} />
        <ScheduleFact label="类型" value={definition.type || "-"} />
        <ScheduleFact label="触发" value={formatScheduleTrigger(definition.trigger)} />
        <ScheduleFact label="启用" value={definition.enabled ? "启用" : "停用"} />
      </div>
    </div>
  );
}

function ScheduleForm({ draft, onChange, agents, readOnly, lockId }) {
  function update(field, value) {
    onChange({ ...draft, [field]: value });
  }

  return (
    <div className="schedule-form">
      <div className="config-grid">
        <ConfigFieldLite label="ID">
          <input value={draft.id} readOnly={readOnly || lockId} onChange={(event) => update("id", event.target.value)} />
        </ConfigFieldLite>
        <ConfigFieldLite label="名称">
          <input value={draft.name} readOnly={readOnly} onChange={(event) => update("name", event.target.value)} />
        </ConfigFieldLite>
        <ConfigFieldLite label="Agent">
          <select value={draft.agent_id} disabled={readOnly} onChange={(event) => update("agent_id", event.target.value)}>
            {!draft.agent_id || !agents.some((agent) => agent.id === draft.agent_id) ? (
              <option value={draft.agent_id}>{draft.agent_id || "-"}</option>
            ) : null}
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name || agent.id}
              </option>
            ))}
          </select>
        </ConfigFieldLite>
        <ConfigFieldLite label="启用">
          <label className="config-toggle field-toggle">
            <input type="checkbox" checked={draft.enabled} disabled={readOnly} onChange={(event) => update("enabled", event.target.checked)} />
            <span>{draft.enabled ? "启用" : "停用"}</span>
          </label>
        </ConfigFieldLite>
      </div>
      <ConfigFieldLite label="Prompt">
        <textarea className="schedule-prompt" value={draft.prompt} readOnly={readOnly} onChange={(event) => update("prompt", event.target.value)} />
      </ConfigFieldLite>
      <div className="config-grid">
        <ConfigFieldLite label="触发类型">
          <select value={draft.trigger_kind} disabled={readOnly} onChange={(event) => update("trigger_kind", event.target.value)}>
            <option value="interval">间隔</option>
            <option value="cron">Cron</option>
            <option value="once">一次性</option>
          </select>
        </ConfigFieldLite>
        {draft.trigger_kind === "interval" ? (
          <ConfigFieldLite label="间隔分钟">
            <input
              type="number"
              min="1"
              value={draft.interval_minutes}
              readOnly={readOnly}
              onChange={(event) => update("interval_minutes", Number(event.target.value) || 1)}
            />
          </ConfigFieldLite>
        ) : null}
        {draft.trigger_kind === "cron" ? (
          <>
            <ConfigFieldLite label="Cron 表达式">
              <input value={draft.cron_expr} readOnly={readOnly} onChange={(event) => update("cron_expr", event.target.value)} />
            </ConfigFieldLite>
            <ConfigFieldLite label="时区">
              <input value={draft.cron_timezone} readOnly={readOnly} onChange={(event) => update("cron_timezone", event.target.value)} />
            </ConfigFieldLite>
          </>
        ) : null}
        {draft.trigger_kind === "once" ? (
          <ConfigFieldLite label="执行时间">
            <input type="datetime-local" value={draft.once_at} readOnly={readOnly} onChange={(event) => update("once_at", event.target.value)} />
          </ConfigFieldLite>
        ) : null}
      </div>
      <div className="config-grid two">
        <ConfigFieldLite label="Context IDs">
          <input value={draft.context_ids_text} readOnly={readOnly} onChange={(event) => update("context_ids_text", event.target.value)} />
        </ConfigFieldLite>
        <ConfigFieldLite label="Session ID">
          <input value={draft.session_id} readOnly={readOnly} onChange={(event) => update("session_id", event.target.value)} />
        </ConfigFieldLite>
      </div>
      <ConfigFieldLite label="Metadata JSON">
        <textarea
          className="schedule-metadata"
          value={draft.metadata_text}
          readOnly={readOnly}
          onChange={(event) => update("metadata_text", event.target.value)}
        />
      </ConfigFieldLite>
    </div>
  );
}

function ScheduleStatePanel({ detail }) {
  const state = detail.state || {};
  const events = detail.events || [];
  return (
    <div className="schedule-state">
      <div className="schedule-facts schedule-state-facts">
        <ScheduleFact label="状态" value={state.status || "-"} />
        <ScheduleFact label="下次运行" value={formatDateTime(state.next_run_at)} />
        <ScheduleFact label="上次运行" value={formatDateTime(state.last_run_at)} />
        <ScheduleFact label="最近 Run" value={state.last_run_id || "-"} />
        <ScheduleFact label="重试" value={formatRetryState(state)} />
      </div>
      {state.last_error ? <PathBox label="最近错误" value={state.last_error.message || JSON.stringify(state.last_error)} /> : null}
      <div className="event-list schedule-events">
        {events.length === 0 ? <div className="empty-state">暂无事件。</div> : null}
        {events.map((event) => (
          <div key={event.seq} className="event-row">
            <span className="event-dot" />
            <time>{formatTime(event.created_at)}</time>
            <strong>{event.type}</strong>
            <code>{formatScheduleEventPayload(event.payload)}</code>
          </div>
        ))}
      </div>
    </div>
  );
}

function ScheduleFact({ label, value }) {
  const displayValue = value || "-";
  return (
    <div className="schedule-fact">
      <span>{label}</span>
      <strong title={String(displayValue)}>{displayValue}</strong>
    </div>
  );
}

function formatRetryState(state) {
  const attempts = Number(state.retry_attempts || 0);
  const maxAttempts = Number(state.retry_max_attempts || 0);
  if (!attempts && !maxAttempts) return "-";
  return `${attempts}/${maxAttempts || 3}`;
}

function ConfigFieldLite({ label, children }) {
  return (
    <label className="config-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function SystemPage({ onNavigate }) {
  const [logs, setLogs] = useState([]);
  const [activeLog, setActiveLog] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function loadLogs() {
    const data = await api("/api/system/logs/list", { method: "POST" });
    setLogs(data.logs || []);
  }

  async function readLog(name) {
    const data = await api("/api/system/logs/read", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    setActiveLog(data.content || "");
  }

  async function updateService() {
    const data = await api("/api/system/update-service", { method: "POST" });
    setMessage(data.message || "更新已开始");
  }

  useEffect(() => {
    loadLogs();
  }, []);

  return (
    <section className="console-screen system-screen">
      <section className="panel system-control-panel">
        <div className="panel-title">
          <div>
            <span>运维</span>
            <small>生产更新、运行日志、工作目录入口</small>
          </div>
        </div>
        {message ? <p className="ok">{message}</p> : null}
        {error ? <p className="error">{error}</p> : null}
        <div className="system-actions">
          <button className="primary" onClick={updateService}>
            生产更新
          </button>
          <button onClick={() => onNavigate("workspace")}>
            <FolderTree size={15} />
            打开工作目录
          </button>
        </div>
        <div className="ops-stack">
          <PathBox label="配置文件" value="workspace/config.yaml（受保护，不可删除）" />
          <PathBox label="服务更新" value="/api/system/update-service" />
          <PathBox label="运行日志" value="workspace/logs/platform-YYYY-MM-DD.log" />
        </div>
      </section>

      <section className="panel logs-panel">
        <div className="panel-title">
          <div>
            <span>platform logs</span>
            <small>workspace/logs/platform-YYYY-MM-DD.log</small>
          </div>
          <button onClick={loadLogs}>
            <RefreshCw size={15} />
          </button>
        </div>
        <div className="log-files">
          {logs.map((log) => (
            <button key={log.name} onClick={() => readLog(log.name)}>
              <Clock3 size={14} />
              {log.name}
            </button>
          ))}
        </div>
        <pre className="log-output">{activeLog || "暂无日志"}</pre>
      </section>
    </section>
  );
}

function BrowserProfilesPage() {
  const [browserProfiles, setBrowserProfiles] = useState([]);
  const [browserAgents, setBrowserAgents] = useState([]);
  const [browserAgentId, setBrowserAgentId] = useState("");
  const [browserUrl, setBrowserUrl] = useState("https://mp.weixin.qq.com/");
  const [browserSession, setBrowserSession] = useState(null);
  const [browserInput, setBrowserInput] = useState("");
  const [screenshotVersion, setScreenshotVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function loadBrowserProfiles() {
    const data = await api("/api/system/browser-profiles");
    const agents = data.agents || [];
    setBrowserAgents(agents);
    setBrowserProfiles(data.profiles || []);
    setBrowserAgentId((current) => (agents.some((agent) => agent.id === current) ? current : agents[0]?.id || ""));
  }

  async function startBrowserAuth() {
    setError("");
    setMessage("");
    try {
      const data = await api("/api/system/browser-auth/sessions", {
        method: "POST",
        body: JSON.stringify({ agent_id: browserAgentId, url: browserUrl.trim() }),
      });
      setBrowserSession(data.session);
      setScreenshotVersion(Date.now());
      setMessage("浏览器授权会话已启动");
      await loadBrowserProfiles();
    } catch (err) {
      setError(err.message);
    }
  }

  async function browserSessionAction(path, payload = {}) {
    if (!browserSession?.id) return;
    setError("");
    try {
      const data = await api(`/api/system/browser-auth/sessions/${encodeURIComponent(browserSession.id)}/${path}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setBrowserSession(data.session);
      setScreenshotVersion(Date.now());
    } catch (err) {
      setError(err.message);
    }
  }

  async function clickBrowserScreenshot(event) {
    if (!browserSession?.id) return;
    const image = event.currentTarget;
    const rect = image.getBoundingClientRect();
    const scaleX = image.naturalWidth / rect.width;
    const scaleY = image.naturalHeight / rect.height;
    await browserSessionAction("click", {
      x: Math.round((event.clientX - rect.left) * scaleX),
      y: Math.round((event.clientY - rect.top) * scaleY),
    });
  }

  async function typeBrowserText() {
    if (!browserInput) return;
    await browserSessionAction("type", { text: browserInput });
    setBrowserInput("");
  }

  async function finishBrowserAuth() {
    if (!browserSession?.id) return;
    await browserSessionAction("finish");
    setBrowserSession(null);
    setMessage("浏览器 profile 已保存");
    await loadBrowserProfiles();
  }

  async function cancelBrowserAuth() {
    if (!browserSession?.id) return;
    await browserSessionAction("cancel");
    setBrowserSession(null);
    setMessage("浏览器授权会话已取消");
    await loadBrowserProfiles();
  }

  useEffect(() => {
    loadBrowserProfiles();
  }, []);

  const activeProfile = browserProfiles.find((profile) => profile.agent_id === browserAgentId) || null;
  const profileStatus = browserSession?.status || (activeProfile?.locked ? "locked" : activeProfile?.exists ? "saved" : "empty");

  return (
    <section className="console-screen browser-screen">
      <section className="panel browser-list-panel">
        <div className="panel-title">
          <div>
            <span>浏览器</span>
            <small>workspace/browser_profiles/</small>
          </div>
          <button className="icon-button" onClick={loadBrowserProfiles} title="刷新 profile">
            <RefreshCw size={15} />
          </button>
        </div>
        <div className="browser-profile-list">
          {browserAgents.map((agent) => {
            const profile = browserProfiles.find((item) => item.agent_id === agent.id);
            const status = profile?.locked ? "locked" : profile?.exists ? "saved" : "empty";
            return (
              <button
                key={agent.id}
                className={agent.id === browserAgentId ? "browser-profile-card active" : "browser-profile-card"}
                disabled={Boolean(browserSession)}
                onClick={() => setBrowserAgentId(agent.id)}
              >
                <span>{agent.name || agent.id}</span>
                <Status status={status} />
                <small>{profile?.profile_path || `workspace/browser_profiles/${agent.id}`}</small>
              </button>
            );
          })}
          {browserAgents.length === 0 ? <p className="empty-state">暂无 Agent</p> : null}
        </div>
      </section>

      <section className="panel browser-auth-panel browser-auth-workspace">
        <div className="panel-title">
          <div>
            <span>授权会话</span>
            <small>当前 Agent: {browserAgentId || "-"}</small>
          </div>
          <div className="config-inline-actions">
            <button onClick={startBrowserAuth} disabled={!browserAgentId || Boolean(browserSession)}>
              <ExternalLink size={15} />
              启动授权
            </button>
          </div>
        </div>
        {message ? <p className="ok browser-feedback">{message}</p> : null}
        {error ? <p className="error browser-feedback">{error}</p> : null}
        <div className="browser-auth-controls">
          <ConfigFieldLite label="Agent">
            <select value={browserAgentId} disabled={Boolean(browserSession)} onChange={(event) => setBrowserAgentId(event.target.value)}>
              {browserAgents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name || agent.id}
                </option>
              ))}
            </select>
          </ConfigFieldLite>
          <ConfigFieldLite label="打开 URL">
            <div className="browser-url-row">
              <input value={browserUrl} disabled={Boolean(browserSession)} onChange={(event) => setBrowserUrl(event.target.value)} />
              <button
                className="icon-button"
                title="跳转"
                disabled={!browserSession}
                onClick={() => browserSessionAction("navigate", { url: browserUrl.trim() })}
              >
                <ExternalLink size={15} />
              </button>
            </div>
          </ConfigFieldLite>
          <ConfigFieldLite label="输入文本">
            <div className="browser-url-row">
              <input value={browserInput} disabled={!browserSession} onChange={(event) => setBrowserInput(event.target.value)} />
              <button className="icon-button" title="输入" disabled={!browserSession || !browserInput} onClick={typeBrowserText}>
                <Keyboard size={15} />
              </button>
            </div>
          </ConfigFieldLite>
        </div>
        <div className="browser-profile-state">
          <PathBox label="Profile" value={activeProfile?.profile_path || "-"} />
          <Kv label="状态" value={profileStatus} />
          <Kv label="当前 URL" value={browserSession?.url || "-"} />
        </div>
        {browserSession ? (
          <div className="browser-session-actions">
            <button onClick={() => browserSessionAction("press", { key: "Enter" })}>Enter</button>
            <button onClick={() => browserSessionAction("press", { key: "Escape" })}>Esc</button>
            <button onClick={() => setScreenshotVersion(Date.now())}>
              <RefreshCw size={15} />
              刷新截图
            </button>
            <button className="primary" onClick={finishBrowserAuth}>
              完成授权
            </button>
            <button className="delete-button" onClick={cancelBrowserAuth}>
              取消
            </button>
          </div>
        ) : null}
        <div className="browser-screenshot-stage">
          {browserSession ? (
            <img
              src={`/api/system/browser-auth/sessions/${encodeURIComponent(browserSession.id)}/screenshot?v=${screenshotVersion}`}
              alt="浏览器授权截图"
              onClick={clickBrowserScreenshot}
            />
          ) : (
            <div className="workspace-empty-editor">
              <TerminalSquare size={30} />
              <strong>启动授权会话</strong>
              <span>这里会显示服务器 Playwright 浏览器截图，点击截图即可操作当前 Agent 的 profile。</span>
            </div>
          )}
        </div>
      </section>
    </section>
  );
}

function normalizeChatMessages(items) {
  return items
    .filter((item) => item && (item.role === "user" || item.role === "assistant"))
    .map((item, index) => ({
      id: item.run_id ? `${item.role}_${item.run_id}` : `${item.role}_${item.seq || index}`,
      role: item.role,
      content: item.content || "",
      created_at: item.created_at || "",
      run_id: item.run_id || "",
    }));
}

function formatSessionOption(item, index) {
  const prefix = item.active ? "当前" : `历史 ${index + 1}`;
  const count = Number(item.message_count || 0);
  const updated = formatTime(item.updated_at || item.created_at);
  return `${prefix} · ${count}条 · ${updated}`;
}

async function hydrateChatRunSnapshots(messages) {
  const runIds = [
    ...new Set(
      messages
        .filter((message) => message.role === "assistant" && message.run_id)
        .slice(-8)
        .map((message) => message.run_id),
    ),
  ];
  if (runIds.length === 0) return messages;
  const runs = await Promise.all(
    runIds.map(async (runId) => {
      try {
        return await api(`/api/runs/${encodeURIComponent(runId)}`);
      } catch {
        return null;
      }
    }),
  );
  return runs.reduce((nextMessages, run) => applyChatRunSnapshot(nextMessages, run), messages);
}

function applyChatRunSnapshot(messages, run) {
  if (!run?.run_id || !run.partial) return messages;
  const partial = run.partial || {};
  const thinking = Array.isArray(partial.thinking) ? partial.thinking.filter(Boolean) : [];
  if (thinking.length === 0 && !partial.content) return messages;
  const status = String(partial.status || runStatus(run) || "");
  const streaming = status === "streaming" || runStatus(run) === "running";
  const id = `assistant_${run.run_id}`;
  return messages.map((message) => {
    if (message.id !== id) return message;
    return {
      ...message,
      content: message.content || partial.content || "",
      thinking: thinking.length ? thinking : message.thinking,
      thinkingCollapsed: partial.thinking_collapsed !== undefined ? Boolean(partial.thinking_collapsed) : !streaming,
      streaming,
      failed: status === "failed" || message.failed,
    };
  });
}

function mergeHydratedChatMessages(current, base, hydrated) {
  if (current.length === base.length && current.every((message, index) => message.id === base[index]?.id)) {
    return hydrated;
  }
  const hydratedById = new Map(hydrated.map((message) => [message.id, message]));
  return current.map((message) => {
    const restored = hydratedById.get(message.id);
    if (!restored) return message;
    return {
      ...message,
      thinking: restored.thinking || message.thinking,
      thinkingCollapsed: restored.thinkingCollapsed ?? message.thinkingCollapsed,
      streaming: restored.streaming || message.streaming,
      failed: restored.failed || message.failed,
    };
  });
}

function upsertChatAssistantMessage(messages, runId, patch) {
  const id = `assistant_${runId}`;
  const { thinkingAppend, ...rest } = patch;
  let replaced = false;
  const next = messages.map((message) => {
    if (message.id !== id) return message;
    replaced = true;
    return { ...message, ...rest, thinking: mergeChatThinking(message.thinking, thinkingAppend) };
  });
  if (replaced) return next;
  return [
    ...next,
    {
      id,
      role: "assistant",
      content: "",
      run_id: runId,
      created_at: new Date().toISOString(),
      ...rest,
      thinking: mergeChatThinking(rest.thinking, thinkingAppend),
    },
  ];
}

function mergeChatThinking(current = [], updates = []) {
  const merged = Array.isArray(current) ? [...current] : [];
  for (const update of Array.isArray(updates) ? updates : []) {
    const text = String(update || "").trim();
    if (!text || merged[merged.length - 1] === text) continue;
    merged.push(text);
  }
  return merged.slice(-10);
}

function carryChatAssistantRuntimeState(nextMessages, currentMessages, runId) {
  const id = `assistant_${runId}`;
  const runtimeMessage = currentMessages.find((message) => message.id === id);
  if (!runtimeMessage?.thinking?.length) return nextMessages;
  return nextMessages.map((message) => {
    if (message.id !== id) return message;
    return {
      ...message,
      thinking: runtimeMessage.thinking,
      thinkingCollapsed: true,
      streaming: false,
      failed: runtimeMessage.failed,
    };
  });
}

function runEventThinkingText(event) {
  const payload = event?.payload || {};
  if (event?.type === "running") {
    return payload.message || "DeepAgent 已开始处理";
  }
  if (event?.type === "agent_update") {
    if (payload.preview) return payload.preview;
    const nodes = Array.isArray(payload.nodes) ? payload.nodes.filter(Boolean).join(", ") : "";
    return nodes ? `图节点更新：${nodes}` : "DeepAgent 状态已更新";
  }
  if (event?.type === "stream_fallback") {
    return payload.message || "当前运行时不支持增量流，已切换为最终结果模式";
  }
  if (event?.type === "image_attachments_textified") {
    return payload.message || "图片已转为文本附件说明";
  }
  return "";
}

function ThinkingPanel({ items, running, collapsed }) {
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

function MarkdownMessage({ content }) {
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

  for (const rawLine of lines) {
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
    paragraph.push(line.trim());
  }
  flushCode();
  flushParagraph();
  flushList();
  flushQuote();
  return blocks.length ? blocks : <p>{content}</p>;
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

function Status({ status }) {
  const value = normalizeStatus(status);
  const icon = value === "failed" || value === "exited" ? <XCircle size={12} /> : <CheckCircle2 size={12} />;
  return (
    <span className={`status status-${String(value)}`}>
      {icon}
      {value}
    </span>
  );
}

function runStatus(run) {
  return normalizeStatus(
    run?.state?.status ||
      run?.status ||
      run?.result?.status ||
      run?.delivery?.status ||
      (run?.result?.error ? "failed" : ""),
  );
}

function normalizeStatus(status) {
  const value = String(status || "").trim();
  return value || "unknown";
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatBytes(value) {
  const size = Number(value) || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function scheduleId(schedule) {
  return schedule?.definition?.id || schedule?.summary?.id || schedule?.id || "";
}

function scheduleToDraft(detail) {
  const definition = detail?.definition || {};
  const trigger = definition.trigger || {};
  return {
    ...EMPTY_SCHEDULE,
    id: definition.id || "",
    name: definition.name || definition.id || "",
    enabled: Boolean(definition.enabled),
    agent_id: definition.agent_id || "assistant",
    prompt: definition.prompt || "",
    context_ids_text: (definition.context_ids || []).join(", "),
    session_id: definition.session_id || "",
    metadata_text: JSON.stringify(definition.metadata || {}, null, 2),
    trigger_kind: trigger.kind || "interval",
    interval_minutes: Math.max(1, Math.round((Number(trigger.seconds) || 3600) / 60)),
    cron_expr: trigger.expr || "0 9 * * *",
    cron_timezone: trigger.timezone || "Asia/Shanghai",
    once_at: trigger.kind === "once" ? toDatetimeLocal(trigger.expr) : "",
  };
}

function draftToSchedulePayload(draft) {
  const id = String(draft.id || "").trim();
  const prompt = String(draft.prompt || "").trim();
  let metadata = {};
  if (String(draft.metadata_text || "").trim()) {
    metadata = JSON.parse(draft.metadata_text);
  }
  if (!id) throw new Error("定时任务 ID 不能为空");
  if (!prompt) throw new Error("Prompt 不能为空");

  return {
    id,
    name: String(draft.name || id).trim(),
    enabled: Boolean(draft.enabled),
    agent_id: String(draft.agent_id || "").trim(),
    prompt,
    context_ids: String(draft.context_ids_text || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
    session_id: String(draft.session_id || "").trim(),
    metadata,
    trigger: draftTriggerPayload(draft),
  };
}

function draftTriggerPayload(draft) {
  if (draft.trigger_kind === "cron") {
    return {
      kind: "cron",
      expr: String(draft.cron_expr || "").trim(),
      timezone: String(draft.cron_timezone || "Asia/Shanghai").trim(),
    };
  }
  if (draft.trigger_kind === "once") {
    const date = new Date(draft.once_at);
    if (Number.isNaN(date.getTime())) throw new Error("一次性执行时间无效");
    return { kind: "once", expr: date.toISOString() };
  }
  return { kind: "interval", seconds: Math.max(1, Number(draft.interval_minutes) || 1) * 60 };
}

function formatScheduleTrigger(trigger) {
  if (!trigger) return "-";
  if (trigger.kind === "interval") return `每 ${Math.round((Number(trigger.seconds) || 0) / 60)} 分钟`;
  if (trigger.kind === "cron") return `${trigger.expr || "-"} · ${trigger.timezone || "UTC"}`;
  if (trigger.kind === "once") return `一次性 · ${formatDateTime(trigger.expr)}`;
  return trigger.kind || "-";
}

function formatScheduleEventPayload(payload) {
  if (!payload) return "-";
  if (typeof payload === "string") return payload;
  const parts = [];
  if (payload.message) parts.push(payload.message);
  if (payload.retention_days) parts.push(`保留 ${payload.retention_days} 天`);
  if (payload.summary && typeof payload.summary === "object") {
    const summary = Object.entries(payload.summary)
      .filter(([, value]) => value !== undefined && value !== null && value !== 0)
      .map(([key, value]) => `${key}: ${value}`);
    if (summary.length) parts.push(summary.join(" · "));
  }
  if (payload.items !== undefined) parts.push(`items: ${payload.items}`);
  if (payload.run_id) parts.push(payload.run_id);
  return parts.length ? parts.join(" · ") : JSON.stringify(payload);
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function toDatetimeLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offsetMs = date.getTimezoneOffset() * 60 * 1000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

createRoot(document.getElementById("root")).render(<App />);
