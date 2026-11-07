import type { AllocationOverview as OverviewData } from "../types";
import {
  formatAmount,
  formatDateTime,
  formatPercent,
  fxSourceLabel,
} from "./allocationUtils";

const DISPLAY_CURRENCY = "USD";

type AllocationOverviewProps = {
  overview: OverviewData;
  fxError?: string;
};

export function AllocationOverview({ overview, fxError }: AllocationOverviewProps) {
  const warnings = [
    ...overview.warnings,
    ...(fxError ? [`汇率刷新失败：${fxError}（继续使用上次汇率）`] : []),
  ];

  return (
    <section className="settings-card allocation-overview">
      <div className="allocation-overview-head">
        <h2>总览</h2>
        <p className="hint">
          按当前汇率把各组合折算成 {overview.display_currency}：USD 与 USDT 按
          1:1，CNY 按 1 USD ≈ {overview.usd_cny.toFixed(4)} CNY。
        </p>
      </div>

      {warnings.length > 0 && (
        <ul className="backtest-warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      <div className="journal-stat-grid">
        <div>
          <span>总资产（{DISPLAY_CURRENCY}）</span>
          <strong>{formatAmount(overview.total_value, DISPLAY_CURRENCY)}</strong>
          <small>现金 + 持仓市值</small>
        </div>
        <div>
          <span>现金</span>
          <strong>{formatAmount(overview.cash, DISPLAY_CURRENCY)}</strong>
          <small>各组合现金折算后合计</small>
        </div>
        <div>
          <span>投入本金</span>
          <strong>{formatAmount(overview.net_contributions, DISPLAY_CURRENCY)}</strong>
          <small>入金 - 出金 + 初始持仓成本</small>
        </div>
        <div>
          <span>浮盈</span>
          <strong>{formatAmount(overview.unrealized_pnl, DISPLAY_CURRENCY, true)}</strong>
          <small>按现价折算</small>
        </div>
        <div>
          <span>已实现</span>
          <strong>{formatAmount(overview.realized_pnl, DISPLAY_CURRENCY, true)}</strong>
          <small>卖出与分红累计</small>
        </div>
        <div>
          <span>总收益</span>
          <strong>{formatAmount(overview.total_return, DISPLAY_CURRENCY, true)}</strong>
          <small>总资产 - 投入本金</small>
        </div>
        <div>
          <span>总收益率</span>
          <strong>{formatPercent(overview.total_return_pct, true)}</strong>
          <small>按投入本金计算</small>
        </div>
        <div>
          <span>XIRR（近似）</span>
          <strong>{formatPercent(overview.xirr_pct, true)}</strong>
          <small>历史现金流按当前汇率折算</small>
        </div>
      </div>

      <div className="allocation-fx-note">
        <span>汇率 1 USD ≈ {overview.usd_cny.toFixed(4)} CNY</span>
        <span>{fxSourceLabel(overview.fx_source)}</span>
        <span>
          {overview.fx_at ? `更新于 ${formatDateTime(overview.fx_at)}` : "尚未自动获取"}
        </span>
        {overview.fx_stale && <span className="allocation-stale">过期</span>}
      </div>

      <h3 className="allocation-overview-title">组合明细</h3>
      <div className="allocation-overview-list">
        {overview.portfolios.map((item) => (
          <div key={item.id} className="allocation-overview-row">
            <div className="allocation-overview-name">
              <strong>{item.name}</strong>
              <small>{item.currency}</small>
            </div>
            <div>
              <span>原币总额</span>
              <strong>{formatAmount(item.total_value, item.currency)}</strong>
            </div>
            <div>
              <span>USD 折算</span>
              <strong>{formatAmount(item.total_value_usd, DISPLAY_CURRENCY)}</strong>
            </div>
            <div>
              <span>现金</span>
              <strong>{formatAmount(item.cash, item.currency)}</strong>
            </div>
            <div>
              <span>占比</span>
              <strong>{formatPercent(item.weight_pct)}</strong>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
