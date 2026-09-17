import React, { useEffect, useMemo, useState } from "react";
import { BookOpen, FileText, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import { parseConfigDraft } from "./configEditor.jsx";

export const SKILL_FILE_NAME = "SKILL.md";
export const SKILL_ID_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
export const MAX_SKILL_ID_CHARS = 64;
export const MAX_SKILL_DESCRIPTION_CHARS = 1024;
export const SKILL_CONTRACT_START = "<!-- BEGIN USER CONTRACT -->";
export const SKILL_CONTRACT_END = "<!-- END USER CONTRACT -->";

export function agentSkillsDir(agentId) {
  return `agents/${agentId}/workspace/skills`;
}

export function skillFilePath(agentId, skillId) {
  return `${agentSkillsDir(agentId)}/${skillId}/${SKILL_FILE_NAME}`;
}

export function parseSkillFrontmatter(content) {
  const lines = String(content || "").split("\n");
  if (lines[0]?.trim() !== "---") {
    return { error: "缺少 YAML frontmatter：SKILL.md 必须以 --- 开头" };
  }
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (end < 0) return { error: "frontmatter 未闭合：缺少结尾的 ---" };
  const fields = {};
  for (const line of lines.slice(1, end)) {
    const match = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if (value.length > 1 && ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'")))) {
      value = value.slice(1, -1);
    }
    fields[match[1]] = value;
  }
  return { name: fields.name || "", description: fields.description || "" };
}

export function validateSkillDocument({ content, skillId }) {
  const parsed = parseSkillFrontmatter(content);
  if (parsed.error) return parsed.error;
  const name = String(parsed.name || "").trim();
  if (!name) return "frontmatter 缺少 name";
  if (name !== skillId) return `frontmatter 的 name（${name}）必须与目录名 ${skillId} 一致`;
  if (!SKILL_ID_PATTERN.test(name) || name.length > MAX_SKILL_ID_CHARS) {
    return `name 只能用小写字母、数字和连字符，且不超过 ${MAX_SKILL_ID_CHARS} 字符`;
  }
  const description = String(parsed.description || "").trim();
  if (!description) return "frontmatter 缺少 description";
  if (description.length > MAX_SKILL_DESCRIPTION_CHARS) {
    return `description 不能超过 ${MAX_SKILL_DESCRIPTION_CHARS} 字符`;
  }
  return "";
}

export function validateSkillId(skillId) {
  const value = String(skillId || "").trim();
  if (!value) return "请填写技能 ID";
  if (!SKILL_ID_PATTERN.test(value)) return "技能 ID 只能用小写字母、数字和连字符，且以字母或数字开头";
  if (value.length > MAX_SKILL_ID_CHARS) return `技能 ID 不能超过 ${MAX_SKILL_ID_CHARS} 字符`;
  return "";
}

export function skillTemplate({ name, description }) {
  return [
    "---",
    `name: ${name}`,
    `description: ${description}`,
    "---",
    "",
    `# ${name}`,
    "",
    "## 何时使用",
    "- 用户要求……",
    "",
    "## 步骤",
    "1. ",
    "",
  ].join("\n");
}

function formatBytes(size) {
  const value = Number(size) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("zh-CN", { hour12: false });
}

export function SkillsPage({ api }) {
  const [agents, setAgents] = useState([]);
  const [groups, setGroups] = useState([]);
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState("");
  const [loadedContent, setLoadedContent] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(null);
  const [createError, setCreateError] = useState("");

  async function loadSkills(agent) {
    let entries = [];
    try {
      const listing = await api("/api/workspace/list", {
        method: "POST",
        body: JSON.stringify({ path: agentSkillsDir(agent.id) }),
      });
      entries = (listing.entries || []).filter((entry) => entry.type === "directory");
    } catch (err) {
      if (err.status !== 404) throw err;
    }
    return Promise.all(entries.map(async (entry) => {
      const skillId = entry.name;
      let content = "";
      let files = [];
      let readError = "";
      try {
        const file = await api("/api/workspace/read", {
          method: "POST",
          body: JSON.stringify({ path: skillFilePath(agent.id, skillId) }),
        });
        content = file.content || "";
      } catch (err) {
        readError = err.message || "读取失败";
      }
      try {
        const listing = await api("/api/workspace/list", {
          method: "POST",
          body: JSON.stringify({ path: `${agentSkillsDir(agent.id)}/${skillId}` }),
        });
        files = listing.entries || [];
      } catch (err) {
        if (err.status !== 404) throw err;
      }
      const parsed = content ? parseSkillFrontmatter(content) : { error: readError || "缺少 SKILL.md" };
      const invalid = content ? validateSkillDocument({ content, skillId }) : (readError || "缺少 SKILL.md");
      return {
        id: skillId,
        path: skillFilePath(agent.id, skillId),
        content,
        files,
        name: parsed.name || skillId,
        description: parsed.description || "",
        invalid,
        contract: content.includes(SKILL_CONTRACT_START),
        modifiedAt: entry.modified_at,
        missing: Boolean(readError) && !content,
      };
    }));
  }

  async function load(preferred = null) {
    setError("");
    try {
      const data = await api("/api/workspace/read", {
        method: "POST",
        body: JSON.stringify({ path: "config.yaml" }),
      });
      const parsed = parseConfigDraft(data.content || "");
      const nextAgents = parsed.config?.agents?.definitions || [];
      const nextGroups = [];
      for (const agent of nextAgents) {
        nextGroups.push({ agent, skills: await loadSkills(agent) });
      }
      setAgents(nextAgents);
      setGroups(nextGroups);
      const target = preferred || selected;
      if (target) {
        const group = nextGroups.find((item) => item.agent.id === target.agentId);
        const skill = group?.skills.find((item) => item.id === target.skillId);
        if (skill) selectSkill(target, skill);
        else {
          setSelected(null);
          setDraft("");
          setLoadedContent("");
        }
      }
    } catch (err) {
      setError(err.message || "读取技能列表失败");
    }
  }

  function selectSkill(target, skill) {
    setSelected(target);
    setDraft(skill.content);
    setLoadedContent(skill.content);
    setError("");
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentSkill = useMemo(() => {
    if (!selected) return null;
    return groups.find((item) => item.agent.id === selected.agentId)?.skills.find((item) => item.id === selected.skillId) || null;
  }, [groups, selected]);

  const dirty = Boolean(currentSkill) && draft !== loadedContent;
  const validation = currentSkill ? validateSkillDocument({ content: draft, skillId: currentSkill.id }) : "";

  async function saveSkill() {
    if (!currentSkill || busy) return;
    const problem = validateSkillDocument({ content: draft, skillId: currentSkill.id });
    if (problem) {
      setError("");
      setMessage("");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api("/api/workspace/write", {
        method: "PUT",
        body: JSON.stringify({ path: currentSkill.path, content: draft }),
      });
      setLoadedContent(draft);
      setMessage("已保存，脚本/规则会在下一次 Agent 运行时生效。");
      await load(selected);
    } catch (err) {
      setError(err.message || "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function createSkill() {
    const agentId = creating?.agentId || "";
    const skillId = String(creating?.skillId || "").trim();
    const description = String(creating?.description || "").trim();
    const problem = validateSkillId(skillId) || (!description ? "请填写 description" : "");
    if (!agentId) {
      setCreateError("请选择 Agent");
      return;
    }
    if (problem) {
      setCreateError(problem);
      return;
    }
    const existing = groups.find((item) => item.agent.id === agentId)?.skills || [];
    if (existing.some((skill) => skill.id === skillId)) {
      setCreateError(`Agent ${agentId} 已经有同名技能 ${skillId}`);
      return;
    }
    setBusy(true);
    setCreateError("");
    try {
      await api("/api/workspace/write", {
        method: "PUT",
        body: JSON.stringify({
          path: skillFilePath(agentId, skillId),
          content: skillTemplate({ name: skillId, description }),
          create: true,
        }),
      });
      setCreating(null);
      setSelected({ agentId, skillId });
      setMessage(`已创建技能 ${skillId}，下一次 Agent 运行时生效。`);
      await load({ agentId, skillId });
    } catch (err) {
      setCreateError(err.message || "创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function deleteSkill(skill, agentId) {
    if (busy) return;
    if (!window.confirm(`删除技能 ${skill.id}？整个 ${agentSkillsDir(agentId)}/${skill.id} 目录（含辅助文件）会一并删除，无法恢复。`)) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api("/api/workspace/delete", {
        method: "POST",
        body: JSON.stringify({ path: `${agentSkillsDir(agentId)}/${skill.id}` }),
      });
      if (selected?.agentId === agentId && selected?.skillId === skill.id) {
        setSelected(null);
        setDraft("");
        setLoadedContent("");
      }
      setMessage(`已删除技能 ${skill.id}`);
      await load(null);
    } catch (err) {
      setError(err.message || "删除失败");
    } finally {
      setBusy(false);
    }
  }

  const auxiliaryFiles = (currentSkill?.files || []).filter((entry) => entry.name !== SKILL_FILE_NAME);

  return (
    <section className="console-screen skills-screen">
      <div className={`workspace-feedback ${message || error ? "has-feedback" : ""}`}>
        {message ? <p className="ok">{message}</p> : null}
        {error ? <p className="error" role="alert">{error}</p> : null}
      </div>

      <div className="skills-layout">
        <section className="panel skills-groups">
          <div className="panel-title">
            <div>
              <span>Agent 技能</span>
              <small>
                {agents.length} 个 Agent · {groups.reduce((total, group) => total + group.skills.length, 0)} 个技能 ·
                保存在各 Agent 私有工作区，下一次运行时生效
              </small>
            </div>
            <button className="icon-button" title="刷新技能列表" onClick={() => load(null)} disabled={busy}>
              <RefreshCw size={15} />
            </button>
          </div>
          <div className="skills-group-list">
            {groups.length === 0 ? <div className="empty-state">配置里还没有 Agent。</div> : null}
            {groups.map((group) => (
              <div className="skills-group" key={group.agent.id}>
                <div className="skills-group-header">
                  <div className="skills-group-title">
                    <strong>{group.agent.name || group.agent.id}</strong>
                    <small>{group.agent.id} · {group.skills.length} 个技能</small>
                  </div>
                  <button
                    type="button"
                    className="icon-button"
                    title={`为 ${group.agent.id} 新建技能`}
                    disabled={busy}
                    onClick={() => { setCreateError(""); setCreating({ agentId: group.agent.id, skillId: "", description: "" }); }}
                  >
                    <Plus size={14} />
                  </button>
                </div>
                {group.skills.length === 0 ? (
                  <p className="skills-muted">还没有技能。可以让 Agent 在对话里创建，或点右侧 + 新建。</p>
                ) : (
                  group.skills.map((skill) => (
                    <div
                      key={skill.id}
                      className={`skills-item ${selected?.agentId === group.agent.id && selected?.skillId === skill.id ? "selected" : ""}`}
                    >
                      <button
                        type="button"
                        className="skills-item-main"
                        onClick={() => { setMessage(""); selectSkill({ agentId: group.agent.id, skillId: skill.id }, skill); }}
                      >
                        <BookOpen size={15} />
                        <span>{skill.name}</span>
                        <small>{skill.description || "（缺少 description）"}</small>
                        <time>{formatTime(skill.modifiedAt * 1000)}</time>
                      </button>
                      {skill.invalid ? <em className="skills-badge" title={skill.invalid}>不生效</em> : null}
                      {skill.contract ? <em className="skills-badge contract" title="包含用户硬约束区块">契约</em> : null}
                      <button
                        type="button"
                        className="icon-button delete-button"
                        title={`删除技能 ${skill.id}`}
                        disabled={busy}
                        onClick={() => deleteSkill(skill, group.agent.id)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="panel skills-editor">
          <div className="panel-title">
            <div>
              <span>{currentSkill ? currentSkill.path : "选择技能"}</span>
              <small>
                {currentSkill
                  ? `${currentSkill.id} · ${dirty ? "有未保存修改" : "已保存"}`
                  : "只编辑 SKILL.md；辅助文件请到工作目录页维护"}
              </small>
            </div>
            {currentSkill ? (
              <div className="skills-editor-actions">
                <button type="button" onClick={saveSkill} disabled={busy || !dirty}>
                  <Save size={15} />
                  保存
                </button>
              </div>
            ) : null}
          </div>
          {currentSkill?.contract ? (
            <div className="skills-contract-note" role="status">
              <strong>{SKILL_CONTRACT_START} 区块</strong>
              <span>这是用户硬约束：Agent 的文件工具不能修改或删除，只有在这个页面（或工作目录页）才能调整。</span>
            </div>
          ) : null}
          {currentSkill ? (
            <>
              {validation && draft !== loadedContent ? (
                <p className="skills-validation" role="alert">保存前请修正：{validation}</p>
              ) : null}
              <textarea
                className="skills-textarea"
                aria-label="SKILL.md"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                spellCheck={false}
              />
              <div className="skills-editor-footer">
                <span>
                  {auxiliaryFiles.length
                    ? `辅助文件：${auxiliaryFiles.map((entry) => `${entry.name}（${formatBytes(entry.size)}）`).join("、")}`
                    : "没有辅助文件"}
                </span>
                <span>保存后在下一次 Agent 运行时生效</span>
              </div>
            </>
          ) : (
            <div className="skills-empty-editor">
              <FileText size={26} />
              <strong>选择左侧技能开始编辑</strong>
              <span>技能文件固定为 skills/{`{skill_id}`}/SKILL.md；frontmatter 的 name 必须与目录名一致。</span>
            </div>
          )}
        </section>
      </div>

      {creating ? (
        <div className="group-modal-backdrop">
          <form
            className="panel group-editor"
            aria-label="新建技能"
            onSubmit={(event) => { event.preventDefault(); createSkill(); }}
          >
            <div className="dialog-header">
              <div>
                <strong>新建技能</strong>
                <span>写入 agents/{creating.agentId || "{agent_id}"}/workspace/skills/{creating.skillId || "{skill_id}"}/SKILL.md</span>
              </div>
              <button type="button" className="icon-button" aria-label="关闭" disabled={busy} onClick={() => setCreating(null)}>
                <X size={16} />
              </button>
            </div>
            <div className="group-editor-body">
              <label>
                Agent
                <select
                  value={creating.agentId}
                  disabled={busy}
                  onChange={(event) => setCreating((current) => ({ ...current, agentId: event.target.value }))}
                >
                  {agents.map((agent) => (
                    <option key={agent.id} value={agent.id}>{agent.name || agent.id}</option>
                  ))}
                </select>
              </label>
              <label>
                技能 ID
                <input
                  value={creating.skillId}
                  disabled={busy}
                  placeholder="web-research"
                  onChange={(event) => setCreating((current) => ({ ...current, skillId: event.target.value }))}
                />
                <small>小写字母、数字和连字符，作为目录名与 frontmatter 的 name。</small>
              </label>
              <label>
                描述
                <input
                  value={creating.description}
                  disabled={busy}
                  placeholder="这个技能解决什么问题"
                  onChange={(event) => setCreating((current) => ({ ...current, description: event.target.value }))}
                />
                <small>写入 frontmatter 的 description，Agent 用它判断何时加载技能。</small>
              </label>
              {createError ? <p className="error" role="alert">{createError}</p> : null}
            </div>
            <div className="dialog-footer">
              <button type="button" disabled={busy} onClick={() => setCreating(null)}>取消</button>
              <button type="submit" className="primary" disabled={busy}>创建</button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
