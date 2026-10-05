import { useState } from "react";
import { fmtPct, timeAgo } from "../format.js";
import { useDashboard } from "../hooks.js";

const CATEGORY_LABELS = { earnings: "Earnings", product: "Product", regulatory: "Regulatory", leadership: "Leadership", news: "Market" };
const NOTE_COLORS = ["cyan", "magenta", "lime", "amber"];

const fmtDate = (iso) => {
  if (!iso) return "n/a";
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
};
const daysUntil = (iso) => Math.round((new Date(`${iso}T12:00:00`) - new Date()) / 864e5);
const fmtBig = (v) => (v == null ? "n/a" : Math.abs(v) >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : `$${(v / 1e6).toFixed(1)}M`);

function FeedState({ state, empty }) {
  if (state.error && !state.data) {
    return (
      <p className="faint feed-note">
        {state.error.status === 404 ? "The news feed needs the latest backend deploy." : `Feed unavailable: ${state.error.message}`}
      </p>
    );
  }
  if (!state.data) return <div className="skeleton" style={{ height: 140 }} />;
  return <p className="faint feed-note">{empty}</p>;
}

function NewsCard({ item, onTicker, showTicker = true }) {
  const [open, setOpen] = useState(false);
  return (
    <article className={`news-card ${open ? "open" : ""}`}>
      <button className="news-main" onClick={() => setOpen(!open)} aria-expanded={open}>
        <div className="news-meta">
          {showTicker && <span className="news-ticker mono">{item.ticker}</span>}
          <span className={`news-cat ${item.category}`}>{CATEGORY_LABELS[item.category] || "Market"}</span>
          <span className="faint">{item.source} · {timeAgo(item.published_at)}</span>
        </div>
        <h4>{item.title}</h4>
      </button>
      {open && (
        <div className="news-detail">
          {item.description && <p>{item.description}</p>}
          <div className="news-actions">
            {item.sentiment && <span className={`tone ${item.sentiment}`}>{item.sentiment}</span>}
            {onTicker && showTicker && <button className="btn small" onClick={() => onTicker(item.ticker)}>Open {item.ticker}</button>}
            {item.url && <a className="btn small" href={item.url} target="_blank" rel="noopener noreferrer">Read at source ↗</a>}
          </div>
        </div>
      )}
    </article>
  );
}

/** Real-time headlines across every covered ticker, filterable by type. Cards expand for detail. */
export function NewsFeed({ onTicker, limit = 30 }) {
  const state = useDashboard();
  const [cat, setCat] = useState("all");
  const news = state.data?.news || [];
  const counts = news.reduce((m, a) => ({ ...m, [a.category]: (m[a.category] || 0) + 1 }), {});
  const shown = news.filter((a) => cat === "all" || a.category === cat).slice(0, limit);
  return (
    <section className="dash-panel glass" aria-labelledby="news-h">
      <div className="dash-head">
        <h2 id="news-h"><span className="live-dot" aria-hidden="true" />News feed</h2>
        {state.data && <span className="faint">updated {timeAgo(state.data.generated_at)}</span>}
      </div>
      {news.length > 0 && (
        <div className="filter-row" role="tablist" aria-label="News type">
          {["all", ...Object.keys(CATEGORY_LABELS).filter((k) => counts[k])].map((k) => (
            <button key={k} role="tab" aria-selected={cat === k} className="filter-chip" onClick={() => setCat(k)}>
              {k === "all" ? "All" : CATEGORY_LABELS[k]} <span className="faint">{k === "all" ? news.length : counts[k]}</span>
            </button>
          ))}
        </div>
      )}
      {shown.length ? (
        <div className="news-list">{shown.map((a) => <NewsCard key={a.url || a.title} item={a} onTicker={onTicker} />)}</div>
      ) : (
        <FeedState state={state} empty="No headlines yet. They arrive as agents refresh their research." />
      )}
    </section>
  );
}

/** Bulletin board of cross-ticker market themes, each tagged with the tickers it touches. */
export function ThemesBoard({ onTicker }) {
  const state = useDashboard();
  const themes = state.data?.themes || [];
  return (
    <section className="dash-panel glass" aria-labelledby="themes-h">
      <div className="dash-head">
        <h2 id="themes-h">Key themes</h2>
        <span className="faint">from headlines + theses</span>
      </div>
      {themes.length ? (
        <div className="board">
          {themes.map((t, i) => (
            <div key={t.name} className={`note ${NOTE_COLORS[i % NOTE_COLORS.length]}`} style={{ "--tilt": `${((i * 37) % 5) - 2}deg` }}>
              <span className="pin" aria-hidden="true" />
              <div className={`tone ${t.tone}`}>{t.tone}</div>
              <h3>{t.name}</h3>
              {t.headlines[0] && <p className="note-quote">“{t.headlines[0].title}”</p>}
              <div className="note-tickers">
                {t.tickers.map((s) => (
                  <button key={s} className="mini-chip mono" onClick={() => onTicker?.(s)} aria-label={`Open ${s}`}>{s}</button>
                ))}
              </div>
              <div className="note-foot faint">{t.mentions} headline{t.mentions === 1 ? "" : "s"}</div>
            </div>
          ))}
        </div>
      ) : (
        <FeedState state={state} empty="No cross-ticker themes yet." />
      )}
    </section>
  );
}

/** Last and next earnings for one ticker, plus the latest reported quarter. */
export function EarningsCard({ ticker }) {
  const state = useDashboard();
  const e = state.data?.earnings?.[ticker];
  if (!state.data) return state.error ? null : <div className="skeleton" style={{ height: 120, marginBottom: 14 }} />;
  if (!e || (!e.last && !e.next && !e.result)) return null;
  const est = e.next_estimate;
  const soon = est != null && daysUntil(est);
  return (
    <section className="section glass" aria-label={`${ticker} earnings`}>
      <h3>Earnings calendar</h3>
      <div className="earnings-grid">
        <div className="earn-cell">
          <div className="k">Last report</div>
          <div className="v">{e.last ? fmtDate(e.last.date) : "n/a"}</div>
          {e.last && (
            <div className="faint sub">
              {e.last.form} {e.last.kind}
              {e.last.url && <> · <a href={e.last.url} target="_blank" rel="noopener noreferrer">filing ↗</a></>}
            </div>
          )}
        </div>
        <div className="earn-cell next">
          <div className="k">Next report</div>
          {e.next ? (
            <>
              <div className="v">{e.next.timing}</div>
              <div className="faint sub">{e.next.event}</div>
            </>
          ) : est ? (
            <>
              <div className="v">~{fmtDate(est)}</div>
              <div className="faint sub">estimated · {soon <= 0 ? "due now" : `in ${soon} days`}</div>
            </>
          ) : <div className="v">n/a</div>}
        </div>
        <div className="earn-cell">
          <div className="k">Latest quarter{e.result ? ` · ${e.result.period.replace("CY", "")}` : ""}</div>
          {e.result ? (
            <>
              <div className="v">{fmtBig(e.result.revenue)} <span className={`small ${e.result.revenue_growth_yoy_pct >= 0 ? "up" : "down"}`}>{fmtPct(e.result.revenue_growth_yoy_pct, 1)} y/y</span></div>
              <div className="faint sub">op. margin {e.result.operating_margin_pct == null ? "n/a" : `${e.result.operating_margin_pct.toFixed(1)}%`} · net income {fmtBig(e.result.net_income)}</div>
            </>
          ) : <div className="v faint">n/a</div>}
        </div>
      </div>
      {e.next?.why && <p className="faint" style={{ fontSize: 13, margin: "10px 0 0" }}>Why it matters: {e.next.why}</p>}
      <p className="faint" style={{ fontSize: 12, margin: "8px 0 0" }}>Consensus expected vs. actual isn't in the data feed yet; results come from SEC filings.</p>
    </section>
  );
}

/** The last few headlines for one ticker. */
export function TickerNews({ ticker, limit = 5 }) {
  const state = useDashboard();
  const items = (state.data?.news || []).filter((a) => a.ticker === ticker).slice(0, limit);
  if (!items.length && state.data) return null;
  return (
    <section className="section glass" aria-label={`${ticker} news`}>
      <h3>Related news</h3>
      {items.length ? <div className="news-list">{items.map((a) => <NewsCard key={a.url || a.title} item={a} showTicker={false} />)}</div> : <FeedState state={state} />}
    </section>
  );
}

/** Market themes that touch this ticker. */
export function TickerThemes({ ticker }) {
  const state = useDashboard();
  const themes = (state.data?.themes || []).filter((t) => t.tickers.includes(ticker));
  if (!themes.length) return null;
  return (
    <section className="section glass" aria-label={`Themes affecting ${ticker}`}>
      <h3>Key themes</h3>
      <div className="theme-rows">
        {themes.map((t) => (
          <div key={t.name} className="theme-row">
            <span className={`tone ${t.tone}`}>{t.tone}</span>
            <b>{t.name}</b>
            <span className="faint">also {t.tickers.filter((s) => s !== ticker).slice(0, 5).join(", ") || "only this name"}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
