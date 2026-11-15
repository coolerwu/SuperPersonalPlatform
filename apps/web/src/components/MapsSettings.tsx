import { FormEvent, useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { MapsConfig } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";

export function MapsSettings() {
  const [config, setConfig] = useState<MapsConfig | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void api
      .getMapsConfig()
      .then((next) => {
        if (!active) return;
        setConfig(next);
        setEnabled(next.enabled);
      })
      .catch((exc) => {
        if (active) setError(errorText(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadAttempt]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!config || saving || clearing || clearOpen) return;
    const trimmed = apiKey.trim();
    if (enabled && !config?.has_api_key && !trimmed) {
      setError("请先填写高德 Web 服务 Key");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await api.updateMapsConfig({
        enabled,
        ...(trimmed ? { api_key: trimmed } : {}),
      });
      setConfig(next);
      setEnabled(next.enabled);
      setApiKey("");
      setNotice("已保存，下一轮对话生效。");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  async function clearKey() {
    if (clearing) return;
    setClearing(true);
    setClearOpen(false);
    setError("");
    setNotice("");
    try {
      const next = await api.updateMapsConfig({ clear_api_key: true });
      setConfig(next);
      setEnabled(next.enabled);
      setApiKey("");
      setNotice("已清除高德 Key 并停用地图工具。");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setClearing(false);
    }
  }

  const saveDisabled =
    !config ||
    saving ||
    clearing ||
    (enabled && !config.has_api_key && !apiKey.trim());

  return (
    <>
      <form className="editor" onSubmit={save}>
        <label className="toggle-row" htmlFor="maps-enabled">
          <input
            id="maps-enabled"
            type="checkbox"
            checked={enabled}
            disabled={!config || saving || clearing}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>
            <strong>启用高德地图工具</strong>
            <small>启用后所有 Agent 都可以搜索地点、规划路线和查天气</small>
          </span>
        </label>

        <label className="field-label" htmlFor="maps-key">
          高德 Web 服务 Key
        </label>
        <input
          id="maps-key"
          type="password"
          maxLength={200}
          autoComplete="off"
          value={apiKey}
          disabled={!config || saving || clearing}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder={
            config?.has_api_key
              ? "已配置，留空保持不变，输入新 Key 可替换"
              : "输入高德 Web 服务 Key（不是 JS API Key）"
          }
        />

        <div className="hint">
          在高德开放平台创建 Web 服务 Key：
          <a
            className="plugin-link"
            href="https://lbs.amap.com/api/mcp-server/create-project-and-key"
            target="_blank"
            rel="noreferrer"
          >
            创建 Key
          </a>
        </div>

        {config?.has_api_key && (
          <div className="form-actions">
            <span className="key-badge ok">密钥已配置</span>
            <button
              type="button"
              className="ghost-button"
              onClick={() => setClearOpen(true)}
              disabled={saving || clearing}
            >
              清除密钥
            </button>
          </div>
        )}

        {error && (
          <div className="error-banner" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <div className="hint" role="status">
            {notice}
          </div>
        )}

        <div className="form-actions">
          {config ? (
            <button className="primary" type="submit" disabled={saveDisabled}>
              {saving ? "保存中…" : "保存地图配置"}
            </button>
          ) : (
            <button
              type="button"
              className="ghost-button"
              onClick={() => setLoadAttempt((value) => value + 1)}
              disabled={loading}
            >
              {loading ? "加载中…" : "重新加载"}
            </button>
          )}
        </div>
      </form>

      <div className="hint">
        保存后下一轮对话生效；可在「工具」页查看 amap 服务的连接状态。
        无效 Key 时地图工具会失败，但普通聊天不受影响。
      </div>

      <ConfirmDialog
        open={clearOpen}
        title="清除地图 Key？"
        description="清除后高德地图工具会停用，需要重新填写 Key 才能恢复。"
        confirmLabel="清除"
        danger
        onClose={() => setClearOpen(false)}
        onConfirm={() => void clearKey()}
      />
    </>
  );
}
