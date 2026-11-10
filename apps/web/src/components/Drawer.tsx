import { useState } from "react";

import type { Agent, AgentSavePayload, DrawerTab, Provider } from "../types";
import { AgentEditor } from "./AgentEditor";

type DrawerProps = {
  tab: DrawerTab;
  onTab: (tab: DrawerTab) => void;
  activity: string[];
  reasoning: string;
  agent: Agent | undefined;
  providers: Provider[];
  onSaveAgent: (payload: AgentSavePayload) => Promise<void>;
  onClose: () => void;
};

export function Drawer(props: DrawerProps) {
  const [savingAgent, setSavingAgent] = useState(false);

  const hasReasoning = Boolean(props.reasoning.trim());
  const idle = props.activity.length === 0 && !hasReasoning;

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
          <button className={props.tab === "run" ? "active" : ""} onClick={() => props.onTab("run")}>执行过程</button>
          <button className={props.tab === "agent" ? "active" : ""} onClick={() => props.onTab("agent")}>Agent</button>
        </div>
        <button className="icon-button" title="关闭详情" onClick={props.onClose}>×</button>
      </div>

      <div className="drawer-body">
        {props.tab === "run" && (
          <div className="run-view">
            {idle ? (
              <div className="empty-note">
                当前没有执行中的任务。发送消息后，这里会显示实时思考和工具调用。
              </div>
            ) : (
              <>
                <div className="run-summary">
                  <div>
                    <div className="eyebrow">EXECUTION</div>
                    <div className="run-id">实时思考与工具调用</div>
                  </div>
                  <span className="status-pill running">运行中</span>
                </div>
                {hasReasoning && (
                  <>
                    <div className="eyebrow events-heading">REASONING</div>
                    <div className="event-list">
                      <div className="event-row reasoning_delta">
                        <div className="event-text">{props.reasoning}</div>
                      </div>
                    </div>
                  </>
                )}
                <div className="eyebrow events-heading">TOOLS</div>
                <div className="event-list">
                  {props.activity.length === 0 && <div className="empty-note">暂无工具调用。</div>}
                  {props.activity.map((name, index) => (
                    <div key={`${name}-${index}`} className="event-row tool_call">
                      <div className="event-top">
                        <span className="event-type">{name}</span>
                        <span className="event-time">#{index + 1}</span>
                      </div>
                    </div>
                  ))}
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
