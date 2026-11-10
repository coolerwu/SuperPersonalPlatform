export type TimeRangeValue = {
  /** Local datetime string (`YYYY-MM-DDTHH:mm`, interpreted as UTC); empty = default. */
  start: string;
  /** Local datetime string; empty = latest closed bar. */
  end: string;
};

export function parseUtcInput(value: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) return null;
  const ms = Date.parse(`${value}Z`);
  return Number.isFinite(ms) ? ms : null;
}

export function toUtcInput(ms: number): string {
  const date = new Date(ms);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

export function shortTime(value: string): string {
  const ms = parseUtcInput(value);
  if (ms === null) return "";
  const date = new Date(ms);
  const pad = (item: number) => String(item).padStart(2, "0");
  return `${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

/** Start of the default window: `defaultBars` bars back from the end (or now). */
export function defaultWindowStart(endValue: string, stepMs: number, defaultBars: number): number {
  const endMs = endValue ? parseUtcInput(endValue) ?? Date.now() : Date.now();
  return Math.floor(endMs / stepMs) * stepMs - defaultBars * stepMs;
}
