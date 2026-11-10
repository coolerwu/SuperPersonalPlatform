import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { ToolsCatalog } from "../types";

type ToolsPageProps = {
  onClose: () => void;
};

export function ToolsPage({ onClose }: ToolsPageProps) {
  const [data, setData] = useState<ToolsCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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

  return (
    <main className="tools-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">TOOLS</div>
          <h1>工具</h1>
          <p>文件工具由 Claude Code 内置提供；当前版本平台工具与技能工具尚未接入 Agent，后续版本启用。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>平台工具</h2>
                <p>由平台声明；当前版本尚未接入 Agent，待工具层迁移。</p>
              </div>
            </div>
            <div className="tool-list">
              {(data?.platform_tools ?? []).map((tool) => (
                <div className="tool-row" key={tool.name}>
                  <div className="tool-main">
                    <strong>{tool.name}</strong>
                    <small>{tool.description}</small>
                  </div>
                  <span className={`capability ${tool.wired ? "on" : "off"}`}>
                    {tool.wired ? "已接入" : "未接入（待工具层）"}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>文件工具</h2>
                <p>Claude Code 内置工具，作用于当前 Agent 的工作区。</p>
              </div>
            </div>
            <div className="tool-list">
              {(data?.filesystem_tools ?? []).map((tool) => (
                <div className="tool-row" key={tool.name}>
                  <div className="tool-main">
                    <strong>{tool.name}</strong>
                    <small>{tool.description}</small>
                  </div>
                  <span className="capability on">Claude Code 内置</span>
                </div>
              ))}
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>技能工具</h2>
                <p>工具注册表已保留；当前版本尚未接入 Agent，待工具层迁移。</p>
              </div>
              <div className="settings-card-actions">
                <button className="ghost-button" onClick={() => void load()} disabled={loading}>
                  {loading ? "刷新中…" : "刷新"}
                </button>
              </div>
            </div>

            <div className="hint">
              web_search / web_fetch 使用当前 Agent Provider 的 DeepSeek Search/Fetch API，
              按成功请求计费；浏览器工具需要 web-browser 技能。
            </div>
            {error && <div className="error-banner">{error}</div>}
            {!data && !error && <div className="empty-note">读取中…</div>}
            {data && data.skill_tools.length === 0 && (
              <div className="empty-note">还没有技能工具。</div>
            )}
            <div className="tool-list">
              {(data?.skill_tools ?? []).map((tool) => (
                <div className="tool-row" key={tool.name}>
                  <div className="tool-main">
                    <strong>{tool.name}</strong>
                    <small>{tool.description || "平台注册表里还没有这个工具"}</small>
                    <small>
                      声明技能：
                      {tool.skills.length > 0
                        ? tool.skills.join(", ")
                        : "—（还没有技能声明，无法通过技能解锁）"}
                    </small>
                  </div>
                  <span className={`capability ${tool.registered ? "on" : "off"}`}>
                    {tool.registered ? "已注册" : "未注册"}
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
