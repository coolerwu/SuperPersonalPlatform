import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { SkillInfo } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { SkillEditorDialog } from "./SkillEditorDialog";

type SkillsPageProps = {
  skills: SkillInfo[];
  skillsErrors: string[];
  onRefresh: () => Promise<void>;
  onClose: () => void;
};

const SOURCE_LABELS: Record<string, string> = {
  builtin: "系统",
  ai: "AI 创建",
  manual: "手动创建",
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? SOURCE_LABELS.manual;
}

export function SkillsPage({
  skills,
  skillsErrors,
  onRefresh,
  onClose,
}: SkillsPageProps) {
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [editorSkill, setEditorSkill] = useState<SkillInfo | null>(null);
  const [deleting, setDeleting] = useState<SkillInfo | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const builtinSkills = skills.filter((skill) => skill.source === "builtin");
  const createdSkills = skills.filter((skill) => skill.source !== "builtin");

  useEffect(() => {
    void onRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSaved(name: string) {
    setError("");
    setNotice(`已保存 ${name}`);
    await onRefresh();
  }

  async function confirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true);
    setError("");
    setNotice("");
    try {
      await api.deleteSkill(deleting.directory);
      setNotice(`已删除 ${deleting.name}`);
      setDeleting(null);
      await onRefresh();
    } catch (exc) {
      setDeleting(null);
      setError(errorText(exc));
    } finally {
      setDeleteBusy(false);
    }
  }

  function renderSkill(skill: SkillInfo) {
    return (
      <div className="skill-row" key={skill.directory}>
        <span>
          <strong>
            {skill.name}
            <span className={`skill-source ${skill.source}`}>{sourceLabel(skill.source)}</span>
          </strong>
          <small>{skill.description}</small>
          {skill.include_tools.length > 0 && (
            <small>声明工具（本版未接入）：{skill.include_tools.join(", ")}</small>
          )}
          <small className="skill-path">{skill.path}</small>
          {skill.warnings.map((warning) => (
            <small className="skill-warning" key={warning}>{warning}</small>
          ))}
        </span>
        <span className="skill-actions">
          <button
            type="button"
            className="skill-open"
            onClick={() => setEditorSkill(skill)}
          >
            查看
          </button>
          {skill.source !== "builtin" && (
            <button
              type="button"
              className="skill-open skill-delete"
              onClick={() => setDeleting(skill)}
            >
              删除
            </button>
          )}
        </span>
      </div>
    );
  }

  return (
    <main className="skills-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">SKILLS</div>
          <h1>技能</h1>
          <p>技能是 Claude Code 的用户级技能，所有 Agent 自动可见，由模型按描述自行选用。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>技能库</h2>
                <p>新增或修改后，新会话即可使用；正在运行的会话不会切换。</p>
              </div>
              <div className="settings-card-actions">
                <button className="ghost-button" onClick={onRefresh}>刷新技能库</button>
              </div>
            </div>

            <div className="hint">
              技能库：~/.super-personal-platform/claude/skills。Claude Code 会自动发现这里的
              SKILL.md 并交给模型按需调用；平台不提供启用开关，也不提供技能工具。可以用文件工具直接
              编辑这些 SKILL.md，或通过对话创建新技能。
            </div>

            <div className="skill-group">
              <div className="skill-group-head">
                <h3>系统技能</h3>
                <small>平台内置，随版本更新；不可删除。</small>
              </div>
              {builtinSkills.length > 0 ? (
                <div className="skill-list">{builtinSkills.map(renderSkill)}</div>
              ) : (
                <div className="empty-note">还没有系统技能。</div>
              )}
            </div>

            <div className="skill-group">
              <div className="skill-group-head">
                <h3>交互创建</h3>
                <small>通过对话中的文件工具创建或编辑。</small>
              </div>
              {createdSkills.length > 0 ? (
                <div className="skill-list">{createdSkills.map(renderSkill)}</div>
              ) : (
                <div className="empty-note">
                  还没有交互创建的技能。可以让 Agent 用文件工具在
                  ~/.super-personal-platform/claude/skills 下写入 SKILL.md，或直接编辑现有技能。
                </div>
              )}
            </div>

            {skillsErrors.map((message) => (
              <div className="hint skill-warning" key={message}>{message}</div>
            ))}
            {notice && <div className="hint">{notice}</div>}
            {error && <div className="error-banner">{error}</div>}
          </section>
        </div>
      </div>

      <SkillEditorDialog
        skill={editorSkill}
        onClose={() => setEditorSkill(null)}
        onSaved={handleSaved}
      />

      <ConfirmDialog
        open={deleting !== null}
        title={deleting ? `删除技能 ${deleting.name}？` : ""}
        description="会删除整个技能目录；系统技能不能删除。"
        confirmLabel={deleteBusy ? "删除中…" : "删除"}
        cancelLabel="取消"
        danger
        onConfirm={() => void confirmDelete()}
        onClose={() => {
          if (!deleteBusy) setDeleting(null);
        }}
      />
    </main>
  );
}
