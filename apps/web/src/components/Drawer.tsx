import { useEffect, useState } from "react";

import type { Agent, DrawerTab, Provider, Run, RunEvent } from "../types";
import { formatTime, previewPayload, truncate } from "../format";
import { AgentEditor } from "./AgentEditor";
import { ProviderSettings } from "./ProviderSettings";
import { Select } from "./Select";

type DrawerProps = {
  tab: DrawerTab;
  onTab: (tab: DrawerTab) => void;
  run: Run | null;
  events: RunEvent[];
  agent: Agent | undefined;
  providers: Provider[];
  onSaveAgent: (payload: { name: string; instructions: string; provider_id: string }) => Promise<void>;
  onSaveProvider: (providerId: string, payload: { name: string; base_url: string; model: string; api_key?: string }) => Promise<void>;
  onClose: () => void;
};

function eventText(event: RunEvent): string {
  const payload = event.payload as Record<string, unknown>;
  switch (event.type) {
    case "assistant_delta":
      return String(payload.delta ?? "");
    case "subagent_response":
      return `${String(payload.agent ?? "subagent")}: ${String(payload.content ?? "")}`;
    case "agent_update": {
      const tools = Array.isArray(payload.tool_calls) ? payload.tool_calls.join(", ") : "";
      return tools ? `调用工具: ${tools}` : `agent: ${String(payload.agent ?? "")}`;
    }
    case "model_usage":
      return `usage: ${String(payload.input_tokens ?? 0)} in / ${String(payload.output_tokens ?? 0)} out`;
    case "run_queued":
      return "run queued";
    case "run_completed":
      return `completed: ${truncate(String(payload.output ?? ""), 180)}`;
    case "run_failed": {
      const error = payload.error as Record<string, unknown> | undefined;
      return `failed: ${String(error?.message ?? payload.message ?? "run failed")}`;
    }
    case "run_cancelled":
      return "run cancelled";
    default:
      return previewPayload(payload);
  }
}

export function Drawer(props: DrawerProps) {
  const [savingAgent, setSavingAgent] = useState(false);
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerId, setProviderId] = useState("");

  useEffect(() => {
    if (!props.providers.some((provider) => provider.id === providerId)) {
      setProviderId(props.providers[0]?.id ?? "");
    }
  }, [props.providers, providerId]);

  const provider = props.providers.find((item) => item.id === providerId) ?? props.providers[0];

  async function saveAgent(payload: { name: string; instructions: string; provider_id: string }) {
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

  return (
    <aside className="drawer">
      <div className="drawer-header">
        <div className="drawer-tabs">
          <button className={props.tab === "run" ? "active" : ""} onClick={() => props.onTab("run")}>Run</button>
          <button className={props.tab === "agent" ? "active" : ""} onClick={() => props.onTab("agent")}>Agent</button>
          <button className={props.tab === "settings" ? "active" : ""} onClick={() => props.onTab("settings")}>Settings</button>
        </div>
        <button className="icon-button" title="关闭详情" onClick={props.onClose}>×</button>
      </div>

      <div className="drawer-body">
        {props.tab === "run" && (
          <div className="run-view">
            {!props.run && (
              <div className="empty-note">还没有 Run。发一条消息后，这里会显示状态、事件和用量。</div>
            )}
            {props.run && (
              <>
                <div className="run-summary">
                  <div>
                    <div className="eyebrow">RUN</div>
                    <div className="run-id">{props.run.id}</div>
                  </div>
                  <span className={`status-pill ${props.run.status}`}>{props.run.status}</span>
                </div>
                <div className="metric-grid">
                  <div><span>Input</span><strong>{props.run.input_tokens}</strong></div>
                  <div><span>Output</span><strong>{props.run.output_tokens}</strong></div>
                  <div><span>Total</span><strong>{props.run.total_tokens}</strong></div>
                  <div><span>Cost</span><strong>{props.run.cost == null ? "—" : props.run.cost.toFixed(6)}</strong></div>
                </div>
                {props.run.error && <div className="error-banner">{props.run.error}</div>}
                <div className="run-times">
                  <span>created {formatTime(props.run.created_at)}</span>
                  <span>finished {formatTime(props.run.finished_at)}</span>
                </div>
                <div className="eyebrow events-heading">EVENTS</div>
                <div className="event-list">
                  {props.events.map((event) => (
                    <div key={event.id} className={`event-row ${event.type}`}>
                      <div className="event-top">
                        <span className="event-type">{event.type}</span>
                        <span className="event-time">{formatTime(event.created_at)}</span>
                      </div>
                      <div className="event-text">{eventText(event)}</div>
                    </div>
                  ))}
                  {props.events.length === 0 && <div className="empty-note">暂无事件。</div>}
                </div>
              </>
            )}
          </div>
        )}

        {props.tab === "agent" && (
          <AgentEditor
            agent={props.agent}
            providers={props.providers}
            saving={savingAgent}
            onSave={saveAgent}
          />
        )}

        {props.tab === "settings" && (
          <div className="settings-view">
            <div className="section-heading">
              <span>Provider</span>
              <Select
                className="compact-select"
                value={provider?.id ?? ""}
                options={props.providers.map((item) => ({
                  value: item.id,
                  label: item.name,
                  description: item.model,
                }))}
                onChange={setProviderId}
                ariaLabel="Provider"
              />
            </div>
            <ProviderSettings
              provider={provider}
              saving={savingProvider}
              onSave={saveProvider}
            />
          </div>
        )}
      </div>
    </aside>
  );
}
