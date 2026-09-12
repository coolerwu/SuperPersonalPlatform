import React, { useEffect, useRef, useState } from "react";
import { Bot, Plus, X, Users, Square, Settings, Archive } from "lucide-react";
import { ChatComposer, ChatMessageList } from "./chatComponents.jsx";
import { useGroupRunEvents } from "./chatRuntime.js";
import { parseConfigDraft } from "./configEditor.jsx";

const newId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(16).slice(2)}`;
export function mentionPosition(content, token) {
  let index = content.indexOf(token);
  while (index >= 0) {
    const after = content[index + token.length];
    if (after === undefined || /\s/.test(after)) return index;
    index = content.indexOf(token, index + token.length);
  }
  return -1;
}
const ACTIVE = ["running", "waiting_approval", "paused", "stopping"];
const statusLabels = { running: "执行中", waiting_approval: "等待审批", paused: "已暂停", stopping: "停止中", completed: "已完成", cancelled: "已停止" };

export function ChatGroupsPage({ api }) {
  const [groups, setGroups] = useState([]);
  const [agents, setAgents] = useState([]);
  const [selected, setSelected] = useState("");
  const [group, setGroup] = useState(null);
  const [draft, setDraft] = useState("");
  const [mentions, setMentions] = useState([]);
  const [editor, setEditor] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedRef = useRef("");
  const submitRef = useRef(false);
  const requestRef = useRef(null);
  const execution = group?.executions?.at(-1);
  const active = Boolean(execution && ACTIVE.includes(execution.status));
  const locked = active || busy || Boolean(group?.archived);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api("/api/chat-groups"),
      api("/api/workspace/read", { method: "POST", body: JSON.stringify({ path: "config.yaml" }) }),
    ]).then(([list, config]) => {
      if (cancelled) return;
      setGroups(list.groups || []);
      setAgents(parseConfigDraft(config.content || "").config?.agents?.definitions || []);
      const first = list.groups?.find((item) => !item.archived) || list.groups?.[0];
      if (first) selectGroup(first.id);
    }).catch((exc) => { if (!cancelled) setError(exc.message); });
    return () => { cancelled = true; };
  }, [api]);

  function selectGroup(id) {
    selectedRef.current = id;
    setSelected(id);
    setGroup(null);
    setDraft("");
    setMentions([]);
    setError("");
    requestRef.current = null;
  }

  useEffect(() => {
    if (!selected) return undefined;
    let cancelled = false;
    let timer;
    async function poll() {
      try {
        const data = await api(`/api/chat-groups/${selected}`);
        if (!cancelled) setGroup((current) => JSON.stringify(current) === JSON.stringify(data) ? current : data);
      } catch (exc) { if (!cancelled) setError(exc.message); }
      if (!cancelled) timer = window.setTimeout(poll, 1000);
    }
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [selected, api]);

  async function refresh(id) {
    const [data, list] = await Promise.all([api(`/api/chat-groups/${id}`), api("/api/chat-groups")]);
    if (selectedRef.current === id) setGroup(data);
    setGroups(list.groups || []);
  }

  async function perform(operation) {
    if (submitRef.current) return;
    submitRef.current = true;
    setBusy(true);
    setError("");
    try { await operation(); }
    catch (exc) { setError(exc.message); }
    finally { submitRef.current = false; setBusy(false); }
  }

  async function send(automatic = false) {
    if (!group || locked || !draft.trim()) return;
    const id = group.id;
    const content = draft.trim();
    const ordered = mentions.filter((m) => mentionPosition(content, m.token) >= 0).sort((a, b) => mentionPosition(content, a.token) - mentionPosition(content, b.token)).map((m) => m.id);
    const signature = JSON.stringify([id, content, ordered, automatic]);
    if (requestRef.current?.signature !== signature) requestRef.current = { signature, id: newId() };
    await perform(async () => {
      await api(`/api/chat-groups/${id}/${automatic ? "collaborations" : "messages"}`, {
        method: "POST", body: JSON.stringify({ content, mentions: ordered, client_message_id: requestRef.current.id }),
      });
      if (selectedRef.current === id) { setDraft(""); setMentions([]); requestRef.current = null; }
      await refresh(id);
    });
  }

  async function control(action) {
    const id = group.id;
    const executionId = execution.id;
    await perform(async () => {
      await api(`/api/chat-groups/${id}/collaborations/${executionId}/${action}`, {
        method: "POST", ...(action === "continue" ? { body: JSON.stringify({ client_message_id: newId() }) } : {}),
      });
      await refresh(id);
    });
  }

  async function decide(runId, decision, message) {
    const id = group.id;
    await perform(async () => {
      await api(`/api/runs/${runId}/resume`, { method: "POST", body: JSON.stringify({ decision, message }) });
      await refresh(id);
    });
  }

  function addMention(member) {
    const token = `@${member.name} `;
    setDraft((value) => value.replace(/@[^@\n]*$/, "") + token);
    setMentions((current) => [...current.filter((item) => item.id !== member.id), { id: member.id, token: token.trim() }]);
  }

  const messages = group?.messages || [];
  const run = group?.active_run;
  const step = execution?.current_step;
  const live = useGroupRunEvents(api, run);
  const displayMessages = run && step && !messages.some((m) => m.run_id === run.run_id) ? [...messages, {
    id: run.run_id, role: "assistant", speaker: step.name, run_id: run.run_id,
    content: run.result?.content || live?.content || run.partial?.content || "",
    thinking: live?.thinking?.length ? live.thinking : run.partial?.thinking || [], thinkingCollapsed: false,
    streaming: ["queued", "running"].includes(run.state?.status),
    failed: ["failed", "cancelled"].includes(run.state?.status),
    approval: live?.approval !== undefined ? live.approval : run.approval?.status === "pending" ? run.approval.request : null,
  }] : messages;
  const mentionQuery = draft.match(/@([^@\n]*)$/)?.[1];
  const mentionOptions = mentionQuery !== undefined && !locked ? (group?.members || []).filter((m) => m.name.includes(mentionQuery)) : [];

  return <section className="group-workspace">
    <aside className="panel group-list">
      <div className="group-local-toolbar"><strong><Users size={16} /> 聊天室</strong><button aria-label="新建群聊" title="新建群聊" disabled={busy} onClick={() => setEditor({ name: "", members: [], host_member_id: "", archived: false })}><Plus size={16} /></button></div>
      {groups.map((item) => <button key={item.id} className={`group-list-item ${selected === item.id ? "selected" : ""}`} onClick={() => selectGroup(item.id)}><span>{item.name}</span><small>{item.archived ? "已归档" : "群聊"}</small></button>)}
      {!groups.length ? <p className="group-muted">创建聊天室，邀请不同角色一起干活。</p> : null}
    </aside>
    <section className="panel chat-panel group-chat-panel">
      {group ? <>
        <div className="group-local-toolbar"><strong>{group.name}</strong><div className="group-controls">
          <span className={`group-state ${active ? "active" : ""}`}>{group.archived ? "已归档" : statusLabels[execution?.status] || "就绪"}{execution?.automatic ? ` · ${execution.round}/3 轮` : ""}</span>
          {active ? <button onClick={() => control("stop")} disabled={busy}><Square size={14} />停止</button> : null}
          {execution?.status === "paused" ? <button onClick={() => control("retry")} disabled={busy}>重试步骤</button> : null}
          {!active && execution?.automatic && execution.status === "completed" && !group.archived ? <button onClick={() => control("continue")} disabled={busy}>继续协作</button> : null}
          <button aria-label="编辑群聊" title="编辑群聊" disabled={active || busy} onClick={() => setEditor({ _groupId: group.id, name: group.name, members: group.members, host_member_id: group.host_member_id, archived: group.archived })}><Settings size={16} /></button>
        </div></div>
        <ChatMessageList messages={displayMessages} onDecision={decide} onError={setError} emptyTitle="把任务交给合适的角色" emptyDescription="@ 点名接力；不点名由主持回复。开始协作后，主持会组织最多三轮分工。" />
        {execution?.error ? <div className="error chat-error">{execution.error}</div> : null}
        {error ? <div role="alert" className="error chat-error">{error}</div> : null}
        <div className="group-input-area">
          {mentions.length ? <div className="group-mention-chips">{mentions.filter((m) => mentionPosition(draft, m.token) >= 0).map((m) => <span key={m.id}>{m.token}</span>)}</div> : null}
          {mentionOptions.length ? <div className="group-mention-menu" role="listbox" aria-label="选择群成员">{mentionOptions.map((member) => <button role="option" aria-selected="false" key={member.id} onClick={() => addMention(member)}><Bot size={15} />{member.name}</button>)}</div> : null}
          <ChatComposer value={draft} onChange={setDraft} onSend={() => send(false)} busy={locked} disabled={locked} placeholder="输入 @ 选择角色，或直接与主持交流">
            <button className="group-collaborate" onClick={() => send(true)} disabled={locked || !draft.trim()}>开始协作</button>
          </ChatComposer>
        </div>
      </> : <div className="chat-empty"><Users size={30} /><strong>{selected ? "正在读取聊天室…" : "选择或创建聊天室"}</strong>{error ? <p role="alert" className="error">{error}</p> : null}</div>}
    </section>
    <aside className="panel group-members"><div className="group-local-toolbar"><strong>成员 <small>{group?.members.length || 0}</small></strong></div>
      {group?.members.map((member) => <div className="group-member" key={member.id}><span className="group-avatar"><Bot size={19} /></span><div><strong>{member.name}</strong><small>{member.id === group.host_member_id ? "主持 · " : ""}{agents.find((a) => a.id === member.agent_id)?.name || member.agent_id}</small>{member.prompt ? <p>{member.prompt}</p> : null}</div></div>)}
      <p className="group-muted">角色沿用基础 Agent 的工具、文件和长期记忆；群内对话状态独立。</p>
    </aside>
    {editor ? <GroupEditor value={editor} agents={agents} busy={busy} onClose={() => setEditor(null)} onSave={(definition) => perform(async () => {
      // The editor carries its target explicitly; creating is never inferred from member identity.
      const target = editor._groupId;
      const data = await api(`/api/chat-groups${target ? `/${target}` : ""}`, { method: target ? "PUT" : "POST", body: JSON.stringify(definition) });
      setEditor(null); selectGroup(data.id); await refresh(data.id);
    })} error={error} /> : null}
  </section>;
}

function GroupEditor({ value, agents, onClose, onSave, busy, error }) {
  const [draft, setDraft] = useState(value);
  function add() {
    if (!agents.length) return;
    const member = { id: newId(), agent_id: agents[0].id, name: `${agents[0].name || agents[0].id} ${draft.members.length + 1}`, prompt: "" };
    setDraft((current) => ({ ...current, members: [...current.members, member], host_member_id: current.host_member_id || member.id }));
  }
  function change(id, key, value) { setDraft((current) => ({ ...current, members: current.members.map((m) => m.id === id ? { ...m, [key]: value } : m) })); }
  return <div className="group-modal-backdrop"><form className="panel group-editor" onSubmit={(event) => { event.preventDefault(); const { _groupId, ...definition } = draft; onSave(definition); }}>
    <div className="group-local-toolbar"><strong>{value._groupId ? "编辑群聊" : "新建群聊"}</strong><button type="button" aria-label="关闭" onClick={onClose} disabled={busy}><X size={18} /></button></div>
    <div className="group-editor-body"><label>群名称<input required maxLength={100} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
      {draft.members.map((member) => <fieldset key={member.id}><legend>{member.name || "群成员"}</legend>
        <label>基础 Agent<select value={member.agent_id} disabled={Boolean(value._groupId && value.members.some((m) => m.id === member.id))} onChange={(event) => change(member.id, "agent_id", event.target.value)}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name || agent.id}</option>)}</select></label>
        <label>群内名称<input required maxLength={80} value={member.name} onChange={(event) => change(member.id, "name", event.target.value)} /></label>
        <label>追加角色 prompt<textarea value={member.prompt} maxLength={20000} placeholder="留空则沿用基础 Agent 人格" onChange={(event) => change(member.id, "prompt", event.target.value)} /></label>
        <div className="group-controls"><label className="group-host-choice"><input type="radio" name="host" checked={draft.host_member_id === member.id} onChange={() => setDraft({ ...draft, host_member_id: member.id })} />设为主持</label><button type="button" onClick={() => setDraft((current) => { const members = current.members.filter((m) => m.id !== member.id); return { ...current, members, host_member_id: current.host_member_id === member.id ? members[0]?.id || "" : current.host_member_id }; })}>移除</button></div>
      </fieldset>)}
      <button type="button" onClick={add} disabled={!agents.length || draft.members.length >= 20}><Plus size={15} />添加成员 / prompt 角色</button>
      {!agents.length ? <p>请先在配置中创建 Agent。</p> : null}
      {value._groupId ? <label className="group-host-choice"><input type="checkbox" checked={draft.archived} onChange={(event) => setDraft({ ...draft, archived: event.target.checked })} /><Archive size={14} />归档此群</label> : null}
      {error ? <p role="alert" className="error">{error}</p> : null}
    </div>
    <div className="group-editor-footer"><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || !draft.members.length}>保存群聊</button></div>
  </form></div>;
}
