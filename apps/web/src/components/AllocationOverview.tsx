import type { AllocationOverview as OverviewData } from "../types";
import {
  formatAmount,
  formatDateTime,
  formatPercent,
  formatPrice,
  formatQuantity,
  marketLabel,
  priceSourceLabel,
  fxSourceLabel,
} from "./allocationUtils";

const DISPLAY_CURRENCY = "USD";

type AllocationOverviewProps = {
  overview: OverviewData;
  fxError?: string;
  onShowLedger?: (symbol: string) => void;
};

export function AllocationOverview({
  overview,
  fxError,
  onShowLedger,
}: AllocationOverviewProps) {
  const warnings = [
    ...overview.warnings,
    ...(fxError ? [`汇率刷新失败：${fxError}（继续使用上次汇率）`] : []),
  ];
  const cashWeight =
    overview.total_value > 0 ? (overview.cash / overview.total_value) * 100 : null;

  return (
    <section className="settings-card allocation-overview">
      <div className="allocation-overview-head">
        <h2>总览</h2>
        <p className="hint">
          所有组合按当前汇率折算成 {overview.display_currency} 后合并：USD 与 USDT 按
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
          <small>{formatPercent(cashWeight)} 占比</small>
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

      <h3 className="allocation-overview-title">持仓明细（{DISPLAY_CURRENCY}）</h3>
      <div className="allocation-holdings">
        <div className="allocation-holdings-row cash">
          <div className="allocation-holdings-name">
            <strong>现金</strong>
            <small>各组合折算</small>
          </div>
          <div>
            <span>数量</span>
            <strong>—</strong>
          </div>
          <div>
            <span>均价</span>
            <strong>—</strong>
          </div>
          <div>
            <span>现价</span>
            <strong>—</strong>
          </div>
          <div>
            <span>市值</span>
            <strong>{formatAmount(overview.cash, DISPLAY_CURRENCY)}</strong>
          </div>
          <div>
            <span>占比</span>
            <strong>{formatPercent(cashWeight)}</strong>
          </div>
          <div>
            <span>浮盈</span>
            <strong>—</strong>
          </div>
          <div>
            <span>已实现</span>
            <strong>—</strong>
          </div>
        </div>
        {overview.positions.map((position) => (
          <div className="allocation-holdings-row" key={position.symbol}>
            <div className="allocation-holdings-name">
              <strong>{position.symbol}</strong>
              <small>
                {marketLabel(position.symbol, position.market)}
                {position.portfolio_count > 1
                  ? ` · ${position.portfolio_count} 个组合`
                  : ""}
              </small>
              {onShowLedger && (
                <button
                  className="journal-edit"
                  onClick={() => onShowLedger(position.symbol)}
                >
                  流水
                </button>
              )}
            </div>
            <div>
              <span>数量</span>
              <strong>{formatQuantity(position.quantity)}</strong>
            </div>
            <div>
              <span>均价</span>
              <strong>{formatPrice(position.avg_cost_usd)}</strong>
            </div>
            <div>
              <span>现价</span>
              <strong>
                {formatPrice(position.price_usd)}
                <i className={`allocation-source ${position.price_source}`}>
                  {priceSourceLabel(position.price_source)}
                </i>
              </strong>
              {position.price_stale && (
                <span className="allocation-stale">过期</span>
              )}
            </div>
            <div>
              <span>市值</span>
              <strong>
                {formatAmount(position.market_value_usd, DISPLAY_CURRENCY)}
              </strong>
            </div>
            <div>
              <span>占比</span>
              <strong>{formatPercent(position.weight_pct)}</strong>
            </div>
            <div>
              <span>浮盈</span>
              <strong
                className={
                  (position.unrealized_pnl_usd ?? 0) > 0
                    ? "tone-good"
                    : (position.unrealized_pnl_usd ?? 0) < 0
                      ? "tone-bad"
                      : ""
                }
              >
                {formatAmount(
                  position.unrealized_pnl_usd,
                  DISPLAY_CURRENCY,
                  true,
                )}
              </strong>
            </div>
            <div>
              <span>已实现</span>
              <strong
                className={
                  position.realized_pnl_usd > 0
                    ? "tone-good"
                    : position.realized_pnl_usd < 0
                      ? "tone-bad"
                      : ""
                }
              >
                {formatAmount(
                  position.realized_pnl_usd,
                  DISPLAY_CURRENCY,
                  true,
                )}
              </strong>
            </div>
          </div>
        ))}
        {!overview.positions.length && (
          <div className="empty-note">
            还没有持仓。记一笔入金或初始持仓后，这里会显示合并后的持仓明细。
          </div>
        )}
      </div>
    </section>
  );
}
