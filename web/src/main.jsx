import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  CheckCircle2,
  FileText,
  LogOut,
  Play,
  RefreshCw,
  Save,
  Send,
  Settings,
  Smartphone,
} from "lucide-react";
import "./styles.css";

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

function LoginPage({ onLogin }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      onLogin();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={submit}>
        <Bot size={34} />
        <h1>DeepAgent Console</h1>
        <label htmlFor="token">访问 Token</label>
        <input
          id="token"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          type="password"
          placeholder="config.yaml auth.token"
        />
        {error ? <p className="error">{error}</p> : null}
        <button className="primary" disabled={!token.trim()}>
          进入
          <Send size={16} />
        </button>
      </form>
    </main>
  );
}

function App() {
  const [authenticated, setAuthenticated] = useState(null);
  const [page, setPage] = useState("runs");

  useEffect(() => {
    api("/api/auth/me")
      .then((data) => setAuthenticated(Boolean(data.authenticated)))
      .catch(() => setAuthenticated(false));
  }, []);

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    setAuthenticated(false);
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
          <Bot size={20} />
          <span>DeepAgent</span>
        </div>
        <button className={page === "runs" ? "active" : ""} onClick={() => setPage("runs")}>
          <Play size={16} />
          Runs
        </button>
        <button className={page === "wechat" ? "active" : ""} onClick={() => setPage("wechat")}>
          <Smartphone size={16} />
          微信
        </button>
        <button className={page === "system" ? "active" : ""} onClick={() => setPage("system")}>
          <Settings size={16} />
          系统
        </button>
        <button className="logout" onClick={logout}>
          <LogOut size={16} />
          退出
        </button>
      </aside>
      <main className="content">
        {page === "runs" ? <RunsPage /> : null}
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

  async function load() {
    const data = await api("/api/runs");
    setRuns(data.runs || []);
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
    <section className="page">
      <form className="composer" onSubmit={submit}>
        <input value={agentId} onChange={(event) => setAgentId(event.target.value)} placeholder="agent_id，可空" />
        <textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="创建一个后端 DeepAgent run" />
        {error ? <p className="error">{error}</p> : null}
        <button className="primary" disabled={!content.trim()}>
          <Play size={16} />
          创建 Run
        </button>
      </form>
      <div className="split">
        <div className="panel">
          <div className="panel-title">
            <FileText size={16} />
            <span>runs/index.json</span>
            <button onClick={load}>
              <RefreshCw size={15} />
            </button>
          </div>
          <div className="list">
            {runs.map((run) => (
              <button key={run.run_id} className="row" onClick={() => setActiveRun({ run_id: run.run_id })}>
                <span>{run.run_id}</span>
                <Status status={run.status} />
              </button>
            ))}
          </div>
        </div>
        <RunDetail run={activeRun} events={events} />
      </div>
    </section>
  );
}

function RunDetail({ run, events }) {
  if (!run) {
    return <div className="panel muted">选择或创建一个 run。</div>;
  }
  const result = run.result?.content || run.result?.error?.message || "";
  return (
    <div className="panel detail">
      <div className="panel-title">
        <CheckCircle2 size={16} />
        <span>{run.run_id}</span>
        <Status status={run.state?.status} />
      </div>
      <pre>{JSON.stringify(run.state, null, 2)}</pre>
      <h3>Result</h3>
      <div className="result">{result || "暂无结果"}</div>
      <h3>Events</h3>
      <div className="events">
        {events.map((event) => (
          <div key={event.seq} className="event">
            <span>#{event.seq}</span>
            <strong>{event.type}</strong>
            <code>{JSON.stringify(event.payload)}</code>
          </div>
        ))}
      </div>
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
    <section className="page">
      {error ? <p className="error">{error}</p> : null}
      <div className="grid">
        {accounts.map((account) => (
          <div className="panel" key={account.id}>
            <div className="panel-title">
              <Smartphone size={16} />
              <span>{account.name || account.id}</span>
              <Status status={account.status?.login_state} />
            </div>
            <p>Agent: {account.default_agent_id || "未绑定"}</p>
            {account.status?.qrcode_data_url ? <img className="qr" src={account.status.qrcode_data_url} alt="微信登录二维码" /> : null}
            <div className="actions">
              <button onClick={() => action(account.id, "start")}>启动</button>
              <button onClick={() => action(account.id, "stop")}>停止</button>
            </div>
          </div>
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

  async function loadConfig() {
    const data = await api("/api/system/config/read", { method: "POST" });
    setConfig(data.content || "");
  }

  async function saveConfig() {
    const data = await api("/api/system/config", {
      method: "PUT",
      body: JSON.stringify({ content: config }),
    });
    setMessage(data.message || "已保存");
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
    <section className="page system-grid">
      <div className="panel">
        <div className="panel-title">
          <Settings size={16} />
          <span>config.yaml</span>
          <button onClick={saveConfig}>
            <Save size={15} />
          </button>
        </div>
        {message ? <p className="ok">{message}</p> : null}
        <textarea className="config-editor" value={config} onChange={(event) => setConfig(event.target.value)} />
      </div>
      <div className="panel">
        <div className="panel-title">
          <FileText size={16} />
          <span>logs</span>
          <button onClick={loadLogs}>
            <RefreshCw size={15} />
          </button>
        </div>
        <div className="actions">
          {logs.map((log) => (
            <button key={log.name} onClick={() => readLog(log.name)}>{log.name}</button>
          ))}
        </div>
        <pre>{activeLog || "暂无日志"}</pre>
        <button className="primary" onClick={updateService}>生产更新</button>
      </div>
    </section>
  );
}

function Status({ status }) {
  return <span className={`status status-${String(status || "unknown")}`}>{status || "unknown"}</span>;
}

createRoot(document.getElementById("root")).render(<App />);
