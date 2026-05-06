import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  ArrowRight,
  ChevronsLeft,
  ChevronsRight,
  FileText,
  History,
  List,
  LogOut,
  PlugZap,
  RefreshCw,
  Save,
  ScrollText,
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
    const error = new Error(data.detail || "请求失败");
    error.status = response.status;
    throw error;
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
      <div className="page-toolbar">
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
          title="Hermes UI"
        />
      </div>
    </section>
  );
}

function SystemPage({ onUnauthorized }) {
  const [activeTab, setActiveTab] = useState("config");
  const [updating, setUpdating] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [configPath, setConfigPath] = useState("");
  const [configContent, setConfigContent] = useState("");
  const [configLoading, setConfigLoading] = useState(true);
  const [configSaving, setConfigSaving] = useState(false);
  const [configStatus, setConfigStatus] = useState("");
  const [configError, setConfigError] = useState("");
  const [logs, setLogs] = useState([]);
  const [selectedLog, setSelectedLog] = useState("");
  const [logContent, setLogContent] = useState("");
  const [logInfo, setLogInfo] = useState(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logError, setLogError] = useState("");
  const logViewerRef = useRef(null);

  function handleApiError(err, setTargetError) {
    if (err.status === 401 || err.message === "Authentication required") {
      onUnauthorized();
      return true;
    }
    setTargetError(err.message);
    return false;
  }

  async function loadConfig() {
    setConfigLoading(true);
    setConfigStatus("");
    setConfigError("");
    try {
      const data = await api("/api/system/config/read", { method: "POST" });
      setConfigPath(data.path || "");
      setConfigContent(data.content || "");
    } catch (err) {
      handleApiError(err, setConfigError);
    } finally {
      setConfigLoading(false);
    }
  }

  async function saveConfig() {
    setConfigSaving(true);
    setConfigStatus("");
    setConfigError("");
    try {
      const data = await api("/api/system/config", {
        method: "PUT",
        body: JSON.stringify({ content: configContent })
      });
      setConfigStatus(data.message || "config.yaml 已保存");
    } catch (err) {
      handleApiError(err, setConfigError);
    } finally {
      setConfigSaving(false);
    }
  }

  useEffect(() => {
    loadConfig();
  }, []);

  useEffect(() => {
    if (logViewerRef.current) {
      logViewerRef.current.scrollTop = logViewerRef.current.scrollHeight;
    }
  }, [logContent, selectedLog]);

  async function loadLogs(nextSelectedLog = selectedLog) {
    setLogsLoading(true);
    setLogError("");
    try {
      const data = await api("/api/system/logs/list", { method: "POST" });
      const nextLogs = data.logs || [];
      setLogs(nextLogs);
      const nextLogName =
        nextLogs.find((log) => log.name === nextSelectedLog)?.name || nextLogs[0]?.name || "";
      setSelectedLog(nextLogName);
      if (nextLogName) {
        await readLog(nextLogName);
      } else {
        setLogContent("");
        setLogInfo(null);
      }
    } catch (err) {
      handleApiError(err, setLogError);
    } finally {
      setLogsLoading(false);
    }
  }

  async function readLog(name) {
    setLogsLoading(true);
    setLogError("");
    try {
      const data = await api("/api/system/logs/read", {
        method: "POST",
        body: JSON.stringify({ name })
      });
      setSelectedLog(data.name || name);
      setLogInfo(data);
      setLogContent(data.content || "");
    } catch (err) {
      handleApiError(err, setLogError);
    } finally {
      setLogsLoading(false);
    }
  }

  function formatBytes(value) {
    if (!Number.isFinite(value)) {
      return "0 B";
    }
    if (value < 1024) {
      return `${value} B`;
    }
    if (value < 1024 * 1024) {
      return `${(value / 1024).toFixed(1)} KB`;
    }
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }

  async function updateService() {
    setUpdating(true);
    setStatus("");
    setError("");
    try {
      const data = await api("/api/system/update-service", { method: "POST" });
      setStatus(data.message || "更新已开始，请稍后刷新页面。");
    } catch (err) {
      if (err.message === "Failed to fetch") {
        setStatus("服务可能正在重启，请稍后刷新页面。");
        return;
      }
      if (handleApiError(err, setError)) {
        return;
      }
      setError(err.message);
    } finally {
      setUpdating(false);
    }
  }

  return (
    <section className="page-section">
      <div className="tab-bar" role="tablist" aria-label="系统功能">
        {[
          { id: "config", label: "配置", icon: FileText },
          { id: "logs", label: "日志", icon: ScrollText },
          { id: "update", label: "更新", icon: RefreshCw }
        ].map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              className={activeTab === tab.id ? "active" : ""}
              onClick={() => {
                setActiveTab(tab.id);
                if (tab.id === "logs" && logs.length === 0) {
                  loadLogs();
                }
              }}
              role="tab"
              aria-selected={activeTab === tab.id}
            >
              <Icon size={16} />
              {tab.label}
            </button>
          );
        })}
      </div>
      {activeTab === "config" ? (
        <article className="config-panel">
          <div className="config-panel-heading">
            <div>
              <span>工作目录配置</span>
              <h3>config.yaml</h3>
              <p>{configPath || "正在读取配置路径"}</p>
            </div>
            <div className="config-actions">
              <button className="secondary-button" onClick={loadConfig} disabled={configLoading}>
                <RefreshCw size={17} />
                重新读取
              </button>
              <button
                className="secondary-button primary-action"
                onClick={saveConfig}
                disabled={configLoading || configSaving}
              >
                {configSaving ? <RefreshCw size={17} /> : <Save size={17} />}
                {configSaving ? "保存中" : "保存"}
              </button>
            </div>
          </div>
          <label className="config-editor-label" htmlFor="config-editor">
            <FileText size={16} />
            YAML
          </label>
          <textarea
            id="config-editor"
            className="config-editor"
            value={configContent}
            onChange={(event) => setConfigContent(event.target.value)}
            spellCheck="false"
            disabled={configLoading}
          />
          {configStatus ? <div className="status-message">{configStatus}</div> : null}
          {configError ? <div className="form-error">{configError}</div> : null}
        </article>
      ) : null}
      {activeTab === "logs" ? (
        <article className="log-panel">
          <div className="log-panel-heading">
            <div className="log-title">
              <span>统一日志</span>
              <h3 title={selectedLog || "platform-YYYY-MM-DD.log"}>
                {selectedLog || "platform-YYYY-MM-DD.log"}
              </h3>
              <p title={logInfo?.path || ""}>
                {logInfo?.path || "日志保存在工作目录 logs 下，保留最近 3 天。"}
              </p>
            </div>
            <button className="secondary-button" onClick={() => loadLogs()} disabled={logsLoading}>
              <RefreshCw size={17} />
              刷新
            </button>
          </div>
          <div className="log-layout">
            <div className="log-list" aria-label="日志文件列表">
              {logs.length ? (
                logs.map((log) => (
                  <button
                    key={log.name}
                    className={selectedLog === log.name ? "active" : ""}
                    onClick={() => readLog(log.name)}
                  >
                    <List size={15} />
                    <span>{log.name}</span>
                    <small>
                      {formatBytes(log.size)}
                      {log.modified_at ? ` · ${log.modified_at}` : ""}
                    </small>
                  </button>
                ))
              ) : (
                <div className="empty-state">暂无日志文件</div>
              )}
            </div>
            <div className="log-viewer-shell" data-testid="log-viewer-shell">
              {logInfo ? (
                <div className="log-meta">
                  <span>{formatBytes(logInfo.size)}</span>
                  <span>{logInfo.modified_at}</span>
                  {logInfo.truncated ? <span>仅显示尾部 200KB</span> : null}
                </div>
              ) : null}
              <pre className="log-viewer" ref={logViewerRef}>
                {logContent || "选择日志文件后查看内容"}
              </pre>
            </div>
          </div>
          {logError ? <div className="form-error">{logError}</div> : null}
        </article>
      ) : null}
      {activeTab === "update" ? (
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
              <p>触发生产更新任务后，输出会写入当天统一日志。</p>
            </div>
            <button className="secondary-button" onClick={updateService} disabled={updating}>
              <RefreshCw size={17} />
              {updating ? "更新中" : "更新服务"}
            </button>
            {status ? <div className="status-message">{status}</div> : null}
            {error ? <div className="form-error">{error}</div> : null}
          </article>
        </div>
      ) : null}
    </section>
  );
}

function TerminalPage({ onUnauthorized }) {
  const [status, setStatus] = useState("disconnected");
  const [sessions, setSessions] = useState([]);
  const [selectedSession, setSelectedSession] = useState(null);
  const [historyContent, setHistoryContent] = useState("");
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [error, setError] = useState("");
  const socketRef = useRef(null);
  const terminalRef = useRef(null);
  const terminalContainerRef = useRef(null);
  const fitAddonRef = useRef(null);
  const resizeObserverRef = useRef(null);

  function handleApiError(err) {
    if (err.status === 401 || err.message === "Authentication required") {
      onUnauthorized();
      return true;
    }
    setError(err.message);
    return false;
  }

  function websocketUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/api/system/terminal/connect`;
  }

  function connect() {
    if (socketRef.current && socketRef.current.readyState <= WebSocket.OPEN) {
      return;
    }
    setError("");
    setStatus("connecting");
    const socket = new WebSocket(websocketUrl());
    socketRef.current = socket;
    socket.onopen = () => {
      setStatus("connected");
      terminalRef.current?.focus();
      fitAndSendSize();
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "output") {
        terminalRef.current?.write(message.data || "");
      }
    };
    socket.onerror = () => {
      setError("终端连接失败");
      setStatus("disconnected");
    };
    socket.onclose = () => {
      setStatus("disconnected");
      loadSessions();
    };
  }

  function disconnect() {
    socketRef.current?.close();
  }

  function sendTerminalMessage(message) {
    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      return;
    }
    socketRef.current.send(JSON.stringify(message));
  }

  function fitAndSendSize() {
    if (!fitAddonRef.current || !terminalRef.current) {
      return;
    }
    fitAddonRef.current.fit();
    sendTerminalMessage({
      type: "resize",
      cols: terminalRef.current.cols,
      rows: terminalRef.current.rows
    });
  }

  async function loadSessions() {
    try {
      const data = await api("/api/system/terminal/sessions/list", { method: "POST" });
      setSessions(data.sessions || []);
    } catch (err) {
      handleApiError(err);
    }
  }

  async function readSession(name) {
    try {
      const data = await api("/api/system/terminal/sessions/read", {
        method: "POST",
        body: JSON.stringify({ name })
      });
      setSelectedSession(data);
      setHistoryContent(formatTranscript(data.content || ""));
    } catch (err) {
      handleApiError(err);
    }
  }

  function formatTranscript(content) {
    return content
      .split("\n")
      .filter(Boolean)
      .map((line) => {
        try {
          const event = JSON.parse(line);
          return `[${event.timestamp}] ${event.stream}: ${event.content}`;
        } catch {
          return line;
        }
      })
      .join("\n");
  }

  useEffect(() => {
    const terminal = new Terminal({
      cursorBlink: true,
      convertEol: false,
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      theme: {
        background: "#101820",
        foreground: "#f7f2e8",
        cursor: "#f7f2e8",
        selectionBackground: "#3f5f67"
      }
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalContainerRef.current);
    terminal.onData((data) => sendTerminalMessage({ type: "input", data }));
    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;
    resizeObserverRef.current = new ResizeObserver(() => fitAndSendSize());
    resizeObserverRef.current.observe(terminalContainerRef.current);

    loadSessions();
    connect();
    return () => {
      resizeObserverRef.current?.disconnect();
      socketRef.current?.close();
      terminal.dispose();
    };
  }, []);

  return (
    <section className={`page-section terminal-section ${historyCollapsed ? "history-collapsed" : ""}`}>
      <div className="terminal-shell">
        <div className="terminal-toolbar">
          <span className={`terminal-status ${status}`}>{status === "connected" ? "已连接" : "未连接"}</span>
          <div className="terminal-actions">
            <button className="secondary-button" onClick={connect} disabled={status === "connected"}>
              <PlugZap size={17} />
              连接
            </button>
            <button className="secondary-button" onClick={disconnect} disabled={status !== "connected"}>
              断开
            </button>
          </div>
        </div>
        <div className="terminal-output" ref={terminalContainerRef} data-testid="terminal-output" />
        {error ? <div className="form-error">{error}</div> : null}
      </div>
      <aside className="terminal-history">
        <div className="terminal-history-heading">
          <History size={17} />
          <span>历史会话</span>
          <button className="secondary-button" onClick={() => setHistoryCollapsed((value) => !value)}>
            {historyCollapsed ? <ChevronsLeft size={16} /> : <ChevronsRight size={16} />}
            {historyCollapsed ? "展开" : "收起"}
          </button>
          <button className="secondary-button" onClick={loadSessions}>
            <RefreshCw size={16} />
            刷新
          </button>
        </div>
        <div className="terminal-session-list">
          {sessions.length ? (
            sessions.map((session) => (
              <button
                key={session.name}
                className={selectedSession?.name === session.name ? "active" : ""}
                onClick={() => readSession(session.name)}
              >
                <span>{session.name}</span>
                <small>{session.modified_at}</small>
              </button>
            ))
          ) : (
            <div className="empty-state">暂无终端历史</div>
          )}
        </div>
        <pre className="terminal-history-content">
          {historyContent || "选择历史会话后查看转录"}
        </pre>
      </aside>
    </section>
  );
}

function AppShell({ onLogout }) {
  const [path, setPath] = useState(window.location.pathname);
  const navItems = useMemo(
    () => [
      { path: "/", label: "首页" },
      { path: "/terminal", label: "终端" },
      { path: "/proxy", label: "Hermes UI" },
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
    if (path === "/terminal") {
      return <TerminalPage onUnauthorized={unauthorized} />;
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
