import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { changeClass, featuredTicker, fmtPct, fmtPrice, timeAgo } from "../format.js";
import { LAPTOP_QUERY, useMediaQuery } from "../hooks.js";
import ConvictionMeter from "./ConvictionMeter.jsx";
import { NewsFeed, ThemesBoard } from "./Dashboard.jsx";
import { RelativeValueBoard } from "./RelativeValue.jsx";

// three.js is only downloaded on laptop-size screens.
const TownScene = lazy(() => import("../3d/TownScene.jsx"));

function AgentCard({ agent, onOpen }) {
  const t = featuredTicker(agent);
  return (
    <button className="agent-card glass" style={{ borderTopColor: agent.color }} onClick={onOpen} aria-label={`Enter ${agent.name}'s office`}>
      <div className="head">
        <div className="avatar" style={{ background: `${agent.color}22`, border: `1px solid ${agent.color}` }} aria-hidden="true">🤖</div>
        <div>
          <h2>{agent.name}</h2>
          <div className="sub">
            <span className="dot" style={{ background: agent.status === "working" ? "var(--green)" : agent.status === "queued" ? "var(--amber)" : "var(--text-3)" }} />{" "}
            {agent.status} · {agent.ticker_count} tickers · updated {timeAgo(agent.last_update)}
          </div>
        </div>
      </div>
      {t && (
        <div className="feature">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
            <span className="sym">{t.symbol}</span>
            {t.signal && <span className={`signal ${t.signal}`}>{t.signal}</span>}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span className="px">{t.price == null ? "—" : fmtPrice(t.price)}</span>
            <span className={`mono ${changeClass(t.change_pct)}`}>{fmtPct(t.change_pct)}</span>
          </div>
          <div style={{ marginTop: 8 }}><ConvictionMeter value={t.conviction_level} /></div>
          <div className="headline">{t.headline || "Initial research queued."}</div>
        </div>
      )}
      <div className="ticker-chips">
        {agent.tickers.map((x) => (
          <span key={x.symbol} className="chip">
            {x.symbol} <span className={changeClass(x.change_pct)}>{x.change_pct == null ? "" : fmtPct(x.change_pct, 1)}</span>
          </span>
        ))}
      </div>
    </button>
  );
}

function Carousel({ agents, onEnterAgent, onOpenHall }) {
  const track = useRef();
  const [active, setActive] = useState(0);
  const total = agents.length + 1;

  useEffect(() => {
    const el = track.current;
    if (!el) return undefined;
    const onScroll = () => {
      const card = el.firstElementChild;
      if (!card) return;
      setActive(Math.round(el.scrollLeft / (card.getBoundingClientRect().width + 14)));
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const go = (i) => {
    const card = track.current?.children[i];
    card?.scrollIntoView({ behavior: "smooth", inline: "start", block: "nearest" });
  };

  return (
    <div className="carousel-page">
      <div className="carousel-intro">
        <h1>Analyst Town</h1>
        <p>{agents.reduce((n, a) => n + a.ticker_count, 0)} tickers across {agents.length} agents. Swipe, then tap to enter an office.</p>
      </div>
      <div className="carousel" ref={track} role="list">
        {agents.map((a) => (
          <div role="listitem" key={a.id} style={{ display: "contents" }}>
            <AgentCard agent={a} onOpen={() => onEnterAgent(a)} />
          </div>
        ))}
        <div role="listitem" style={{ display: "contents" }}>
          <button className="agent-card glass hall-card" onClick={onOpenHall} aria-label="Enter the boardroom">
            <div className="head">
              <div className="avatar" style={{ border: "1px solid var(--text-2)" }} aria-hidden="true">🏙️</div>
              <div>
                <h2>Boardroom</h2>
                <div className="sub">Daily pod meeting over Central Park · 4:30pm ET</div>
              </div>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              Agents present their theses, challenge each other and update their calls. Tap to hear the latest meeting in their voices, or ask the pod a question.
            </p>
          </button>
        </div>
      </div>
      <div className="carousel-dots" role="tablist" aria-label="Agents">
        {Array.from({ length: total }, (_, i) => (
          <button key={i} role="tab" aria-current={i === active} aria-label={i < agents.length ? agents[i].name : "Boardroom"} onClick={() => go(i)}>
            <span />
          </button>
        ))}
      </div>
    </div>
  );
}

function TownStats({ agents }) {
  const working = agents.filter((a) => a.status === "working").length;
  const tickers = agents.flatMap((a) => a.tickers);
  const up = tickers.filter((t) => t.change_pct != null && t.change_pct >= 0).length;
  const priced = tickers.filter((t) => t.change_pct != null).length;
  return (
    <div className="town-stats">
      <div><b>{agents.length}</b><span>agents</span></div>
      <div><b>{tickers.length}</b><span>tickers</span></div>
      <div><b className={working ? "up" : ""}>{working}</b><span>working now</span></div>
      <div><b>{priced ? `${up}/${priced}` : "–"}</b><span>green today</span></div>
    </div>
  );
}

/** 3D town + dashboard sidebar on laptops (>= 1200px); card carousel + dashboard below on phones and tablets. */
export default function TownView({ agents, onEnterAgent, onOpenHall, onTicker }) {
  const laptop = useMediaQuery(LAPTOP_QUERY);
  if (!laptop) {
    return (
      <>
        <Carousel agents={agents} onEnterAgent={onEnterAgent} onOpenHall={onOpenHall} />
        <div className="dash-mobile">
          <RelativeValueBoard onTicker={onTicker} limit={4} />
          <ThemesBoard onTicker={onTicker} />
          <NewsFeed onTicker={onTicker} limit={15} />
        </div>
      </>
    );
  }
  return (
    <div className="home">
      <div className="town">
        <Suspense fallback={<div className="town-loading">Building the town…</div>}>
          <TownScene agents={agents} onEnterAgent={onEnterAgent} onOpenHall={onOpenHall} />
        </Suspense>
        <div className="town-hud glass">
          <h1>Analyst Town</h1>
          <p>Central Park. Click a house to step into an agent's office; the town hall or the tower on the skyline takes you up to the boardroom. Brighter screens = fresher thinking.</p>
          <TownStats agents={agents} />
        </div>
      </div>
      <aside className="dash-side" aria-label="Market dashboard">
        <RelativeValueBoard onTicker={onTicker} />
        <ThemesBoard onTicker={onTicker} />
        <NewsFeed onTicker={onTicker} />
      </aside>
    </div>
  );
}
