import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowRight,
  BrainCircuit,
  Bot,
  Check,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Circle,
  Cpu,
  FileCode,
  FileEdit,
  FileText,
  Image as ImageIcon,
  List,
  Loader2,
  LogOut,
  MessageSquare,
  Play,
  PlugZap,
  Plus,
  RefreshCw,
  Save,
  Search,
  ScrollText,
  Send,
  Settings,
  ShieldCheck,
  Trash2,
  TrendingUp,
  User,
  X,
  XCircle
} from "lucide-react";
import { CritiquePage } from "./CritiquePage.jsx";
import "./styles.css";

const AGENT_NAV_ITEMS = [
  { path: "/agents", label: "对话", icon: MessageSquare, tab: "chat" },
  { path: "/agents/manage", label: "Agent 管理", icon: List, tab: "agents" },
  { path: "/agents/skills", label: "Skill 管理", icon: FileText, tab: "skills" },
  { path: "/agents/models", label: "模型配置", icon: Cpu, tab: "models" }
];

function agentTabForPath(path) {
  if (path === "/models") {
    return "models";
  }
  return AGENT_NAV_ITEMS.find((item) => item.path === path)?.tab || "chat";
}

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
      window.history.replaceState({}, "", "/agents");
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
        if (/^###\s+/.test(block)) {
          return <h4 key={`block-${blockIndex}`}>{renderInlineMarkdown(block.replace(/^###\s+/, ""), `${blockIndex}`)}</h4>;
        }
        if (/^##\s+/.test(block)) {
          return <h3 key={`block-${blockIndex}`}>{renderInlineMarkdown(block.replace(/^##\s+/, ""), `${blockIndex}`)}</h3>;
        }
        if (/^#\s+/.test(block)) {
          return <h2 key={`block-${blockIndex}`}>{renderInlineMarkdown(block.replace(/^#\s+/, ""), `${blockIndex}`)}</h2>;
        }
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

function AgentPage({ onUnauthorized, initialTab = "chat" }) {
  const [activeTab, setActiveTab] = useState(initialTab);
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
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [activeSessionIds, setActiveSessionIds] = useState({});
  const [sessionLoading, setSessionLoading] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);
  const [expandedSkillIndex, setExpandedSkillIndex] = useState(null);
  const [skillContents, setSkillContents] = useState({});
  const [skillContentLoading, setSkillContentLoading] = useState(null);
  const [skillToolQuery, setSkillToolQuery] = useState("");
  const [skillToolFilter, setSkillToolFilter] = useState("all");
  const [toolDefinitions, setToolDefinitions] = useState([]);
  const [toolDefinitionsError, setToolDefinitionsError] = useState("");
  const [expandedModelIndex, setExpandedModelIndex] = useState(null);
  const socketRef = useRef(null);
  const messageListRef = useRef(null);
  const fileInputRef = useRef(null);
  const composingRef = useRef(false);
  const sessionRequestRef = useRef(0);

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

  async function loadSessions(selectedAgentId) {
    if (!selectedAgentId) return;
    const requestId = ++sessionRequestRef.current;
    setSessionLoading(true);
    try {
      const data = await api(`/api/sessions?agent_id=${encodeURIComponent(selectedAgentId)}`);
      if (requestId !== sessionRequestRef.current) return;
      const nextSessions = data.sessions || [];
      setSessions(nextSessions);
      const rememberedId = activeSessionIds[selectedAgentId];
      if (rememberedId && nextSessions.some((session) => session.id === rememberedId)) {
        await switchSession(rememberedId, selectedAgentId);
      } else {
        setActiveSessionId(null);
        setMessages([]);
      }
    } catch (err) {
      if (err.status !== 404) {
        handleApiError(err);
      }
    } finally {
      if (requestId === sessionRequestRef.current) setSessionLoading(false);
    }
  }

  async function loadToolDefinitions() {
    setToolDefinitionsError("");
    try {
      const data = await api("/api/agents/tools");
      setToolDefinitions(data.tools || []);
    } catch (err) {
      setToolDefinitionsError(err.message);
    }
  }

  async function createSessionRequest(selectedAgentId) {
    setSessionLoading(true);
    try {
      const data = await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ agent_id: selectedAgentId || agentId })
      });
      const session = data.session;
      setSessions((prev) => [session, ...prev]);
      setActiveSessionId(session.id);
      setActiveSessionIds((current) => ({ ...current, [session.agent_id]: session.id }));
      setMessages([]);
      return session;
    } catch (err) {
      handleApiError(err);
      return null;
    } finally {
      setSessionLoading(false);
    }
  }

  async function deleteSessionItem(sessionId) {
    try {
      await api(`/api/sessions/${sessionId}?agent_id=${encodeURIComponent(agentId)}`, { method: "DELETE" });
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setActiveSessionIds((current) => ({ ...current, [agentId]: null }));
        setMessages([]);
      }
    } catch (err) {
      handleApiError(err);
    }
  }

  async function switchSession(sessionId, selectedAgentId = agentId) {
    setSessionLoading(true);
    setError("");
    try {
      const data = await api(`/api/sessions/${sessionId}?agent_id=${encodeURIComponent(selectedAgentId)}`);
      const session = data.session;
      if (session.agent_id !== selectedAgentId) throw new Error("会话不属于当前 Agent");
      setActiveSessionId(session.id);
      setActiveSessionIds((current) => ({ ...current, [selectedAgentId]: session.id }));
      const historyMessages = (session.messages || []).map((msg) => ({
        role: msg.role,
        content: msg.content,
        images: (msg.images || []).map((img, i) => ({
          id: `history-${session.id}-${i}`,
          name: "",
          mime_type: img.mime_type,
          data: img.data,
          preview: `data:${img.mime_type};base64,${img.data}`
        })),
        checkpoints: msg.checkpoints || [],
        checkpointsCollapsed: true
      }));
      setMessages(historyMessages);
    } catch (err) {
      handleApiError(err);
    } finally {
      setSessionLoading(false);
    }
  }

  async function loadOptions() {
    setLoading(true);
    setError("");
    try {
      const data = await api("/api/agents/options");
      setOptions(data);
      setAgentId(data.agents?.[0]?.id || "");
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
      setConfig({
        ...data,
        skills: data.skills || []
      });
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
        setMessages((items) => {
          const kept = [];
          let checkpoints = [];
          for (const item of items) {
            if (item.role === "pending") {
              checkpoints = item.checkpoints || [];
            } else {
              kept.push(item);
            }
          }
          kept.push({
            role: "assistant",
            content: message.content || "",
            checkpoints,
            checkpointsCollapsed: true
          });
          return kept;
        });
        setSending(false);
        setStatus("connected");
        loadSessions(agentId);
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

  async function sendMessage() {
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

    let currentSessionId = activeSessionId;
    if (!currentSessionId) {
      const session = await createSessionRequest(agentId);
      currentSessionId = session ? session.id : null;
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
        session_id: currentSessionId,
        images: attachments.map((image) => ({
          mime_type: image.mime_type,
          data: image.data
        }))
      })
    );
  }

  function toggleCheckpoints(messageIndex) {
    setMessages((items) =>
      items.map((item, i) =>
        i === messageIndex
          ? { ...item, checkpointsCollapsed: !item.checkpointsCollapsed }
          : item
      )
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
    loadToolDefinitions();
    connect();
    return () => socketRef.current?.close();
  }, []);

  useEffect(() => {
    if (!agentId) return;
    setMessages([]);
    setSessions([]);
    setActiveSessionId(activeSessionIds[agentId] || null);
    loadSessions(agentId);
  }, [agentId]);

  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);

  useEffect(() => {
    if (activeTab !== "skills" || !config?.skills?.length) {
      return;
    }
    const nextIndex = Math.min(expandedSkillIndex ?? 0, config.skills.length - 1);
    if (expandedSkillIndex !== nextIndex) {
      setExpandedSkillIndex(nextIndex);
    }
    loadSkillContent(config.skills[nextIndex]);
  }, [activeTab, config, expandedSkillIndex]);

  useEffect(() => {
    if (messageListRef.current) {
      messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
    }
  }, [messages]);

  const selectedAgent = options?.agents?.find((agent) => agent.id === agentId);
  const selectedAgentConfig = config?.agents?.find((agent) => agent.id === agentId);
  const selectedAgentSkills = selectedAgentConfig?.skill_ids || [];
  const canChat = Boolean(options?.agents?.length);
  const blocked = !loading && (!canChat || !selectedAgent?.model || !selectedAgent?.model?.has_api_key);
  const quickPrompts = [
    "总结一下今天需要处理的事项",
    "帮我检查系统设置有没有风险",
    "根据截图分析这个 UI 怎么改",
    "生成一个开发任务说明"
  ];
  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const connectionLabel = status === "running" ? "生成中" : status === "connected" ? "已连接" : "未连接";
  const modelCapabilityLabel = selectedAgent?.model?.supports_images ? "文本 + 图片" : "文本";
  const providerLabel = (provider) => provider === "anthropic" ? "Anthropic" : "OpenAI 兼容";
  const skillItems = config?.skills || [];
  const selectedSkillIndex = skillItems.length ? Math.min(expandedSkillIndex ?? 0, skillItems.length - 1) : -1;
  const selectedSkill = selectedSkillIndex >= 0 ? skillItems[selectedSkillIndex] : null;
  const selectedConfigAgentIndex = config?.agents?.length ? Math.min(expandedRow ?? 0, config.agents.length - 1) : -1;
  const selectedConfigAgent = selectedConfigAgentIndex >= 0 ? config.agents[selectedConfigAgentIndex] : null;
  const selectedConfigAgentModel = selectedConfigAgent
    ? config.models.find((model) => model.id === selectedConfigAgent.model_id)
    : null;
  const selectedSkillAllow = selectedSkill?.tools?.allow || [];
  const selectedSkillMarkdown = selectedSkill
    ? skillContentLoading === selectedSkill.id && skillContents[selectedSkill.id] === undefined
      ? "加载中..."
      : skillContents[selectedSkill.id] ?? ""
    : "";
  const normalizedToolQuery = skillToolQuery.trim().toLowerCase();
  const filteredSkillTools = toolDefinitions.filter((tool) => {
      const matchesQuery = !normalizedToolQuery || [tool.display_name, tool.name, tool.description].some((value) =>
        String(value).toLowerCase().includes(normalizedToolQuery)
      );
      const matchesFilter = skillToolFilter === "all" || (tool.support_scene || []).includes(skillToolFilter);
      return matchesQuery && matchesFilter;
    });
  const missingSkillTools = selectedSkillAllow.filter(
    (name) => !toolDefinitions.some((tool) => tool.name === name)
  );

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

  function updateAgent(index, field, value) {
    setConfig((current) => {
      const previousId = current.agents[index]?.id;
      return {
        ...current,
        portfolio_agent_id:
          field === "id" && current.portfolio_agent_id === previousId
            ? value
            : current.portfolio_agent_id,
        agents: current.agents.map((agent, agentIndex) =>
          agentIndex === index ? { ...agent, [field]: value } : agent
        )
      };
    });
  }

  function updateSkill(index, field, value) {
    setConfig((current) => ({
      ...current,
      skills: (current.skills || []).map((skill, skillIndex) =>
        skillIndex === index ? { ...skill, [field]: value } : skill
      )
    }));
  }

  function skillAgentId(skillId) {
    if (!String(skillId || "").startsWith("private:")) {
      return null;
    }
    return (config?.agents || []).find((agent) => (agent.skill_ids || []).includes(skillId))?.id || "";
  }

  async function loadSkillContent(skill) {
    if (!skill?.id || Object.prototype.hasOwnProperty.call(skillContents, skill.id)) {
      return;
    }
    setSkillContentLoading(skill.id);
    try {
      const params = new URLSearchParams({ id: skill.id });
      const agentForSkill = skillAgentId(skill.id);
      if (agentForSkill) {
        params.set("agent_id", agentForSkill);
      }
      const data = await api(`/api/agents/skills/content?${params.toString()}`);
      setSkillContents((current) => ({ ...current, [skill.id]: data.content || "" }));
      setConfig((current) => ({
        ...current,
        skills: (current.skills || []).map((item) =>
          item.id === skill.id
            ? {
                ...item,
                name: item.id,
                tools: data.tools || item.tools || { allow: [] }
              }
            : item
        )
      }));
    } catch (err) {
      handleApiError(err);
      setConfigError(err.message);
      setSkillContents((current) => ({ ...current, [skill.id]: "" }));
    } finally {
      setSkillContentLoading(null);
    }
  }

  function toggleSkillExpanded(index) {
    const nextIndex = expandedSkillIndex === index ? null : index;
    setExpandedSkillIndex(nextIndex);
    if (nextIndex !== null) {
      loadSkillContent((config?.skills || [])[nextIndex]);
    }
  }

  function selectSkill(index) {
    setExpandedSkillIndex(index);
    loadSkillContent((config?.skills || [])[index]);
  }

  function updateSkillContent(skillId, content) {
    setSkillContents((current) => ({ ...current, [skillId]: content }));
  }

  function toggleAgentSkill(index, skillId) {
    const current = config.agents[index].skill_ids || [];
    updateAgent(index, "skill_ids", current.includes(skillId)
      ? current.filter((id) => id !== skillId)
      : [...current, skillId]);
  }

  function toggleSkillTool(index, toolId) {
    setConfig((current) => ({
      ...current,
      skills: (current.skills || []).map((skill, skillIndex) => {
        if (skillIndex !== index) {
          return skill;
        }
        const tools = skill.tools || { allow: [] };
        const allow = new Set(tools.allow || []);
        if (allow.has(toolId)) {
          allow.delete(toolId);
        } else {
          allow.add(toolId);
        }
        return {
          ...skill,
          tools: {
            allow: Array.from(allow)
          }
        };
      })
    }));
  }

  function insertSkillEvolutionTemplate() {
    if (!selectedSkill) return;
    const currentContent = selectedSkillMarkdown.trim();
    const template = `# ${selectedSkill.id}

## 自进化目标

- 记录这个 Skill 要持续改进的工作边界、质量标准和禁区。
- 每次使用后，把可复用经验沉淀到本文件，不把临时任务细节写进长期规则。

## 使用流程

1. 先读取当前任务和已绑定工具能力。
2. 执行任务时保留可验证证据。
3. 任务结束后，只在发现稳定模式、常见错误或新约束时更新本 Skill。
4. 更新前确认不会覆盖用户偏好、项目约定或安全边界。

## 可改进内容

- 更清晰的判断标准。
- 更可靠的工具选择规则。
- 常见失败案例与规避方式。
- 与项目架构、Agent 分工有关的长期约定。
`;
    updateSkillContent(
      selectedSkill.id,
      currentContent ? `${currentContent}\n\n${template}` : template
    );
  }

  function addSkill() {
    const id = `common:skill-${(config?.skills?.length || 0) + 1}`;
    const nextIndex = config?.skills?.length || 0;
    setConfig((current) => ({
      ...current,
      skills: [
        ...(current.skills || []),
        {
          id,
          name: id,
          tools: { allow: [] }
        }
      ]
    }));
    setExpandedSkillIndex(nextIndex);
  }

  function addAgent() {
    const id = `agent-${(config?.agents?.length || 0) + 1}`;
    const nextIndex = config?.agents?.length || 0;
    setConfig((current) => ({
      ...current,
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
    setExpandedRow(nextIndex);
  }

  function removeAgent(index) {
    setConfig((current) => {
      const removedId = current.agents[index]?.id;
      return {
        ...current,
        portfolio_agent_id: current.portfolio_agent_id === removedId ? "" : current.portfolio_agent_id,
        agents: current.agents.filter((_, agentIndex) => agentIndex !== index)
      };
    });
  }

  function updateModel(index, field, value) {
    setConfig((current) => ({
      ...current,
      models: current.models.map((model, modelIndex) =>
        modelIndex === index ? { ...model, [field]: value } : model
      )
    }));
  }

  function addModel() {
    const id = `model-${(config?.models?.length || 0) + 1}`;
    const nextIndex = config?.models?.length || 0;
    setConfig((current) => ({
      ...current,
      default_model_id: current.default_model_id || id,
      models: [
        ...current.models,
        {
          id,
          name: "新模型",
          provider: "openai_compatible",
          base_url: "https://api.openai.com/v1",
          model: "",
          api_key: "",
          mode: "prompt",
          temperature: 0.7,
          supports_images: false,
          has_api_key: false,
          api_key_mask: ""
        }
      ]
    }));
    setExpandedModelIndex(nextIndex);
  }

  async function saveAgentConfig(successMessage = "Agent 管理配置已保存") {
    setConfigSaving(true);
    setConfigError("");
    setConfigStatus("");
    try {
      await Promise.all(
        Object.entries(skillContents).map(([skillId, content]) =>
          api("/api/agents/skills/content", {
            method: "PUT",
            body: JSON.stringify({
              id: skillId,
              content,
              name: skillId,
              tools: {
                allow: ((config.skills || []).find((skill) => skill.id === skillId)?.tools?.allow || [])
              },
              agent_id: skillAgentId(skillId) || null
            })
          })
        )
      );
      await api("/api/agents/config", {
        method: "PUT",
        body: JSON.stringify({
          default_model_id: config.default_model_id || config.models?.[0]?.id || "",
          portfolio_agent_id: config.portfolio_agent_id || "",
          skills: (config.skills || []).map((skill) => ({
            id: skill.id,
            name: skill.id
          })),
          models: config.models.map((model) => ({
            id: model.id,
            name: model.name,
            provider: model.provider || "openai_compatible",
            base_url: model.base_url,
            model: model.model,
            api_key: model.api_key || "",
            mode: model.mode || "prompt",
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
      setConfigStatus(successMessage);
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
    <section className={`page-section agent-section${activeTab === "agents" || activeTab === "skills" || activeTab === "models" ? " agent-section-config" : ""}`}>
      <div className="agent-command-stage">
      {activeTab === "chat" ? (
        <div className="agent-chat-shell ai-chat-workspace">
          <aside className="ai-chat-sidebar">
            <div className="ai-agent-card">
              <span className="ai-agent-orb">
                <Bot size={18} />
              </span>
              <div className="ai-agent-card-copy">
                <strong>{selectedAgent?.name || "Agent"}</strong>
                <small>{selectedAgent?.model?.name || "未选择模型"}</small>
              </div>
              <span className={`ai-agent-dot ${status === "connected" || status === "running" ? "online" : ""}`} />
            </div>
            <label className="ai-agent-select">
              <span>当前 Agent</span>
              <select
                aria-label="Agent"
                value={agentId}
                onChange={(event) => setAgentId(event.target.value)}
                disabled={loading || !canChat || sending}
              >
                {(options?.agents || []).map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="session-new-button"
              onClick={() => createSessionRequest(agentId)}
              disabled={sessionLoading || loading || !canChat}
            >
              <Plus size={16} />
              新对话
            </button>
            <div className="ai-session-list">
              <div className="ai-session-heading">
                <span>对话历史</span>
                <small>{sessions.length} 个</small>
              </div>
              {sessionLoading ? (
                <div className="session-empty">加载中...</div>
              ) : sessions.length === 0 ? (
                <div className="session-empty">暂无对话记录</div>
              ) : (
                sessions.map((session) => (
                  <div
                    key={session.id}
                    className={`session-item${activeSessionId === session.id ? " active" : ""}`}
                  >
                    <button className="session-item-open" onClick={() => switchSession(session.id)} disabled={sending}>
                      <span className="session-item-title"><MessageSquare size={14} />{session.title || "新对话"}</span>
                      <span className="session-item-meta"><span>{session.message_count} 条消息</span></span>
                    </button>
                    <button className="session-delete-btn" onClick={() => deleteSessionItem(session.id)} title="删除对话"><X size={12} /></button>
                  </div>
                ))
              )}
            </div>
            <div className="ai-agent-health">
              <span className={`runtime-status ${status === "connected" || status === "running" ? "connected" : ""}`}>
                {connectionLabel}
              </span>
              <button className="secondary-button" onClick={connect} disabled={status === "connected" || loading}>
                <PlugZap size={15} />
                重连
              </button>
            </div>
          </aside>
          <main className="ai-chat-main">
            <div className="ai-chat-topline">
              <div className="ai-chat-titleblock">
                <strong>{selectedAgent?.name || "Agent Chat"}</strong>
                <span>
                  {selectedAgent?.model?.model || "model"}
                  {selectedAgent?.model?.supports_images ? " · vision" : ""}
                </span>
              </div>
              <div className="ai-chat-badges">
                <span className={`runtime-status ${status === "connected" || status === "running" ? "connected" : ""}`}>
                  {connectionLabel}
                </span>
                {selectedAgent?.model?.has_api_key ? <span>key ready</span> : <span>key missing</span>}
                {selectedAgent?.model?.supports_images ? <span>image input</span> : null}
              </div>
            </div>
            <div className="ai-context-strip" aria-label="当前对话上下文">
              <span>
                <Cpu size={14} />
                {selectedAgent?.model?.name || "未选择模型"}
              </span>
              <span>
                <MessageSquare size={14} />
                {activeSession?.title || (activeSessionId ? "当前会话" : "临时会话")}
              </span>
              <span>
                <ImageIcon size={14} />
                {modelCapabilityLabel}
              </span>
            </div>
            <div className="agent-message-list" ref={messageListRef}>
              {loading ? <div className="empty-state">正在加载 Agent 配置</div> : null}
              {!loading && error && !options ? <div className="error-state">{error}</div> : null}
              {!loading && !options?.agents?.length ? <div className="empty-state">请先在 Agent 管理中添加 Agent</div> : null}
              {!loading && selectedAgent && !selectedAgent.model ? <div className="empty-state">Agent 未配置模型</div> : null}
              {!loading && selectedAgent?.model && !selectedAgent.model.has_api_key ? (
                <div className="empty-state">模型 API Key 不可用</div>
              ) : null}
              {!loading && !blocked && messages.length === 0 && sessions.length === 0 ? (
                <div className="agent-welcome">
                  <span className="agent-welcome-mark"><Bot size={20} /></span>
                  <h2>开始新的对话</h2>
                  <p>点击左侧"新对话"开始，或直接输入你的任务。</p>
                  <div className="ai-empty-prompts">
                    {quickPrompts.map((prompt) => (
                      <button key={prompt} type="button" onClick={() => setInput(prompt)}>
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
              {!loading && !blocked && messages.length === 0 && sessions.length > 0 ? (
                <div className="agent-welcome">
                  <h2>选择对话或创建新对话</h2>
                  <p>从左侧对话历史中选择，或点击"新对话"开始。</p>
                </div>
              ) : null}
              {messages.map((message, index) => (
                <div key={`${message.role}-${index}`} className={`agent-message ${message.role}`}>
                  <span>{message.role === "user" ? "你" : message.role === "assistant" ? selectedAgent?.name || "Agent" : ""}</span>
                  {message.content && message.role === "assistant" ? <MarkdownMessage content={message.content} /> : null}
                  {message.content && message.role !== "assistant" ? <p>{message.content}</p> : null}
                  {message.checkpoints?.length ? (
                    <div className="agent-checkpoints-wrapper">
                      <button
                        type="button"
                        className="checkpoint-toggle"
                        onClick={() => toggleCheckpoints(index)}
                      >
                        <span className="checkpoint-toggle-icon">
                          {message.checkpointsCollapsed ? "▶" : "▼"}
                        </span>
                        {message.checkpointsCollapsed ? "展开" : "折叠"}思维链
                        <span className="checkpoint-toggle-count">
                          {message.checkpoints.length} 步
                        </span>
                      </button>
                      {!message.checkpointsCollapsed ? (
                        <ol className="agent-checkpoints">
                          {message.checkpoints.map((checkpoint, checkpointIndex) => (
                            <li key={`${checkpoint.stage}-${checkpointIndex}`}>
                              <strong>{checkpoint.title}</strong>
                              {checkpoint.detail ? <small>{checkpoint.detail}</small> : null}
                            </li>
                          ))}
                        </ol>
                      ) : null}
                    </div>
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
          <aside className="agent-runtime-inspector" aria-label="Agent 运行信息">
            <div className="runtime-inspector-heading">
              <span>运行信息</span>
              <span className={`ai-agent-dot ${status === "connected" || status === "running" ? "online" : ""}`} />
            </div>
            <div className="runtime-agent-identity">
              <span className="ai-agent-orb"><Bot size={18} /></span>
              <div>
                <strong>{selectedAgent?.name || "未选择 Agent"}</strong>
                <small>{connectionLabel}</small>
              </div>
            </div>
            <section className="runtime-inspector-section">
              <span>绑定模型</span>
              <strong>{selectedAgent?.model?.name || "未配置"}</strong>
              <small>{selectedAgent?.model?.model || "等待模型配置"}</small>
            </section>
            <section className="runtime-inspector-section">
              <div className="runtime-inspector-row">
                <span>Skills</span>
                <strong>{selectedAgentSkills.length}</strong>
              </div>
              {selectedAgentSkills.length ? (
                <div className="runtime-skill-list">
                  {selectedAgentSkills.slice(0, 5).map((skillId) => <span key={skillId}>{skillId}</span>)}
                </div>
              ) : <small>当前 Agent 未绑定 Skill</small>}
            </section>
            <section className="runtime-inspector-section runtime-readiness">
              <div><CheckCircle size={14} /><span>连接状态</span><strong>{connectionLabel}</strong></div>
              <div><Cpu size={14} /><span>API Key</span><strong>{selectedAgent?.model?.has_api_key ? "就绪" : "缺失"}</strong></div>
              <div><ImageIcon size={14} /><span>输入能力</span><strong>{modelCapabilityLabel}</strong></div>
            </section>
            <button className="secondary-button runtime-reconnect" onClick={connect} disabled={status === "connected" || loading}>
              <PlugZap size={15} />
              重新连接
            </button>
          </aside>
        </div>
      ) : null}
      {activeTab === "agents" ? (
        <div className="agent-config-panel">
          <div className="agent-config-toolbar">
            <div className="agent-config-meta">
              <span>Workspace 配置</span>
              <small>{config?.path || "正在读取 config.yaml"}</small>
            </div>
            <div className="config-actions">
              <button className="secondary-button small" onClick={loadAgentConfig} disabled={configLoading}>
                <RefreshCw size={15} />
                重新读取
              </button>
              <button className="secondary-button primary-action small" onClick={saveAgentConfig} disabled={!config || configSaving}>
                <Save size={15} />
                {configSaving ? "保存中" : "保存"}
              </button>
            </div>
          </div>
          {configError ? <div className="form-error">{configError}</div> : null}
          {configStatus ? <div className="status-message">{configStatus}</div> : null}
          {config ? (
            <div className="agent-management-workspace">
              <aside className="agent-management-rail" role="navigation" aria-label="Agent 列表">
                <div className="agent-rail-heading">
                  <div>
                    <strong>Agent 列表</strong>
                    <span>{config.agents.length} 个定义</span>
                  </div>
                  <button className="icon-action" title="添加 Agent" onClick={addAgent}>
                    <Plus size={15} />
                  </button>
                </div>
                <div className="agent-rail-list">
                  {config.agents.map((agent, index) => {
                    const model = config.models.find((item) => item.id === agent.model_id);
                    const active = selectedConfigAgentIndex === index;
                    return (
                      <button
                        key={`${agent.id}-${index}`}
                        type="button"
                        className={`agent-rail-item${active ? " active" : ""}`}
                        onClick={() => setExpandedRow(index)}
                      >
                        <span className="agent-rail-icon"><Bot size={15} /></span>
                        <span className="agent-rail-copy">
                          <strong>{agent.name || agent.id || "未命名 Agent"}</strong>
                          <small>{agent.id || "未填写 ID"} · {model?.name || "未绑定模型"}</small>
                        </span>
                        <span className="agent-rail-count">{(agent.skill_ids || []).length}</span>
                      </button>
                    );
                  })}
                </div>
                <label className="agent-portfolio-binding">
                  <span>资产组合 Agent</span>
                  <select
                    aria-label="资产组合 Agent"
                    value={config.portfolio_agent_id || ""}
                    onChange={(event) => setConfig((current) => ({ ...current, portfolio_agent_id: event.target.value }))}
                  >
                    <option value="">未配置</option>
                    {config.agents.map((agent) => (
                      <option key={agent.id} value={agent.id}>{agent.name || agent.id}</option>
                    ))}
                  </select>
                  <small>资产页只绑定 Agent；工具能力由该 Agent 选择的 Skills 决定。</small>
                </label>
              </aside>
              {selectedConfigAgent ? (
                <section className="agent-detail-workspace">
                  <div className="agent-detail-editor">
                    <div className="agent-detail-topbar">
                      <div className="agent-detail-title">
                        <span className="ai-agent-orb"><Bot size={17} /></span>
                        <div>
                          <strong>{selectedConfigAgent.name || selectedConfigAgent.id || "未命名 Agent"}</strong>
                          <small>{selectedConfigAgent.id || "未填写 ID"}</small>
                        </div>
                      </div>
                      <div className="agent-detail-actions">
                        <button className="icon-action" title={expandedRow === selectedConfigAgentIndex ? "收起" : "展开"} onClick={() => setExpandedRow(selectedConfigAgentIndex)}>
                          <ChevronDown size={14} />
                        </button>
                        <button className="icon-action danger" title="删除" onClick={() => removeAgent(selectedConfigAgentIndex)}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                    <div className="agent-identity-grid">
                      <label>
                        <span>Agent ID</span>
                        <input value={selectedConfigAgent.id} onChange={(event) => updateAgent(selectedConfigAgentIndex, "id", event.target.value)} />
                      </label>
                      <label>
                        <span>显示名称</span>
                        <input value={selectedConfigAgent.name} onChange={(event) => updateAgent(selectedConfigAgentIndex, "name", event.target.value)} />
                      </label>
                      <label>
                        <span>绑定模型</span>
                        <select value={selectedConfigAgent.model_id || ""} onChange={(event) => updateAgent(selectedConfigAgentIndex, "model_id", event.target.value)}>
                          {config.models.map((model) => <option key={model.id} value={model.id}>{model.name || model.id}</option>)}
                        </select>
                      </label>
                    </div>
                    <label className="agent-prompt-editor">
                      <span>系统提示词</span>
                      <textarea value={selectedConfigAgent.system_prompt} onChange={(event) => updateAgent(selectedConfigAgentIndex, "system_prompt", event.target.value)} />
                    </label>
                    <div className="agent-detail-field">
                      <span>Skills（可选）</span>
                      <div className="agent-skill-picker">
                        {(config.skills || [])
                          .filter((skill) => skill.id.startsWith("common:") || (selectedConfigAgent.skill_ids || []).includes(skill.id))
                          .map((skill) => (
                            <label key={skill.id} className={(selectedConfigAgent.skill_ids || []).includes(skill.id) ? "selected" : ""}>
                              <input
                                type="checkbox"
                                checked={(selectedConfigAgent.skill_ids || []).includes(skill.id)}
                                onChange={() => toggleAgentSkill(selectedConfigAgentIndex, skill.id)}
                              />
                              <span>{skill.name || skill.id} ({skill.id})</span>
                            </label>
                          ))}
                        {(config.skills || []).length === 0 ? <small>暂无可选 Skill</small> : null}
                      </div>
                    </div>
                  </div>
                  <aside className="agent-profile-panel">
                    <div className="agent-profile-heading">
                      <span>运行画像</span>
                      <strong>{selectedConfigAgentModel?.name || "未绑定模型"}</strong>
                    </div>
                    <div className="agent-profile-row">
                      <span>模型模式</span>
                      <strong>{(selectedConfigAgentModel?.mode || "prompt") === "agent" ? "Agent" : "Prompt"}</strong>
                    </div>
                    <div className="agent-profile-row">
                      <span>输入能力</span>
                      <strong>{selectedConfigAgentModel?.supports_images ? "文本 + 图片" : "文本"}</strong>
                    </div>
                    <div className="agent-profile-row">
                      <span>已绑定 Skills</span>
                      <strong>{(selectedConfigAgent.skill_ids || []).length}</strong>
                    </div>
                    <div className="agent-profile-skills">
                      {(selectedConfigAgent.skill_ids || []).length ? (
                        selectedConfigAgent.skill_ids.map((skillId) => <span key={skillId}>{skillId}</span>)
                      ) : (
                        <small>未绑定 Skill；该 Agent 不会获得 Skill 暴露的工具。</small>
                      )}
                    </div>
                  </aside>
                </section>
              ) : (
                <div className="empty-state">还没有 Agent，点击添加创建一个工作代理。</div>
              )}
            </div>
          ) : (
            <div className="empty-state">正在加载 Agent 配置</div>
          )}
        </div>
      ) : null}
      {activeTab === "skills" ? (
        <div className="agent-config-panel">
          <div className="agent-config-toolbar">
            <div className="agent-config-meta">
              <span>Workspace 配置</span>
              <small>{config?.path || "正在读取 config.yaml"}</small>
            </div>
            <div className="config-actions">
              <button className="secondary-button small" onClick={loadAgentConfig} disabled={configLoading}>
                <RefreshCw size={15} />
                重新读取
              </button>
              <button className="secondary-button primary-action small" onClick={() => saveAgentConfig("Skill 工具配置已保存")} disabled={!config || configSaving}>
                <Save size={15} />
                {configSaving ? "保存中" : "保存"}
              </button>
            </div>
          </div>
          {configError ? <div className="form-error">{configError}</div> : null}
          {configStatus ? <div className="status-message">{configStatus}</div> : null}
          {config ? (
            <div className="skill-management-workspace">
              <aside className="skill-library-panel" aria-label="Skill 文件">
                <div className="skill-library-heading">
                  <div>
                    <strong>Skill 文件</strong>
                    <span>{skillItems.length} 个文件</span>
                  </div>
                  <button className="icon-action" title="添加 Skill" onClick={addSkill}>
                    <Plus size={15} />
                  </button>
                </div>
                <div className="skill-library-list">
                  {skillItems.map((skill, index) => (
                    <button
                      key={`${skill.id}-${index}`}
                      className={`skill-library-item${selectedSkillIndex === index ? " active" : ""}`}
                      onClick={() => selectSkill(index)}
                    >
                      <span className="skill-library-icon"><FileText size={15} /></span>
                      <span className="skill-library-copy">
                        <strong>{skill.id || "未命名 Skill"}</strong>
                        <small>{skill.id || "未填写 ID"}</small>
                      </span>
                    </button>
                  ))}
                </div>
                <div className="skill-library-stats">
                  <span>{config.agents.reduce((sum, agent) => sum + (agent.skill_ids || []).length, 0)} 次绑定</span>
                  <span>{toolDefinitions.length} 个工具项</span>
                </div>
              </aside>
              {selectedSkill ? (
                <section className="skill-editor-shell">
                  <div className="skill-editor-topbar">
                    <div className="skill-editor-title">
                      <span className="skill-editor-icon"><FileCode size={16} /></span>
                      <div>
                        <strong>{selectedSkill.id || "未命名 Skill"}</strong>
                        <small>{selectedSkill.id}</small>
                      </div>
                    </div>
                    <div className="skill-editor-status">
                      <span>Workspace</span>
                          <span>{selectedSkillAllow.length} tools</span>
                    </div>
                    <div className="skill-editor-actions">
                      <button
                          className="icon-action danger"
                          title="删除 Skill"
                          onClick={() => {
                            setConfig((current) => ({
                              ...current,
                              skills: (current.skills || []).filter((_, itemIndex) => itemIndex !== selectedSkillIndex)
                            }));
                            setExpandedSkillIndex((current) => {
                              const nextLength = Math.max((skillItems.length || 1) - 1, 0);
                              if (!nextLength) {
                                return null;
                              }
                              return Math.min(current ?? 0, nextLength - 1);
                            });
                          }}
                        >
                          <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                  <div className="skill-editor-fields">
                    <label>
                      <span>Skill ID</span>
                      <input value={selectedSkill.id} onChange={(event) => updateSkill(selectedSkillIndex, "id", event.target.value)} />
                    </label>
                  </div>
                  <div className="skill-editor-grid">
                    <div className="skill-markdown-pane">
                      <div className="skill-markdown-toolbar">
                        <span>Markdown 内容</span>
                        <div>
                          <button className="secondary-button small" type="button" onClick={insertSkillEvolutionTemplate}>
                            <BrainCircuit size={14} />
                            插入自进化模板
                          </button>
                          <small>实时预览</small>
                        </div>
                      </div>
                      <div className="skill-markdown-split">
                        <textarea
                          aria-label="Markdown 内容"
                          className="skill-markdown-editor"
                          placeholder="# Skill 名称&#10;&#10;描述这个 skill 的工作方式。"
                          value={selectedSkillMarkdown}
                          onChange={(event) => updateSkillContent(selectedSkill.id, event.target.value)}
                          disabled={skillContentLoading === selectedSkill.id}
                        />
                        <div className="skill-markdown-preview" aria-label="Markdown 预览">
                          {selectedSkillMarkdown.trim() ? (
                            <MarkdownMessage content={selectedSkillMarkdown} />
                          ) : (
                            <div className="skill-preview-empty">Markdown 预览会实时显示在这里</div>
                          )}
                        </div>
                      </div>
                    </div>
                    <aside className="skill-tools-pane">
                      <div className="skill-tools-heading">
                        <div>
                          <strong>工具能力</strong>
                          <span>{selectedSkillAllow.length} 个额外启用</span>
                        </div>
                      </div>
                      <div className="skill-tool-controls">
                        <label className="skill-tool-search">
                          <Search size={14} />
                          <input
                            aria-label="搜索工具能力"
                            placeholder="搜索工具名称或 ID"
                            value={skillToolQuery}
                            onChange={(event) => setSkillToolQuery(event.target.value)}
                          />
                        </label>
                        <div className="skill-tool-filter" aria-label="工具过滤">
                          <button className={skillToolFilter === "all" ? "active" : ""} onClick={() => setSkillToolFilter("all")}>全部</button>
                          <button className={skillToolFilter === "mcp" ? "active" : ""} onClick={() => setSkillToolFilter("mcp")}>MCP</button>
                          <button className={skillToolFilter === "dag" ? "active" : ""} onClick={() => setSkillToolFilter("dag")}>DAG</button>
                          <button className={skillToolFilter === "agent" ? "active" : ""} onClick={() => setSkillToolFilter("agent")}>AGENT</button>
                        </div>
                      </div>
                      <div className="skill-tool-groups">
                        {missingSkillTools.map((name) => (
                          <label key={name} className="skill-tool-row selected unavailable">
                            <input type="checkbox" checked onChange={() => toggleSkillTool(selectedSkillIndex, name)} />
                            <span className="agent-tool-check"><X size={12} /></span>
                            <span>能力不可用</span>
                            <small>{name}</small>
                          </label>
                        ))}
                        {toolDefinitionsError ? (
                          <div className="skill-tool-empty">工具目录加载失败：{toolDefinitionsError}</div>
                        ) : filteredSkillTools.length ? (
                            <div className="skill-tool-list">
                                {filteredSkillTools.map((tool) => {
                                  const selected = selectedSkillAllow.includes(tool.name);
                                  return (
                                    <label key={tool.name} className={`skill-tool-row${selected ? " selected" : ""}`}>
                                      <input
                                        type="checkbox"
                                        checked={selected}
                                        onChange={() => toggleSkillTool(selectedSkillIndex, tool.name)}
                                      />
                                      <span className="agent-tool-check" aria-hidden="true">
                                        {selected ? <Check size={12} /> : null}
                                      </span>
                                      <span>{tool.display_name}</span>
                                      <small>{tool.name}</small>
                                      <small>{tool.description}</small>
                                      <span className="skill-tool-scenes">{(tool.support_scene || []).map((scene) => <em key={scene}>{scene.toUpperCase()}</em>)}</span>
                                      <details className="skill-tool-schema">
                                        <summary>Input / Output</summary>
                                        <pre>{JSON.stringify({ input: tool.input, output: tool.output }, null, 2)}</pre>
                                      </details>
                                    </label>
                                  );
                                })}
                            </div>
                        ) : (
                          <div className="skill-tool-empty">没有匹配的工具能力</div>
                        )}
                      </div>
                    </aside>
                  </div>
                </section>
              ) : (
                <div className="empty-state">还没有 Skill，点击添加创建一个 Markdown skill。</div>
              )}
            </div>
          ) : (
            <div className="empty-state">正在加载 Agent 配置</div>
          )}
        </div>
      ) : null}
      {activeTab === "models" ? (
        <div className="agent-config-panel">
          <div className="agent-config-toolbar">
            <div className="agent-config-meta">
              <span>模型配置</span>
              <small>{config?.path || "正在读取 config.yaml"}</small>
            </div>
            <div className="config-actions">
              <button className="secondary-button small" onClick={loadAgentConfig} disabled={configLoading}>
                <RefreshCw size={15} />
                重新读取
              </button>
              <button className="secondary-button primary-action small" onClick={() => saveAgentConfig("模型配置已保存")} disabled={!config || configSaving}>
                <Save size={15} />
                {configSaving ? "保存中" : "保存"}
              </button>
            </div>
          </div>
          {configError ? <div className="form-error">{configError}</div> : null}
          {configStatus ? <div className="status-message">{configStatus}</div> : null}
          {config ? (
            <div className="agent-config-workspace">
              <aside className="agent-config-rail">
                <div className="agent-config-rail-card">
                  <span>默认模型</span>
                  <strong>{config.models.find((model) => model.id === config.default_model_id)?.name || config.default_model_id || "未设置"}</strong>
                </div>
                <div className="agent-config-rail-card compact">
                  <small>{config.models.length} 个模型</small>
                  <small>{config.agents.length} 个 Agent</small>
                </div>
              </aside>
              <div className="agent-config-content">
                <section className="agent-config-section">
                  <div className="agent-config-section-heading">
                    <div>
                      <h3>模型</h3>
                      <p>配置模型接口，API Key 留空会保留原值。</p>
                    </div>
                    <button className="secondary-button small" onClick={addModel}>
                      <Plus size={15} />
                      添加模型
                    </button>
                  </div>
                  <div className="agent-config-list">
                    {config.models.map((model, index) => (
                      <article className="agent-config-card" key={`${model.id}-${index}`}>
                        <div className="agent-config-card-title">
                          <div className="agent-config-card-summary">
                            <strong>{model.name || model.id || "未命名模型"}</strong>
                            <div className="agent-config-card-meta">
                              <span>{providerLabel(model.provider)}</span>
                              <span>{model.model || "未填写模型名"}</span>
                              <span>{model.base_url || "无 Base URL"}</span>
                            </div>
                          </div>
                          <div className="agent-config-card-controls">
                            {config.default_model_id === model.id ? <span className="badge-default">默认</span> : null}
                            <span className="badge-capability">{(model.mode || "prompt") === "agent" ? "Agent" : "Prompt"}</span>
                            {model.supports_images ? <span className="badge-capability">图片</span> : null}
                            <button className="icon-action" title={expandedModelIndex === index ? "收起" : "展开"} onClick={() => setExpandedModelIndex((current) => current === index ? null : index)}>
                              {expandedModelIndex === index ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                            </button>
                          </div>
                        </div>
                        {expandedModelIndex === index ? (
                          <>
                            <div className="agent-config-grid">
                              <label>提供商<select value={model.provider || "openai_compatible"} onChange={(event) => updateModel(index, "provider", event.target.value)}><option value="openai_compatible">OpenAI 兼容</option><option value="anthropic">Anthropic (Claude)</option></select></label>
                              <label>ID<input value={model.id} onChange={(event) => { if (!composingRef.current) updateModel(index, "id", event.target.value); }} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={(event) => { composingRef.current = false; updateModel(index, "id", event.target.value); }} /></label>
                              <label>显示名<input value={model.name} onChange={(event) => { if (!composingRef.current) updateModel(index, "name", event.target.value); }} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={(event) => { composingRef.current = false; updateModel(index, "name", event.target.value); }} /></label>
                              <label>Base URL<input value={model.base_url} onChange={(event) => updateModel(index, "base_url", event.target.value)} placeholder={(model.provider || "openai_compatible") === "anthropic" ? "可选" : ""} /></label>
                              <label>模型名<input value={model.model} onChange={(event) => updateModel(index, "model", event.target.value)} /></label>
                              <label>运行模式<select value={model.mode || "prompt"} onChange={(event) => updateModel(index, "mode", event.target.value)}><option value="prompt">Prompt</option><option value="agent">Agent</option></select></label>
                              <label>API Key<input type="password" placeholder={model.api_key_mask || "留空保留旧 key"} value={model.api_key || ""} onChange={(event) => updateModel(index, "api_key", event.target.value)} /></label>
                              <label>Temperature<input type="number" step="0.1" value={model.temperature ?? ""} onChange={(event) => updateModel(index, "temperature", event.target.value)} /></label>
                            </div>
                            <div className="agent-config-card-actions">
                              <label className="agent-checkbox"><input type="checkbox" checked={Boolean(model.supports_images)} onChange={(event) => updateModel(index, "supports_images", event.target.checked)} />支持图片</label>
                              <button className="secondary-button small" onClick={() => setConfig((current) => ({ ...current, default_model_id: model.id }))}>设为默认</button>
                              <button
                                className="secondary-button small"
                                onClick={() => {
                                  setConfig((current) => ({ ...current, models: current.models.filter((_, itemIndex) => itemIndex !== index) }));
                                  setExpandedModelIndex((current) => current === index ? null : current > index ? current - 1 : current);
                                }}
                              >
                                删除
                              </button>
                            </div>
                          </>
                        ) : null}
                      </article>
                    ))}
                  </div>
                </section>
              </div>
            </div>
          ) : (
            <div className="empty-state">正在加载模型配置</div>
          )}
        </div>
      ) : null}
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

 function ChannelsPage({ onUnauthorized }) {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showAddModal, setShowAddModal] = useState(false);
  const [addForm, setAddForm] = useState({ id: "", name: "", default_agent_id: "", auto_start: false, proxy: "" });
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [agentOptions, setAgentOptions] = useState([]);
  const [useMultiAccount, setUseMultiAccount] = useState(true);

  async function loadAgentOptions() {
    try {
      const data = await api("/api/agents/options");
      setAgentOptions(data.agents || []);
    } catch (_) {}
  }

  async function loadAccounts() {
    if (!useMultiAccount) {
      try {
        const data = await api("/api/channels/wechat/status");
        if (data.wechat) {
          setAccounts([{ id: "default", name: "微信机器人", status: data.wechat }]);
        } else {
          setAccounts([]);
        }
      } catch (err) {
        if (err.status === 401) { onUnauthorized(); return; }
        setError(err.message);
      }
      return;
    }
    try {
      const data = await api("/api/channels/wechat/accounts");
      setAccounts(data.accounts || []);
      setError("");
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      if (err.status === 404) {
        setUseMultiAccount(false);
        return;
      }
      setError(err.message);
    }
  }

  async function startAccount(id) {
    setLoading(true);
    setError("");
    try {
      if (useMultiAccount) {
        const data = await api(`/api/channels/wechat/accounts/${id}/start`, { method: "POST" });
        setAccounts(prev => prev.map(a => a.id === id ? { ...a, ...data.account, status: data.account.status || a.status } : a));
      } else {
        const data = await api("/api/channels/wechat/start", { method: "POST" });
        setAccounts([{ id: "default", name: "微信机器人", status: data.wechat }]);
      }
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setError(err.message);
    } finally { setLoading(false); }
  }

  async function stopAccount(id) {
    setLoading(true);
    setError("");
    try {
      if (useMultiAccount) {
        const data = await api(`/api/channels/wechat/accounts/${id}/stop`, { method: "POST" });
        setAccounts(prev => prev.map(a => a.id === id ? { ...a, ...data.account, status: data.account.status || a.status } : a));
      } else {
        const data = await api("/api/channels/wechat/stop", { method: "POST" });
        setAccounts([{ id: "default", name: "微信机器人", status: data.wechat }]);
      }
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setError(err.message);
    } finally { setLoading(false); }
  }

  async function updateAccountAgent(id, defaultAgentId) {
    setLoading(true);
    setError("");
    try {
      const data = await api(`/api/channels/wechat/accounts/${id}`, {
        method: "PUT",
        body: JSON.stringify({ default_agent_id: defaultAgentId }),
      });
      setAccounts(prev => prev.map(a => (
        a.id === id ? { ...a, ...data.account, status: data.account.status || a.status } : a
      )));
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setError(err.message);
    } finally { setLoading(false); }
  }

  async function addAccount() {
    if (!addForm.id.trim()) return;
    setLoading(true);
    setError("");
    try {
      await api("/api/channels/wechat/accounts", {
        method: "POST",
        body: JSON.stringify({
          id: addForm.id.trim(),
          name: addForm.name.trim() || addForm.id.trim(),
          default_agent_id: addForm.default_agent_id,
          auto_start: addForm.auto_start,
          proxy: addForm.proxy,
        }),
      });
      setShowAddModal(false);
      setAddForm({ id: "", name: "", default_agent_id: "", auto_start: false, proxy: "" });
      await loadAccounts();
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setError(err.message);
    } finally { setLoading(false); }
  }

  async function deleteAccount(id) {
    setLoading(true);
    setError("");
    try {
      await api(`/api/channels/wechat/accounts/${id}`, { method: "DELETE" });
      setDeleteConfirm(null);
      setAccounts(prev => prev.filter(a => a.id !== id));
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setError(err.message);
    } finally { setLoading(false); }
  }

  useEffect(() => {
    loadAgentOptions();
    loadAccounts();
    const timer = setInterval(loadAccounts, 3000);
    return () => clearInterval(timer);
  }, [useMultiAccount]);

  return (
    <section className="page-section channels-section">
      <div className="channels-hero">
        <span>Channels</span>
        <h2>消息渠道</h2>
        <p>把外部消息入口接入 Agent runtime。支持多个微信账号独立扫码登录，每个账号可绑定不同微信号，独立收发消息。</p>
      </div>

      {useMultiAccount && (
        <div className="channels-toolbar">
          <button className="primary-button" onClick={() => setShowAddModal(true)} disabled={loading}>
            <Plus size={16} /> 添加账号
          </button>
        </div>
      )}

      {error ? <div className="form-error">{error}</div> : null}

      {accounts.length === 0 && !loading ? (
        <div className="channel-grid">
          <article className="channel-card">
            <h3>暂无微信账号</h3>
            <p>点击"添加账号"创建第一个微信机器人账号，然后扫码登录即可使用。</p>
          </article>
        </div>
      ) : (
        <div className="channel-grid">
          {accounts.map(acct => {
            const s = acct.status || {};
            const qrSource = s.qrcode_data_url || s.qrcode_url || "";
            const acctError = s.error || "";
            const acctErrorText = acctError.includes("400")
              ? "扫码通道返回了内部状态提示。二维码仍可扫描；如果扫码失败，请停止后重新启动微信。"
              : acctError;
            return (
              <article className="channel-card primary-channel" key={acct.id}>
                <div className="channel-card-heading">
                  <span className="channel-icon"><MessageSquare size={18} /></span>
                  <div className="channel-card-title">
                    <h3>{acct.name || acct.id}</h3>
                    <p>个人微信机器人 · {acct.id}</p>
                  </div>
                  <div className="channel-card-meta">
                    <strong>{s.login_state || "stopped"}</strong>
                    {useMultiAccount ? (
                      <label className="channel-agent-select">
                        <span>绑定 Agent</span>
                        <select
                          aria-label={`${acct.id} 绑定 Agent`}
                          value={acct.default_agent_id || ""}
                          onChange={e => updateAccountAgent(acct.id, e.target.value)}
                          disabled={loading}
                        >
                      <option value="">未绑定 Agent</option>
                          {agentOptions.map(a => (
                            <option key={a.id} value={a.id}>{a.name || a.id}</option>
                          ))}
                        </select>
                      </label>
                    ) : null}
                  </div>
                </div>
                <div className="wechat-control">
                  <div>
                    <span className={`runtime-status ${s.running ? "connected" : ""}`}>
                      {s.running ? "运行中" : "未启动"}
                    </span>
                    {s.user ? <strong>{s.user}</strong> : null}
                  </div>
                  <div className="wechat-actions">
                    <button className="secondary-button primary-action" onClick={() => startAccount(acct.id)} disabled={loading || s.running}>
                      <Play size={16} /> 启动微信
                    </button>
                    <button className="secondary-button" onClick={() => stopAccount(acct.id)} disabled={loading || !s.running}>
                      <XCircle size={16} /> 停止
                    </button>
                    {useMultiAccount && (
                      <button
                        className="secondary-button delete-account-button"
                        onClick={() => setDeleteConfirm(acct.id)}
                        disabled={loading}
                        title="删除账号"
                      >
                        <X size={16} />
                      </button>
                    )}
                  </div>
                </div>
                {qrSource ? (
                  <div className="wechat-qr-panel">
                    <img src={qrSource} alt="微信登录二维码" />
                    <div>
                      <strong>用微信扫码登录</strong>
                      <p>状态：{s.qrcode_status || "等待扫码"}</p>
                    </div>
                  </div>
                ) : null}
                {acctErrorText && !qrSource ? <div className="form-error">{acctErrorText}</div> : null}
                {acctErrorText && qrSource ? <div className="wechat-notice">{acctErrorText}</div> : null}
              </article>
            );
          })}
        </div>
      )}

      {/* Add Account Modal */}
      {showAddModal ? (
        <div className="modal-overlay" onClick={() => setShowAddModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>添加微信账号</h3>
              <button className="icon-button" onClick={() => setShowAddModal(false)}><X size={18} /></button>
            </div>
            <div className="modal-form">
              <label className="form-label">
                账号 ID
                <input
                  type="text"
                  className="form-input"
                  placeholder="英文、数字、短横线，如 work"
                  value={addForm.id}
                  onChange={e => setAddForm(f => ({ ...f, id: e.target.value }))}
                />
              </label>
              <label className="form-label">
                显示名称
                <input
                  type="text"
                  className="form-input"
                  placeholder="如：工作微信"
                  value={addForm.name}
                  onChange={e => setAddForm(f => ({ ...f, name: e.target.value }))}
                />
              </label>
              <label className="form-label">
                绑定 Agent
                <select
                  className="form-input"
                  value={addForm.default_agent_id}
                  onChange={e => setAddForm(f => ({ ...f, default_agent_id: e.target.value }))}
                >
                  <option value="">未绑定 Agent</option>
                  {agentOptions.map(a => (
                    <option key={a.id} value={a.id}>{a.name || a.id}</option>
                  ))}
                </select>
              </label>
              <label className="form-label-checkbox">
                <input
                  type="checkbox"
                  checked={addForm.auto_start}
                  onChange={e => setAddForm(f => ({ ...f, auto_start: e.target.checked }))}
                />
                启动时自动登录
              </label>
              <label className="form-label">
                HTTP 代理（可选）
                <input
                  type="text"
                  className="form-input"
                  placeholder="如 http://127.0.0.1:7890"
                  value={addForm.proxy}
                  onChange={e => setAddForm(f => ({ ...f, proxy: e.target.value }))}
                />
              </label>
              <div className="modal-actions">
                <button className="secondary-button" onClick={() => setShowAddModal(false)}>取消</button>
                <button className="primary-button" onClick={addAccount} disabled={loading || !addForm.id.trim()}>保存</button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* Delete Confirmation Modal */}
      {deleteConfirm ? (
        <div className="modal-overlay" onClick={() => setDeleteConfirm(null)}>
          <div className="modal-content modal-confirm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>确认删除</h3>
              <button className="icon-button" onClick={() => setDeleteConfirm(null)}><X size={18} /></button>
            </div>
            <p>删除后将停止该账号并清除登录会话，无法恢复。</p>
            <div className="modal-actions">
              <button className="secondary-button" onClick={() => setDeleteConfirm(null)}>取消</button>
              <button className="primary-button danger-button" onClick={() => deleteAccount(deleteConfirm)} disabled={loading}>确认删除</button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function PortfolioPage({ onUnauthorized }) {
  const [holdings, setHoldings] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState(null);
  const [form, setForm] = useState({
    type: "stock", symbol: "", name: "", quantity: "", avg_cost: "", currency: "CNY", notes: ""
  });
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [chatStatus, setChatStatus] = useState("disconnected");
  const [chatError, setChatError] = useState("");
  const [chatSessions, setChatSessions] = useState([]);
  const [activeChatSessionId, setActiveChatSessionId] = useState(null);
  const [chatSessionLoading, setChatSessionLoading] = useState(false);
  const [portfolioAgentId, setPortfolioAgentId] = useState("");
  const [portfolioAgent, setPortfolioAgent] = useState(null);
  const [portfolioConfigLoading, setPortfolioConfigLoading] = useState(true);
  const chatEndRef = useRef(null);
  const wsRef = useRef(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  function chatWsUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/api/agents/chat/connect`;
  }

  function connectChat() {
    if (!portfolioAgentId) return;
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return;
    setChatError("");
    setChatStatus("connecting");
    const ws = new WebSocket(chatWsUrl());
    wsRef.current = ws;
    ws.onopen = () => setChatStatus("connected");
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "status") {
        setChatStatus(message.status === "running" ? "running" : "connected");
      }
      if (message.type === "assistant_message") {
        setChatMessages((items) => {
          const kept = [];
          let checkpoints = [];
          for (const item of items) {
            if (item.role === "pending") {
              checkpoints = item.checkpoints || [];
            } else {
              kept.push(item);
            }
          }
          kept.push({
            role: "assistant",
            content: message.content || "",
            checkpoints,
            checkpointsCollapsed: true
          });
          return kept;
        });
        setChatSending(false);
        setChatStatus("connected");
        loadHoldings();
        loadPortfolioSessions();
      }
      if (message.type === "checkpoint") {
        setChatMessages((items) =>
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
        setChatMessages((items) => items.filter((item) => item.role !== "pending"));
        setChatError(message.message || "AI 回复失败");
        setChatSending(false);
        setChatStatus("connected");
      }
    };
    ws.onerror = () => {
      setChatError("连接失败");
      setChatSending(false);
      setChatStatus("disconnected");
    };
    ws.onclose = () => {
      setChatSending(false);
      setChatStatus("disconnected");
    };
  }

  async function loadPortfolioAgent() {
    setPortfolioConfigLoading(true);
    try {
      const data = await api("/api/agents/options");
      const agentId = data.portfolio_agent_id || "";
      const agent = (data.agents || []).find((item) => item.id === agentId) || null;
      setPortfolioAgentId(agent ? agentId : "");
      setPortfolioAgent(agent);
      if (agentId && !agent) {
        setChatError("资产组合 Agent 配置无效，请在 Agent 管理中重新选择");
      }
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setChatError(err.message);
    } finally {
      setPortfolioConfigLoading(false);
    }
  }

  useEffect(() => {
    if (!portfolioAgentId) return undefined;
    connectChat();
    loadPortfolioSessions();
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [portfolioAgentId]);

  async function loadPortfolioSessions() {
    if (!portfolioAgentId) return;
    setChatSessionLoading(true);
    try {
      const data = await api(`/api/sessions?agent_id=${encodeURIComponent(portfolioAgentId)}`);
      setChatSessions(data.sessions || []);
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
    } finally {
      setChatSessionLoading(false);
    }
  }

  async function createPortfolioSession() {
    if (!portfolioAgentId) return null;
    setChatSessionLoading(true);
    try {
      const data = await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ agent_id: portfolioAgentId })
      });
      const session = data.session;
      setChatSessions((items) => [session, ...items]);
      setActiveChatSessionId(session.id);
      setChatMessages([]);
      return session;
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return null; }
      setChatError(err.message);
      return null;
    } finally {
      setChatSessionLoading(false);
    }
  }

  async function switchPortfolioSession(sessionId) {
    if (!portfolioAgentId) return;
    setChatSessionLoading(true);
    setChatError("");
    try {
      const data = await api(`/api/sessions/${sessionId}?agent_id=${encodeURIComponent(portfolioAgentId)}`);
      const session = data.session;
      setActiveChatSessionId(session.id);
      setChatMessages((session.messages || []).map((msg) => ({
        role: msg.role,
        content: msg.content,
        checkpoints: msg.checkpoints || [],
        checkpointsCollapsed: true
      })));
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setChatError(err.message);
    } finally {
      setChatSessionLoading(false);
    }
  }

  async function loadHoldings() {
    setLoading(true);
    setError("");
    try {
      const data = await api("/api/portfolio/holdings");
      setHoldings(data.holdings || []);
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setError(err.message);
    } finally { setLoading(false); }
  }

  useEffect(() => {
    loadHoldings();
    loadPortfolioAgent();
  }, []);

  function resetForm() {
    setForm({ type: "stock", symbol: "", name: "", quantity: "", avg_cost: "", currency: "CNY", notes: "" });
    setEditId(null);
    setShowForm(false);
  }

  function openEdit(h) {
    setForm({
      type: h.type, symbol: h.symbol, name: h.name,
      quantity: String(h.quantity), avg_cost: String(h.avg_cost),
      currency: h.currency, notes: h.notes
    });
    setEditId(h.id);
    setShowForm(true);
  }

  async function submitForm(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const body = {
        type: form.type, symbol: form.symbol, name: form.name,
        quantity: parseFloat(form.quantity), avg_cost: parseFloat(form.avg_cost),
        currency: form.currency, notes: form.notes
      };
      if (editId) {
        const data = await api(`/api/portfolio/holdings/${editId}`, {
          method: "PUT", body: JSON.stringify(body)
        });
        setHoldings(prev => prev.map(h => h.id === editId ? data.holding : h));
      } else {
        const data = await api("/api/portfolio/holdings", {
          method: "POST", body: JSON.stringify(body)
        });
        setHoldings(prev => [data.holding, ...prev]);
      }
      resetForm();
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setError(err.message);
    } finally { setLoading(false); }
  }

  async function deleteHolding(id) {
    if (!confirm("确定删除这条持仓记录？")) return;
    setLoading(true);
    try {
      await api(`/api/portfolio/holdings/${id}`, { method: "DELETE" });
      setHoldings(prev => prev.filter(h => h.id !== id));
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return; }
      setError(err.message);
    } finally { setLoading(false); }
  }

  function typeLabel(t) {
    return { stock: "股票", fund: "基金", crypto: "加密货币" }[t] || t;
  }

  function totalCost(holdings) {
    return holdings.reduce((s, h) => s + h.total_cost, 0).toFixed(2);
  }

  async function sendChat() {
    const msg = chatInput.trim();
    if (!msg || chatSending || !portfolioAgentId) return;
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      connectChat();
      setChatError("连接未就绪，请稍后重试");
      return;
    }
    let sessionId = activeChatSessionId;
    if (!sessionId) {
      const session = await createPortfolioSession();
      sessionId = session?.id || null;
    }
    setChatInput("");
    setChatSending(true);
    setChatError("");
    setChatMessages((items) => [
      ...items,
      { role: "user", content: msg },
      { role: "pending", content: "思考中", checkpoints: [] }
    ]);
    wsRef.current.send(
      JSON.stringify({
        type: "message",
        agent_id: portfolioAgentId,
        content: msg,
        session_id: sessionId
      })
    );
  }

  function handleChatKey(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendChat();
    }
  }

  return (
    <section className="page-section portfolio-section">
      <div className="tab-bar" role="tablist" aria-label="资产组合">
        <button className="active">
          <TrendingUp size={16} />
          投资组合
        </button>
      </div>

      {error ? <div className="error-state">{error}</div> : null}

      <div className="portfolio-workspace">
        {/* ── Left: Holdings panel ── */}
        <div className="portfolio-panel">
          <div className="portfolio-panel-header">
            <div>
              <strong>持仓列表</strong>
              <span className="portfolio-summary">
                {holdings.length} 项 · 总成本 {totalCost(holdings)} {holdings[0]?.currency || "CNY"}
              </span>
            </div>
            <button className="secondary-button" onClick={() => { resetForm(); setShowForm(true); }}>
              <Plus size={15} />
              添加持仓
            </button>
          </div>

          {showForm ? (
            <form className="portfolio-form" onSubmit={submitForm}>
              <div className="portfolio-form-row">
                <label>
                  <span>类型</span>
                  <select value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))}>
                    <option value="stock">股票</option>
                    <option value="fund">基金</option>
                    <option value="crypto">加密货币</option>
                  </select>
                </label>
                <label>
                  <span>代码</span>
                  <input value={form.symbol} onChange={e => setForm(f => ({ ...f, symbol: e.target.value }))}
                    placeholder="如 AAPL, 00700" required />
                </label>
                <label>
                  <span>名称</span>
                  <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    placeholder="如 苹果, 腾讯控股" />
                </label>
              </div>
              <div className="portfolio-form-row">
                <label>
                  <span>数量</span>
                  <input type="number" step="any" value={form.quantity}
                    onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))}
                    placeholder="100" required />
                </label>
                <label>
                  <span>均价</span>
                  <input type="number" step="any" value={form.avg_cost}
                    onChange={e => setForm(f => ({ ...f, avg_cost: e.target.value }))}
                    placeholder="380" required />
                </label>
                <label>
                  <span>货币</span>
                  <select value={form.currency} onChange={e => setForm(f => ({ ...f, currency: e.target.value }))}>
                    <option value="CNY">CNY</option>
                    <option value="USD">USD</option>
                    <option value="HKD">HKD</option>
                  </select>
                </label>
              </div>
              <label>
                <span>备注</span>
                <input value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                  placeholder="可选备注" />
              </label>
              <div className="portfolio-form-actions">
                <button className="secondary-button primary-action" type="submit" disabled={loading}>
                  {editId ? "保存修改" : "添加"}
                </button>
                <button className="secondary-button" type="button" onClick={resetForm}>取消</button>
              </div>
            </form>
          ) : null}

          <div className="portfolio-table-wrap">
            {holdings.length === 0 && !loading ? (
              <div className="empty-state">暂无持仓记录。点击"添加持仓"手动录入，或通过 AI 对话创建。</div>
            ) : (
              <table className="portfolio-table">
                <thead>
                  <tr>
                    <th>类型</th>
                    <th>代码</th>
                    <th>名称</th>
                    <th>数量</th>
                    <th>均价</th>
                    <th>总成本</th>
                    <th>货币</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map(h => (
                    <tr key={h.id}>
                      <td><span className="type-badge">{typeLabel(h.type)}</span></td>
                      <td className="mono">{h.symbol}</td>
                      <td>{h.name}</td>
                      <td className="num">{h.quantity}</td>
                      <td className="num">{h.avg_cost}</td>
                      <td className="num">{h.total_cost}</td>
                      <td>{h.currency}</td>
                      <td className="actions-cell">
                        <button className="icon-btn" title="编辑" onClick={() => openEdit(h)}>
                          <FileEdit size={14} />
                        </button>
                        <button className="icon-btn danger" title="删除" onClick={() => deleteHolding(h.id)}>
                          <X size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* ── Right: AI Chat panel ── */}
        <div className="portfolio-chat-panel">
          <div className="portfolio-chat-header">
            <Bot size={16} />
            <strong>{portfolioAgent?.name || "资产组合 Agent"}</strong>
            <button className="secondary-button small" onClick={createPortfolioSession} disabled={chatSessionLoading || !portfolioAgentId}>
              <Plus size={14} />
              新对话
            </button>
            <span className={`runtime-status ${chatStatus === "connected" || chatStatus === "running" ? "connected" : ""}`}>
              {chatStatus === "running" ? "生成中" : chatStatus === "connected" ? "已连接" : "未连接"}
            </span>
          </div>
          {!portfolioConfigLoading && !portfolioAgentId ? (
            <div className="portfolio-agent-empty">
              <Bot size={28} />
              <strong>请先在 Agent 管理中配置资产组合 Agent</strong>
              <span>Agent 通过自身的 Skills 决定可用工具，资产页不会选择或注入 Skill。</span>
            </div>
          ) : (
          <div className="portfolio-chat-body">
            <aside className="portfolio-chat-history">
              <div className="portfolio-chat-history-heading">
                <span>对话历史</span>
                <small>{chatSessions.length} 个</small>
              </div>
              {chatSessionLoading ? (
                <div className="portfolio-chat-history-empty">加载中...</div>
              ) : chatSessions.length === 0 ? (
                <div className="portfolio-chat-history-empty">暂无历史</div>
              ) : (
                chatSessions.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    className={`portfolio-chat-history-item${activeChatSessionId === session.id ? " active" : ""}`}
                    onClick={() => switchPortfolioSession(session.id)}
                  >
                    <span>{session.title || "新对话"}</span>
                    <small>{session.message_count} 条消息</small>
                  </button>
                ))
              )}
            </aside>
            <div className="portfolio-chat-main">
              <div className="portfolio-chat-messages">
                {chatMessages.length === 0 ? (
                  <div className="chat-empty-hint">
                    <Bot size={32} />
                    <p>告诉我想做什么，比如：</p>
                    <ul>
                      <li>"帮我添加 100 股腾讯，成本价 380 HKD"</li>
                      <li>"目前都有哪些持仓？"</li>
                      <li>"删除苹果的持仓记录"</li>
                    </ul>
                  </div>
                ) : (
                  chatMessages.map((m, i) => (
                    <div key={i} className={`portfolio-chat-msg ${m.role}`}>
                      <div className="chat-msg-bubble">{m.content}</div>
                      {m.checkpoints?.length ? (
                        <div className="agent-checkpoints-wrapper" style={{marginTop: 4}}>
                          <button
                            type="button"
                            className="checkpoint-toggle"
                            onClick={() => setChatMessages((items) =>
                              items.map((item, idx) =>
                                idx === i ? { ...item, checkpointsCollapsed: !item.checkpointsCollapsed } : item
                              )
                            )}
                          >
                            <span className="checkpoint-toggle-icon">
                              {m.checkpointsCollapsed ? "▶" : "▼"}
                            </span>
                            {m.checkpointsCollapsed ? "展开" : "折叠"}思维链
                            <span className="checkpoint-toggle-count">{m.checkpoints.length} 步</span>
                          </button>
                          {!m.checkpointsCollapsed ? (
                            <ol className="agent-checkpoints">
                              {m.checkpoints.map((cp, cpi) => (
                                <li key={`${cp.stage}-${cpi}`}>
                                  <strong>{cp.title}</strong>
                                  {cp.detail ? <small>{cp.detail}</small> : null}
                                </li>
                              ))}
                            </ol>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  ))
                )}
                {chatError ? <div className="form-error" style={{margin: "8px 14px", fontSize: 12}}>{chatError}</div> : null}
                <div ref={chatEndRef} />
              </div>
              <div className="portfolio-chat-input-row">
                <input
                  className="chat-input"
                  value={chatInput}
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={handleChatKey}
                  placeholder="输入指令管理持仓..."
                  disabled={chatSending || chatStatus === "disconnected" || !portfolioAgentId}
                />
                <button className="secondary-button primary-action send-btn" onClick={sendChat} disabled={!chatInput.trim() || chatSending || chatStatus === "disconnected" || !portfolioAgentId}>
                  <Send size={16} />
                </button>
              </div>
            </div>
          </div>
          )}
        </div>
      </div>
    </section>
  );
}

function AppShell({ onLogout }) {
  const initialPath = window.location.pathname === "/" ? "/agents" : window.location.pathname;
  const [path, setPath] = useState(initialPath);
  const isAgentPath = path === "/models" || path === "/agents" || path.startsWith("/agents/");
  const navItems = useMemo(
    () => [
      { path: "/agents", label: "Agent", icon: Bot },
      { path: "/critique", label: "维度聊天室", icon: BrainCircuit },
      { path: "/channels", label: "渠道", icon: MessageSquare },
      { path: "/portfolio", label: "资产组合", icon: TrendingUp },
      { path: "/system", label: "系统", icon: Settings }
    ],
    []
  );

  function navigate(nextPath) {
    window.history.pushState({}, "", nextPath);
    setPath(nextPath);
  }

  useEffect(() => {
    if (window.location.pathname === "/") {
      window.history.replaceState({}, "", "/agents");
    }
    const handler = () => setPath(window.location.pathname === "/" ? "/agents" : window.location.pathname);
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
    if (isAgentPath) {
      return <AgentPage onUnauthorized={unauthorized} initialTab={agentTabForPath(path)} />;
    }
    if (path === "/portfolio") {
      return <PortfolioPage onUnauthorized={unauthorized} />;
    }
    if (path === "/critique") {
      return <CritiquePage onUnauthorized={unauthorized} />;
    }
    if (path === "/system") {
      return <SystemPage onUnauthorized={unauthorized} />;
    }
    if (path === "/channels") {
      return <ChannelsPage onUnauthorized={unauthorized} />;
    }
    return <AgentPage onUnauthorized={unauthorized} initialTab="chat" />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-title">
          <Bot size={24} />
          <span>超级个人平台</span>
        </div>
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            if (item.path === "/agents") {
              return (
                <div className="sidebar-nav-group" key={item.path}>
                  <button
                    type="button"
                    className={isAgentPath ? "active" : ""}
                    aria-current={isAgentPath ? "page" : undefined}
                    onClick={() => navigate(item.path)}
                  >
                    <Icon size={18} />
                    <span>{item.label}</span>
                  </button>
                  {isAgentPath ? (
                    <div className="sidebar-subnav" role="navigation" aria-label="Agent 功能">
                      {AGENT_NAV_ITEMS.map((child) => {
                        const ChildIcon = child.icon;
                        const active = child.tab === agentTabForPath(path);
                        return (
                          <button
                            key={child.path}
                            type="button"
                            className={active ? "active" : ""}
                            aria-current={active ? "page" : undefined}
                            onClick={() => navigate(child.path)}
                          >
                            <ChildIcon size={15} />
                            <span>{child.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            }
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
      <main className={`content${isAgentPath ? " content-agents" : ""}`}>{renderPage()}</main>
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
