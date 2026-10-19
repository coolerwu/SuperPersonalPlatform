import { FormEvent, useState } from "react";

type ProviderCreateFormProps = {
  busy: boolean;
  onCreate: (payload: { name: string; base_url: string; model: string; api_key: string }) => Promise<void>;
  onCancel: () => void;
};

export function ProviderCreateForm({ busy, onCreate, onCancel }: ProviderCreateFormProps) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    try {
      await onCreate({
        name: name.trim(),
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim(),
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  const valid = Boolean(name.trim() && baseUrl.trim() && model.trim());

  return (
    <form className="editor provider-create" onSubmit={submit}>
      <label className="field-label" htmlFor="new-provider-name">名称</label>
      <input id="new-provider-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} />

      <label className="field-label" htmlFor="new-provider-base-url">Base URL</label>
      <input
        id="new-provider-base-url"
        value={baseUrl}
        onChange={(event) => setBaseUrl(event.target.value)}
        placeholder="https://api.deepseek.com"
      />

      <label className="field-label" htmlFor="new-provider-model">Model</label>
      <input
        id="new-provider-model"
        value={model}
        onChange={(event) => setModel(event.target.value)}
        placeholder="deepseek-flash"
      />

      <label className="field-label" htmlFor="new-provider-key">API Key</label>
      <input
        id="new-provider-key"
        type="password"
        value={apiKey}
        onChange={(event) => setApiKey(event.target.value)}
        placeholder="sk-…"
      />

      {error && <div className="error-banner">{error}</div>}
      <div className="form-actions">
        <button type="button" className="ghost-button" onClick={onCancel}>取消</button>
        <button className="primary" type="submit" disabled={busy || !valid}>
          {busy ? "创建中…" : "创建 Provider"}
        </button>
      </div>
    </form>
  );
}
