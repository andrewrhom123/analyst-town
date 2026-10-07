import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { changeClass, fmtPct } from "../format.js";
import ConvictionMeter from "./ConvictionMeter.jsx";

const STRUCTURE = { pair: "Pair trade", outright_long: "Outright long", outright_short: "Outright short", hedged: "Hedged" };
const STANCE = { agree: "agrees", disagree: "pushes back", build: "builds on it" };

/** 30-day price sparkline for one leg (only for tickers the pod covers). */
function LegChart({ symbol, side, owner }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    let live = true;
    if (owner) api.chart(owner, symbol, 30).then((d) => live && setData(d)).catch(() => {});
    return () => { live = false; };
  }, [owner, symbol]);
  const pts = (data?.series || []).map((p) => p.close ?? p.value ?? p.price).filter((v) => v != null);
  let path = null;
  let move = null;
  if (pts.length > 1) {
    const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1;
    path = pts.map((v, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * 120).toFixed(1)},${(34 - ((v - lo) / span) * 30).toFixed(1)}`).join(" ");
    move = ((pts.at(-1) / pts[0]) - 1) * 100;
  }
  return (
    <div className={`leg ${side}`}>
      <div className="leg-head"><span className="leg-side">{side}</span> <b className="mono">{symbol}</b>
        {move != null && <span className={`mono ${changeClass(move)}`}> {fmtPct(move, 1)} 30d</span>}</div>
      {path ? (
        <svg viewBox="0 0 120 36" preserveAspectRatio="none" className="spark" role="img" aria-label={`${symbol} 30-day price`}>
          <path d={path} fill="none" stroke={move >= 0 ? "var(--up)" : "var(--down)"} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
        </svg>
      ) : <div className="faint" style={{ fontSize: 12 }}>{owner ? "Loading chart…" : "Not covered: no chart"}</div>}
    </div>
  );
}

/**
 * A pitched trade: structure, legs with charts, narrative, what the market is missing, data, catalysts,
 * news, conviction, and the room's discussion. `pitch` is the stored pitch data (PitchOut shape);
 * `discussion` the colleagues' comments plus the pitcher's response.
 */
export default function PitchCard({ pitch, analyst, color, conviction, discussion = [], owners = {} }) {
  const p = pitch || {};
  const legs = [p.long_ticker && ["long", p.long_ticker], p.short_ticker && ["short", p.short_ticker]].filter(Boolean);
  const response = discussion.find((d) => d.role === "pitcher");
  return (
    <article className="pitch-card" style={{ "--c": color || "var(--cyan)" }}>
      <header>
        <span className="chip">{STRUCTURE[p.structure] || p.structure}</span>
        {analyst && <span className="faint">pitched by {analyst}</span>}
      </header>
      <h4>{p.title}</h4>
      {legs.length > 0 && <div className="legs">{legs.map(([side, sym]) => <LegChart key={side} side={side} symbol={sym} owner={owners[sym]} />)}</div>}
      <div className="pitch-grid">
        <div><div className="k">What the market is missing</div><p>{p.market_missing}</p></div>
        <div><div className="k">Business trade-offs</div><p>{p.business_tradeoffs}</p></div>
      </div>
      {p.data_points?.length > 0 && <><div className="k">Data</div><ul className="list">{p.data_points.map((d) => <li key={d}>{d}</li>)}</ul></>}
      {p.catalysts?.length > 0 && (
        <><div className="k">Catalysts</div><ul className="list">{p.catalysts.map((c) => <li key={c.event}><b>{c.event}</b> · {c.timing}</li>)}</ul></>
      )}
      {p.news?.length > 0 && (
        <><div className="k">News</div><ul className="list">{p.news.map((n) => <li key={n.url}><a href={n.url} target="_blank" rel="noreferrer">{n.title}</a></li>)}</ul></>
      )}
      <div className="pitch-conv">
        <span className="k">Conviction</span> <ConvictionMeter value={conviction ?? p.conviction} />
        {conviction != null && p.conviction != null && conviction !== p.conviction && <span className="faint"> (was {p.conviction} before the debate)</span>}
      </div>
      {discussion.filter((d) => d.role !== "pitcher").length > 0 && (
        <div className="pitch-debate">
          <div className="k">The room</div>
          {discussion.filter((d) => d.role !== "pitcher").map((d, i) => (
            <p key={i}><b>{d.analyst}</b> {STANCE[d.stance] || d.stance}: {d.spoken}</p>
          ))}
          {response && <p className="pitch-response"><b>{response.analyst}</b> answers: {response.spoken}{response.adjustments && response.adjustments !== "none" ? ` (${response.adjustments})` : ""}</p>}
        </div>
      )}
    </article>
  );
}
