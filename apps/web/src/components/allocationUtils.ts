const ENTRY_LABELS: Record<string, string> = {
  deposit: "入金",
  withdraw: "出金",
  opening: "初始持仓",
  buy: "买入",
  sell: "卖出",
  dividend: "分红",
  fee: "费用",
};

export function formatAmount(
  value: number | null | undefined,
  currency?: string,
  signed = false,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const prefix = signed && value > 0 ? "+" : "";
  const text = `${prefix}${value.toLocaleString("en-US", {
    maximumFractionDigits: 2,
  })}`;
  return currency ? `${text} ${currency}` : text;
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", {
    maximumFractionDigits: Math.abs(value) >= 1000 ? 2 : Math.abs(value) >= 1 ? 4 : 8,
  });
}

export function formatQuantity(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: 8 });
}

export function formatPercent(
  value: number | null | undefined,
  signed = false,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${signed && value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function formatPp(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}pp`;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function localInputValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function defaultLocalInput(): string {
  const date = new Date();
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function inputToIso(value: string): string {
  if (!value.trim()) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

export function marketLabel(symbol: string): string {
  return /^\d{6}(?:\.(?:SH|SZ|BJ))?$/.test(symbol) ? "A股" : "Crypto";
}

export function priceSourceLabel(source: string): string {
  switch (source) {
    case "live":
      return "实时";
    case "mark":
      return "手动";
    case "cache":
      return "缓存";
    case "cost":
      return "成本";
    case "none":
      return "—";
    default:
      return source;
  }
}

export function entryTypeLabel(type: string): string {
  return ENTRY_LABELS[type] ?? type;
}

export function entryTypeTone(type: string): string {
  if (type === "buy" || type === "opening") return "buy";
  if (type === "sell" || type === "withdraw" || type === "fee") return "sell";
  if (type === "dividend") return "income";
  return "cash";
}

const PALETTE = [
  "#6366f1",
  "#f59e0b",
  "#06b6d4",
  "#a855f7",
  "#ef4444",
  "#10b981",
  "#3b82f6",
  "#f97316",
  "#14b8a6",
  "#ec4899",
];

const COLORS = new Map<string, string>();

export function colorForSymbol(symbol: string): string {
  const existing = COLORS.get(symbol);
  if (existing) return existing;
  const index = COLORS.size % PALETTE.length;
  const color = PALETTE[index];
  COLORS.set(symbol, color);
  return color;
}
