import { useState } from "react";

import type { Agent, AgentSavePayload, DrawerTab, Provider, Run, RunEvent } from "../types";
import { formatTime, previewPayload, truncate } from "../format";
import { AgentEditor } from "./AgentEditor";

type DrawerProps = {
  tab: DrawerTab;
  onTab: (tab: DrawerTab) => void;
  run: Run | null;
  events: RunEvent[];
  agent: Agent | undefined;
  providers: Provider[];
  onSaveAgent: (payload: AgentSavePayload) => Promise<void>;
  onClose: () => void;
};

function eventText(event: RunEvent): string {
  const payload = event.payload as Record<string, unknown>;
  switch (event.type) {
    case "assistant_delta":
      return String(payload.delta ?? "");
    case "reasoning_delta":
      return String(payload.delta ?? "");
    case "subagent_response":
      return `${String(payload.agent ?? "subagent")}: ${String(payload.content ?? "")}`;
    case "agent_update": {
      const tools = Array.isArray(payload.tool_calls) ? payload.tool_calls.join(", ") : "";
      return tools ? `调用工具: ${tools}` : `agent: ${String(payload.agent ?? "")}`;
    }
    case "browser_action": {
      const tool = String(payload.tool ?? "browser");
      return `浏览器 ${tool}: ${truncate(String(payload.summary ?? ""), 180)}`;
    }
    case "model_usage":
      return `usage: ${String(payload.input_tokens ?? 0)} in / ${String(payload.output_tokens ?? 0)} out`;
    case "memory_saved": {
      const action = String(payload.action ?? "add");
      const label =
        action === "update"
          ? "更新记忆"
          : action === "delete"
            ? "删除记忆"
            : payload.source === "auto"
              ? "自动记住"
              : "记住了";
      return `${label}：${String(payload.content ?? "")}`;
    }
    case "run_queued":
      return "run queued";
    case "step_limit": {
      const limit = String(payload.limit ?? "");
      return payload.action === "continue"
        ? `达到执行步骤上限（${limit}），已自动续跑`
        : `达到执行步骤上限（${limit}），已生成进展总结`;
    }
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
  const [showRawEvents, setShowRawEvents] = useState(false);

  const deltaCount = props.events.filter(
    (event) => event.type === "assistant_delta" || event.type === "reasoning_delta"
  ).length;
  const visibleEvents = showRawEvents
    ? props.events
    : props.events.filter(
        (event) => event.type !== "assistant_delta" && event.type !== "reasoning_delta"
      );

  async function saveAgent(payload: AgentSavePayload) {
    setSavingAgent(true);
    try {
      await props.onSaveAgent(payload);
    } finally {
      setSavingAgent(false);
    }
  }

  return (
    <aside className="drawer">
      <div className="drawer-header">
        <div className="drawer-tabs">
          <button className={props.tab === "run" ? "active" : ""} onClick={() => props.onTab("run")}>Run</button>
          <button className={props.tab === "agent" ? "active" : ""} onClick={() => props.onTab("agent")}>Agent</button>
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
                {deltaCount > 0 && (
                  <button
                    type="button"
                    className="event-toggle"
                    onClick={() => setShowRawEvents((current) => !current)}
                  >
                    {showRawEvents
                      ? "隐藏原始流式片段"
                      : `已折叠 ${deltaCount} 条流式片段；展开查看原始事件`}
                  </button>
                )}
                <div className="event-list">
                  {visibleEvents.map((event) => (
                    <div key={event.id} className={`event-row ${event.type}`}>
                      <div className="event-top">
                        <span className="event-type">{event.type}</span>
                        <span className="event-time">{formatTime(event.created_at)}</span>
                      </div>
                      <div className="event-text">{eventText(event)}</div>
                    </div>
                  ))}
                  {visibleEvents.length === 0 && <div className="empty-note">暂无事件。</div>}
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
      </div>
    </aside>
  );
}
