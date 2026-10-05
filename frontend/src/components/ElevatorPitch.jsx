import { changeClass, fmtPct, fmtPrice, timeAgo } from "../format.js";
import ConvictionMeter from "./ConvictionMeter.jsx";
import Markdown from "./Markdown.jsx";

function RangeBar({ low, high, now }) {
  if (low == null || high == null || now == null || high <= low) return null;
  const pct = Math.max(0, Math.min(100, ((now - low) / (high - low)) * 100));
  return (
    <div aria-label={`52-week range ${fmtPrice(low)} to ${fmtPrice(high)}, now ${fmtPrice(now)}`}>
      <div className="range-bar"><div className="fill" style={{ width: `${pct}%` }} /><div className="now" style={{ left: `calc(${pct}% - 1px)` }} /></div>
      <div className="range-labels"><span>52w low {fmtPrice(low)}</span><span>52w high {fmtPrice(high)}</span></div>
    </div>
  );
}

/** Formatted pitch: price + signal + conviction + executive summary + key number + levels. */
export default function ElevatorPitch({ b }) {
  const p = b.price || {};
  const lv = p.levels || {};
  return (
    <section className="section glass" aria-label={`${b.ticker} elevator pitch`}>
      <div className="pitch-head">
        <span className="sym">{b.ticker}</span>
        <span className="px">{p.price == null ? "—" : fmtPrice(p.price)}</span>
        <span className={`chg ${changeClass(p.change_pct)}`}>{fmtPct(p.change_pct)} today</span>
        {b.signal && <span className={`signal ${b.signal}`}>{b.signal}</span>}
        {b.stance && <span className="chip">research: {b.stance}</span>}
      </div>
      <div className="faint" style={{ fontSize: 13 }}>{b.name} · {b.analyst} · thesis updated {timeAgo(b.thesis_updated)}</div>

      {b.headline ? (
        <>
          {b.title && <h2 className="pitch-title">{b.title}</h2>}
          <div className="pitch-headline">“{b.headline}”</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center" }}>
            <div><div className="faint" style={{ fontSize: 12 }}>Conviction</div><ConvictionMeter value={b.conviction_level} /></div>
            {b.position && <span className="chip">position: {b.position}</span>}
            {b.risk && <span className="chip">risk: {b.risk.level}, {b.risk.trend}</span>}
            {b.time_horizon && <span className="chip">{b.time_horizon}</span>}
          </div>
          <div className="stats">
            <div className="stat"><div className="k">Entry zone</div><div className="v">{lv.entry_zone_low == null ? "—" : `${fmtPrice(lv.entry_zone_low)}–${fmtPrice(lv.entry_zone_high)}`}</div></div>
            <div className="stat"><div className="k">Target</div><div className="v">{fmtPrice(lv.target_price)} <span className="faint" style={{ fontSize: 12 }}>{fmtPct(lv.to_target_pct, 0)}</span></div></div>
            <div className="stat"><div className="k">Stop</div><div className="v">{fmtPrice(lv.stop_loss)} <span className="faint" style={{ fontSize: 12 }}>{fmtPct(lv.to_stop_pct, 0)}</span></div></div>
          </div>
          <RangeBar low={p.week_52_low} high={p.week_52_high} now={p.price} />
        </>
      ) : (
        <p className="muted">The initial deep dive is queued. Research, model and trading thesis appear here once it runs.</p>
      )}

      {b.executive_summary && (
        <>
          <h3 style={{ marginTop: 18 }}>Elevator pitch</h3>
          <div className="pitch-summary"><Markdown>{b.executive_summary}</Markdown></div>
        </>
      )}
      {b.key_metric && (
        <div className="key-metric">
          <div className="label">Key number · {b.key_metric.label}</div>
          <div className="value">{b.key_metric.value}</div>
          <div className="why">{b.key_metric.why_it_matters}</div>
        </div>
      )}
    </section>
  );
}

export function ThesisDetails({ b }) {
  if (!b.thesis) return null;
  return (
    <section className="section glass">
      <h3>Trading thesis</h3>
      <Markdown>{b.thesis}</Markdown>
      {b.risk_factors?.length > 0 && (<><h3 style={{ marginTop: 14 }}>Risk factors</h3><ul className="list">{b.risk_factors.map((r) => <li key={r}>{r}</li>)}</ul></>)}
      {b.next_catalysts?.length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>Next catalysts</h3>
          <ul className="list">{b.next_catalysts.map((c) => <li key={c.event}><b>{c.timing}</b>: {c.event}. <span className="muted">{c.why_it_matters}</span></li>)}</ul>
        </>
      )}
      {b.cross_ticker_dependencies?.length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>Cross-ticker dependencies</h3>
          <ul className="list">{b.cross_ticker_dependencies.map((d) => <li key={d.ticker + d.relationship}><span className="mono">{d.ticker}</span> ({d.relationship}): <span className="muted">{d.impact}</span></li>)}</ul>
        </>
      )}
    </section>
  );
}
