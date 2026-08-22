import React, { useMemo, useState } from "react";
import { Plus, Trash2, X } from "lucide-react";

const AGENT_TOOL_CARDS = [
  {
    id: "search_context",
    name: "Search Context",
    summary: "搜索本地知识和已同步的 WebDAV 只读目录。",
    badge: "只读",
  },
  {
    id: "write_context",
    name: "Write Context",
    summary: "用户确认后写入 /files/... 或可写 WebDAV root。",
    badge: "需确认",
  },
  {
    id: "browser_extract",
    name: "Browser Extract",
    summary: "用 Playwright 打开公开网页，提取渲染后的文本和链接。",
    badge: "浏览器",
  },
];

const DEFAULT_CONFIG = {
  auth: { token: "" },
  server: { host: "0.0.0.0", port: 8888 },
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
      interval_seconds: 600,
      max_files_per_root: 500,
      max_file_size_bytes: 524288,
      extensions: [".md", ".txt", ".json", ".jsonl"],
    },
    webdav_roots: [
      {
        id: "my_notes",
        name: "我的心得",
        path: "/Knowledge/notes",
        readable: true,
        writable: false,
        protected: true,
      },
      {
        id: "agent_inbox",
        name: "Agent 写入区",
        path: "/AgentWorkspace/inbox",
        readable: true,
        writable: true,
        protected: false,
      },
    ],
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
        deepagent: {
          max_iterations: 60,
          name: "",
          debug: false,
          todo_list: true,
          filesystem: {
            enabled: false,
            root: "agent",
            mode: "read_write",
          },
          use_longterm_memory: true,
          tools: [],
          interrupt_on: [],
          middleware: [],
          subagents: [],
          response_format: "",
          context_schema: "",
          checkpointer: false,
          store: "",
          cache: "",
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

export function ConfigVisualEditor({ draft, onChange, readOnly }) {
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

  function updateWebdavRoot(index, field, value) {
    update((next) => {
      const root = { ...next.context.webdav_roots[index], [field]: value };
      if (field === "protected" && value) root.writable = false;
      if (field === "writable" && value) root.protected = false;
      next.context.webdav_roots[index] = root;
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
            <span>context.webdav_sync / context.webdav_roots</span>
          </div>
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
        <div className="config-grid">
          <ConfigField label="同步间隔秒数">
            <input
              type="number"
              min="60"
              value={config.context.webdav_sync.interval_seconds}
              readOnly={readOnly}
              onChange={(event) => updateWebdavSync("interval_seconds", Number(event.target.value) || 600)}
            />
          </ConfigField>
          <ConfigField label="单 Root 最大文件数">
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
          <ConfigField label="文件后缀">
            <input
              value={(config.context.webdav_sync.extensions || []).join(", ")}
              readOnly={readOnly}
              onChange={(event) => updateWebdavSync("extensions", splitList(event.target.value))}
            />
          </ConfigField>
        </div>
        <ConfigList
          title="WebDAV Roots"
          subtitle="远端路径按 nutstore.root_path + WebDAV 路径解析；本地缓存落在 workspace/context/webdav/"
          readOnly={readOnly}
          onAdd={() =>
            update((next) => {
              const id = `webdav_${next.context.webdav_roots.length + 1}`;
              next.context.webdav_roots.push({
                id,
                name: id,
                path: "/",
                readable: true,
                writable: false,
                protected: true,
              });
            })
          }
        >
          {config.context.webdav_roots.map((root, index) => (
            <div className="config-item" key={`webdav-root-${index}`}>
              <div className="config-item-title">
                <strong>{root.id || `root_${index + 1}`}</strong>
                <button
                  className="icon-button delete-button"
                  type="button"
                  title="删除 Root"
                  disabled={readOnly}
                  onClick={() => update((next) => next.context.webdav_roots.splice(index, 1))}
                >
                  <Trash2 size={14} />
                </button>
              </div>
              <div className="config-grid two">
                <ConfigField label="ID">
                  <input value={root.id} readOnly={readOnly} onChange={(event) => updateWebdavRoot(index, "id", event.target.value)} />
                </ConfigField>
                <ConfigField label="名称">
                  <input value={root.name} readOnly={readOnly} onChange={(event) => updateWebdavRoot(index, "name", event.target.value)} />
                </ConfigField>
                <ConfigField label="WebDAV 路径">
                  <input value={root.path} readOnly={readOnly} onChange={(event) => updateWebdavRoot(index, "path", event.target.value)} />
                </ConfigField>
                <div className="builtin-toggle-row">
                  <label className="config-toggle field-toggle">
                    <input
                      type="checkbox"
                      checked={Boolean(root.readable)}
                      disabled={readOnly}
                      onChange={(event) => updateWebdavRoot(index, "readable", event.target.checked)}
                    />
                    <span>可读</span>
                  </label>
                  <label className="config-toggle field-toggle">
                    <input
                      type="checkbox"
                      checked={Boolean(root.writable)}
                      disabled={readOnly}
                      onChange={(event) => updateWebdavRoot(index, "writable", event.target.checked)}
                    />
                    <span>可写</span>
                  </label>
                  <label className="config-toggle field-toggle">
                    <input
                      type="checkbox"
                      checked={Boolean(root.protected)}
                      disabled={readOnly}
                      onChange={(event) => updateWebdavRoot(index, "protected", event.target.checked)}
                    />
                    <span>保护</span>
                  </label>
                </div>
              </div>
            </div>
          ))}
        </ConfigList>
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

export function AgentConfigEditor({ draft, onChange, readOnly }) {
  const parsed = useConfigDraft(draft);
  const config = parsed.config;
  const models = config.llm.models;
  const agents = config.agents.definitions;
  const [toolDialogAgentIndex, setToolDialogAgentIndex] = useState(null);
  const toolDialogAgent = Number.isInteger(toolDialogAgentIndex) ? agents[toolDialogAgentIndex] : null;

  function update(mutator) {
    const next = cloneConfig(config);
    mutator(next);
    onChange(dumpSimpleYaml(next));
  }

  function updateAgent(index, field, value) {
    update((next) => {
      next.agents.definitions[index] = { ...next.agents.definitions[index], [field]: value };
    });
  }

  function updateDeepAgent(index, field, value) {
    update((next) => {
      const current = next.agents.definitions[index].deepagent || cloneConfig(DEFAULT_CONFIG.agents.definitions[0].deepagent);
      next.agents.definitions[index].deepagent = { ...current, [field]: value };
    });
  }

  function updateDeepAgentFilesystem(index, field, value) {
    update((next) => {
      const current = next.agents.definitions[index].deepagent || cloneConfig(DEFAULT_CONFIG.agents.definitions[0].deepagent);
      const filesystem = current.filesystem || cloneConfig(DEFAULT_CONFIG.agents.definitions[0].deepagent.filesystem);
      next.agents.definitions[index].deepagent = {
        ...current,
        filesystem: { ...filesystem, [field]: value },
      };
    });
  }

  function toggleTool(index, toolId, enabled) {
    update((next) => {
      const current = next.agents.definitions[index].deepagent || cloneConfig(DEFAULT_CONFIG.agents.definitions[0].deepagent);
      const toolSet = new Set(normalizeList(current.tools));
      if (enabled) {
        toolSet.add(toolId);
      } else {
        toolSet.delete(toolId);
      }
      const orderedKnownTools = AGENT_TOOL_CARDS.map((tool) => tool.id).filter((id) => toolSet.has(id));
      const customTools = [...toolSet].filter((id) => !AGENT_TOOL_CARDS.some((tool) => tool.id === id));
      next.agents.definitions[index].deepagent = {
        ...current,
        tools: [...orderedKnownTools, ...customTools],
      };
    });
  }

  if (parsed.error) {
    return <ConfigFallbackEditor draft={draft} onChange={onChange} readOnly={readOnly} error={parsed.error} />;
  }

  return (
    <div className="config-editor">

      <ConfigList
        title="Agents"
        subtitle="agents.definitions"
        readOnly={readOnly}
        onAdd={() =>
          update((next) => {
            const id = `agent_${next.agents.definitions.length + 1}`;
            next.agents.definitions.push({
              id,
              name: id,
              system_prompt: "你是一个运行在后端的 DeepAgent。",
              model_id: next.llm.default_model_id,
              context_ids: [],
              deepagent: cloneConfig(DEFAULT_CONFIG.agents.definitions[0].deepagent),
            });
          })
        }
      >
        {agents.map((agent, index) => (
          <div className="config-item" key={`agent-${index}`}>
            <div className="config-item-title">
              <strong>{agent.id || `agent_${index + 1}`}</strong>
              <button
                className="icon-button delete-button"
                type="button"
                title="删除 Agent"
                disabled={readOnly || agents.length <= 1}
                onClick={() => update((next) => next.agents.definitions.splice(index, 1))}
              >
                <Trash2 size={14} />
              </button>
            </div>
            <div className="config-grid two">
              <ConfigField label="ID">
                <input value={agent.id} readOnly={readOnly} onChange={(event) => updateAgent(index, "id", event.target.value)} />
              </ConfigField>
              <ConfigField label="名称">
                <input value={agent.name} readOnly={readOnly} onChange={(event) => updateAgent(index, "name", event.target.value)} />
              </ConfigField>
              <ConfigField label="模型">
                <select value={agent.model_id || ""} disabled={readOnly} onChange={(event) => updateAgent(index, "model_id", event.target.value)}>
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
                  onChange={(event) => updateAgent(index, "context_ids", splitList(event.target.value))}
                />
              </ConfigField>
            </div>
            <ConfigField label="System Prompt">
              <textarea
                value={agent.system_prompt}
                readOnly={readOnly}
                onChange={(event) => updateAgent(index, "system_prompt", event.target.value)}
              />
            </ConfigField>
            <section className="config-subsection">
              <div className="config-section-title compact">
                <strong>DeepAgent 运行选项</strong>
                <span>create_deep_agent / recursion_limit</span>
              </div>
              <div className="agent-tool-summary">
                <div>
                  <strong>Agent 工具</strong>
                  <span>{formatSelectedTools(agent.deepagent?.tools)}</span>
                </div>
                <button type="button" onClick={() => setToolDialogAgentIndex(index)}>
                  配置工具
                </button>
              </div>
              <div className="agent-tool-summary">
                <div>
                  <strong>DeepAgent 内置能力</strong>
                  <span>
                    write_todos
                    {agent.deepagent?.filesystem?.enabled ? `, filesystem -> workspace/agents/${agent.id || "{agent_id}"}` : ""}
                  </span>
                </div>
                <div className="builtin-toggle-row">
                  <label className="config-toggle field-toggle" title="当前 deepagents 版本默认内置 write_todos">
                    <input type="checkbox" checked readOnly disabled />
                    <span>Todo List</span>
                  </label>
                  <label className="config-toggle field-toggle">
                    <input
                      type="checkbox"
                      checked={Boolean(agent.deepagent?.filesystem?.enabled)}
                      disabled={readOnly}
                      onChange={(event) => updateDeepAgentFilesystem(index, "enabled", event.target.checked)}
                    />
                    <span>Agent 文件系统</span>
                  </label>
                </div>
              </div>
              <div className="config-grid two">
                <ConfigField label="Runtime Name">
                  <input
                    value={agent.deepagent?.name || ""}
                    readOnly={readOnly}
                    onChange={(event) => updateDeepAgent(index, "name", event.target.value)}
                  />
                </ConfigField>
                <ConfigField label="Max Iterations">
                  <input
                    type="number"
                    min="1"
                    max="1000"
                    value={agent.deepagent?.max_iterations ?? 60}
                    readOnly={readOnly}
                    onChange={(event) => updateDeepAgent(index, "max_iterations", Number(event.target.value) || 60)}
                  />
                </ConfigField>
                <ConfigField label="Interrupt On">
                  <input
                    value={(agent.deepagent?.interrupt_on || []).join(", ")}
                    readOnly={readOnly}
                    onChange={(event) => updateDeepAgent(index, "interrupt_on", splitList(event.target.value))}
                  />
                </ConfigField>
                <ConfigField label="Filesystem Root">
                  <input value={`workspace/agents/${agent.id || "{agent_id}"}`} readOnly />
                </ConfigField>
                <ConfigField label="Response Format">
                  <input
                    value={agent.deepagent?.response_format || ""}
                    readOnly={readOnly}
                    onChange={(event) => updateDeepAgent(index, "response_format", event.target.value)}
                  />
                </ConfigField>
                <ConfigField label="Context Schema">
                  <input
                    value={agent.deepagent?.context_schema || ""}
                    readOnly={readOnly}
                    onChange={(event) => updateDeepAgent(index, "context_schema", event.target.value)}
                  />
                </ConfigField>
                <ConfigField label="Memory Store">
                  <input
                    value={
                      agent.deepagent?.use_longterm_memory === false
                        ? "disabled"
                        : `workspace/agents/${agent.id || "{agent_id}"}/memory/store.json`
                    }
                    readOnly={readOnly}
                    disabled
                  />
                </ConfigField>
                <ConfigField label="Cache">
                  <input
                    value={agent.deepagent?.cache || ""}
                    readOnly={readOnly}
                    onChange={(event) => updateDeepAgent(index, "cache", event.target.value)}
                  />
                </ConfigField>
                <label className="config-toggle field-toggle">
                  <input
                    type="checkbox"
                    checked={Boolean(agent.deepagent?.debug)}
                    disabled={readOnly}
                    onChange={(event) => updateDeepAgent(index, "debug", event.target.checked)}
                  />
                  <span>Debug</span>
                </label>
                <label className="config-toggle field-toggle">
                  <input
                    type="checkbox"
                    checked={Boolean(agent.deepagent?.use_longterm_memory)}
                    disabled={readOnly}
                    onChange={(event) => updateDeepAgent(index, "use_longterm_memory", event.target.checked)}
                  />
                  <span>Long-term Memory</span>
                </label>
                <label className="config-toggle field-toggle">
                  <input
                    type="checkbox"
                    checked={Boolean(agent.deepagent?.checkpointer)}
                    disabled={readOnly}
                    onChange={(event) => updateDeepAgent(index, "checkpointer", event.target.checked)}
                  />
                  <span>Checkpointer</span>
                </label>
              </div>
              <ConfigField label="Subagents JSON">
                <textarea
                  value={JSON.stringify(agent.deepagent?.subagents || [], null, 2)}
                  readOnly={readOnly}
                  onChange={(event) => updateDeepAgent(index, "subagents", parseJsonList(event.target.value))}
                />
              </ConfigField>
            </section>
          </div>
        ))}
      </ConfigList>
      {toolDialogAgent ? (
        <ToolPickerDialog
          agent={toolDialogAgent}
          agentIndex={toolDialogAgentIndex}
          readOnly={readOnly}
          onClose={() => setToolDialogAgentIndex(null)}
          onToggle={toggleTool}
        />
      ) : null}
    </div>
  );
}

function ToolPickerDialog({ agent, agentIndex, readOnly, onClose, onToggle }) {
  const selectedTools = normalizeList(agent.deepagent?.tools);
  return (
    <div className="dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="tool-dialog" role="dialog" aria-modal="true" aria-labelledby="tool-dialog-title">
        <div className="dialog-header">
          <div>
            <strong id="tool-dialog-title">Agent 工具授权</strong>
            <span>{agent.id || "未命名 Agent"} · agents.definitions[].deepagent.tools</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭工具弹窗">
            <X size={15} />
          </button>
        </div>
        <div className="tool-card-grid dialog-tool-grid">
          {AGENT_TOOL_CARDS.map((tool) => {
            const checked = selectedTools.includes(tool.id);
            return (
              <label className={`tool-choice ${checked ? "selected" : ""}`} key={tool.id}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={readOnly}
                  onChange={(event) => onToggle(agentIndex, tool.id, event.target.checked)}
                  aria-label={tool.name}
                />
                <span>
                  <strong>{tool.name}</strong>
                  <small>{tool.summary}</small>
                </span>
                <em>{tool.badge}</em>
              </label>
            );
          })}
        </div>
        <div className="dialog-footer">
          <span>已选择：{formatSelectedTools(selectedTools)}</span>
          <button type="button" className="primary" onClick={onClose}>
            完成
          </button>
        </div>
      </section>
    </div>
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
  const config = mergeObjects(cloneConfig(DEFAULT_CONFIG), isPlainObject(value) ? value : {});
  config.llm.models = Array.isArray(config.llm.models) ? config.llm.models.map((model) => ({ ...model })) : [];
  config.agents.definitions = Array.isArray(config.agents.definitions)
    ? config.agents.definitions.map((agent) => ({
        ...agent,
        context_ids: normalizeList(agent.context_ids),
        deepagent: normalizeDeepAgent(agent.deepagent),
      }))
    : [];
  config.channels.wechat_personal.accounts = Array.isArray(config.channels.wechat_personal.accounts)
    ? config.channels.wechat_personal.accounts.map((account) => ({ ...account }))
    : [];
  config.context.webdav_sync.extensions = normalizeList(config.context.webdav_sync.extensions);
  config.context.webdav_roots = Array.isArray(config.context.webdav_roots)
    ? config.context.webdav_roots.map((root) => ({
        ...root,
        readable: root.readable !== false,
        writable: Boolean(root.writable) && !Boolean(root.protected),
        protected: Boolean(root.protected),
      }))
    : [];
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

function normalizeDeepAgent(value) {
  const defaults = cloneConfig(DEFAULT_CONFIG.agents.definitions[0].deepagent);
  const next = mergeObjects(defaults, isPlainObject(value) ? value : {});
  next.tools = normalizeList(next.tools);
  next.interrupt_on = normalizeList(next.interrupt_on);
  next.middleware = normalizeList(next.middleware);
  next.todo_list = true;
  next.use_longterm_memory = next.use_longterm_memory !== false;
  next.filesystem = isPlainObject(next.filesystem)
    ? {
        enabled: Boolean(next.filesystem.enabled),
        root: "agent",
        mode: "read_write",
      }
    : cloneConfig(DEFAULT_CONFIG.agents.definitions[0].deepagent.filesystem);
  next.subagents = Array.isArray(next.subagents) ? next.subagents.filter(isPlainObject) : [];
  return next;
}

function splitList(value) {
  return String(value)
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseJsonList(value) {
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter(isPlainObject) : [];
  } catch {
    return [];
  }
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
