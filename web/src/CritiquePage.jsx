import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  BrainCircuit,
  ChevronDown,
  ChevronUp,
  History,
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
    failed: "失败"
  };
  return <span className={`critique-status ${status || "queued"}`}>{labels[status] || labels.queued}</span>;
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
            <span>
              <strong>默认参与</strong>
              <small>后续新问题自动启用这个学科</small>
            </span>
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
  const [historyOpen, setHistoryOpen] = useState(false);
  const [editingDiscipline, setEditingDiscipline] = useState(undefined);
  const socketRef = useRef(null);

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
      setDisciplines(nextDisciplines);
      setSelectedIds(new Set(nextDisciplines.filter((item) => item.default_enabled).map((item) => item.id)));
      setRuns(runData.runs || []);
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
        return;
      }
      if (message.type === "discipline_status") {
        setActiveRun((current) => {
          if (!current) return current;
          const existing = current.results || [];
          const found = existing.some((item) => item.discipline_id === message.discipline_id);
          const nextResult = message.result || {
            discipline_id: message.discipline_id,
            discipline_name: current.disciplines.find((item) => item.id === message.discipline_id)?.name || "学科",
            status: message.status,
            analysis: null,
            error: ""
          };
          return {
            ...current,
            results: found
              ? existing.map((item) => item.discipline_id === message.discipline_id ? { ...item, ...nextResult, status: message.status } : item)
              : [...existing, nextResult]
          };
        });
        return;
      }
      if (message.type === "judgment_status" && message.status === "completed") {
        setActiveRun((current) => current ? { ...current, judgment: message.judgment } : current);
        return;
      }
      if (message.type === "run_completed") {
        setActiveRun(message.run);
        setRuns((current) => [message.run, ...current.filter((item) => item.id !== message.run.id)]);
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

  const resultMap = useMemo(
    () => new Map((activeRun?.results || []).map((item) => [item.discipline_id, item])),
    [activeRun?.results]
  );
  const visibleDisciplines = activeRun?.disciplines || disciplines.filter((item) => selectedIds.has(item.id));

  function toggleSelected(disciplineId) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(disciplineId)) next.delete(disciplineId);
      else next.add(disciplineId);
      return next;
    });
  }

  function runCritique() {
    const content = question.trim();
    if (!content || !selectedIds.size || sending) return;
    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      setError("连接未就绪，请稍后重试");
      return;
    }
    const selectedDisciplines = disciplines.filter((item) => selectedIds.has(item.id));
    setError("");
    setSending(true);
    setActiveRun({
      id: "pending",
      question: content,
      disciplines: selectedDisciplines,
      results: selectedDisciplines.map((item) => ({
        discipline_id: item.id,
        discipline_name: item.name,
        status: "queued",
        analysis: null,
        error: ""
      })),
      judgment: null,
      status: "running"
    });
    socketRef.current.send(JSON.stringify({
      type: "run",
      question: content,
      discipline_ids: selectedDisciplines.map((item) => item.id)
    }));
  }

  function retryDiscipline(disciplineId) {
    if (!activeRun?.id || activeRun.id === "pending" || sending) return;
    setSending(true);
    socketRef.current?.send(JSON.stringify({
      type: "retry",
      run_id: activeRun.id,
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
      <div className="critique-toolbar">
        <button type="button" className="secondary-button" onClick={() => setHistoryOpen((value) => !value)}>
          <History size={15} />运行历史<span className="toolbar-count">{runs.length}</span>
        </button>
        <span>{selectedIds.size}/{disciplines.length} 个学科参与</span>
        <span className={`runtime-status ${connection === "connected" ? "connected" : ""}`}>
          {connection === "connected" ? "已连接" : "未连接"}
        </span>
        <button type="button" className="secondary-button" onClick={() => setLibraryOpen((value) => !value)}>
          <BrainCircuit size={15} />管理学科{libraryOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>

      <div className={`critique-workspace${libraryOpen ? " with-library" : ""}`}>
        <main className="critique-main">
          {historyOpen ? (
            <section className="critique-history-strip" aria-label="运行历史">
              {runs.length ? runs.map((run) => (
                <button key={run.id} type="button" onClick={() => { setActiveRun(run); setQuestion(run.question); }}>
                  <strong>{run.question}</strong><span>{run.status}</span>
                </button>
              )) : <span>暂无批判记录</span>}
            </section>
          ) : null}

          <section className="critique-composer">
            <label htmlFor="critique-question">提问</label>
            <textarea
              id="critique-question"
              rows={3}
              maxLength={1000}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="输入你想被质疑的问题..."
            />
            <div>
              <span>系统会从已选学科并行质疑，再由综合裁判压缩结论。</span>
              <button type="button" className="secondary-button primary-action" onClick={runCritique} disabled={!question.trim() || !selectedIds.size || sending || loading}>
                {sending ? <RefreshCw size={16} className="spin" /> : <Send size={16} />}
                {sending ? "压榨中" : "开始压榨"}
              </button>
            </div>
          </section>

          {error ? <div className="form-error">{error}</div> : null}

          <div className="critique-matrix-wrap">
            <table className="critique-matrix">
              <thead>
                <tr>
                  <th>学科</th><th>核心假设</th><th>反证</th><th>机会成本</th><th>关键追问</th>
                </tr>
              </thead>
              <tbody>
                {visibleDisciplines.length ? visibleDisciplines.map((discipline) => {
                  const result = resultMap.get(discipline.id);
                  return (
                    <tr key={discipline.id}>
                      <th scope="row">
                        <strong>{discipline.name}</strong>
                        <CritiqueStatus status={result?.status || (activeRun ? "queued" : "queued")} />
                        {result?.status === "failed" ? (
                          <button type="button" className="critique-retry" onClick={() => retryDiscipline(discipline.id)}><RefreshCw size={12} />重试</button>
                        ) : null}
                      </th>
                      <td data-label="核心假设">{result?.analysis?.core_assumption || result?.error || "等待运行"}</td>
                      <td data-label="反证">{result?.analysis?.counterevidence || "-"}</td>
                      <td data-label="机会成本">{result?.analysis?.opportunity_cost || "-"}</td>
                      <td data-label="关键追问">{result?.analysis?.key_question || "-"}</td>
                    </tr>
                  );
                }) : (
                  <tr><td colSpan={5} className="critique-empty">添加并选择你了解的学科后开始提问</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <section className="critique-judgment">
            <header><Scale size={17} /><strong>综合裁判</strong><span>{activeRun?.judgment ? "综合完成" : "等待各学科输出"}</span></header>
            <div className="critique-judgment-grid">
              <article><span>最薄弱假设</span><p>{activeRun?.judgment?.weakest_assumption || "运行后生成"}</p></article>
              <article><span>最大分歧</span><p>{activeRun?.judgment?.largest_disagreement || "运行后生成"}</p></article>
              <article><span>建议验证</span><p>{activeRun?.judgment?.recommended_validation || "运行后生成"}</p></article>
            </div>
          </section>
        </main>

        {libraryOpen ? (
          <aside className="critique-library">
            <header>
              <div><strong>学科库</strong><span>管理长期参与设置</span></div>
              <button type="button" className="secondary-button small" onClick={() => setEditingDiscipline(null)}><Plus size={14} />添加学科</button>
            </header>
            <div className="critique-library-columns"><span>学科与范围</span><span>当前参与</span></div>
            <div className="critique-discipline-list">
              {disciplines.map((discipline) => (
                <div className="critique-discipline-row" key={discipline.id}>
                  <label>
                    <input type="checkbox" aria-label={`${discipline.name} 当前参与`} checked={selectedIds.has(discipline.id)} onChange={() => toggleSelected(discipline.id)} />
                    <span><strong>{discipline.name}</strong><small>{discipline.known_scope}</small><small>{discipline.critique_focus}</small></span>
                  </label>
                  <div>
                    <button type="button" className="icon-button" aria-label={`编辑${discipline.name}`} onClick={() => setEditingDiscipline(discipline)}><Pencil size={14} /></button>
                    <button type="button" className="icon-button danger" aria-label={`删除${discipline.name}`} onClick={() => deleteDiscipline(discipline)}><Trash2 size={14} /></button>
                  </div>
                </div>
              ))}
              {!loading && disciplines.length === 0 ? <div className="critique-empty">暂无学科</div> : null}
            </div>
          </aside>
        ) : null}
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
