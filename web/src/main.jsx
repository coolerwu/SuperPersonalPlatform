import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  FileJson,
  FolderTree,
  LogOut,
  Play,
  RefreshCw,
  Save,
  Send,
  Settings,
  Smartphone,
  TerminalSquare,
  XCircle,
} from "lucide-react";
import "./styles.css";

const NAV_ITEMS = [
  { id: "runs", path: "/runs", label: "Runs", icon: Play },
  { id: "workspace", path: "/workspace", label: "工作目录", icon: FolderTree },
  { id: "wechat", path: "/wechat", label: "微信", icon: Smartphone },
  { id: "system", path: "/system", label: "系统", icon: Settings },
];

const WORKSPACE_TREE = [
  ["workspace/runs/index.json", "Run 摘要与状态索引"],
  ["workspace/runs/{run_id}/input.json", "创建时输入与 Agent/Context 快照"],
  ["workspace/runs/{run_id}/state.json", "当前状态、更新时间、事件序号"],
  ["workspace/runs/{run_id}/events.jsonl", "前端轮询读取的事件流"],
  ["workspace/runs/{run_id}/result.json", "DeepAgent 最终输出"],
  ["workspace/runs/{run_id}/delivery.json", "微信等渠道投递状态"],
  ["workspace/contexts/{context_id}/context.json", "Context 隔离边界"],
  ["workspace/contexts/{context_id}/knowledge/index.json", "Context 内知识索引"],
  ["workspace/channels/wechat/sessions/{account_id}.json", "微信登录态"],
  ["workspace/logs/platform-YYYY-MM-DD.log", "系统日志"],
];

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

function routeFromPath(pathname) {
  if (pathname === "/" || pathname === "/agents" || pathname === "/login") return "runs";
  const match = NAV_ITEMS.find((item) => item.path === pathname);
  return match?.id || "runs";
}

function pathForPage(page) {
  return NAV_ITEMS.find((item) => item.id === page)?.path || "/runs";
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
              <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => navigate(item.id)}>
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
        {page === "runs" ? <RunsPage /> : null}
        {page === "workspace" ? <WorkspacePage /> : null}
        {page === "wechat" ? <WechatPage /> : null}
        {page === "system" ? <SystemPage /> : null}
      </main>
    </div>
  );
}

function RunsPage() {
  const [runs, setRuns] = useState([]);
  const [activeRun, setActiveRun] = useState(null);
  const [events, setEvents] = useState([]);
  const [content, setContent] = useState("");
  const [agentId, setAgentId] = useState("");
  const [error, setError] = useState("");

  const counts = useMemo(() => {
    return runs.reduce(
      (acc, run) => {
        const status = run.status || "unknown";
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
    setRuns(nextRuns);
    if (!activeRun && nextRuns[0]?.run_id) {
      setActiveRun({ run_id: nextRuns[0].run_id });
    }
  }

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 2500);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!activeRun?.run_id) return undefined;
    async function poll() {
      const [run, eventData] = await Promise.all([
        api(`/api/runs/${activeRun.run_id}`),
        api(`/api/runs/${activeRun.run_id}/events`),
      ]);
      setActiveRun(run);
      setEvents(eventData.events || []);
    }
    poll();
    const timer = window.setInterval(poll, 2000);
    return () => window.clearInterval(timer);
  }, [activeRun?.run_id]);

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      const run = await api("/api/runs", {
        method: "POST",
        body: JSON.stringify({ content, agent_id: agentId }),
      });
      setContent("");
      setActiveRun(run);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section className="console-screen">
      <form className="command-strip" onSubmit={submit}>
        <label>
          Prompt
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="输入任务指令，后端创建 workspace/runs/{run_id}"
          />
        </label>
        <label className="agent-field">
          Agent ID
          <input value={agentId} onChange={(event) => setAgentId(event.target.value)} placeholder="可空，默认第一个 Agent" />
        </label>
        <button className="primary create-run" disabled={!content.trim()}>
          <Play size={16} />
          创建 Run
        </button>
        {error ? <p className="error command-error">{error}</p> : null}
      </form>

      <div className="metrics-row">
        <Metric label="全部 Run" value={counts.total} tone="blue" />
        <Metric label="运行中" value={counts.running} tone="cyan" />
        <Metric label="已完成" value={counts.completed} tone="green" />
        <Metric label="失败" value={counts.failed} tone="red" />
      </div>

      <div className="runs-grid">
        <RunIndex runs={runs} activeRunId={activeRun?.run_id} onSelect={(runId) => setActiveRun({ run_id: runId })} onRefresh={load} />
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
        {runs.length === 0 ? <div className="empty-state">暂无 runs。创建任务后会写入 index.json。</div> : null}
        {runs.map((run) => (
          <button
            key={run.run_id}
            className={`run-table row ${activeRunId === run.run_id ? "selected" : ""}`}
            onClick={() => onSelect(run.run_id)}
          >
            <span className="mono">{run.run_id}</span>
            <Status status={run.status} />
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
        <strong>选择或创建一个 run</strong>
        <span>前端不会执行 Agent，只轮询磁盘状态和事件。</span>
      </section>
    );
  }
  const input = run.input || {};
  const state = run.state || {};
  const result = run.result?.content || run.result?.error?.message || "";
  const runId = run.run_id || input.run_id;

  return (
    <section className="panel run-detail">
      <div className="detail-header">
        <div>
          <span className="section-label">Run 详情</span>
          <h2>{runId}</h2>
        </div>
        <Status status={state.status} />
      </div>
      <div className="kv-grid">
        <Kv label="Agent" value={input.agent_id || "-"} />
        <Kv label="来源" value={input.source || "api"} />
        <Kv label="创建时间" value={formatTime(input.created_at)} />
        <Kv label="事件序号" value={state.seq ?? 0} />
      </div>
      <PathBox label="工作目录" value={`workspace/runs/${runId}/`} />
      <PathBox label="状态文件" value={`workspace/runs/${runId}/state.json`} />
      <PathBox label="事件文件" value={`workspace/runs/${runId}/events.jsonl`} />

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
          <span className="section-label">结果预览</span>
          <small>{`workspace/runs/${runId}/result.json`}</small>
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
        <RailRow label="投递" value="delivery.json" />
      </RailCard>
      <RailCard title="Nutstore WebDAV" status="Context" tone="green">
        <RailRow label="地址" value="dav.jianguoyun.com" />
        <RailRow label="根目录" value="config.yaml" />
      </RailCard>
      <RailCard title="运行概览" status={`${runs.length} runs`} tone="neutral">
        <RailRow label="索引" value="runs/index.json" />
        <RailRow label="状态" value="state.json" />
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
  return (
    <section className="console-screen workspace-screen">
      <div className="workspace-header">
        <div>
          <span className="section-label">工作目录</span>
          <h1>workspace runtime layout</h1>
        </div>
        <Status status="落盘运行" />
      </div>
      <div className="workspace-layout">
        <section className="panel tree-panel">
          <div className="panel-title">
            <div>
              <span>目录结构</span>
              <small>后端重启后从磁盘恢复状态</small>
            </div>
          </div>
          <div className="tree-list">
            {WORKSPACE_TREE.map(([path, description]) => (
              <div className="tree-row" key={path}>
                <FileJson size={15} />
                <code>{path}</code>
                <span>{description}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="panel workspace-notes">
          <div className="panel-title">
            <div>
              <span>边界</span>
              <small>Agent / Context / Knowledge / Run</small>
            </div>
          </div>
          <div className="model-stack">
            <ModelLine title="Agent" text="人格、模型、默认 Context 集合" />
            <ModelLine title="Context" text="归属隔离边界，包含 roots、tools、knowledge" />
            <ModelLine title="Knowledge" text="Context 内部资源，不全局散放" />
            <ModelLine title="Run" text="创建时固化 Agent + Context + Knowledge 快照" />
          </div>
        </section>
      </div>
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
  const [error, setError] = useState("");

  async function load() {
    try {
      const data = await api("/api/channels/wechat/accounts");
      setAccounts(data.accounts || []);
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

  return (
    <section className="console-screen">
      <div className="workspace-header">
        <div>
          <span className="section-label">微信</span>
          <h1>WeChat channel accounts</h1>
        </div>
        <button onClick={load}>
          <RefreshCw size={15} />
          刷新
        </button>
      </div>
      {error ? <p className="error">{error}</p> : null}
      <div className="account-grid">
        {accounts.map((account) => (
          <section className="panel account-panel" key={account.id}>
            <div className="detail-header">
              <div>
                <span className="section-label">{account.id}</span>
                <h2>{account.name || account.id}</h2>
              </div>
              <Status status={account.status?.login_state} />
            </div>
            <div className="kv-grid two">
              <Kv label="Agent" value={account.default_agent_id || "未绑定"} />
              <Kv label="自动启动" value={account.auto_start ? "是" : "否"} />
              <Kv label="运行" value={account.status?.running ? "running" : "stopped"} />
              <Kv label="用户" value={account.status?.user || "-"} />
            </div>
            {account.status?.qrcode_data_url ? (
              <img className="qr" src={account.status.qrcode_data_url} alt="微信登录二维码" />
            ) : null}
            <div className="actions">
              <button className="primary" onClick={() => action(account.id, "start")}>
                启动
              </button>
              <button onClick={() => action(account.id, "stop")}>停止</button>
            </div>
          </section>
        ))}
      </div>
    </section>
  );
}

function SystemPage() {
  const [config, setConfig] = useState("");
  const [logs, setLogs] = useState([]);
  const [activeLog, setActiveLog] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function loadConfig() {
    const data = await api("/api/system/config/read", { method: "POST" });
    setConfig(data.content || "");
  }

  async function saveConfig() {
    setError("");
    try {
      const data = await api("/api/system/config", {
        method: "PUT",
        body: JSON.stringify({ content: config }),
      });
      setMessage(data.message || "已保存");
    } catch (err) {
      setError(err.message);
    }
  }

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
    loadConfig();
    loadLogs();
  }, []);

  return (
    <section className="console-screen system-screen">
      <section className="panel config-panel">
        <div className="panel-title">
          <div>
            <span>config.yaml</span>
            <small>active workspace configuration</small>
          </div>
          <button className="primary" onClick={saveConfig}>
            <Save size={15} />
            保存
          </button>
        </div>
        {message ? <p className="ok">{message}</p> : null}
        {error ? <p className="error">{error}</p> : null}
        <textarea className="config-editor" value={config} onChange={(event) => setConfig(event.target.value)} />
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
        <button className="primary update-button" onClick={updateService}>
          生产更新
        </button>
      </section>
    </section>
  );
}

function Status({ status }) {
  const value = status || "unknown";
  const icon = value === "failed" || value === "exited" ? <XCircle size={12} /> : <CheckCircle2 size={12} />;
  return (
    <span className={`status status-${String(value)}`}>
      {icon}
      {value}
    </span>
  );
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

createRoot(document.getElementById("root")).render(<App />);
