import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowRight,
  LogOut,
  RefreshCw,
  ShieldCheck,
  TerminalSquare
} from "lucide-react";
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

function ProxyPage() {
  const [frameKey, setFrameKey] = useState(0);

  return (
    <section className="page-section">
      <div className="section-heading row-heading">
        <div>
          <p>Proxy</p>
          <h2>代理转发</h2>
        </div>
        <button className="secondary-button" onClick={() => setFrameKey((value) => value + 1)}>
          <RefreshCw size={17} />
          刷新
        </button>
      </div>
      <div className="proxy-frame-shell">
        <iframe
          key={frameKey}
          className="proxy-frame"
          src="/api/proxy/site/"
          title="代理转发"
        />
      </div>
    </section>
  );
}

function SystemPage({ onUnauthorized }) {
  const [updating, setUpdating] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  async function updateService() {
    setUpdating(true);
    setStatus("");
    setError("");
    try {
      const data = await api("/api/system/update-service", { method: "POST" });
      setStatus(data.message || "更新已开始，请稍后刷新页面。");
    } catch (err) {
      if (err.message === "Authentication required") {
        onUnauthorized();
        return;
      }
      if (err.message === "Failed to fetch") {
        setStatus("服务可能正在重启，请稍后刷新页面。");
        return;
      }
      setError(err.message);
    } finally {
      setUpdating(false);
    }
  }

  return (
    <section className="page-section">
      <div className="section-heading">
        <p>System</p>
        <h2>系统运维</h2>
      </div>
      <div className="system-grid">
        <article className="metric-card">
          <span>更新方式</span>
          <strong>systemd</strong>
          <p>生产环境通过 systemd 重启服务，前端产物随代码提交。</p>
        </article>
        <article className="action-panel">
          <div>
            <span>服务更新</span>
            <h3>拉取代码并重启</h3>
            <p>触发生产更新任务后，服务会短暂不可用。</p>
          </div>
          <button className="secondary-button" onClick={updateService} disabled={updating}>
            <RefreshCw size={17} />
            {updating ? "更新中" : "更新服务"}
          </button>
          {status ? <div className="status-message">{status}</div> : null}
          {error ? <div className="form-error">{error}</div> : null}
        </article>
      </div>
    </section>
  );
}

function AppShell({ onLogout }) {
  const [path, setPath] = useState(window.location.pathname);
  const navItems = useMemo(
    () => [
      { path: "/", label: "首页" },
      { path: "/proxy", label: "代理转发" },
      { path: "/system", label: "系统" }
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

  function unauthorized() {
    onLogout();
    window.history.replaceState({}, "", "/login");
    setPath("/login");
  }

  function renderPage() {
    if (path === "/proxy") {
      return <ProxyPage />;
    }
    if (path === "/system") {
      return <SystemPage onUnauthorized={unauthorized} />;
    }
    return <HomePage />;
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
      <main className="content">{renderPage()}</main>
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
