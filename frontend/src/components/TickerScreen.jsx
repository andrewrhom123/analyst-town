import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { changeClass, fmtMoneyMm, fmtPct, fmtPrice } from "../format.js";
import ChartWidget from "./ChartWidget.jsx";
import { EarningsCard, TickerNews, TickerThemes } from "./Dashboard.jsx";
import DownloadButtons from "./DownloadButtons.jsx";
import ElevatorPitch, { ThesisDetails } from "./ElevatorPitch.jsx";
import Markdown from "./Markdown.jsx";
import ModelVersions from "./ModelVersions.jsx";
import { ModelRelativeValue } from "./RelativeValue.jsx";

function useLoad(fetcher, deps) {
  const [state, setState] = useState({ data: null, error: null });
  useEffect(() => {
    let live = true;
    setState({ data: null, error: null });
    fetcher().then((data) => live && setState({ data, error: null })).catch((error) => live && setState({ data: null, error }));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

function MemoView({ agentId, ticker }) {
  const { data, error } = useLoad(() => api.memo(agentId, ticker), [agentId, ticker]);
  if (error) return <p className="muted">{error.status === 404 ? "No research memo yet. The deep dive is queued." : error.message}</p>;
  if (!data) return <div className="skeleton" style={{ height: 400 }} />;
  return <section className="section glass"><Markdown>{data.long_form_memo}</Markdown></section>;
}

function ModelView({ agentId, ticker }) {
  const [rev, setRev] = useState(0);
  const { data, error } = useLoad(() => api.model(agentId, ticker), [agentId, ticker, rev]);
  // the versions panel stays mounted while the model reloads after an upload (keeps the upload result on screen)
  const versions = <ModelVersions agentId={agentId} ticker={ticker} onUploaded={() => setRev((n) => n + 1)} />;
  if (error) return <>{versions}<p className="down">{error.message}</p></>;
  if (!data) return <>{versions}<div className="skeleton" style={{ height: 300 }} /></>;
  if (!data.annual) return <p className="muted">{data.note === "no deep dive yet" ? "No model yet. The deep dive is queued." : `No financial model: ${data.note || "not available"}.`}</p>;
  const rows = data.annual;
  const line = (label, key, fmt) => (
    <tr><td>{label}</td>{rows.map((r) => <td key={r.year} className="mono">{fmt(r[key])}</td>)}</tr>
  );
  return (
    <>
      {versions}
      {data.summary && (
        <section className="section glass">
          <h3>Summary</h3>
          <div className="stats">{Object.entries(data.summary).map(([k, v]) => <div className="stat" key={k}><div className="k">{k.replace(/_/g, " ")}</div><div className="v">{v}</div></div>)}</div>
        </section>
      )}
      <section className="section glass">
        <h3>Annual model ($mm)</h3>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Line item</th>{rows.map((r) => <th key={r.year}>{r.year}</th>)}</tr></thead>
            <tbody>
              {line("Revenue", "revenue", fmtMoneyMm)}
              {line("Growth", "revenue_growth_pct", (v) => fmtPct(v, 1))}
              {line("Gross margin", "gross_margin_pct", (v) => (v == null ? "n/a" : `${v.toFixed(1)}%`))}
              {line("Adj. EBITDA", "adj_ebitda", fmtMoneyMm)}
              {line("EBITDA margin", "adj_ebitda_margin_pct", (v) => (v == null ? "n/a" : `${v.toFixed(1)}%`))}
              {line("Free cash flow", "fcf", fmtMoneyMm)}
            </tbody>
          </table>
        </div>
      </section>
      <section className="section glass">
        <h3>Valuation</h3>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Method</th><th>EV</th><th>Per share</th><th>vs. now</th></tr></thead>
            <tbody>
              {data.valuation.football_field.map((m) => (
                <tr key={m.method}><td>{m.method}</td><td className="mono">{fmtMoneyMm(m.enterprise_value)}</td><td className="mono">{fmtPrice(m.implied_price)}</td><td className="mono">{fmtPct(m.upside_pct, 0)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3 style={{ marginTop: 14 }}>Scenarios</h3>
        <ul className="list">
          {data.scenarios.map((s) => <li key={s.name}><b>{s.name}</b> ({s.probability_pct}%): {fmtPrice(s.implied_price)}. <span className="muted">{s.description}</span></li>)}
        </ul>
      </section>
      <ModelRelativeValue relativeValue={data.relative_value} privateMarket={data.private_market} />
    </>
  );
}

/** Recent performance tiles: today, ~1 week, ~1 month (from the 30-day series), with a sparkline. */
function PerformanceStrip({ today, series }) {
  const closes = (series || []).map((d) => d.close).filter((v) => v != null);
  const last = closes[closes.length - 1];
  const back = (n) => (closes.length > n ? ((last - closes[closes.length - 1 - n]) / closes[closes.length - 1 - n]) * 100 : null);
  const tiles = [["Today", today], ["5 days", back(5)], ["30 days", closes.length > 1 ? ((last - closes[0]) / closes[0]) * 100 : null]];
  let spark = null;
  if (closes.length > 1) {
    const lo = Math.min(...closes), hi = Math.max(...closes), span = hi - lo || 1;
    const pts = closes.map((v, i) => `${(i / (closes.length - 1)) * 120},${34 - ((v - lo) / span) * 30}`).join(" ");
    const up = last >= closes[0];
    spark = (
      <svg className="spark" viewBox="0 0 120 36" preserveAspectRatio="none" role="img" aria-label={`30-day trend ${up ? "up" : "down"}`}>
        <polyline points={pts} fill="none" stroke={up ? "var(--up)" : "var(--down)"} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <div className="perf-strip">
      {tiles.map(([k, v]) => (
        <div className="perf-tile" key={k}><div className="k">{k}</div><div className={`v mono ${changeClass(v)}`}>{v == null ? "n/a" : fmtPct(v, 1)}</div></div>
      ))}
      {spark && <div className="perf-tile spark-tile"><div className="k">Trend</div>{spark}</div>}
    </div>
  );
}

const VIEWS = ["Pitch", "Memo", "Model", "Chart"];

/** Everything about one ticker: pitch/conviction, memo, model, chart, downloads. */
export default function TickerScreen({ agentId, ticker, color, compact = false }) {
  const [view, setView] = useState("Pitch");
  const [series, setSeries] = useState(null);
  const { data: b, error } = useLoad(() => api.briefing(agentId, ticker), [agentId, ticker]);

  return (
    <div>
      <div className="view-tabs" role="tablist" aria-label="Views">
        {VIEWS.map((v) => <button key={v} role="tab" className="btn small" aria-pressed={view === v} onClick={() => setView(v)}>{v}</button>)}
      </div>
      {error && <p className="down">{error.message}</p>}
      {view === "Pitch" && (
        b ? (
          <>
            <ElevatorPitch b={b} agentId={agentId} color={color} />
            {b.price?.price != null && (
              <section className="section glass">
                <h3>Price performance</h3>
                <PerformanceStrip today={b.price.change_pct} series={series} />
                <ChartWidget agentId={agentId} ticker={ticker} onData={(d) => setSeries(d.series)} />
              </section>
            )}
            {b.type === "public" && <EarningsCard ticker={ticker} />}
            <TickerNews ticker={ticker} />
            <TickerThemes ticker={ticker} />
            <ThesisDetails b={b} />
            {b.headline && (
              <section className="section glass">
                <h3>Download</h3>
                <DownloadButtons agentId={agentId} ticker={ticker} hasModel={b.type === "public"} />
              </section>
            )}
          </>
        ) : !error && <div className="skeleton" style={{ height: compact ? 260 : 360 }} />
      )}
      {view === "Memo" && <MemoView agentId={agentId} ticker={ticker} />}
      {view === "Model" && <ModelView agentId={agentId} ticker={ticker} />}
      {view === "Chart" && <section className="section glass"><ChartWidget agentId={agentId} ticker={ticker} /></section>}
    </div>
  );
}
