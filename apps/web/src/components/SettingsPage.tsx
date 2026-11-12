import { FormEvent, useEffect, useRef, useState } from "react";

import { api, errorText } from "../api";
import type { Agent, AgentSavePayload, Provider, SystemInfo } from "../types";
import { AgentEditor } from "./AgentEditor";
import { DeleteAgentDialog } from "./DeleteAgentDialog";
import { KnowledgeSettings } from "./KnowledgeSettings";
import { ProviderCreateForm } from "./ProviderCreateForm";
import { ProviderSettings } from "./ProviderSettings";

type SettingsPageProps = {
  agents: Agent[];
  agentId: string;
  providers: Provider[];
  onSaveAgent: (payload: AgentSavePayload) => Promise<void>;
  onSaveProvider: (
    providerId: string,
    payload: { name: string; base_url: string; model: string; api_key?: string },
  ) => Promise<void>;
  onCreateProvider: (payload: {
    name: string;
    base_url: string;
    model: string;
    api_key?: string;
  }) => Promise<Provider>;
  onSelectAgent: (id: string) => void;
  onNewAgent: () => void;
  onDeleteAgent: (id: string) => Promise<{ deleted: boolean; sessions: number; runs: number }>;
  onClose: () => void;
};

const CAPABILITY_LABELS: Record<string, string> = {
  chat: "Chat",
  multi_agent: "多 Agent",
  subagent: "子 Agent",
  checkpoints: "Checkpoint",
  filesystem: "文件系统",
  shell: "Shell",
  browser: "浏览器工具（未接入）",
  skills: "Skills",
  plugins: "插件集市",
  memory: "Memory",
};

// Mirrors TASK_BUDGET_MIN/MAX in apps/api/app/services/settings_store.py.
function clampTaskBudget(value: number): number {
  const rounded = Math.max(0, Math.min(1_000_000, Math.round(value || 0)));
  return rounded > 0 && rounded < 20000 ? 20000 : rounded;
}

export function SettingsPage(props: SettingsPageProps) {
  const [tab, setTab] = useState<"provider" | "agent" | "knowledge" | "runtime">("provider");
  const [providerId, setProviderId] = useState("");
  const [providerEditing, setProviderEditing] = useState(false);
  const [agentEditing, setAgentEditing] = useState(false);
  const [creatingProvider, setCreatingProvider] = useState(false);
  const [savingProvider, setSavingProvider] = useState(false);
  const [savingAgent, setSavingAgent] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Agent | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [systemError, setSystemError] = useState("");
  const [retentionDays, setRetentionDays] = useState(30);
  const [taskBudgetTokens, setTaskBudgetTokens] = useState(0);
  const [savingRuntime, setSavingRuntime] = useState(false);
  const [runtimeNotice, setRuntimeNotice] = useState("");
  const [runtimeError, setRuntimeError] = useState("");

  const provider = props.providers.find((item) => item.id === providerId) ?? props.providers[0];
  const agent = props.agents.find((item) => item.id === props.agentId);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [tab]);

  useEffect(() => {
    if (!props.providers.some((item) => item.id === providerId)) {
      setProviderId(props.providers[0]?.id ?? "");
    }
  }, [props.providers, providerId]);

  useEffect(() => {
    if (agent?.provider_id) {
      setProviderId(agent.provider_id);
    }
  }, [agent?.id, agent?.provider_id]);

  useEffect(() => {
    let active = true;
    setSystemError("");
    void api
      .getSystem()
      .then((info) => {
        if (active) setSystem(info);
      })
      .catch((exc) => {
        if (active) setSystemError(errorText(exc));
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    void api
      .getSettings()
      .then((next) => {
        if (!active) return;
        setRetentionDays(next.session_retention_days);
        setTaskBudgetTokens(next.task_budget_tokens);
      })
      .catch((exc) => {
        if (active) setRuntimeError(errorText(exc));
      });
    return () => {
      active = false;
    };
  }, []);

  async function saveRuntimeSettings(event: FormEvent) {
    event.preventDefault();
    if (savingRuntime) return;
    setSavingRuntime(true);
    setRuntimeError("");
    setRuntimeNotice("");
    try {
      const next = await api.updateSettings({
        session_retention_days: Math.max(
          0,
          Math.min(3650, Math.round(retentionDays || 0)),
        ),
        task_budget_tokens: clampTaskBudget(taskBudgetTokens),
      });
      setRetentionDays(next.session_retention_days);
      setTaskBudgetTokens(next.task_budget_tokens);
      setRuntimeNotice("已保存运行环境设置。");
    } catch (exc) {
      setRuntimeError(errorText(exc));
    } finally {
      setSavingRuntime(false);
    }
  }

  async function saveAgent(payload: AgentSavePayload) {
    setSavingAgent(true);
    try {
      await props.onSaveAgent(payload);
    } finally {
      setSavingAgent(false);
    }
  }

  async function saveProvider(payload: { name: string; base_url: string; model: string; api_key?: string }) {
    if (!provider) return;
    setSavingProvider(true);
    try {
      await props.onSaveProvider(provider.id, payload);
    } finally {
      setSavingProvider(false);
    }
  }

  async function createProvider(payload: {
    name: string;
    base_url: string;
    model: string;
    api_key?: string;
  }) {
    setCreating(true);
    try {
      const created = await props.onCreateProvider(payload);
      setProviderId(created.id);
      setCreatingProvider(false);
      setProviderEditing(true);
    } finally {
      setCreating(false);
    }
  }

  return (
    <main className="settings-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">CONFIGURATION</div>
          <h1>配置</h1>
          <p>Provider、Agent、知识库和运行环境都集中在这里。</p>
        </div>
        <button className="ghost-button" onClick={props.onClose}>返回对话</button>
      </header>

      <div className="settings-scroll" ref={scrollRef}>
        <div className="settings-lane">
          <div className="settings-tabs">
            <button
              type="button"
              className={tab === "provider" ? "active" : ""}
              onClick={() => setTab("provider")}
            >
              Provider
            </button>
            <button
              type="button"
              className={tab === "agent" ? "active" : ""}
              onClick={() => setTab("agent")}
            >
              Agent
            </button>
            <button
              type="button"
              className={tab === "knowledge" ? "active" : ""}
              onClick={() => setTab("knowledge")}
            >
              知识库
            </button>
            <button
              type="button"
              className={tab === "runtime" ? "active" : ""}
              onClick={() => setTab("runtime")}
            >
              运行环境
            </button>
          </div>

          {tab === "provider" && (
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>Provider</h2>
                <p>OpenAI-compatible 端点；密钥只保存在后端，不会回传前端。</p>
              </div>
              <div className="settings-card-actions">
                <button
                  className="ghost-button"
                  disabled={!provider}
                  onClick={() => {
                    setProviderEditing((current) => !current);
                    setCreatingProvider(false);
                  }}
                >
                  {providerEditing ? "收起" : "编辑"}
                </button>
                <button
                  className="ghost-button"
                  onClick={() => {
                    setCreatingProvider((current) => !current);
                    setProviderEditing(false);
                  }}
                >
                  {creatingProvider ? "收起" : "新增 Provider"}
                </button>
              </div>
            </div>

            <div className="settings-provider-list">
              {props.providers.length === 0 && <div className="empty-note">还没有 Provider。</div>}
              {props.providers.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`settings-provider-row ${item.id === provider?.id ? "selected" : ""}`}
                  onClick={() => {
                    setProviderId(item.id);
                    setProviderEditing(true);
                    setCreatingProvider(false);
                  }}
                >
                  <span className="settings-provider-name">{item.name}</span>
                  <span className="settings-provider-meta">{item.model} · {item.base_url}</span>
                  <span className={`key-badge ${item.has_api_key ? "ok" : "missing"}`}>
                    {item.has_api_key ? "密钥已配置" : "缺少密钥"}
                  </span>
                </button>
              ))}
            </div>

            {creatingProvider && (
              <ProviderCreateForm
                busy={creating}
                onCreate={createProvider}
                onCancel={() => setCreatingProvider(false)}
              />
            )}

            {providerEditing && provider && (
              <>
                <div className="settings-subhead">编辑 {provider.name}</div>
                <ProviderSettings provider={provider} saving={savingProvider} onSave={saveProvider} />
              </>
            )}
          </section>
          )}

          {tab === "agent" && (
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>Agent</h2>
                <p>每个 Agent 有自己的 instructions、Provider 和独立会话。</p>
              </div>
              <div className="settings-card-actions">
                <button
                  className="ghost-button"
                  disabled={!agent}
                  onClick={() => setAgentEditing((current) => !current)}
                >
                  {agentEditing ? "收起" : "编辑"}
                </button>
                <button className="ghost-button" onClick={props.onNewAgent}>新建 Agent</button>
              </div>
            </div>

            <div className="settings-agent-list">
              {props.agents.length === 0 && <div className="empty-note">还没有 Agent。</div>}
              {props.agents.map((item) => {
                const selected = item.id === props.agentId;
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`settings-agent-row ${selected ? "selected" : ""}`}
                    aria-pressed={selected}
                    title={selected ? "当前 Agent" : `切换到 ${item.name}`}
                    onClick={() => {
                      if (!selected) props.onSelectAgent(item.id);
                    }}
                  >
                    <span className="settings-provider-name">{item.name}</span>
                    <span className="settings-provider-meta">
                      {item.provider_name} · {item.model}
                    </span>
                    <span className="agent-row-badges">
                      <span className="agent-status">记忆 {item.auto_memory ? "开" : "关"}</span>
                      <span className="agent-status">
                        {item.status === "archived"
                          ? "已归档"
                          : item.status === "paused"
                            ? "已暂停"
                            : item.status}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="hint">
              点击列表切换当前 Agent（与侧边栏选择器同步）；下方只编辑当前 Agent，切换时未保存的修改会丢弃。
            </div>

            {agentEditing && agent && (
              <>
                <div className="settings-subhead">编辑 {agent.name}</div>
                <AgentEditor
                  agent={agent}
                  providers={props.providers}
                  saving={savingAgent}
                  workspaceRoot={system?.workspace_root}
                  onSave={saveAgent}
                />
                <div className="danger-zone">
                  <div>
                    <strong>彻底删除这个 Agent</strong>
                    <p>删除它的全部会话 transcript、工作区、渠道和定时任务，无法恢复。</p>
                  </div>
                  <button
                    type="button"
                    className="danger-button"
                    onClick={() => setDeleteTarget(agent)}
                  >
                    彻底删除
                  </button>
                </div>
              </>
            )}
          </section>
          )}

          {tab === "knowledge" && (
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>知识库</h2>
                <p>坚果云 WebDAV 镜像；Agent 用文件工具直接读写知识库目录。</p>
              </div>
            </div>
            <KnowledgeSettings />
          </section>
          )}

          {tab === "runtime" && (
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>运行环境</h2>
                <p>当前进程的实际配置与能力边界。</p>
              </div>
            </div>

            {systemError && <div className="error-banner">{systemError}</div>}
            {!system && !systemError && <div className="empty-note">读取中…</div>}
            {system && (
              <>
                <div className="system-grid">
                  <div><span>版本</span><strong>{system.version}</strong></div>
                  <div><span>执行核</span><strong>{system.execution_core}</strong></div>
                  <div><span>数据库</span><strong>{system.database}</strong></div>
                  <div><span>单轮执行超时</span><strong>{system.run_timeout_seconds}s</strong></div>
                </div>

                <div className="system-counts">
                  {system.counts.providers} providers · {system.counts.agents} agents ·{" "}
                  {system.counts.sessions} sessions
                </div>

                <div className="system-counts workspace-path-preview">
                  Skills 目录：{system.skills_root}
                </div>

                <div className="capability-list">
                  {Object.entries(system.capabilities).map(([key, enabled]) => (
                    <span key={key} className={`capability ${enabled ? "on" : "off"}`}>
                      {CAPABILITY_LABELS[key] ?? key} · {enabled ? "启用" : "未启用"}
                    </span>
                  ))}
                </div>
              </>
            )}

            <div className="settings-subhead">会话与运行参数</div>
            <form className="editor" onSubmit={saveRuntimeSettings}>
              <p className="hint">
                超过保留天数没有更新的会话由 Claude Code 自己清理；平台只保存这个设置，不单独扫描
                transcript。0 表示不清理。
              </p>
              <label className="field-label" htmlFor="retention-days">保留天数</label>
              <input
                id="retention-days"
                type="number"
                min={0}
                max={3650}
                step={1}
                value={retentionDays}
                onChange={(event) =>
                  setRetentionDays(Number(event.target.value) || 0)
                }
              />
              <label className="field-label" htmlFor="task-budget">
                单轮任务预算（tokens）
              </label>
              <input
                id="task-budget"
                type="number"
                min={0}
                max={1_000_000}
                step={1}
                value={taskBudgetTokens}
                onChange={(event) =>
                  setTaskBudgetTokens(Number(event.target.value) || 0)
                }
              />
              <p className="hint">
                本轮可用的 token 预算；0 表示不设置，非 0 时最小 20000（低于 20000
                会按 20000 保存，上限 1000000）。该预算依赖 Anthropic beta，当前
                Provider 不支持时请保持 0。
              </p>
              {runtimeError && <div className="error-banner">{runtimeError}</div>}
              {runtimeNotice && <div className="hint">{runtimeNotice}</div>}
              <div className="form-actions">
                <button className="primary" type="submit" disabled={savingRuntime}>
                  {savingRuntime ? "保存中…" : "保存运行环境设置"}
                </button>
              </div>
            </form>
          </section>
          )}
        </div>
      </div>

      <DeleteAgentDialog
        agent={deleteTarget}
        busy={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={async (target) => {
          setDeleting(true);
          try {
            await props.onDeleteAgent(target.id);
            setDeleteTarget(null);
          } finally {
            setDeleting(false);
          }
        }}
      />
    </main>
  );
}
