import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { fmtMoneyMm, fmtPct, fmtPrice } from "../format.js";
import ChartWidget from "./ChartWidget.jsx";
import DownloadButtons from "./DownloadButtons.jsx";
import ElevatorPitch, { ThesisDetails } from "./ElevatorPitch.jsx";
import Markdown from "./Markdown.jsx";

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
  const { data, error } = useLoad(() => api.model(agentId, ticker), [agentId, ticker]);
  if (error) return <p className="down">{error.message}</p>;
  if (!data) return <div className="skeleton" style={{ height: 300 }} />;
  if (!data.annual) return <p className="muted">{data.note === "no deep dive yet" ? "No model yet. The deep dive is queued." : `No financial model: ${data.note || "not available"}.`}</p>;
  const rows = data.annual;
  const line = (label, key, fmt) => (
    <tr><td>{label}</td>{rows.map((r) => <td key={r.year} className="mono">{fmt(r[key])}</td>)}</tr>
  );
  return (
    <>
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
    </>
  );
}

const VIEWS = ["Pitch", "Memo", "Model", "Chart"];

/** Everything about one ticker: pitch/conviction, memo, model, chart, downloads. */
export default function TickerScreen({ agentId, ticker, compact = false }) {
  const [view, setView] = useState("Pitch");
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
            <ElevatorPitch b={b} />
            {b.price?.price != null && (
              <section className="section glass">
                <h3>Price chart</h3>
                <ChartWidget agentId={agentId} ticker={ticker} />
              </section>
            )}
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
