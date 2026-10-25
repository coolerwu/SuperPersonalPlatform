import { useEffect, useRef, useState } from "react";

/**
 * Bound Markdown re-renders while tokens stream in. The newest text is never
 * lost: a pending flush always reads from the ref. Shrinking or empty values
 * (stream reset, retry) are applied immediately.
 */
export function useThrottledText(value: string, intervalMs = 80): string {
  const [display, setDisplay] = useState(value);
  const latestRef = useRef(value);
  const timerRef = useRef<number | null>(null);
  const lastFlushRef = useRef(0);

  useEffect(() => {
    latestRef.current = value;
    if (value === display) return;
    if (value.length < display.length || value === "") {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      setDisplay(value);
      lastFlushRef.current = Date.now();
      return;
    }
    if (timerRef.current !== null) return;
    const delay = Math.max(0, intervalMs - (Date.now() - lastFlushRef.current));
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      lastFlushRef.current = Date.now();
      setDisplay(latestRef.current);
    }, delay);
  }, [value, display.length, intervalMs]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  return display;
}
