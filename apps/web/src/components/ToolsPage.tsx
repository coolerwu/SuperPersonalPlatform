import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { Agent, ToolsCatalog } from "../types";

type ToolsPageProps = {
  agent: Agent | undefined;
  onSaveEnabled: (names: string[]) => Promise<void>;
  onClose: () => void;
};

export function ToolsPage({ agent, onSaveEnabled, onClose }: ToolsPageProps) {
  const [data, setData] = useState<ToolsCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const enabled = agent?.enabled_tools ?? [];

  async function load() {
    setLoading(true);
    setError("");
    try {
      setData(await api.listTools());
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setNotice("");
    setError("");
  }, [agent?.id]);

  async function toggle(name: string, checked: boolean) {
    if (!agent || saving) return;
    const next = checked
      ? [...enabled.filter((item) => item !== name), name]
      : enabled.filter((item) => item !== name);
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await onSaveEnabled(next);
      setNotice(`${checked ? "已启用" : "已停用"} ${name}`);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="tools-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">TOOLS</div>
          <h1>工具</h1>
          <p>文件工具始终可用；技能工具需要为当前 Agent 启用，并在读取对应 SKILL.md 后解锁。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>文件工具</h2>
                <p>由 FilesystemMiddleware 提供，作用于当前 Agent 的工作区，不需要技能解锁。</p>
              </div>
            </div>
            <div className="tool-list">
              {(data?.filesystem_tools ?? []).map((tool) => (
                <div className="tool-row" key={tool.name}>
                  <div className="tool-main">
                    <strong>{tool.name}</strong>
                    <small>{tool.description}</small>
                  </div>
                  <span className="capability on">始终可用</span>
                </div>
              ))}
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>技能工具 · {agent?.name ?? "—"}</h2>
                <p>启用跟随左上角选中的 Agent；还需要该工具被已启用技能的 SKILL.md 声明，读取后才会解锁。</p>
              </div>
              <div className="settings-card-actions">
                <button className="ghost-button" onClick={() => void load()} disabled={loading}>
                  {loading ? "刷新中…" : "刷新"}
                </button>
              </div>
            </div>

            {!agent && <div className="empty-note">还没有 Agent。</div>}
            {agent && (
              <>
                <div className="hint">
                  web_fetch 使用当前 Agent Provider 的 DeepSeek Fetch API，按成功请求计费；
                  未注册的工具不会生效。
                </div>
                {error && <div className="error-banner">{error}</div>}
                {!data && !error && <div className="empty-note">读取中…</div>}
                {data && data.skill_tools.length === 0 && (
                  <div className="empty-note">还没有技能工具。</div>
                )}
                <div className="tool-list">
                  {(data?.skill_tools ?? []).map((tool) => (
                    <label className="toggle-row tool-toggle" htmlFor={`tool-${tool.name}`} key={tool.name}>
                      <input
                        id={`tool-${tool.name}`}
                        type="checkbox"
                        checked={enabled.includes(tool.name)}
                        disabled={saving}
                        onChange={(event) => void toggle(tool.name, event.target.checked)}
                      />
                      <span className="tool-main">
                        <strong>{tool.name}</strong>
                        <small>{tool.description || "平台注册表里还没有这个工具"}</small>
                        <small>
                          声明技能：{tool.skills.length > 0 ? tool.skills.join(", ") : "—"}
                        </small>
                      </span>
                      <span className={`capability ${tool.registered ? "on" : "off"}`}>
                        {tool.registered ? "已注册" : "未注册"}
                      </span>
                    </label>
                  ))}
                </div>
                {notice && <div className="hint">{notice}</div>}
              </>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
