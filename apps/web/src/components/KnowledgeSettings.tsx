import { FormEvent, useEffect, useRef, useState } from "react";

import { api, errorText } from "../api";
import { formatTime } from "../format";
import type { KnowledgeState } from "../types";

export function KnowledgeSettings() {
  const [state, setState] = useState<KnowledgeState | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [url, setUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [root, setRoot] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState(300);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const loaded = useRef(false);

  function fillForm(next: KnowledgeState) {
    setEnabled(next.config.enabled);
    setUrl(next.config.webdav_url);
    setUsername(next.config.username);
    setRoot(next.config.root);
    setIntervalSeconds(next.config.interval_seconds);
    setPassword("");
  }

  useEffect(() => {
    let active = true;
    void api
      .getKnowledge()
      .then((next) => {
        if (!active) return;
        setState(next);
        if (!loaded.current) {
          fillForm(next);
          loaded.current = true;
        }
      })
      .catch((exc) => {
        if (active) setError(errorText(exc));
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void api
        .getKnowledge()
        .then((next) => {
          setState(next);
          if (!loaded.current) {
            fillForm(next);
            loaded.current = true;
          }
        })
        .catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await api.updateKnowledge({
        enabled,
        webdav_url: url.trim(),
        username: username.trim(),
        ...(password.trim() ? { password: password.trim() } : {}),
        root: root.trim(),
        interval_seconds: Math.max(30, intervalSeconds || 300),
      });
      setState(next);
      setPassword("");
      setNotice("已保存；启用且已配置地址时会立即开始同步。");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  async function syncNow() {
    if (syncing) return;
    setSyncing(true);
    setError("");
    setNotice("");
    try {
      await api.syncKnowledge();
      setNotice("已触发同步，几秒后刷新状态。");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSyncing(false);
    }
  }

  async function clearPassword() {
    if (saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await api.updateKnowledge({ clear_password: true });
      setState(next);
      setPassword("");
      setNotice("已清除保存的 WebDAV 密码。");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  const config = state?.config;
  const status = state?.status;
  const statusLabel = !config
    ? "读取中"
    : status?.blocked
      ? "限流中"
      : status?.syncing
        ? "同步中"
        : !config.webdav_url
          ? "未配置"
          : config.enabled
            ? "已启用"
            : "已停用";
  const result = status?.last_result;
  const resultLabel = result
    ? `下载 ${result.downloaded} · 上传 ${result.uploaded} · 冲突 ${result.conflicts}`
    : "—";

  return (
    <>
      <div className="settings-subhead">同步状态</div>
      <div className="system-grid">
        <div><span>状态</span><strong>{statusLabel}</strong></div>
        <div><span>同步间隔</span><strong>{config?.interval_seconds ?? intervalSeconds}s</strong></div>
        <div><span>上次同步</span><strong>{status?.last_sync_at ? formatTime(status.last_sync_at) : "—"}</strong></div>
      </div>
      <div className="system-counts workspace-path-preview">
        本地镜像：{config?.local_dir ?? "—"}
      </div>
      <div className="system-counts">上次结果：{resultLabel}</div>
      {status?.blocked && status.retry_at && (
        <div className="system-counts">限流解除后自动重试：{formatTime(status.retry_at)}</div>
      )}
      {status?.last_error && <div className="error-banner">{status.last_error}</div>}

      <div className="settings-subhead">WebDAV 配置</div>
      <form className="editor" onSubmit={submit}>
        <label className="toggle-row" htmlFor="knowledge-enabled">
          <input
            id="knowledge-enabled"
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>
            <strong>启用知识库同步</strong>
            <small>停用后保留本地镜像和配置，不再访问 WebDAV</small>
          </span>
        </label>

        <label className="field-label" htmlFor="knowledge-url">WebDAV 地址</label>
        <input
          id="knowledge-url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://dav.jianguoyun.com/dav/"
        />

        <label className="field-label" htmlFor="knowledge-username">账号</label>
        <input
          id="knowledge-username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          placeholder="坚果云账号"
        />

        <label className="field-label" htmlFor="knowledge-password">应用密码</label>
        <input
          id="knowledge-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder={config?.has_password ? "已配置；留空保持原值" : "坚果云第三方应用密码"}
        />
        {config?.has_password && (
          <button
            type="button"
            className="ghost-button"
            onClick={clearPassword}
            disabled={saving}
          >
            清除已保存的密码
          </button>
        )}

        <label className="field-label" htmlFor="knowledge-root">根目录</label>
        <input
          id="knowledge-root"
          value={root}
          onChange={(event) => setRoot(event.target.value)}
          placeholder="例如 知识库；留空表示 WebDAV 根目录"
        />

        <label className="field-label" htmlFor="knowledge-interval">同步间隔（秒）</label>
        <input
          id="knowledge-interval"
          type="number"
          min={30}
          max={86400}
          step={30}
          value={intervalSeconds}
          onChange={(event) => setIntervalSeconds(Number(event.target.value) || 0)}
        />

        {error && <div className="error-banner">{error}</div>}
        {notice && <div className="hint">{notice}</div>}

        <div className="form-actions">
          <button
            type="button"
            className="ghost-button"
            onClick={syncNow}
            disabled={
              syncing || !state?.config.enabled || !state?.config.webdav_url
            }
          >
            {syncing ? "同步中…" : "立即同步"}
          </button>
          <button className="primary" type="submit" disabled={saving}>
            {saving ? "保存中…" : "保存知识库配置"}
          </button>
        </div>
      </form>
      <div className="hint">
        WebDAV 密码保存在 app.db，与 Provider 密钥相同，不会回传前端、不写日志；留空表示不修改。
        保存后立即唤醒同步；Agent 要在「技能」页启用 knowledge-base 才能看到 /knowledge/。
      </div>
      <div className="hint">
        坚果云免费账号限制约每 30 分钟 600 次 WebDAV 请求（所有第三方应用共享），平台已自动限速；
        首次同步大目录会持续较长时间，建议根目录只指向笔记子目录。遇到限流会暂停约 30 分钟后自动重试。
        更改 WebDAV 地址、账号或根目录会重置本地镜像，旧镜像保留为备份。
      </div>
    </>
  );
}
