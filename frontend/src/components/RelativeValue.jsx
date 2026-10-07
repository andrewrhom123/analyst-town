import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { fmtMoneyMm } from "../format.js";

const pct = (v) => (v == null ? "n/a" : `${v > 0 ? "+" : ""}${v.toFixed(0)}%`);
const mult = (v) => (v == null ? "n/m" : `${v.toFixed(1)}x`);
/** LTM growth / margin: "–" when not meaningful (negative base, no data); clamp huge off-tiny-base growth. */
const ltm = (v) => (v == null ? "–" : v > 300 ? ">300%" : v < -99 ? "<-99%" : `${v.toFixed(0)}%`);
const SHORT = { "EV/Revenue": "EV/Rev", "EV/EBITDA": "EV/EBITDA" };
const timeShort = (iso) => {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return d.toLocaleString([], today ? { hour: "numeric", minute: "2-digit" } : { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
};

/**
 * Rich-to-cheap rows for one bucket: a bar per name centred on fair value (left), then the bucket's multiple and
 * LTM revenue growth, EBITDA growth and EBITDA margin (last four quarters) on the right.
 */
export function BucketRows({ rows, onTicker, multipleName = "EV/Revenue" }) {
  const max = Math.max(50, ...rows.map((r) => Math.abs(r.gap_pct ?? 0)));
  return (
    <ol className="rv-rows">
      <li className="rv-colhead" aria-hidden="true">
        <span /><span /><span className="rv-num" title="Premium (+) or discount (-) to the growth-adjusted fair multiple">vs fair</span>
        <span title={`${multipleName}: EV marked to the latest price over LTM`}>{SHORT[multipleName] || multipleName}</span>
        <span title="LTM revenue growth (last four quarters vs the four before)">Rev gr</span>
        <span title="LTM EBITDA growth (last four quarters vs the four before)">EBITDA gr</span>
        <span title="LTM EBITDA margin (EBITDA / revenue, last four quarters)">EBITDA %</span>
      </li>
      {rows.map((r) => {
        const gap = r.gap_pct;
        const w = gap == null ? 0 : Math.min(50, (Math.abs(gap) / max) * 50);
        return (
          <li key={r.symbol} className={`${r.verdict || "na"} ${r.is_subject || r.covered ? "ours" : ""}`}
            title={r.flag || `${r.symbol}: ${mult(r.multiple ?? r.ev_to_revenue)} ${multipleName}, fair ${mult(r.fair_multiple ?? r.fair_ev_to_revenue)}${r.period ? ` · ${r.period}` : ""}`}>
            <button type="button" className="rv-sym mono" onClick={() => onTicker?.(r.symbol)} disabled={!onTicker || !(r.covered || r.is_subject)}>
              {r.is_subject ? `▸ ${r.symbol}` : r.symbol}
            </button>
            <span className="rv-bar" aria-hidden="true">
              {gap != null && <i style={{ width: `${w}%`, [gap >= 0 ? "left" : "right"]: "50%" }} />}
              <b className="rv-mid" />
            </span>
            <span className="rv-num mono">{r.flag ? "basis?" : pct(gap)}</span>
            <span className="rv-c mono" title={[(r.feed_multiples || []).includes(r.multiple_key) ? "From the market feed's TTM ratio (LTM EBITDA/revenue not available from filings)" : r.period,
              r.ev_live ? `EV marked to $${r.price} at ${timeShort(r.priced_at)}` : "EV not repriced (no live quote or share count)"].filter(Boolean).join(" · ")}>
              {mult(r.multiple ?? r.ev_to_revenue)}{(r.feed_multiples || []).includes(r.multiple_key) ? "*" : ""}
            </span>
            <span className="rv-c mono">{ltm(r.revenue_growth_pct)}</span>
            <span className="rv-c mono">{ltm(r.ebitda_growth_pct)}</span>
            <span className="rv-c mono">{ltm(r.ebitda_margin_pct)}</span>
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
    const load = () => api.relativeValue().then(setData).catch((e) => setError(e.message));
    load();
    const id = setInterval(() => document.visibilityState === "visible" && load(), 5 * 60 * 1000);
    return () => clearInterval(id);
  }, []);
  if (error) return null;
  const buckets = (data?.buckets || []).filter((b) => b.priced >= 2);
  return (
    <section className="dash-panel glass rv-board" aria-label="Relative value">
      <div className="dash-head">
        <h2>Relative value</h2>
        <span className="faint" title={data ? `${data.live_names} of ${data.total_names} names repriced to the latest quote; the rest use their last cached EV` : undefined}>
          {data?.priced_as_of ? `priced ${timeShort(data.priced_as_of)} · ${data.live_names}/${data.total_names} live` : "rich ↔ cheap vs peers"}
        </span>
      </div>
      {!data ? <div className="skeleton" style={{ height: 160 }} /> : buckets.length === 0 ? (
        <p className="faint">Peer multiples fill in as the comps cache warms up.</p>
      ) : (
        <>
          {(showAll ? buckets : buckets.slice(0, limit)).map((b) => (
            <div key={b.bucket} className="rv-bucket">
              <div className="rv-head">
                <b>{b.bucket}</b>
                <span className="faint" title={b.multiple_reason}>
                  <b className="rv-mult">{b.multiple_name}</b> · {b.fit ? `fair = ${b.fit.intercept.toFixed(1)}x + ${b.fit.slope.toFixed(2)}x/pt rev growth` : `median ${mult(b.median_multiple)}`}
                </span>
              </div>
              <BucketRows rows={b.rows.filter((r) => r.verdict || r.flag)} onTicker={onTicker} multipleName={b.multiple_name} />
              {b.pair_idea && (
                <div className="rv-pair">Pair: <b className="up">long {b.pair_idea.long}</b> / <b className="down">short {b.pair_idea.short}</b>
                  <span className="faint"> · {b.pair_idea.spread_pct.toFixed(0)} pt valuation gap</span></div>
              )}
            </div>
          ))}
          {buckets.length > limit && (
            <button type="button" className="btn small" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `All ${buckets.length} buckets`}</button>
          )}
          <p className="faint rv-note">
            Each bucket uses its industry's multiple (EV/EBITDA or EV/Revenue): EV marked to the latest price (refreshes every 30 min in market hours) over LTM figures from the last four
            quarters of SEC filings (market-feed TTM where unavailable). EBITDA = operating income + D&amp;A + stock comp.
            Fair multiple = the multiple regressed on LTM revenue growth across the bucket; ±15% = in line. "basis?" = not
            comparable (e.g. gross vs net revenue). "–" = not meaningful or no data. * = multiple from the market feed.
          </p>
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
      <p className="faint" style={{ fontSize: 12, margin: "0 0 8px" }}>This name uses current-year model figures; peers use LTM (last four quarters).</p>
      {relativeValue.map((b) => (
        <div key={b.bucket} className="rv-bucket">
          <div className="rv-head">
            <b>{b.bucket}{b.primary ? " (primary)" : ""} · <span className="rv-mult" title={b.multiple_reason}>{b.multiple_name || "EV/Revenue"}</span></b>
            <span className="faint">
              {b.subject?.verdict ? `this name: ${b.subject.verdict} (${pct(b.subject.gap_pct)} vs ${b.subject.basis})` : b.subject?.flag || ""}
            </span>
          </div>
          {b.rows.filter((r) => r.verdict).length >= 2
            ? <BucketRows rows={b.rows.filter((r) => (r.multiple ?? r.ev_to_revenue) != null || r.flag || r.is_subject)} multipleName={b.multiple_name} />
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
