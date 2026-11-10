import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { Agent, SkillInfo } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { SkillEditorDialog } from "./SkillEditorDialog";

type SkillsPageProps = {
  agent: Agent | undefined;
  skills: SkillInfo[];
  skillsErrors: string[];
  onRefresh: () => Promise<void>;
  onSaveEnabled: (names: string[]) => Promise<void>;
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
  agent,
  skills,
  skillsErrors,
  onRefresh,
  onSaveEnabled,
  onClose,
}: SkillsPageProps) {
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [editorSkill, setEditorSkill] = useState<SkillInfo | null>(null);
  const [deleting, setDeleting] = useState<SkillInfo | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const enabled = agent?.enabled_skills ?? [];
  const installed = new Set(skills.map((skill) => skill.name));
  const missing = enabled.filter((name) => !installed.has(name));
  const builtinSkills = skills.filter((skill) => skill.source === "builtin");
  const createdSkills = skills.filter((skill) => skill.source !== "builtin");

  useEffect(() => {
    setNotice("");
    setError("");
  }, [agent?.id]);

  useEffect(() => {
    void onRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent?.id]);

  async function toggle(name: string, checked: boolean) {
    if (!agent || saving) return;
    const next = checked
      ? [...enabled.filter((item) => item !== name), name]
      : enabled.filter((item) => item !== name);
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await onSaveEnabled(next);
      setNotice(`${checked ? "已启用" : "已停用"} ${name}`);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

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
      <label
        className="toggle-row skill-row"
        htmlFor={`skills-page-${skill.directory}`}
        key={skill.directory}
      >
        <input
          id={`skills-page-${skill.directory}`}
          type="checkbox"
          checked={enabled.includes(skill.name)}
          disabled={saving || !skill.can_enable}
          onChange={(event) => void toggle(skill.name, event.target.checked)}
        />
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
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              setEditorSkill(skill);
            }}
          >
            查看
          </button>
          {skill.source !== "builtin" && (
            <button
              type="button"
              className="skill-open skill-delete"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                setDeleting(skill);
              }}
            >
              删除
            </button>
          )}
        </span>
      </label>
    );
  }

  return (
    <main className="skills-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">SKILLS</div>
          <h1>技能</h1>
          <p>为当前 Agent 维护技能启用列表；本版技能是文件说明，不改变 Agent 可用的工具。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>返回对话</button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>当前 Agent：{agent?.name ?? "—"}</h2>
                <p>跟随左上角选中的 Agent；启停会立即保存，下一轮对话生效。</p>
              </div>
              <div className="settings-card-actions">
                <button className="ghost-button" onClick={onRefresh}>刷新技能库</button>
              </div>
            </div>

            {!agent && <div className="empty-note">还没有 Agent。</div>}
            {agent && (
              <>
                <div className="hint">
                  技能库：~/.super-personal-platform/skills。技能是文件说明，启用列表仅为后续工具层
                  准备并保存到当前 Agent；本版平台技能工具与 skill_save 尚未接入 SDK，启用不会改变
                  当前可用工具。Claude Code 自带的 Skill 工具可用，但它不读取这里的平台技能库。
                  Agent 可以在对话中用文件工具查看或修改 skills/ 下的 SKILL.md。
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
                    <small>通过对话中的文件工具创建或编辑；启用列表仅保存偏好。</small>
                  </div>
                  {createdSkills.length > 0 ? (
                    <div className="skill-list">{createdSkills.map(renderSkill)}</div>
                  ) : (
                    <div className="empty-note">
                      还没有交互创建的技能。可以让 Agent 用文件工具在 skills/ 下写入 SKILL.md，
                      或直接编辑现有技能。
                    </div>
                  )}
                </div>

                {missing.length > 0 && (
                  <div className="skill-group">
                    <div className="skill-group-head">
                      <h3>已丢失</h3>
                      <small>启用列表里有、但技能目录中已不存在的技能。</small>
                    </div>
                    <div className="skill-list">
                      {missing.map((skillName) => (
                        <label className="toggle-row" htmlFor={`skills-page-missing-${skillName}`} key={skillName}>
                          <input
                            id={`skills-page-missing-${skillName}`}
                            type="checkbox"
                            checked
                            disabled={saving}
                            onChange={() => void toggle(skillName, false)}
                          />
                          <span>
                            <strong>{skillName}</strong>
                            <small className="skill-warning">技能目录中未找到；取消勾选可移除。</small>
                          </span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}
                {skillsErrors.map((message) => (
                  <div className="hint skill-warning" key={message}>{message}</div>
                ))}
                {notice && <div className="hint">{notice}</div>}
                {error && <div className="error-banner">{error}</div>}
              </>
            )}
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
        description="会删除整个技能目录，并从所有 Agent 的启用列表中移除；系统技能不能删除。"
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
