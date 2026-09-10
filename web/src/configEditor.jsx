import React, { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, Plus, Trash2, X } from "lucide-react";

const AGENT_TOOL_CARDS = [
  {
    id: "search_context",
    name: "Search Context",
    summary: "搜索本地知识和当前 Agent 映射的 WebDAV 目录。",
    badge: "只读",
  },
  {
    id: "search_session",
    name: "Search Session",
    summary: "按关键词检索当前会话历史，用于关联前文要求、图片和链接。",
    badge: "会话",
  },
  {
    id: "arxiv",
    name: "arXiv",
    summary: "搜索 arXiv 论文摘要和链接，内置 3 秒请求间隔。",
    badge: "学术",
  },
  {
    id: "yahoo_finance_news",
    name: "Yahoo Finance News",
    summary: "按股票代码获取轻量财经新闻，不作为交易级数据源。",
    badge: "财经",
  },
  {
    id: "write_context",
    name: "Write Context",
    summary: "用户确认后写入 /files/... 或当前 Agent 可写的 /webdav/...。",
    badge: "需确认",
  },
  {
    id: "browser_extract",
    name: "Browser Extract",
    summary: "用 Playwright 搜索 Bing、打开公开网页，并提取渲染后的文本和链接。",
    badge: "浏览器",
  },
  {
    id: "schedule",
    name: "Schedule",
    summary: "创建、查看、修改或删除当前会话自己创建的定时任务，并回发到渠道。",
    badge: "调度",
  },
  {
    id: "execute_code",
    name: "Execute Code",
    summary: "在 Docker + gVisor 沙箱中运行短 Python 或 shell 代码，输出 artifacts。",
    badge: "沙箱",
  },
];

const DEFAULT_CONFIG = {
  auth: { token: "" },
  server: { host: "0.0.0.0", port: 8888 },
  browser: { proxy: "", timeout_ms: 60000, allow_private_hosts: [] },
  code_execution: {
    enabled: true,
    runtime: "docker_gvisor",
    languages: ["python", "shell"],
    timeout_seconds: 20,
    max_stdout_chars: 20000,
    max_stderr_chars: 20000,
    max_file_bytes: 10485760,
    max_files: 20,
    docker: {
      runtime: "runsc",
      image: "python:3.12-slim-bookworm",
      network: "none",
      memory: "512m",
      cpus: "1",
      pids_limit: 64,
    },
  },
  llm: {
    default_model_id: "default",
    models: [
      {
        id: "default",
        name: "默认 DeepAgent 模型",
        provider: "openai_compatible",
        base_url: "https://api.openai.com/v1",
        api_key: "",
        model: "gpt-4o-mini",
        temperature: 0.7,
        supports_images: false,
      },
    ],
  },
  nutstore: {
    enabled: false,
    base_url: "https://dav.jianguoyun.com/dav/",
    username: "",
    password: "",
    root_path: "/",
  },
  context: {
    webdav_sync: {
      enabled: false,
      root_path: "/notebook",
      interval_seconds: 600,
      max_files_per_root: 500,
      max_file_size_bytes: 524288,
      extensions: [".md", ".txt", ".json", ".jsonl"],
    },

  },
  maintenance: {
    enabled: true,
    interval_seconds: 86400,
    retention_days: 15,
    dry_run: false,
  },
  channels: {
    wechat_personal: {
      enabled: false,
      accounts: [],
    },
  },
  agents: {
    definitions: [
      {
        id: "assistant",
        name: "默认助手",
        system_prompt: "你是一个运行在后端的 DeepAgent。",
        model_id: "default",
        context_ids: [],
        webdav: { enabled: false, path: "/", permission: "write", description: "" },
        deepagent: {
          max_iterations: 60,
          todo_list: true,
          tools: [],
        },
      },
    ],
  },
};

export function parseConfigDraft(draft) {
  try {
    return { config: withDefaults(parseSimpleYaml(draft)), error: "" };
  } catch (error) {
    return { config: withDefaults({}), error: error.message };
  }
}

function useConfigDraft(draft) {
  return useMemo(() => {
    try {
      return { config: withDefaults(parseSimpleYaml(draft)), error: "" };
    } catch (error) {
      return { config: withDefaults({}), error: error.message };
    }
  }, [draft]);
}

export function ConfigVisualEditor({ draft, onChange, readOnly, onSyncWebdav, onTestWebdav }) {
  const parsed = useConfigDraft(draft);
  const config = parsed.config;

  function update(mutator) {
    const next = cloneConfig(config);
    mutator(next);
    onChange(dumpSimpleYaml(next));
  }

  function updateWebdavSync(field, value) {
    update((next) => {
      next.context.webdav_sync = { ...next.context.webdav_sync, [field]: value };
    });
  }

  if (parsed.error) {
    return (
      <div className="config-editor">
        <p className="error">config.yaml 解析失败：{parsed.error}</p>
        <textarea
          className="workspace-editor"
          value={draft}
          readOnly={readOnly}
          onChange={(event) => onChange(event.target.value)}
          spellCheck={false}
        />
      </div>
    );
  }

  return (
    <div className="config-editor">
      <section className="config-section">
        <div className="config-section-title">
          <strong>认证与服务</strong>
          <span>auth / server</span>
        </div>
        <div className="config-grid">
          <ConfigField label="访问 Token">
            <input
              type="text"
              value={config.auth.token}
              readOnly={readOnly}
              onChange={(event) => update((next) => (next.auth.token = event.target.value))}
            />
          </ConfigField>
          <ConfigField label="监听 Host">
            <input
              value={config.server.host}
              readOnly={readOnly}
              onChange={(event) => update((next) => (next.server.host = event.target.value))}
            />
          </ConfigField>
          <ConfigField label="监听端口">
            <input
              type="number"
              min="1"
              max="65535"
              value={config.server.port}
              readOnly={readOnly}
              onChange={(event) => update((next) => (next.server.port = Number(event.target.value) || 8888))}
            />
          </ConfigField>
        </div>
      </section>

      <section className="config-section">
        <div className="config-section-title">
          <strong>浏览器抓取</strong>
          <span>browser / Playwright</span>
        </div>
        <div className="config-grid">
          <ConfigField label="代理">
            <input
              value={config.browser.proxy}
              readOnly={readOnly}
              placeholder="http://127.0.0.1:7890 或 socks5://127.0.0.1:7890"
              onChange={(event) => update((next) => (next.browser.proxy = event.target.value))}
            />
          </ConfigField>
          <ConfigField label="超时毫秒">
            <input
              type="number"
              min="1000"
              value={config.browser.timeout_ms}
              readOnly={readOnly}
              onChange={(event) => update((next) => (next.browser.timeout_ms = Number(event.target.value) || 60000))}
            />
          </ConfigField>
          <ConfigField label="允许内网 Host">
            <input
              value={normalizeList(config.browser.allow_private_hosts).join(", ")}
              readOnly={readOnly}
              placeholder="finance.wulang.vip, .wulang.vip"
              onChange={(event) =>
                update((next) => {
                  next.browser.allow_private_hosts = normalizeList(event.target.value);
                })
              }
            />
          </ConfigField>
        </div>
      </section>

      <section className="config-section">
        <div className="config-section-title">
          <div>
            <strong>维护清理</strong>
            <span>maintenance</span>
          </div>
          <label className="config-toggle">
            <input
              type="checkbox"
              checked={Boolean(config.maintenance.enabled)}
              disabled={readOnly}
              onChange={(event) => update((next) => (next.maintenance.enabled = event.target.checked))}
            />
            <span>启用</span>
          </label>
        </div>
        <div className="config-grid">
          <ConfigField label="保留天数">
            <input
              type="number"
              min="1"
              value={config.maintenance.retention_days}
              readOnly={readOnly}
              onChange={(event) =>
                update((next) => (next.maintenance.retention_days = Number(event.target.value) || 15))
              }
            />
          </ConfigField>
          <ConfigField label="清理间隔秒数">
            <input
              type="number"
              min="60"
              value={config.maintenance.interval_seconds}
              readOnly={readOnly}
              onChange={(event) =>
                update((next) => (next.maintenance.interval_seconds = Number(event.target.value) || 86400))
              }
            />
          </ConfigField>
          <label className="config-toggle field-toggle">
            <input
              type="checkbox"
              checked={Boolean(config.maintenance.dry_run)}
              disabled={readOnly}
              onChange={(event) => update((next) => (next.maintenance.dry_run = event.target.checked))}
            />
            <span>只预览不删除</span>
          </label>
        </div>
      </section>

      <section className="config-section">
        <div className="config-section-title">
          <strong>坚果云 WebDAV</strong>
          <label className="config-toggle">
            <input
              type="checkbox"
              checked={config.nutstore.enabled}
              disabled={readOnly}
              onChange={(event) => update((next) => (next.nutstore.enabled = event.target.checked))}
            />
            <span>启用</span>
          </label>
        </div>
        <div className="config-grid two">
          <ConfigField label="WebDAV 地址">
            <input
              value={config.nutstore.base_url}
              readOnly={readOnly}
              onChange={(event) => update((next) => (next.nutstore.base_url = event.target.value))}
            />
          </ConfigField>
          <ConfigField label="根目录">
            <input
              value={config.nutstore.root_path}
              readOnly={readOnly}
              onChange={(event) => update((next) => (next.nutstore.root_path = event.target.value))}
            />
          </ConfigField>
          <ConfigField label="账号">
            <input
              value={config.nutstore.username}
              readOnly={readOnly}
              onChange={(event) => update((next) => (next.nutstore.username = event.target.value))}
            />
          </ConfigField>
          <ConfigField label="应用密码">
            <input
              type="password"
              value={config.nutstore.password}
              readOnly={readOnly}
              onChange={(event) => update((next) => (next.nutstore.password = event.target.value))}
            />
          </ConfigField>
        </div>
      </section>

      <section className="config-section">
        <div className="config-section-title">
          <div>
            <strong>Context WebDAV 同步</strong>
            <span>连接和同步由全局管理，访问范围在 Agent 中配置</span>
          </div>
          <div className="config-inline-actions">
            {onTestWebdav ? (
              <button
                className="ghost"
                type="button"
                disabled={readOnly}
                onClick={onTestWebdav}
                title="使用当前表单草稿测试 WebDAV，不保存配置"
              >
                测试连接
              </button>
            ) : null}
            {onSyncWebdav ? (
              <button
                className="ghost"
                type="button"
                disabled={readOnly}
                onClick={onSyncWebdav}
                title="使用已保存的 config.yaml 同步 WebDAV"
              >
                <RefreshCw size={14} />
                立即同步
              </button>
            ) : null}
            <label className="config-toggle">
              <input
                type="checkbox"
                checked={Boolean(config.context.webdav_sync.enabled)}
                disabled={readOnly}
                onChange={(event) => updateWebdavSync("enabled", event.target.checked)}
              />
              <span>启用同步</span>
            </label>
          </div>
        </div>
        <div className="config-grid">
          <ConfigField label="同步根目录">
            <input
              value={config.context.webdav_sync.root_path}
              readOnly={readOnly}
              onChange={(event) => updateWebdavSync("root_path", event.target.value)}
            />
          </ConfigField>
          <ConfigField label="同步间隔秒数">
            <input
              type="number"
              min="60"
              value={config.context.webdav_sync.interval_seconds}
              readOnly={readOnly}
              onChange={(event) => updateWebdavSync("interval_seconds", Number(event.target.value) || 600)}
            />
          </ConfigField>
          <ConfigField label="最大文件数">
            <input
              type="number"
              min="1"
              value={config.context.webdav_sync.max_files_per_root}
              readOnly={readOnly}
              onChange={(event) => updateWebdavSync("max_files_per_root", Number(event.target.value) || 500)}
            />
          </ConfigField>
          <ConfigField label="单文件最大字节">
            <input
              type="number"
              min="1"
              value={config.context.webdav_sync.max_file_size_bytes}
              readOnly={readOnly}
              onChange={(event) => updateWebdavSync("max_file_size_bytes", Number(event.target.value) || 524288)}
            />
          </ConfigField>
          <ConfigField label="文本索引后缀">
            <input
              value={(config.context.webdav_sync.extensions || []).join(", ")}
              readOnly={readOnly}
              onChange={(event) => updateWebdavSync("extensions", splitList(event.target.value))}
            />
          </ConfigField>
        </div>

      </section>
    </div>
  );
}

export function ProviderConfigEditor({ draft, onChange, readOnly }) {
  const parsed = useConfigDraft(draft);
  const config = parsed.config;
  const models = config.llm.models;

  function update(mutator) {
    const next = cloneConfig(config);
    mutator(next);
    onChange(dumpSimpleYaml(next));
  }

  function updateModel(index, field, value) {
    update((next) => {
      const previousId = next.llm.models[index]?.id;
      next.llm.models[index] = { ...next.llm.models[index], [field]: value };
      if (field === "id" && previousId && previousId !== value) {
        if (next.llm.default_model_id === previousId) next.llm.default_model_id = value;
        next.agents.definitions = next.agents.definitions.map((agent) =>
          agent.model_id === previousId ? { ...agent, model_id: value } : agent
        );
      }
    });
  }

  function removeModel(index) {
    update((next) => {
      if (next.llm.models.length <= 1) return;
      const removed = next.llm.models[index]?.id;
      next.llm.models.splice(index, 1);
      const remainingIds = next.llm.models.map((item) => item.id).filter(Boolean);
      const fallbackId = remainingIds.includes(next.llm.default_model_id)
        ? next.llm.default_model_id
        : remainingIds[0] || "";
      next.llm.default_model_id = fallbackId;
      if (removed) {
        next.agents.definitions = next.agents.definitions.map((agent) =>
          agent.model_id === removed ? { ...agent, model_id: fallbackId } : agent
        );
      }
    });
  }

  if (parsed.error) {
    return <ConfigFallbackEditor draft={draft} onChange={onChange} readOnly={readOnly} error={parsed.error} />;
  }

  return (
    <div className="config-editor">
      <section className="config-section">
        <div className="config-section-title">
          <strong>Provider 默认项</strong>
          <span>llm.default_model_id</span>
        </div>
        <div className="config-grid">
          <ConfigField label="默认模型">
            <select
              value={config.llm.default_model_id}
              disabled={readOnly}
              onChange={(event) => update((next) => (next.llm.default_model_id = event.target.value))}
            >
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.id || "未命名模型"}
                </option>
              ))}
            </select>
          </ConfigField>
        </div>
      </section>
      <ConfigList
        title="Providers"
        subtitle="llm.models"
        readOnly={readOnly}
        onAdd={() =>
          update((next) => {
            const id = `model_${next.llm.models.length + 1}`;
            next.llm.models.push({
              id,
              name: id,
              provider: "openai_compatible",
              base_url: "",
              api_key: "",
              model: "",
              temperature: 0.7,
              supports_images: false,
            });
            if (!next.llm.default_model_id) next.llm.default_model_id = id;
          })
        }
      >
        {models.map((model, index) => (
          <div className="config-item" key={`model-${index}`}>
            <div className="config-item-title">
              <strong>{model.id || `model_${index + 1}`}</strong>
              <button
                className="icon-button delete-button"
                type="button"
                title={models.length <= 1 ? "至少保留一个模型" : "删除模型"}
                disabled={readOnly || models.length <= 1}
                onClick={() => removeModel(index)}
              >
                <Trash2 size={14} />
              </button>
            </div>
            <div className="config-grid two">
              <ConfigField label="ID">
                <input value={model.id} readOnly={readOnly} onChange={(event) => updateModel(index, "id", event.target.value)} />
              </ConfigField>
              <ConfigField label="名称">
                <input value={model.name} readOnly={readOnly} onChange={(event) => updateModel(index, "name", event.target.value)} />
              </ConfigField>
              <ConfigField label="Provider">
                <select value={model.provider} disabled={readOnly} onChange={(event) => updateModel(index, "provider", event.target.value)}>
                  <option value="openai_compatible">openai_compatible</option>
                  <option value="anthropic">anthropic</option>
                </select>
              </ConfigField>
              <ConfigField label="模型名">
                <input value={model.model} readOnly={readOnly} onChange={(event) => updateModel(index, "model", event.target.value)} />
              </ConfigField>
              <ConfigField label="Base URL">
                <input value={model.base_url} readOnly={readOnly} onChange={(event) => updateModel(index, "base_url", event.target.value)} />
              </ConfigField>
              <ConfigField label="API Key">
                <input type="password" value={model.api_key} readOnly={readOnly} onChange={(event) => updateModel(index, "api_key", event.target.value)} />
              </ConfigField>
              <ConfigField label="Temperature">
                <input
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
                  value={model.temperature ?? ""}
                  readOnly={readOnly}
                  onChange={(event) =>
                    updateModel(index, "temperature", event.target.value === "" ? undefined : Number(event.target.value))
                  }
                />
              </ConfigField>
              {[ ["input_price_per_million", "输入单价 / 百万 tokens"], ["output_price_per_million", "输出单价 / 百万 tokens"] ].map(([field, label]) => (
                <ConfigField key={field} label={label}>
                  <input type="number" min="0" step="any" value={model[field] ?? ""} readOnly={readOnly}
                    placeholder="未配置"
                    onChange={(event) => updateModel(index, field, event.target.value === "" ? undefined : Number(event.target.value))} />
                </ConfigField>
              ))}
              <ConfigField label="估算币种（不含缓存折扣、阶梯价及工具费用）">
                <select value={model.price_currency || "USD"} disabled={readOnly} onChange={(event) => updateModel(index, "price_currency", event.target.value)}>
                  <option value="USD">USD</option><option value="CNY">CNY</option>
                </select>
              </ConfigField>
              <label className="config-toggle field-toggle">
                <input
                  type="checkbox"
                  checked={Boolean(model.supports_images)}
                  disabled={readOnly}
                  onChange={(event) => updateModel(index, "supports_images", event.target.checked)}
                />
                <span>支持图片</span>
              </label>
            </div>
          </div>
        ))}
      </ConfigList>
    </div>
  );
}

export function AgentConfigEditor({ draft, onChange, onSave, readOnly }) {
  const parsed = useConfigDraft(draft);
  const config = parsed.config;
  const agents = config.agents.definitions;
  const [editing, setEditing] = useState(null);

  function createAgent() {
    let suffix = agents.length + 1;
    while (agents.some((agent) => agent.id === `agent_${suffix}`)) suffix += 1;
    const id = `agent_${suffix}`;
    setEditing({ index: null, agent: { ...cloneConfig(DEFAULT_CONFIG.agents.definitions[0]), id, name: id, model_id: config.llm.default_model_id } });
  }

  async function saveAgent(agent) {
    const next = cloneConfig(config);
    if (editing.index === null) next.agents.definitions.push(agent);
    else next.agents.definitions[editing.index] = agent;
    const content = dumpSimpleYaml(next);
    if (onSave) {
      if (!(await onSave(content))) return false;
    } else onChange(content);
    setEditing(null);
    return true;
  }

  if (parsed.error) return <ConfigFallbackEditor draft={draft} onChange={onChange} readOnly={readOnly} error={parsed.error} />;
  return (
    <div className="config-editor">
      <ConfigList title="Agents" subtitle={`${agents.length} 个 Agent`} readOnly={readOnly} onAdd={createAgent}>
        {agents.map((agent, index) => (
          <div className="agent-config-row" key={`agent-${index}`}>
            <button type="button" className="agent-config-open" aria-label={`配置 Agent ${agent.name || agent.id}`}
              onClick={() => setEditing({ index, agent: cloneConfig(agent) })}>
              <span className="agent-config-identity"><strong>{agent.name || agent.id}</strong><small>{agent.id}</small></span>
              <span><small>模型</small><strong>{agent.model_id || config.llm.default_model_id || "默认模型"}</strong></span>
              <span><small>工具</small><strong>{agent.deepagent.tools.length} 项</strong></span>
              <span><small>WebDAV</small><strong>{agent.webdav.enabled ? `${agent.webdav.path} · ${agent.webdav.permission === "read" ? "只读" : "读写"}` : "未启用"}</strong></span>
              <span className="agent-config-edit-label">配置</span>
            </button>
            <button className="icon-button delete-button" type="button" title="删除 Agent"
              disabled={readOnly || agents.length <= 1} onClick={() => {
                const next = cloneConfig(config);
                next.agents.definitions.splice(index, 1);
                onChange(dumpSimpleYaml(next));
              }}><Trash2 size={14} /></button>
          </div>
        ))}
      </ConfigList>
      {editing ? <AgentSettingsDialog initialAgent={editing.agent} models={config.llm.models}
        readOnly={readOnly} onClose={() => setEditing(null)} onSave={saveAgent} /> : null}
    </div>
  );
}

function AgentSettingsDialog({ initialAgent, models, readOnly, onClose, onSave }) {
  const dialogRef = useRef(null);
  const [agent, setAgent] = useState(() => cloneConfig(initialAgent));
  const [showTools, setShowTools] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog.showModal) dialog.showModal();
    else dialog.setAttribute("open", "");
    return () => { if (dialog.close) dialog.close(); };
  }, []);

  function updateAgent(field, value) { setAgent((current) => ({ ...current, [field]: value })); }
  function updateDeepAgent(field, value) {
    setAgent((current) => ({ ...current, deepagent: { ...current.deepagent, [field]: value } }));
  }
  function updateAgentWebdav(field, value) {
    setAgent((current) => ({ ...current, webdav: { ...current.webdav, [field]: value } }));
  }
  function toggleTool(toolId, enabled) {
    setAgent((current) => ({ ...current, deepagent: { ...current.deepagent,
      tools: enabled ? [...new Set([...current.deepagent.tools, toolId])] : current.deepagent.tools.filter((id) => id !== toolId),
    } }));
  }
  async function save() {
    if (saving) return;
    if (!agent.id.trim() || !agent.name.trim() || !agent.system_prompt.trim()) {
      setError("请填写 ID、名称和 System Prompt。"); return;
    }
    setSaving(true);
    setError("");
    try { if (!(await onSave(agent))) setError("保存失败，请检查配置后重试；编辑内容已保留。"); }
    catch (err) { setError(err.message || "保存失败，请重试。"); }
    finally { setSaving(false); }
  }
  return (
    <dialog ref={dialogRef} className="agent-settings-dialog" aria-labelledby="agent-settings-title"
      onCancel={(event) => { event.preventDefault(); if (!saving) onClose(); }}
      onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
      <div className="dialog-header">
        <div><strong id="agent-settings-title">配置 Agent</strong><span>{initialAgent.name || initialAgent.id}</span></div>
        <button type="button" className="icon-button" aria-label="关闭 Agent 配置" disabled={saving} onClick={onClose}><X size={16} /></button>
      </div>
      <div className="agent-settings-fields" inert={saving ? "" : undefined}>
            <div className="config-grid two">
              <ConfigField label="ID">
                <input value={agent.id} readOnly={readOnly} onChange={(event) => updateAgent("id", event.target.value)} />
              </ConfigField>
              <ConfigField label="名称">
                <input value={agent.name} readOnly={readOnly} onChange={(event) => updateAgent("name", event.target.value)} />
              </ConfigField>
              <ConfigField label="模型">
                <select value={agent.model_id || ""} disabled={readOnly} onChange={(event) => updateAgent("model_id", event.target.value)}>
                  <option value="">默认模型</option>
                  {models.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.id || "未命名模型"}
                    </option>
                  ))}
                </select>
              </ConfigField>
              <ConfigField label="Context IDs">
                <input
                  value={(agent.context_ids || []).join(", ")}
                  readOnly={readOnly}
                  onChange={(event) => updateAgent("context_ids", splitList(event.target.value))}
                />
              </ConfigField>
            </div>
            <ConfigField label="System Prompt">
              <textarea
                value={agent.system_prompt}
                readOnly={readOnly}
                onChange={(event) => updateAgent("system_prompt", event.target.value)}
              />
            </ConfigField>
            <section className="config-subsection">
              <div className="config-section-title compact">
                <strong>DeepAgent 运行选项</strong>
                <span>限制单次任务的执行步数</span>
              </div>
              <div className="agent-tool-summary">
                <div><strong>Agent 工具</strong><span>{formatSelectedTools(agent.deepagent?.tools)}</span></div>
                <button type="button" onClick={() => setShowTools((value) => !value)} aria-expanded={showTools}>配置工具</button>
              </div>
              {showTools ? <div className="tool-choice-grid">
                {AGENT_TOOL_CARDS.map((tool) => (
                  <label className={`tool-choice ${agent.deepagent.tools.includes(tool.id) ? "selected" : ""}`} key={tool.id}>
                    <input type="checkbox" aria-label={tool.name} disabled={readOnly || saving}
                      checked={agent.deepagent.tools.includes(tool.id)} onChange={(event) => toggleTool(tool.id, event.target.checked)} />
                    <span><strong>{tool.name}</strong><small>{tool.summary}</small></span><em>{tool.badge}</em>
                  </label>
                ))}
              </div> : null}
              <ConfigField label="最大执行步数">
                <input type="number" min="1" max="1000"
                  value={agent.deepagent?.max_iterations ?? 60} readOnly={readOnly}
                  onChange={(event) => updateDeepAgent("max_iterations", Number(event.target.value) || 60)} />
              </ConfigField>
            </section>
            <section className="config-subsection webdav-workspace">
              <div className="config-section-title compact"><strong>WebDAV 工作区</strong><span>访问入口 /webdav/ · 修改会同步到坚果云</span></div>
              <label className="config-toggle field-toggle">
                <input type="checkbox" checked={agent.webdav.enabled} disabled={readOnly}
                  onChange={(event) => updateAgentWebdav("enabled", event.target.checked)} />
                <span>启用 WebDAV</span>
              </label>
              {agent.webdav.enabled ? (
                <div className="config-grid two">
                  <ConfigField label="映射目录（相对于全局同步目录）">
                    <input value={agent.webdav.path} readOnly={readOnly} placeholder="/"
                      onChange={(event) => updateAgentWebdav("path", event.target.value)} />
                  </ConfigField>
                  <ConfigField label="访问权限">
                    <select value={agent.webdav.permission} disabled={readOnly}
                      onChange={(event) => updateAgentWebdav("permission", event.target.value)}>
                      <option value="write">读写</option><option value="read">只读</option>
                    </select>
                  </ConfigField>
                  <ConfigField label="目录说明">
                    <textarea value={agent.webdav.description} readOnly={readOnly}
                      placeholder="用户文档与共享知识库；说明会提供给 Agent。"
                      onChange={(event) => updateAgentWebdav("description", event.target.value)} />
                  </ConfigField>
                </div>
              ) : null}
            </section>

      </div>
      <div className="dialog-footer">
        <span role={error ? "alert" : undefined} className={error ? "error" : ""}>{error || "保存后立即生效；取消将放弃本次编辑。"}</span>
        <button type="button" disabled={saving} onClick={onClose}>取消</button>
        <button type="button" className="primary" disabled={readOnly || saving} onClick={save}>{saving ? "保存中…" : "保存 Agent"}</button>
      </div>
    </dialog>
  );
}

function formatSelectedTools(value) {
  const selected = normalizeList(value);
  if (selected.length === 0) return "未授权工具";
  return selected.join(", ");
}

function ConfigList({ title, subtitle, children, extra, onAdd, readOnly }) {
  return (
    <section className="config-section">
      <div className="config-section-title">
        <div>
          <strong>{title}</strong>
          <span>{subtitle}</span>
        </div>
        <div className="config-section-actions">
          {extra}
          <button type="button" onClick={onAdd} disabled={readOnly}>
            <Plus size={14} />
            新增
          </button>
        </div>
      </div>
      <div className="config-list">{children}</div>
    </section>
  );
}

function ConfigField({ label, children }) {
  return (
    <label className="config-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function ConfigFallbackEditor({ draft, onChange, readOnly, error }) {
  return (
    <div className="config-editor">
      <p className="error">config.yaml 解析失败：{error}</p>
      <textarea
        className="workspace-editor"
        value={draft}
        readOnly={readOnly}
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
      />
    </div>
  );
}

function withDefaults(value) {
  const incoming = isPlainObject(value) ? cloneConfig(value) : {};
  const config = mergeObjects(cloneConfig(DEFAULT_CONFIG), incoming);
  config.llm.models = Array.isArray(config.llm.models) ? config.llm.models.map((model) => ({ ...model })) : [];
  config.agents.definitions = Array.isArray(config.agents.definitions)
    ? config.agents.definitions.map((agent) => ({
        ...agent,
        context_ids: normalizeList(agent.context_ids),
        deepagent: normalizeDeepAgent(agent.deepagent),
        webdav: { enabled: false, path: "/", permission: "write", description: "", ...(isPlainObject(agent.webdav) ? agent.webdav : {}) },
      }))
    : [];
  config.channels.wechat_personal.accounts = Array.isArray(config.channels.wechat_personal.accounts)
    ? config.channels.wechat_personal.accounts.map((account) => ({ ...account }))
    : [];
  config.maintenance = isPlainObject(config.maintenance)
    ? {
        enabled: config.maintenance.enabled !== false,
        interval_seconds: Number(config.maintenance.interval_seconds) || 86400,
        retention_days: Number(config.maintenance.retention_days) || 15,
        dry_run: Boolean(config.maintenance.dry_run),
      }
    : cloneConfig(DEFAULT_CONFIG.maintenance);
  config.browser = isPlainObject(config.browser)
    ? {
        proxy: String(config.browser.proxy || ""),
        timeout_ms: Number(config.browser.timeout_ms) || 60000,
        allow_private_hosts: normalizeList(config.browser.allow_private_hosts),
      }
    : cloneConfig(DEFAULT_CONFIG.browser);
  config.code_execution = normalizeCodeExecution(config.code_execution);
  config.context.webdav_sync.root_path = normalizePath(config.context.webdav_sync.root_path || "/");
  config.context.webdav_sync.extensions = normalizeList(config.context.webdav_sync.extensions);
  delete config.context.webdav_permissions;
  delete config.context.webdav_roots;
  if (config.llm.models.length === 0) config.llm.models = cloneConfig(DEFAULT_CONFIG.llm.models);
  if (config.agents.definitions.length === 0) config.agents.definitions = cloneConfig(DEFAULT_CONFIG.agents.definitions);
  return config;
}

function mergeObjects(base, incoming) {
  for (const [key, value] of Object.entries(incoming)) {
    if (isPlainObject(value) && isPlainObject(base[key])) {
      base[key] = mergeObjects(base[key], value);
    } else {
      base[key] = value;
    }
  }
  return base;
}

function cloneConfig(value) {
  return JSON.parse(JSON.stringify(value));
}

function normalizeList(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item).trim()).filter(Boolean);
}

function normalizePath(value) {
  const raw = String(value || "").trim();
  if (!raw) return "/";
  const parts = raw.split("/").filter(Boolean);
  return `/${parts.join("/")}`;
}

function normalizeDeepAgent(value) {
  const raw = isPlainObject(value) ? value : {};
  return { max_iterations: raw.max_iterations ?? 60, todo_list: true, tools: normalizeList(raw.tools) };
}

function normalizeCodeExecution(value) {
  const next = mergeObjects(cloneConfig(DEFAULT_CONFIG.code_execution), isPlainObject(value) ? value : {});
  next.enabled = Boolean(next.enabled);
  next.runtime = String(next.runtime || "docker_gvisor");
  next.languages = normalizeList(next.languages);
  next.timeout_seconds = Number(next.timeout_seconds) || 20;
  next.max_stdout_chars = Number(next.max_stdout_chars) || 20000;
  next.max_stderr_chars = Number(next.max_stderr_chars) || 20000;
  next.max_file_bytes = Number(next.max_file_bytes) || 10485760;
  next.max_files = Number(next.max_files) || 20;
  next.docker = isPlainObject(next.docker)
    ? {
        runtime: String(next.docker.runtime || "runsc"),
        image: String(next.docker.image || "python:3.12-slim-bookworm"),
        network: String(next.docker.network || "none"),
        memory: String(next.docker.memory || "512m"),
        cpus: String(next.docker.cpus || "1"),
        pids_limit: Number(next.docker.pids_limit) || 64,
      }
    : cloneConfig(DEFAULT_CONFIG.code_execution.docker);
  return next;
}

function splitList(value) {
  return String(value)
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseSimpleYaml(text) {
  const lines = String(text)
    .replace(/\t/g, "  ")
    .split(/\r?\n/)
    .map((raw) => ({ raw, indent: raw.match(/^ */)?.[0].length || 0, text: raw.trim() }))
    .filter((line) => line.text && !line.text.startsWith("#"));
  const [value] = parseBlock(lines, 0, 0);
  return value || {};
}

function parseBlock(lines, start, indent) {
  if (start >= lines.length || lines[start].indent < indent) return [{}, start];
  return lines[start].text.startsWith("- ") ? parseArray(lines, start, indent) : parseObject(lines, start, indent);
}

function parseObject(lines, start, indent) {
  const result = {};
  let index = start;
  while (index < lines.length) {
    const line = lines[index];
    if (line.indent < indent) break;
    if (line.indent > indent) {
      index += 1;
      continue;
    }
    if (line.text.startsWith("- ")) break;
    const item = readKeyValue(line.text);
    if (!item) throw new Error(`invalid line: ${line.raw.trim()}`);
    if (isBlockScalar(item.value)) {
      const block = readBlockScalar(lines, index + 1, indent + 2);
      result[item.key] = block.value;
      index = block.next;
    } else if (item.value === "") {
      const nextLine = lines[index + 1];
      if (nextLine && nextLine.text.startsWith("- ") && nextLine.indent >= indent) {
        const [child, next] = parseBlock(lines, index + 1, nextLine.indent);
        result[item.key] = child;
        index = next;
      } else if (nextLine && nextLine.indent > indent) {
        const [child, next] = parseBlock(lines, index + 1, lines[index + 1].indent);
        result[item.key] = child;
        index = next;
      } else {
        result[item.key] = {};
        index += 1;
      }
    } else {
      result[item.key] = parseScalar(item.value);
      index += 1;
    }
  }
  return [result, index];
}

function parseArray(lines, start, indent) {
  const result = [];
  let index = start;
  while (index < lines.length) {
    const line = lines[index];
    if (line.indent < indent) break;
    if (line.indent > indent) {
      index += 1;
      continue;
    }
    if (!line.text.startsWith("- ")) break;
    const rest = line.text.slice(2).trim();
    if (!rest) {
      const [child, next] = parseBlock(lines, index + 1, indent + 2);
      result.push(child);
      index = next;
      continue;
    }
    const item = readKeyValue(rest);
    if (item) {
      const object = {};
      if (isBlockScalar(item.value)) {
        const block = readBlockScalar(lines, index + 1, indent + 2);
        object[item.key] = block.value;
        index = block.next;
      } else {
        object[item.key] = item.value === "" ? {} : parseScalar(item.value);
        index += 1;
      }
      if (index < lines.length && lines[index].indent > indent) {
        const [child, next] = parseBlock(lines, index, lines[index].indent);
        if (isPlainObject(child)) Object.assign(object, child);
        index = next;
      }
      result.push(object);
    } else {
      result.push(parseScalar(rest));
      index += 1;
    }
  }
  return [result, index];
}

function readKeyValue(text) {
  const colon = text.indexOf(":");
  if (colon < 0) return null;
  return { key: text.slice(0, colon).trim(), value: text.slice(colon + 1).trim() };
}

function readBlockScalar(lines, start, indent) {
  const values = [];
  let index = start;
  while (index < lines.length && lines[index].indent >= indent) {
    values.push(lines[index].raw.slice(Math.min(indent, lines[index].raw.length)));
    index += 1;
  }
  return { value: values.join("\n"), next: index };
}

function parseScalar(value) {
  const trimmed = stripInlineComment(value.trim());
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (trimmed === "null") return null;
  if (trimmed === "[]") return [];
  if (trimmed === "{}") return {};
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    try {
      return JSON.parse(trimmed.startsWith("'") ? `"${trimmed.slice(1, -1).replace(/"/g, '\\"')}"` : trimmed);
    } catch {
      return trimmed.slice(1, -1);
    }
  }
  return trimmed;
}

function stripInlineComment(value) {
  if (value.startsWith('"') || value.startsWith("'")) return value;
  const index = value.indexOf(" #");
  return index >= 0 ? value.slice(0, index).trim() : value;
}

function isBlockScalar(value) {
  return value === "|" || value === "|-" || value === "|+";
}

function dumpSimpleYaml(value, indent = 0) {
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return value.map((item) => dumpArrayItem(item, indent)).join("\n");
  }
  if (!isPlainObject(value)) return formatScalar(value);
  return Object.entries(value)
    .filter(([, child]) => child !== undefined)
    .map(([key, child]) => dumpObjectItem(key, child, indent))
    .join("\n");
}

function dumpObjectItem(key, value, indent) {
  const pad = " ".repeat(indent);
  if (Array.isArray(value)) {
    return value.length === 0 ? `${pad}${key}: []` : `${pad}${key}:\n${dumpSimpleYaml(value, indent + 2)}`;
  }
  if (isPlainObject(value)) {
    const body = dumpSimpleYaml(value, indent + 2);
    return body ? `${pad}${key}:\n${body}` : `${pad}${key}: {}`;
  }
  return `${pad}${key}: ${formatScalar(value)}`;
}

function dumpArrayItem(value, indent) {
  const pad = " ".repeat(indent);
  if (!isPlainObject(value)) return `${pad}- ${formatScalar(value)}`;
  const entries = Object.entries(value).filter(([, child]) => child !== undefined);
  if (entries.length === 0) return `${pad}- {}`;
  const [firstKey, firstValue] = entries[0];
  const lines = [];
  if (Array.isArray(firstValue) || isPlainObject(firstValue)) {
    lines.push(`${pad}- ${firstKey}:`);
    lines.push(dumpSimpleYaml(firstValue, indent + 4));
  } else {
    lines.push(`${pad}- ${firstKey}: ${formatScalar(firstValue)}`);
  }
  for (const [key, child] of entries.slice(1)) {
    lines.push(dumpObjectItem(key, child, indent + 2));
  }
  return lines.join("\n");
}

function formatScalar(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (value === null) return "null";
  return JSON.stringify(String(value ?? ""));
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
