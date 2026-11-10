import { useEffect, useState } from "react";

import { api, errorText } from "../api";

type MemoryPageProps = {
  onClose: () => void;
};

/**
 * The user profile is one Markdown document (`claude/CLAUDE.md`) that the
 * Claude Agent SDK loads across every Agent, so this page is a single-document
 * editor rather than a list of memory rows.
 */
export function MemoryPage({ onClose }: MemoryPageProps) {
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    setError("");
    void api
      .getMemory()
      .then((result) => {
        if (!active) return;
        setContent(result.content);
        setSavedContent(result.content);
      })
      .catch((exc) => {
        if (active) setError(errorText(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function save() {
    if (saving || loading) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await api.saveMemory(content);
      setContent(result.content);
      setSavedContent(result.content);
      setNotice("已保存，新的会话会加载这份用户画像。");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  const dirty = content !== savedContent;

  return (
    <main className="memory-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">MEMORY</div>
          <h1>记忆</h1>
          <p>Claude 的用户画像（CLAUDE.md），跨 Agent 生效。</p>
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
                  这份 Markdown 会作为用户级说明加载到每个 Agent 的上下文中；
                  Agent 自己的笔记由 Claude 的 auto memory 管理，不在这里编辑。
                </p>
              </div>
            </div>

            <p className="memory-scope-hint">
              修改后保存到 <code>~/.super-personal-platform/claude/CLAUDE.md</code>，
              新会话生效；旧版本保留为同目录的 <code>CLAUDE.md.bak</code>。
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
                  void save();
                }}
              >
                <textarea
                  value={content}
                  onChange={(event) => {
                    setContent(event.target.value);
                    setNotice("");
                  }}
                  placeholder={"# 我\n- 偏好中文回复\n- 回答先给结论"}
                  spellCheck={false}
                  aria-label="CLAUDE.md 用户画像"
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
                    <button className="primary" type="submit" disabled={saving || !dirty}>
                      {saving ? "保存中…" : "保存"}
                    </button>
                  </div>
                </div>
              </form>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
