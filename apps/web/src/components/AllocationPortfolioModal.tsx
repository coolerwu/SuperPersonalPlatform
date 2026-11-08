import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { AllocationPortfolio, AllocationPortfolioConfig } from "../types";
import { marketLabel } from "./allocationUtils";

type TargetRow = {
  symbol: string;
  target: string;
  mark: string;
  held: boolean;
  rate: string;
  start: string;
  compound: boolean;
};

type Props = {
  open: boolean;
  portfolio: AllocationPortfolio | null;
  onClose: () => void;
  onSaved: (portfolio: AllocationPortfolioConfig) => void;
  onDelete?: () => void;
};

function sortRows(rows: TargetRow[]): TargetRow[] {
  return [...rows].sort((left, right) => {
    if (left.held !== right.held) return left.held ? -1 : 1;
    return left.symbol.localeCompare(right.symbol);
  });
}

export function AllocationPortfolioModal({
  open,
  portfolio,
  onClose,
  onSaved,
  onDelete,
}: Props) {
  const [name, setName] = useState("长期核心");
  const [currency, setCurrency] = useState("USDT");
  const [cashTarget, setCashTarget] = useState("");
  const [band, setBand] = useState("5");
  const [note, setNote] = useState("");
  const [rows, setRows] = useState<TargetRow[]>([]);
  const [newSymbol, setNewSymbol] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    if (portfolio) {
      setName(portfolio.name);
      setCurrency(portfolio.currency);
      setCashTarget(
        portfolio.cash_target_pct === null ? "" : String(portfolio.cash_target_pct),
      );
      setBand(String(portfolio.rebalance_band_pct));
      setNote(portfolio.note ?? "");
      const symbols = new Set<string>([
        ...portfolio.positions.map((item) => item.symbol),
        ...Object.keys(portfolio.targets),
        ...Object.keys(portfolio.marks),
        ...Object.keys(portfolio.accruals ?? {}),
      ]);
      setRows(
        sortRows(
          [...symbols].map((symbol) => ({
            symbol,
            target:
              portfolio.targets[symbol] === undefined
                ? ""
                : String(portfolio.targets[symbol]),
            mark:
              portfolio.marks[symbol]?.price === undefined
                ? ""
                : String(portfolio.marks[symbol].price),
            held: portfolio.positions.some(
              (item) => item.symbol === symbol && item.quantity > 0,
            ),
            rate:
              portfolio.accruals?.[symbol]?.annual_rate === undefined
                ? ""
                : String(portfolio.accruals[symbol].annual_rate),
            start: portfolio.accruals?.[symbol]?.start ?? "",
            compound: Boolean(portfolio.accruals?.[symbol]?.compound),
          })),
        ),
      );
    } else {
      setName("长期核心");
      setCurrency("USDT");
      setCashTarget("");
      setBand("5");
      setNote("");
      setRows([]);
    }
    setNewSymbol("");
    setError("");
  }, [open, portfolio]);

  if (!open) return null;

  const hasAnyTarget =
    rows.some((row) => row.target.trim() !== "") || cashTarget.trim() !== "";
  const targetSum =
    rows.reduce((total, row) => {
      const value = Number(row.target);
      return total + (row.target.trim() && Number.isFinite(value) ? value : 0);
    }, 0) + (cashTarget.trim() && Number.isFinite(Number(cashTarget)) ? Number(cashTarget) : 0);
  const targetMismatch = hasAnyTarget && Math.abs(targetSum - 100) > 0.5;

  function updateRow(
    symbol: string,
    key: "target" | "mark" | "rate" | "start",
    value: string,
  ) {
    setRows((current) =>
      current.map((row) => (row.symbol === symbol ? { ...row, [key]: value } : row)),
    );
  }

  function toggleCompound(symbol: string) {
    setRows((current) =>
      current.map((row) =>
        row.symbol === symbol ? { ...row, compound: !row.compound } : row,
      ),
    );
  }

  function removeRow(symbol: string) {
    setRows((current) => current.filter((row) => row.symbol !== symbol));
  }

  function addTarget() {
    const symbol = newSymbol.trim().toUpperCase();
    if (!symbol) return;
    if (!/^[A-Z0-9][A-Z0-9._-]*$/.test(symbol) || symbol.length > 32) {
      setError("标的只能包含字母、数字、点、下划线和连字符");
      return;
    }
    if (rows.some((row) => row.symbol === symbol)) {
      setError("这个标的已经在列表里");
      return;
    }
    setRows((current) =>
      sortRows([
        ...current,
        { symbol, target: "", mark: "", held: false, rate: "", start: "", compound: false },
      ]),
    );
    setNewSymbol("");
    setError("");
  }

  function fillCurrentWeights() {
    if (!portfolio) return;
    const weights = new Map(
      portfolio.positions.map((item) => [item.symbol, item.weight_pct]),
    );
    setRows((current) =>
      current.map((row) => {
        const weight = weights.get(row.symbol);
        return weight === null || weight === undefined
          ? row
          : { ...row, target: weight.toFixed(2) };
      }),
    );
    if (portfolio.cash_weight_pct !== null) {
      setCashTarget(portfolio.cash_weight_pct.toFixed(2));
    }
  }

  function marketFor(symbol: string): string | undefined {
    return portfolio?.positions.find((item) => item.symbol === symbol)?.market;
  }

  async function save() {
    setError("");
    if (!name.trim()) {
      setError("组合名称不能为空");
      return;
    }
    const normalizedCurrency = currency.trim().toUpperCase();
    if (!/^[A-Z0-9]{2,10}$/.test(normalizedCurrency)) {
      setError("基础币种必须是 2-10 位大写字母或数字");
      return;
    }
    const targets: Record<string, number> = {};
    const marks: Record<string, { price: number }> = {};
    for (const row of rows) {
      if (row.target.trim()) {
        const value = Number(row.target);
        if (!Number.isFinite(value) || value < 0 || value > 100) {
          setError(`${row.symbol} 的目标占比必须在 0-100 之间`);
          return;
        }
        targets[row.symbol] = value;
      }
      if (row.mark.trim()) {
        const price = Number(row.mark);
        if (!Number.isFinite(price) || price <= 0) {
          setError(`${row.symbol} 的手动价必须大于 0`);
          return;
        }
        marks[row.symbol] = { price };
      }
    }
    const cashValue = cashTarget.trim() ? Number(cashTarget) : null;
    if (
      cashValue !== null &&
      (!Number.isFinite(cashValue) || cashValue < 0 || cashValue > 100)
    ) {
      setError("现金目标占比必须在 0-100 之间");
      return;
    }
    const accruals: Record<
      string,
      { annual_rate: number; start: string; compound: boolean }
    > = {};
    for (const row of rows) {
      const hasRate = row.rate.trim() !== "";
      const hasStart = row.start.trim() !== "";
      if (!hasRate && !hasStart) continue;
      if (!hasRate || !hasStart) {
        setError(`${row.symbol} 的固定收益需要同时填写年化利率和起息日`);
        return;
      }
      const rateValue = Number(row.rate);
      if (!Number.isFinite(rateValue) || rateValue < 0 || rateValue > 100) {
        setError(`${row.symbol} 的年化利率必须在 0-100 之间`);
        return;
      }
      if (!/^\d{4}-\d{2}-\d{2}$/.test(row.start.trim())) {
        setError(`${row.symbol} 的起息日格式必须是 YYYY-MM-DD`);
        return;
      }
      accruals[row.symbol] = {
        annual_rate: rateValue,
        start: row.start.trim(),
        compound: row.compound,
      };
    }
    const bandValue = band.trim() ? Number(band) : 5;
    if (!Number.isFinite(bandValue) || bandValue < 0 || bandValue > 50) {
      setError("再平衡带宽必须在 0-50 个百分点之间");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        currency: normalizedCurrency,
        cash_target_pct: cashValue,
        rebalance_band_pct: bandValue,
        targets,
        marks,
        accruals,
        note: note.trim(),
      };
      const saved = portfolio
        ? await api.updateAllocationPortfolio(portfolio.id, payload)
        : await api.createAllocationPortfolio(payload);
      onSaved(saved);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal journal-modal allocation-modal"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <h2>{portfolio ? "编辑资产组合" : "新建资产组合"}</h2>
            <p className="journal-modal-sub">
              目标占比按总资产（含现金）计算；不填目标表示不检查偏离。
            </p>
          </div>
          <button className="ghost-button" onClick={onClose}>
            关闭
          </button>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <div className="journal-form-grid">
          <label className="backtest-field">
            <span>组合名称</span>
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="backtest-field">
            <span>基础币种</span>
            <input
              value={currency}
              onChange={(event) => setCurrency(event.target.value.toUpperCase())}
              disabled={Boolean(portfolio && portfolio.entry_count > 0)}
            />
            {portfolio && portfolio.entry_count > 0 && (
              <small className="allocation-field-hint">已有流水，币种不可修改</small>
            )}
          </label>
          <label className="backtest-field">
            <span>现金目标占比 %</span>
            <input
              type="number"
              min="0"
              max="100"
              step="any"
              value={cashTarget}
              onChange={(event) => setCashTarget(event.target.value)}
              placeholder="可选"
            />
          </label>
          <label className="backtest-field">
            <span>再平衡带宽（百分点）</span>
            <input
              type="number"
              min="0"
              max="50"
              step="any"
              value={band}
              onChange={(event) => setBand(event.target.value)}
            />
          </label>
          <label className="backtest-field journal-wide">
            <span>备注 / 长期逻辑</span>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="为什么这样配？什么情况下才再平衡？"
            />
          </label>
        </div>

        <section className="allocation-config-section">
          <div className="settings-card-head">
            <div>
              <h2>目标配置</h2>
              <p>持仓标的会自动出现；也可以添加还没买入的目标标的。移除只删除目标、手动价和计息配置，不会卖出持仓。固定收益填年化利率和起息日后按天累计，可选择是否复投。</p>
            </div>
            <div className="settings-card-actions">
              <button
                className="ghost-button"
                onClick={fillCurrentWeights}
                disabled={!portfolio}
              >
                用当前占比填入
              </button>
            </div>
          </div>

          <div className="allocation-add-row">
            <input
              value={newSymbol}
              onChange={(event) => setNewSymbol(event.target.value.toUpperCase())}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addTarget();
                }
              }}
              placeholder="添加标的，例如 BTCUSDT 或 600519.SH"
            />
            <button className="ghost-button" onClick={addTarget}>
              添加
            </button>
          </div>

          <div className="backtest-table-wrap">
            <table className="backtest-table allocation-target-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>市场</th>
                  <th>目标占比 %</th>
                  <th>手动价</th>
                  <th>年化 %</th>
                  <th>起息日</th>
                  <th>复投</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.symbol}>
                    <td>
                      <div className="allocation-symbol">
                        {row.symbol}
                        {row.held && <span className="allocation-sub">持仓中</span>}
                      </div>
                    </td>
                    <td>
                      <span className="journal-market">{marketLabel(row.symbol, marketFor(row.symbol))}</span>
                    </td>
                    <td>
                      <input
                        className="allocation-mini-input"
                        type="number"
                        min="0"
                        max="100"
                        step="any"
                        value={row.target}
                        onChange={(event) =>
                          updateRow(row.symbol, "target", event.target.value)
                        }
                        placeholder="—"
                      />
                    </td>
                    <td>
                      <input
                        className="allocation-mini-input"
                        type="number"
                        min="0"
                        step="any"
                        value={row.mark}
                        onChange={(event) =>
                          updateRow(row.symbol, "mark", event.target.value)
                        }
                        placeholder={marketLabel(row.symbol, marketFor(row.symbol)) === "A股" ? "按成本" : "可选"}
                      />
                    </td>
                    <td>
                      <input
                        className="allocation-mini-input"
                        type="number"
                        min="0"
                        max="100"
                        step="any"
                        value={row.rate}
                        onChange={(event) =>
                          updateRow(row.symbol, "rate", event.target.value)
                        }
                        placeholder="—"
                      />
                    </td>
                    <td>
                      <input
                        className="allocation-mini-input allocation-date-input"
                        type="date"
                        value={row.start}
                        onChange={(event) =>
                          updateRow(row.symbol, "start", event.target.value)
                        }
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={row.compound}
                        onChange={() => toggleCompound(row.symbol)}
                        aria-label={`${row.symbol} 利息复投`}
                      />
                    </td>
                    <td>
                      <button className="journal-edit" onClick={() => removeRow(row.symbol)}>
                        移除
                      </button>
                    </td>
                  </tr>
                ))}
                {!rows.length && (
                  <tr>
                    <td colSpan={8} className="journal-empty">
                      还没有标的。先添加目标，或记录买入后自动出现。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div
            className={`allocation-target-sum ${
              targetMismatch ? "mismatch" : ""
            }`}
          >
            目标合计 {targetSum.toFixed(2)}%
            {targetMismatch
              ? targetSum > 100
                ? "，超过 100%"
                : `，还有 ${(100 - targetSum).toFixed(2)}% 未分配`
              : hasAnyTarget
                ? "，正好 100%"
                : "，还没有设置目标"}
          </div>
        </section>

        <div className="modal-actions">
          {portfolio && portfolio.entry_count === 0 && onDelete && (
            <button className="danger-button" onClick={onDelete} disabled={saving}>
              删除组合
            </button>
          )}
          <button className="ghost-button" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button className="primary" onClick={() => void save()} disabled={saving}>
            {saving ? "保存中…" : portfolio ? "保存配置" : "创建组合"}
          </button>
        </div>
      </div>
    </div>
  );
}
