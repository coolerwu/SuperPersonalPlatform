import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { BacktestTrade } from "../types";

type Candle = [number, number, number, number, number, number];
type Point = [number, number];

const ACCENT = "#0f766e";
const ACCENT_SOFT = "rgba(15, 118, 110, 0.22)";
const ACCENT_FADE = "rgba(15, 118, 110, 0.02)";
const DANGER = "#b91c1c";
const DANGER_SOFT = "rgba(185, 28, 28, 0.22)";
const DANGER_FADE = "rgba(185, 28, 28, 0.02)";

function createBaseChart(container: HTMLDivElement, height: number): IChartApi {
  return createChart(container, {
    width: container.clientWidth,
    height,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: "#5c6873",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: "#efebe2" },
      horzLines: { color: "#efebe2" },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#e6e2d8" },
    timeScale: {
      borderColor: "#e6e2d8",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 4,
    },
  });
}

function watchResize(container: HTMLDivElement, chart: IChartApi): () => void {
  if (typeof ResizeObserver === "undefined") return () => undefined;
  const observer = new ResizeObserver(() => {
    chart.applyOptions({ width: container.clientWidth });
  });
  observer.observe(container);
  return () => observer.disconnect();
}

function nearestTime(times: number[], target: number): number {
  if (!times.length) return target;
  let low = 0;
  let high = times.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (times[middle] < target) low = middle + 1;
    else high = middle;
  }
  if (low === 0) return times[0];
  const current = times[low];
  const previous = times[low - 1];
  return Math.abs(current - target) <= Math.abs(target - previous) ? current : previous;
}

export function PriceChart({
  candles,
  trades,
  height = 300,
}: {
  candles: Candle[];
  trades: BacktestTrade[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !candles.length) return;
    const chart = createBaseChart(container, height);
    const series = chart.addCandlestickSeries({
      upColor: ACCENT,
      downColor: DANGER,
      borderVisible: false,
      wickUpColor: ACCENT,
      wickDownColor: DANGER,
    });
    series.setData(
      candles.map(([ms, open, high, low, close]) => ({
        time: (ms / 1000) as UTCTimestamp,
        open,
        high,
        low,
        close,
      })),
    );
    const times = candles.map((candle) => candle[0]);
    const markers: SeriesMarker<Time>[] = [];
    for (const trade of trades) {
      const entryTime = nearestTime(times, trade.entry_ts);
      if (trade.side === "short") {
        markers.push({
          time: (entryTime / 1000) as UTCTimestamp,
          position: "aboveBar",
          color: DANGER,
          shape: "arrowDown",
          text: "空",
        });
      } else {
        markers.push({
          time: (entryTime / 1000) as UTCTimestamp,
          position: "belowBar",
          color: ACCENT,
          shape: "arrowUp",
          text: "买",
        });
      }
      if (!trade.closed) continue;
      const exitTime = nearestTime(times, trade.exit_ts);
      if (trade.side === "short") {
        markers.push({
          time: (exitTime / 1000) as UTCTimestamp,
          position: "belowBar",
          color: ACCENT,
          shape: "arrowUp",
          text: "平",
        });
      } else {
        markers.push({
          time: (exitTime / 1000) as UTCTimestamp,
          position: "aboveBar",
          color: DANGER,
          shape: "arrowDown",
          text: "卖",
        });
      }
    }
    markers.sort((left, right) => Number(left.time) - Number(right.time));
    series.setMarkers(markers);
    chart.timeScale().fitContent();
    const unwatch = watchResize(container, chart);
    return () => {
      unwatch();
      chart.remove();
    };
  }, [candles, trades, height]);

  return <div className="backtest-chart" ref={containerRef} style={{ height }} />;
}

export function EquityChart({ points, height = 240 }: { points: Point[]; height?: number }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !points.length) return;
    const chart = createBaseChart(container, height);
    const series = chart.addAreaSeries({
      lineColor: ACCENT,
      topColor: ACCENT_SOFT,
      bottomColor: ACCENT_FADE,
      lineWidth: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    series.setData(
      points.map(([ms, value]) => ({
        time: (ms / 1000) as UTCTimestamp,
        value,
      })),
    );
    chart.timeScale().fitContent();
    const unwatch = watchResize(container, chart);
    return () => {
      unwatch();
      chart.remove();
    };
  }, [points, height]);

  return <div className="backtest-chart" ref={containerRef} style={{ height }} />;
}

export function DrawdownChart({
  points,
  height = 200,
}: {
  points: Point[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !points.length) return;
    const chart = createBaseChart(container, height);
    const series = chart.addAreaSeries({
      lineColor: DANGER,
      topColor: DANGER_FADE,
      bottomColor: DANGER_SOFT,
      lineWidth: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    series.setData(
      points.map(([ms, value]) => ({
        time: (ms / 1000) as UTCTimestamp,
        value: value * 100,
      })),
    );
    chart.timeScale().fitContent();
    const unwatch = watchResize(container, chart);
    return () => {
      unwatch();
      chart.remove();
    };
  }, [points, height]);

  return <div className="backtest-chart" ref={containerRef} style={{ height }} />;
}
