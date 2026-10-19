import { FormEvent, useState } from "react";

import { api, errorText } from "../api";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!token.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.login(token.trim());
      onLogin();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="login-mark">ACC</div>
        <h1>Agent Command Center</h1>
        <p>输入访问 token 进入工作台。</p>
        <input
          autoFocus
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="Access token"
        />
        {error && <div className="error-banner">{error}</div>}
        <button className="primary" type="submit" disabled={busy || !token.trim()}>
          {busy ? "验证中…" : "进入"}
        </button>
      </form>
    </div>
  );
}
