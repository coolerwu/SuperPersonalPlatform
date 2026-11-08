import type {
  AllocationEntry,
  AllocationEntryPayload,
  AllocationPortfolio,
  AllocationPosition,
} from "../types";
import {
  colorForSymbol,
  entryTypeLabel,
  entryTypeTone,
  formatAmount,
  formatDateTime,
  formatPercent,
  formatPp,
  formatPrice,
  formatQuantity,
  marketLabel,
  priceSourceLabel,
} from "./allocationUtils";

type Segment = {
  key: string;
  label: string;
  value: number;
  color?: string;
  residual?: boolean;
};

function actualSegments(portfolio: AllocationPortfolio): Segment[] {
  const segments: Segment[] = portfolio.positions
    .filter((item) => (item.weight_pct ?? 0) > 0.005)
    .map((item) => ({
      key: item.symbol,
      label: item.symbol,
      value: item.weight_pct ?? 0,
      color: colorForSymbol(item.symbol),
    }));
  if ((portfolio.cash_weight_pct ?? 0) > 0.005) {
    segments.push({
      key: "cash",
      label: "现金",
      value: portfolio.cash_weight_pct ?? 0,
      color: "#94a3b8",
    });
  }
  return segments;
}

function targetSegments(portfolio: AllocationPortfolio): Segment[] {
  const segments: Segment[] = Object.entries(portfolio.targets)
    .filter(([, value]) => value > 0.005)
    .map(([symbol, value]) => ({
      key: symbol,
      label: symbol,
      value,
      color: colorForSymbol(symbol),
    }));
  if ((portfolio.cash_target_pct ?? 0) > 0.005) {
    segments.push({
      key: "cash-target",
      label: "现金目标",
      value: portfolio.cash_target_pct ?? 0,
      color: "#cbd5e1",
    });
  }
  if ((portfolio.unallocated_pct ?? 0) > 0.005) {
    segments.push({
      key: "unallocated",
      label: "未分配",
      value: portfolio.unallocated_pct ?? 0,
      residual: true,
    });
  }
  return segments;
}

function driftClass(value: number | null, needed: boolean | null): string {
  if (value === null || needed === null) return "";
  if (!needed) return "ok";
  return value > 0 ? "over" : "under";
}

type AllocationPortfolioSectionProps = {
  portfolio: AllocationPortfolio;
  entries: AllocationEntry[];
  entriesTotal: number;
  entriesLoading: boolean;
  ledgerSymbol: string;
  onLedgerSymbolChange: (symbol: string) => void;
  onNewEntry: (initial?: Partial<AllocationEntryPayload>) => void;
  onEditEntry: (entry: AllocationEntry) => void;
  onDeleteEntry: (entry: AllocationEntry) => void;
  onEditConfig: () => void;
  onRebalance: (position: AllocationPosition) => void;
};

export function AllocationPortfolioSection({
  portfolio,
  entries,
  entriesTotal,
  entriesLoading,
  ledgerSymbol,
  onLedgerSymbolChange,
  onNewEntry,
  onEditEntry,
  onDeleteEntry,
  onEditConfig,
  onRebalance,
}: AllocationPortfolioSectionProps) {
  const warnings = [
    ...portfolio.warnings,
    ...portfolio.positions.flatMap((position) =>
      position.warnings.map((warning) => `${position.symbol}：${warning}`),
    ),
  ];
  const actual = actualSegments(portfolio);
  const targets = targetSegments(portfolio);
  const rebalanceItems = portfolio.positions.filter(
    (item) => item.rebalance_needed && item.rebalance_amount !== null,
  );
  const hasTargets = Boolean(
    Object.keys(portfolio.targets).length > 0 || portfolio.cash_target_pct !== null,
  );
  const cashNeedsRebalance = Boolean(
    portfolio.cash_rebalance_needed && portfolio.cash_rebalance_amount !== null,
  );
  const unrealizedPct =
    portfolio.invested_cost > 0
      ? (portfolio.unrealized_pnl / portfolio.invested_cost) * 100
      : null;

  const ledgerSymbols = (() => {
    const symbols = new Set<string>();
    portfolio.positions.forEach((item) => symbols.add(item.symbol));
    entries.forEach((entry) => {
      if (entry.symbol) symbols.add(entry.symbol);
    });
    return [...symbols].sort();
  })();

  const visibleEntries = ledgerSymbol
    ? entries.filter((entry) => entry.symbol === ledgerSymbol)
    : entries;
  const ledgerAnchor = `allocation-ledger-${portfolio.id}`;

  function renderBar(label: string, segments: Segment[]) {
    const total = segments.reduce((sum, item) => sum + item.value, 0);
    if (!segments.length || total <= 0) {
      return (
        <div className="allocation-bar-row">
          <span className="allocation-bar-label">{label}</span>
          <span className="empty-note">无法计算占比</span>
        </div>
      );
    }
    return (
      <>
        <div className="allocation-bar-row">
          <span className="allocation-bar-label">{label}</span>
          <div className="allocation-bar-track">
            {segments.map((item) => (
              <span
                key={item.key}
                className={`allocation-bar-seg ${item.residual ? "residual" : ""}`}
                style={{
                  width: `${(item.value / total) * 100}%`,
                  background: item.residual ? undefined : item.color,
                }}
                title={`${item.label} ${item.value.toFixed(2)}%`}
              />
            ))}
          </div>
        </div>
        <div className="allocation-legend">
          {segments.map((item) => (
            <span key={item.key} className="allocation-legend-item">
              <i
                className={`allocation-legend-dot ${item.residual ? "residual" : ""}`}
                style={{ background: item.residual ? undefined : item.color }}
              />
              {item.label} {item.value.toFixed(2)}%
            </span>
          ))}
        </div>
      </>
    );
  }

  return (
    <section className="allocation-portfolio-block">
      <div className="allocation-portfolio-head">
        <div className="allocation-portfolio-title">
          <h2>{portfolio.name}</h2>
          <span className="journal-market">{portfolio.currency}</span>
        </div>
        <div className="settings-card-actions">
          <button className="ghost-button" onClick={onEditConfig}>
            编辑配置
          </button>
          <button className="primary" onClick={() => onNewEntry()}>
            + 记一笔
          </button>
        </div>
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
          <span>总资产</span>
          <strong>{formatAmount(portfolio.total_value, portfolio.currency)}</strong>
          <small>现金 + 持仓市值</small>
        </div>
        <div>
          <span>现金</span>
          <strong>{formatAmount(portfolio.cash, portfolio.currency)}</strong>
          <small>{formatPercent(portfolio.cash_weight_pct)} 占比</small>
        </div>
        <div>
          <span>投入本金</span>
          <strong>{formatAmount(portfolio.net_contributions, portfolio.currency)}</strong>
          <small>入金 - 出金 + 初始持仓成本</small>
        </div>
        <div>
          <span>未实现盈亏</span>
          <strong
            className={
              portfolio.unrealized_pnl > 0
                ? "tone-good"
                : portfolio.unrealized_pnl < 0
                  ? "tone-bad"
                  : ""
            }
          >
            {formatAmount(portfolio.unrealized_pnl, portfolio.currency, true)}
          </strong>
          <small>{formatPercent(unrealizedPct, true)}</small>
        </div>
        <div>
          <span>总收益</span>
          <strong
            className={
              portfolio.total_return > 0
                ? "tone-good"
                : portfolio.total_return < 0
                  ? "tone-bad"
                  : ""
            }
          >
            {formatAmount(portfolio.total_return, portfolio.currency, true)}
          </strong>
          <small>
            {formatPercent(portfolio.total_return_pct, true)} · XIRR{" "}
            {formatPercent(portfolio.xirr_pct)}
          </small>
        </div>
      </div>

      <div className="allocation-breakdown">
        <span>
          已实现
          <strong
            className={
              portfolio.realized_pnl > 0
                ? "tone-good"
                : portfolio.realized_pnl < 0
                  ? "tone-bad"
                  : ""
            }
          >
            {formatAmount(portfolio.realized_pnl, portfolio.currency, true)}
          </strong>
        </span>
        <span>
          分红
          <strong
            className={
              portfolio.dividends > 0
                ? "tone-good"
                : portfolio.dividends < 0
                  ? "tone-bad"
                  : ""
            }
          >
            {formatAmount(portfolio.dividends, portfolio.currency, true)}
          </strong>
        </span>
        <span>
          费用
          <strong>{formatAmount(portfolio.fees, portfolio.currency)}</strong>
        </span>
        <small>
          总收益 = 当前总资产 - 投入本金；费用含买入/卖出手续费和费用流水。
        </small>
      </div>

      {portfolio.note && (
        <div className="allocation-note">
          <span>组合逻辑</span>
          <p>{portfolio.note}</p>
        </div>
      )}

      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>实际 vs 目标</h2>
            <p>
              目标占比按总资产计算；偏离超过 ±{portfolio.rebalance_band_pct}pp
              时提示再平衡。
            </p>
          </div>
          <div className="settings-card-actions">
            <span className="allocation-chain">
              目标合计 {formatPercent(portfolio.target_sum_pct)}
              {portfolio.unallocated_pct !== null &&
                portfolio.unallocated_pct > 0.01 && (
                  <em>未分配 {formatPercent(portfolio.unallocated_pct)}</em>
                )}
            </span>
          </div>
        </div>
        <div className="allocation-bars">
          {renderBar("实际", actual)}
          {hasTargets ? (
            renderBar("目标", targets)
          ) : (
            <p className="allocation-field-hint">
              还没有目标配置。点上方「编辑配置」设置目标占比。
            </p>
          )}
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>持仓</h2>
            <p>市值、占比和浮盈由流水和最新价推导；点「流水」筛选该标的的记录。</p>
          </div>
        </div>
        <div className="backtest-table-wrap">
          <table className="backtest-table allocation-table">
            <thead>
              <tr>
                <th>标的</th>
                <th>数量</th>
                <th>均价</th>
                <th>现价</th>
                <th>市值</th>
                <th>占比</th>
                <th>目标</th>
                <th>偏离</th>
                <th>再平衡</th>
                <th>已实现</th>
                <th>流水</th>
              </tr>
            </thead>
            <tbody>
              <tr className="allocation-cash-row">
                <td>
                  <div className="allocation-symbol">
                    现金
                    <span className="journal-market">{portfolio.currency}</span>
                  </div>
                </td>
                <td>—</td>
                <td>—</td>
                <td>—</td>
                <td>{formatAmount(portfolio.cash, portfolio.currency)}</td>
                <td>{formatPercent(portfolio.cash_weight_pct)}</td>
                <td>{formatPercent(portfolio.cash_target_pct)}</td>
                <td>
                  <span
                    className={`allocation-drift ${driftClass(
                      portfolio.cash_drift_pp,
                      portfolio.cash_rebalance_needed,
                    )}`}
                  >
                    {formatPp(portfolio.cash_drift_pp)}
                  </span>
                </td>
                <td>
                  {portfolio.cash_rebalance_amount === null ? (
                    "—"
                  ) : (
                    <span
                      className={
                        portfolio.cash_rebalance_amount >= 0
                          ? "allocation-rebalance sell"
                          : "allocation-rebalance buy"
                      }
                      title="正数表示需要增加现金（卖出资产），负数表示现金过多（买入资产）"
                    >
                      {formatAmount(
                        portfolio.cash_rebalance_amount,
                        portfolio.currency,
                        true,
                      )}
                    </span>
                  )}
                </td>
                <td>—</td>
                <td />
              </tr>
              {portfolio.positions.map((position) => (
                <tr key={position.symbol}>
                  <td>
                    <div className="allocation-symbol">
                      {position.symbol}
                      <span className="journal-market">
                        {marketLabel(position.symbol, position.market)}
                      </span>
                      {position.warnings.length > 0 && (
                        <span
                          className="allocation-warning"
                          title={position.warnings.join("；")}
                        >
                          !
                        </span>
                      )}
                    </div>
                  </td>
                  <td>{formatQuantity(position.quantity)}</td>
                  <td>{formatPrice(position.avg_cost)}</td>
                  <td>
                    <div className="allocation-price">
                      {formatPrice(position.price)}
                      <span
                        className={`allocation-source ${position.price_source}`}
                      >
                        {priceSourceLabel(position.price_source)}
                      </span>
                    </div>
                    {position.price_stale && (
                      <span className="allocation-stale">过期</span>
                    )}
                  </td>
                  <td>{formatAmount(position.market_value, portfolio.currency)}</td>
                  <td>{formatPercent(position.weight_pct)}</td>
                  <td>{formatPercent(position.target_pct)}</td>
                  <td>
                    <span
                      className={`allocation-drift ${driftClass(
                        position.drift_pp,
                        position.rebalance_needed,
                      )}`}
                    >
                      {formatPp(position.drift_pp)}
                    </span>
                  </td>
                  <td>
                    {position.rebalance_amount === null ? (
                      "—"
                    ) : (
                      <span
                        className={
                          position.rebalance_amount >= 0
                            ? "allocation-rebalance buy"
                            : "allocation-rebalance sell"
                        }
                      >
                        {position.rebalance_amount >= 0 ? "买 " : "卖 "}
                        {formatAmount(
                          Math.abs(position.rebalance_amount),
                          portfolio.currency,
                        )}
                      </span>
                    )}
                  </td>
                  <td>
                    <span
                      className={
                        position.realized_pnl > 0
                          ? "tone-good"
                          : position.realized_pnl < 0
                            ? "tone-bad"
                            : ""
                      }
                    >
                      {formatAmount(position.realized_pnl, portfolio.currency, true)}
                    </span>
                  </td>
                  <td>
                    <button
                      className="journal-edit"
                      onClick={() => {
                        onLedgerSymbolChange(position.symbol);
                        document
                          .getElementById(ledgerAnchor)
                          ?.scrollIntoView({ behavior: "smooth", block: "start" });
                      }}
                    >
                      {position.entry_count} 笔
                    </button>
                  </td>
                </tr>
              ))}
              {!portfolio.positions.length && (
                <tr>
                  <td colSpan={11} className="journal-empty">
                    还没有持仓。先记一笔入金，再记买入；已有仓位可以用「初始持仓」录入。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>再平衡提示</h2>
            <p>
              只在偏离超过 ±{portfolio.rebalance_band_pct}pp
              时提示；这里只给建议，不会自动下单。
            </p>
          </div>
        </div>
        {!hasTargets ? (
          <p className="empty-note">设置目标占比后，这里会提示需要买卖的金额。</p>
        ) : !rebalanceItems.length && !cashNeedsRebalance ? (
          <p className="empty-note">当前所有持仓都在再平衡带宽内。</p>
        ) : (
          <div className="allocation-rebalance-list">
            {rebalanceItems.map((position) => (
              <div className="allocation-rebalance-item" key={position.symbol}>
                <div>
                  <strong>{position.symbol}</strong>
                  <span>偏离 {formatPp(position.drift_pp)}</span>
                </div>
                <div
                  className={
                    position.rebalance_amount !== null &&
                    position.rebalance_amount >= 0
                      ? "allocation-rebalance buy"
                      : "allocation-rebalance sell"
                  }
                >
                  {position.rebalance_amount !== null &&
                  position.rebalance_amount >= 0
                    ? "买入"
                    : "卖出"}{" "}
                  {formatAmount(
                    Math.abs(position.rebalance_amount ?? 0),
                    portfolio.currency,
                  )}
                </div>
                <button className="ghost-button" onClick={() => onRebalance(position)}>
                  记一笔
                </button>
              </div>
            ))}
            {cashNeedsRebalance && (
              <div className="allocation-rebalance-item">
                <div>
                  <strong>现金</strong>
                  <span>偏离 {formatPp(portfolio.cash_drift_pp)}</span>
                </div>
                <div className="allocation-rebalance">
                  {formatAmount(
                    portfolio.cash_rebalance_amount,
                    portfolio.currency,
                    true,
                  )}
                </div>
                <span />
              </div>
            )}
          </div>
        )}
      </section>

      <section className="settings-card" id={ledgerAnchor}>
        <div className="settings-card-head">
          <div>
            <h2>流水</h2>
            <p>
              共 {entriesTotal} 笔，显示最近 50 笔；修改历史会整本重放，成本和已实现盈亏会随之变化。
            </p>
          </div>
          <div className="settings-card-actions">
            <select
              className="allocation-ledger-filter"
              value={ledgerSymbol}
              onChange={(event) => onLedgerSymbolChange(event.target.value)}
              aria-label="筛选流水标的"
            >
              <option value="">全部标的</option>
              {ledgerSymbols.map((symbol) => (
                <option key={symbol} value={symbol}>
                  {symbol}
                </option>
              ))}
            </select>
            <button className="primary" onClick={() => onNewEntry()}>
              + 记一笔
            </button>
          </div>
        </div>
        <div className="backtest-table-wrap">
          <table className="backtest-table allocation-ledger-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>类型</th>
                <th>标的</th>
                <th>数量</th>
                <th>价格</th>
                <th>金额</th>
                <th>手续费</th>
                <th>备注</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visibleEntries.map((entry) => (
                <tr key={entry.id}>
                  <td className="journal-time">{formatDateTime(entry.at)}</td>
                  <td>
                    <span className={`allocation-type ${entryTypeTone(entry.type)}`}>
                      {entryTypeLabel(entry.type)}
                    </span>
                  </td>
                  <td>{entry.symbol ?? "—"}</td>
                  <td>
                    {entry.quantity === undefined
                      ? "—"
                      : formatQuantity(entry.quantity)}
                  </td>
                  <td>
                    {entry.price === undefined ? "—" : formatPrice(entry.price)}
                  </td>
                  <td>{formatAmount(entry.amount, portfolio.currency)}</td>
                  <td>
                    {entry.fees
                      ? formatAmount(entry.fees, portfolio.currency)
                      : "—"}
                  </td>
                  <td className="allocation-ledger-note" title={entry.note}>
                    {entry.note || "—"}
                  </td>
                  <td>
                    <button className="journal-edit" onClick={() => onEditEntry(entry)}>
                      编辑
                    </button>
                    <button
                      className="journal-edit danger"
                      onClick={() => onDeleteEntry(entry)}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!visibleEntries.length && (
                <tr>
                  <td colSpan={9} className="journal-empty">
                    {entriesLoading
                      ? "加载中…"
                      : ledgerSymbol
                        ? "这个标的还没有流水。"
                        : "还没有流水。先记一笔入金或初始持仓。"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}
