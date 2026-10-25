import type { Agent, Session } from "../types";
import { formatTime } from "../format";
import { Select } from "./Select";

type SidebarProps = {
  open: boolean;
  onClose: () => void;
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
  onOpenMemory: () => void;
  onOpenSkills: () => void;
  onOpenTools: () => void;
  onOpenChannels: () => void;
  settingsActive: boolean;
  memoryActive: boolean;
  skillsActive: boolean;
  toolsActive: boolean;
  channelsActive: boolean;
  onLogout: () => void;
};

export function Sidebar(props: SidebarProps) {
  return (
    <>
      <div
        className={`sidebar-backdrop ${props.open ? "open" : ""}`}
        onClick={props.onClose}
        aria-hidden="true"
      />
      <aside id="app-sidebar" className={`sidebar ${props.open ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">ACC</div>
          <div className="brand-text">
            <div className="brand-title">Agent Command</div>
            <div className="brand-subtitle">web chat · v1</div>
          </div>
          <button
            type="button"
            className="icon-button subtle sidebar-close"
            onClick={props.onClose}
            aria-label="关闭菜单"
            title="关闭菜单"
          >
            ×
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

        <div className="sidebar-actions sidebar-top-nav">
          <button
            className={`sidebar-action ${props.skillsActive ? "active" : ""}`}
            onClick={props.onOpenSkills}
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
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z" />
                <path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16z" />
              </svg>
            </span>
            <span>技能</span>
          </button>
          <button
            className={`sidebar-action ${props.toolsActive ? "active" : ""}`}
            onClick={props.onOpenTools}
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
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2-2 2.6-2.6z" />
              </svg>
            </span>
            <span>工具</span>
          </button>
          <button
            className={`sidebar-action ${props.channelsActive ? "active" : ""}`}
            onClick={props.onOpenChannels}
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
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1 1" />
                <path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1-1" />
              </svg>
            </span>
            <span>渠道</span>
          </button>
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
            className={`sidebar-action ${props.memoryActive ? "active" : ""}`}
            onClick={props.onOpenMemory}
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
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <ellipse cx="12" cy="5" rx="8" ry="3" />
                <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
                <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
              </svg>
            </span>
            <span>记忆</span>
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
                  <div className="session-title">
                    {session.channel_type === "wechat" && (
                      <span className="session-channel" title={session.channel_name ?? "微信"}>
                        微信
                      </span>
                    )}
                    {session.title || "新会话"}
                  </div>
                  <div className="session-meta">
                    {session.active_run_status === "running" && (
                      <span className="session-activity running">运行中</span>
                    )}
                    {session.active_run_status === "queued" && (
                      <span className="session-activity queued">排队</span>
                    )}
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
    </>
  );
}
