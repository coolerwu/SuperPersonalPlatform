import { FormEvent, useEffect, useState } from "react";

import type { Agent } from "../types";

type DeleteAgentDialogProps = {
  agent: Agent | null;
  busy: boolean;
  onClose: () => void;
  onConfirm: (agent: Agent) => Promise<void>;
};

export function DeleteAgentDialog({ agent, busy, onClose, onConfirm }: DeleteAgentDialogProps) {
  const [confirmName, setConfirmName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setConfirmName("");
    setError("");
  }, [agent?.id]);

  if (!agent) return null;

  const target = agent;
  const valid = confirmName.trim() === target.name;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid || busy) return;
    setError("");
    try {
      await onConfirm(target);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal danger-modal" onClick={(event) => event.stopPropagation()} onSubmit={submit}>
        <div className="modal-heading">
          <h2>彻底删除 Agent</h2>
          <button type="button" className="icon-button" onClick={onClose}>×</button>
        </div>
        <p className="danger-copy">
          这会永久删除 <strong>{agent.name}</strong>，以及它的所有会话、消息、Run、事件和
          checkpoint。删除后无法恢复。
        </p>
        <label className="field-label" htmlFor="delete-agent-name">输入 Agent 名称确认</label>
        <input
          id="delete-agent-name"
          autoFocus
          value={confirmName}
          onChange={(event) => setConfirmName(event.target.value)}
          placeholder={agent.name}
        />
        {error && <div className="error-banner">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onClose}>取消</button>
          <button className="danger-button" type="submit" disabled={!valid || busy}>
            {busy ? "删除中…" : "彻底删除"}
          </button>
        </div>
      </form>
    </div>
  );
}
