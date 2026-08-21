import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Cpu,
  FileJson,
  FileText,
  FolderOpen,
  FolderTree,
  LogOut,
  Play,
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
  { id: "runs", path: "/runs", label: "Runs", icon: Play },
  { id: "workspace", path: "/workspace", label: "工作目录", icon: FolderTree },
  { id: "config", path: "/config", label: "配置", icon: SlidersHorizontal },
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
  ["workspace/runs/{run_id}/result.json", "DeepAgent 最终输出"],
  ["workspace/runs/{run_id}/delivery.json", "微信等渠道投递状态"],
  ["workspace/context/knowledge/files/", "默认 Context 知识目录，search_context / write_context 使用"],
  ["workspace/channels/wechat/sessions/{account_id}.json", "微信登录态，不作为聊天历史"],
  ["workspace/logs/platform-YYYY-MM-DD.log", "系统日志"],
];

const RUN_POLL_INTERVAL_MS = 60_000;

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
        {page === "runs" ? <RunsPage /> : null}
        {page === "workspace" ? <WorkspacePage /> : null}
        {page === "config" ? <ConfigPage onNavigate={navigate} /> : null}
        {page === "providers" ? <ProviderPage onNavigate={navigate} /> : null}
        {page === "agent-config" ? <AgentConfigPage onNavigate={navigate} /> : null}
        {page === "wechat" ? <WechatPage /> : null}
        {page === "system" ? <SystemPage onNavigate={navigate} /> : null}
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
    const timer = window.setInterval(poll, RUN_POLL_INTERVAL_MS);
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
        {runs.length === 0 ? <div className="empty-state">暂无 runs。创建任务后会写入 index.json。</div> : null}
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
        <strong>选择或创建一个 run</strong>
        <span>前端不会执行 Agent，只轮询磁盘状态和事件。</span>
      </section>
    );
  }
  const input = run.input || {};
  const state = run.state || {};
  const result = run.result?.content || run.result?.error?.message || "";
  const runId = run.run_id || input.run_id;
  const sessionId = input.session_id || state.session_id || run.session_id || "";
  const status = runStatus(run);

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
        <span className="toolbar-spacer" />
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

      {message ? <p className="ok">{message}</p> : null}
      {error ? <p className="error">{error}</p> : null}

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
        <div className="panel-title">
          <div>
            <span>{panelTitle}</span>
            <small>{configFile ? `${formatBytes(configFile.size)} · workspace/config.yaml` : "读取中"}</small>
          </div>
          <div className="config-page-actions">
            <button className="icon-button" onClick={loadConfig} title="重新读取">
              <RefreshCw size={15} />
            </button>
            <button className="primary" onClick={saveConfig} disabled={!configFile?.editable || !dirty}>
              <Save size={15} />
              保存
            </button>
          </div>
        </div>
        {configFile ? (
          <Editor draft={draft} onChange={setDraft} readOnly={!configFile.editable} />
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
      setActiveId((current) => current || nextAccounts[0]?.id || "");
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
          </div>
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

function WechatAccountDetail({ account, agents, onAction, onUpdate }) {
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
        <Status status={status.login_state} />
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

createRoot(document.getElementById("root")).render(<App />);
