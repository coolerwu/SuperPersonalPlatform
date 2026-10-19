import { FormEvent, useEffect, useState } from "react";

import type { Agent, AgentSavePayload, Provider, SkillInfo } from "../types";
import { Select } from "./Select";

type AgentEditorProps = {
  agent: Agent | undefined;
  providers: Provider[];
  saving: boolean;
  workspaceRoot?: string;
  skills?: SkillInfo[];
  skillsErrors?: string[];
  skillsRoot?: string;
  onRefreshSkills?: () => void;
  onSave: (payload: AgentSavePayload) => Promise<void>;
};

export function AgentEditor({
  agent,
  providers,
  saving,
  workspaceRoot,
  skills,
  skillsErrors,
  skillsRoot,
  onRefreshSkills,
  onSave,
}: AgentEditorProps) {
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [providerId, setProviderId] = useState("");
  const [autoMemory, setAutoMemory] = useState(true);
  const [enabledSkills, setEnabledSkills] = useState<string[]>([]);
  const [error, setError] = useState("");

  const enabledSkillsKey = agent?.enabled_skills?.join("|") ?? "";

  useEffect(() => {
    setName(agent?.name ?? "");
    setInstructions(agent?.instructions ?? "");
    setProviderId(agent?.provider_id ?? providers[0]?.id ?? "");
    setAutoMemory(agent?.auto_memory ?? true);
    setEnabledSkills(agent?.enabled_skills ?? []);
    setError("");
  }, [
    agent?.id,
    agent?.name,
    agent?.instructions,
    agent?.provider_id,
    agent?.auto_memory,
    enabledSkillsKey,
    providers,
  ]);

  if (!agent) {
    return <div className="empty-note">选择一个 Agent 后编辑。</div>;
  }

  const installed = new Set((skills ?? []).map((skill) => skill.name));
  const missingSkills = enabledSkills.filter((name) => !installed.has(name));

  function toggleSkill(skillName: string, checked: boolean) {
    setEnabledSkills((current) => {
      if (checked) {
        return current.includes(skillName) ? current : [...current, skillName];
      }
      return current.filter((item) => item !== skillName);
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setError("");
    try {
      await onSave({
        name: name.trim(),
        instructions,
        provider_id: providerId,
        auto_memory: autoMemory,
        enabled_skills: enabledSkills,
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <form className="editor" onSubmit={submit}>
      <label className="field-label" htmlFor="agent-name">名称</label>
      <input id="agent-name" value={name} onChange={(event) => setName(event.target.value)} />

      <div className="field-label">Provider</div>
      <Select
        value={providerId}
        options={providers.map((provider) => ({
          value: provider.id,
          label: provider.name,
          description: provider.model,
        }))}
        onChange={setProviderId}
        ariaLabel="Provider"
      />

      <div className="field-label">工作区</div>
      <div className="hint workspace-path-preview">
        机器目录：{workspaceRoot || "~/.super-personal-platform/workspaces"}/{agent.id}
      </div>

      <label className="field-label" htmlFor="agent-instructions">Instructions</label>
      <textarea
        id="agent-instructions"
        rows={12}
        value={instructions}
        onChange={(event) => setInstructions(event.target.value)}
        placeholder="你是一个……"
      />

      <div className="field-label">技能</div>
      <div className="hint skills-toolbar">
        <span className="workspace-path-preview">
          技能库：{skillsRoot || "~/.super-personal-platform/skills"}
        </span>
        {onRefreshSkills && (
          <button type="button" className="ghost-button ghost-small" onClick={onRefreshSkills}>
            刷新
          </button>
        )}
      </div>
      <div className="hint">
        只有启用的技能会出现在系统提示词。读取 /skills/&lt;name&gt;/SKILL.md 后，该技能声明的工具才会解锁；
        文件工具始终可用。
      </div>

      <div className="skill-list">
        {(skills ?? []).map((skill) => (
          <label className="toggle-row" htmlFor={`agent-skill-${skill.name}`} key={skill.name}>
            <input
              id={`agent-skill-${skill.name}`}
              type="checkbox"
              checked={enabledSkills.includes(skill.name)}
              disabled={!skill.can_enable}
              onChange={(event) => toggleSkill(skill.name, event.target.checked)}
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
        {missingSkills.map((skillName) => (
          <label className="toggle-row" htmlFor={`agent-skill-missing-${skillName}`} key={skillName}>
            <input
              id={`agent-skill-missing-${skillName}`}
              type="checkbox"
              checked
              onChange={() => toggleSkill(skillName, false)}
            />
            <span>
              <strong>{skillName}</strong>
              <small className="skill-warning">技能目录中未找到；取消勾选可移除。</small>
            </span>
          </label>
        ))}
      </div>
      {(skills ?? []).length === 0 && missingSkills.length === 0 && (
        <div className="empty-note">
          还没有安装技能。把 SKILL.md 放到 {skillsRoot || "~/.super-personal-platform/skills"}/&lt;name&gt;/
          后点刷新。
        </div>
      )}
      {(skillsErrors ?? []).map((message) => (
        <div className="hint skill-warning" key={message}>{message}</div>
      ))}

      <label className="toggle-row" htmlFor="agent-auto-memory">
        <input
          id="agent-auto-memory"
          type="checkbox"
          checked={autoMemory}
          onChange={(event) => setAutoMemory(event.target.checked)}
        />
        <span>
          <strong>自动学习记忆</strong>
          <small>每次 Run 完成后提取 Agent 专属长期记忆</small>
        </span>
      </label>

      {error && <div className="error-banner">{error}</div>}
      <button className="primary" type="submit" disabled={saving || !name.trim()}>
        {saving ? "保存中…" : "保存 Agent"}
      </button>
      <div className="hint">修改在下一轮对话生效；当前 session 的 checkpoint 不会被清空。</div>
    </form>
  );
}
