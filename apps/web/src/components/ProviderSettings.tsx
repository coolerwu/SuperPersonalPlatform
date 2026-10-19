import { FormEvent, useEffect, useState } from "react";

import type { Provider } from "../types";

type ProviderSettingsProps = {
  provider: Provider | undefined;
  saving: boolean;
  onSave: (payload: { name: string; base_url: string; model: string; api_key?: string }) => Promise<void>;
};

export function ProviderSettings({ provider, saving, onSave }: ProviderSettingsProps) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setName(provider?.name ?? "");
    setBaseUrl(provider?.base_url ?? "");
    setModel(provider?.model ?? "");
    setApiKey("");
    setError("");
  }, [provider?.id, provider?.name, provider?.base_url, provider?.model]);

  if (!provider) {
    return <div className="empty-note">没有 provider。</div>;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setError("");
    try {
      await onSave({
        name: name.trim(),
        base_url: baseUrl.trim(),
        model: model.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setApiKey("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <form className="editor" onSubmit={submit}>
      <label className="field-label" htmlFor="provider-name">名称</label>
      <input id="provider-name" value={name} onChange={(event) => setName(event.target.value)} />

      <label className="field-label" htmlFor="provider-base-url">Base URL</label>
      <input id="provider-base-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />

      <label className="field-label" htmlFor="provider-model">Model</label>
      <input id="provider-model" value={model} onChange={(event) => setModel(event.target.value)} />

      <label className="field-label" htmlFor="provider-key">API Key</label>
      <input
        id="provider-key"
        type="password"
        value={apiKey}
        onChange={(event) => setApiKey(event.target.value)}
        placeholder={provider.has_api_key ? "已配置；留空保持原值" : "未配置"}
      />

      {error && <div className="error-banner">{error}</div>}
      <button className="primary" type="submit" disabled={saving}>
        {saving ? "保存中…" : "保存 Provider"}
      </button>
      <div className="hint">密钥只保存在后端，不会返回给前端；留空表示不修改。</div>
    </form>
  );
}
