import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { api, errorText } from "../api";
import { copyText } from "../clipboard";
import { formatTime } from "../format";
import type { Agent, Channel, ChannelLogin } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";

type ChannelsPageProps = {
  agent: Agent | undefined;
  agentId: string;
  onClose: () => void;
};

const STATUS_TEXT: Record<string, string> = {
  pairing: "待扫码",
  active: "已连接",
  paused: "已暂停",
  expired: "登录过期",
};

const LOGIN_STATUS_TEXT: Record<string, string> = {
  wait: "等待扫码…",
  scaned: "已扫码，请在微信里确认登录",
  scaned_but_redirect: "正在切换登录节点，请稍候…",
  expired: "二维码已过期，请刷新",
  confirmed: "已连接",
};

export function ChannelsPage({ agent, agentId, onClose }: ChannelsPageProps) {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("我的微信");
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [login, setLogin] = useState<ChannelLogin | null>(null);
  const [loginChannelId, setLoginChannelId] = useState("");
  const [loginStatus, setLoginStatus] = useState("wait");
  const [loginError, setLoginError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Channel | null>(null);

  const loadChannels = useCallback(async (id: string) => {
    if (!id) {
      setChannels([]);
      return;
    }
    setLoading(true);
    try {
      setChannels(await api.listChannels(id));
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setError("");
    setNotice("");
    setLogin(null);
    setLoginChannelId("");
    setCreateOpen(false);
    setDeleteTarget(null);
    void loadChannels(agentId);
  }, [agentId, loadChannels]);

  useEffect(() => {
    if (!login || !loginChannelId) return;
    let cancelled = false;
    let timer: number | null = null;
    const tick = async () => {
      let delay = 1500;
      try {
        const result = await api.getWechatLoginStatus(loginChannelId, login.qrcode);
        if (cancelled) return;
        setLoginStatus(result.status);
        if (result.status === "confirmed") {
          setLogin(null);
          setLoginChannelId("");
          setNotice("微信已连接");
          await loadChannels(agentId);
          return;
        }
        if (result.status === "expired") return;
      } catch (exc) {
        if (!cancelled) setLoginError(errorText(exc));
        delay = 5000;
      }
      if (!cancelled) timer = window.setTimeout(tick, delay);
    };
    timer = window.setTimeout(tick, 1200);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [login, loginChannelId, agentId, loadChannels]);

  async function startLogin(channelId: string) {
    setError("");
    setNotice("");
    setLoginError("");
    try {
      const data = await api.startWechatLogin(channelId);
      setLoginChannelId(channelId);
      setLoginStatus("wait");
      setLogin(data);
      await loadChannels(agentId);
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  async function createChannel(event: FormEvent) {
    event.preventDefault();
    if (!agentId || !name.trim() || creating) return;
    setCreating(true);
    setError("");
    try {
      const channel = await api.createChannel(agentId, name.trim());
      setCreateOpen(false);
      await loadChannels(agentId);
      await startLogin(channel.id);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setCreating(false);
    }
  }

  async function toggleChannel(channel: Channel) {
    if (busyId) return;
    setBusyId(channel.id);
    setError("");
    setNotice("");
    try {
      const next = channel.status === "active" ? "paused" : "active";
      await api.updateChannel(channel.id, { status: next });
      setNotice(next === "paused" ? "渠道已暂停" : "渠道已启用");
      await loadChannels(agentId);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusyId("");
    }
  }

  async function relogin(channel: Channel) {
    if (busyId) return;
    setBusyId(channel.id);
    setError("");
    try {
      await api.reloginWechat(channel.id);
      await loadChannels(agentId);
    } catch (exc) {
      setError(errorText(exc));
      setBusyId("");
      return;
    }
    setBusyId("");
    await startLogin(channel.id);
  }

  async function copyScanUrl() {
    if (!login) return;
    const copied = await copyText(login.scan_url);
    setLoginError(copied ? "" : "复制失败，请手动选择链接");
  }

  async function confirmDelete() {
    const target = deleteTarget;
    setDeleteTarget(null);
    if (!target) return;
    setError("");
    try {
      await api.deleteChannel(target.id);
      setNotice("渠道已删除，已有会话保留");
      await loadChannels(agentId);
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  return (
    <main className="channels-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">CHANNELS</div>
          <h1>渠道</h1>
          <p>为当前 Agent 绑定微信个人号；每个微信联系人对应一个独立会话。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>当前 Agent：{agent?.name ?? "—"}</h2>
                <p>一个 Agent 可以绑定多个微信渠道；扫码后由平台内置的 iLink 运行时收发消息。</p>
              </div>
              <div className="settings-card-actions">
                <button
                  className="primary"
                  onClick={() => {
                    setName("我的微信");
                    setError("");
                    setCreateOpen(true);
                  }}
                  disabled={!agentId}
                >
                  新建微信渠道
                </button>
              </div>
            </div>

            {error && <div className="error-banner">{error}</div>}
            {notice && <div className="hint">{notice}</div>}
            {loading && channels.length === 0 && <div className="empty-note">加载中…</div>}
            {!loading && channels.length === 0 && (
              <div className="empty-note">还没有渠道。新建后扫码登录微信即可开始接收消息。</div>
            )}

            <div className="channel-list">
              {channels.map((channel) => (
                <div className="channel-row" key={channel.id}>
                  <div className="channel-main">
                    <div className="channel-title-line">
                      <span className="channel-name">{channel.name || "微信渠道"}</span>
                      <span className={`channel-status ${channel.status}`}>
                        {STATUS_TEXT[channel.status] ?? channel.status}
                      </span>
                    </div>
                    <div className="channel-meta">
                      {channel.account_id
                        ? `iLink 账号：${channel.account_id}`
                        : "尚未完成微信登录"}
                      {" · "}
                      {channel.conversation_count} 个会话
                      {channel.last_seen_at
                        ? ` · 最近轮询 ${formatTime(channel.last_seen_at)}`
                        : ""}
                    </div>
                    {channel.last_error && (
                      <div className="channel-error">{channel.last_error}</div>
                    )}
                  </div>
                  <div className="channel-actions">
                    {(channel.status === "pairing" || channel.status === "expired") && (
                      <button
                        className="ghost-button"
                        onClick={() => void startLogin(channel.id)}
                        disabled={busyId === channel.id}
                      >
                        {channel.status === "expired" ? "重新扫码" : "扫码登录"}
                      </button>
                    )}
                    {channel.status === "paused" && (
                      <button
                        className="ghost-button"
                        onClick={() => void toggleChannel(channel)}
                        disabled={busyId === channel.id}
                      >
                        启用
                      </button>
                    )}
                    {channel.status === "active" && (
                      <>
                        <button
                          className="ghost-button"
                          onClick={() => void toggleChannel(channel)}
                          disabled={busyId === channel.id}
                        >
                          暂停
                        </button>
                        <button
                          className="ghost-button"
                          onClick={() => void relogin(channel)}
                          disabled={busyId === channel.id}
                        >
                          重新扫码
                        </button>
                      </>
                    )}
                    <button
                      className="danger-button"
                      onClick={() => setDeleteTarget(channel)}
                      disabled={busyId === channel.id}
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

      {createOpen && (
        <div className="modal-backdrop" onClick={() => setCreateOpen(false)}>
          <form
            className="modal channel-create-modal"
            onSubmit={(event) => void createChannel(event)}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-heading">
              <h2>新建微信渠道</h2>
              <button
                type="button"
                className="icon-button"
                onClick={() => setCreateOpen(false)}
                aria-label="关闭"
              >
                ×
              </button>
            </div>
            <label className="field-label" htmlFor="channel-name">渠道名称</label>
            <input
              id="channel-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：我的微信"
              autoFocus
            />
            <div className="hint">类型：微信个人号（iLink Bot API，扫码登录）</div>
            {error && <div className="error-banner">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="ghost-button" onClick={() => setCreateOpen(false)}>
                取消
              </button>
              <button className="primary" type="submit" disabled={creating || !name.trim()}>
                {creating ? "创建中…" : "创建并扫码"}
              </button>
            </div>
          </form>
        </div>
      )}

      {login && (
        <div className="modal-backdrop" onClick={() => setLogin(null)}>
          <div
            className="modal channel-qr-modal"
            role="dialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-heading">
              <h2>扫码连接微信</h2>
              <button
                type="button"
                className="icon-button"
                onClick={() => setLogin(null)}
                aria-label="关闭"
              >
                ×
              </button>
            </div>
            <p className="confirm-copy">请使用微信扫描二维码，并在手机上确认登录。</p>
            {login.qr_svg ? (
              <div
                className="channel-qr-frame"
                dangerouslySetInnerHTML={{ __html: login.qr_svg }}
              />
            ) : (
              <div className="empty-note">二维码渲染失败，请复制下方链接在浏览器打开。</div>
            )}
            <div className={`channel-login-status ${loginStatus}`}>
              {LOGIN_STATUS_TEXT[loginStatus] ?? loginStatus}
            </div>
            <div className="channel-scan-url">
              <input readOnly value={login.scan_url} onFocus={(event) => event.currentTarget.select()} />
              <button type="button" className="ghost-button" onClick={() => void copyScanUrl()}>
                复制链接
              </button>
            </div>
            {loginStatus === "expired" && (
              <button
                type="button"
                className="primary"
                onClick={() => void startLogin(loginChannelId)}
              >
                刷新二维码
              </button>
            )}
            {loginError && <div className="error-banner">{loginError}</div>}
            <div className="modal-actions">
              <button type="button" className="ghost-button" onClick={() => setLogin(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除这个渠道？"
        description={`「${deleteTarget?.name || "微信渠道"}」的登录凭据和渠道绑定会删除；已产生的会话和消息会保留。`}
        confirmLabel="删除"
        danger
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
      />
    </main>
  );
}
