import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  ArrowRight,
  Bot,
  ChevronsLeft,
  ChevronsRight,
  FileText,
  Globe2,
  History,
  Home,
  Image as ImageIcon,
  List,
  LogOut,
  PlugZap,
  Plus,
  RefreshCw,
  Save,
  ScrollText,
  Send,
  Settings,
  ShieldCheck,
  TerminalSquare,
  Trash2,
  X
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

function HomePage({ onNavigate }) {
  return (
    <section className="page-section">
      <div className="home-hero">
        <div>
          <span>控制台概览</span>
          <h2>个人平台运行中</h2>
          <p>统一入口承载 Agent、终端、系统配置和 Hermes UI。</p>
        </div>
        <div className="home-actions">
          <button className="secondary-button primary-action" onClick={() => onNavigate("/agents")}>
            <Bot size={17} />
            打开 Agent
          </button>
          <button className="secondary-button" onClick={() => onNavigate("/system")}>
            <Settings size={17} />
            系统设置
          </button>
        </div>
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
      <div className="quick-grid">
        <button className="quick-card" onClick={() => onNavigate("/terminal")}>
          <TerminalSquare size={18} />
          <span>终端</span>
          <p>打开认证后的后端 PTY 会话。</p>
        </button>
        <button className="quick-card" onClick={() => onNavigate("/proxy")}>
          <Globe2 size={18} />
          <span>Hermes UI</span>
          <p>进入同源代理后的上游应用。</p>
        </button>
        <button className="quick-card" onClick={() => onNavigate("/system")}>
          <ScrollText size={18} />
          <span>日志</span>
          <p>查看统一平台日志和更新输出。</p>
        </button>
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

function AgentPage({ onUnauthorized }) {
  const [activeTab, setActiveTab] = useState("chat");
  const [options, setOptions] = useState(null);
  const [config, setConfig] = useState(null);
  const [agentId, setAgentId] = useState("");
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState([]);
  const [status, setStatus] = useState("disconnected");
  const [loading, setLoading] = useState(true);
  const [configLoading, setConfigLoading] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [configError, setConfigError] = useState("");
  const [configStatus, setConfigStatus] = useState("");
  const socketRef = useRef(null);
  const messageListRef = useRef(null);
  const fileInputRef = useRef(null);

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
    return `${protocol}//${window.location.host}/api/agents/chat/connect`;
  }

  async function loadOptions() {
    setLoading(true);
    setError("");
    try {
      const data = await api("/api/agents/options");
      setOptions(data);
      setAgentId(data.default_agent_id || data.agents?.[0]?.id || "");
    } catch (err) {
      handleApiError(err);
    } finally {
      setLoading(false);
    }
  }

  async function loadAgentConfig() {
    setConfigLoading(true);
    setConfigError("");
    setConfigStatus("");
    try {
      const data = await api("/api/agents/config");
      setConfig(data);
    } catch (err) {
      handleApiError(err);
      setConfigError(err.message);
    } finally {
      setConfigLoading(false);
    }
  }

  function connect() {
    if (socketRef.current && socketRef.current.readyState <= WebSocket.OPEN) {
      return;
    }
    setError("");
    setStatus("connecting");
    const socket = new WebSocket(websocketUrl());
    socketRef.current = socket;
    socket.onopen = () => setStatus("connected");
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "status") {
        setStatus(message.status === "running" ? "running" : "connected");
      }
      if (message.type === "assistant_message") {
        setMessages((items) => [
          ...items.filter((item) => item.role !== "pending"),
          { role: "assistant", content: message.content || "" }
        ]);
        setSending(false);
        setStatus("connected");
      }
      if (message.type === "checkpoint") {
        setMessages((items) =>
          items.map((item) =>
            item.role === "pending"
              ? {
                  ...item,
                  checkpoints: [
                    ...(item.checkpoints || []),
                    {
                      stage: message.stage || "",
                      title: message.title || "",
                      detail: message.detail || ""
                    }
                  ]
                }
              : item
          )
        );
      }
      if (message.type === "error") {
        setMessages((items) => items.filter((item) => item.role !== "pending"));
        setError(message.message || "Agent 回复失败");
        setSending(false);
        setStatus("connected");
      }
    };
    socket.onerror = () => {
      setError("Agent 连接失败");
      setSending(false);
      setStatus("disconnected");
    };
    socket.onclose = () => {
      setSending(false);
      setStatus("disconnected");
    };
  }

  function sendMessage() {
    const content = input.trim();
    if ((!content && attachments.length === 0) || sending || !agentId || blocked) {
      return;
    }
    if (attachments.length > 0 && !selectedAgent?.model?.supports_images) {
      setError("当前模型不支持图片输入");
      return;
    }
    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      connect();
      setError("连接未就绪，请稍后重试");
      return;
    }
    setError("");
    setInput("");
    setAttachments([]);
    setSending(true);
    setMessages((items) => [
      ...items,
      { role: "user", content, images: attachments },
      { role: "pending", content: "生成中", checkpoints: [] }
    ]);
    socketRef.current.send(
      JSON.stringify({
        type: "message",
        agent_id: agentId,
        content,
        images: attachments.map((image) => ({
          mime_type: image.mime_type,
          data: image.data
        }))
      })
    );
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }

  useEffect(() => {
    loadOptions();
    loadAgentConfig();
    connect();
    return () => socketRef.current?.close();
  }, []);

  useEffect(() => {
    if (messageListRef.current) {
      messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
    }
  }, [messages]);

  const selectedAgent = options?.agents?.find((agent) => agent.id === agentId);
  const canChat = Boolean(options?.agents?.length);
  const blocked = !loading && (!canChat || !selectedAgent?.model || !selectedAgent?.model?.has_api_key);

  async function addFiles(fileList) {
    const imageFiles = Array.from(fileList || []).filter((file) =>
      ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)
    );
    const nextImages = await Promise.all(
      imageFiles.map(
        (file) =>
          new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
              const result = String(reader.result || "");
              resolve({
                id: `${file.name}-${file.size}-${Date.now()}-${Math.random()}`,
                name: file.name,
                mime_type: file.type,
                data: result.split(",")[1] || "",
                preview: result
              });
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
          })
      )
    );
    setAttachments((items) => [...items, ...nextImages]);
  }

  function handlePaste(event) {
    const files = Array.from(event.clipboardData?.files || []);
    if (files.some((file) => file.type.startsWith("image/"))) {
      addFiles(files);
    }
  }

  function updateModel(index, field, value) {
    setConfig((current) => ({
      ...current,
      models: current.models.map((model, modelIndex) =>
        modelIndex === index ? { ...model, [field]: value } : model
      )
    }));
  }

  function updateAgent(index, field, value) {
    setConfig((current) => ({
      ...current,
      agents: current.agents.map((agent, agentIndex) =>
        agentIndex === index ? { ...agent, [field]: value } : agent
      )
    }));
  }

  function updateCommonSkillTools(value) {
    setConfig((current) => ({
      ...current,
      common_skill_tools: value.split(/\s+/).map((item) => item.trim()).filter(Boolean)
    }));
  }

  function updateAgentSkillIds(index, value) {
    updateAgent(index, "skill_ids", value.split(/\s+/).map((item) => item.trim()).filter(Boolean));
  }

  function addModel() {
    const id = `model-${(config?.models?.length || 0) + 1}`;
    setConfig((current) => ({
      ...current,
      default_model_id: current.default_model_id || id,
      models: [
        ...current.models,
        {
          id,
          name: "新模型",
          base_url: "https://api.openai.com/v1",
          model: "",
          api_key: "",
          temperature: 0.7,
          supports_images: false,
          has_api_key: false,
          api_key_mask: ""
        }
      ]
    }));
  }

  function addAgent() {
    const id = `agent-${(config?.agents?.length || 0) + 1}`;
    setConfig((current) => ({
      ...current,
      default_agent_id: current.default_agent_id || id,
      agents: [
        ...current.agents,
        {
          id,
          name: "新 Agent",
          model_id: current.default_model_id || current.models?.[0]?.id || "",
          system_prompt: "",
          skill_ids: []
        }
      ]
    }));
  }

  async function saveAgentConfig() {
    setConfigSaving(true);
    setConfigError("");
    setConfigStatus("");
    try {
      await api("/api/agents/config", {
        method: "PUT",
        body: JSON.stringify({
          default_model_id: config.default_model_id || config.models?.[0]?.id || "",
          default_agent_id: config.default_agent_id || config.agents?.[0]?.id || "",
          common_skill_tools: config.common_skill_tools || [],
          models: config.models.map((model) => ({
            id: model.id,
            name: model.name,
            base_url: model.base_url,
            model: model.model,
            api_key: model.api_key || "",
            temperature: model.temperature === "" ? null : Number(model.temperature),
            supports_images: Boolean(model.supports_images)
          })),
          agents: config.agents.map((agent) => ({
            id: agent.id,
            name: agent.name,
            model_id: agent.model_id,
            system_prompt: agent.system_prompt,
            skill_ids: agent.skill_ids || []
          }))
        })
      });
      setConfigStatus("Agent 配置已保存");
      await loadAgentConfig();
      await loadOptions();
    } catch (err) {
      handleApiError(err);
      setConfigError(err.message);
    } finally {
      setConfigSaving(false);
    }
  }

  return (
    <section className="page-section agent-section">
      <div className="tab-bar" role="tablist" aria-label="Agent">
        <button className={activeTab === "chat" ? "active" : ""} onClick={() => setActiveTab("chat")}>
          <Bot size={16} />
          对话
        </button>
        <button className={activeTab === "config" ? "active" : ""} onClick={() => setActiveTab("config")}>
          <Save size={16} />
          配置
        </button>
      </div>
      {activeTab === "chat" ? (
        <div className="agent-chat-shell">
          <div className="agent-chat-topbar">
            <select
              aria-label="Agent"
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
              disabled={loading || !canChat}
            >
              {(options?.agents || []).map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
            <button className="secondary-button" onClick={connect} disabled={status === "connected" || loading}>
              <PlugZap size={17} />
              {status === "disconnected" ? "重连" : "已连接"}
            </button>
            <span className={`terminal-status ${status === "connected" || status === "running" ? "connected" : ""}`}>
              {status === "running" ? "生成中" : status === "connected" ? "已连接" : "未连接"}
            </span>
          </div>
          <div className="agent-message-list" ref={messageListRef}>
            {loading ? <div className="empty-state">正在加载 Agent 配置</div> : null}
            {!loading && error && !options ? <div className="error-state">{error}</div> : null}
            {!loading && !options?.agents?.length ? <div className="empty-state">请先在配置页添加 Agent</div> : null}
            {!loading && selectedAgent && !selectedAgent.model ? <div className="empty-state">Agent 未配置模型</div> : null}
            {!loading && selectedAgent?.model && !selectedAgent.model.has_api_key ? (
              <div className="empty-state">模型 API Key 不可用</div>
            ) : null}
            {!loading && !blocked && messages.length === 0 ? <div className="empty-state">开始对话</div> : null}
            {messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`agent-message ${message.role}`}>
                <span>{message.role === "user" ? "你" : message.role === "assistant" ? "Agent" : ""}</span>
                {message.content ? <p>{message.content}</p> : null}
                {message.checkpoints?.length ? (
                  <ol className="agent-checkpoints">
                    {message.checkpoints.map((checkpoint, checkpointIndex) => (
                      <li key={`${checkpoint.stage}-${checkpointIndex}`}>
                        <strong>{checkpoint.title}</strong>
                        {checkpoint.detail ? <small>{checkpoint.detail}</small> : null}
                      </li>
                    ))}
                  </ol>
                ) : null}
                {message.images?.length ? (
                  <div className="agent-message-images">
                    {message.images.map((image) => (
                      <img key={image.id} src={image.preview} alt={image.name || "上传图片"} />
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
          {error && options ? <div className="form-error">{error}</div> : null}
          <div className="agent-input-row">
            {attachments.length ? (
              <div className="agent-attachments">
                {attachments.map((image) => (
                  <div key={image.id} className="agent-attachment">
                    <img src={image.preview} alt={image.name} />
                    <button
                      type="button"
                      aria-label={`移除 ${image.name}`}
                      onClick={() => setAttachments((items) => items.filter((item) => item.id !== image.id))}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="agent-composer">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                multiple
                hidden
                onChange={(event) => {
                  addFiles(event.target.files);
                  event.target.value = "";
                }}
              />
              <button
                className="secondary-button agent-icon-button"
                onClick={() => fileInputRef.current?.click()}
                disabled={loading || !canChat || sending}
                aria-label="添加图片"
              >
                <ImageIcon size={17} />
              </button>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder={canChat ? "输入消息，Enter 发送，Shift+Enter 换行" : "Agent 未配置"}
                disabled={loading || blocked || sending}
              />
              <button
                className="secondary-button primary-action"
                onClick={sendMessage}
                disabled={loading || blocked || sending || (!input.trim() && attachments.length === 0)}
              >
                <Send size={17} />
                {sending ? "发送中" : "发送"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {activeTab === "config" ? (
        <div className="agent-config-panel">
          <div className="agent-config-heading">
            <div>
              <span>Workspace 配置</span>
              <p>{config?.path || "正在读取 config.yaml"}</p>
            </div>
            <div className="config-actions">
              <button className="secondary-button" onClick={loadAgentConfig} disabled={configLoading}>
                <RefreshCw size={17} />
                重新读取
              </button>
              <button className="secondary-button primary-action" onClick={saveAgentConfig} disabled={!config || configSaving}>
                <Save size={17} />
                {configSaving ? "保存中" : "保存到 workspace"}
              </button>
            </div>
          </div>
          {configError ? <div className="form-error">{configError}</div> : null}
          {configStatus ? <div className="status-message">{configStatus}</div> : null}
          {config ? (
            <>
              <section className="agent-config-section">
                <div className="agent-config-section-heading">
                  <h3>Common Skill Tools</h3>
                </div>
                <article className="agent-config-card">
                  <label className="agent-prompt-label">工具声明<textarea value={(config.common_skill_tools || []).join("\n")} onChange={(event) => updateCommonSkillTools(event.target.value)} /></label>
                </article>
              </section>
              <section className="agent-config-section">
                <div className="agent-config-section-heading">
                  <h3>模型</h3>
                  <button className="secondary-button" onClick={addModel}>
                    <Plus size={17} />
                    添加模型
                  </button>
                </div>
                <div className="agent-config-list">
                  {config.models.map((model, index) => (
                    <article className="agent-config-card" key={`${model.id}-${index}`}>
                      <div className="agent-config-grid">
                        <label>ID<input value={model.id} onChange={(event) => updateModel(index, "id", event.target.value)} /></label>
                        <label>显示名<input value={model.name} onChange={(event) => updateModel(index, "name", event.target.value)} /></label>
                        <label>Base URL<input value={model.base_url} onChange={(event) => updateModel(index, "base_url", event.target.value)} /></label>
                        <label>Model<input value={model.model} onChange={(event) => updateModel(index, "model", event.target.value)} /></label>
                        <label>API Key<input type="password" placeholder={model.api_key_mask || "留空保留旧 key"} value={model.api_key || ""} onChange={(event) => updateModel(index, "api_key", event.target.value)} /></label>
                        <label>Temperature<input type="number" step="0.1" value={model.temperature ?? ""} onChange={(event) => updateModel(index, "temperature", event.target.value)} /></label>
                      </div>
                      <div className="agent-config-card-actions">
                        <label className="agent-checkbox"><input type="checkbox" checked={Boolean(model.supports_images)} onChange={(event) => updateModel(index, "supports_images", event.target.checked)} />支持图片</label>
                        <button className="secondary-button" onClick={() => setConfig((current) => ({ ...current, default_model_id: model.id }))}>设为默认</button>
                        <button className="secondary-button" onClick={() => setConfig((current) => ({ ...current, models: current.models.filter((_, itemIndex) => itemIndex !== index) }))}>删除</button>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
              <section className="agent-config-section">
                <div className="agent-config-section-heading">
                  <h3>Agent</h3>
                  <button className="secondary-button" onClick={addAgent}>
                    <Plus size={17} />
                    添加 Agent
                  </button>
                </div>
                <div className="agent-config-list">
                  {config.agents.map((agent, index) => (
                    <article className="agent-config-card" key={`${agent.id}-${index}`}>
                      <div className="agent-config-grid">
                        <label>ID<input value={agent.id} onChange={(event) => updateAgent(index, "id", event.target.value)} /></label>
                        <label>名称<input value={agent.name} onChange={(event) => updateAgent(index, "name", event.target.value)} /></label>
                        <label>绑定模型<select value={agent.model_id || ""} onChange={(event) => updateAgent(index, "model_id", event.target.value)}>{config.models.map((model) => <option key={model.id} value={model.id}>{model.name || model.id}</option>)}</select></label>
                      </div>
                      <label className="agent-prompt-label">系统提示词<textarea value={agent.system_prompt} onChange={(event) => updateAgent(index, "system_prompt", event.target.value)} /></label>
                      <label className="agent-prompt-label">Skill IDs<textarea value={(agent.skill_ids || []).join("\n")} onChange={(event) => updateAgentSkillIds(index, event.target.value)} /></label>
                      <div className="agent-config-card-actions">
                        <button className="secondary-button" onClick={() => setConfig((current) => ({ ...current, default_agent_id: agent.id }))}>设为默认</button>
                        <button className="secondary-button" onClick={() => setConfig((current) => ({ ...current, agents: current.agents.filter((_, itemIndex) => itemIndex !== index) }))}>删除</button>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            </>
          ) : (
            <div className="empty-state">正在加载 Agent 配置</div>
          )}
        </div>
      ) : null}
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

  async function deleteSession(name) {
    try {
      await api("/api/system/terminal/sessions/delete", {
        method: "POST",
        body: JSON.stringify({ name })
      });
      if (selectedSession?.name === name) {
        setSelectedSession(null);
        setHistoryContent("");
      }
      await loadSessions();
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
              <div
                key={session.name}
                className={`terminal-session-item ${selectedSession?.name === session.name ? "active" : ""}`}
              >
                <button className="terminal-session-open" onClick={() => readSession(session.name)}>
                  <span>{session.name}</span>
                  <small>{session.modified_at}</small>
                </button>
                <button
                  className="terminal-session-delete"
                  aria-label={`删除 ${session.name}`}
                  onClick={() => deleteSession(session.name)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
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
      { path: "/", label: "首页", icon: Home },
      { path: "/agents", label: "Agent", icon: Bot },
      { path: "/terminal", label: "终端", icon: TerminalSquare },
      { path: "/proxy", label: "Hermes UI", icon: Globe2 },
      { path: "/system", label: "系统", icon: Settings }
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
    if (path === "/agents") {
      return <AgentPage onUnauthorized={unauthorized} />;
    }
    if (path === "/system") {
      return <SystemPage onUnauthorized={unauthorized} />;
    }
    if (path === "/terminal") {
      return <TerminalPage onUnauthorized={unauthorized} />;
    }
    return <HomePage onNavigate={navigate} />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-title">
          <TerminalSquare size={24} />
          <span>超级个人平台</span>
        </div>
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.path}
                className={path === item.path ? "active" : ""}
                onClick={() => navigate(item.path)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
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
