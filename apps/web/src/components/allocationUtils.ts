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

const CRYPTO_QUOTES = ["USDT", "USDC", "BTC", "ETH", "BGB"];
const CRYPTO_BASES = new Set([
  "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "TRX", "AVAX", "LINK",
  "TON", "SUI", "ARB", "OP", "PEPE", "SHIB", "LTC", "BCH", "DOT", "MATIC",
  "POL", "NEAR", "APT", "ATOM", "UNI", "AAVE", "INJ", "TIA", "SEI", "FTM",
  "ETC", "FIL", "ICP", "HBAR", "STX", "RUNE", "GRT", "SAND", "MANA", "AXS",
  "WLD", "JUP", "ONDO", "ENA", "TAO", "KAS", "BGB",
]);

export function marketLabel(symbol: string, market?: string | null): string {
  if (market === "a_share") return "A股";
  if (market === "stock") return "美股";
  if (market === "crypto") return "Crypto";
  const text = symbol.trim().toUpperCase();
  if (/^\d{6}(?:\.(?:SH|SZ|BJ))?$/.test(text)) return "A股";
  if (
    CRYPTO_QUOTES.some(
      (quote) => text.endsWith(quote) && text.length > quote.length,
    )
  ) {
    return "Crypto";
  }
  return CRYPTO_BASES.has(text) ? "Crypto" : "美股";
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
