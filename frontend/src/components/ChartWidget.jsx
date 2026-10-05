import { createChart, CrosshairMode, LineSeries, LineStyle } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { fmtPrice } from "../format.js";

// One series (no legend box needed); level lines are status-like and always carry a text label + dash style.
const COLORS = { price: "#22d3ee", entry: "#fbbf24", target: "#4ade80", stop: "#f87171", grid: "rgba(148,197,255,0.06)", text: "#a9b6cc" };

function PriceChart({ series, levels, height }) {
  const box = useRef();
  const [tip, setTip] = useState(null);

  useEffect(() => {
    if (!box.current || !series.length) return undefined;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: COLORS.text, fontFamily: "Inter, system-ui, sans-serif", attributionLogo: false },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: CrosshairMode.Magnet, vertLine: { color: "rgba(230,237,247,0.35)" }, horzLine: { color: "rgba(230,237,247,0.35)" } },
      handleScale: { pinch: true, mouseWheel: true, axisPressedMouseMove: true },
      handleScroll: { horzTouchDrag: true, vertTouchDrag: false },
    });
    const lv = levels || {};
    const levelPrices = [lv.entry_zone_low, lv.entry_zone_high, lv.target_price, lv.stop_loss].filter((v) => v != null);
    const line = chart.addSeries(LineSeries, {
      color: COLORS.price,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 4,
      // keep entry/target/stop in view even when they sit far from today's price
      autoscaleInfoProvider: (original) => {
        const res = original();
        if (!res || !levelPrices.length) return res;
        return { ...res, priceRange: {
          minValue: Math.min(res.priceRange.minValue, ...levelPrices),
          maxValue: Math.max(res.priceRange.maxValue, ...levelPrices),
        } };
      },
    });
    line.setData(series.map((d) => ({ time: d.date, value: d.close })));
    const addLevel = (price, color, title, style) =>
      price != null && line.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
    addLevel(lv.entry_zone_low, COLORS.entry, "Entry", LineStyle.Dashed);
    addLevel(lv.entry_zone_high, COLORS.entry, "Entry", LineStyle.Dashed);
    addLevel(lv.target_price, COLORS.target, "Target", LineStyle.LargeDashed);
    addLevel(lv.stop_loss, COLORS.stop, "Stop", LineStyle.Dotted);
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      const point = param.time ? param.seriesData.get(line) : null;
      setTip(point ? { date: param.time, value: point.value } : null);
    });
    return () => chart.remove();
  }, [series, levels]);

  const last = series[series.length - 1];
  return (
    <div className="chart-box" ref={box} style={height ? { height } : undefined}>
      <div className="chart-tip" aria-live="off">
        {tip ? `${tip.date} · ${fmtPrice(tip.value)}` : last ? `${last.date} · ${fmtPrice(last.close)}` : ""}
      </div>
    </div>
  );
}

export default function ChartWidget({ agentId, ticker }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [table, setTable] = useState(false);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    api.chart(agentId, ticker).then((d) => live && setData(d)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [agentId, ticker]);

  if (error) return <p className="down">{error}</p>;
  if (!data) return <div className="skeleton" style={{ height: 220 }} />;
  if (!data.series.length) return <p className="faint">{data.note || "No price history yet; it builds up as the price monitor runs."}</p>;

  const lv = data.levels || {};
  const legend = (
    <div className="chart-legend">
      <span><span className="swatch" style={{ borderColor: COLORS.price }} />Price (30 days)</span>
      {lv.entry_zone_low != null && <span><span className="swatch" style={{ borderColor: COLORS.entry, borderTopStyle: "dashed" }} />Entry {fmtPrice(lv.entry_zone_low)}–{fmtPrice(lv.entry_zone_high)}</span>}
      {lv.target_price != null && <span><span className="swatch" style={{ borderColor: COLORS.target, borderTopStyle: "dashed" }} />Target {fmtPrice(lv.target_price)}</span>}
      {lv.stop_loss != null && <span><span className="swatch" style={{ borderColor: COLORS.stop, borderTopStyle: "dotted" }} />Stop {fmtPrice(lv.stop_loss)}</span>}
    </div>
  );

  return (
    <div>
      <div className="view-tabs">
        <button className="btn small" aria-pressed={!table} onClick={() => setTable(false)}>Chart</button>
        <button className="btn small" aria-pressed={table} onClick={() => setTable(true)}>Table</button>
        <button className="btn small" onClick={() => setExpanded(true)} aria-label="Expand chart">⤢ Expand</button>
      </div>
      {table ? (
        <div className="table-wrap" style={{ maxHeight: 260 }}>
          <table className="data">
            <thead><tr><th>Date</th><th>Close</th></tr></thead>
            <tbody>{[...data.series].reverse().map((d) => <tr key={d.date}><td className="mono">{d.date}</td><td className="mono">{fmtPrice(d.close)}</td></tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div onClick={() => window.matchMedia("(pointer: coarse)").matches && setExpanded(true)}>
          <PriceChart series={data.series} levels={data.levels} />
        </div>
      )}
      {legend}
      {expanded && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label={`${ticker} price chart`} onClick={() => setExpanded(false)}>
          <div className="modal glass chart-expanded" style={{ width: "min(1100px, 100%)" }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h2>{ticker}: 30-day price with entry/exit levels</h2>
              <button className="btn icon" onClick={() => setExpanded(false)} aria-label="Close">✕</button>
            </div>
            <PriceChart series={data.series} levels={data.levels} />
            {legend}
            <p className="faint" style={{ fontSize: 12 }}>Pinch or scroll to zoom, drag to pan.</p>
          </div>
        </div>
      )}
    </div>
  );
}
