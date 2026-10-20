import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { SkillDetail, SkillInfo } from "../types";

type SkillEditorDialogProps = {
  skill: SkillInfo | null;
  onClose: () => void;
  onSaved: (name: string) => Promise<void>;
};

export function SkillEditorDialog({ skill, onClose, onSaved }: SkillEditorDialogProps) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!skill) {
      setDetail(null);
      setContent("");
      setError("");
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    setDetail(null);
    api
      .getSkill(skill.directory)
      .then((document) => {
        if (!active) return;
        setDetail(document);
        setContent(document.content);
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
  }, [skill?.directory]);

  if (!skill) return null;

  const target = skill;
  const dirty = detail !== null && content !== detail.content;

  async function save() {
    if (!detail || !dirty || saving) return;
    setSaving(true);
    setError("");
    try {
      await api.updateSkill(target.directory, content);
      await onSaved(target.name);
      onClose();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      onClick={() => {
        if (!saving) onClose();
      }}
    >
      <div className="modal skill-editor" onClick={(event) => event.stopPropagation()}>
        <div className="modal-heading">
          <div>
            <h2>{skill.name}</h2>
            <div className="skill-editor-path">{skill.path}</div>
          </div>
          <button type="button" className="icon-button" onClick={onClose} disabled={saving}>
            ×
          </button>
        </div>

        {loading && <div className="empty-note">读取 SKILL.md…</div>}
        {error && <div className="error-banner">{error}</div>}

        {detail && (
          <>
            <p className="skill-editor-hint">
              直接编辑 SKILL.md 后保存。frontmatter 必须包含 name 和 description；
              metadata.include_tools 用空格分隔工具名。
            </p>
            <textarea
              className="skill-editor-text"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              spellCheck={false}
              aria-label="SKILL.md 内容"
            />
            {detail.warnings.map((warning) => (
              <div key={warning} className="hint skill-warning">
                {warning}
              </div>
            ))}
          </>
        )}

        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => void save()}
            disabled={!detail || !dirty || saving}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
        <div className="skill-editor-hint">
          新建技能仍然在对话里让 Agent 创建；这里用于查看和修改现有技能。
        </div>
      </div>
    </div>
  );
}
