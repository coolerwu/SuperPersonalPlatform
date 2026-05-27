import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  ArrowRight,
  Bot,
  Check,
  CheckCircle,
  Circle,
  Clock,
  Code2,
  Eye,
  FileCode,
  FileMinus,
  FilePlus,
  FileText,
  GitBranch,
  Globe2,
  HelpCircle,
  Home,
  Image as ImageIcon,
  List,
  Loader2,
  LogOut,
  MessageSquare,
  PauseCircle,
  Play,
  PlugZap,
  Plus,
  RefreshCw,
  Save,
  ScrollText,
  Send,
  Settings,
  ShieldCheck,
  Terminal as TerminalIcon,
  TerminalSquare,
  User,
  X,
  XCircle
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
  const primaryItems = [
    {
      path: "/agents",
      icon: Bot,
      title: "Agent 对话",
      desc: "处理问题、上传截图、跟进上下文。",
      meta: "默认入口"
    },
    {
      path: "/self-dev",
      icon: Code2,
      title: "自开发",
      desc: "创建任务、查看变更、审查推送。",
      meta: "任务队列"
    },
    {
      path: "/terminal",
      icon: TerminalSquare,
      title: "终端",
      desc: "认证 PTY，会话不落盘。",
      meta: "本机命令"
    }
  ];
  const secondaryItems = [
    {
      path: "/channels",
      icon: MessageSquare,
      title: "渠道",
      desc: "个人微信扫码机器人、Webhook。",
      meta: "QR"
    },
    {
      path: "/system",
      icon: Settings,
      title: "系统",
      desc: "配置、日志、更新。",
      meta: "ops"
    },
    {
      path: "/proxy",
      icon: Globe2,
      title: "Hermes UI",
      desc: "同源代理的上游工作区。",
      meta: "proxy"
    }
  ];

  return (
    <section className="page-section home-workspace">
      <div className="home-console">
        <div className="home-console-title">
          <span>Workspace</span>
          <h2>控制台</h2>
        </div>
        <div className="home-status-strip" aria-label="运行状态">
          <span><ShieldCheck size={15} /> Token 会话</span>
          <span><PlugZap size={15} /> localhost:8888</span>
          <span><Clock size={15} /> FastAPI 同源</span>
        </div>
        <button className="home-agent-command" onClick={() => onNavigate("/agents")}>
          <span><Bot size={18} /></span>
          <strong>今天想处理什么？</strong>
          <small>打开 Agent 对话，直接输入任务或上传截图</small>
          <ArrowRight size={17} />
        </button>
      </div>

      <div className="home-grid">
        <div className="home-primary-grid">
          {primaryItems.map((item) => {
            const Icon = item.icon;
            return (
              <button key={item.path} className="home-tool-card" onClick={() => onNavigate(item.path)}>
                <span className="workspace-row-icon"><Icon size={18} /></span>
                <strong>{item.title}</strong>
                <small>{item.desc}</small>
                <em>{item.meta}</em>
              </button>
            );
          })}
        </div>
        <div className="workspace-list compact" aria-label="辅助入口">
          {secondaryItems.map((item) => {
            const Icon = item.icon;
            return (
              <button key={item.path} className="workspace-row" onClick={() => onNavigate(item.path)}>
                <span className="workspace-row-icon"><Icon size={17} /></span>
                <span className="workspace-row-copy">
                  <strong>{item.title}</strong>
                  <small>{item.desc}</small>
                </span>
                <span className="workspace-row-meta">{item.meta}</span>
                <ArrowRight size={15} />
              </button>
            );
          })}
        </div>
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

function renderInlineMarkdown(text, keyPrefix) {
  const parts = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let lastIndex = 0;
  let match;
  let index = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("**")) {
      parts.push(<strong key={`${keyPrefix}-strong-${index}`}>{token.slice(2, -2)}</strong>);
    } else {
      parts.push(<code key={`${keyPrefix}-code-${index}`}>{token.slice(1, -1)}</code>);
    }
    lastIndex = pattern.lastIndex;
    index += 1;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

function MarkdownMessage({ content }) {
  const blocks = String(content || "").split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  return (
    <div className="agent-markdown">
      {blocks.map((block, blockIndex) => {
        const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
        const ordered = lines.every((line) => /^\d+\.\s+/.test(line));
        const unordered = lines.every((line) => /^[-*]\s+/.test(line));
        if (ordered || unordered) {
          const ListTag = ordered ? "ol" : "ul";
          return (
            <ListTag key={`block-${blockIndex}`}>
              {lines.map((line, lineIndex) => (
                <li key={`line-${lineIndex}`}>
                  {renderInlineMarkdown(line.replace(ordered ? /^\d+\.\s+/ : /^[-*]\s+/, ""), `${blockIndex}-${lineIndex}`)}
                </li>
              ))}
            </ListTag>
          );
        }
        return (
          <p key={`block-${blockIndex}`}>
            {renderInlineMarkdown(lines.join("\n"), `${blockIndex}`)}
          </p>
        );
      })}
    </div>
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
  const quickPrompts = [
    "总结一下今天需要处理的事项",
    "帮我检查系统设置有没有风险",
    "根据截图分析这个 UI 怎么改",
    "生成一个开发任务说明"
  ];

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

  function updatePlatformTools(field, value) {
    setConfig((current) => ({
      ...current,
      tools: {
        ...(current.tools || { profile: "default", allow: [], deny: [] }),
        [field]: field === "profile" ? value : value.split(/\s+/).map((item) => item.trim()).filter(Boolean)
      }
    }));
  }

  function updateAgentTools(index, field, value) {
    setConfig((current) => ({
      ...current,
      agents: current.agents.map((agent, agentIndex) =>
        agentIndex === index
          ? {
              ...agent,
              tools: {
                ...(agent.tools || { profile: config?.tools?.profile || "default", allow: [], deny: [] }),
                [field]: field === "profile" ? value : value.split(/\s+/).map((item) => item.trim()).filter(Boolean)
              }
            }
          : agent
      )
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
          tools: config.tools || { profile: "default", allow: [], deny: [] },
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
            skill_ids: agent.skill_ids || [],
            tools: agent.tools || null
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
    <section className={`page-section agent-section${activeTab === "config" ? " agent-section-config" : ""}`}>
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
        <div className="agent-chat-shell ai-chat-workspace">
          <aside className="ai-chat-sidebar">
            <div className="ai-agent-card">
              <span className="ai-agent-orb">
                <Bot size={18} />
              </span>
              <div>
                <strong>{selectedAgent?.name || "Agent"}</strong>
                <small>{selectedAgent?.model?.name || "未选择模型"}</small>
              </div>
            </div>
            <label className="ai-agent-select">
              <span>当前 Agent</span>
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
            </label>
            <div className="ai-session-list">
              <span>快捷开始</span>
              {quickPrompts.map((prompt) => (
                <button key={prompt} type="button" onClick={() => setInput(prompt)}>
                  <MessageSquare size={14} />
                  {prompt}
                </button>
              ))}
            </div>
            <div className="ai-agent-health">
              <span className={`terminal-status ${status === "connected" || status === "running" ? "connected" : ""}`}>
                {status === "running" ? "生成中" : status === "connected" ? "已连接" : "未连接"}
              </span>
              <button className="secondary-button" onClick={connect} disabled={status === "connected" || loading}>
                <PlugZap size={15} />
                重连
              </button>
            </div>
          </aside>
          <main className="ai-chat-main">
            <div className="ai-chat-topline">
              <div>
                <strong>{selectedAgent?.name || "Agent Chat"}</strong>
                <span>
                  {selectedAgent?.model?.model || "model"}
                  {selectedAgent?.model?.supports_images ? " · vision" : ""}
                </span>
              </div>
              <div className="ai-chat-badges">
                {selectedAgent?.model?.has_api_key ? <span>key ready</span> : <span>key missing</span>}
                {selectedAgent?.model?.supports_images ? <span>image input</span> : null}
              </div>
            </div>
            <div className="agent-message-list" ref={messageListRef}>
              {loading ? <div className="empty-state">正在加载 Agent 配置</div> : null}
              {!loading && error && !options ? <div className="error-state">{error}</div> : null}
              {!loading && !options?.agents?.length ? <div className="empty-state">请先在配置页添加 Agent</div> : null}
              {!loading && selectedAgent && !selectedAgent.model ? <div className="empty-state">Agent 未配置模型</div> : null}
              {!loading && selectedAgent?.model && !selectedAgent.model.has_api_key ? (
                <div className="empty-state">模型 API Key 不可用</div>
              ) : null}
              {!loading && !blocked && messages.length === 0 ? (
                <div className="agent-welcome">
                  <h2>开始新的对话</h2>
                  <p>选择一个快捷问题，或者直接输入你的任务。</p>
                  <div className="ai-empty-prompts">
                    {quickPrompts.slice(0, 3).map((prompt) => (
                      <button key={prompt} type="button" onClick={() => setInput(prompt)}>
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
              {messages.map((message, index) => (
                <div key={`${message.role}-${index}`} className={`agent-message ${message.role}`}>
                  <span>{message.role === "user" ? "你" : message.role === "assistant" ? selectedAgent?.name || "Agent" : ""}</span>
                  {message.content && message.role === "assistant" ? <MarkdownMessage content={message.content} /> : null}
                  {message.content && message.role !== "assistant" ? <p>{message.content}</p> : null}
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
                  title="添加图片"
                >
                  <ImageIcon size={20} />
                </button>
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder={canChat ? "输入消息..." : "Agent 未配置"}
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
          </main>
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
            <div className="agent-config-workspace">
              <aside className="agent-config-rail">
                <div className="agent-config-rail-card">
                  <span>默认 Agent</span>
                  <strong>{config.agents.find((agent) => agent.id === config.default_agent_id)?.name || config.default_agent_id || "未设置"}</strong>
                </div>
                <div className="agent-config-rail-card">
                  <span>默认模型</span>
                  <strong>{config.models.find((model) => model.id === config.default_model_id)?.name || config.default_model_id || "未设置"}</strong>
                </div>
                <div className="agent-config-rail-card compact">
                  <small>{config.models.length} 个模型</small>
                  <small>{config.agents.length} 个 Agent</small>
                  <small>Tools: {config.tools?.profile || "default"}</small>
                </div>
              </aside>
              <div className="agent-config-content">
              <section className="agent-config-section">
                <div className="agent-config-section-heading">
                  <div>
                    <h3>工具权限</h3>
                    <p>控制平台级工具集合，Agent 可在自己的配置中覆盖。</p>
                  </div>
                </div>
                <article className="agent-config-card">
                  <div className="agent-config-grid">
                    <label>工具模板<select value={config.tools?.profile || "default"} onChange={(event) => updatePlatformTools("profile", event.target.value)}><option value="default">default</option><option value="self-dev">self-dev</option></select></label>
                    <label className="agent-prompt-label">允许工具<textarea placeholder="每行一个工具 id" value={(config.tools?.allow || []).join("\n")} onChange={(event) => updatePlatformTools("allow", event.target.value)} /></label>
                    <label className="agent-prompt-label">禁用工具<textarea placeholder="每行一个工具 id，优先级高于允许列表" value={(config.tools?.deny || []).join("\n")} onChange={(event) => updatePlatformTools("deny", event.target.value)} /></label>
                  </div>
                </article>
              </section>
              <section className="agent-config-section">
                <div className="agent-config-section-heading">
                  <div>
                    <h3>模型</h3>
                    <p>配置 OpenAI-compatible 接口，API Key 留空会保留原值。</p>
                  </div>
                  <button className="secondary-button" onClick={addModel}>
                    <Plus size={17} />
                    添加模型
                  </button>
                </div>
                <div className="agent-config-list">
                  {config.models.map((model, index) => (
                    <article className="agent-config-card" key={`${model.id}-${index}`}>
                      <div className="agent-config-card-title">
                        <strong>{model.name || model.id || "未命名模型"}</strong>
                        {config.default_model_id === model.id ? <span>默认</span> : null}
                      </div>
                      <div className="agent-config-grid">
                        <label>ID<input value={model.id} onChange={(event) => updateModel(index, "id", event.target.value)} /></label>
                        <label>显示名<input value={model.name} onChange={(event) => updateModel(index, "name", event.target.value)} /></label>
                        <label>Base URL<input value={model.base_url} onChange={(event) => updateModel(index, "base_url", event.target.value)} /></label>
                        <label>模型名<input value={model.model} onChange={(event) => updateModel(index, "model", event.target.value)} /></label>
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
                  <div>
                    <h3>Agent</h3>
                    <p>定义人格、系统提示词、绑定模型和私有工具权限。</p>
                  </div>
                  <button className="secondary-button" onClick={addAgent}>
                    <Plus size={17} />
                    添加 Agent
                  </button>
                </div>
                <div className="agent-config-list">
                  {config.agents.map((agent, index) => (
                    <article className="agent-config-card" key={`${agent.id}-${index}`}>
                      <div className="agent-config-card-title">
                        <strong>{agent.name || agent.id || "未命名 Agent"}</strong>
                        {config.default_agent_id === agent.id ? <span>默认</span> : null}
                      </div>
                      <div className="agent-config-grid">
                        <label>ID<input value={agent.id} onChange={(event) => updateAgent(index, "id", event.target.value)} /></label>
                        <label>名称<input value={agent.name} onChange={(event) => updateAgent(index, "name", event.target.value)} /></label>
                        <label>绑定模型<select value={agent.model_id || ""} onChange={(event) => updateAgent(index, "model_id", event.target.value)}>{config.models.map((model) => <option key={model.id} value={model.id}>{model.name || model.id}</option>)}</select></label>
                      </div>
                      <label className="agent-prompt-label large">系统提示词<textarea value={agent.system_prompt} onChange={(event) => updateAgent(index, "system_prompt", event.target.value)} /></label>
                      <label className="agent-prompt-label">Skill IDs<textarea placeholder="每行一个 skill id，如 common:xxx" value={(agent.skill_ids || []).join("\n")} onChange={(event) => updateAgentSkillIds(index, event.target.value)} /></label>
                      <div className="agent-config-grid">
                        <label>工具模板<select value={agent.tools?.profile || config.tools?.profile || "default"} onChange={(event) => updateAgentTools(index, "profile", event.target.value)}><option value="default">default</option><option value="self-dev">self-dev</option></select></label>
                        <label className="agent-prompt-label">允许工具<textarea value={(agent.tools?.allow || []).join("\n")} onChange={(event) => updateAgentTools(index, "allow", event.target.value)} /></label>
                        <label className="agent-prompt-label">禁用工具<textarea value={(agent.tools?.deny || []).join("\n")} onChange={(event) => updateAgentTools(index, "deny", event.target.value)} /></label>
                      </div>
                      <div className="agent-config-card-actions">
                        <button className="secondary-button" onClick={() => setConfig((current) => ({ ...current, default_agent_id: agent.id }))}>设为默认</button>
                        <button className="secondary-button" onClick={() => setConfig((current) => ({ ...current, agents: current.agents.filter((_, itemIndex) => itemIndex !== index) }))}>删除</button>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
              </div>
            </div>
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

function SelfDevPage({ onUnauthorized }) {
  const [agents, setAgents] = useState([]);
  const [agentId, setAgentId] = useState("");
  const [goal, setGoal] = useState("");
  const [repoUrl, setRepoUrl] = useState("https://github.com/coolerwu/SuperPersonalPlatform.git");
  const [tasks, setTasks] = useState([]);
  const [selectedTask, setSelectedTask] = useState(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [taskChatInput, setTaskChatInput] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [taskTab, setTaskTab] = useState("chat");

  const taskActions = useMemo(() => ({
    createAndRun: {
      requireTask: false,
      run: async () => {
        const created = await api("/api/self-dev/tasks", {
          method: "POST",
          body: JSON.stringify({ goal, agent_id: agentId, repo_url: repoUrl })
        });
        const runData = await api(`/api/self-dev/tasks/${created.task.id}/run`, {
          method: "POST",
          body: JSON.stringify({ instruction: goal })
        });
        setGoal("");
        return runData.task;
      }
    },
    chat: {
      requireTask: true,
      run: async (task) => {
        const data = await api(`/api/self-dev/tasks/${task.id}/run`, {
          method: "POST",
          body: JSON.stringify({ instruction: taskChatInput })
        });
        setTaskChatInput("");
        return data.task;
      }
    },
    accept: {
      requireTask: true,
      run: async (task) => {
        const data = await api(`/api/self-dev/tasks/${task.id}/accept`, {
          method: "POST",
          body: JSON.stringify({ note: reviewNote })
        });
        setReviewNote("");
        return data.task;
      }
    },
    reject: {
      requireTask: true,
      run: async (task) => {
        const data = await api(`/api/self-dev/tasks/${task.id}/reject`, {
          method: "POST",
          body: JSON.stringify({ reason: reviewNote })
        });
        setReviewNote("");
        return data.task;
      }
    },
    cancel: {
      requireTask: true,
      run: async (task) => {
        const data = await api(`/api/self-dev/tasks/${task.id}/cancel`, {
          method: "POST",
          body: JSON.stringify({ reason: "user cancelled from UI" })
        });
        return data.task;
      }
    }
  }), [agentId, goal, repoUrl, reviewNote, taskChatInput]);

  function statusLabel(value) {
    return {
      created: "已创建",
      running: "运行中",
      needs_review: "待确认",
      pushed: "已 Push",
      accepted: "已接受",
      cancelled: "已终止",
      failed: "失败"
    }[value] || value || "未选择任务";
  }

  function statusColor(value) {
    return {
      created: "var(--text-muted)",
      running: "var(--primary)",
      needs_review: "var(--warning)",
      pushed: "var(--success)",
      accepted: "var(--success)",
      cancelled: "var(--text-muted)",
      failed: "var(--danger)"
    }[value] || "var(--text-muted)";
  }

  function statusIcon(value) {
    return {
      created: Circle,
      running: Loader2,
      needs_review: Eye,
      pushed: GitBranch,
      accepted: CheckCircle,
      cancelled: XCircle,
      failed: XCircle
    }[value] || Circle;
  }

  function handleApiError(err) {
    if (err.status === 401 || err.message === "Authentication required") {
      onUnauthorized();
      return true;
    }
    setError(err.message);
    return false;
  }

  async function loadSelfDev() {
    setLoading(true);
    setError("");
    try {
      const [options, taskList] = await Promise.all([
        api("/api/agents/options"),
        api("/api/self-dev/tasks")
      ]);
      setAgents(options.agents || []);
      setAgentId((current) => current || options.default_agent_id || options.agents?.[0]?.id || "");
      setTasks(taskList.tasks || []);
    } catch (err) {
      handleApiError(err);
    } finally {
      setLoading(false);
    }
  }

  async function refreshTask(taskId = selectedTask?.id) {
    if (!taskId) return;
    try {
      const data = await api(`/api/self-dev/tasks/${taskId}`);
      setSelectedTask(data.task);
      setSelectedFile(null);
      setTaskTab((current) => current || "chat");
      return data.task;
    } catch (err) {
      handleApiError(err);
    }
  }

  async function performTaskAction(actionName) {
    const action = taskActions[actionName];
    if (!action) return;
    if (action.requireTask && !selectedTask?.id) return;
    setRunning(true);
    setError("");
    try {
      const nextTask = await action.run(selectedTask);
      if (nextTask) {
        setSelectedTask(nextTask);
        await refreshTask(nextTask.id);
      }
      await loadSelfDev();
    } catch (err) {
      handleApiError(err);
    } finally {
      setRunning(false);
    }
  }

  useEffect(() => {
    loadSelfDev();
  }, []);

  useEffect(() => {
    if (selectedTask?.status === "running") {
      const interval = setInterval(() => {
        refreshTask(selectedTask.id);
      }, 3000);
      return () => clearInterval(interval);
    }
  }, [selectedTask?.status, selectedTask?.id]);

  function formatTime(isoString) {
    if (!isoString) return "";
    const date = new Date(isoString);
    const now = new Date();
    const diff = (now - date) / 1000;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
    return date.toLocaleDateString("zh-CN");
  }

  function formatDuration(start, end) {
    if (!start) return "未知";
    const startedAt = new Date(start).getTime();
    const endedAt = end ? new Date(end).getTime() : Date.now();
    if (Number.isNaN(startedAt) || Number.isNaN(endedAt)) return "未知";
    const totalSeconds = Math.max(0, Math.floor((endedAt - startedAt) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes < 1) return `${seconds} 秒`;
    return `${minutes} 分 ${seconds} 秒`;
  }

  function parseDiffToFiles(diff) {
    if (!diff) return [];
    const files = [];
    const lines = diff.split("\n");
    let currentFile = null;
    let currentContent = [];
    
    for (const line of lines) {
      if (line.startsWith("diff --git")) {
        if (currentFile) {
          currentFile.content = currentContent.join("\n");
          files.push(currentFile);
        }
        const match = line.match(/diff --git a\/(.+) b\/(.+)/);
        if (match) {
          currentFile = { path: match[2], status: "modified", additions: 0, deletions: 0, content: "" };
          currentContent = [];
        }
      } else if (line.startsWith("new file")) {
        if (currentFile) currentFile.status = "added";
      } else if (line.startsWith("deleted file")) {
        if (currentFile) currentFile.status = "deleted";
      } else if (line.startsWith("+") && !line.startsWith("+++")) {
        if (currentFile) currentFile.additions++;
      } else if (line.startsWith("-") && !line.startsWith("---")) {
        if (currentFile) currentFile.deletions++;
      }
      if (currentFile) currentContent.push(line);
    }
    if (currentFile) {
      currentFile.content = currentContent.join("\n");
      files.push(currentFile);
    }
    return files;
  }

  function parseEvents(events) {
    if (!events || !Array.isArray(events)) return [];
    return events.map(e => {
      try {
        return typeof e === "string" ? JSON.parse(e) : e;
      } catch {
        return { type: "unknown", content: String(e) };
      }
    });
  }

  const fileChanges = useMemo(() => parseDiffToFiles(selectedTask?.diff), [selectedTask?.diff]);
  const parsedEvents = useMemo(() => parseEvents(selectedTask?.events), [selectedTask?.events]);

  function handleNewTask() {
    setSelectedTask(null);
    setSelectedFile(null);
    setTaskTab("chat");
    setGoal("");
  }

  function truncate(str, len) {
    if (!str) return "";
    return str.length > len ? str.slice(0, len) + "..." : str;
  }

  return (
    <section className="page-section self-dev-section">
      <div className="self-dev-layout">
        <TaskListSidebar
          tasks={tasks}
          selectedTask={selectedTask}
          onSelectTask={refreshTask}
          onNewTask={handleNewTask}
          statusLabel={statusLabel}
          statusColor={statusColor}
          statusIcon={statusIcon}
          formatTime={formatTime}
          truncate={truncate}
          loading={loading}
        />
        
        <MainWorkbench
          selectedTask={selectedTask}
          agents={agents}
          agentId={agentId}
          setAgentId={setAgentId}
          repoUrl={repoUrl}
          setRepoUrl={setRepoUrl}
          goal={goal}
          setGoal={setGoal}
          createTask={() => performTaskAction("createAndRun")}
          running={running}
          error={error}
          reviewNote={reviewNote}
          setReviewNote={setReviewNote}
          onAccept={() => performTaskAction("accept")}
          onReject={() => performTaskAction("reject")}
          taskChatInput={taskChatInput}
          setTaskChatInput={setTaskChatInput}
          onSendChat={() => performTaskAction("chat")}
          fileChanges={fileChanges}
          selectedFile={selectedFile}
          onSelectFile={setSelectedFile}
          parsedEvents={parsedEvents}
          statusLabel={statusLabel}
          statusColor={statusColor}
          statusIcon={statusIcon}
          formatDuration={formatDuration}
          taskTab={taskTab}
          setTaskTab={setTaskTab}
          onRefresh={() => refreshTask()}
          onCancel={() => performTaskAction("cancel")}
        />
      </div>
    </section>
  );
}

function TaskListSidebar({ tasks, selectedTask, onSelectTask, onNewTask, statusLabel, statusColor, statusIcon, formatTime, truncate, loading }) {
  return (
    <aside className="self-dev-task-sidebar">
      <div className="task-sidebar-header">
        <h3>任务列表</h3>
        <span className="task-count">{loading ? "加载中" : `${tasks.length} 个任务`}</span>
      </div>
      
      <div className="self-dev-task-list">
        {tasks.length ? tasks.map((task) => {
          const StatusIcon = statusIcon(task.status);
          const isActive = selectedTask?.id === task.id;
          
          return (
            <button
              key={task.id}
              className={`task-list-item ${isActive ? "active" : ""}`}
              onClick={() => onSelectTask(task.id)}
            >
              <div className="task-item-header">
                <StatusIcon
                  size={14}
                  className={`task-status-icon ${task.status === "running" ? "spin" : ""}`}
                  style={{ color: statusColor(task.status) }}
                />
                <span className="task-status-label" style={{ color: statusColor(task.status) }}>
                  {statusLabel(task.status)}
                </span>
                <time className="task-time">{formatTime(task.updated_at)}</time>
              </div>
              <p className="task-goal">{truncate(task.goal, 72)}</p>
              <div className="task-branch">
                <GitBranch size={12} />
                <span>{task.branch}</span>
              </div>
            </button>
          );
        }) : <div className="empty-state">暂无自开发任务，右侧可直接创建</div>}
      </div>
      
      <button className="self-dev-new-task-btn" onClick={onNewTask}>
        <Plus size={16} /> 新建任务
      </button>
    </aside>
  );
}

function MainWorkbench({
  selectedTask, agents, agentId, setAgentId, repoUrl, setRepoUrl, goal, setGoal,
  createTask, running, error, reviewNote, setReviewNote, onAccept, onReject,
  taskChatInput, setTaskChatInput, onSendChat, fileChanges, selectedFile, onSelectFile,
  parsedEvents, statusLabel, statusColor, statusIcon, formatDuration, taskTab, setTaskTab, onRefresh, onCancel
}) {
  if (!selectedTask) {
    return (
      <main className="self-dev-workbench create-mode">
        <CreateTaskView
          agents={agents}
          agentId={agentId}
          setAgentId={setAgentId}
          repoUrl={repoUrl}
          setRepoUrl={setRepoUrl}
          goal={goal}
          setGoal={setGoal}
          onCreate={createTask}
          running={running}
          error={error}
        />
      </main>
    );
  }

  return (
    <main className="self-dev-workbench task-mode">
      {error ? <div className="form-error">{error}</div> : null}
      <TaskOverviewBar
        task={selectedTask}
        statusLabel={statusLabel}
        statusColor={statusColor}
        statusIcon={statusIcon}
        formatDuration={formatDuration}
        onRefresh={onRefresh}
        onCancel={onCancel}
        running={running}
      />
      {selectedTask.status === "needs_review" ? (
        <ReviewTaskView
          task={selectedTask}
          reviewNote={reviewNote}
          setReviewNote={setReviewNote}
          onAccept={onAccept}
          onReject={onReject}
          running={running}
          fileChanges={fileChanges}
        />
      ) : null}
      <TaskTabs
        task={selectedTask}
        activeTab={taskTab}
        setActiveTab={setTaskTab}
        fileChanges={fileChanges}
        selectedFile={selectedFile}
        onSelectFile={onSelectFile}
        parsedEvents={parsedEvents}
        taskChatInput={taskChatInput}
        setTaskChatInput={setTaskChatInput}
        onSendChat={onSendChat}
        running={running}
      />
    </main>
  );
}

function CreateTaskView({ agents, agentId, setAgentId, repoUrl, setRepoUrl, goal, setGoal, onCreate, running, error }) {
  return (
    <div className="create-task-view">
      <div className="create-task-header">
        <div className="create-task-icon">
          <Plus size={32} />
        </div>
        <h2>新建开发任务</h2>
        <p>描述你的开发目标，AI Agent 将自动完成代码修改</p>
      </div>
      
      <div className="create-task-form">
        <label className="form-field">
          <span>选择 Agent</span>
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>{agent.name}</option>
            ))}
          </select>
        </label>
        
        <label className="form-field">
          <span>仓库地址</span>
          <input value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} placeholder="https://github.com/..." />
        </label>
        
        <label className="form-field">
          <span>开发目标</span>
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="描述你想要实现的功能或修改，例如：优化登录页面的UI，添加用户注册表单..."
            rows={5}
          />
        </label>
        {error ? <div className="form-error">{error}</div> : null}
        
        <button
          className="secondary-button primary-action create-task-btn"
          onClick={onCreate}
          disabled={running || !goal.trim() || !agentId}
        >
          {running ? <><RefreshCw size={18} className="spin" /> 创建中...</> : <><Play size={18} /> 开始执行</>}
        </button>
      </div>
    </div>
  );
}

function TaskOverviewBar({ task, statusLabel, statusColor, statusIcon, formatDuration, onRefresh, onCancel, running }) {
  const StatusIcon = statusIcon(task.status);
  const finishedAt = ["pushed", "accepted", "failed", "needs_review", "cancelled"].includes(task.status) ? task.updated_at : null;
  const outcome = {
    pushed: "已推送到远程仓库，请前往 GitHub 合并该分支。",
    accepted: "已接受 AI 建议，可继续通过对话补充修改。",
    cancelled: "任务已终止，不会继续占用执行队列。",
    failed: task.error || "任务执行失败，请查看执行日志。"
  }[task.status];

  return (
    <section className={`task-overview-bar ${task.status}`}>
      <div className="task-overview-main">
        <StatusBadge task={task} label={statusLabel(task.status)} color={statusColor(task.status)} icon={StatusIcon} />
        <div className="task-overview-title">
          <h2>{task.goal}</h2>
          <p>
            <GitBranch size={13} />
            <span>{task.branch}</span>
          </p>
        </div>
      </div>
      <div className="task-overview-meta">
        <span>实际状态：{statusLabel(task.status)}</span>
        <span>运行时间：{formatDuration(task.created_at, finishedAt)}</span>
        {outcome ? <strong>{outcome}</strong> : null}
      </div>
      <div className="task-overview-actions">
        {task.status === "running" ? (
          <button className="secondary-button danger-button" onClick={onCancel} disabled={running}>
            <XCircle size={16} />
            终止
          </button>
        ) : null}
        <button className="secondary-button icon-btn" onClick={onRefresh} aria-label="刷新任务">
          <RefreshCw size={16} />
        </button>
      </div>
    </section>
  );
}

function StatusBadge({ task, label, color, icon: StatusIcon }) {
  return (
    <span className="task-status-badge" style={{ color }}>
      <StatusIcon size={15} className={task.status === "running" ? "spin" : ""} />
      {label}
    </span>
  );
}

function TaskTabs({
  task, activeTab, setActiveTab, fileChanges, selectedFile, onSelectFile, parsedEvents,
  taskChatInput, setTaskChatInput, onSendChat, running
}) {
  const tabs = [
    { key: "chat", label: "对话", icon: MessageSquare, count: null },
    { key: "files", label: "文件变更", icon: FileCode, count: fileChanges.length || null },
    { key: "logs", label: "执行日志", icon: TerminalIcon, count: parsedEvents.length || null }
  ];

  return (
    <section className="task-detail-tabs-shell">
      <div className="task-detail-tabs" role="tablist" aria-label="任务详情">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.key}
              role="tab"
              type="button"
              aria-selected={activeTab === tab.key}
              className={activeTab === tab.key ? "active" : ""}
              onClick={() => setActiveTab(tab.key)}
            >
              <Icon size={15} />
              {tab.label}
              {tab.count ? <span className="tab-badge">{tab.count}</span> : null}
            </button>
          );
        })}
      </div>
      <div className="task-tab-panel">
        {activeTab === "chat" ? (
          <ChatPanel
            task={task}
            input={taskChatInput}
            setInput={setTaskChatInput}
            onSend={onSendChat}
            running={running}
            parsedEvents={parsedEvents}
          />
        ) : null}
        {activeTab === "files" ? (
          <FileChangesPanel fileChanges={fileChanges} selectedFile={selectedFile} onSelectFile={onSelectFile} />
        ) : null}
        {activeTab === "logs" ? <ExecutionLogsPanel parsedEvents={parsedEvents} /> : null}
      </div>
    </section>
  );
}

function ReviewTaskView({ task, reviewNote, setReviewNote, onAccept, onReject, running, fileChanges }) {
  const recommendationConfig = {
    push: { icon: CheckCircle, color: "var(--success)", bg: "var(--success-soft)", border: "#bce5cf", title: "AI 建议：可以 Push", desc: "代码修改已完成，建议推送到远程仓库" },
    hold: { icon: PauseCircle, color: "var(--warning)", bg: "#fff8e8", border: "#f1d28b", title: "AI 建议：暂不 Push", desc: "代码有潜在问题，建议继续修改后再推送" },
    review: { icon: HelpCircle, color: "var(--primary)", bg: "var(--primary-soft)", border: "#cfe0ff", title: "AI 建议：需要人工判断", desc: "请仔细审查代码变更后决定" }
  };
  
  const config = recommendationConfig[task.recommendation] || recommendationConfig.review;
  const RecIcon = config.icon;
  
  return (
    <section className="review-task-view">
      <div className="review-header">
        <Eye size={22} style={{ color: "var(--warning)" }} />
        <div>
          <h3>审查 AI 建议</h3>
          <p>请查看代码变更，决定是否接受 AI 的建议。</p>
        </div>
      </div>
      
      <div className="recommendation-card" style={{ background: config.bg, borderColor: config.border }}>
        <RecIcon size={30} style={{ color: config.color }} />
        <div className="recommendation-content">
          <h3 style={{ color: config.color }}>{config.title}</h3>
          <p>{task.result || config.desc}</p>
        </div>
      </div>
      
      {fileChanges.length > 0 && (
        <div className="review-file-summary">
          <h4>Diff 摘要</h4>
          <div className="file-summary-list">
            {fileChanges.slice(0, 5).map((file, idx) => (
              <div key={idx} className={`file-summary-item ${file.status}`}>
                <FileCode size={14} />
                <span className="file-path">{file.path}</span>
                <span className="file-stats">
                  {file.additions > 0 && <span className="add">+{file.additions}</span>}
                  {file.deletions > 0 && <span className="del">-{file.deletions}</span>}
                </span>
              </div>
            ))}
            {fileChanges.length > 5 && (
              <div className="file-summary-more">还有 {fileChanges.length - 5} 个文件...</div>
            )}
          </div>
        </div>
      )}
      
      <label className="review-note-field">
        <span>补充说明（可选）</span>
        <textarea
          value={reviewNote}
          onChange={(e) => setReviewNote(e.target.value)}
          placeholder="接受或拒绝时都可以留下说明，帮助 AI 理解你的决策..."
          rows={3}
        />
      </label>
      
      <div className="review-actions">
        <button className="secondary-button reject-btn" onClick={onReject} disabled={running}>
          <X size={18} />
          拒绝并继续修改
        </button>
        <button className="secondary-button primary-action accept-btn" onClick={onAccept} disabled={running}>
          <Check size={18} />
          接受 AI 建议
        </button>
      </div>
    </section>
  );
}

function ChatPanel({ task, input, setInput, onSend, running, parsedEvents }) {
  const messagesEndRef = useRef(null);
  
  const messages = useMemo(() => {
    const msgs = [];
    parsedEvents.forEach(event => {
      if (event.goal) {
        msgs.push({ role: "user", content: event.goal, timestamp: event.timestamp });
      }
      if (event.result && event.type === "result") {
        msgs.push({ role: "assistant", content: event.result, timestamp: event.timestamp });
      }
    });
    return msgs;
  }, [parsedEvents]);
  
  useEffect(() => {
    if (typeof messagesEndRef.current?.scrollIntoView === "function") {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);
  
  const canChat = task.status !== "running" && task.status !== "pushed";
  
  return (
    <div className="chat-panel">
      <div className="chat-panel-header">
        <MessageSquare size={16} />
        <span>任务对话</span>
        <small>{messages.length} 条消息</small>
      </div>
      
      <div className="chat-messages">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <Bot size={32} />
            <p>暂无对话记录</p>
          </div>
        ) : messages.map((msg, idx) => (
          <div key={idx} className={`chat-message ${msg.role}`}>
            <div className="message-avatar">
              {msg.role === "user" ? <User size={14} /> : <Bot size={14} />}
            </div>
            <div className="message-content">
              <div className="message-text">{msg.content}</div>
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>
      
      {canChat && (
        <div className="chat-input-row">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="继续对话，补充要求或追问..."
            rows={2}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
          />
          <button
            className="secondary-button primary-action"
            onClick={onSend}
            disabled={running || !input.trim()}
          >
            <Send size={16} />
          </button>
        </div>
      )}
    </div>
  );
}

function FileChangesPanel({ fileChanges, selectedFile, onSelectFile }) {
  const selectedFileContent = selectedFile ? fileChanges.find(f => f.path === selectedFile)?.content : "";

  if (fileChanges.length === 0) {
    return <div className="panel-empty">暂无文件变更</div>;
  }

  return (
    <div className="files-panel">
      <div className="file-change-tree">
        {fileChanges.map((file, idx) => (
          <button
            type="button"
            key={idx}
            className={`file-change-item ${file.status} ${selectedFile === file.path ? "selected" : ""}`}
            onClick={() => onSelectFile(file.path)}
          >
            {file.status === "added" && <FilePlus size={14} className="file-icon added" />}
            {file.status === "deleted" && <FileMinus size={14} className="file-icon deleted" />}
            {file.status === "modified" && <FileCode size={14} className="file-icon modified" />}
            <span className="file-path">{file.path}</span>
            <span className="file-stats">
              {file.additions > 0 && <span className="stat-add">+{file.additions}</span>}
              {file.deletions > 0 && <span className="stat-del">-{file.deletions}</span>}
            </span>
          </button>
        ))}
      </div>
      <div className="diff-viewer">
        {selectedFileContent ? (
          <pre>{selectedFileContent}</pre>
        ) : (
          <div className="diff-placeholder">选择文件查看变更详情</div>
        )}
      </div>
    </div>
  );
}

function ExecutionLogsPanel({ parsedEvents }) {
  if (parsedEvents.length === 0) {
    return <div className="panel-empty">暂无执行日志</div>;
  }

  return (
    <div className="logs-panel">
      <div className="execution-logs">
        {parsedEvents.map((event, idx) => (
          <div key={idx} className={`log-entry ${event.type} ${event.level || ''}`}>
            {event.type === 'log' ? (
              <div className="log-line">
                <span className={`log-level ${event.level || 'info'}`}></span>
                <span className="log-message">{event.message}</span>
              </div>
            ) : (
              <>
                <div className="log-header">
                  <span className="log-type">{event.type}</span>
                  {event.timestamp && <time>{new Date(event.timestamp).toLocaleTimeString()}</time>}
                </div>
                <div className="log-content">
                  {event.goal && <p className="log-goal">{event.goal}</p>}
                  {event.status && <p className="log-status">状态: {event.status}</p>}
                  {event.result && <p className="log-result">{event.result}</p>}
                  {event.error && <p className="log-error">{event.error}</p>}
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
function TerminalPage({ onUnauthorized }) {
  const [status, setStatus] = useState("disconnected");
  const [error, setError] = useState("");
  const [mobileInput, setMobileInput] = useState("");
  const [mobileSecret, setMobileSecret] = useState(true);
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

  function sendMobileInput(event) {
    event.preventDefault();
    if (!mobileInput) {
      sendTerminalMessage({ type: "input", data: "\r" });
      return;
    }
    sendTerminalMessage({ type: "input", data: mobileInput });
    sendTerminalMessage({ type: "input", data: "\r" });
    setMobileInput("");
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

    connect();
    return () => {
      resizeObserverRef.current?.disconnect();
      socketRef.current?.close();
      terminal.dispose();
    };
  }, []);

  return (
    <section className="page-section terminal-section">
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
        <form className="terminal-mobile-input" onSubmit={sendMobileInput}>
          <label htmlFor="terminal-mobile-input">移动端输入（密码场景可用）</label>
          <div className="terminal-mobile-input-row">
            <input
              id="terminal-mobile-input"
              type={mobileSecret ? "password" : "text"}
              value={mobileInput}
              onChange={(event) => setMobileInput(event.target.value)}
              placeholder="输入后发送到终端"
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              disabled={status !== "connected"}
            />
            <button
              type="button"
              className="secondary-button"
              onClick={() => setMobileSecret((value) => !value)}
              disabled={status !== "connected"}
            >
              {mobileSecret ? "显示" : "隐藏"}
            </button>
            <button className="secondary-button primary-action" type="submit" disabled={status !== "connected"}>
              发送
            </button>
          </div>
        </form>
        {error ? <div className="form-error">{error}</div> : null}
      </div>
    </section>
  );
}

function ChannelsPage({ onUnauthorized }) {
  const [wechat, setWechat] = useState(null);
  const [channelLoading, setChannelLoading] = useState(false);
  const [channelError, setChannelError] = useState("");

  async function loadWechatStatus() {
    try {
      const data = await api("/api/channels/wechat/status");
      setWechat(data.wechat);
    } catch (err) {
      if (err.status === 401 || err.message === "Authentication required") {
        onUnauthorized();
        return;
      }
      setChannelError(err.message);
    }
  }

  async function updateWechat(action) {
    setChannelLoading(true);
    setChannelError("");
    try {
      const data = await api(`/api/channels/wechat/${action}`, { method: "POST" });
      setWechat(data.wechat);
    } catch (err) {
      if (err.status === 401 || err.message === "Authentication required") {
        onUnauthorized();
        return;
      }
      setChannelError(err.message);
    } finally {
      setChannelLoading(false);
    }
  }

  useEffect(() => {
    loadWechatStatus();
    const timer = setInterval(loadWechatStatus, 2500);
    return () => clearInterval(timer);
  }, []);

  const wechatQrSource = wechat?.qrcode_data_url || wechat?.qrcode_url || "";
  const wechatError = channelError || wechat?.error || "";
  const wechatErrorText = wechatError.includes("400")
    ? "扫码通道返回了内部状态提示。二维码仍可扫描；如果扫码失败，请停止后重新启动微信。"
    : wechatError;

  return (
    <section className="page-section channels-section">
      <div className="channels-hero">
        <span>Channels</span>
        <h2>消息渠道</h2>
        <p>把外部消息入口接入 Agent runtime。个人微信可以做扫码登录机器人，适合你这种单人自用场景；当前版本已接入 Wechaty sidecar、二维码登录状态和默认 Agent 回复通道。</p>
      </div>
      <div className="channel-grid">
        <article className="channel-card primary-channel">
          <div className="channel-card-heading">
            <span className="channel-icon"><MessageSquare size={18} /></span>
            <div>
              <h3>个人微信机器人</h3>
              <p>扫码登录 + 白名单 + 默认 Agent 回复</p>
            </div>
            <strong>{wechat?.login_state || "stopped"}</strong>
          </div>
          <div className="wechat-control">
            <div>
              <span className={`terminal-status ${wechat?.running ? "connected" : ""}`}>
                {wechat?.running ? "运行中" : "未启动"}
              </span>
              {wechat?.user ? <strong>{wechat.user}</strong> : null}
            </div>
            <div className="wechat-actions">
              <button className="secondary-button primary-action" onClick={() => updateWechat("start")} disabled={channelLoading || wechat?.running}>
                <Play size={16} />
                启动微信
              </button>
              <button className="secondary-button" onClick={() => updateWechat("stop")} disabled={channelLoading || !wechat?.running}>
                <XCircle size={16} />
                停止
              </button>
            </div>
          </div>
          {wechatQrSource ? (
            <div className="wechat-qr-panel">
              <img src={wechatQrSource} alt="微信登录二维码" />
              <div>
                <strong>用微信扫码登录</strong>
                <p>状态：{wechat.qrcode_status || "等待扫码"}</p>
              </div>
            </div>
          ) : null}
          {wechatErrorText && !wechatQrSource ? <div className="form-error">{wechatErrorText}</div> : null}
          {wechatErrorText && wechatQrSource ? <div className="wechat-notice">{wechatErrorText}</div> : null}
          <div className="channel-steps">
            <div>
              <span>1</span>
              <p>启动 Wechaty sidecar，浏览器展示登录二维码。</p>
            </div>
            <div>
              <span>2</span>
              <p>扫码登录后保活会话，并限制只响应你的联系人或指定群。</p>
            </div>
            <div>
              <span>3</span>
              <p>消息进入后转发到默认 Agent，回复再发送回微信。</p>
            </div>
          </div>
        </article>
        <article className="channel-card">
          <h3>建议配置形态</h3>
          <pre>{`channels:
  wechat_personal:
    enabled: true
    provider: "wechaty"
    login: "qr"
    proxy: ""
    default_agent_id: "assistant"
    allow_contacts:
      - "你的微信昵称或备注"
    allow_rooms: []`}</pre>
        </article>
      </div>
    </section>
  );
}

function AppShell({ onLogout }) {
  const [path, setPath] = useState(window.location.pathname);
  const navItems = useMemo(
    () => [
      { path: "/", label: "首页", icon: Home },
      { path: "/agents", label: "Agent", icon: Bot },
      { path: "/self-dev", label: "自开发", icon: Code2 },
      { path: "/channels", label: "渠道", icon: MessageSquare },
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

  useEffect(() => {
    if (!window.matchMedia?.("(max-width: 430px)").matches) {
      return;
    }
    document.querySelector(".sidebar nav button.active")?.scrollIntoView({
      block: "nearest",
      inline: "center"
    });
  }, [path]);

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
    if (path === "/self-dev") {
      return <SelfDevPage onUnauthorized={unauthorized} />;
    }
    if (path === "/terminal") {
      return <TerminalPage onUnauthorized={unauthorized} />;
    }
    if (path === "/channels") {
      return <ChannelsPage onUnauthorized={unauthorized} />;
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
                type="button"
                className={path === item.path ? "active" : ""}
                aria-current={path === item.path ? "page" : undefined}
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
      <main className={`content${path === "/agents" ? " content-agents" : ""}`}>{renderPage()}</main>
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
