import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, errorText } from "../api";
import type {
  AllocationEntry,
  AllocationEntryPayload,
  AllocationPortfolio,
  AllocationSnapshot,
} from "../types";
import { AllocationEntryModal } from "./AllocationEntryModal";
import { AllocationOverview } from "./AllocationOverview";
import { AllocationPortfolioModal } from "./AllocationPortfolioModal";
import {
  entryTypeLabel,
  entryTypeTone,
  formatAmount,
  formatDateTime,
  formatPercent,
  formatPp,
  formatPrice,
  formatQuantity,
} from "./allocationUtils";
import { ConfirmDialog } from "./ConfirmDialog";

// Auto-refresh throttle for quote requests across remounts; this is not a
// price cache, so a browser reload starts a fresh request.
const AUTO_REFRESH_INTERVAL_MS = 60_000;
let lastAutoRefreshAt = 0;

type EntriesState = {
  items: AllocationEntry[];
  total: number;
  loading: boolean;
};

export function AllocationPage() {
  const [snapshot, setSnapshot] = useState<AllocationSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [entriesByPortfolio, setEntriesByPortfolio] = useState<
    Record<string, EntriesState>
  >({});
  const [ledgerSymbol, setLedgerSymbol] = useState("");
  const [ledgerPortfolio, setLedgerPortfolio] = useState("");
  const [newEntryPortfolioId, setNewEntryPortfolioId] = useState("");
  const [portfolioModalOpen, setPortfolioModalOpen] = useState(false);
  const [portfolioModalMode, setPortfolioModalMode] = useState<"create" | "edit">(
    "create",
  );
  const [configTarget, setConfigTarget] = useState<AllocationPortfolio | null>(null);
  const [entryModalOpen, setEntryModalOpen] = useState(false);
  const [entryPortfolio, setEntryPortfolio] = useState<AllocationPortfolio | null>(
    null,
  );
  const [editingEntry, setEditingEntry] = useState<AllocationEntry | null>(null);
  const [entryInitial, setEntryInitial] = useState<
    Partial<AllocationEntryPayload> | undefined
  >(undefined);
  const [deleteEntry, setDeleteEntry] = useState<{
    portfolio: AllocationPortfolio;
    entry: AllocationEntry;
  } | null>(null);
  const [deletePortfolioTarget, setDeletePortfolioTarget] =
    useState<AllocationPortfolio | null>(null);
  const autoRefreshAttempted = useRef(false);

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
    setEntriesByPortfolio((current) => ({
      ...current,
      [portfolioId]: {
        items: current[portfolioId]?.items ?? [],
        total: current[portfolioId]?.total ?? 0,
        loading: true,
      },
    }));
    try {
      const data = await api.listAllocationEntries(portfolioId, { limit: 50 });
      setEntriesByPortfolio((current) => ({
        ...current,
        [portfolioId]: { items: data.items, total: data.total, loading: false },
      }));
    } catch (exc) {
      setError(errorText(exc));
      setEntriesByPortfolio((current) => ({
        ...current,
        [portfolioId]: {
          items: current[portfolioId]?.items ?? [],
          total: current[portfolioId]?.total ?? 0,
          loading: false,
        },
      }));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const portfolioIdsKey = useMemo(
    () => (snapshot?.portfolios ?? []).map((item) => item.id).join("|"),
    [snapshot],
  );

  useEffect(() => {
    if (!snapshot) return;
    void Promise.all(snapshot.portfolios.map((item) => loadEntries(item.id)));
    // Only refetch when the set of portfolios changes; entry edits reload the
    // affected ledger explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolioIdsKey]);

  useEffect(() => {
    if (!snapshot) return;
    if (!snapshot.portfolios.some((item) => item.id === newEntryPortfolioId)) {
      setNewEntryPortfolioId(snapshot.portfolios[0]?.id ?? "");
    }
  }, [snapshot, newEntryPortfolioId]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

  async function refreshPrices() {
    if (!snapshot?.portfolios.length) return;
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

  async function onPortfolioSaved() {
    setPortfolioModalOpen(false);
    setConfigTarget(null);
    setNotice("组合配置已保存");
    await load();
  }

  async function onEntrySaved() {
    const target = entryPortfolio;
    setEntryModalOpen(false);
    setEditingEntry(null);
    setEntryInitial(undefined);
    setNotice("流水已保存");
    await load();
    if (target) await loadEntries(target.id);
  }

  function openCreatePortfolio() {
    setConfigTarget(null);
    setPortfolioModalMode("create");
    setPortfolioModalOpen(true);
  }

  function openEditPortfolio(target: AllocationPortfolio) {
    setConfigTarget(target);
    setPortfolioModalMode("edit");
    setPortfolioModalOpen(true);
  }

  function openNewEntry(
    target: AllocationPortfolio,
    initial?: Partial<AllocationEntryPayload>,
  ) {
    setEntryPortfolio(target);
    setEditingEntry(null);
    setEntryInitial(initial);
    setEntryModalOpen(true);
  }

  function openEditEntry(entry: AllocationEntry, target: AllocationPortfolio) {
    setEntryPortfolio(target);
    setEditingEntry(entry);
    setEntryInitial(undefined);
    setEntryModalOpen(true);
  }

  function showLedgerForSymbol(symbol: string) {
    setLedgerSymbol(symbol);
    setLedgerPortfolio("");
    document
      .getElementById("allocation-ledger")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function confirmDeleteEntry() {
    if (!deleteEntry) return;
    const target = deleteEntry;
    setDeleteEntry(null);
    try {
      await api.deleteAllocationEntry(target.portfolio.id, target.entry.id);
      setNotice("流水已删除");
      await load();
      await loadEntries(target.portfolio.id);
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  async function confirmDeletePortfolio() {
    if (!deletePortfolioTarget) return;
    const target = deletePortfolioTarget;
    setDeletePortfolioTarget(null);
    try {
      await api.deleteAllocationPortfolio(target.id);
      setPortfolioModalOpen(false);
      setConfigTarget(null);
      setNotice("组合已删除");
      await load();
    } catch (exc) {
      setError(errorText(exc));
    }
  }

  const overview = snapshot?.overview;
  const rateByPortfolio = useMemo(() => {
    const map = new Map<string, number>();
    (overview?.portfolios ?? []).forEach((item) =>
      map.set(item.id, item.rate_to_usd),
    );
    return map;
  }, [overview]);

  const mergedEntries = useMemo(() => {
    const rows: { entry: AllocationEntry; portfolio: AllocationPortfolio }[] = [];
    (snapshot?.portfolios ?? []).forEach((portfolio) => {
      const state = entriesByPortfolio[portfolio.id];
      if (!state) return;
      state.items.forEach((entry) => rows.push({ entry, portfolio }));
    });
    rows.sort((left, right) => {
      if (left.entry.at !== right.entry.at) {
        return left.entry.at < right.entry.at ? 1 : -1;
      }
      return right.entry.id.localeCompare(left.entry.id);
    });
    return rows;
  }, [snapshot, entriesByPortfolio]);

  const ledgerSymbols = useMemo(() => {
    const symbols = new Set<string>();
    mergedEntries.forEach(({ entry }) => {
      if (entry.symbol) symbols.add(entry.symbol);
    });
    return [...symbols].sort();
  }, [mergedEntries]);

  const visibleEntries = mergedEntries.filter(
    ({ entry, portfolio }) =>
      (!ledgerSymbol || entry.symbol === ledgerSymbol) &&
      (!ledgerPortfolio || portfolio.id === ledgerPortfolio),
  );

  const entriesLoading = Object.values(entriesByPortfolio).some(
    (state) => state.loading,
  );

  const hasPortfolios = Boolean(snapshot?.portfolios.length);

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
            独立于交易记录的核心组合账本：所有组合按当前汇率折算成 USD，
            合并成一份持仓和流水明细。
          </p>
        </div>
        <div className="settings-card-actions">
          <button
            className="ghost-button"
            onClick={() => void refreshPrices()}
            disabled={refreshing || !hasPortfolios}
            title="一次性拉取所有组合的最新行情和 USD/CNY 汇率；A 股和手动价不会自动更新"
          >
            {refreshing ? "刷新中…" : "刷新全部账本"}
          </button>
          <button className="primary" onClick={openCreatePortfolio}>
            + 新建组合
          </button>
        </div>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          {error && <div className="error-banner">{error}</div>}
          {notice && <div className="journal-notice">{notice}</div>}

          {loading && !snapshot ? (
            <div className="empty-note">加载中…</div>
          ) : !snapshot ? (
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
          ) : !snapshot.portfolios.length ? (
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
          ) : (
            <>
              {snapshot.price_errors.length > 0 && (
                <ul className="backtest-warnings">
                  {snapshot.price_errors.map((item) => (
                    <li key={item.symbol}>
                      {item.symbol}：{item.error}
                    </li>
                  ))}
                </ul>
              )}

              <AllocationOverview
                overview={snapshot.overview}
                fxError={snapshot.fx_error}
                onShowLedger={showLedgerForSymbol}
              />

              <section className="settings-card" id="allocation-ledger">
                <div className="settings-card-head">
                  <div>
                    <h2>流水</h2>
                    <p>
                      所有组合合并显示，金额和价格按当前汇率折算成 USD；修改历史会整本重放。
                    </p>
                  </div>
                  <div className="settings-card-actions">
                    <select
                      className="allocation-ledger-filter"
                      value={ledgerPortfolio}
                      onChange={(event) => setLedgerPortfolio(event.target.value)}
                      aria-label="筛选组合"
                    >
                      <option value="">全部组合</option>
                      {snapshot.portfolios.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                    <select
                      className="allocation-ledger-filter"
                      value={ledgerSymbol}
                      onChange={(event) => setLedgerSymbol(event.target.value)}
                      aria-label="筛选标的"
                    >
                      <option value="">全部标的</option>
                      {ledgerSymbols.map((symbol) => (
                        <option key={symbol} value={symbol}>
                          {symbol}
                        </option>
                      ))}
                    </select>
                    <select
                      className="allocation-ledger-filter"
                      value={newEntryPortfolioId}
                      onChange={(event) => setNewEntryPortfolioId(event.target.value)}
                      aria-label="记到哪个组合"
                    >
                      {snapshot.portfolios.map((item) => (
                        <option key={item.id} value={item.id}>
                          记到 {item.name}
                        </option>
                      ))}
                    </select>
                    <button
                      className="primary"
                      onClick={() => {
                        const target = snapshot.portfolios.find(
                          (item) => item.id === newEntryPortfolioId,
                        );
                        if (target) openNewEntry(target);
                      }}
                      disabled={!newEntryPortfolioId}
                    >
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
                      {visibleEntries.map(({ entry, portfolio }) => {
                        const rate = rateByPortfolio.get(portfolio.id);
                        const factor = rate ?? 1;
                        const displayCurrency = rate === undefined ? portfolio.currency : "USD";
                        return (
                          <tr key={entry.id}>
                            <td className="journal-time">
                              {formatDateTime(entry.at)}
                              <small className="allocation-ledger-portfolio">
                                {portfolio.name}
                              </small>
                            </td>
                            <td>
                              <span
                                className={`allocation-type ${entryTypeTone(entry.type)}`}
                              >
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
                                : formatPrice(entry.price * factor)}
                            </td>
                            <td>
                              {entry.amount === undefined
                                ? "—"
                                : formatAmount(entry.amount * factor, displayCurrency)}
                            </td>
                            <td>
                              {entry.fees
                                ? formatAmount(entry.fees * factor, displayCurrency)
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
                                onClick={() => openEditEntry(entry, portfolio)}
                              >
                                编辑
                              </button>
                              <button
                                className="journal-edit danger"
                                onClick={() =>
                                  setDeleteEntry({ portfolio, entry })
                                }
                              >
                                删除
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                      {!visibleEntries.length && (
                        <tr>
                          <td colSpan={9} className="journal-empty">
                            {entriesLoading
                              ? "加载中…"
                              : ledgerSymbol || ledgerPortfolio
                                ? "没有符合筛选条件的流水。"
                                : "还没有流水。先记一笔入金或初始持仓。"}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="settings-card" id="allocation-portfolios">
                <div className="settings-card-head">
                  <div>
                    <h2>组合设置</h2>
                    <p>
                      每个组合单独计算目标、偏离和再平衡；持仓明细已在上方按 USD 合并。
                    </p>
                  </div>
                </div>
                <div className="allocation-portfolio-list">
                  {snapshot.portfolios.map((item) => {
                    const rate = rateByPortfolio.get(item.id);
                    const rebalanceItems = item.positions.filter(
                      (position) =>
                        position.rebalance_needed &&
                        position.rebalance_amount !== null,
                    );
                    return (
                      <div className="allocation-portfolio-row" key={item.id}>
                        <div className="allocation-portfolio-summary">
                          <div className="allocation-portfolio-name">
                            <strong>{item.name}</strong>
                            <small>{item.currency}</small>
                          </div>
                          <div>
                            <span>原币总额</span>
                            <strong>
                              {formatAmount(item.total_value, item.currency)}
                            </strong>
                          </div>
                          <div>
                            <span>USD 折算</span>
                            <strong>
                              {rate === undefined
                                ? "—"
                                : formatAmount(item.total_value * rate, "USD")}
                            </strong>
                          </div>
                          <div>
                            <span>目标合计</span>
                            <strong>{formatPercent(item.target_sum_pct)}</strong>
                          </div>
                          <div className="settings-card-actions">
                            <button
                              className="ghost-button"
                              onClick={() => openEditPortfolio(item)}
                            >
                              编辑配置
                            </button>
                            <button
                              className="ghost-button"
                              onClick={() => openNewEntry(item)}
                            >
                              记一笔
                            </button>
                          </div>
                        </div>
                        {item.error ? (
                          <p className="error-banner">{item.error}</p>
                        ) : rebalanceItems.length || item.cash_rebalance_needed ? (
                          <div className="allocation-rebalance-list compact">
                            {rebalanceItems.map((position) => (
                              <div
                                className="allocation-rebalance-item"
                                key={position.symbol}
                              >
                                <div>
                                  <strong>{position.symbol}</strong>
                                  <span>偏离 {formatPp(position.drift_pp)}</span>
                                </div>
                                <div
                                  className={
                                    (position.rebalance_amount ?? 0) >= 0
                                      ? "allocation-rebalance buy"
                                      : "allocation-rebalance sell"
                                  }
                                >
                                  {(position.rebalance_amount ?? 0) >= 0
                                    ? "买入"
                                    : "卖出"}{" "}
                                  {formatAmount(
                                    Math.abs(position.rebalance_amount ?? 0),
                                    item.currency,
                                  )}
                                </div>
                                <button
                                  className="ghost-button"
                                  onClick={() => {
                                    const side =
                                      (position.rebalance_amount ?? 0) >= 0
                                        ? "buy"
                                        : "sell";
                                    const initial: Partial<AllocationEntryPayload> =
                                      { type: side, symbol: position.symbol };
                                    if (position.price && position.price > 0) {
                                      const suggested = Number(
                                        (
                                          Math.abs(
                                            position.rebalance_amount ?? 0,
                                          ) / position.price
                                        ).toFixed(8),
                                      );
                                      initial.price = position.price;
                                      initial.quantity =
                                        side === "sell"
                                          ? Math.min(
                                              suggested,
                                              position.quantity,
                                            )
                                          : suggested;
                                    }
                                    openNewEntry(item, initial);
                                  }}
                                >
                                  记一笔
                                </button>
                              </div>
                            ))}
                            {item.cash_rebalance_needed && (
                              <div className="allocation-rebalance-item">
                                <div>
                                  <strong>现金</strong>
                                  <span>
                                    偏离 {formatPercent(item.cash_drift_pp)}
                                  </span>
                                </div>
                                <div className="allocation-rebalance">
                                  {formatAmount(
                                    item.cash_rebalance_amount,
                                    item.currency,
                                    true,
                                  )}
                                </div>
                                <span />
                              </div>
                            )}
                          </div>
                        ) : (
                          <p className="allocation-field-hint">
                            目标内或尚未设置目标。
                          </p>
                        )}
                      </div>
                    );
                  })}
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
                  <li>
                    美股价格先取东方财富，失败再用 Yahoo Finance 兜底（非官方接口），按 USD 计；
                    总览按 USD/CNY 汇率近似折算，组合内部不做汇率换算。失败时回退手动价/成本。
                  </li>
                  <li>
                    顶部「刷新全部账本」会一次性拉取所有组合的最新行情和汇率；A 股、手动价和利息不会自动更新。
                  </li>
                  <li>现金可以为负，说明有入金未记录或用了外部资金；XIRR 在样本期不足 30 天时仅供参考。</li>
                </ul>
              </section>
            </>
          )}
        </div>
      </div>

      <AllocationPortfolioModal
        open={portfolioModalOpen}
        portfolio={portfolioModalMode === "edit" ? configTarget : null}
        onClose={() => {
          setPortfolioModalOpen(false);
          setConfigTarget(null);
        }}
        onSaved={() => void onPortfolioSaved()}
        onDelete={
          configTarget && configTarget.entry_count === 0
            ? () => setDeletePortfolioTarget(configTarget)
            : undefined
        }
      />

      {entryPortfolio && (
        <AllocationEntryModal
          open={entryModalOpen}
          portfolio={entryPortfolio}
          entry={editingEntry}
          initial={entryInitial}
          onClose={() => {
            setEntryModalOpen(false);
            setEditingEntry(null);
            setEntryInitial(undefined);
            setEntryPortfolio(null);
          }}
          onSaved={() => void onEntrySaved()}
        />
      )}

      <ConfirmDialog
        open={Boolean(deleteEntry)}
        title="删除这笔流水？"
        description={
          deleteEntry
            ? `${entryTypeLabel(deleteEntry.entry.type)} ${
                deleteEntry.entry.symbol ?? ""
              } ${deleteEntry.entry.quantity ?? ""} ${deleteEntry.entry.at}。删除后会整本重放，可能改变之后的成本和已实现盈亏。`
            : ""
        }
        confirmLabel="删除"
        danger
        onConfirm={() => void confirmDeleteEntry()}
        onClose={() => setDeleteEntry(null)}
      />

      <ConfirmDialog
        open={Boolean(deletePortfolioTarget)}
        title="删除这个组合？"
        description="组合没有流水，删除后配置不可恢复。"
        confirmLabel="删除"
        danger
        onConfirm={() => void confirmDeletePortfolio()}
        onClose={() => setDeletePortfolioTarget(null)}
      />
    </main>
  );
}
