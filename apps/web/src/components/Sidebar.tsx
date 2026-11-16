import { useLayoutEffect, useState, type ReactNode } from "react";
import type { Agent, SessionSummary, SettingsTab } from "../types";
import { Select } from "./Select";

/** `SessionSummary.last_modified` is epoch milliseconds. */
function sessionTime(value: number): string {
  if (!value || !Number.isFinite(value)) return "";
  return new Date(value).toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type MenuSection = "trade" | "config";

function SidebarMenu({
  title,
  open,
  onToggle,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div className={`sidebar-menu ${open ? "open" : ""}`}>
      <button
        type="button"
        className="sidebar-group-head sidebar-menu-head"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className="sidebar-group-label">{title}</span>
        <svg
          className="sidebar-menu-chevron"
          viewBox="0 0 24 24"
          width="13"
          height="13"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="m9 6 6 6-6 6" />
        </svg>
      </button>
      {open && <div className="sidebar-menu-body">{children}</div>}
    </div>
  );
}

type SidebarProps = {
  open: boolean;
  onClose: () => void;
  workspace: "agent" | "features";
  onWorkspaceChange: (workspace: "agent" | "features") => void;
  agents: Agent[];
  agentId: string;
  onAgentChange: (id: string) => void;
  sessions: SessionSummary[];
  unread: Record<string, "done" | "failed">;
  sessionId: string;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  newSessionActive: boolean;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string) => void;
  onOpenMemory: () => void;
  onOpenSkills: () => void;
  onOpenTools: () => void;
  onOpenPlugins: () => void;
  onOpenChannels: () => void;
  onOpenCron: () => void;
  onOpenBacktest: () => void;
  onOpenJournal: () => void;
  onOpenChan: () => void;
  onOpenAllocation: () => void;
  settingsActive: boolean;
  settingsTab: SettingsTab;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  memoryActive: boolean;
  skillsActive: boolean;
  toolsActive: boolean;
  pluginsActive: boolean;
  channelsActive: boolean;
  cronActive: boolean;
  backtestActive: boolean;
  journalActive: boolean;
  chanActive: boolean;
  allocationActive: boolean;
  onLogout: () => void;
};

export function Sidebar(props: SidebarProps) {
  const capabilitiesActive =
    props.skillsActive ||
    props.toolsActive ||
    props.pluginsActive ||
    props.channelsActive ||
    props.cronActive ||
    props.memoryActive;
  const activeMenu: MenuSection | null =
    props.backtestActive ||
    props.journalActive ||
    props.allocationActive ||
    props.chanActive
      ? "trade"
      : props.settingsActive
        ? "config"
        : null;
  const [openMenus, setOpenMenus] = useState<ReadonlySet<MenuSection>>(
    () => new Set<MenuSection>(["trade", "config"]),
  );
  const [capabilitiesOpen, setCapabilitiesOpen] = useState(true);
  useLayoutEffect(() => {
    if (!activeMenu) return;
    setOpenMenus((current) =>
      current.has(activeMenu) ? current : new Set(current).add(activeMenu),
    );
  }, [activeMenu]);
  useLayoutEffect(() => {
    if (capabilitiesActive) setCapabilitiesOpen(true);
  }, [capabilitiesActive]);
  const toggleMenu = (menu: MenuSection) =>
    setOpenMenus((current) => {
      const next = new Set(current);
      if (next.has(menu)) {
        next.delete(menu);
      } else {
        next.add(menu);
      }
      return next;
    });

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
          <div className="workspace-tabs" role="tablist" aria-label="工作区">
            <button
              type="button"
              role="tab"
              aria-selected={props.workspace === "agent"}
              className={`workspace-tab ${props.workspace === "agent" ? "active" : ""}`}
              onClick={() => props.onWorkspaceChange("agent")}
            >
              Agent
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={props.workspace === "features"}
              className={`workspace-tab ${props.workspace === "features" ? "active" : ""}`}
              onClick={() => props.onWorkspaceChange("features")}
            >
              管理
            </button>
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

        {props.workspace === "agent" && (
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
                description:
                  agent.status === "archived"
                    ? `${agent.model} · 已归档`
                    : agent.status === "paused"
                      ? `${agent.model} · 已暂停`
                      : agent.model,
              }))}
              onChange={props.onAgentChange}
              ariaLabel="Agent"
            />
          </div>
        </div>
        )}

        {props.workspace === "agent" ? (
          <>
        <div className="sidebar-group sidebar-workspace">
          <div className="sidebar-group-head">
            <span className="sidebar-group-label">Agent 工作台</span>
            <span className="sidebar-group-count">{props.sessions.length}</span>
          </div>
          <button
            className={`sidebar-action new-session ${props.newSessionActive ? "active" : ""}`}
            onClick={props.onNewSession}
          >
            <span className="sidebar-action-icon">+</span>
            <span>新对话</span>
          </button>
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
                    {props.unread[session.id] && (
                      <span
                        className={`session-unread ${props.unread[session.id]}`}
                        role="img"
                        title={
                          props.unread[session.id] === "failed"
                            ? "执行失败"
                            : "已完成"
                        }
                        aria-label={
                          props.unread[session.id] === "failed"
                            ? "执行失败"
                            : "已完成"
                        }
                      />
                    )}
                    {session.channel_type === "wechat" && (
                      <span className="session-channel" title={session.channel_name ?? "微信"}>
                        微信
                      </span>
                    )}
                    {session.title || "新会话"}
                  </div>
                  <div className="session-meta">
                    {session.busy && (
                      <span className="session-activity running">忙碌</span>
                    )}
                    {sessionTime(session.last_modified)}
                  </div>
                </div>
                <button
                  className="icon-button subtle session-rename"
                  title="重命名会话"
                  aria-label="重命名会话"
                  onClick={(event) => {
                    event.stopPropagation();
                    props.onRenameSession(session.id);
                  }}
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="13"
                    height="13"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M12 20h9" />
                    <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                  </svg>
                </button>
                <button
                  className="icon-button subtle"
                  title="删除会话"
                  aria-label="删除会话"
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

        <SidebarMenu
          title="Agent 能力"
          open={capabilitiesOpen}
          onToggle={() => setCapabilitiesOpen((current) => !current)}
        >
              <div className="sidebar-capabilities">
                <button
                  className={`sidebar-action ${props.skillsActive ? "active" : ""}`}
                  onClick={props.onOpenSkills}
                >
                  <span className="sidebar-action-icon">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
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
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2-2 2.6-2.6z" />
                    </svg>
                  </span>
                  <span>工具</span>
                </button>
                <button
                  className={`sidebar-action ${props.pluginsActive ? "active" : ""}`}
                  onClick={props.onOpenPlugins}
                >
                  <span className="sidebar-action-icon">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M9 3v4" />
                      <path d="M15 3v4" />
                      <path d="M6 7h12v4a6 6 0 0 1-12 0V7z" />
                      <path d="M12 17v4" />
                    </svg>
                  </span>
                  <span>插件</span>
                </button>
                <button
                  className={`sidebar-action ${props.channelsActive ? "active" : ""}`}
                  onClick={props.onOpenChannels}
                >
                  <span className="sidebar-action-icon">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1 1" />
                      <path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1-1" />
                    </svg>
                  </span>
                  <span>渠道</span>
                </button>
                <button
                  className={`sidebar-action ${props.cronActive ? "active" : ""}`}
                  onClick={props.onOpenCron}
                >
                  <span className="sidebar-action-icon">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <circle cx="12" cy="12" r="9" />
                      <path d="M12 7v5l3 2" />
                    </svg>
                  </span>
                  <span>定时</span>
                </button>
                <button
                  className={`sidebar-action ${props.memoryActive ? "active" : ""}`}
                  onClick={props.onOpenMemory}
                >
                  <span className="sidebar-action-icon">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <ellipse cx="12" cy="5" rx="8" ry="3" />
                      <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
                      <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
                    </svg>
                  </span>
                  <span>记忆</span>
                </button>
              </div>
        </SidebarMenu>
          </>
        ) : (
          <>
            <SidebarMenu
              title="交易"
              open={openMenus.has("trade")}
              onToggle={() => toggleMenu("trade")}
            >
              <button
                className={`sidebar-action sidebar-standalone ${props.backtestActive ? "active" : ""}`}
                onClick={props.onOpenBacktest}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M4 20h16" />
                    <path d="M7 17V9M12 15V5M17 18v-7" />
                    <rect x="5" y="10" width="4" height="5" rx="1" />
                    <rect x="10" y="6" width="4" height="6" rx="1" />
                    <rect x="15" y="12" width="4" height="4" rx="1" />
                  </svg>
                </span>
                <span>回测</span>
              </button>
              <button
                className={`sidebar-action sidebar-standalone ${props.journalActive ? "active" : ""}`}
                onClick={props.onOpenJournal}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 4h14v16H5z" />
                    <path d="M8 8h8M8 12h8M8 16h5" />
                  </svg>
                </span>
                <span>交易记录</span>
              </button>
              <button
                className={`sidebar-action sidebar-standalone ${props.allocationActive ? "active" : ""}`}
                onClick={props.onOpenAllocation}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 3v9h9" />
                  </svg>
                </span>
                <span>资产配置</span>
              </button>
              <button
                className={`sidebar-action sidebar-standalone ${props.chanActive ? "active" : ""}`}
                onClick={props.onOpenChan}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M4 18 8 9l4 6 4-9 4 12" />
                    <path d="M4 20h16" />
                  </svg>
                </span>
                <span>缠论图</span>
              </button>
            </SidebarMenu>

            <SidebarMenu
              title="Agent全局配置"
              open={openMenus.has("config")}
              onToggle={() => toggleMenu("config")}
            >
              <button
                className={`sidebar-action ${props.settingsActive && props.settingsTab === "provider" ? "active" : ""}`}
                onClick={() => props.onOpenSettingsTab("provider")}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="3" y="4" width="18" height="7" rx="2" />
                    <rect x="3" y="13" width="18" height="7" rx="2" />
                    <path d="M7 7.5h.01M7 16.5h.01" />
                  </svg>
                </span>
                <span>Provider</span>
              </button>
              <button
                className={`sidebar-action ${props.settingsActive && props.settingsTab === "agent" ? "active" : ""}`}
                onClick={() => props.onOpenSettingsTab("agent")}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="12" cy="8" r="3" />
                    <path d="M5 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
                  </svg>
                </span>
                <span>Agent</span>
              </button>
              <button
                className={`sidebar-action ${props.settingsActive && props.settingsTab === "knowledge" ? "active" : ""}`}
                onClick={() => props.onOpenSettingsTab("knowledge")}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v18H6.5A2.5 2.5 0 0 1 4 18.5z" />
                    <path d="M8 3v18" />
                  </svg>
                </span>
                <span>知识库</span>
              </button>
              <button
                className={`sidebar-action ${props.settingsActive && props.settingsTab === "maps" ? "active" : ""}`}
                onClick={() => props.onOpenSettingsTab("maps")}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 21s7-5.5 7-11a7 7 0 1 0-14 0c0 5.5 7 11 7 11z" />
                    <circle cx="12" cy="10" r="2.5" />
                  </svg>
                </span>
                <span>地图</span>
              </button>
              <button
                className={`sidebar-action ${props.settingsActive && props.settingsTab === "runtime" ? "active" : ""}`}
                onClick={() => props.onOpenSettingsTab("runtime")}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m5 7 4 4-4 4" />
                    <path d="M12 17h7" />
                  </svg>
                </span>
                <span>运行环境</span>
              </button>
              <button className="sidebar-action" onClick={props.onLogout}>
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                    <path d="m16 17 5-5-5-5" />
                    <path d="M21 12H9" />
                  </svg>
                </span>
                <span>退出登录</span>
              </button>
            </SidebarMenu>
          </>
        )}
      </aside>
    </>
  );
}
