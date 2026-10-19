import type { Agent, Session } from "../types";
import { formatTime } from "../format";
import { Select } from "./Select";

type SidebarProps = {
  agents: Agent[];
  agentId: string;
  onAgentChange: (id: string) => void;
  sessions: Session[];
  sessionId: string;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  onNewAgent: () => void;
  onOpenSettings: () => void;
  settingsActive: boolean;
  onLogout: () => void;
};

export function Sidebar(props: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">ACC</div>
        <div>
          <div className="brand-title">Agent Command</div>
          <div className="brand-subtitle">web chat · v1</div>
        </div>
      </div>

      <div className="sidebar-section">
        <div className="field-label">Agent</div>
        <div className="agent-row">
          <Select
            value={props.agentId}
            options={props.agents.map((agent) => ({
              value: agent.id,
              label: agent.name,
              description: agent.model,
            }))}
            onChange={props.onAgentChange}
            ariaLabel="Agent"
          />
          <button className="icon-button" title="新建 Agent" onClick={props.onNewAgent}>+</button>
        </div>
      </div>

      <div className="sidebar-section sessions-section">
        <div className="section-heading">
          <span>会话</span>
          <button className="icon-button" title="新建会话" onClick={props.onNewSession}>+</button>
        </div>
        <div className="session-list">
          {props.sessions.length === 0 && <div className="empty-note">还没有会话</div>}
          {props.sessions.map((session) => (
            <div
              key={session.id}
              className={`session-item ${session.id === props.sessionId ? "selected" : ""}`}
              onClick={() => props.onSelectSession(session.id)}
            >
              <div className="session-main">
                <div className="session-title">{session.title || "新会话"}</div>
                <div className="session-meta">
                  {session.message_count} 条 · {formatTime(session.updated_at)}
                </div>
              </div>
              <button
                className="icon-button subtle"
                title="删除会话"
                onClick={(event) => {
                  event.stopPropagation();
                  props.onDeleteSession(session.id);
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="sidebar-footer">
        <button
          className={`ghost-button ${props.settingsActive ? "active" : ""}`}
          onClick={props.onOpenSettings}
        >
          配置
        </button>
        <button className="ghost-button" onClick={props.onLogout}>退出登录</button>
      </div>
    </aside>
  );
}
