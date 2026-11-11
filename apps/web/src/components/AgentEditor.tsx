import { FormEvent, useEffect, useState } from "react";

import type { Agent, AgentSavePayload, Provider } from "../types";
import { Select } from "./Select";

type AgentEditorProps = {
  agent: Agent | undefined;
  providers: Provider[];
  saving: boolean;
  workspaceRoot?: string;
  onSave: (payload: AgentSavePayload) => Promise<void>;
};

export function AgentEditor({ agent, providers, saving, workspaceRoot, onSave }: AgentEditorProps) {
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [providerId, setProviderId] = useState("");
  const [autoMemory, setAutoMemory] = useState(true);
  const [status, setStatus] = useState<string>("active");
  const [error, setError] = useState("");

  useEffect(() => {
    setName(agent?.name ?? "");
    setInstructions(agent?.instructions ?? "");
    setProviderId(agent?.provider_id ?? providers[0]?.id ?? "");
    setAutoMemory(agent?.auto_memory ?? true);
    setStatus(agent?.status ?? "active");
    setError("");
  }, [agent?.id, agent?.name, agent?.instructions, agent?.provider_id, agent?.auto_memory, agent?.status, providers]);

  if (!agent) {
    return <div className="empty-note">选择一个 Agent 后编辑。</div>;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setError("");
    try {
      await onSave({
        name: name.trim(),
        instructions,
        provider_id: providerId,
        auto_memory: autoMemory,
        ...(status === "active" || status === "paused" ? { status } : {}),
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <form className="editor" onSubmit={submit}>
      <label className="field-label" htmlFor="agent-name">名称</label>
      <input id="agent-name" value={name} onChange={(event) => setName(event.target.value)} />

      <div className="field-label">Provider</div>
      <Select
        value={providerId}
        options={providers.map((provider) => ({
          value: provider.id,
          label: provider.name,
          description: provider.model,
        }))}
        onChange={setProviderId}
        ariaLabel="Provider"
      />

      <label className="field-label" htmlFor="agent-status">状态</label>
      <select
        id="agent-status"
        value={status}
        onChange={(event) => setStatus(event.target.value)}
      >
        <option value="active">active（正常运行）</option>
        <option value="paused">paused（暂停）</option>
        {status === "archived" && (
          <option value="archived" disabled>archived（已归档，可改为 active 恢复）</option>
        )}
      </select>

      <div className="field-label">工作区</div>
      <div className="hint workspace-path-preview">
        机器目录：{workspaceRoot || "~/.super-personal-platform/workspaces"}/{agent.id}
      </div>

      <label className="field-label" htmlFor="agent-instructions">Instructions</label>
      <textarea
        id="agent-instructions"
        rows={12}
        value={instructions}
        onChange={(event) => setInstructions(event.target.value)}
        placeholder="你是一个……"
      />

      <div className="field-label">技能</div>
      <div className="hint">
        技能由 Claude Code 自动发现、所有 Agent 共享；本版没有技能创作工具，
        需要时由模型按技能说明调用。
      </div>

      <div className="field-label">渠道</div>
      <div className="hint">
        在左侧「渠道」页面为当前 Agent 绑定微信个人号；每个微信联系人对应一个独立会话。
      </div>

      <label className="toggle-row" htmlFor="agent-auto-memory">
        <input
          id="agent-auto-memory"
          type="checkbox"
          checked={autoMemory}
          onChange={(event) => setAutoMemory(event.target.checked)}
        />
        <span>
          <strong>允许 Agent 写入记忆</strong>
          <small>兼容保留字段；当前版本不注入记忆工具，Agent 笔记由 Claude auto memory 管理</small>
        </span>
      </label>

      {error && <div className="error-banner">{error}</div>}
      <button className="primary" type="submit" disabled={saving || !name.trim()}>
        {saving ? "保存中…" : "保存 Agent"}
      </button>
      <div className="hint">修改在下一轮对话生效；空闲会话的缓存客户端会在保存后关闭并在下一次对话重建。</div>
    </form>
  );
}
