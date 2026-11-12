import { useCallback, useEffect, useMemo, useState } from "react";

import { api, errorText } from "../api";
import {
  invalidatePageCache,
  peekPageCache,
  storePageCache,
} from "../pageCache";
import type {
  TradeJournalCreatePayload,
  TradeJournalRecord,
  TradeJournalStats,
  TradeJournalUpdatePayload,
} from "../types";

type Filters = {
  status: string;
  market: string;
  search: string;
};

type FormState = {
  id: string;
  symbol: string;
  market: string;
  direction: string;
  status: "open" | "closed";
  entryAt: string;
  entryPrice: string;
  exitAt: string;
  exitPrice: string;
  stopLoss: string;
  quantity: string;
  fees: string;
  thesis: string;
  review: string;
  tags: string;
  followedPlan: "unset" | "yes" | "no";
};

const EMPTY_FORM: FormState = {
  id: "",
  symbol: "",
  market: "crypto",
  direction: "long",
  status: "open",
  entryAt: "",
  entryPrice: "",
  exitAt: "",
  exitPrice: "",
  stopLoss: "",
  quantity: "",
  fees: "0",
  thesis: "",
  review: "",
  tags: "",
  followedPlan: "unset",
};

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function localInputValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function inputToIso(value: string): string | null {
  if (!value.trim()) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", {
    maximumFractionDigits: Math.abs(value) >= 1000 ? 2 : Math.abs(value) >= 1 ? 4 : 8,
  });
}

function formatPct(value: number | null | undefined, signed = true): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const pct = value * 100;
  return `${signed && pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function formatR(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}R`;
}

function formatMoney(value: number | null | undefined, currency: string | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toLocaleString("en-US", {
    maximumFractionDigits: 2,
  })} ${currency ?? ""}`.trim();
}

function directionLabel(value: string): string {
  return value === "short" ? "空" : "多";
}

function statusLabel(value: string): string {
  return value === "closed" ? "已平仓" : "持仓中";
}

function marketLabel(value: string): string {
  return value === "a_share" ? "A股" : "Crypto";
}

function splitTags(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function recordToForm(record: TradeJournalRecord): FormState {
  return {
    id: record.id,
    symbol: record.symbol,
    market: record.market,
    direction: record.direction,
    status: record.status === "closed" ? "closed" : "open",
    entryAt: localInputValue(record.entry_at),
    entryPrice: String(record.entry_price),
    exitAt: localInputValue(record.exit_at),
    exitPrice: record.exit_price === null ? "" : String(record.exit_price),
    stopLoss: record.stop_loss === null ? "" : String(record.stop_loss),
    quantity: record.quantity === null ? "" : String(record.quantity),
    fees: String(record.fees ?? 0),
    thesis: record.thesis,
    review: record.review,
    tags: (record.tags ?? []).join(", "),
    followedPlan:
      record.followed_plan === true ? "yes" : record.followed_plan === false ? "no" : "unset",
  };
}

export function TradeJournalPage({ onClose }: { onClose: () => void }) {
  const [filters, setFilters] = useState<Filters>({ status: "", market: "", search: "" });
  const [records, setRecords] = useState<TradeJournalRecord[]>([]);
  const [stats, setStats] = useState<TradeJournalStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const load = useCallback(async () => {
    const query = {
      status: filters.status,
      market: filters.market,
      search: filters.search,
      limit: 50,
    };
    const cacheKey = `journal:${query.status}|${query.market}|${query.search}`;
    const cached = peekPageCache<{
      items: TradeJournalRecord[];
      stats: TradeJournalStats;
    }>(cacheKey);
    if (cached) {
      setRecords(cached.items);
      setStats(cached.stats);
      setLoading(false);
    }
    setError("");
    if (!cached) setLoading(true);
    try {
      const [list, nextStats] = await Promise.all([
        api.listTradeJournal(query),
        api.getTradeJournalStats({
          market: filters.market,
        }),
      ]);
      setRecords(list.items);
      setStats(nextStats);
      storePageCache(cacheKey, { items: list.items, stats: nextStats });
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  }, [filters.status, filters.market, filters.search]);

  useEffect(() => {
    void load();
  }, [load]);

  const visibleWarnings = useMemo(
    () => records.flatMap((record) => record.warnings ?? []),
    [records],
  );

  function updateFilter<K extends keyof Filters>(key: K, value: Filters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function openNew() {
    setForm({ ...EMPTY_FORM, entryAt: localInputValue(new Date().toISOString()) });
    setFormError("");
    setModalOpen(true);
  }

  function openEdit(record: TradeJournalRecord) {
    setForm(recordToForm(record));
    setFormError("");
    setModalOpen(true);
  }

  function updateForm<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    setFormError("");
    const symbol = form.symbol.trim().toUpperCase();
    const entryAt = inputToIso(form.entryAt);
    const entryPrice = Number(form.entryPrice);
    const closed = form.status === "closed";
    const exitAt = closed ? inputToIso(form.exitAt) : null;
    const exitPrice = closed ? Number(form.exitPrice) : null;
    if (!symbol) {
      setFormError("请填写交易对");
      return;
    }
    if (!entryAt) {
      setFormError("请填写入场时间");
      return;
    }
    if (!Number.isFinite(entryPrice) || entryPrice <= 0) {
      setFormError("入场价必须大于 0");
      return;
    }
    if (!form.thesis.trim()) {
      setFormError("请写一句交易理由");
      return;
    }
    if (closed && (!exitAt || exitPrice === null || !Number.isFinite(exitPrice) || exitPrice <= 0)) {
      setFormError("已平仓记录需要填写出场时间和出场价");
      return;
    }

    const payload: TradeJournalCreatePayload = {
      symbol,
      market: form.market,
      direction: form.direction,
      entry_at: entryAt,
      entry_price: entryPrice,
      thesis: form.thesis.trim(),
      stop_loss: form.stopLoss.trim() ? Number(form.stopLoss) : null,
      quantity: form.quantity.trim() ? Number(form.quantity) : null,
      fees: form.fees.trim() ? Number(form.fees) : 0,
      exit_at: exitAt,
      exit_price: exitPrice,
      review: form.review.trim(),
      tags: splitTags(form.tags),
      followed_plan:
        form.followedPlan === "unset" ? null : form.followedPlan === "yes",
    };
    if (!closed) {
      payload.exit_at = null;
      payload.exit_price = null;
    }
    setSaving(true);
    try {
      if (form.id) {
        await api.updateTradeJournal(form.id, payload as TradeJournalUpdatePayload);
        setNotice("交易记录已更新");
      } else {
        await api.createTradeJournal(payload);
        setNotice("交易记录已保存");
      }
      setModalOpen(false);
      invalidatePageCache("journal:");
      await load();
    } catch (exc) {
      setFormError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="backtest-page journal-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">FEATURES</div>
          <h1>
            交易记录
            <span className="page-scope-pill">个人日志</span>
          </h1>
          <p>记录真实或模拟交易的计划、执行与复盘；价格收益、R 和净盈亏由平台计算。</p>
        </div>
        <div className="settings-card-actions">
          <button className="ghost-button" onClick={onClose}>
            返回对话
          </button>
          <button className="primary" onClick={openNew}>
            + 新建记录
          </button>
        </div>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          {error && <div className="error-banner">{error}</div>}
          {notice && <div className="journal-notice">{notice}</div>}

          <div className="journal-stat-grid">
            <div>
              <span>已平仓</span>
              <strong>{stats?.count ?? 0}</strong>
              <small>持仓中 {stats?.open_count ?? 0}</small>
            </div>
            <div>
              <span>胜率</span>
              <strong>{stats?.win_rate === null || stats?.win_rate === undefined ? "—" : formatPct(stats.win_rate, false)}</strong>
              <small>已平仓样本</small>
            </div>
            <div>
              <span>平均收益</span>
              <strong className={stats?.avg_return != null && stats.avg_return >= 0 ? "tone-good" : stats?.avg_return != null ? "tone-bad" : ""}>
                {formatPct(stats?.avg_return)}
              </strong>
              <small>价格收益，未扣费</small>
            </div>
            <div>
              <span>平均 R</span>
              <strong>{formatR(stats?.avg_r)}</strong>
              <small>需要填写止损</small>
            </div>
            <div>
              <span>累计盈亏</span>
              <strong className={stats?.total_pnl != null && stats.total_pnl >= 0 ? "tone-good" : stats?.total_pnl != null ? "tone-bad" : ""}>
                {stats?.total_pnl != null
                  ? formatMoney(stats.total_pnl, stats.currency)
                  : stats?.pnl_currencies?.length
                    ? "多币种"
                    : "—"}
              </strong>
              <small>{stats?.pnl_currencies?.join(" / ") || "已填数量才计算"}</small>
            </div>
          </div>

          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>交易记录</h2>
                <p>点击一行编辑；标签建议用 setup:、mistake:、emotion:、regime: 前缀。</p>
              </div>
              <div className="settings-card-actions">
                <button className="ghost-button" onClick={() => void load()} disabled={loading}>
                  {loading ? "加载中…" : "刷新"}
                </button>
              </div>
            </div>

            <div className="journal-filters">
              <select
                value={filters.status}
                onChange={(event) => updateFilter("status", event.target.value)}
                aria-label="状态"
              >
                <option value="">全部状态</option>
                <option value="open">持仓中</option>
                <option value="closed">已平仓</option>
              </select>
              <select
                value={filters.market}
                onChange={(event) => updateFilter("market", event.target.value)}
                aria-label="市场"
              >
                <option value="">全部市场</option>
                <option value="crypto">Crypto</option>
                <option value="a_share">A股</option>
              </select>
              <input
                value={filters.search}
                onChange={(event) => updateFilter("search", event.target.value)}
                placeholder="搜索标的、理由、复盘、标签"
                onKeyDown={(event) => {
                  if (event.key === "Enter") void load();
                }}
              />
              <button className="ghost-button" onClick={() => void load()}>
                查询
              </button>
            </div>

            {visibleWarnings.length > 0 && (
              <ul className="backtest-warnings">
                {Array.from(new Set(visibleWarnings)).map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}

            <div className="backtest-table-wrap">
              <table className="backtest-table journal-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>交易</th>
                    <th>策略 / 标签</th>
                    <th>入场 → 出场</th>
                    <th>收益</th>
                    <th>R</th>
                    <th>盈亏</th>
                    <th>状态</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {records.map((record) => (
                    <tr key={record.id} onClick={() => openEdit(record)}>
                      <td className="journal-time">{formatDateTime(record.entry_at)}</td>
                      <td>
                        <div className="journal-symbol">
                          {record.symbol}
                          <span className={`backtest-side ${record.direction === "short" ? "short" : ""}`}>
                            {directionLabel(record.direction)}
                          </span>
                          <span className="journal-market">{marketLabel(record.market)}</span>
                        </div>
                        <div className="journal-tags">
                          {record.tags.slice(0, 3).map((tag) => (
                            <span key={tag}>{tag}</span>
                          ))}
                        </div>
                      </td>
                      <td>{record.tags.find((tag) => tag.startsWith("setup:"))?.slice(6) ?? "—"}</td>
                      <td className="journal-price">
                        {formatPrice(record.entry_price)}
                        <span>→</span>
                        {record.exit_price === null ? "持仓中" : formatPrice(record.exit_price)}
                      </td>
                      <td className={record.return_pct != null && record.return_pct >= 0 ? "tone-good" : record.return_pct != null ? "tone-bad" : ""}>
                        {formatPct(record.return_pct)}
                      </td>
                      <td>{formatR(record.r_multiple)}</td>
                      <td className={record.pnl != null && record.pnl >= 0 ? "tone-good" : record.pnl != null ? "tone-bad" : ""}>
                        {formatMoney(record.pnl, record.currency)}
                      </td>
                      <td>
                        <span className={`backtest-status ${record.status}`}>{statusLabel(record.status)}</span>
                      </td>
                      <td>
                        <button
                          className="journal-edit"
                          onClick={(event) => {
                            event.stopPropagation();
                            openEdit(record);
                          }}
                        >
                          编辑
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!records.length && !loading && (
                    <tr>
                      <td colSpan={9} className="journal-empty">
                        还没有交易记录。点右上角「新建记录」记第一笔。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>

      {modalOpen && (
        <div className="modal-backdrop" onClick={() => setModalOpen(false)}>
          <div className="modal journal-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-heading">
              <div>
                <h2>{form.id ? "编辑交易记录" : "新建交易记录"}</h2>
                <p className="journal-modal-sub">状态由出场价决定；填了出场价就是已平仓。</p>
              </div>
              <button className="ghost-button" onClick={() => setModalOpen(false)}>
                关闭
              </button>
            </div>

            {formError && <div className="error-banner">{formError}</div>}

            <div className="journal-form-grid">
              <label className="backtest-field">
                <span>交易对</span>
                <input
                  value={form.symbol}
                  onChange={(event) => updateForm("symbol", event.target.value.toUpperCase())}
                  placeholder="BTCUSDT 或 600519.SH"
                  autoComplete="off"
                />
              </label>
              <label className="backtest-field">
                <span>市场</span>
                <select value={form.market} onChange={(event) => updateForm("market", event.target.value)}>
                  <option value="crypto">Crypto</option>
                  <option value="a_share">A股</option>
                </select>
              </label>
              <label className="backtest-field">
                <span>方向</span>
                <select value={form.direction} onChange={(event) => updateForm("direction", event.target.value)}>
                  <option value="long">做多</option>
                  <option value="short">做空</option>
                </select>
              </label>
              <label className="backtest-field">
                <span>状态</span>
                <select
                  value={form.status}
                  onChange={(event) => updateForm("status", event.target.value as "open" | "closed")}
                >
                  <option value="open">持仓中</option>
                  <option value="closed">已平仓</option>
                </select>
              </label>
              <label className="backtest-field">
                <span>入场时间</span>
                <input
                  type="datetime-local"
                  value={form.entryAt}
                  onChange={(event) => updateForm("entryAt", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>入场价</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={form.entryPrice}
                  onChange={(event) => updateForm("entryPrice", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>出场时间</span>
                <input
                  type="datetime-local"
                  value={form.exitAt}
                  onChange={(event) => updateForm("exitAt", event.target.value)}
                  disabled={form.status !== "closed"}
                />
              </label>
              <label className="backtest-field">
                <span>出场价</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={form.exitPrice}
                  onChange={(event) => updateForm("exitPrice", event.target.value)}
                  disabled={form.status !== "closed"}
                />
              </label>
              <label className="backtest-field">
                <span>止损</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={form.stopLoss}
                  onChange={(event) => updateForm("stopLoss", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>数量</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={form.quantity}
                  onChange={(event) => updateForm("quantity", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>手续费</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={form.fees}
                  onChange={(event) => updateForm("fees", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>按计划</span>
                <select
                  value={form.followedPlan}
                  onChange={(event) =>
                    updateForm("followedPlan", event.target.value as FormState["followedPlan"])
                  }
                >
                  <option value="unset">未记录</option>
                  <option value="yes">是</option>
                  <option value="no">否</option>
                </select>
              </label>
              <label className="backtest-field journal-wide">
                <span>标签（逗号分隔）</span>
                <input
                  value={form.tags}
                  onChange={(event) => updateForm("tags", event.target.value)}
                  placeholder="setup:趋势回踩, mistake:追高, regime:趋势"
                />
              </label>
              <label className="backtest-field journal-wide">
                <span>交易理由 / 计划</span>
                <textarea
                  value={form.thesis}
                  onChange={(event) => updateForm("thesis", event.target.value)}
                  placeholder="我在赌什么？什么证明我错了？风险多少？"
                />
              </label>
              <label className="backtest-field journal-wide">
                <span>复盘总结</span>
                <textarea
                  value={form.review}
                  onChange={(event) => updateForm("review", event.target.value)}
                  placeholder="执行问题还是判断问题？重来会怎么做？"
                />
              </label>
            </div>

            <div className="modal-actions">
              <button className="ghost-button" onClick={() => setModalOpen(false)} disabled={saving}>
                取消
              </button>
              <button className="primary" onClick={() => void save()} disabled={saving}>
                {saving ? "保存中…" : "保存记录"}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
