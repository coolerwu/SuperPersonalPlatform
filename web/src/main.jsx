import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowRight, LogOut, RefreshCw, ShieldCheck, TerminalSquare } from "lucide-react";
import "./styles.css";

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
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
        body: JSON.stringify({ token })
      });
      onLogin();
      window.history.replaceState({}, "", "/");
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-screen">
      <section className="login-panel">
        <div className="brand-mark">
          <ShieldCheck size={28} />
        </div>
        <h1>超级个人平台</h1>
        <p>使用本地配置的访问 token 进入控制台。</p>
        <form onSubmit={submit}>
          <label htmlFor="token">访问 Token</label>
          <input
            id="token"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            type="password"
            autoComplete="current-password"
            placeholder="输入 config.yaml 中的 token"
          />
          {error ? <div className="form-error">{error}</div> : null}
          <button type="submit" disabled={submitting || !token.trim()}>
            {submitting ? "登录中" : "进入平台"}
            <ArrowRight size={18} />
          </button>
        </form>
      </section>
    </main>
  );
}

function HomePage() {
  return (
    <section className="page-section">
      <div className="section-heading">
        <p>Overview</p>
        <h2>个人平台控制台</h2>
      </div>
      <div className="metrics-grid">
        <article className="metric-card">
          <span>访问模式</span>
          <strong>Token</strong>
          <p>单 token 登录，后端签发 HttpOnly 会话 cookie。</p>
        </article>
        <article className="metric-card">
          <span>部署方式</span>
          <strong>同源</strong>
          <p>FastAPI 托管 Vite 构建产物和 API。</p>
        </article>
        <article className="metric-card">
          <span>服务端口</span>
          <strong>8888</strong>
          <p>统一入口为 http://localhost:8888。</p>
        </article>
      </div>
    </section>
  );
}

function JsonPanel({ data }) {
  const items = Array.isArray(data) ? data : [data];
  return (
    <div className="logs-grid">
      {items.map((item, index) => (
        <article className="log-card" key={index}>
          <span>#{index + 1}</span>
          <pre>{JSON.stringify(item, null, 2)}</pre>
        </article>
      ))}
    </div>
  );
}

function TextPanel({ lines }) {
  if (!lines.length) {
    return <div className="empty-state">暂无日志内容</div>;
  }
  return (
    <div className="logs-list">
      {lines.map((line, index) => (
        <div className="log-line" key={`${index}-${line}`}>
          <span>{String(index + 1).padStart(3, "0")}</span>
          <code>{line}</code>
        </div>
      ))}
    </div>
  );
}

function LogsPage() {
  const [state, setState] = useState({ status: "idle", payload: null, error: "" });

  const load = useCallback(async () => {
    setState({ status: "loading", payload: null, error: "" });
    try {
      const payload = await api("/api/proxy/logs");
      setState({ status: "ready", payload, error: "" });
    } catch (err) {
      setState({ status: "error", payload: null, error: err.message });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="page-section">
      <div className="section-heading row-heading">
        <div>
          <p>Proxy</p>
          <h2>代理转发</h2>
        </div>
        <button className="secondary-button" onClick={load} disabled={state.status === "loading"}>
          <RefreshCw size={17} />
          刷新
        </button>
      </div>
      {state.status === "loading" ? <div className="empty-state">正在读取日志</div> : null}
      {state.status === "error" ? <div className="error-state">{state.error}</div> : null}
      {state.status === "ready" && state.payload?.type === "json" ? (
        <JsonPanel data={state.payload.data} />
      ) : null}
      {state.status === "ready" && state.payload?.type === "text" ? (
        <TextPanel lines={state.payload.data || []} />
      ) : null}
    </section>
  );
}

function AppShell({ onLogout }) {
  const [path, setPath] = useState(window.location.pathname);
  const navItems = useMemo(
    () => [
      { path: "/", label: "首页" },
      { path: "/proxy/logs", label: "代理转发" }
    ],
    []
  );

  function navigate(nextPath) {
    window.history.pushState({}, "", nextPath);
    setPath(nextPath);
  }

  useEffect(() => {
    const handler = () => setPath(window.location.pathname);
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    onLogout();
    window.history.replaceState({}, "", "/login");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-title">
          <TerminalSquare size={24} />
          <span>超级个人平台</span>
        </div>
        <nav>
          {navItems.map((item) => (
            <button
              key={item.path}
              className={path === item.path ? "active" : ""}
              onClick={() => navigate(item.path)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <button className="logout-button" onClick={logout}>
          <LogOut size={17} />
          退出
        </button>
      </aside>
      <main className="content">{path === "/proxy/logs" ? <LogsPage /> : <HomePage />}</main>
    </div>
  );
}

function Root() {
  const [checking, setChecking] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  async function refreshAuth() {
    const data = await api("/api/auth/me");
    setAuthenticated(data.authenticated);
    setChecking(false);
    if (!data.authenticated && window.location.pathname !== "/login") {
      window.history.replaceState({}, "", "/login");
    }
  }

  useEffect(() => {
    refreshAuth();
  }, []);

  if (checking) {
    return <div className="boot-screen">正在进入平台</div>;
  }

  if (!authenticated) {
    return <LoginPage onLogin={() => setAuthenticated(true)} />;
  }

  return <AppShell onLogout={() => setAuthenticated(false)} />;
}

createRoot(document.getElementById("root")).render(<Root />);
