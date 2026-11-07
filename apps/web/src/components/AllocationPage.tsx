import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, errorText } from "../api";
import type {
  AllocationEntry,
  AllocationEntryPayload,
  AllocationPortfolio,
  AllocationPortfolioConfig,
  AllocationPosition,
  AllocationSnapshot,
} from "../types";
import { AllocationEntryModal } from "./AllocationEntryModal";
import { AllocationPortfolioModal } from "./AllocationPortfolioModal";
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
import { ConfirmDialog } from "./ConfirmDialog";

// Auto-refresh throttle for quote requests across remounts; this is not a
// price cache, so a browser reload starts a fresh request.
const AUTO_REFRESH_INTERVAL_MS = 60_000;
let lastAutoRefreshAt = 0;

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

export function AllocationPage() {
  const [snapshot, setSnapshot] = useState<AllocationSnapshot | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [entries, setEntries] = useState<AllocationEntry[]>([]);
  const [entriesTotal, setEntriesTotal] = useState(0);
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [ledgerSymbol, setLedgerSymbol] = useState("");
  const [portfolioModalOpen, setPortfolioModalOpen] = useState(false);
  const [portfolioModalMode, setPortfolioModalMode] = useState<"create" | "edit">(
    "create",
  );
  const [entryModalOpen, setEntryModalOpen] = useState(false);
  const [editingEntry, setEditingEntry] = useState<AllocationEntry | null>(null);
  const [entryInitial, setEntryInitial] = useState<
    Partial<AllocationEntryPayload> | undefined
  >(undefined);
  const [deleteEntry, setDeleteEntry] = useState<AllocationEntry | null>(null);
  const [deletePortfolioOpen, setDeletePortfolioOpen] = useState(false);
  const autoRefreshAttempted = useRef(false);

  const portfolio = useMemo(() => {
    if (!snapshot) return null;
    return (
      snapshot.portfolios.find((item) => item.id === selectedId) ??
      snapshot.portfolios[0] ??
      null
    );
  }, [snapshot, selectedId]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.getAllocation();
      setSnapshot(data);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadEntries = useCallback(async (portfolioId: string) => {
    if (!portfolioId) {
      setEntries([]);
      setEntriesTotal(0);
      return;
    }
    setEntriesLoading(true);
    try {
      const data = await api.listAllocationEntries(portfolioId, { limit: 50 });
      setEntries(data.items);
      setEntriesTotal(data.total);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setEntriesLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Fetch fresh quotes when the page opens so the user does not have to click
  // refresh every visit. Throttled because a request can be slow and switching
  // pages remounts this component.
  useEffect(() => {
    if (!snapshot || autoRefreshAttempted.current) return;
    if (!snapshot.portfolios.length) return;
    autoRefreshAttempted.current = true;
    const now = Date.now();
    if (now - lastAutoRefreshAt < AUTO_REFRESH_INTERVAL_MS) return;
    lastAutoRefreshAt = now;
    void refreshPrices();
  }, [snapshot]);

  useEffect(() => {
    if (!snapshot) return;
    if (!snapshot.portfolios.length) {
      if (selectedId) setSelectedId("");
      return;
    }
    if (!snapshot.portfolios.some((item) => item.id === selectedId)) {
      setSelectedId(snapshot.portfolios[0].id);
    }
  }, [snapshot, selectedId]);

  useEffect(() => {
    const portfolioId = portfolio?.id;
    if (!portfolioId) {
      setEntries([]);
      setEntriesTotal(0);
      return;
    }
    setLedgerSymbol("");
    void loadEntries(portfolioId);
  }, [portfolio?.id, loadEntries]);

  async function refreshPrices() {
    if (!portfolio) return;
    setRefreshing(true);
    setError("");
    setNotice("");
    try {
      const data = await api.refreshAllocation();
      setSnapshot(data);
      const failed = data.price_errors?.length ?? 0;
      setNotice(
        failed
          ? `全部账本行情已刷新，${failed} 个标的失败`
          : "全部账本行情已刷新",
      );
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setRefreshing(false);
    }
  }

  async function onPortfolioSaved(saved: AllocationPortfolioConfig) {
    setPortfolioModalOpen(false);
    setNotice("组合配置已保存");
    await load();
    setSelectedId(saved.id);
  }

  async function onEntrySaved() {
    setEntryModalOpen(false);
    setEditingEntry(null);
    setEntryInitial(undefined);
    setNotice("流水已保存");
    await load();
    if (portfolio) await loadEntries(portfolio.id);
  }

  function openCreatePortfolio() {
    setPortfolioModalMode("create");
    setPortfolioModalOpen(true);
  }

  function openEditPortfolio() {
    if (!portfolio) return;
    setPortfolioModalMode("edit");
    setPortfolioModalOpen(true);
  }

  function openNewEntry(initial?: Partial<AllocationEntryPayload>) {
    setEditingEntry(null);
    setEntryInitial(initial);
    setEntryModalOpen(true);
  }

  function openEditEntry(entry: AllocationEntry) {
    setEditingEntry(entry);
    setEntryInitial(undefined);
    setEntryModalOpen(true);
  }

  async function confirmDeleteEntry() {
    if (!deleteEntry || !portfolio) return;
    const entry = deleteEntry;
    setDeleteEntry(null);
    try {
      await api.deleteAllocationEntry(portfolio.id, entry.id);
      setNotice("流水已删除");
      await load();
      await loadEntries(portfolio.id);
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  async function confirmDeletePortfolio() {
    if (!portfolio) return;
    setDeletePortfolioOpen(false);
    try {
      await api.deleteAllocationPortfolio(portfolio.id);
      setPortfolioModalOpen(false);
      setSelectedId("");
      setNotice("组合已删除");
      await load();
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  function openRebalance(position: AllocationPosition) {
    if (!portfolio || position.rebalance_amount === null) return;
    const side = position.rebalance_amount >= 0 ? "buy" : "sell";
    const initial: Partial<AllocationEntryPayload> = {
      type: side,
      symbol: position.symbol,
    };
    if (position.price && position.price > 0) {
      const suggested = Number(
        (Math.abs(position.rebalance_amount) / position.price).toFixed(8),
      );
      initial.price = position.price;
      initial.quantity =
        side === "sell"
          ? Math.min(suggested, position.quantity)
          : suggested;
    }
    openNewEntry(initial);
  }

  const ledgerSymbols = useMemo(() => {
    const symbols = new Set<string>();
    portfolio?.positions.forEach((item) => symbols.add(item.symbol));
    entries.forEach((entry) => {
      if (entry.symbol) symbols.add(entry.symbol);
    });
    return [...symbols].sort();
  }, [portfolio, entries]);

  const visibleEntries = useMemo(
    () =>
      ledgerSymbol
        ? entries.filter((entry) => entry.symbol === ledgerSymbol)
        : entries,
    [entries, ledgerSymbol],
  );

  const warnings = useMemo(() => {
    const values: string[] = [];
    if (portfolio) {
      values.push(...portfolio.warnings);
      portfolio.positions.forEach((position) => {
        position.warnings.forEach((warning) =>
          values.push(`${position.symbol}：${warning}`),
        );
      });
    }
    (snapshot?.price_errors ?? []).forEach((item) =>
      values.push(`${item.symbol}：${item.error}`),
    );
    return [...new Set(values)];
  }, [portfolio, snapshot]);

  const actual = portfolio ? actualSegments(portfolio) : [];
  const targets = portfolio ? targetSegments(portfolio) : [];
  const rebalanceItems =
    portfolio?.positions.filter(
      (item) => item.rebalance_needed && item.rebalance_amount !== null,
    ) ?? [];
  const hasTargets = Boolean(
    portfolio &&
      (Object.keys(portfolio.targets).length > 0 ||
        portfolio.cash_target_pct !== null),
  );
  const cashNeedsRebalance = Boolean(
    portfolio?.cash_rebalance_needed && portfolio.cash_rebalance_amount !== null,
  );
  const unrealizedPct =
    portfolio && portfolio.invested_cost > 0
      ? (portfolio.unrealized_pnl / portfolio.invested_cost) * 100
      : null;

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
    <main className="backtest-page allocation-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">FEATURES</div>
          <h1>
            资产配置
            <span className="page-scope-pill">长期组合</span>
          </h1>
          <p>
            独立于交易记录的核心组合账本：记录入金、买入、卖出和分红，
            按目标权重检查偏离。
          </p>
        </div>
        <div className="settings-card-actions">
          <button
            className="ghost-button"
            onClick={() => void refreshPrices()}
            disabled={!portfolio || refreshing}
            title="一次性拉取所有组合的最新加密行情；A 股和手动价不会自动更新"
          >
            {refreshing ? "刷新中…" : "刷新全部账本"}
          </button>
          <button
            className="ghost-button"
            onClick={openEditPortfolio}
            disabled={!portfolio}
          >
            编辑配置
          </button>
          <button
            className="primary"
            onClick={() => openNewEntry()}
            disabled={!portfolio}
          >
            + 记一笔
          </button>
        </div>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          {error && <div className="error-banner">{error}</div>}
          {notice && <div className="journal-notice">{notice}</div>}

          {snapshot && snapshot.portfolios.length > 0 && (
            <div className="allocation-portfolio-bar">
              <div className="allocation-tabs">
                {snapshot.portfolios.map((item) => (
                  <button
                    key={item.id}
                    className={`allocation-tab ${
                      item.id === portfolio?.id ? "active" : ""
                    }`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <span>{item.name}</span>
                    <small>{item.currency}</small>
                  </button>
                ))}
              </div>
              <button className="ghost-button" onClick={openCreatePortfolio}>
                + 新建组合
              </button>
            </div>
          )}

          {loading ? (
            <div className="empty-note">加载中…</div>
          ) : !portfolio ? (
            error ? (
              <section className="settings-card allocation-empty">
                <h2>资产配置加载失败</h2>
                <p>请检查服务状态或稍后重试；上面的错误信息可以帮助定位问题。</p>
                <button className="primary" onClick={() => void load()}>
                  重试
                </button>
              </section>
            ) : (
              <section className="settings-card allocation-empty">
                <h2>创建你的第一个长期组合</h2>
                <p>
                  资产配置独立于交易记录：它记录的是长期资金、持仓、目标比例和再平衡纪律，
                  不读取交易记录里的短线仓位。
                </p>
                <button className="primary" onClick={openCreatePortfolio}>
                  + 新建组合
                </button>
              </section>
            )
          ) : portfolio.error ? (
            <section className="settings-card allocation-empty">
              <h2>这个组合的流水无法重放</h2>
              <p>{portfolio.error}</p>
              <p>请检查 data/allocation-ledger.jsonl 中的流水，修正后刷新。</p>
            </section>
          ) : (
            <>
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
                      还没有目标配置。点右上角「编辑配置」设置目标占比。
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
                            <span className="journal-market">
                              {portfolio.currency}
                            </span>
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
                          <td>
                            {formatAmount(position.market_value, portfolio.currency)}
                          </td>
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
                                setLedgerSymbol(position.symbol);
                                document
                                  .getElementById("allocation-ledger")
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
                        <button
                          className="ghost-button"
                          onClick={() => openRebalance(position)}
                        >
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

              <section className="settings-card" id="allocation-ledger">
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
                      onChange={(event) => setLedgerSymbol(event.target.value)}
                      aria-label="筛选流水标的"
                    >
                      <option value="">全部标的</option>
                      {ledgerSymbols.map((symbol) => (
                        <option key={symbol} value={symbol}>
                          {symbol}
                        </option>
                      ))}
                    </select>
                    <button className="primary" onClick={() => openNewEntry()}>
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
                            {entry.price === undefined
                              ? "—"
                              : formatPrice(entry.price)}
                          </td>
                          <td>{formatAmount(entry.amount, portfolio.currency)}</td>
                          <td>
                            {entry.fees
                              ? formatAmount(entry.fees, portfolio.currency)
                              : "—"}
                          </td>
                          <td
                            className="allocation-ledger-note"
                            title={entry.note}
                          >
                            {entry.note || "—"}
                          </td>
                          <td>
                            <button
                              className="journal-edit"
                              onClick={() => openEditEntry(entry)}
                            >
                              编辑
                            </button>
                            <button
                              className="journal-edit danger"
                              onClick={() => setDeleteEntry(entry)}
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

              <section className="settings-card allocation-notes">
                <div className="settings-card-head">
                  <div>
                    <h2>口径与边界</h2>
                    <p>这是一本长期账本，不是券商账户。</p>
                  </div>
                </div>
                <ul className="backtest-warnings">
                  <li>与交易记录完全分开：交易记录里的短线仓位不会出现在这里。</li>
                  <li>现金、持仓和收益都由流水推导；漏记会让现金、成本和收益失真。</li>
                  <li>卖出按移动加权成本结转已实现盈亏，不做税务批次。</li>
                  <li>分红单独统计，不冲减成本；费用计入成本或单独扣现金。</li>
                  <li>A 股没有自动行情，按手动价、缓存或成本估值；加密价格刷新失败会回退。</li>
                  <li>美股价格先取东方财富，失败再用 Yahoo Finance 兜底（非官方接口），按 USD 计；组合币种是 USDT 时近似按 1:1 处理，其他币种不做汇率换算。失败时回退手动价/成本。</li>
                  <li>顶部「刷新全部账本」会一次性拉取所有组合的最新加密行情；A 股、手动价和利息不会自动更新。</li>
                  <li>现金可以为负，说明有入金未记录或用了外部资金；XIRR 在样本期不足 30 天时仅供参考。</li>
                </ul>
              </section>
            </>
          )}
        </div>
      </div>

      <AllocationPortfolioModal
        open={portfolioModalOpen}
        portfolio={portfolioModalMode === "edit" ? portfolio : null}
        onClose={() => setPortfolioModalOpen(false)}
        onSaved={(saved) => void onPortfolioSaved(saved)}
        onDelete={
          portfolio && portfolio.entry_count === 0
            ? () => setDeletePortfolioOpen(true)
            : undefined
        }
      />

      {portfolio && (
        <AllocationEntryModal
          open={entryModalOpen}
          portfolio={portfolio}
          entry={editingEntry}
          initial={entryInitial}
          onClose={() => {
            setEntryModalOpen(false);
            setEditingEntry(null);
            setEntryInitial(undefined);
          }}
          onSaved={() => void onEntrySaved()}
        />
      )}

      <ConfirmDialog
        open={Boolean(deleteEntry)}
        title="删除这笔流水？"
        description={
          deleteEntry
            ? `${entryTypeLabel(deleteEntry.type)} ${deleteEntry.symbol ?? ""} ${
                deleteEntry.quantity ?? ""
              } ${deleteEntry.at}。删除后会整本重放，可能改变之后的成本和已实现盈亏。`
            : ""
        }
        confirmLabel="删除"
        danger
        onConfirm={() => void confirmDeleteEntry()}
        onClose={() => setDeleteEntry(null)}
      />

      <ConfirmDialog
        open={deletePortfolioOpen}
        title="删除这个组合？"
        description="组合没有流水，删除后配置不可恢复。"
        confirmLabel="删除"
        danger
        onConfirm={() => void confirmDeletePortfolio()}
        onClose={() => setDeletePortfolioOpen(false)}
      />
    </main>
  );
}
