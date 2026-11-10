import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorText } from "../api";
import type { ChanAnalysis } from "../types";
import { ChanBacktestPanel } from "./ChanBacktestPanel";
import { ChanChart } from "./ChanChart";

function formatUtc(ms: number): string {
  if (!ms) return "—";
  return `${new Date(ms).toLocaleString("zh-CN", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })} UTC`;
}

const POPULAR_SYMBOLS = [
  "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
  "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT",
  "TRXUSDT", "TONUSDT", "SHIBUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT",
  "OPUSDT", "NEARUSDT", "ATOMUSDT", "FILUSDT", "INJUSDT", "PEPEUSDT",
];

const CHAN_GRANULARITIES = ["15min", "1h", "4h", "1day", "1week"] as const;
const GRANULARITY_STEP_SECONDS: Record<(typeof CHAN_GRANULARITIES)[number], number> = {
  "15min": 900,
  "1h": 3600,
  "4h": 14_400,
  "1day": 86_400,
  "1week": 604_800,
};
const DEFAULT_WINDOW_BARS = 500;

type ChanGranularity = (typeof CHAN_GRANULARITIES)[number];
type ChanMode = "chart" | "backtest";

function parseUtcInput(value: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) return null;
  const ms = Date.parse(`${value}Z`);
  return Number.isFinite(ms) ? ms : null;
}

function shortTime(value: string): string {
  const ms = parseUtcInput(value);
  if (ms === null) return "";
  const date = new Date(ms);
  const pad = (item: number) => String(item).padStart(2, "0");
  return `${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

type ChanConfig = {
  symbol: string;
  granularity: ChanGranularity;
  start: string;
  end: string;
  mode: ChanMode;
  showMerged: boolean;
  showFractals: boolean;
  showStrokes: boolean;
  showZones: boolean;
  showPoints: boolean;
  showMacd: boolean;
  showDivergence: boolean;
};

function readChanConfig(search: string): ChanConfig {
  const params = new URLSearchParams(search);
  const flag = (name: string) => {
    const value = params.get(name);
    if (value === null) return true;
    return value !== "0" && value.toLowerCase() !== "false";
  };
  const granularity = params.get("granularity") ?? "";
  const symbol = (params.get("symbol") ?? "").trim().toUpperCase();
  const nextGranularity = (CHAN_GRANULARITIES as readonly string[]).includes(granularity)
    ? (granularity as ChanGranularity)
    : "1h";
  const startParam = (params.get("start") ?? "").trim();
  const endParam = (params.get("end") ?? "").trim();
  return {
    symbol: symbol || "BTCUSDT",
    granularity: nextGranularity,
    start: parseUtcInput(startParam) === null ? "" : startParam,
    end: parseUtcInput(endParam) === null ? "" : endParam,
    mode: params.get("mode") === "backtest" ? "backtest" : "chart",
    showMerged: flag("merged"),
    showFractals: flag("fractals"),
    showStrokes: flag("strokes"),
    showZones: flag("zones"),
    showPoints: flag("points"),
    showMacd: flag("macd"),
    showDivergence: flag("divergence"),
  };
}

function chanSearch(config: ChanConfig): string {
  const params = new URLSearchParams();
  params.set("symbol", config.symbol);
  params.set("granularity", config.granularity);
  if (config.start) params.set("start", config.start);
  if (config.end) params.set("end", config.end);
  params.set("mode", config.mode);
  params.set("merged", config.showMerged ? "1" : "0");
  params.set("fractals", config.showFractals ? "1" : "0");
  params.set("strokes", config.showStrokes ? "1" : "0");
  params.set("zones", config.showZones ? "1" : "0");
  params.set("points", config.showPoints ? "1" : "0");
  params.set("macd", config.showMacd ? "1" : "0");
  params.set("divergence", config.showDivergence ? "1" : "0");
  return params.toString();
}

export function ChanPage() {
  const [initial] = useState(() => readChanConfig(window.location.search));
  const [symbol, setSymbol] = useState(initial.symbol);
  const [granularity, setGranularity] = useState<ChanGranularity>(initial.granularity);
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [analysis, setAnalysis] = useState<ChanAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showMerged, setShowMerged] = useState(initial.showMerged);
  const [showFractals, setShowFractals] = useState(initial.showFractals);
  const [showStrokes, setShowStrokes] = useState(initial.showStrokes);
  const [showZones, setShowZones] = useState(initial.showZones);
  const [showPoints, setShowPoints] = useState(initial.showPoints);
  const [showMacd, setShowMacd] = useState(initial.showMacd);
  const [showDivergence, setShowDivergence] = useState(initial.showDivergence);
  const [mode, setMode] = useState<ChanMode>(initial.mode);
  const [rangeOpen, setRangeOpen] = useState(false);
  const [draftStart, setDraftStart] = useState("");
  const [draftEnd, setDraftEnd] = useState("");
  const [rangeError, setRangeError] = useState("");
  const rangeWrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!rangeOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (rangeWrapRef.current && !rangeWrapRef.current.contains(event.target as Node)) {
        setRangeOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setRangeOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [rangeOpen]);

  const analyze = useCallback(
    async (override?: { start?: string; end?: string }) => {
      const nextStart = (override?.start ?? start).trim();
      const nextEnd = (override?.end ?? end).trim();
      const startMs = nextStart ? parseUtcInput(nextStart) : null;
      const endMs = nextEnd ? parseUtcInput(nextEnd) : null;
      if ((nextStart && startMs === null) || (nextEnd && endMs === null)) {
        setError("时间格式无效，请重新选择时间窗口");
        return;
      }
      if (startMs !== null && endMs !== null && startMs >= endMs) {
        setError("开始时间必须早于结束时间");
        return;
      }
      setLoading(true);
      setError("");
      try {
        const result = await api.analyzeChan({
          symbol: symbol.trim().toUpperCase(),
          granularity,
          ...(nextStart ? { start_date: nextStart } : {}),
          ...(nextEnd ? { end_date: nextEnd } : {}),
        });
        setAnalysis(result);
      } catch (exc) {
        setError(errorText(exc));
      } finally {
        setLoading(false);
      }
    },
    [symbol, granularity, start, end],
  );

  function openRange() {
    setDraftStart(start);
    setDraftEnd(end);
    setRangeError("");
    setRangeOpen(true);
  }

  function applyRange() {
    const nextStart = draftStart.trim();
    const nextEnd = draftEnd.trim();
    const startMs = nextStart ? parseUtcInput(nextStart) : null;
    const endMs = nextEnd ? parseUtcInput(nextEnd) : null;
    if ((nextStart && startMs === null) || (nextEnd && endMs === null)) {
      setRangeError("时间格式无效，请重新选择");
      return;
    }
    if (startMs !== null && endMs !== null && startMs >= endMs) {
      setRangeError("开始时间必须早于结束时间");
      return;
    }
    setStart(nextStart);
    setEnd(nextEnd);
    setRangeOpen(false);
    void analyze({ start: nextStart, end: nextEnd });
  }

  const rangeLabel = `${start ? shortTime(start) : `最近 ${DEFAULT_WINDOW_BARS} 根`} → ${
    end ? shortTime(end) : "最新"
  }`;

  useEffect(() => {
    void analyze();
    // Initial load only; changing the form does not fetch until the user clicks 分析.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (window.location.pathname.replace(/\/+$/, "") !== "/chan") return;
    const next = `/chan?${chanSearch({
      symbol,
      granularity,
      start,
      end,
      mode,
      showMerged,
      showFractals,
      showStrokes,
      showZones,
      showPoints,
      showMacd,
      showDivergence,
    })}`;
    if (window.location.pathname + window.location.search !== next) {
      window.history.replaceState(null, "", next);
    }
  }, [
    symbol,
    granularity,
    start,
    end,
    mode,
    showMerged,
    showFractals,
    showStrokes,
    showZones,
    showPoints,
    showMacd,
    showDivergence,
  ]);

  return (
    <main className="backtest-page chan-page">
      <header className="chan-toolbar">
        <label className="chan-control">
          <span>交易对</span>
          <input
            className="chan-symbol"
            list="chan-symbol-options"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            placeholder="搜索或输入交易对"
            autoComplete="off"
          />
          <datalist id="chan-symbol-options">
            {POPULAR_SYMBOLS.map((item) => <option key={item} value={item} />)}
          </datalist>
        </label>
        <label className="chan-control">
          <span>周期</span>
          <select
            value={granularity}
            onChange={(event) => setGranularity(event.target.value as ChanGranularity)}
          >
            <option value="15min">15min</option>
            <option value="1h">1h</option>
            <option value="4h">4h</option>
            <option value="1day">1day</option>
            <option value="1week">1w</option>
          </select>
        </label>
        <div className="chan-range-wrap" ref={rangeWrapRef}>
          <button
            type="button"
            className="chan-range-trigger"
            onClick={() => (rangeOpen ? setRangeOpen(false) : openRange())}
          >
            <span>时间窗口</span>
            <strong>{rangeLabel}</strong>
          </button>
          {rangeOpen && (
            <div className="chan-range-popover" role="dialog" aria-label="选择时间窗口">
              <p className="chan-range-hint">
                UTC 时间，步长跟随周期（{granularity}）。结束留空 = 最新已收盘 K 线；
                开始留空 = 结束往前 {DEFAULT_WINDOW_BARS} 根。
              </p>
              <label className="chan-range-field">
                <span>开始 (UTC)</span>
                <input
                  type="datetime-local"
                  step={GRANULARITY_STEP_SECONDS[granularity]}
                  value={draftStart}
                  onChange={(event) => setDraftStart(event.target.value)}
                  autoFocus
                />
              </label>
              <label className="chan-range-field">
                <span>结束 (UTC)</span>
                <input
                  type="datetime-local"
                  step={GRANULARITY_STEP_SECONDS[granularity]}
                  value={draftEnd}
                  onChange={(event) => setDraftEnd(event.target.value)}
                />
              </label>
              {rangeError && <div className="chan-range-error">{rangeError}</div>}
              <div className="chan-range-actions">
                <button
                  type="button"
                  onClick={() => {
                    setDraftStart("");
                    setDraftEnd("");
                  }}
                >
                  清空
                </button>
                <button type="button" className="primary" onClick={applyRange}>
                  应用并分析
                </button>
              </div>
            </div>
          )}
        </div>
        <div className="chan-mode-tabs">
          <button className={mode === "chart" ? "active" : ""} onClick={() => setMode("chart")}>结构图</button>
          <button className={mode === "backtest" ? "active" : ""} onClick={() => setMode("backtest")}>信号回测</button>
        </div>
        {mode === "chart" && (
          <div className="chan-toggles chan-toolbar-toggles">
            <label><input type="checkbox" checked={showMerged} onChange={(e) => setShowMerged(e.target.checked)} /> 合并 K 线</label>
            <label><input type="checkbox" checked={showFractals} onChange={(e) => setShowFractals(e.target.checked)} /> 分型</label>
            <label><input type="checkbox" checked={showStrokes} onChange={(e) => setShowStrokes(e.target.checked)} /> 笔</label>
            <label><input type="checkbox" checked={showZones} onChange={(e) => setShowZones(e.target.checked)} /> 中枢</label>
            <label><input type="checkbox" checked={showPoints} onChange={(e) => setShowPoints(e.target.checked)} /> 买卖点</label>
            <label><input type="checkbox" checked={showMacd} onChange={(e) => setShowMacd(e.target.checked)} /> MACD</label>
            <label>
              <input
                type="checkbox"
                checked={showMacd && showDivergence}
                disabled={!showMacd}
                onChange={(e) => setShowDivergence(e.target.checked)}
              />{" "}
              背离
            </label>
          </div>
        )}
        {mode === "chart" && (
          <div className="chan-toolbar-actions">
            <button className="primary" onClick={() => void analyze()} disabled={loading}>
              {loading ? "分析中…" : "分析"}
            </button>
          </div>
        )}
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          {error && <div className="error-banner">{error}</div>}

          {mode === "backtest" ? (
            <ChanBacktestPanel symbol={symbol} granularity={granularity} startDate={start} endDate={end} />
          ) : analysis ? (
            <>
              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>{analysis.symbol} · {analysis.granularity === "1week" ? "1w" : analysis.granularity}</h2>
                    <p>
                      {formatUtc(analysis.start_ms)} → {formatUtc(analysis.end_ms)} ·{" "}
                      {analysis.bars} 根原始 K 线 · {analysis.merged_bars} 根合并 K 线 ·{" "}
                      {analysis.fractals.length} 个分型 · {analysis.strokes.length} 笔
                      {(analysis.pending_strokes?.length ?? 0) > 0 ? "（含未完成）" : ""} ·{" "}
                      {analysis.zones.length} 个中枢 ·{" "}
                      {analysis.points.filter((point) => point.broken).length > 0
                        ? `${analysis.points.length} 个买卖点（${analysis.points.filter((point) => point.broken).length} 个已失效）`
                        : `${analysis.points.length} 个买卖点`} ·{" "}
                      {analysis.divergence_count ?? analysis.divergences?.length ?? 0} 个 MACD 背离
                    </p>
                  </div>
                </div>

                <div className="chan-legend">
                  <span><i className="dot candle" />原始 K 线</span>
                  <span><i className="dot merged" />合并 K 线</span>
                  <span><i className="dot top" />笔端点分型</span>
                  <span><i className="dot stroke" />笔</span>
                  <span><i className="dot pending" />未完成走势</span>
                  <span><i className="dot zone" />中枢</span>
                  <span><i className="dot buy" />买点</span>
                  <span><i className="dot sell" />卖点</span>
                  <span><i className="dot broken" />已失效</span>
                  <span><i className="dot dif" />DIF</span>
                  <span><i className="dot dea" />DEA</span>
                  <span><i className="dot hist-up" />MACD 柱</span>
                  <span><i className="dot divergence" />背离</span>
                </div>

                <ChanChart
                  analysis={analysis}
                  showMerged={showMerged}
                  showFractals={showFractals}
                  showStrokes={showStrokes}
                  showZones={showZones}
                  showPoints={showPoints}
                  showMacd={showMacd}
                  showDivergence={showDivergence}
                />

                {analysis.warnings.length > 0 && (
                  <ul className="backtest-warnings">
                    {analysis.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>规则说明</h2>
                    <p>{analysis.rules.name}。不同缠论流派口径不同，本图只按下面这套规则计算。</p>
                  </div>
                </div>
                <dl className="chan-rules">
                  <div><dt>包含处理</dt><dd>按当前方向合并，向上取高高，向下取低低。</dd></div>
                  <div><dt>分型</dt><dd>{analysis.rules.fractal_rule}</dd></div>
                  <div><dt>笔</dt><dd>{analysis.rules.bi_rule}</dd></div>
                  <div><dt>未完成走势</dt><dd>{analysis.rules.pending_rule ?? "最后一笔之后的未确认走势用虚线显示，不计入笔数，不参与中枢、背离和买卖点。"}</dd></div>
                  <div><dt>中枢</dt><dd>{analysis.rules.zone_rule}</dd></div>
                  <div><dt>买卖点</dt><dd>{analysis.rules.point_rule}</dd></div>
                  <div><dt>MACD 背离</dt><dd>{analysis.rules.divergence_rule ?? "同向笔比较 MACD 柱面积，价格创新高/低但面积收缩记为顶/底背离。"}</dd></div>
                  <div><dt>本版不做</dt><dd>线段、多级别联立；买卖点与背离都是笔级别近似，不是唯一标准。</dd></div>
                </dl>
              </section>
            </>
          ) : (
            <section className="settings-card">
              <div className="empty-note">
                {loading ? "正在拉取行情并计算结构…" : "点「分析」拉取最近 K 线并画缠论结构。"}
              </div>
            </section>
          )}
        </div>
      </div>
    </main>
  );
}
