import type { SessionSummary } from "./types";

/** True when two sidebar snapshots would render identically. */
export function sessionSummariesEqual(
  a: SessionSummary[],
  b: SessionSummary[],
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    const left = a[i];
    const right = b[i];
    if (left === right) continue;
    if (
      left.id !== right.id ||
      left.title !== right.title ||
      left.last_modified !== right.last_modified ||
      left.channel_type !== right.channel_type ||
      left.channel_name !== right.channel_name ||
      left.busy !== right.busy
    ) {
      return false;
    }
  }
  return true;
}
