import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type MouseEventParams,
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
const MACD_DIF = "#5b4bf0";
const MACD_DEA = "#d97706";
const MACD_UP = "rgba(15, 118, 110, 0.72)";
const MACD_DOWN = "rgba(185, 28, 28, 0.72)";
const MACD_HEIGHT = 170;

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
    rightPriceScale: { borderColor: "#e6e2d8", minimumWidth: 72 },
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
  showMacd,
  showDivergence,
  height = 560,
}: {
  analysis: ChanAnalysis;
  showMerged: boolean;
  showFractals: boolean;
  showStrokes: boolean;
  showZones: boolean;
  showPoints: boolean;
  showMacd: boolean;
  showDivergence: boolean;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const macdRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    const overlay = overlayRef.current;
    const macdContainer = macdRef.current;
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

    let macdChart: IChartApi | null = null;
    let unwatchMacd: () => void = () => undefined;
    let unsubscribeMainRange: () => void = () => undefined;
    let unsubscribeMacdRange: () => void = () => undefined;
    let unsubscribeCrosshair: () => void = () => undefined;
    if (showMacd && macdContainer && analysis.macd?.series?.length) {
      macdChart = createBaseChart(macdContainer, MACD_HEIGHT);
      const macdSeries = analysis.macd.series;
      const largest = macdSeries.reduce(
        (peak, point) => Math.max(peak, Math.abs(point.hist), Math.abs(point.dif), Math.abs(point.dea)),
        0,
      );
      const precision = largest >= 100 ? 1 : largest >= 10 ? 2 : largest >= 0.1 ? 4 : 6;
      const priceFormat = {
        type: "price" as const,
        precision,
        minMove: 10 ** -precision,
      };
      const histogram = macdChart.addHistogramSeries({
        priceFormat,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      const difSeries = macdChart.addLineSeries({
        color: MACD_DIF,
        lineWidth: 1,
        priceFormat,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const deaSeries = macdChart.addLineSeries({
        color: MACD_DEA,
        lineWidth: 1,
        priceFormat,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      histogram.setData(
        macdSeries.map((point) => ({
          time: seconds(point.time),
          value: point.hist,
          color: point.hist >= 0 ? MACD_UP : MACD_DOWN,
        })),
      );
      difSeries.setData(
        macdSeries.map((point) => ({ time: seconds(point.time), value: point.dif })),
      );
      deaSeries.setData(
        macdSeries.map((point) => ({ time: seconds(point.time), value: point.dea })),
      );
      histogram.createPriceLine({
        price: 0,
        color: "#d5d0c4",
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: false,
        title: "",
      });

      const macdMarkers: SeriesMarker<Time>[] = [];
      if (showDivergence) {
        for (const divergence of analysis.divergences ?? []) {
          const bottom = divergence.kind === "bottom";
          macdMarkers.push({
            time: seconds(divergence.time),
            position: bottom ? "belowBar" : "aboveBar",
            color: bottom ? "#0f766e" : "#b91c1c",
            shape: bottom ? "arrowUp" : "arrowDown",
            text: bottom ? "底背" : "顶背",
          });
        }
        macdMarkers.sort((left, right) => Number(left.time) - Number(right.time));
        histogram.setMarkers(macdMarkers);
      }

      macdChart.timeScale().fitContent();
      const histByTime = new Map<number, number>();
      for (const point of macdSeries) histByTime.set(point.time, point.hist);
      let syncing = false;
      const syncRange = (source: IChartApi, target: IChartApi) => () => {
        if (syncing) return;
        const range = source.timeScale().getVisibleLogicalRange();
        if (!range) return;
        const current = target.timeScale().getVisibleLogicalRange();
        if (
          current &&
          Math.abs(current.from - range.from) < 1e-6 &&
          Math.abs(current.to - range.to) < 1e-6
        ) {
          return;
        }
        syncing = true;
        target.timeScale().setVisibleLogicalRange(range);
        syncing = false;
      };
      const mainToMacd = syncRange(chart, macdChart);
      const macdToMain = syncRange(macdChart, chart);
      timeScale.subscribeVisibleLogicalRangeChange(mainToMacd);
      macdChart.timeScale().subscribeVisibleLogicalRangeChange(macdToMain);
      unsubscribeMainRange = () =>
        timeScale.unsubscribeVisibleLogicalRangeChange(mainToMacd);
      unsubscribeMacdRange = () =>
        macdChart?.timeScale().unsubscribeVisibleLogicalRangeChange(macdToMain);
      const onCrosshair = (param: MouseEventParams<Time>) => {
        if (!macdChart) return;
        const value = param.time ? histByTime.get(Number(param.time) * 1000) : undefined;
        if (value === undefined) {
          macdChart.clearCrosshairPosition();
          return;
        }
        macdChart.setCrosshairPosition(value, param.time as Time, histogram);
      };
      chart.subscribeCrosshairMove(onCrosshair);
      unsubscribeCrosshair = () => chart.unsubscribeCrosshairMove(onCrosshair);
      unwatchMacd = watchResize(macdContainer, macdChart, () => undefined);
    }

    return () => {
      unsubscribeCrosshair();
      unsubscribeMainRange();
      unsubscribeMacdRange();
      unwatchMacd();
      timeScale.unsubscribeVisibleLogicalRangeChange(redraw);
      unwatch();
      if (macdChart) macdChart.remove();
      chart.remove();
    };
  }, [
    analysis,
    showMerged,
    showFractals,
    showStrokes,
    showZones,
    showPoints,
    showMacd,
    showDivergence,
    height,
  ]);

  return (
    <div className="chan-chart-wrap">
      <div className="chan-chart-main" style={{ height }}>
        <div className="chan-chart-canvas" ref={containerRef} />
        <canvas className="chan-zone-overlay" ref={overlayRef} />
      </div>
      {showMacd && (
        <div className="chan-macd-canvas" ref={macdRef} style={{ height: MACD_HEIGHT }} />
      )}
    </div>
  );
}
