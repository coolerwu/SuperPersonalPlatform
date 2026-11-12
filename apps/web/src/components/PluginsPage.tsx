import { useEffect, useMemo, useState } from "react";

import { api, errorText } from "../api";
import { peekPageCache, storePageCache } from "../pageCache";
import type { PluginsCatalog } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";

type PluginsPageProps = {
  onClose: () => void;
};

type Confirmation =
  | { kind: "uninstall"; id: string; name: string }
  | { kind: "remove-marketplace" };

export function PluginsPage({ onClose }: PluginsPageProps) {
  const [catalog, setCatalog] = useState<PluginsCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);

  async function load() {
    const cached = peekPageCache<PluginsCatalog>("plugins:catalog");
    if (cached) {
      setCatalog(cached);
      setLoading(false);
    } else {
      setLoading(true);
    }
    setError("");
    try {
      const next = await api.listPlugins();
      setCatalog(next);
      storePageCache("plugins:catalog", next);
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

  async function reloadQuietly() {
    try {
      const next = await api.listPlugins();
      setCatalog(next);
      storePageCache("plugins:catalog", next);
    } catch {
      // Keep the original action error visible.
    }
  }

  async function run(
    label: string,
    action: () => Promise<PluginsCatalog>,
    done: string,
  ) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const next = await action();
      setCatalog(next);
      storePageCache("plugins:catalog", next);
      setNotice(done);
    } catch (exc) {
      setError(errorText(exc));
      void reloadQuietly();
    } finally {
      setBusy("");
    }
  }

  const filteredAvailable = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const entries = catalog?.available ?? [];
    if (!keyword) return entries;
    return entries.filter((plugin) =>
      [plugin.name, plugin.description, plugin.author, plugin.category]
        .join(" ")
        .toLowerCase()
        .includes(keyword),
    );
  }, [catalog, query]);

  const disabled = busy !== "";
  const official = catalog?.marketplaces[0] ?? null;

  async function confirmAction() {
    if (!confirmation) return;
    if (confirmation.kind === "uninstall") {
      const { id, name } = confirmation;
      setConfirmation(null);
      await run(
        "正在卸载…",
        () => api.uninstallPlugin(id),
        `已卸载 ${name}`,
      );
      return;
    }
    setConfirmation(null);
    await run(
      "正在移除集市…",
      () => api.removePluginMarketplace(),
      "官方集市已移除",
    );
  }

  return (
    <main className="plugins-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">PLUGINS</div>
          <h1>插件</h1>
          <p>
            从 Claude Code 官方集市安装插件，所有 Agent 共享；安装或启停后，新的一轮对话开始生效。
          </p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>官方集市</h2>
                <p>
                  只接入 anthropics/claude-plugins-official。添加和更新集市需要访问 GitHub。
                </p>
              </div>
              <div className="settings-card-actions">
                <button
                  className="ghost-button"
                  onClick={() => void load()}
                  disabled={loading || disabled}
                >
                  {loading ? "刷新中…" : "刷新"}
                </button>
              </div>
            </div>

            <div className="hint plugin-warning">
              插件可能包含技能、命令、hooks 与 MCP server，其中 hooks 和 MCP
              会在宿主机上执行代码。只安装你信任的插件；平台无法为插件代码提供沙箱。
            </div>

            {error && <div className="error-banner">{error}</div>}
            {notice && <div className="hint">{notice}</div>}
            {busy && <div className="hint">{busy}可能需要几十秒。</div>}
            {catalog && catalog.errors.length > 0 && (
              <div className="hint skill-warning">
                {catalog.errors.join("；")}
              </div>
            )}

            {!catalog && !error && <div className="empty-note">读取中…</div>}

            {catalog && !catalog.cli_available && (
              <div className="empty-note">
                未找到 Claude Code CLI：请确认服务端已安装 Claude Code，或先把
                CLI 加入服务进程的 PATH。
              </div>
            )}

            {catalog && catalog.cli_available && !official && (
              <div className="plugin-empty">
                <span>还没有添加官方集市。</span>
                <button
                  className="ghost-button"
                  disabled={disabled}
                  onClick={() =>
                    void run(
                      "正在添加集市…",
                      () => api.addPluginMarketplace(),
                      "官方集市已添加",
                    )
                  }
                >
                  添加官方集市
                </button>
              </div>
            )}

            {official && (
              <div className="plugin-row">
                <div className="plugin-main">
                  <strong>{official.name}</strong>
                  <small>{official.repo}</small>
                </div>
                <div className="plugin-actions">
                  <button
                    className="ghost-button"
                    disabled={disabled}
                    onClick={() =>
                      void run(
                        "正在更新集市…",
                        () => api.updatePluginMarketplace(),
                        "集市已更新",
                      )
                    }
                  >
                    更新集市
                  </button>
                  <button
                    className="ghost-button"
                    disabled={disabled}
                    onClick={() => setConfirmation({ kind: "remove-marketplace" })}
                  >
                    移除集市
                  </button>
                </div>
              </div>
            )}
          </section>

          {official && (
            <>
              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>已安装</h2>
                    <p>
                      启停和卸载会写入 Claude Code 配置；正在运行的对话不受影响。
                    </p>
                  </div>
                </div>
                {catalog && catalog.installed.length === 0 && (
                  <div className="empty-note">还没有安装插件。</div>
                )}
                {catalog?.installed.map((plugin) => (
                  <div className="plugin-row" key={plugin.id}>
                    <div className="plugin-main">
                      <strong>
                        {plugin.name}
                        <span className={`capability ${plugin.enabled ? "on" : "off"}`}>
                          {plugin.enabled ? "已启用" : "已停用"}
                        </span>
                      </strong>
                      <small>
                        {plugin.id}
                        {plugin.version ? ` · v${plugin.version}` : ""}
                        {` · ${plugin.scope || "user"}`}
                      </small>
                    </div>
                    <div className="plugin-actions">
                      <button
                        className="ghost-button"
                        disabled={disabled}
                        onClick={() =>
                          void run(
                            plugin.enabled ? "正在停用…" : "正在启用…",
                            () =>
                              plugin.enabled
                                ? api.disablePlugin(plugin.id)
                                : api.enablePlugin(plugin.id),
                            plugin.enabled
                              ? `已停用 ${plugin.name}`
                              : `已启用 ${plugin.name}`,
                          )
                        }
                      >
                        {plugin.enabled ? "停用" : "启用"}
                      </button>
                      <button
                        className="ghost-button"
                        disabled={disabled}
                        onClick={() =>
                          void run(
                            "正在更新…",
                            () => api.updatePlugin(plugin.id),
                            `已更新 ${plugin.name}`,
                          )
                        }
                      >
                        更新
                      </button>
                      <button
                        className="ghost-button"
                        disabled={disabled}
                        onClick={() =>
                          setConfirmation({
                            kind: "uninstall",
                            id: plugin.id,
                            name: plugin.name,
                          })
                        }
                      >
                        卸载
                      </button>
                    </div>
                  </div>
                ))}
              </section>

              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>可安装</h2>
                    <p>来自官方集市清单；安装时从 GitHub 拉取插件内容。</p>
                  </div>
                  <div className="settings-card-actions">
                    <input
                      className="plugin-search"
                      type="search"
                      value={query}
                      placeholder="搜索插件"
                      onChange={(event) => setQuery(event.target.value)}
                    />
                  </div>
                </div>
                {catalog && catalog.available.length === 0 && (
                  <div className="empty-note">
                    集市清单里没有可安装的插件；可以更新集市后重试。
                  </div>
                )}
                {catalog &&
                  catalog.available.length > 0 &&
                  filteredAvailable.length === 0 && (
                    <div className="empty-note">没有匹配的插件。</div>
                  )}
                {filteredAvailable.map((plugin) => (
                  <div className="plugin-row" key={plugin.name}>
                    <div className="plugin-main">
                      <strong>{plugin.name}</strong>
                      <small className="plugin-meta">
                        {[plugin.author, plugin.category]
                          .filter(Boolean)
                          .join(" · ") || "—"}
                      </small>
                      {plugin.description && (
                        <p className="plugin-description">{plugin.description}</p>
                      )}
                      {plugin.homepage && (
                        <a
                          className="plugin-link"
                          href={plugin.homepage}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {plugin.homepage}
                        </a>
                      )}
                    </div>
                    <div className="plugin-actions">
                      <button
                        className="ghost-button"
                        disabled={disabled}
                        onClick={() =>
                          void run(
                            "正在安装…",
                            () => api.installPlugin(plugin.name),
                            `已安装 ${plugin.name}`,
                          )
                        }
                      >
                        安装
                      </button>
                    </div>
                  </div>
                ))}
              </section>
            </>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmation !== null}
        title={
          confirmation?.kind === "uninstall"
            ? `卸载插件 ${confirmation.name}？`
            : "移除官方集市？"
        }
        description={
          confirmation?.kind === "uninstall"
            ? "插件提供的技能、命令、hooks 和 MCP 服务将不再可用。"
            : "集市下仍有已安装插件时会被拒绝；建议先卸载插件。"
        }
        confirmLabel={
          busy !== "" ? "处理中…" : confirmation?.kind === "uninstall" ? "卸载" : "移除"
        }
        danger
        onConfirm={() => void confirmAction()}
        onClose={() => {
          if (!disabled) setConfirmation(null);
        }}
      />
    </main>
  );
}
