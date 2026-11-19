import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api";
import { describeApiError } from "../errors";
import { formatBytes } from "../messageContent";
import { Markdown } from "./Markdown";
import type { RandomNote, RandomNoteMeta } from "../types";

type RandomNotesPageProps = {
  onAsk: (note: RandomNote) => void;
  onClose: () => void;
  onOpenKnowledgeSettings: () => void;
};

export function RandomNotesPage({
  onAsk,
  onClose,
  onOpenKnowledgeSettings,
}: RandomNotesPageProps) {
  const [note, setNote] = useState<RandomNote | null>(null);
  const [meta, setMeta] = useState<RandomNoteMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [empty, setEmpty] = useState(false);
  const startedRef = useRef(false);

  const load = useCallback(async (exclude?: string) => {
    setLoading(true);
    setError("");
    try {
      const response = await api.randomNote(exclude);
      setNote(response.note);
      setMeta(response.meta);
      setEmpty(false);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 404) {
        setEmpty(true);
        setNote(null);
        setMeta(null);
      } else {
        setError(describeApiError(exc) || "加载失败，请重试");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // StrictMode runs effects twice in dev; avoid consuming two draws.
    if (startedRef.current) return;
    startedRef.current = true;
    void load();
  }, [load]);

  return (
    <main className="knowledge-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">KNOWLEDGE</div>
          <h1>随机笔记</h1>
          <p>从知识库里随机翻一篇笔记，看完换下一篇。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>
          返回对话
        </button>
      </header>
      <div className="settings-scroll">
        <div className="knowledge-progress" role="status">
          {meta ? (
            <span>
              本轮已看 <b>{meta.seen}</b> / 共 <b>{meta.total}</b> · 剩余{" "}
              {Math.max(meta.total - meta.seen, 0)}
            </span>
          ) : loading ? (
            <span>正在整理笔记列表…</span>
          ) : null}
          {meta?.notice ? <em className="knowledge-notice">{meta.notice}</em> : null}
        </div>

        {empty ? (
          <div className="knowledge-empty">
            <p>知识库没有可用的笔记，先去配置页检查知识库同步。</p>
            <button className="primary" onClick={onOpenKnowledgeSettings}>
              去配置知识库
            </button>
            {loading ? null : (
              <button className="ghost-button" onClick={() => void load()}>
                重试
              </button>
            )}
          </div>
        ) : error ? (
          <div className="knowledge-empty" role="alert">
            <p>{error}</p>
            <button className="primary" onClick={() => void load(note?.path)}>
              重试
            </button>
          </div>
        ) : (
          <section className="knowledge-card">
            {note ? (
              <>
                <header className="knowledge-card-head">
                  <div>
                    <h2>{note.title}</h2>
                    <div className="knowledge-card-path">📄 {note.path}</div>
                  </div>
                  <span className="knowledge-card-size">{formatBytes(note.size)}</span>
                </header>
                <div className="knowledge-card-body">
                  <Markdown content={note.content} highlight={false} />
                </div>
              </>
            ) : (
              <div className="knowledge-card-skeleton">正在抽下一篇…</div>
            )}
            <footer className="knowledge-card-actions">
              <button
                className="primary"
                disabled={loading}
                onClick={() => void load(note?.path)}
              >
                {loading ? "正在抽下一篇…" : "换一篇"}
              </button>
              <button
                className="ghost-button"
                disabled={loading || !note}
                onClick={() => {
                  if (note) onAsk(note);
                }}
              >
                ✦ 追问
              </button>
            </footer>
          </section>
        )}
      </div>
    </main>
  );
}
