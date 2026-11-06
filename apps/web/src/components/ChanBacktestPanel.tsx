import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { ChanBacktestResult } from "../types";
import { EquityChart } from "./BacktestCharts";

type FormState = {
  initialCapital: string;
  stopPct: string;
  riskPct: string;
  rMultiple: string;
  maxHoldBars: string;
  feePct: string;
  slippagePct: string;
};

const DEFAULT_FORM: FormState = {
  initialCapital: "10000",
  stopPct: "1",
  riskPct: "1",
  rMultiple: "2",
  maxHoldBars: "50",
  feePct: "0.1",
  slippagePct: "0.05",
};

const TYPE_LABELS: Record<string, string> = {
  "1buy": "1买",
  "2buy": "2买",
  "3buy": "3买",
  "1sell": "1卖",
  "2sell": "2卖",
  "3sell": "3卖",
};

const REASON_LABELS: Record<string, string> = {
  stop: "止损",
  target: "目标",
  time: "时间止损",
  end: "样本结束",
};

function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
}

function formatNum(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toLocaleString("en-US", {
    maximumFractionDigits: 2,
  })}`;
}

function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", {
    maximumFractionDigits: Math.abs(value) >= 1000 ? 2 : Math.abs(value) >= 1 ? 4 : 8,
  });
}

function formatUtc(ms: number): string {
  if (!ms) return "—";
  return new Date(ms).toLocaleString("zh-CN", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function ChanBacktestPanel({
  symbol,
  granularity,
  bars,
}: {
  symbol: string;
  granularity: string;
  bars: number;
}) {
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [result, setResult] = useState<ChanBacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const effectiveBars = Math.min(bars, 500);

  useEffect(() => {
    setResult(null);
    setError("");
  }, [symbol, granularity, bars]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function run() {
    setError("");
    const initialCapital = Number(form.initialCapital);
    const stopPct = Number(form.stopPct);
    const riskPct = Number(form.riskPct);
    const rMultiple = Number(form.rMultiple);
    const maxHoldBars = Number(form.maxHoldBars);
    const feePct = Number(form.feePct);
    const slippagePct = Number(form.slippagePct);
    if (![initialCapital, stopPct, riskPct, rMultiple, maxHoldBars, feePct, slippagePct].every(Number.isFinite)) {
      setError("参数必须是数字");
      return;
    }
    if (initialCapital <= 0 || stopPct <= 0 || riskPct <= 0 || rMultiple <= 0 || maxHoldBars < 1) {
      setError("资金、止损、风险、盈亏比和持仓时间必须大于 0");
      return;
    }
    setLoading(true);
    try {
      const next = await api.runChanBacktest({
        symbol: symbol.trim().toUpperCase(),
        granularity,
        bars: effectiveBars,
        initial_capital: initialCapital,
        fee_rate: feePct / 100,
        slippage_rate: slippagePct / 100,
        stop_pct: stopPct / 100,
        risk_pct: riskPct / 100,
        r_multiple: rMultiple,
        max_hold_bars: maxHoldBars,
      });
      setResult(next);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>信号回测 / 交易计划</h2>
            <p>
              因果前缀回放：信号收盘确认，下一根开盘进场；一次一笔，固定止损、R 倍目标和时间止损。
              {bars > 500 ? ` 回测最多使用 500 根（当前 ${bars}）。` : ` 当前 ${effectiveBars} 根。`}
            </p>
          </div>
          <div className="settings-card-actions">
            <button className="primary" onClick={() => void run()} disabled={loading}>
              {loading ? "回测中…" : "运行回测"}
            </button>
          </div>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <div className="backtest-form-grid">
          <label className="backtest-field"><span>初始资金</span><input type="number" min="1" step="100" value={form.initialCapital} onChange={(e) => update("initialCapital", e.target.value)} /></label>
          <label className="backtest-field"><span>止损 (%)</span><input type="number" min="0.1" step="0.1" value={form.stopPct} onChange={(e) => update("stopPct", e.target.value)} /></label>
          <label className="backtest-field"><span>单笔风险 (%)</span><input type="number" min="0.1" step="0.1" value={form.riskPct} onChange={(e) => update("riskPct", e.target.value)} /></label>
          <label className="backtest-field"><span>盈亏比 (R)</span><input type="number" min="0.1" step="0.1" value={form.rMultiple} onChange={(e) => update("rMultiple", e.target.value)} /></label>
          <label className="backtest-field"><span>最大持仓 K 线</span><input type="number" min="1" step="1" value={form.maxHoldBars} onChange={(e) => update("maxHoldBars", e.target.value)} /></label>
          <label className="backtest-field"><span>手续费 (%)</span><input type="number" min="0" step="0.01" value={form.feePct} onChange={(e) => update("feePct", e.target.value)} /></label>
          <label className="backtest-field"><span>滑点 (%)</span><input type="number" min="0" step="0.01" value={form.slippagePct} onChange={(e) => update("slippagePct", e.target.value)} /></label>
        </div>
      </section>

      {result ? (
        <>
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>{result.symbol} · {result.granularity === "1week" ? "1w" : result.granularity}</h2>
                <p>
                  {formatUtc(result.start_ms)} → {formatUtc(result.end_ms)} UTC ·{" "}
                  {result.bars} 根 K 线 · 因果信号 {result.causal_signals} 个 · 实际交易{" "}
                  {result.trades} 笔 · 跳过 {result.skipped_signals} 个
                </p>
              </div>
            </div>

            <div className="metric-grid">
              <div><span>总收益</span><strong className={result.total_return >= 0 ? "tone-good" : "tone-bad"}>{formatPct(result.total_return)}</strong></div>
              <div><span>最大回撤</span><strong className="tone-bad">{formatPct(result.max_drawdown)}</strong></div>
              <div><span>胜率</span><strong>{result.win_rate === null ? "—" : formatRatio(result.win_rate)}</strong></div>
              <div><span>平均 R</span><strong>{formatNum(result.avg_r)}</strong></div>
              <div><span>盈亏比</span><strong>{result.profit_factor === null ? "—" : result.profit_factor.toFixed(2)}</strong></div>
              <div><span>累计盈亏</span><strong className={result.total_pnl >= 0 ? "tone-good" : "tone-bad"}>{formatMoney(result.total_pnl)}</strong></div>
              <div><span>暴露</span><strong>{formatRatio(result.exposure)}</strong></div>
              <div><span>最终权益</span><strong>{formatAmount(result.final_equity)}</strong></div>
            </div>

            <EquityChart points={result.equity} height={240} />

            {result.by_type.length > 0 && (
              <div className="backtest-table-wrap">
                <table className="backtest-table">
                  <thead><tr><th>信号</th><th>交易</th><th>胜率</th><th>平均 R</th><th>盈亏比</th><th>累计盈亏</th></tr></thead>
                  <tbody>
                    {result.by_type.map((group) => (
                      <tr key={group.type}>
                        <td>{TYPE_LABELS[group.type] ?? group.type}</td>
                        <td>{group.trades}</td>
                        <td>{group.win_rate === null ? "—" : formatRatio(group.win_rate)}</td>
                        <td>{formatNum(group.avg_r)}</td>
                        <td>{group.profit_factor === null ? "—" : group.profit_factor.toFixed(2)}</td>
                        <td className={group.total_pnl >= 0 ? "tone-good" : "tone-bad"}>{formatMoney(group.total_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {result.warnings.length > 0 && (
              <ul className="backtest-warnings">
                {result.warnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>
            )}
          </section>

          <section className="settings-card">
            <div className="settings-card-head">
              <div><h2>交易明细</h2><p>按信号确认后的下一根开盘进场；止损/目标先到先算，同一根两根都碰到时按止损。</p></div>
            </div>
            <div className="backtest-table-wrap">
              <table className="backtest-table">
                <thead><tr><th>信号</th><th>方向</th><th>确认</th><th>入场</th><th>出场</th><th>R</th><th>盈亏</th><th>原因</th></tr></thead>
                <tbody>
                  {result.trades_detail.length ? result.trades_detail.slice().reverse().map((trade, index) => (
                    <tr key={`${trade.entry_time}-${index}`}>
                      <td>{TYPE_LABELS[trade.signal_type] ?? trade.signal_type}</td>
                      <td>{trade.side === "short" ? "空" : "多"}</td>
                      <td>{formatUtc(trade.confirm_time)}</td>
                      <td>{formatPrice(trade.entry_price)}</td>
                      <td>{formatPrice(trade.exit_price)}</td>
                      <td>{formatNum(trade.r_multiple)}</td>
                      <td className={trade.pnl >= 0 ? "tone-good" : "tone-bad"}>{formatMoney(trade.pnl)}</td>
                      <td>{REASON_LABELS[trade.reason] ?? trade.reason}</td>
                    </tr>
                  )) : (
                    <tr><td colSpan={8} className="journal-empty">这段样本没有触发可执行的交易</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : (
        <section className="settings-card">
          <div className="empty-note">
            {loading ? "正在逐根回放结构并执行交易计划…" : "点「运行回测」查看这套买卖点在扣费后的实际表现。"}
          </div>
        </section>
      )}
    </>
  );
}
