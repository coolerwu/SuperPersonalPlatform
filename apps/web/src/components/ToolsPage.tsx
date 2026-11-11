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
          <p>Agent 使用 Claude Code 默认工具集；平台业务工具已通过进程内 MCP 接入，浏览器与搜索由 Claude Code 自带能力或插件提供。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>平台工具</h2>
                <p>技能创作工具尚未接入 Agent；平台业务工具见下方。</p>
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
                    {tool.wired ? "已接入" : "未接入"}
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
                    "Claude Code 默认工具集，随 bundled CLI 版本和插件环境变化；平台业务工具以 mcp__platform_tools__* 出现在运行时 init frame。"}
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
                <p>平台业务工具已通过进程内 MCP 接入；浏览器、搜索和技能创作仍未接入。</p>
              </div>
              <div className="settings-card-actions">
                <button className="ghost-button" onClick={() => void load()} disabled={loading}>
                  {loading ? "刷新中…" : "刷新"}
                </button>
              </div>
            </div>

            <div className="hint">
              回测、行情、交易记录、资产配置和缠论工具已接入 Agent；
              浏览器操作走官方 Playwright 插件，搜索走 Claude Code 自带的 WebSearch/WebFetch。
              平台自带的浏览器工具、web_search/web_fetch 与技能创作仍未接入。
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
                  <span className={`capability ${tool.wired ? "on" : "off"}`}>
                    {tool.wired
                      ? "已接入"
                      : tool.registered
                        ? "已注册（未接入）"
                        : "未注册"}
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
