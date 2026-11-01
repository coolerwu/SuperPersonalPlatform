const GRANULARITY_LABELS: Record<string, string> = {
  "1min": "1 分钟",
  "3min": "3 分钟",
  "5min": "5 分钟",
  "15min": "15 分钟",
  "30min": "30 分钟",
  "1h": "1 小时",
  "4h": "4 小时",
  "6h": "6 小时",
  "12h": "12 小时",
  "1day": "1 天",
  "1week": "1 周",
  "1M": "1 月",
};

const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  fetching: "抓取行情",
  running: "回测中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function granularityLabel(value: string): string {
  return GRANULARITY_LABELS[value] ?? value;
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function dateInputToUtcMs(value: string): number {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match) return Number.NaN;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return Number.NaN;
  return Date.UTC(year, month - 1, day);
}

export function utcMsToDateInput(ms: number): string {
  const date = new Date(ms);
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function defaultDateRange(days = 365): { start: string; end: string } {
  const now = new Date();
  const endMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const startMs = endMs - days * 24 * 3600 * 1000;
  return { start: utcMsToDateInput(startMs), end: utcMsToDateInput(endMs) };
}

export function formatUtc(ms: number | null | undefined, withTime = false): string {
  if (!ms) return "—";
  const date = new Date(ms);
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  if (!withTime) return `${year}-${month}-${day}`;
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}`;
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

export function formatAmount(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1000) return formatAmount(value, 2);
  if (Math.abs(value) >= 1) return value.toFixed(4);
  return value.toPrecision(4);
}

export function tradeSideLabel(side: string): string {
  return side === "short" ? "做空" : "做多";
}
