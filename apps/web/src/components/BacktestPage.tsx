import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, errorText } from "../api";
import {
  dateInputToUtcMs,
  defaultDateRange,
  formatPercent,
  formatUtc,
  granularityLabel,
  statusLabel,
} from "../backtestFormat";
import type {
  BacktestDetail,
  BacktestMeta,
  BacktestResult,
  BacktestSummary,
  MarketProbe,
  MarketStatus,
} from "../types";
import { Select } from "./Select";
import { BacktestResultPanel } from "./BacktestResult";

const ACTIVE_STATUSES = ["queued", "fetching", "running"];

const FALLBACK_GRANULARITIES = [
  "1min",
  "5min",
  "15min",
  "1h",
  "4h",
  "1day",
  "1week",
  "1M",
].map((value) => ({ value, label: granularityLabel(value) }));

const DEFAULT_STRATEGY = `def strategy(df):
    """df: UTC DatetimeIndex, columns open/high/low/close/volume.

    Return the target position for each bar in [-1, 1].
    """
    fast = df["close"].rolling(20).mean()
    slow = df["close"].rolling(60).mean()
    return (fast > slow).astype(float)
`;

type FormState = {
  symbol: string;
  granularity: string;
  start: string;
  end: string;
  capital: string;
  feePct: string;
  slippagePct: string;
  code: string;
};

function initialForm(): FormState {
  return {
    symbol: "BTCUSDT",
    granularity: "1h",
    ...defaultDateRange(365),
    capital: "10000",
    feePct: "0.1",
    slippagePct: "0.05",
    code: DEFAULT_STRATEGY,
  };
}

export function BacktestPage({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState<FormState>(initialForm);
  const [meta, setMeta] = useState<BacktestMeta | null>(null);
  const [history, setHistory] = useState<BacktestSummary[]>([]);
  const [current, setCurrent] = useState<BacktestDetail | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [cacheStatus, setCacheStatus] = useState<MarketStatus | null>(null);
  const [probe, setProbe] = useState<MarketProbe | null>(null);
  const [probing, setProbing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const [cacheNonce, setCacheNonce] = useState(0);
  const [pollTick, setPollTick] = useState(0);
  const [pollDelay, setPollDelay] = useState(1200);
  const [historyView, setHistoryView] = useState<{
    detail: BacktestDetail;
    result: BacktestResult | null;
  } | null>(null);
  const [historyLoadingId, setHistoryLoadingId] = useState("");
  const [historyError, setHistoryError] = useState("");
  const historyRequestRef = useRef(0);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api.listBacktests(50));
    } catch {
      // Keep the previous list; the page-level error is enough.
    }
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      setLoading(true);
      try {
        const [metaData, list] = await Promise.all([
          api.getBacktestMeta(),
          api.listBacktests(50),
        ]);
        if (!active) return;
        setMeta(metaData);
        setHistory(list);
        setForm((value) =>
          metaData.granularities.some((item) => item.value === value.granularity)
            ? value
            : {
                ...value,
                granularity: metaData.granularities[0]?.value ?? value.granularity,
              },
        );
      } catch (exc) {
        if (active) setError(errorText(exc));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const symbol = form.symbol.trim().toUpperCase();
    if (symbol.length < 3) {
      setCacheStatus(null);
      return;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      api
        .getMarketStatus(symbol, form.granularity)
        .then((status) => {
          if (active) setCacheStatus(status);
        })
        .catch(() => {
          if (active) setCacheStatus(null);
        });
    }, 500);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [form.symbol, form.granularity, cacheNonce]);

  useEffect(() => {
    setProbe(null);
  }, [form.symbol, form.granularity]);

  useEffect(() => {
    if (!current || !ACTIVE_STATUSES.includes(current.status)) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const detail = await api.getBacktest(current.id);
          if (!active) return;
          setError("");
          setPollDelay(1200);
          setCurrent(detail);
          if (detail.status === "completed") {
            const data = await api.getBacktestResult(detail.id);
            if (!active) return;
            setResult(data);
            setCacheNonce((value) => value + 1);
            await refreshHistory();
          } else if (detail.status === "failed" || detail.status === "cancelled") {
            await refreshHistory();
          }
        } catch (exc) {
          if (!active) return;
          setError(errorText(exc));
          if (exc instanceof ApiError && exc.status === 404) return;
          setPollDelay((value) => Math.min(Math.round(value * 1.5), 10000));
          setPollTick((value) => value + 1);
        }
      })();
    }, pollDelay);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [current, refreshHistory, pollTick, pollDelay]);

  useEffect(() => {
    if (!historyView || !ACTIVE_STATUSES.includes(historyView.detail.status)) return;
    const backtestId = historyView.detail.id;
    let active = true;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const detail = await api.getBacktest(backtestId);
          if (!active) return;
          if (detail.status === "completed") {
            const data = await api.getBacktestResult(backtestId);
            if (!active) return;
            setHistoryError("");
            setHistoryView({ detail, result: data });
            setCacheNonce((value) => value + 1);
            await refreshHistory();
          } else {
            setHistoryError("");
            setHistoryView({ detail, result: null });
            if (detail.status === "failed" || detail.status === "cancelled") {
              await refreshHistory();
            }
          }
        } catch (exc) {
          if (active) setHistoryError(errorText(exc));
        }
      })();
    }, 1500);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [historyView, refreshHistory]);

  useEffect(() => {
    if (!historyView) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setHistoryView(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [historyView]);

  const active = current !== null && ACTIVE_STATUSES.includes(current.status);
  const granularities = meta?.granularities?.length
    ? meta.granularities
    : FALLBACK_GRANULARITIES;

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((currentForm) => ({ ...currentForm, [key]: value }));
  }

  async function runBacktest() {
    setError("");
    const symbol = form.symbol.trim().toUpperCase();
    if (!/^[A-Z0-9]{3,30}$/.test(symbol)) {
      setError("交易对只能包含大写字母和数字，例如 BTCUSDT");
      return;
    }
    const startMs = dateInputToUtcMs(form.start);
    const endMs = dateInputToUtcMs(form.end);
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
      setError("请选择有效的开始和结束日期，结束日期必须晚于开始日期");
      return;
    }
    const capital = Number(form.capital);
    if (!Number.isFinite(capital) || capital <= 0) {
      setError("初始资金必须大于 0");
      return;
    }
    const feeRate = Number(form.feePct) / 100;
    const slippageRate = Number(form.slippagePct) / 100;
    if (
      !Number.isFinite(feeRate) ||
      !Number.isFinite(slippageRate) ||
      feeRate < 0 ||
      slippageRate < 0
    ) {
      setError("手续费率和滑点必须是非负数");
      return;
    }
    if (!form.code.trim()) {
      setError("策略代码不能为空");
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const detail = await api.createBacktest({
        symbol,
        granularity: form.granularity,
        start_ms: startMs,
        end_ms: endMs,
        initial_capital: capital,
        fee_rate: feeRate,
        slippage_rate: slippageRate,
        strategy_code: form.code,
      });
      setCurrent(detail);
      setCacheNonce((value) => value + 1);
      await refreshHistory();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  }

  async function openBacktest(id: string) {
    const requestId = historyRequestRef.current + 1;
    historyRequestRef.current = requestId;
    setError("");
    setHistoryError("");
    setHistoryLoadingId(id);
    try {
      const detail = await api.getBacktest(id);
      const data =
        detail.status === "completed" ? await api.getBacktestResult(id) : null;
      if (requestId !== historyRequestRef.current) return;
      setHistoryView({ detail, result: data });
    } catch (exc) {
      if (requestId === historyRequestRef.current) setError(errorText(exc));
    } finally {
      if (requestId === historyRequestRef.current) setHistoryLoadingId("");
    }
  }

  async function cancelHistoryBacktest() {
    if (!historyView) return;
    setCancelling(true);
    setError("");
    setHistoryError("");
    try {
      await api.cancelBacktest(historyView.detail.id);
      setHistoryView({
        detail: await api.getBacktest(historyView.detail.id),
        result: null,
      });
      await refreshHistory();
    } catch (exc) {
      setHistoryError(errorText(exc));
    } finally {
      setCancelling(false);
    }
  }

  async function cancelBacktest() {
    if (!current) return;
    setCancelling(true);
    setError("");
    try {
      await api.cancelBacktest(current.id);
      setCurrent(await api.getBacktest(current.id));
      await refreshHistory();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setCancelling(false);
    }
  }

  async function testConnection() {
    setProbing(true);
    setProbe(null);
    setError("");
    try {
      setProbe(
        await api.probeMarket(form.symbol.trim().toUpperCase(), form.granularity),
      );
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setProbing(false);
    }
  }

  function statusText(detail: BacktestDetail): string {
    switch (detail.status) {
      case "queued":
        return "排队中，等待执行";
      case "fetching":
        return detail.progress.fetched_bars
          ? `正在抓取行情，已写入 ${detail.progress.fetched_bars} 根 K 线`
          : "正在抓取行情";
      case "running":
        return "正在执行策略";
      case "completed":
        return `回测完成，共 ${detail.bar_count} 根 K 线，${detail.trades_total} 笔交易`;
      case "failed":
        return detail.error ?? "回测失败";
      case "cancelled":
        return "回测已取消";
      default:
        return detail.status;
    }
  }

  return (
    <main className="backtest-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">QUANT</div>
          <h1>
            量化回测
            <span className="page-scope-pill">独立工具</span>
          </h1>
          <p>Bitget 公共行情 · Python 策略 · 本地缓存增量更新。日期按 UTC 计算。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>
          返回对话
        </button>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          {error && <div className="error-banner">{error}</div>}

          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>回测参数</h2>
                <p>
                  单标的、现货、历史回测；同一时间只执行一个回测任务，其余排队。
                </p>
              </div>
              <div className="settings-card-actions">
                <button
                  className="ghost-button"
                  onClick={() => void testConnection()}
                  disabled={probing}
                >
                  {probing ? "测试中…" : "测试连接"}
                </button>
                <button
                  className="primary"
                  onClick={() => void runBacktest()}
                  disabled={busy || active}
                >
                  {busy ? "提交中…" : "运行回测"}
                </button>
              </div>
            </div>

            <div className="backtest-form-grid">
              <label className="backtest-field">
                <span>交易对</span>
                <input
                  value={form.symbol}
                  onChange={(event) => update("symbol", event.target.value.toUpperCase())}
                  placeholder="BTCUSDT"
                  autoComplete="off"
                />
              </label>
              <div className="backtest-field">
                <span>K 线周期</span>
                <Select
                  value={form.granularity}
                  options={granularities}
                  onChange={(value) => update("granularity", value)}
                  ariaLabel="K 线周期"
                />
              </div>
              <label className="backtest-field">
                <span>开始日期 (UTC)</span>
                <input
                  type="date"
                  value={form.start}
                  onChange={(event) => update("start", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>结束日期 (UTC，不含)</span>
                <input
                  type="date"
                  value={form.end}
                  onChange={(event) => update("end", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>初始资金 (USDT)</span>
                <input
                  type="number"
                  min="1"
                  step="100"
                  value={form.capital}
                  onChange={(event) => update("capital", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>手续费率 (%)</span>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.feePct}
                  onChange={(event) => update("feePct", event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>滑点 (%)</span>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.slippagePct}
                  onChange={(event) => update("slippagePct", event.target.value)}
                />
              </label>
            </div>

            <label className="field-label" htmlFor="backtest-strategy">
              策略代码
            </label>
            <textarea
              id="backtest-strategy"
              className="backtest-code-input"
              value={form.code}
              onChange={(event) => update("code", event.target.value)}
              spellCheck={false}
            />
            <div className="hint">
              接口：<code>def strategy(df) -&gt; pd.Series</code>。df 为 UTC
              DatetimeIndex，列为 open/high/low/close/volume；返回 [-1, 1]
              的目标仓位（负数为模拟做空）。代码在独立子进程中执行。
            </div>

            <div className="backtest-cache">
              <strong>行情缓存</strong>
              {cacheStatus ? (
                cacheStatus.rows > 0 ? (
                  <span>
                    已缓存 {cacheStatus.rows} 根：{formatUtc(cacheStatus.first_ms, true)} →{" "}
                    {formatUtc(cacheStatus.last_ms, true)} UTC
                    {cacheStatus.oldest_available_ms
                      ? `；Bitget 最早可用 ${formatUtc(cacheStatus.oldest_available_ms)}`
                      : ""}
                  </span>
                ) : (
                  <span>尚未缓存该交易对和周期，运行回测时会自动抓取。</span>
                )
              ) : (
                <span>尚未缓存该交易对和周期。</span>
              )}
              {cacheStatus?.last_error && (
                <span className="tone-bad">上次抓取失败：{cacheStatus.last_error}</span>
              )}
              {probe && (
                <span className={probe.ok ? "tone-good" : "tone-bad"}>
                  {probe.ok
                    ? `Bitget 连接正常${probe.latency_ms !== null ? `（${probe.latency_ms} ms）` : ""}`
                    : `Bitget 连接失败：${probe.error ?? "未知错误"}`}
                  {probe.proxy_configured ? "" : "（未配置 HTTP_PROXY/HTTPS_PROXY）"}
                </span>
              )}
            </div>

            {meta && (
              <div className="hint">
                Bitget：{meta.bitget_base_url} · 最多 {meta.max_bars.toLocaleString()} 根
                K 线 · 交易明细最多展示 {meta.max_trades.toLocaleString()} 笔
              </div>
            )}
            {loading && <div className="hint">正在加载回测配置…</div>}
          </section>

          {current && (
            <section className="settings-card">
              <div className="settings-card-head">
                <div>
                  <h2>回测任务</h2>
                  <p>
                    {current.symbol} · {granularityLabel(current.granularity)} ·{" "}
                    {formatUtc(current.start_ms)} → {formatUtc(current.end_ms)} UTC
                  </p>
                </div>
                <span className={`backtest-status ${current.status}`}>
                  {statusLabel(current.status)}
                </span>
              </div>
              <div className="backtest-status-line">{statusText(current)}</div>
              {current.error && (
                <div className="error-banner">
                  {current.error}
                  {current.error_detail && (
                    <details className="backtest-error-detail">
                      <summary>查看详情</summary>
                      <pre>{current.error_detail}</pre>
                    </details>
                  )}
                </div>
              )}
              {active && (
                <div className="backtest-status-actions">
                  <button
                    className="ghost-button"
                    onClick={() => void cancelBacktest()}
                    disabled={cancelling}
                  >
                    {cancelling ? "取消中…" : "取消回测"}
                  </button>
                </div>
              )}
            </section>
          )}

          {current && result && <BacktestResultPanel detail={current} result={result} />}

          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>历史回测</h2>
                <p>最近 50 条；点击回看结果，运行中的任务会继续在后台执行。</p>
              </div>
            </div>
            {history.length === 0 ? (
              <div className="empty-note">还没有回测记录。</div>
            ) : (
              <div className="backtest-history">
                {history.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    className={`backtest-history-row ${
                      historyView?.detail.id === item.id ? "active" : ""
                    }`}
                    onClick={() => void openBacktest(item.id)}
                    aria-busy={historyLoadingId === item.id}
                    title="查看回测详情"
                  >
                    <span className="backtest-history-title">
                      {item.symbol}
                      <em>{granularityLabel(item.granularity)}</em>
                    </span>
                    <span className="backtest-history-meta">
                      {formatUtc(item.start_ms)} → {formatUtc(item.end_ms)} ·{" "}
                      {item.metrics
                        ? `收益 ${formatPercent(item.metrics.total_return)}`
                        : statusLabel(item.status)}
                      {item.metrics
                        ? ` · 最大回撤 ${formatPercent(item.metrics.max_drawdown)}`
                        : ""}
                    </span>
                    <span className={`backtest-status ${item.status}`}>
                      {historyLoadingId === item.id
                        ? "加载中…"
                        : statusLabel(item.status)}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>

      {historyView && (
        <div className="modal-backdrop" onClick={() => setHistoryView(null)}>
          <div
            className="modal backtest-detail-modal"
            role="dialog"
            aria-modal="true"
            aria-label="回测详情"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-heading backtest-detail-head">
              <div>
                <h2>
                  {historyView.detail.symbol} ·{" "}
                  {granularityLabel(historyView.detail.granularity)}
                </h2>
                <p>
                  {formatUtc(historyView.detail.start_ms)} →{" "}
                  {formatUtc(historyView.detail.end_ms)} UTC
                  <span
                    className={`backtest-status ${historyView.detail.status}`}
                  >
                    {statusLabel(historyView.detail.status)}
                  </span>
                </p>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setHistoryView(null)}
                aria-label="关闭"
                title="关闭"
              >
                ×
              </button>
            </div>
            <div className="backtest-detail-body">
              {historyError && (
                <div className="error-banner">{historyError}</div>
              )}
              {historyView.detail.error && (
                <div className="error-banner">
                  {historyView.detail.error}
                  {historyView.detail.error_detail && (
                    <details className="backtest-error-detail">
                      <summary>查看详情</summary>
                      <pre>{historyView.detail.error_detail}</pre>
                    </details>
                  )}
                </div>
              )}
              {historyView.result ? (
                <BacktestResultPanel
                  detail={historyView.detail}
                  result={historyView.result}
                />
              ) : (
                <div className="backtest-detail-progress">
                  <span
                    className={`backtest-status ${historyView.detail.status}`}
                  >
                    {statusLabel(historyView.detail.status)}
                  </span>
                  <p>{statusText(historyView.detail)}</p>
                  {ACTIVE_STATUSES.includes(historyView.detail.status) && (
                    <button
                      className="ghost-button"
                      onClick={() => void cancelHistoryBacktest()}
                      disabled={cancelling}
                    >
                      {cancelling ? "取消中…" : "取消回测"}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
