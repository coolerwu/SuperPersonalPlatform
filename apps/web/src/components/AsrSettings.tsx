import { FormEvent, useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { AsrConfig } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";

export function AsrSettings() {
  const [config, setConfig] = useState<AsrConfig | null>(null);
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
      .getAsrConfig()
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
    if (enabled && !config.has_api_key && !trimmed) {
      setError("请先填写 DeepSeek API Key");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await api.updateAsrConfig({
        enabled,
        ...(trimmed ? { api_key: trimmed } : {}),
      });
      setConfig(next);
      setEnabled(next.enabled);
      setApiKey("");
      setNotice("已保存。");
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
      const next = await api.updateAsrConfig({ clear_api_key: true });
      setConfig(next);
      setEnabled(next.enabled);
      setApiKey("");
      setNotice("已清除 DeepSeek API Key 并停用语音输入。");
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
        <label className="toggle-row" htmlFor="asr-enabled">
          <input
            id="asr-enabled"
            type="checkbox"
            checked={enabled}
            disabled={!config || saving || clearing}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>
            <strong>启用语音输入</strong>
            <small>聊天输入框出现麦克风按钮，所有 Agent 共享</small>
          </span>
        </label>

        <label className="field-label" htmlFor="asr-key">
          DeepSeek API Key
        </label>
        <input
          id="asr-key"
          type="password"
          maxLength={200}
          autoComplete="off"
          value={apiKey}
          disabled={!config || saving || clearing}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder={
            config?.has_api_key
              ? "已配置，留空保持不变，输入新 Key 可替换"
              : "输入 DeepSeek API Key"
          }
        />

        <div className="hint">
          音频只用于转写，不会保存到工作区或会话记录；单次录音最长 2 分钟。
          ASR 按音频时长计费。
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
              {saving ? "保存中…" : "保存语音识别配置"}
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

      <ConfirmDialog
        open={clearOpen}
        title="清除 DeepSeek API Key？"
        description="清除后语音输入会停用，需要重新填写 Key 才能恢复。"
        confirmLabel="清除"
        danger
        onClose={() => setClearOpen(false)}
        onConfirm={() => void clearKey()}
      />
    </>
  );
}
