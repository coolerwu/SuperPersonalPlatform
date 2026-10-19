import { FormEvent, useEffect, useState } from "react";

import type { Provider } from "../types";
import { Select } from "./Select";

type NewAgentDialogProps = {
  open: boolean;
  providers: Provider[];
  busy: boolean;
  onClose: () => void;
  onCreate: (payload: { name: string; instructions: string; provider_id: string }) => Promise<void>;
};

export function NewAgentDialog({ open, providers, busy, onClose, onCreate }: NewAgentDialogProps) {
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [providerId, setProviderId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setName("");
    setInstructions("");
    setProviderId(providers[0]?.id ?? "");
    setError("");
  }, [open, providers]);

  if (!open) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setError("");
    try {
      await onCreate({ name: name.trim(), instructions, provider_id: providerId });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal" onClick={(event) => event.stopPropagation()} onSubmit={submit}>
        <div className="modal-heading">
          <h2>新建 Agent</h2>
          <button type="button" className="icon-button" onClick={onClose}>×</button>
        </div>
        <label className="field-label" htmlFor="new-agent-name">名称</label>
        <input id="new-agent-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} />
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
        <label className="field-label" htmlFor="new-agent-instructions">Instructions</label>
        <textarea
          id="new-agent-instructions"
          rows={6}
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
          placeholder="你是一个……"
        />
        {error && <div className="error-banner">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onClose}>取消</button>
          <button className="primary" type="submit" disabled={busy || !name.trim()}>
            {busy ? "创建中…" : "创建"}
          </button>
        </div>
      </form>
    </div>
  );
}
