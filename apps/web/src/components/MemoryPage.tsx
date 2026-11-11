import { useEffect, useRef, useState } from "react";

import { api, errorText } from "../api";
import { formatTime } from "../format";
import { formatBytes } from "../messageContent";
import type { Agent, AgentAutoMemory, MemoryDocument } from "../types";

type MemoryPageProps = {
  agent?: Agent;
  agentId: string;
  onClose: () => void;
};

type MemoryDocumentEditorProps = {
  reloadKey: string;
  load: () => Promise<MemoryDocument>;
  save: (content: string) => Promise<MemoryDocument>;
  path: string;
  placeholder: string;
  ariaLabel: string;
  savedMessage: string;
};

/**
 * One Markdown document backed by a CLAUDE.md file. The user profile is the
 * user-level memory shared by every Agent; the Agent document is the project
 * memory of the selected workspace.
 */
function MemoryDocumentEditor({
  reloadKey,
  load,
  save,
  path,
  placeholder,
  ariaLabel,
  savedMessage,
}: MemoryDocumentEditorProps) {
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const keyRef = useRef(reloadKey);
  keyRef.current = reloadKey;

  useEffect(() => {
    let active = true;
    setContent("");
    setSavedContent("");
    setLoading(true);
    setSaving(false);
    setError("");
    setNotice("");
    void load()
      .then((result) => {
        if (!active || keyRef.current !== reloadKey) return;
        setContent(result.content);
        setSavedContent(result.content);
      })
      .catch((exc) => {
        if (active && keyRef.current === reloadKey) setError(errorText(exc));
      })
      .finally(() => {
        if (active && keyRef.current === reloadKey) setLoading(false);
      });
    return () => {
      active = false;
    };
    // reloadKey is the only reload trigger; callbacks belong to this render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  async function submit() {
    if (saving || loading) return;
    const key = reloadKey;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await save(content);
      if (keyRef.current !== key) return;
      setContent(result.content);
      setSavedContent(result.content);
      setNotice(savedMessage);
    } catch (exc) {
      if (keyRef.current === key) setError(errorText(exc));
    } finally {
      if (keyRef.current === key) setSaving(false);
    }
  }

  const dirty = content !== savedContent;

  return (
    <>
      <p className="memory-scope-hint">
        修改后保存到 <code>{path}</code>，下一轮对话生效；旧版本保留为同目录的{" "}
        <code>CLAUDE.md.bak</code>。
      </p>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="hint">{notice}</div>}

      {loading ? (
        <div className="empty-note">读取中…</div>
      ) : (
        <form
          className="memory-editor"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <textarea
            value={content}
            onChange={(event) => {
              setContent(event.target.value);
              setNotice("");
            }}
            placeholder={placeholder}
            spellCheck={false}
            aria-label={ariaLabel}
          />
          <div className="memory-editor-footer">
            <span className="memory-form-note">
              {content.length} 字符{dirty ? " · 有未保存修改" : ""}
            </span>
            <div className="memory-editor-actions">
              <button
                className="ghost-button"
                type="button"
                disabled={!dirty || saving}
                onClick={() => {
                  setContent(savedContent);
                  setNotice("");
                }}
              >
                撤销修改
              </button>
              <button
                className="primary"
                type="submit"
                disabled={saving || !dirty}
              >
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </form>
      )}
    </>
  );
}

function AutoMemoryPanel({ agentId }: { agentId: string }) {
  const [data, setData] = useState<AgentAutoMemory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestSeq = useRef(0);

  async function loadFor(id: string) {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError("");
    try {
      const result = await api.getAgentAutoMemory(id);
      if (requestSeq.current !== seq) return;
      setData(result);
    } catch (exc) {
      if (requestSeq.current === seq) setError(errorText(exc));
    } finally {
      if (requestSeq.current === seq) setLoading(false);
    }
  }

  useEffect(() => {
    setData(null);
    void loadFor(agentId);
    return () => {
      requestSeq.current += 1;
    };
    // loadFor is recreated per render but agentId is the only trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  return (
    <div className="memory-auto">
      <div className="memory-auto-head">
        <div>
          <h3>Claude auto memory（只读）</h3>
          <p>
            Claude 在对话中自动维护的记忆文件；平台只读展示，不编辑、不整理。
          </p>
        </div>
        <button
          className="ghost-button"
          type="button"
          disabled={loading}
          onClick={() => void loadFor(agentId)}
        >
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {data && <p className="memory-auto-dir">{data.directory}</p>}
      {data && data.errors.length > 0 && (
        <div className="hint skill-warning">{data.errors.join("；")}</div>
      )}
      {loading && !data && <div className="empty-note">读取中…</div>}
      {data && data.files.length === 0 && (
        <div className="empty-note">Claude 还没有写入自动记忆。</div>
      )}
      {data?.files.map((file) => (
        <details className="memory-auto-file" key={file.name}>
          <summary>
            <span>{file.name}</span>
            <span className="memory-auto-meta">
              {formatBytes(file.size)} · {formatTime(file.updated_at)}
            </span>
          </summary>
          <pre>{file.content}</pre>
        </details>
      ))}
    </div>
  );
}

export function MemoryPage({ agent, agentId, onClose }: MemoryPageProps) {
  return (
    <main className="memory-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">MEMORY</div>
          <h1>记忆</h1>
          <p>用户画像跨 Agent 生效；Agent 记忆只属于当前 Agent。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>用户画像</h2>
                <p>
                  这份 Markdown 会作为用户级说明加载到每个 Agent 的上下文中，跨 Agent 生效。
                </p>
              </div>
            </div>

            <MemoryDocumentEditor
              reloadKey="user"
              load={api.getMemory}
              save={api.saveMemory}
              path="~/.super-personal-platform/claude/CLAUDE.md"
              placeholder={"# 我\n- 偏好中文回复\n- 回答先给结论"}
              ariaLabel="CLAUDE.md 用户画像"
              savedMessage="已保存，下一轮对话会加载这份用户画像。"
            />
          </section>

          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>Agent 记忆</h2>
                <p>
                  当前 Agent：{agent?.name ?? "—"}。这份文档只影响当前 Agent；
                  切换 Agent 时未保存的修改会丢弃。
                </p>
              </div>
            </div>

            {agentId ? (
              <>
                <MemoryDocumentEditor
                  reloadKey={agentId}
                  load={() => api.getAgentMemory(agentId)}
                  save={(content) => api.saveAgentMemory(agentId, content)}
                  path={`~/.super-personal-platform/workspaces/${agentId}/CLAUDE.md`}
                  placeholder={"# 这个 Agent 的长期记忆\n- 项目背景、约定或偏好"}
                  ariaLabel={`${agent?.name ?? "Agent"} 的记忆`}
                  savedMessage="已保存，下一轮对话会加载这个 Agent 的记忆。"
                />
                <AutoMemoryPanel agentId={agentId} />
              </>
            ) : (
              <div className="empty-note">先在左上角选择一个 Agent。</div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
