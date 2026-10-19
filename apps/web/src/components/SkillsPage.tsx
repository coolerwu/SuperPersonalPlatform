import { useEffect, useState } from "react";

import { errorText } from "../api";
import type { Agent, SkillInfo } from "../types";

type SkillsPageProps = {
  agent: Agent | undefined;
  skills: SkillInfo[];
  skillsErrors: string[];
  onRefresh: () => void;
  onSaveEnabled: (names: string[]) => Promise<void>;
  onClose: () => void;
};

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

  const enabled = agent?.enabled_skills ?? [];
  const installed = new Set(skills.map((skill) => skill.name));
  const missing = enabled.filter((name) => !installed.has(name));

  useEffect(() => {
    setNotice("");
    setError("");
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

  return (
    <main className="skills-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">SKILLS</div>
          <h1>技能</h1>
          <p>为当前 Agent 启用技能；读取 SKILL.md 后，技能工具才会解锁。</p>
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
                  技能库：~/.super-personal-platform/skills。只有启用的技能会出现在系统提示里；
                  未读取 SKILL.md 前，技能工具不可见也不可调用。技能声明的工具还需要在「工具」页面为当前
                  Agent 启用。文件工具始终可用。
                </div>
                <div className="skill-list">
                  {skills.map((skill) => (
                    <label className="toggle-row" htmlFor={`skills-page-${skill.name}`} key={skill.name}>
                      <input
                        id={`skills-page-${skill.name}`}
                        type="checkbox"
                        checked={enabled.includes(skill.name)}
                        disabled={saving || !skill.can_enable}
                        onChange={(event) => void toggle(skill.name, event.target.checked)}
                      />
                      <span>
                        <strong>{skill.name}</strong>
                        <small>{skill.description}</small>
                        {skill.include_tools.length > 0 && (
                          <small>提供工具：{skill.include_tools.join(", ")}</small>
                        )}
                        <small className="skill-path">{skill.path}</small>
                        {skill.warnings.map((warning) => (
                          <small className="skill-warning" key={warning}>{warning}</small>
                        ))}
                      </span>
                    </label>
                  ))}
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
                {skills.length === 0 && missing.length === 0 && (
                  <div className="empty-note">
                    还没有安装技能。把 SKILL.md 放到 ~/.super-personal-platform/skills/&lt;name&gt;/
                    后点「刷新技能库」。
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
    </main>
  );
}
