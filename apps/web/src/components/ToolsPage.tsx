import { useEffect, useMemo, useState } from "react";

import { api, errorText } from "../api";
import type { McpServerInfo, ToolInfo, ToolsCatalog } from "../types";

type ToolsPageProps = {
  agentId: string;
  onClose: () => void;
};

function statusLabel(status: string): string {
  switch (status) {
    case "connected":
      return "已连接";
    case "failed":
      return "连接失败";
    case "pending":
      return "连接中";
    default:
      return status || "未知状态";
  }
}

export function ToolsPage({ agentId, onClose }: ToolsPageProps) {
  const [data, setData] = useState<ToolsCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    if (!agentId) {
      setData(null);
      return;
    }
    setLoading(true);
    try {
      setData(await api.listTools(agentId));
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setData(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  const mcpGroups = useMemo(() => {
    const byServer = new Map<string, ToolInfo[]>();
    for (const tool of data?.mcp_tools ?? []) {
      const server = tool.server || "unknown";
      byServer.set(server, [...(byServer.get(server) ?? []), tool]);
    }
    const known = new Map(
      (data?.mcp_servers ?? []).map((server) => [server.name, server]),
    );
    const names = new Set([...known.keys(), ...byServer.keys()]);
    return Array.from(names)
      .sort((left, right) => left.localeCompare(right))
      .map((name) => ({
        name,
        meta: known.get(name) as McpServerInfo | undefined,
        tools: byServer.get(name) ?? [],
      }));
  }, [data]);

  return (
    <main className="tools-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">TOOLS</div>
          <h1>工具</h1>
          <p>
            读取当前 Agent 最近一轮对话的运行时 init frame；工具、MCP、技能和插件
            都以 Claude Code 实际加载的内容为准，平台不再维护自己的副本。
          </p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          {error && <div className="error-banner">{error}</div>}
          {!agentId && <div className="empty-note">先在侧栏选择一个 Agent。</div>}
          {agentId && !data && !error && <div className="empty-note">读取中…</div>}

          {data && !data.loaded && (
            <section className="settings-card">
              <div className="settings-card-head">
                <div>
                  <h2>运行时尚未加载</h2>
                  <p>{data.note}</p>
                </div>
                <div className="settings-card-actions">
                  <button className="ghost-button" onClick={() => void load()} disabled={loading}>
                    {loading ? "刷新中…" : "刷新"}
                  </button>
                </div>
              </div>
            </section>
          )}

          {data?.loaded && (
            <>
              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>运行时</h2>
                    <p>{data.note}</p>
                  </div>
                  <div className="settings-card-actions">
                    <button className="ghost-button" onClick={() => void load()} disabled={loading}>
                      {loading ? "刷新中…" : "刷新"}
                    </button>
                  </div>
                </div>
                <div className="system-counts">
                  Claude Code {data.claude_code_version || "—"} · 模型 {data.model || "—"} ·
                  权限模式 {data.permission_mode || "—"}
                </div>
                <div className="system-counts">
                  {data.claude_tools.length} 个内置工具 · {data.mcp_tools.length} 个 MCP 工具 ·{" "}
                  {data.skills.length} 个技能 · {data.plugins.length} 个插件
                </div>
              </section>

              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>Claude Code 内置工具</h2>
                    <p>来自 init frame 的 tools，名称和数量随 CLI 版本与插件环境变化。</p>
                  </div>
                </div>
                {data.claude_tools.length === 0 ? (
                  <div className="empty-note">运行时没有报告内置工具。</div>
                ) : (
                  <div className="tool-list">
                    {data.claude_tools.map((tool) => (
                      <div className="tool-row" key={tool.raw_name || tool.name}>
                        <div className="tool-main">
                          <strong>{tool.name}</strong>
                          {tool.description && <small>{tool.description}</small>}
                        </div>
                        <span className="capability on">运行时</span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>MCP 工具</h2>
                    <p>
                      进程内平台工具（platform_tools）和插件自带 MCP 都按运行时实际连接结果展示；
                      平台业务工具的描述来自平台注册表。
                    </p>
                  </div>
                </div>
                {mcpGroups.length === 0 && (
                  <div className="empty-note">运行时没有报告 MCP server。</div>
                )}
                {mcpGroups.map((group) => (
                  <div key={group.name} className="tool-mcp-group">
                    <div className="settings-card-head">
                      <div>
                        <h3>{group.name}</h3>
                        <p>
                          {group.meta
                            ? `${statusLabel(group.meta.status)} · 来源 ${group.meta.source || "—"}`
                            : "未在 mcp_servers 中报告"}
                        </p>
                      </div>
                      <span className={`capability ${group.meta?.status === "connected" ? "on" : "off"}`}>
                        {group.meta ? statusLabel(group.meta.status) : "未知"}
                      </span>
                    </div>
                    {group.tools.length === 0 ? (
                      <div className="empty-note">这个 server 没有暴露工具。</div>
                    ) : (
                      <div className="tool-list">
                        {group.tools.map((tool) => (
                          <div className="tool-row" key={tool.raw_name || tool.name}>
                            <div className="tool-main">
                              <strong>{tool.name}</strong>
                              {tool.description && <small>{tool.description}</small>}
                            </div>
                            <span className="capability on">MCP</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </section>

              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>技能</h2>
                    <p>来自 init frame 的 skills；技能内容仍在 claude/skills 下由 Claude Code 自动发现。</p>
                  </div>
                </div>
                {data.skills.length === 0 ? (
                  <div className="empty-note">运行时没有报告技能。</div>
                ) : (
                  <div className="tool-list">
                    {data.skills.map((skill) => (
                      <div className="tool-row" key={skill}>
                        <div className="tool-main">
                          <strong>{skill}</strong>
                        </div>
                        <span className="capability on">Skill</span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>插件</h2>
                    <p>来自 init frame 的 plugins；加载失败会单独列出。</p>
                  </div>
                </div>
                {data.plugins.length === 0 && data.plugin_errors.length === 0 ? (
                  <div className="empty-note">运行时没有报告插件。</div>
                ) : (
                  <div className="tool-list">
                    {data.plugins.map((plugin) => (
                      <div className="tool-row" key={plugin.name}>
                        <div className="tool-main">
                          <strong>{plugin.name}</strong>
                          {plugin.version && <small>版本 {plugin.version}</small>}
                        </div>
                        <span className="capability on">已加载</span>
                      </div>
                    ))}
                    {data.plugin_errors.map((item, index) => (
                      <div className="tool-row" key={`${item.plugin}-${item.type}-${index}`}>
                        <div className="tool-main">
                          <strong>{item.plugin}</strong>
                          <small>
                            {item.type || "加载失败"}
                            {item.message ? `：${item.message}` : ""}
                          </small>
                        </div>
                        <span className="capability off">加载失败</span>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
