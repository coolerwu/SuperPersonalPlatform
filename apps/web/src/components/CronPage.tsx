import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { api, errorText } from "../api";
import { formatTime } from "../format";
import type { Agent, Channel, ChannelConversation, CronJob } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";

type CronPageProps = {
  agent: Agent | undefined;
  agentId: string;
  onOpenSession: (sessionId: string) => void;
  onClose: () => void;
};

const RUN_STATUS_TEXT: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const CHANNEL_STATUS_TEXT: Record<string, string> = {
  pairing: "待登录",
  active: "已连接",
  paused: "已暂停",
  expired: "登录过期",
};

function formatInZone(value: string | null, timeZone: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
    return `${get("month")}-${get("day")} ${get("hour")}:${get("minute")}`;
  } catch {
    return formatTime(value);
  }
}

type EditorState = {
  id: string | null;
  name: string;
  prompt: string;
  schedule: string;
  timezone: string;
  enabled: boolean;
  pushEnabled: boolean;
  pushChannelId: string;
  pushConversationId: string;
};

const NEW_EDITOR: EditorState = {
  id: null,
  name: "",
  prompt: "",
  schedule: "0 8 * * *",
  timezone: "Asia/Shanghai",
  enabled: true,
  pushEnabled: false,
  pushChannelId: "",
  pushConversationId: "",
};

export function CronPage({ agent, agentId, onOpenSession, onClose }: CronPageProps) {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [conversations, setConversations] = useState<ChannelConversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState("");
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<CronJob | null>(null);
  const requestRef = useRef(0);
  const conversationRequestRef = useRef(0);
  const agentIdRef = useRef("");

  useEffect(() => {
    agentIdRef.current = agentId;
  }, [agentId]);

  const loadJobs = useCallback(async (id: string, silent = false) => {
    if (!id) {
      setJobs([]);
      return;
    }
    const requestId = ++requestRef.current;
    if (!silent) setLoading(true);
    try {
      const list = await api.listCronJobs(id);
      if (requestId !== requestRef.current || id !== agentIdRef.current) return;
      setJobs(list);
    } catch (exc) {
      if (requestId !== requestRef.current || id !== agentIdRef.current) return;
      // A background refresh failure keeps the current list; an explicit load
      // or a user action is what surfaces the error.
      if (!silent) setError(errorText(exc));
    } finally {
      if (requestId === requestRef.current && id === agentIdRef.current) {
        setLoading(false);
      }
    }
  }, []);

  const loadChannels = useCallback(async (id: string) => {
    if (!id) {
      setChannels([]);
      return;
    }
    try {
      const list = await api.listChannels(id);
      if (id !== agentIdRef.current) return;
      setChannels(list);
    } catch {
      // Jobs still load; the push target picker stays empty.
    }
  }, []);

  const loadConversations = useCallback(async (channelId: string) => {
    const requestId = ++conversationRequestRef.current;
    if (!channelId) {
      setConversations([]);
      setConversationsLoading(false);
      return;
    }
    setConversations([]);
    setConversationsLoading(true);
    try {
      const list = await api.listChannelConversations(channelId);
      if (requestId !== conversationRequestRef.current) return;
      setConversations(list);
    } catch (exc) {
      if (requestId !== conversationRequestRef.current) return;
      setConversations([]);
      setError(errorText(exc));
    } finally {
      if (requestId === conversationRequestRef.current) {
        setConversationsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    setError("");
    setNotice("");
    setEditor(null);
    setDeleteTarget(null);
    setBusyId("");
    setJobs([]);
    setChannels([]);
    setConversations([]);
    setConversationsLoading(false);
    void loadJobs(agentId);
    void loadChannels(agentId);
  }, [agentId, loadJobs, loadChannels]);

  useEffect(() => {
    if (!agentId) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void loadJobs(agentId, true);
    }, 20_000);
    return () => window.clearInterval(timer);
  }, [agentId, loadJobs]);

  function openCreate() {
    setError("");
    setNotice("");
    setConversations([]);
    setEditor({ ...NEW_EDITOR, timezone: jobs[0]?.timezone || NEW_EDITOR.timezone });
  }

  function openEdit(job: CronJob) {
    setError("");
    setNotice("");
    setEditor({
      id: job.id,
      name: job.name,
      prompt: job.prompt,
      schedule: job.schedule,
      timezone: job.timezone,
      enabled: job.enabled,
      pushEnabled: Boolean(job.push_channel_id && job.push_conversation_id),
      pushChannelId: job.push_channel_id ?? "",
      pushConversationId: job.push_conversation_id ?? "",
    });
    if (job.push_channel_id) {
      void loadConversations(job.push_channel_id);
    } else {
      setConversations([]);
    }
  }

  async function saveJob(event: FormEvent) {
    event.preventDefault();
    if (!agentId || !editor || saving) return;
    if (editor.pushEnabled && (!editor.pushChannelId || !editor.pushConversationId)) {
      setError("请选择推送渠道和联系人");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    const push = editor.pushEnabled
      ? {
          push_channel_id: editor.pushChannelId,
          push_conversation_id: editor.pushConversationId,
        }
      : { push_channel_id: "", push_conversation_id: "" };
    try {
      if (editor.id) {
        await api.updateCronJob(editor.id, {
          name: editor.name,
          prompt: editor.prompt,
          schedule: editor.schedule,
          timezone: editor.timezone,
          enabled: editor.enabled,
          ...push,
        });
        setNotice("定时任务已保存");
      } else {
        await api.createCronJob({
          agent_id: agentId,
          name: editor.name,
          prompt: editor.prompt,
          schedule: editor.schedule,
          timezone: editor.timezone,
          enabled: editor.enabled,
          ...push,
        });
        setNotice("定时任务已创建，专属会话已生成");
      }
      setEditor(null);
      await loadJobs(agentId, true);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  async function toggleJob(job: CronJob) {
    if (busyId) return;
    setBusyId(job.id);
    setError("");
    setNotice("");
    try {
      await api.updateCronJob(job.id, { enabled: !job.enabled });
      setNotice(job.enabled ? "任务已暂停" : "任务已启用");
      await loadJobs(agentId, true);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusyId("");
    }
  }

  async function runJob(job: CronJob) {
    if (busyId) return;
    setBusyId(job.id);
    setError("");
    setNotice("");
    try {
      await api.runCronJob(job.id);
      setNotice("已在任务会话中创建运行");
      await loadJobs(agentId, true);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusyId("");
    }
  }

  async function confirmDelete() {
    const target = deleteTarget;
    setDeleteTarget(null);
    if (!target) return;
    setError("");
    setNotice("");
    try {
      await api.deleteCronJob(target.id);
      setNotice("任务已删除，会话与历史保留");
      await loadJobs(agentId, true);
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  return (
    <main className="channels-page cron-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">SCHEDULE</div>
          <h1>定时任务</h1>
          <p>按 cron 表达式定时发起对话；每个任务拥有独立会话，可以直接进入追问。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>当前 Agent：{agent?.name ?? "—"}</h2>
                <p>到点后自动在任务会话里发起一轮 Run；上一次还没结束时会跳过本次触发。</p>
              </div>
              <div className="settings-card-actions">
                <button className="primary" onClick={openCreate} disabled={!agentId}>
                  新建定时任务
                </button>
              </div>
            </div>

            {error && <div className="error-banner">{error}</div>}
            {agent && agent.status !== "active" && (
              <div className="hint">
                当前 Agent 状态为「{agent.status === "paused" ? "暂停" : agent.status}」，
                定时任务不会触发；恢复 Agent 后从下一个匹配时间继续。
              </div>
            )}
            {notice && <div className="hint">{notice}</div>}
            {loading && jobs.length === 0 && <div className="empty-note">加载中…</div>}
            {!loading && jobs.length === 0 && (
              <div className="empty-note">
                还没有定时任务。新建后平台会在下一次匹配时间自动运行。
              </div>
            )}

            <div className="channel-list">
              {jobs.map((job) => (
                <div className="channel-row cron-row" key={job.id}>
                  <div className="channel-main">
                    <div className="channel-title-line">
                      <span className="channel-name">{job.name}</span>
                      <span className={`channel-status ${job.enabled ? "active" : "paused"}`}>
                        {job.enabled ? "启用" : "暂停"}
                      </span>
                    </div>
                    <div className="channel-meta">
                      <span className="cron-schedule">{job.schedule}</span>
                      {" · "}
                      {job.timezone}
                      {" · "}
                      下次 {job.enabled ? formatInZone(job.next_run_at, job.timezone) || "—" : "—"}
                    </div>
                    <div className="channel-meta">
                      最近运行 {formatInZone(job.last_run_at, job.timezone) || "—"}
                      {job.last_run_status
                        ? ` · ${RUN_STATUS_TEXT[job.last_run_status] ?? job.last_run_status}`
                        : ""}
                      {job.last_skipped_at ? " · 上次触发因会话忙被跳过" : ""}
                    </div>
                    <div className="channel-meta">
                      推送：
                      {job.push_channel_id && job.push_conversation_id
                        ? `微信 · ${job.push_channel_name || "已删除渠道"} / ${
                            job.push_conversation_name || "已删除联系人"
                          }`
                        : "不推送"}
                    </div>
                    {!job.session_id && (
                      <div className="channel-error">会话已删除，下次触发时会自动重建。</div>
                    )}
                  </div>
                  <div className="channel-actions">
                    <button
                      className="ghost-button"
                      onClick={() => job.session_id && onOpenSession(job.session_id)}
                      disabled={!job.session_id}
                    >
                      打开会话
                    </button>
                    <button
                      className="ghost-button"
                      onClick={() => void runJob(job)}
                      disabled={busyId === job.id}
                    >
                      立即运行
                    </button>
                    <button
                      className="ghost-button"
                      onClick={() => void toggleJob(job)}
                      disabled={busyId === job.id}
                    >
                      {job.enabled ? "暂停" : "启用"}
                    </button>
                    <button className="ghost-button" onClick={() => openEdit(job)}>
                      编辑
                    </button>
                    <button
                      className="danger-button"
                      onClick={() => setDeleteTarget(job)}
                      disabled={busyId === job.id}
                    >
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>

      {editor && (
        <div className="modal-backdrop" onClick={() => setEditor(null)}>
          <form
            className="modal cron-editor-modal"
            onSubmit={(event) => void saveJob(event)}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-heading">
              <h2>{editor.id ? "编辑定时任务" : "新建定时任务"}</h2>
              <button
                type="button"
                className="icon-button"
                onClick={() => setEditor(null)}
                aria-label="关闭"
              >
                ×
              </button>
            </div>

            <label className="field-label" htmlFor="cron-name">任务名称</label>
            <input
              id="cron-name"
              value={editor.name}
              onChange={(event) =>
                setEditor((current) => (current ? { ...current, name: event.target.value } : current))
              }
              placeholder="例如：每日工作简报"
              autoFocus
            />

            <label className="field-label" htmlFor="cron-prompt">任务内容</label>
            <textarea
              id="cron-prompt"
              rows={6}
              value={editor.prompt}
              onChange={(event) =>
                setEditor((current) => (current ? { ...current, prompt: event.target.value } : current))
              }
              placeholder="每次触发时发给 Agent 的消息，例如：总结昨天工作区里的变化，并列出今天要做的事。"
            />

            <div className="cron-form-grid">
              <div>
                <label className="field-label" htmlFor="cron-schedule">cron 表达式</label>
                <input
                  id="cron-schedule"
                  value={editor.schedule}
                  onChange={(event) =>
                    setEditor((current) =>
                      current ? { ...current, schedule: event.target.value } : current,
                    )
                  }
                  placeholder="0 8 * * *"
                  spellCheck={false}
                />
              </div>
              <div>
                <label className="field-label" htmlFor="cron-timezone">时区</label>
                <input
                  id="cron-timezone"
                  value={editor.timezone}
                  onChange={(event) =>
                    setEditor((current) =>
                      current ? { ...current, timezone: event.target.value } : current,
                    )
                  }
                  placeholder="Asia/Shanghai"
                  spellCheck={false}
                />
              </div>
            </div>
            <div className="hint">
              5 个字段：分 时 日 月 周。例如 <code>0 8 * * *</code> 表示每天 08:00，
              <code>*/30 * * * *</code> 表示每 30 分钟。
            </div>

            <label className="cron-checkbox">
              <input
                type="checkbox"
                checked={editor.enabled}
                onChange={(event) =>
                  setEditor((current) =>
                    current ? { ...current, enabled: event.target.checked } : current,
                  )
                }
              />
              {editor.id ? "启用任务" : "创建后立即启用"}
            </label>

            <label className="cron-checkbox">
              <input
                type="checkbox"
                checked={editor.pushEnabled}
                onChange={(event) =>
                  setEditor((current) =>
                    current
                      ? {
                          ...current,
                          pushEnabled: event.target.checked,
                          pushChannelId: event.target.checked ? current.pushChannelId : "",
                          pushConversationId: event.target.checked
                            ? current.pushConversationId
                            : "",
                        }
                      : current,
                  )
                }
              />
              推送到微信
            </label>

            {editor.pushEnabled && (
              <div className="cron-push-fields">
                <div className="cron-form-grid">
                  <div>
                    <label className="field-label" htmlFor="cron-push-channel">渠道</label>
                    <select
                      id="cron-push-channel"
                      value={editor.pushChannelId}
                      onChange={(event) => {
                        const channelId = event.target.value;
                        setEditor((current) =>
                          current
                            ? {
                                ...current,
                                pushChannelId: channelId,
                                pushConversationId: "",
                              }
                            : current,
                        );
                        void loadConversations(channelId);
                      }}
                    >
                      <option value="">选择渠道</option>
                      {channels.map((channel) => (
                        <option key={channel.id} value={channel.id}>
                          {channel.name || channel.id}
                          {channel.status !== "active"
                            ? `（${CHANNEL_STATUS_TEXT[channel.status] ?? channel.status}）`
                            : ""}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="field-label" htmlFor="cron-push-conversation">
                      联系人
                    </label>
                    <select
                      id="cron-push-conversation"
                      value={editor.pushConversationId}
                      onChange={(event) =>
                        setEditor((current) =>
                          current
                            ? { ...current, pushConversationId: event.target.value }
                            : current,
                        )
                      }
                      disabled={!editor.pushChannelId || conversationsLoading}
                    >
                      <option value="">
                        {conversationsLoading ? "加载中…" : "选择联系人"}
                      </option>
                      {conversations.map((conversation) => (
                        <option key={conversation.id} value={conversation.id}>
                          {conversation.display_name || "未命名联系人"}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                {channels.length === 0 && (
                  <div className="hint">
                    当前 Agent 还没有微信渠道；可先在「渠道」页扫码登录。
                  </div>
                )}
                {editor.pushChannelId &&
                  !conversationsLoading &&
                  conversations.length === 0 && (
                    <div className="hint">
                      该渠道还没有联系人会话；需要对方先给 bot 发一条消息。
                    </div>
                  )}
                {editor.pushChannelId &&
                  channels.length > 0 &&
                  !channels.some((channel) => channel.id === editor.pushChannelId) && (
                    <div className="hint">
                      原推送渠道已删除，请重新选择渠道和联系人。
                    </div>
                  )}
                <div className="hint">
                  只支持已有 1:1 联系人会话；渠道暂停时结果会等待恢复后补发。任务结果仍会保存在专属会话里。
                </div>
              </div>
            )}

            {error && <div className="error-banner">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="ghost-button" onClick={() => setEditor(null)}>
                取消
              </button>
              <button
                className="primary"
                type="submit"
                disabled={saving || !editor.name.trim() || !editor.prompt.trim()}
              >
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </form>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除这个定时任务？"
        description={`「${deleteTarget?.name || "定时任务"}」的配置会删除，不再触发；它的会话和已产生的消息会保留。`}
        confirmLabel="删除"
        danger
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
      />
    </main>
  );
}
