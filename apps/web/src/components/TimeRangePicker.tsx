import { useEffect, useRef, useState } from "react";

import {
  defaultWindowStart,
  parseUtcInput,
  shortTime,
  toUtcInput,
  type TimeRangeValue,
} from "../timeRange";

export function TimeRangePicker({
  value,
  stepSeconds,
  granularityLabel,
  defaultBars = 500,
  applyLabel = "应用并分析",
  onApply,
}: {
  value: TimeRangeValue;
  stepSeconds: number;
  granularityLabel: string;
  defaultBars?: number;
  applyLabel?: string;
  onApply: (next: TimeRangeValue) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draftStart, setDraftStart] = useState("");
  const [draftEnd, setDraftEnd] = useState("");
  const [error, setError] = useState("");
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const stepMs = stepSeconds * 1000;

  function openPicker() {
    setDraftStart(value.start || toUtcInput(defaultWindowStart(value.end, stepMs, defaultBars)));
    setDraftEnd(value.end);
    setError("");
    setOpen(true);
  }

  function apply() {
    const nextStart = draftStart.trim();
    const nextEnd = draftEnd.trim();
    const startMs = nextStart ? parseUtcInput(nextStart) : null;
    const endMs = nextEnd ? parseUtcInput(nextEnd) : null;
    if ((nextStart && startMs === null) || (nextEnd && endMs === null)) {
      setError("时间格式无效，请重新选择");
      return;
    }
    if (startMs !== null && endMs !== null && startMs >= endMs) {
      setError("开始时间必须早于结束时间");
      return;
    }
    // Leaving the prefilled default start untouched keeps the window sliding
    // with the latest bar instead of freezing that timestamp.
    const fallbackStart = defaultWindowStart(nextEnd, stepMs, defaultBars);
    const keepDefaultStart = startMs !== null && Math.abs(startMs - fallbackStart) <= stepMs;
    const storedStart = nextStart === "" || keepDefaultStart ? "" : nextStart;
    setOpen(false);
    onApply({ start: storedStart, end: nextEnd });
  }

  const resolvedStart = value.start
    ? shortTime(value.start)
    : shortTime(toUtcInput(defaultWindowStart(value.end, stepMs, defaultBars)));
  const label = `${resolvedStart} → ${value.end ? shortTime(value.end) : "最新"}`;

  return (
    <div className="chan-range-wrap" ref={wrapRef}>
      <button
        type="button"
        className="chan-range-trigger"
        onClick={() => (open ? setOpen(false) : openPicker())}
      >
        <span>时间窗口</span>
        <strong>{label}</strong>
      </button>
      {open && (
        <div className="chan-range-popover" role="dialog" aria-label="选择时间窗口">
          <p className="chan-range-hint">
            UTC 时间，步长跟随周期（{granularityLabel}）。结束留空 = 最新已收盘 K 线；
            开始默认是结束往前 {defaultBars} 根，留空表示跟随最新。
          </p>
          <label className="chan-range-field">
            <span>开始 (UTC)</span>
            <input
              type="datetime-local"
              step={stepSeconds}
              value={draftStart}
              onChange={(event) => setDraftStart(event.target.value)}
              autoFocus
            />
          </label>
          <label className="chan-range-field">
            <span>结束 (UTC)</span>
            <input
              type="datetime-local"
              step={stepSeconds}
              value={draftEnd}
              onChange={(event) => setDraftEnd(event.target.value)}
            />
          </label>
          {error && <div className="chan-range-error">{error}</div>}
          <div className="chan-range-actions">
            <button
              type="button"
              className="ghost-button"
              onClick={() => {
                setDraftStart("");
                setDraftEnd("");
              }}
            >
              清空
            </button>
            <button type="button" className="primary" onClick={apply}>
              {applyLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
