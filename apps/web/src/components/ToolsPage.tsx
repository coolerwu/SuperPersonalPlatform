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
          <p>文件工具始终可用；技能工具只有在读取对应 SKILL.md 后才会解锁。</p>
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
                <h2>技能工具</h2>
                <p>SKILL.md 的 metadata.include_tools 声明工具名；平台注册表里已注册的工具才会在读取技能后解锁。</p>
              </div>
              <div className="settings-card-actions">
                <button className="ghost-button" onClick={() => void load()} disabled={loading}>
                  {loading ? "刷新中…" : "刷新"}
                </button>
              </div>
            </div>

            {error && <div className="error-banner">{error}</div>}
            {!data && !error && <div className="empty-note">读取中…</div>}
            {data && data.skill_tools.length === 0 && (
              <div className="empty-note">
                还没有技能声明工具。v1 的平台注册表为空；在 apps/api/app/services/skill_tools.py
                注册工具后，这里会显示可解锁的工具。
              </div>
            )}
            <div className="tool-list">
              {(data?.skill_tools ?? []).map((tool) => (
                <div className="tool-row" key={tool.name}>
                  <div className="tool-main">
                    <strong>{tool.name}</strong>
                    <small>{tool.description || "平台注册表里还没有这个工具"}</small>
                    <small>
                      声明技能：{tool.skills.length > 0 ? tool.skills.join(", ") : "—"}
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
