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

import type { ChanAnalysis } from "../types";

const UP = "#0f766e";
const DOWN = "#b91c1c";
const STROKE = "#5b4bf0";
const ZONE_FILL = "rgba(91, 75, 240, 0.12)";
const ZONE_LINE = "rgba(91, 75, 240, 0.68)";

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

function watchResize(
  container: HTMLDivElement,
  chart: IChartApi,
  onChange: () => void,
): () => void {
  if (typeof ResizeObserver === "undefined") return () => undefined;
  const observer = new ResizeObserver(() => {
    chart.applyOptions({ width: container.clientWidth });
    onChange();
  });
  observer.observe(container);
  return () => observer.disconnect();
}

function seconds(value: number): UTCTimestamp {
  return (value / 1000) as UTCTimestamp;
}

const POINT_STYLE: Record<
  string,
  { color: string; text: string; buy: boolean }
> = {
  "1buy": { color: "#0f766e", text: "1买", buy: true },
  "2buy": { color: "#0ea5e9", text: "2买", buy: true },
  "3buy": { color: "#7c3aed", text: "3买", buy: true },
  "1sell": { color: "#b91c1c", text: "1卖", buy: false },
  "2sell": { color: "#f97316", text: "2卖", buy: false },
  "3sell": { color: "#db2777", text: "3卖", buy: false },
};

export function ChanChart({
  analysis,
  showMerged,
  showFractals,
  showStrokes,
  showZones,
  showPoints,
  height = 560,
}: {
  analysis: ChanAnalysis;
  showMerged: boolean;
  showFractals: boolean;
  showStrokes: boolean;
  showZones: boolean;
  showPoints: boolean;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    const overlay = overlayRef.current;
    if (!container || !overlay || !analysis.candles.length) return;
    const chart = createBaseChart(container, height);
    const candles = chart.addCandlestickSeries({
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    candles.setData(
      analysis.candles.map((candle) => ({
        time: seconds(candle.time),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      })),
    );

    if (showMerged && analysis.merged.length) {
      const merged = chart.addCandlestickSeries({
        upColor: "rgba(15, 118, 110, 0.28)",
        downColor: "rgba(185, 28, 28, 0.24)",
        borderVisible: false,
        wickUpColor: "rgba(15, 118, 110, 0.35)",
        wickDownColor: "rgba(185, 28, 28, 0.3)",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const seen = new Set<number>();
      merged.setData(
        analysis.merged
          .filter((bar) => {
            if (seen.has(bar.end_time)) return false;
            seen.add(bar.end_time);
            return true;
          })
          .map((bar) => ({
            time: seconds(bar.end_time),
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
          }))
          .sort((left, right) => Number(left.time) - Number(right.time)),
      );
    }

    if (showStrokes && analysis.strokes.length) {
      const strokeSeries = chart.addLineSeries({
        color: STROKE,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const points: { time: UTCTimestamp; value: number }[] = [];
      const seen = new Set<number>();
      for (const stroke of analysis.strokes) {
        for (const [time, value] of [
          [stroke.start_time, stroke.start_price],
          [stroke.end_time, stroke.end_price],
        ] as const) {
          if (seen.has(time)) continue;
          seen.add(time);
          points.push({ time: seconds(time), value });
        }
      }
      points.sort((left, right) => Number(left.time) - Number(right.time));
      strokeSeries.setData(points);
    }

    const drawZones = () => {
      const rect = container.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      overlay.width = Math.max(1, Math.floor(rect.width * ratio));
      overlay.height = Math.max(1, Math.floor(rect.height * ratio));
      overlay.style.width = `${rect.width}px`;
      overlay.style.height = `${rect.height}px`;
      const context = overlay.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, rect.width, rect.height);
      if (!showZones || !analysis.zones.length) return;
      for (const zone of analysis.zones) {
        const x1 = chart.timeScale().timeToCoordinate(seconds(zone.start_time));
        const x2 = chart.timeScale().timeToCoordinate(seconds(zone.end_time));
        const y1 = candles.priceToCoordinate(zone.zg);
        const y2 = candles.priceToCoordinate(zone.zd);
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue;
        const left = Math.min(x1, x2);
        const top = Math.min(y1, y2);
        const width = Math.abs(x2 - x1);
        const zoneHeight = Math.abs(y2 - y1);
        context.fillStyle = ZONE_FILL;
        context.fillRect(left, top, width, zoneHeight);
        context.strokeStyle = ZONE_LINE;
        context.lineWidth = 1;
        context.setLineDash([4, 4]);
        context.strokeRect(left, top, width, zoneHeight);
        context.setLineDash([]);
        if (width > 58) {
          context.fillStyle = "rgba(75, 59, 219, 0.9)";
          context.font = "11px Inter, PingFang SC, sans-serif";
          context.fillText("中枢", left + 6, top + 14);
        }
      }
    };

    const markers: SeriesMarker<Time>[] = [];
    const pointTimes = new Set<number>();
    if (showPoints) {
      for (const point of analysis.points) {
        const style = POINT_STYLE[point.type];
        if (!style) continue;
        pointTimes.add(point.time);
        markers.push({
          time: seconds(point.time),
          position: style.buy ? "belowBar" : "aboveBar",
          color: style.color,
          shape: style.buy ? "arrowUp" : "arrowDown",
          text: style.text,
        });
      }
    }
    if (showFractals) {
      for (const fractal of analysis.fractals) {
        if (fractal.used === false || pointTimes.has(fractal.time)) continue;
        markers.push({
          time: seconds(fractal.time),
          position: fractal.type === "top" ? "aboveBar" : "belowBar",
          color: fractal.type === "top" ? DOWN : UP,
          shape: fractal.type === "top" ? "arrowDown" : "arrowUp",
          text: fractal.type === "top" ? "顶" : "底",
        });
      }
    }
    markers.sort((left, right) => Number(left.time) - Number(right.time));
    candles.setMarkers(markers);

    chart.timeScale().fitContent();
    const timeScale = chart.timeScale();
    const redraw = () => requestAnimationFrame(drawZones);
    timeScale.subscribeVisibleLogicalRangeChange(redraw);
    const unwatch = watchResize(container, chart, redraw);
    requestAnimationFrame(drawZones);

    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(redraw);
      unwatch();
      chart.remove();
    };
  }, [analysis, showMerged, showFractals, showStrokes, showZones, showPoints, height]);

  return (
    <div className="chan-chart-wrap" style={{ height }}>
      <div className="chan-chart-canvas" ref={containerRef} />
      <canvas className="chan-zone-overlay" ref={overlayRef} />
    </div>
  );
}
