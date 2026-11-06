import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type {
  AllocationEntry,
  AllocationEntryPayload,
  AllocationPortfolio,
} from "../types";
import { defaultLocalInput, inputToIso, localInputValue } from "./allocationUtils";

type Props = {
  open: boolean;
  portfolio: AllocationPortfolio;
  entry: AllocationEntry | null;
  initial?: Partial<AllocationEntryPayload>;
  onClose: () => void;
  onSaved: () => void;
};

const CASH_TYPES = new Set(["deposit", "withdraw"]);
const INCOME_TYPES = new Set(["dividend", "fee"]);
const TRADE_TYPES = new Set(["opening", "buy", "sell"]);

export function AllocationEntryModal({
  open,
  portfolio,
  entry,
  initial,
  onClose,
  onSaved,
}: Props) {
  const [type, setType] = useState("buy");
  const [at, setAt] = useState("");
  const [amount, setAmount] = useState("");
  const [symbol, setSymbol] = useState("");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [fees, setFees] = useState("0");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    if (entry) {
      setType(entry.type);
      setAt(localInputValue(entry.at));
      setAmount(entry.amount === undefined ? "" : String(entry.amount));
      setSymbol(entry.symbol ?? "");
      setQuantity(entry.quantity === undefined ? "" : String(entry.quantity));
      setPrice(entry.price === undefined ? "" : String(entry.price));
      setFees(entry.fees === undefined ? "0" : String(entry.fees));
      setNote(entry.note ?? "");
    } else {
      setType(initial?.type ?? "buy");
      setAt(defaultLocalInput());
      setAmount(initial?.amount === undefined || initial?.amount === null ? "" : String(initial.amount));
      setSymbol(initial?.symbol ?? "");
      setQuantity(
        initial?.quantity === undefined || initial?.quantity === null
          ? ""
          : String(initial.quantity),
      );
      setPrice(
        initial?.price === undefined || initial?.price === null
          ? ""
          : String(initial.price),
      );
      setFees(
        initial?.fees === undefined || initial?.fees === null
          ? "0"
          : String(initial.fees),
      );
      setNote("");
    }
    setError("");
  }, [open, entry, initial]);

  if (!open) return null;

  const isCash = CASH_TYPES.has(type);
  const isIncome = INCOME_TYPES.has(type);
  const isTrade = TRADE_TYPES.has(type);
  const normalizedSymbol = symbol.trim().toUpperCase();
  const position = portfolio.positions.find(
    (item) => item.symbol === normalizedSymbol,
  );

  async function save() {
    setError("");
    const iso = inputToIso(at);
    if (!iso) {
      setError("请选择流水时间");
      return;
    }
    const noteValue = note.trim();
    let payload: AllocationEntryPayload;
    if (isCash) {
      const amountValue = Number(amount);
      if (!Number.isFinite(amountValue) || amountValue <= 0) {
        setError("金额必须大于 0");
        return;
      }
      payload = { type, at: iso, amount: amountValue, note: noteValue };
    } else if (isIncome) {
      const amountValue = Number(amount);
      if (!Number.isFinite(amountValue) || amountValue <= 0) {
        setError("金额必须大于 0");
        return;
      }
      if (normalizedSymbol && !/^[A-Z0-9][A-Z0-9._-]*$/.test(normalizedSymbol)) {
        setError("标的格式不正确");
        return;
      }
      payload = {
        type,
        at: iso,
        amount: amountValue,
        symbol: normalizedSymbol || null,
        note: noteValue,
      };
    } else if (isTrade) {
      if (!normalizedSymbol || !/^[A-Z0-9][A-Z0-9._-]*$/.test(normalizedSymbol)) {
        setError("请填写有效的标的");
        return;
      }
      const quantityValue = Number(quantity);
      const priceValue = Number(price);
      const feesValue = fees.trim() ? Number(fees) : 0;
      if (!Number.isFinite(quantityValue) || quantityValue <= 0) {
        setError("数量必须大于 0");
        return;
      }
      if (!Number.isFinite(priceValue) || priceValue <= 0) {
        setError("价格必须大于 0");
        return;
      }
      if (!Number.isFinite(feesValue) || feesValue < 0) {
        setError("手续费不能小于 0");
        return;
      }
      if (type === "sell" && quantityValue > (position?.quantity ?? 0) + 1e-9) {
        setError(
          `卖出数量超过当前持仓 ${position?.quantity ?? 0} ${normalizedSymbol}`,
        );
        return;
      }
      payload = {
        type,
        at: iso,
        symbol: normalizedSymbol,
        quantity: quantityValue,
        price: priceValue,
        fees: feesValue,
        note: noteValue,
      };
    } else {
      setError("未知的流水类型");
      return;
    }
    setSaving(true);
    try {
      if (entry) {
        await api.updateAllocationEntry(portfolio.id, entry.id, payload);
      } else {
        await api.createAllocationEntry(portfolio.id, payload);
      }
      onSaved();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal journal-modal allocation-entry-modal"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <h2>{entry ? "编辑流水" : "记一笔"}</h2>
            <p className="journal-modal-sub">
              {portfolio.name} · {portfolio.currency}；流水会整本重放，修改历史会影响之后成本。
            </p>
          </div>
          <button className="ghost-button" onClick={onClose}>
            关闭
          </button>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <div className="journal-form-grid">
          <label className="backtest-field">
            <span>类型</span>
            <select value={type} onChange={(event) => setType(event.target.value)}>
              <option value="deposit">入金</option>
              <option value="withdraw">出金</option>
              <option value="opening">初始持仓</option>
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
              <option value="dividend">分红</option>
              <option value="fee">费用</option>
            </select>
          </label>
          <label className="backtest-field">
            <span>时间</span>
            <input
              type="datetime-local"
              value={at}
              onChange={(event) => setAt(event.target.value)}
            />
          </label>

          {isCash && (
            <label className="backtest-field">
              <span>金额（{portfolio.currency}）</span>
              <input
                type="number"
                min="0"
                step="any"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
              />
            </label>
          )}

          {isIncome && (
            <>
              <label className="backtest-field">
                <span>金额（{portfolio.currency}）</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={amount}
                  onChange={(event) => setAmount(event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>标的（可选）</span>
                <input
                  value={symbol}
                  onChange={(event) => setSymbol(event.target.value.toUpperCase())}
                  placeholder="例如 BTCUSDT"
                />
              </label>
            </>
          )}

          {isTrade && (
            <>
              <label className="backtest-field">
                <span>标的</span>
                <input
                  value={symbol}
                  onChange={(event) => setSymbol(event.target.value.toUpperCase())}
                  placeholder="BTCUSDT 或 600519.SH"
                />
              </label>
              <label className="backtest-field">
                <span>数量</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={quantity}
                  onChange={(event) => setQuantity(event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>{type === "opening" ? "均价成本" : "价格"}</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={price}
                  onChange={(event) => setPrice(event.target.value)}
                />
              </label>
              <label className="backtest-field">
                <span>手续费（{portfolio.currency}）</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={fees}
                  onChange={(event) => setFees(event.target.value)}
                />
              </label>
            </>
          )}

          <label className="backtest-field journal-wide">
            <span>备注</span>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="为什么做这笔？"
            />
          </label>
        </div>

        {type === "opening" && (
          <p className="allocation-field-hint">
            初始持仓不改变现金；如果还有现金，请另记一笔入金。
          </p>
        )}
        {type === "sell" && (
          <p className="allocation-field-hint">
            当前持仓：{position ? `${position.quantity} ${normalizedSymbol}` : "0"}
          </p>
        )}

        <div className="modal-actions">
          <button className="ghost-button" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button className="primary" onClick={() => void save()} disabled={saving}>
            {saving ? "保存中…" : entry ? "保存流水" : "记一笔"}
          </button>
        </div>
      </div>
    </div>
  );
}
