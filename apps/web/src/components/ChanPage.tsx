import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { ChanAnalysis } from "../types";
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

export function ChanPage({ onClose }: { onClose: () => void }) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [granularity, setGranularity] = useState("1h");
  const [bars, setBars] = useState(300);
  const [analysis, setAnalysis] = useState<ChanAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showMerged, setShowMerged] = useState(true);
  const [showFractals, setShowFractals] = useState(true);
  const [showStrokes, setShowStrokes] = useState(true);
  const [showZones, setShowZones] = useState(true);

  const analyze = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.analyzeChan({
        symbol: symbol.trim().toUpperCase(),
        granularity,
        bars,
      });
      setAnalysis(result);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  }, [symbol, granularity, bars]);

  useEffect(() => {
    void analyze();
    // Initial load only; changing the form does not fetch until the user clicks 分析.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="backtest-page chan-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">TRADING / CHAN</div>
          <h1>
            缠论图
            <span className="page-scope-pill">程序化结构</span>
          </h1>
          <p>包含处理 · 分型 · 老笔 · 中枢；行情按需从 Bitget 增量拉取，只使用已收盘 K 线。</p>
        </div>
        <div className="settings-card-actions">
          <button className="ghost-button" onClick={onClose}>
            返回对话
          </button>
          <button className="primary" onClick={() => void analyze()} disabled={loading}>
            {loading ? "分析中…" : "分析"}
          </button>
        </div>
      </header>

      <div className="settings-scroll">
        <div className="settings-lane">
          {error && <div className="error-banner">{error}</div>}

          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>分析参数</h2>
                <p>第一版只做 crypto、单周期；线段和买卖点暂不画。</p>
              </div>
              <div className="settings-card-actions">
                <button className="ghost-button" onClick={() => void analyze()} disabled={loading}>
                  {loading ? "拉取中…" : "刷新最新"}
                </button>
              </div>
            </div>

            <div className="chan-form-grid">
              <label className="backtest-field">
                <span>交易对</span>
                <input
                  value={symbol}
                  onChange={(event) => setSymbol(event.target.value.toUpperCase())}
                  placeholder="BTCUSDT 或 ETHUSDT"
                  autoComplete="off"
                />
              </label>
              <label className="backtest-field">
                <span>周期</span>
                <select value={granularity} onChange={(event) => setGranularity(event.target.value)}>
                  <option value="1h">1h</option>
                  <option value="4h">4h</option>
                  <option value="1day">1day</option>
                </select>
              </label>
              <label className="backtest-field">
                <span>最近 K 线</span>
                <select value={bars} onChange={(event) => setBars(Number(event.target.value))}>
                  <option value="200">200</option>
                  <option value="300">300</option>
                  <option value="500">500</option>
                  <option value="800">800</option>
                </select>
              </label>
            </div>

            <div className="chan-toggles">
              <label><input type="checkbox" checked={showMerged} onChange={(e) => setShowMerged(e.target.checked)} /> 合并 K 线</label>
              <label><input type="checkbox" checked={showFractals} onChange={(e) => setShowFractals(e.target.checked)} /> 分型</label>
              <label><input type="checkbox" checked={showStrokes} onChange={(e) => setShowStrokes(e.target.checked)} /> 笔</label>
              <label><input type="checkbox" checked={showZones} onChange={(e) => setShowZones(e.target.checked)} /> 中枢</label>
            </div>
          </section>

          {analysis ? (
            <>
              <section className="settings-card">
                <div className="settings-card-head">
                  <div>
                    <h2>{analysis.symbol} · {analysis.granularity}</h2>
                    <p>
                      {formatUtc(analysis.start_ms)} → {formatUtc(analysis.end_ms)} ·{" "}
                      {analysis.bars} 根原始 K 线 · {analysis.merged_bars} 根合并 K 线 ·{" "}
                      {analysis.fractals.length} 个分型 · {analysis.strokes.length} 笔 ·{" "}
                      {analysis.zones.length} 个中枢
                    </p>
                  </div>
                </div>

                <div className="chan-legend">
                  <span><i className="dot candle" />原始 K 线</span>
                  <span><i className="dot merged" />合并 K 线</span>
                  <span><i className="dot top" />顶分型</span>
                  <span><i className="dot bottom" />底分型</span>
                  <span><i className="dot stroke" />笔</span>
                  <span><i className="dot zone" />中枢边界</span>
                </div>

                <ChanChart
                  analysis={analysis}
                  showMerged={showMerged}
                  showFractals={showFractals}
                  showStrokes={showStrokes}
                  showZones={showZones}
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
                  <div><dt>中枢</dt><dd>{analysis.rules.zone_rule}</dd></div>
                  <div><dt>本版不做</dt><dd>线段、买卖点、多级别联立。</dd></div>
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
