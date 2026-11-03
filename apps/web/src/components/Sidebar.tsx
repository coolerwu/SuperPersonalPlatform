import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { Agent, Session, SessionSearchHit } from "../types";
import { formatTime } from "../format";
import { Select } from "./Select";

type SidebarProps = {
  open: boolean;
  onClose: () => void;
  workspace: "agent" | "features";
  onWorkspaceChange: (workspace: "agent" | "features") => void;
  agents: Agent[];
  agentId: string;
  onAgentChange: (id: string) => void;
  sessions: Session[];
  sessionId: string;
  onSelectSession: (id: string) => void;
  onSelectSearchHit: (hit: SessionSearchHit) => void;
  onNewSession: () => void;
  newSessionActive: boolean;
  onDeleteSession: (id: string) => void;
  onOpenSettings: () => void;
  onOpenMemory: () => void;
  onOpenSkills: () => void;
  onOpenTools: () => void;
  onOpenChannels: () => void;
  onOpenCron: () => void;
  onOpenBacktest: () => void;
  settingsActive: boolean;
  memoryActive: boolean;
  skillsActive: boolean;
  toolsActive: boolean;
  channelsActive: boolean;
  cronActive: boolean;
  backtestActive: boolean;
  onLogout: () => void;
};

export function Sidebar(props: SidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SessionSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const searchActive = searchQuery.trim().length > 0;

  useEffect(() => {
    setSearchQuery("");
    setSearchResults([]);
    setSearchError("");
    setSearching(false);
  }, [props.agentId]);

  useEffect(() => {
    const value = searchQuery.trim();
    if (value.length < 2 || !props.agentId) {
      setSearchResults([]);
      setSearchError("");
      setSearching(false);
      return;
    }
    let active = true;
    setSearching(true);
    setSearchError("");
    const timer = window.setTimeout(() => {
      api
        .searchMessages(props.agentId, value, 20)
        .then((hits) => {
          if (active) setSearchResults(hits);
        })
        .catch((exc) => {
          if (active) {
            setSearchResults([]);
            setSearchError(errorText(exc));
          }
        })
        .finally(() => {
          if (active) setSearching(false);
        });
    }, 300);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [searchQuery, props.agentId]);

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
              功能
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

        {props.workspace === "agent" ? (
          <>
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
          <div className="session-search">
            <input
              type="text"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="搜索历史消息"
              aria-label="搜索历史消息"
              autoComplete="off"
            />
            {searchQuery && (
              <button
                type="button"
                className="icon-button subtle session-search-clear"
                onClick={() => setSearchQuery("")}
                aria-label="清空搜索"
              >
                ×
              </button>
            )}
          </div>
          {searchActive ? (
            <div className="session-search-results">
              {searchQuery.trim().length < 2 && (
                <div className="empty-note">至少输入 2 个字符</div>
              )}
              {searching && <div className="empty-note">搜索中…</div>}
              {searchError && <div className="error-banner">{searchError}</div>}
              {!searching &&
                !searchError &&
                searchQuery.trim().length >= 2 &&
                searchResults.length === 0 && (
                  <div className="empty-note">没有匹配的历史消息</div>
                )}
              {searchResults.map((hit) => (
                <button
                  type="button"
                  className="search-result"
                  key={hit.message_id}
                  onClick={() => {
                    props.onSelectSearchHit(hit);
                    props.onClose();
                  }}
                >
                  <div className="search-result-title">
                    {hit.session_title || "新会话"}
                  </div>
                  <div className="search-result-snippet">{hit.snippet}</div>
                  <div className="search-result-meta">
                    {hit.role === "user" ? "你" : "Agent"} · {formatTime(hit.created_at)}
                  </div>
                </button>
              ))}
            </div>
          ) : (
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
                      {session.origin === "cron" && (
                        <span className="session-channel cron" title="定时任务会话">
                          定时
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
          )}
        </div>

        <div className="sidebar-group">
            <div className="sidebar-group-head">
              <span className="sidebar-group-label">Agent 能力</span>
            </div>
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
          </div>

          <div className="sidebar-group sidebar-group-platform">
            <div className="sidebar-group-head">
              <span className="sidebar-group-label">平台</span>
            </div>
            <div className="sidebar-platform-actions">
              <button
                className={`sidebar-action ${props.settingsActive ? "active" : ""}`}
                onClick={props.onOpenSettings}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                    <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
                    <circle cx="16" cy="7" r="2" />
                    <circle cx="8" cy="17" r="2" />
                  </svg>
                </span>
                <span>配置</span>
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
            </div>
        </div>
          </>
        ) : (
          <>
            <div className="sidebar-group sidebar-features">
              <div className="sidebar-group-head">
                <span className="sidebar-group-label">独立工具</span>
              </div>
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
                <span className="sidebar-badge">不绑定 Agent</span>
              </button>
              <p className="sidebar-features-hint">手动操作 · 不绑定 Agent</p>
            </div>

            <div className="sidebar-group sidebar-group-platform">
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
            </div>
          </>
        )}
      </aside>
    </>
  );
}
