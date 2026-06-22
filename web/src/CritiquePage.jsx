import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  BrainCircuit,
  ChevronRight,
  Clock3,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  Scale,
  Send,
  Trash2,
  X
} from "lucide-react";


async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || "请求失败");
    error.status = response.status;
    throw error;
  }
  return data;
}


const EMPTY_FORM = {
  name: "",
  known_scope: "",
  critique_focus: "",
  default_enabled: true
};


function CritiqueStatus({ status }) {
  const labels = {
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    partial: "部分完成"
  };
  return <span className={`critique-status ${status || "queued"}`}>{labels[status] || labels.queued}</span>;
}


function normalizeRun(run) {
  if (!run) return null;
  const turns = Array.isArray(run.turns) && run.turns.length ? run.turns : [{
    id: `t-legacy-${run.id}`,
    question: run.question,
    results: run.results || [],
    judgment: run.judgment || null,
    status: run.status,
    created_at: run.created_at,
    updated_at: run.updated_at
  }];
  const latest = turns[turns.length - 1];
  return {
    ...run,
    title: run.title || turns[0].question,
    question: latest.question,
    results: latest.results || [],
    judgment: latest.judgment || null,
    status: latest.status,
    turns
  };
}


function updateTurn(run, turnId, updater) {
  if (!run) return run;
  const normalized = normalizeRun(run);
  let found = false;
  const turns = normalized.turns.map((turn) => {
    if (turn.id !== turnId) return turn;
    found = true;
    return updater(turn);
  });
  if (!found && turns.length) turns[turns.length - 1] = updater(turns[turns.length - 1]);
  const latest = turns[turns.length - 1];
  return {
    ...normalized,
    question: latest.question,
    results: latest.results || [],
    judgment: latest.judgment || null,
    status: latest.status,
    turns
  };
}


function formatConversationTime(value) {
  if (!value) return "刚刚";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}


function DisciplineEditor({ discipline, onClose, onSave }) {
  const [form, setForm] = useState(discipline ? {
    name: discipline.name,
    known_scope: discipline.known_scope,
    critique_focus: discipline.critique_focus,
    default_enabled: discipline.default_enabled
  } : EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await onSave(form);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="critique-dialog-backdrop" role="presentation">
      <section className="critique-dialog" role="dialog" aria-modal="true" aria-label={discipline ? "编辑学科" : "添加学科"}>
        <header>
          <div>
            <strong>{discipline ? "编辑学科" : "添加学科"}</strong>
            <span>只添加你能理解和判断的批判视角</span>
          </div>
          <button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><X size={17} /></button>
        </header>
        <form onSubmit={submit}>
          <label>
            <span>学科名称</span>
            <input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} />
          </label>
          <label>
            <span>我了解的范围</span>
            <textarea rows={4} value={form.known_scope} onChange={(event) => setForm((current) => ({ ...current, known_scope: event.target.value }))} />
          </label>
          <label>
            <span>重点批判方向</span>
            <textarea rows={4} value={form.critique_focus} onChange={(event) => setForm((current) => ({ ...current, critique_focus: event.target.value }))} />
          </label>
          <label className="critique-toggle-row">
            <span><strong>默认参与</strong><small>后续新问题自动启用这个学科</small></span>
            <input
              type="checkbox"
              aria-label="默认参与"
              checked={form.default_enabled}
              onChange={(event) => setForm((current) => ({ ...current, default_enabled: event.target.checked }))}
            />
          </label>
          {error ? <div className="form-error">{error}</div> : null}
          <footer>
            <button type="button" className="secondary-button" onClick={onClose}>取消</button>
            <button type="submit" className="secondary-button primary-action" disabled={saving || !form.name.trim() || !form.known_scope.trim() || !form.critique_focus.trim()}>
              {saving ? "保存中" : "保存学科"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}


function CritiqueTurnView({ turn, index, sending, onRetry }) {
  const completedResults = (turn.results || []).filter((item) => item.status === "completed");
  return (
    <article className="critique-turn" aria-label={`第 ${index + 1} 轮批判`}>
      <div className="critique-user-message">
        <span className="critique-avatar">你</span>
        <div><strong>你</strong><p>{turn.question}</p></div>
      </div>
      <div className="critique-assistant-message">
        <div className="critique-assistant-heading">
          <Scale size={18} />
          <strong>多维批判</strong>
          <span>第 {index + 1} 轮</span>
        </div>
        {turn.status === "running" && !completedResults.length ? (
          <div className="critique-thinking"><RefreshCw size={15} className="spin" />各学科正在并行分析...</div>
        ) : null}
        <div className="critique-expert-responses">
          {(turn.results || []).map((result) => (
            <details key={result.discipline_id} className={`critique-expert-response ${result.status}`}>
              <summary>
                <span className="critique-expert-dot" />
                <span className="critique-expert-summary">
                  <strong>{result.discipline_name}</strong>
                  <span>{result.analysis?.core_assumption || result.error || "等待输出"}</span>
                </span>
                <CritiqueStatus status={result.status} />
                <ChevronRight size={14} />
              </summary>
              {result.analysis ? (
                <div className="critique-analysis-grid">
                  <p><span>反证</span>{result.analysis.counterevidence}</p>
                  <p><span>机会成本</span>{result.analysis.opportunity_cost}</p>
                  <p><span>关键追问</span>{result.analysis.key_question}</p>
                </div>
              ) : result.status === "failed" ? (
                <div className="critique-result-error">
                  <span>{result.error || "学科分析失败"}</span>
                  <button type="button" className="critique-retry" onClick={() => onRetry(turn.id, result.discipline_id)} disabled={sending}>
                    <RefreshCw size={12} />重试
                  </button>
                </div>
              ) : <div className="critique-thinking">等待输出...</div>}
            </details>
          ))}
        </div>
        {turn.judgment ? (
          <section className="critique-judgment-inline">
            <header><Scale size={16} /><strong>综合裁判</strong></header>
            <div>
              <p><span>最薄弱假设</span>{turn.judgment.weakest_assumption}</p>
              <p><span>最大分歧</span>{turn.judgment.largest_disagreement}</p>
              <p><span>建议验证</span>{turn.judgment.recommended_validation}</p>
            </div>
          </section>
        ) : null}
      </div>
    </article>
  );
}


export function CritiquePage({ onUnauthorized }) {
  const [disciplines, setDisciplines] = useState([]);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [runs, setRuns] = useState([]);
  const [activeRun, setActiveRun] = useState(null);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [connection, setConnection] = useState("connecting");
  const [error, setError] = useState("");
  const [libraryOpen, setLibraryOpen] = useState(true);
  const [editingDiscipline, setEditingDiscipline] = useState(undefined);
  const socketRef = useRef(null);
  const streamRef = useRef(null);

  function handleError(err) {
    if (err.status === 401) {
      onUnauthorized?.();
      return;
    }
    setError(err.message);
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      requestJson("/api/critique/disciplines"),
      requestJson("/api/critique/runs")
    ]).then(([disciplineData, runData]) => {
      if (cancelled) return;
      const nextDisciplines = disciplineData.disciplines || [];
      const nextRuns = (runData.runs || []).map(normalizeRun);
      setDisciplines(nextDisciplines);
      setSelectedIds(new Set(nextDisciplines.filter((item) => item.default_enabled).map((item) => item.id)));
      setRuns(nextRuns);
      setActiveRun(nextRuns[0] || null);
    }).catch(handleError).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/critique/runs/connect`);
    socketRef.current = socket;
    socket.onopen = () => setConnection("connected");
    socket.onclose = () => setConnection("disconnected");
    socket.onerror = () => setError("多维批判连接失败");
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "status") {
        setConnection(message.status === "connected" ? "connected" : message.status);
        return;
      }
      if (message.type === "run_started") {
        setSending(true);
        setActiveRun((current) => updateTurn(current, "pending", (turn) => ({ ...turn, id: message.turn_id, status: "running" })));
        return;
      }
      if (message.type === "discipline_status") {
        setActiveRun((current) => updateTurn(current, message.turn_id, (turn) => {
          const existing = turn.results || [];
          const found = existing.some((item) => item.discipline_id === message.discipline_id);
          const discipline = (current?.disciplines || []).find((item) => item.id === message.discipline_id);
          const nextResult = message.result || {
            discipline_id: message.discipline_id,
            discipline_name: discipline?.name || "学科",
            status: message.status,
            analysis: null,
            error: ""
          };
          return {
            ...turn,
            results: found
              ? existing.map((item) => item.discipline_id === message.discipline_id ? { ...item, ...nextResult, status: message.status } : item)
              : [...existing, nextResult]
          };
        }));
        return;
      }
      if (message.type === "judgment_status" && message.status === "completed") {
        setActiveRun((current) => updateTurn(current, message.turn_id, (turn) => ({ ...turn, judgment: message.judgment })));
        return;
      }
      if (message.type === "run_completed") {
        const completed = normalizeRun(message.run);
        setActiveRun(completed);
        setRuns((current) => [completed, ...current.filter((item) => item.id !== completed.id)]);
        setSending(false);
        return;
      }
      if (message.type === "error") {
        setError(message.message || "多维批判运行失败");
        setSending(false);
      }
    };
    return () => socket.close();
  }, []);

  useEffect(() => {
    if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight;
  }, [activeRun?.turns, sending]);

  const activeTurns = activeRun?.turns || [];
  const activeDisciplines = activeRun?.disciplines || disciplines.filter((item) => selectedIds.has(item.id));
  const activeDisciplineNames = useMemo(
    () => activeDisciplines.map((item) => item.name).join("、"),
    [activeDisciplines]
  );

  function newConversation() {
    if (sending) return;
    setActiveRun(null);
    setQuestion("");
    setError("");
    setSelectedIds(new Set(disciplines.filter((item) => item.default_enabled).map((item) => item.id)));
  }

  function selectConversation(run) {
    if (sending) return;
    const normalized = normalizeRun(run);
    setActiveRun(normalized);
    setQuestion("");
    setError("");
    setSelectedIds(new Set(normalized.disciplines.map((item) => item.id)));
  }

  function toggleSelected(disciplineId) {
    if (activeRun) return;
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(disciplineId)) next.delete(disciplineId);
      else next.add(disciplineId);
      return next;
    });
  }

  function submitCritique() {
    const content = question.trim();
    if (!content || sending) return;
    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      setError("连接未就绪，请稍后重试");
      return;
    }
    const isFollowUp = Boolean(activeRun?.id && activeRun.id !== "pending");
    const selectedDisciplines = isFollowUp
      ? activeRun.disciplines
      : disciplines.filter((item) => selectedIds.has(item.id));
    if (!selectedDisciplines.length) return;
    const now = new Date().toISOString();
    const pendingTurn = {
      id: "pending",
      question: content,
      results: selectedDisciplines.map((item) => ({
        discipline_id: item.id,
        discipline_name: item.name,
        status: "queued",
        analysis: null,
        error: ""
      })),
      judgment: null,
      status: "running",
      created_at: now,
      updated_at: now
    };
    setError("");
    setSending(true);
    setQuestion("");
    if (isFollowUp) {
      setActiveRun((current) => normalizeRun({
        ...current,
        question: content,
        results: pendingTurn.results,
        judgment: null,
        status: "running",
        updated_at: now,
        turns: [...current.turns, pendingTurn]
      }));
      socketRef.current.send(JSON.stringify({ type: "follow_up", run_id: activeRun.id, question: content }));
      return;
    }
    setActiveRun({
      id: "pending",
      title: content,
      question: content,
      model_id: "",
      disciplines: selectedDisciplines,
      results: pendingTurn.results,
      judgment: null,
      turns: [pendingTurn],
      status: "running",
      created_at: now,
      updated_at: now
    });
    socketRef.current.send(JSON.stringify({
      type: "run",
      question: content,
      discipline_ids: selectedDisciplines.map((item) => item.id)
    }));
  }

  function handleComposerKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitCritique();
    }
  }

  function retryDiscipline(turnId, disciplineId) {
    if (!activeRun?.id || activeRun.id === "pending" || sending) return;
    setSending(true);
    socketRef.current?.send(JSON.stringify({
      type: "retry",
      run_id: activeRun.id,
      turn_id: turnId,
      discipline_id: disciplineId
    }));
  }

  async function saveDiscipline(form) {
    const current = editingDiscipline || null;
    const data = await requestJson(
      current ? `/api/critique/disciplines/${current.id}` : "/api/critique/disciplines",
      { method: current ? "PUT" : "POST", body: JSON.stringify(form) }
    );
    setDisciplines((items) => current
      ? items.map((item) => item.id === current.id ? data.discipline : item)
      : [...items, data.discipline]
    );
    setSelectedIds((ids) => {
      const next = new Set(ids);
      if (data.discipline.default_enabled) next.add(data.discipline.id);
      else next.delete(data.discipline.id);
      return next;
    });
  }

  async function deleteDiscipline(discipline) {
    if (!window.confirm(`删除学科“${discipline.name}”？历史记录不会受影响。`)) return;
    try {
      await requestJson(`/api/critique/disciplines/${discipline.id}`, { method: "DELETE" });
      setDisciplines((items) => items.filter((item) => item.id !== discipline.id));
      setSelectedIds((ids) => {
        const next = new Set(ids);
        next.delete(discipline.id);
        return next;
      });
    } catch (err) {
      handleError(err);
    }
  }

  return (
    <section className="page-section critique-section">
      <div className="critique-topbar">
        <div className="critique-topbar-context">
          <MessageSquareText size={16} />
          <strong>{activeRun?.title || "新批判对话"}</strong>
          {activeTurns.length ? <span>第 {activeTurns.length} 轮</span> : <span>等待提问</span>}
        </div>
        <span className={`runtime-status ${connection === "connected" ? "connected" : ""}`}>
          {connection === "connected" ? "已连接" : "未连接"}
        </span>
        <button type="button" className="secondary-button" onClick={() => setLibraryOpen((value) => !value)}>
          <BrainCircuit size={15} />管理学科
        </button>
      </div>

      <div className="critique-chat-workspace">
        <aside className="critique-conversation-rail" aria-label="批判对话历史">
          <button type="button" className="secondary-button critique-new-conversation" onClick={newConversation} disabled={sending}>
            <Plus size={15} />新建对话
          </button>
          <div className="critique-rail-heading"><span>对话历史</span><span>{runs.length}</span></div>
          <div className="critique-conversation-list">
            {runs.map((run) => (
              <button
                key={run.id}
                type="button"
                className={activeRun?.id === run.id ? "active" : ""}
                aria-label={`${run.title}，${run.turns.length} 轮`}
                onClick={() => selectConversation(run)}
              >
                <strong>{run.title}</strong>
                <span><Clock3 size={11} />{run.turns.length} 轮 · {formatConversationTime(run.updated_at)}</span>
              </button>
            ))}
            {!loading && runs.length === 0 ? <div className="critique-rail-empty">首个问题会创建一段可持续追问的对话</div> : null}
          </div>
        </aside>

        <main className="critique-chat-main">
          <div className="critique-message-stream" ref={streamRef}>
            {activeTurns.length ? activeTurns.map((turn, index) => (
              <CritiqueTurnView
                key={turn.id}
                turn={turn}
                index={index}
                sending={sending}
                onRetry={retryDiscipline}
              />
            )) : (
              <div className="critique-chat-empty">
                <Scale size={24} />
                <strong>提出一个值得被质疑的问题</strong>
                <p>已选学科会并行拆解假设，综合裁判给出验证方向；之后可以在同一上下文中继续追问。</p>
              </div>
            )}
          </div>
          {error ? <div className="form-error critique-chat-error">{error}</div> : null}
          <section className="critique-chat-composer">
            <textarea
              rows={2}
              maxLength={1000}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder={activeRun ? "继续追问，或要求某个学科深入..." : "输入你想被质疑的问题..."}
              aria-label={activeRun ? "继续追问" : "开始提问"}
            />
            <div>
              <span className="critique-active-experts">
                <span />{activeDisciplines.length ? `已选学科：${activeDisciplineNames}` : "请选择至少一个学科"}
              </span>
              <button
                type="button"
                className="secondary-button primary-action"
                onClick={submitCritique}
                disabled={!question.trim() || !activeDisciplines.length || sending || loading}
              >
                {sending ? <RefreshCw size={16} className="spin" /> : <Send size={16} />}
                {sending ? "分析中" : activeRun ? "继续追问" : "开始压榨"}
              </button>
            </div>
          </section>
        </main>

        <aside className="critique-context-inspector">
          <header>
            <div><strong>{activeRun ? "已选学科" : "选择学科"}</strong><span>{activeDisciplines.length}/{disciplines.length}</span></div>
            <button type="button" className="secondary-button small" onClick={() => setEditingDiscipline(null)}><Plus size={14} />添加</button>
          </header>
          <div className="critique-discipline-list">
            {disciplines.map((discipline) => {
              const selected = activeRun
                ? activeRun.disciplines.some((item) => item.id === discipline.id)
                : selectedIds.has(discipline.id);
              if (activeRun && !selected && !libraryOpen) return null;
              return (
                <div className="critique-discipline-row" key={discipline.id}>
                  <label>
                    <input
                      type="checkbox"
                      aria-label={`${discipline.name} 当前参与`}
                      checked={selected}
                      disabled={Boolean(activeRun)}
                      onChange={() => toggleSelected(discipline.id)}
                    />
                    <span><strong>{discipline.name}</strong><small>{discipline.known_scope}</small>{libraryOpen ? <small>{discipline.critique_focus}</small> : null}</span>
                  </label>
                  {libraryOpen ? (
                    <div>
                      <button type="button" className="icon-button" aria-label={`编辑${discipline.name}`} onClick={() => setEditingDiscipline(discipline)}><Pencil size={14} /></button>
                      <button type="button" className="icon-button danger" aria-label={`删除${discipline.name}`} onClick={() => deleteDiscipline(discipline)}><Trash2 size={14} /></button>
                    </div>
                  ) : null}
                </div>
              );
            })}
            {!loading && disciplines.length === 0 ? <div className="critique-empty">暂无学科</div> : null}
          </div>
          <section className="critique-context-summary">
            <strong>对话上下文</strong>
            <dl>
              <div><dt>主题</dt><dd>{activeRun?.title || "尚未创建"}</dd></div>
              <div><dt>当前轮次</dt><dd>{activeTurns.length || 0}</dd></div>
              <div><dt>参与学科</dt><dd>{activeDisciplines.length}</dd></div>
            </dl>
            {activeRun ? <p>本会话沿用首轮选择的学科与历史结论。</p> : <p>首轮开始后，学科组合会锁定到这段对话。</p>}
          </section>
        </aside>
      </div>

      {editingDiscipline !== undefined ? (
        <DisciplineEditor
          discipline={editingDiscipline}
          onClose={() => setEditingDiscipline(undefined)}
          onSave={saveDiscipline}
        />
      ) : null}
    </section>
  );
}
