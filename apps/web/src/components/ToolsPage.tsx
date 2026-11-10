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
          <p>Agent 使用 Claude Code 默认工具集；当前版本平台工具与技能工具尚未接入 Agent，后续版本启用。</p>
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
                <h2>Claude Code 默认工具集</h2>
                <p>
                  {data?.claude_code_note ??
                    "Claude Code 默认工具集，随 bundled CLI 版本和插件环境变化，以运行时 init frame 为准。"}
                </p>
                <p>
                  默认以工作区为起点，但没有沙箱或权限边界（Bash 同理）；
                  访问工作区外路径可能触发授权。
                </p>
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
              平台注册表中的搜索、浏览器、回测、交易记录、资产配置、缠论和知识库工具都未接入 SDK；
              本版 Agent 不会调用这些工具。列表中的 Skill 是 Claude Code 自带工具，
              不读取平台 skills/ 目录。
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
                        : "—（没有技能声明）"}
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
