import type { BacktestDetail, BacktestResult } from "../types";
import {
  formatAmount,
  formatNumber,
  formatPercent,
  formatPrice,
  formatUtc,
  tradeSideLabel,
} from "../backtestFormat";
import { DrawdownChart, EquityChart, PriceChart } from "./BacktestCharts";

type MetricTile = {
  label: string;
  value: string;
  tone?: "good" | "bad";
};

export function BacktestResultPanel({
  detail,
  result,
}: {
  detail: BacktestDetail;
  result: BacktestResult;
}) {
  const metrics = result.metrics;
  if (!metrics) {
    return (
      <section className="settings-card">
        <div className="empty-note">回测没有返回指标。</div>
      </section>
    );
  }
  const tiles: MetricTile[] = [
    {
      label: "总收益",
      value: formatPercent(metrics.total_return),
      tone: metrics.total_return >= 0 ? "good" : "bad",
    },
    {
      label: "年化收益",
      value: formatPercent(metrics.annual_return),
      tone: (metrics.annual_return ?? 0) >= 0 ? "good" : "bad",
    },
    { label: "最大回撤", value: formatPercent(metrics.max_drawdown), tone: "bad" },
    { label: "Sharpe", value: formatNumber(metrics.sharpe) },
    { label: "Sortino", value: formatNumber(metrics.sortino) },
    { label: "胜率", value: formatPercent(metrics.win_rate) },
    { label: "交易次数", value: String(metrics.trades) },
    {
      label: "基准买入持有",
      value: formatPercent(metrics.benchmark_return),
      tone: metrics.benchmark_return >= 0 ? "good" : "bad",
    },
    { label: "波动率", value: formatPercent(metrics.volatility) },
    { label: "盈亏比", value: formatNumber(metrics.profit_factor) },
    { label: "平均暴露", value: formatPercent(metrics.avg_exposure) },
    { label: "累计成本", value: formatPercent(metrics.total_cost) },
  ];

  const trades = [...result.trades].reverse();

  return (
    <>
      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>回测结果</h2>
            <p>
              {formatUtc(metrics.start_ms, true)} → {formatUtc(metrics.end_ms, true)} UTC ·{" "}
              {metrics.bars} 根 K 线
              {metrics.warmup_bars ? ` · 预热 ${metrics.warmup_bars} 根` : ""}
              {metrics.missing_bars
                ? ` · 缺失 ${metrics.missing_bars} 根${
                    metrics.gap_count ? `（${metrics.gap_count} 段缺口）` : ""
                  }`
                : ""} · 初始{" "}
              {formatAmount(metrics.initial_capital)} → 最终{" "}
              {formatAmount(metrics.final_equity)} USDT
            </p>
          </div>
        </div>
        <div className="metric-grid backtest-metrics">
          {tiles.map((tile) => (
            <div key={tile.label}>
              <span>{tile.label}</span>
              <strong className={tile.tone ? `tone-${tile.tone}` : ""}>{tile.value}</strong>
            </div>
          ))}
        </div>
        {result.warnings.length > 0 && (
          <ul className="backtest-warnings">
            {result.warnings.map((warning, index) => (
              <li key={`${warning}-${index}`}>{warning}</li>
            ))}
          </ul>
        )}
        {metrics.future_check_ok === true && (
          <div className="hint">
            未来函数检测通过：策略在截断前缀上的信号与完整区间一致。
          </div>
        )}
        {result.trades_truncated && (
          <div className="hint">
            共 {result.trades_total} 笔交易，只展示最近 {result.trades.length} 笔。
          </div>
        )}
        <div className="hint">
          现货数据上的负仓位为模拟做空，未计入借币成本、资金费率与强平风险；结果不构成投资建议。
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>价格与买卖点</h2>
            <p>K 线按 UTC 对齐；标记为仓位变化点，不表示真实成交价格。</p>
          </div>
        </div>
        <PriceChart candles={result.candles} trades={result.trades} />
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>净值与回撤</h2>
            <p>净值曲线与回撤曲线均已下采样，保留主要峰值。</p>
          </div>
        </div>
        <EquityChart points={result.equity} />
        <DrawdownChart points={result.drawdown} />
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>交易明细</h2>
            <p>按仓位符号变化划分交易；未平仓交易按最后收盘价估值。</p>
          </div>
        </div>
        {trades.length === 0 ? (
          <div className="empty-note">策略没有产生交易。</div>
        ) : (
          <div className="backtest-table-wrap">
            <table className="backtest-table">
              <thead>
                <tr>
                  <th>方向</th>
                  <th>开仓</th>
                  <th>平仓</th>
                  <th>开仓价</th>
                  <th>平仓价</th>
                  <th>收益</th>
                  <th>持有 K 线</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade, index) => (
                  <tr key={`${trade.entry_ts}-${index}`}>
                    <td>
                      <span className={`backtest-side ${trade.side}`}>
                        {tradeSideLabel(trade.side)}
                      </span>
                    </td>
                    <td>{formatUtc(trade.entry_ts, true)}</td>
                    <td>{formatUtc(trade.exit_ts, true)}</td>
                    <td>{formatPrice(trade.entry_price)}</td>
                    <td>{formatPrice(trade.exit_price)}</td>
                    <td className={trade.return >= 0 ? "tone-good" : "tone-bad"}>
                      {formatPercent(trade.return)}
                    </td>
                    <td>{trade.bars}</td>
                    <td>{trade.closed ? "已平仓" : "未平仓"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="settings-card">
        <details className="backtest-code">
          <summary>策略代码</summary>
          <pre>{detail.strategy_code}</pre>
        </details>
      </section>
    </>
  );
}
