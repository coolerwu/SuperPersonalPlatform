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
  newSessionActive: boolean;
  onDeleteSession: (id: string) => void;
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

      <div className="sidebar-actions">
        <button
          className={`sidebar-action ${props.newSessionActive ? "active" : ""}`}
          onClick={props.onNewSession}
        >
          <span className="sidebar-action-icon">+</span>
          <span>新对话</span>
        </button>
        <button
          className={`sidebar-action ${props.settingsActive ? "active" : ""}`}
          onClick={props.onOpenSettings}
        >
          <span className="sidebar-action-icon">
            <svg
              viewBox="0 0 24 24"
              width="15"
              height="15"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
              <circle cx="16" cy="7" r="2" />
              <circle cx="8" cy="17" r="2" />
            </svg>
          </span>
          <span>配置</span>
        </button>
      </div>

      <div className="sidebar-section">
        <div className="agent-row">
          <span className="agent-row-icon" aria-hidden="true">
            <svg
              viewBox="0 0 24 24"
              width="17"
              height="17"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="9" cy="8" r="3" />
              <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
              <path d="M16 4.5a3 3 0 0 1 0 5.5" />
              <path d="M18 14.5c2.3.5 4 2.5 4 5.5" />
            </svg>
          </span>
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
        </div>
      </div>

      <div className="sidebar-section sessions-section">
        <div className="section-heading">
          <span>会话</span>
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
        <button className="sidebar-action" onClick={props.onLogout}>
          <span className="sidebar-action-icon">
            <svg
              viewBox="0 0 24 24"
              width="15"
              height="15"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <path d="m16 17 5-5-5-5" />
              <path d="M21 12H9" />
            </svg>
          </span>
          <span>退出登录</span>
        </button>
      </div>
    </aside>
  );
}
