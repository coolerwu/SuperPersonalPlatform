import { FormEvent, useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { Agent, MemoryItem, MemoryScope } from "../types";
import { formatTime } from "../format";
import { ConfirmDialog } from "./ConfirmDialog";

type MemoryPageProps = {
  agents: Agent[];
  agentId: string;
  onClose: () => void;
};

function parseTags(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 8);
}

export function MemoryPage({ agents, agentId, onClose }: MemoryPageProps) {
  const [scope, setScope] = useState<MemoryScope>("user");
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [query, setQuery] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [editingId, setEditingId] = useState("");
  const [editContent, setEditContent] = useState("");
  const [editTags, setEditTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmId, setConfirmId] = useState("");

  const agent = agents.find((item) => item.id === agentId);

  useEffect(() => {
    let active = true;
    setError("");
    if (scope === "agent" && !agentId) {
      setItems([]);
      return () => {
        active = false;
      };
    }
    void api
      .listMemories({ scope, agentId: scope === "agent" ? agentId : undefined })
      .then((list) => {
        if (active) setItems(list);
      })
      .catch((exc) => {
        if (active) setError(errorText(exc));
      });
    return () => {
      active = false;
    };
  }, [scope, agentId]);

  async function reload() {
    const list = await api.listMemories({
      scope,
      agentId: scope === "agent" ? agentId : undefined,
    });
    setItems(list);
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!content.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.createMemory({
        scope,
        agent_id: scope === "agent" ? agentId : undefined,
        content: content.trim(),
        tags: parseTags(tags),
      });
      setContent("");
      setTags("");
      await reload();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  }

  function startEdit(item: MemoryItem) {
    setEditingId(item.id);
    setEditContent(item.content);
    setEditTags(item.tags.join(", "));
  }

  async function saveEdit() {
    if (!editingId || !editContent.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.updateMemory(editingId, {
        content: editContent.trim(),
        tags: parseTags(editTags),
      });
      setEditingId("");
      await reload();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setError("");
    try {
      await api.deleteMemory(id);
      if (editingId === id) setEditingId("");
      await reload();
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  const filtered = items.filter((item) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return (
      item.content.toLowerCase().includes(needle) ||
      item.tags.some((tag) => tag.toLowerCase().includes(needle))
    );
  });
  const unavailable = scope === "agent" && !agentId;

  return (
    <main className="memory-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">MEMORY</div>
          <h1>记忆</h1>
          <p>跨会话保留的用户偏好和 Agent 知识。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="memory-toolbar">
              <div className="settings-tabs">
                <button
                  type="button"
                  className={scope === "user" ? "active" : ""}
                  onClick={() => setScope("user")}
                >
                  用户记忆
                </button>
                <button
                  type="button"
                  className={scope === "agent" ? "active" : ""}
                  onClick={() => setScope("agent")}
                >
                  Agent 记忆
                </button>
              </div>
              <input
                className="memory-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索记忆…"
              />
            </div>

            <p className="memory-scope-hint">
              {scope === "user"
                ? "所有 Agent 共享；也可以在对话里说“记住：……”。"
                : agent
                  ? `仅对 ${agent.name} 生效；开启自动学习后会在 Run 完成时自动提取。`
                  : "先选择一个 Agent。"}
            </p>

            {!unavailable && (
              <>
                <form className="memory-form" onSubmit={add}>
                  <textarea
                    rows={2}
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                    placeholder="一条长期记忆，例如：回答先给结论，再展开。"
                  />
                  <div className="memory-form-row">
                    <input
                      value={tags}
                      onChange={(event) => setTags(event.target.value)}
                      placeholder="标签，用逗号分隔（可选）"
                    />
                    <button className="primary" type="submit" disabled={busy || !content.trim()}>
                      {busy ? "保存中…" : "添加"}
                    </button>
                  </div>
                </form>

                {error && <div className="error-banner">{error}</div>}

                <div className="memory-list">
                  {filtered.length === 0 && <div className="empty-note">还没有记忆。</div>}
                  {filtered.map((item) =>
                    editingId === item.id ? (
                      <div key={item.id} className="memory-item editing">
                        <textarea
                          rows={2}
                          value={editContent}
                          onChange={(event) => setEditContent(event.target.value)}
                        />
                        <div className="memory-form-row">
                          <input
                            value={editTags}
                            onChange={(event) => setEditTags(event.target.value)}
                            placeholder="标签，用逗号分隔"
                          />
                          <button
                            className="primary"
                            type="button"
                            disabled={busy || !editContent.trim()}
                            onClick={() => void saveEdit()}
                          >
                            保存
                          </button>
                          <button
                            className="ghost-button"
                            type="button"
                            onClick={() => setEditingId("")}
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div key={item.id} className="memory-item">
                        <div className="memory-content">{item.content}</div>
                        <div className="memory-meta">
                          {item.tags.map((tag) => (
                            <span key={tag} className="memory-tag">{tag}</span>
                          ))}
                          <span>
                            {item.source_run_id
                              ? `来自 Run ${item.source_run_id.slice(0, 8)}`
                              : "手动添加"}
                          </span>
                          <span>{formatTime(item.updated_at)}</span>
                        </div>
                        <div className="memory-actions">
                          <button type="button" onClick={() => startEdit(item)}>编辑</button>
                          <button
                            type="button"
                            onClick={() => setConfirmId(item.id)}
                          >
                            删除
                          </button>
                        </div>
                      </div>
                    ),
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      </div>

      <ConfirmDialog
        open={confirmId !== ""}
        title="删除这条记忆？"
        description="删除后无法恢复，仅删除这一条记忆。"
        confirmLabel="删除"
        danger
        onClose={() => setConfirmId("")}
        onConfirm={() => {
          const target = confirmId;
          setConfirmId("");
          if (target) void remove(target);
        }}
      />
    </main>
  );
}
