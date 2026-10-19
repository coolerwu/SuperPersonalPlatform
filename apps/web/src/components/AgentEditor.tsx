import { FormEvent, useEffect, useState } from "react";

import type { Agent, Provider } from "../types";
import { Select } from "./Select";

type AgentEditorProps = {
  agent: Agent | undefined;
  providers: Provider[];
  saving: boolean;
  onSave: (payload: { name: string; instructions: string; provider_id: string }) => Promise<void>;
};

export function AgentEditor({ agent, providers, saving, onSave }: AgentEditorProps) {
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [providerId, setProviderId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setName(agent?.name ?? "");
    setInstructions(agent?.instructions ?? "");
    setProviderId(agent?.provider_id ?? providers[0]?.id ?? "");
    setError("");
  }, [agent?.id, agent?.name, agent?.instructions, agent?.provider_id, providers]);

  if (!agent) {
    return <div className="empty-note">选择一个 Agent 后编辑。</div>;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setError("");
    try {
      await onSave({ name: name.trim(), instructions, provider_id: providerId });
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

      <label className="field-label" htmlFor="agent-instructions">Instructions</label>
      <textarea
        id="agent-instructions"
        rows={12}
        value={instructions}
        onChange={(event) => setInstructions(event.target.value)}
        placeholder="你是一个……"
      />

      {error && <div className="error-banner">{error}</div>}
      <button className="primary" type="submit" disabled={saving || !name.trim()}>
        {saving ? "保存中…" : "保存 Agent"}
      </button>
      <div className="hint">修改在下一轮对话生效；当前 session 的 checkpoint 不会被清空。</div>
    </form>
  );
}
