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
  const [error, setError] = useState("");

  useEffect(() => {
    setName(agent?.name ?? "");
    setInstructions(agent?.instructions ?? "");
    setProviderId(agent?.provider_id ?? providers[0]?.id ?? "");
    setAutoMemory(agent?.auto_memory ?? true);
    setError("");
  }, [agent?.id, agent?.name, agent?.instructions, agent?.provider_id, agent?.auto_memory, providers]);

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

      <div className="field-label">技能与工具</div>
      <div className="hint">
        在左侧「技能」「工具」页面为当前 Agent 启用；读取 SKILL.md 后，已启用的技能工具才会解锁。
        文件工具始终可用。
      </div>

      <label className="toggle-row" htmlFor="agent-auto-memory">
        <input
          id="agent-auto-memory"
          type="checkbox"
          checked={autoMemory}
          onChange={(event) => setAutoMemory(event.target.checked)}
        />
        <span>
          <strong>自动学习记忆</strong>
          <small>每次 Run 完成后提取 Agent 专属长期记忆</small>
        </span>
      </label>

      {error && <div className="error-banner">{error}</div>}
      <button className="primary" type="submit" disabled={saving || !name.trim()}>
        {saving ? "保存中…" : "保存 Agent"}
      </button>
      <div className="hint">修改在下一轮对话生效；当前 session 的 checkpoint 不会被清空。</div>
    </form>
  );
}
