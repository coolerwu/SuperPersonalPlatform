import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
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
const ZONE = "rgba(91, 75, 240, 0.58)";

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

function seconds(value: number): UTCTimestamp {
  return (value / 1000) as UTCTimestamp;
}

export function ChanChart({
  analysis,
  showMerged,
  showFractals,
  showStrokes,
  showZones,
  height = 520,
}: {
  analysis: ChanAnalysis;
  showMerged: boolean;
  showFractals: boolean;
  showStrokes: boolean;
  showZones: boolean;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !analysis.candles.length) return;
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

    if (showZones && analysis.zones.length) {
      for (const zone of analysis.zones) {
        const upper = chart.addLineSeries({
          color: ZONE,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        upper.setData([
          { time: seconds(zone.start_time), value: zone.zg },
          { time: seconds(zone.end_time), value: zone.zg },
        ]);
        const lower = chart.addLineSeries({
          color: ZONE,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        lower.setData([
          { time: seconds(zone.start_time), value: zone.zd },
          { time: seconds(zone.end_time), value: zone.zd },
        ]);
      }
    }

    if (showFractals && analysis.fractals.length) {
      const markers: SeriesMarker<Time>[] = analysis.fractals.map((fractal) => ({
        time: seconds(fractal.time),
        position: fractal.type === "top" ? "aboveBar" : "belowBar",
        color: fractal.type === "top" ? DOWN : UP,
        shape: fractal.type === "top" ? "arrowDown" : "arrowUp",
        text: fractal.type === "top" ? "顶" : "底",
      }));
      markers.sort((left, right) => Number(left.time) - Number(right.time));
      candles.setMarkers(markers);
    }

    chart.timeScale().fitContent();
    const unwatch = watchResize(container, chart);
    return () => {
      unwatch();
      chart.remove();
    };
  }, [analysis, showMerged, showFractals, showStrokes, showZones, height]);

  return <div className="backtest-chart chan-chart" ref={containerRef} style={{ height }} />;
}
