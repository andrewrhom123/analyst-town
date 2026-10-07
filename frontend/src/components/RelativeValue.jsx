import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { fmtMoneyMm } from "../format.js";

const pct = (v) => (v == null ? "n/a" : `${v > 0 ? "+" : ""}${v.toFixed(0)}%`);
const mult = (v) => (v == null ? "n/a" : `${v.toFixed(1)}x`);

/** Rich-to-cheap rows for one bucket: a bar per name centred on fair value. */
export function BucketRows({ rows, onTicker, subjectLabel }) {
  const max = Math.max(50, ...rows.map((r) => Math.abs(r.gap_pct ?? 0)));
  return (
    <ol className="rv-rows">
      {rows.map((r) => {
        const gap = r.gap_pct;
        const w = gap == null ? 0 : Math.min(50, (Math.abs(gap) / max) * 50);
        return (
          <li key={r.symbol} className={`${r.verdict || "na"} ${r.is_subject || r.covered ? "ours" : ""}`}
            title={r.flag || `${r.symbol}: ${mult(r.ev_to_revenue)} EV/revenue, growth ${pct(r.revenue_growth_pct)}, fair ${mult(r.fair_ev_to_revenue)}`}>
            <button type="button" className="rv-sym mono" onClick={() => onTicker?.(r.symbol)} disabled={!onTicker || !(r.covered || r.is_subject)}>
              {r.is_subject ? subjectLabel || r.symbol : r.symbol}
            </button>
            <span className="rv-bar" aria-hidden="true">
              {gap != null && <i style={{ width: `${w}%`, [gap >= 0 ? "left" : "right"]: "50%" }} />}
              <b className="rv-mid" />
            </span>
            <span className="rv-num mono">{r.flag ? "basis?" : pct(gap)}</span>
            <span className="rv-meta faint mono">{mult(r.ev_to_revenue)} · {r.revenue_growth_pct == null ? "–" : `${r.revenue_growth_pct.toFixed(0)}% gr`}</span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Pod-wide relative value board: every comps bucket, rich to cheap vs a growth-adjusted fair multiple,
 * with the long-cheap / short-rich pair it implies. Covered names are bold and clickable.
 */
export function RelativeValueBoard({ onTicker, limit = 6 }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    api.relativeValue().then(setData).catch((e) => setError(e.message));
  }, []);
  if (error) return null;
  const buckets = (data?.buckets || []).filter((b) => b.priced >= 2);
  return (
    <section className="dash-panel glass rv-board" aria-label="Relative value">
      <div className="dash-head">
        <h2>Relative value</h2>
        <span className="faint">rich ↔ cheap vs peers</span>
      </div>
      {!data ? <div className="skeleton" style={{ height: 160 }} /> : buckets.length === 0 ? (
        <p className="faint">Peer multiples fill in as the comps cache warms up.</p>
      ) : (
        <>
          {(showAll ? buckets : buckets.slice(0, limit)).map((b) => (
            <div key={b.bucket} className="rv-bucket">
              <div className="rv-head">
                <b>{b.bucket}</b>
                <span className="faint">{b.fit ? `fair = ${b.fit.intercept.toFixed(1)}x + ${b.fit.slope.toFixed(2)}x/pt growth` : `median ${mult(b.median_ev_to_revenue)}`}</span>
              </div>
              <BucketRows rows={b.rows.filter((r) => r.verdict || r.flag)} onTicker={onTicker} />
              {b.pair_idea && (
                <div className="rv-pair">Pair: <b className="up">long {b.pair_idea.long}</b> / <b className="down">short {b.pair_idea.short}</b>
                  <span className="faint"> · {b.pair_idea.spread_pct.toFixed(0)} pt valuation gap</span></div>
              )}
            </div>
          ))}
          {buckets.length > limit && (
            <button type="button" className="btn small" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `All ${buckets.length} buckets`}</button>
          )}
          <p className="faint rv-note">LTM EV/revenue. Fair multiple regresses EV/revenue on growth across each bucket; ±15% = in line. "basis?" = revenue basis differs (e.g. gross vs net), excluded.</p>
        </>
      )}
    </section>
  );
}

/** Per-ticker: the model's relative value by bucket plus private-market marks (Model view). */
export function ModelRelativeValue({ relativeValue = [], privateMarket }) {
  const marks = privateMarket?.marks || [];
  if (!relativeValue.length && !marks.length) return null;
  return (
    <section className="section glass">
      <h3>Relative value vs peers</h3>
      {relativeValue.map((b) => (
        <div key={b.bucket} className="rv-bucket">
          <div className="rv-head">
            <b>{b.bucket}{b.primary ? " (primary)" : ""}</b>
            <span className="faint">
              {b.subject?.verdict ? `this name: ${b.subject.verdict} (${pct(b.subject.gap_pct)} vs ${b.subject.basis})` : b.subject?.flag || ""}
            </span>
          </div>
          {b.rows.filter((r) => r.verdict).length >= 2
            ? <BucketRows rows={b.rows.filter((r) => r.ev_to_revenue != null || r.is_subject)} subjectLabel="This name" />
            : <p className="faint" style={{ fontSize: 12, margin: 0 }}>Not enough peer multiples yet to rank this bucket.</p>}
        </div>
      ))}
      {marks.length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>Private-market marks</h3>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Mark</th><th>Type</th><th>Date</th><th>Valuation</th><th>EV/Rev</th></tr></thead>
              <tbody>
                {marks.map((k) => (
                  <tr key={k.name} title={`${k.relevance} · ${k.source}`}>
                    <td style={{ textAlign: "left" }}>{k.name}</td><td>{k.kind.replace(/_/g, " ")}</td><td>{k.date}</td>
                    <td className="mono">{fmtMoneyMm(k.valuation_mm)}</td><td className="mono">{mult(k.implied_ev_to_revenue)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {privateMarket.applied_ev_to_revenue && (
            <p className="muted" style={{ fontSize: 13 }}>Private / strategic buyer multiple: <b>{mult(privateMarket.applied_ev_to_revenue)}</b>. {privateMarket.rationale}</p>
          )}
        </>
      )}
    </section>
  );
}
