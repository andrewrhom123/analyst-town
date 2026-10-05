import { lazy, Suspense, useEffect } from "react";
import { Link } from "react-router-dom";
import { changeClass, fmtPct } from "../format.js";
import { LAPTOP_QUERY, useKeyboardOffset, useMediaQuery, useSwipe } from "../hooks.js";
import { setAmbience, usePlayer, useVoicePrefs } from "../voice/voice.js";
import ChatPanel from "./ChatPanel.jsx";
import TickerScreen from "./TickerScreen.jsx";

const OfficeScene = lazy(() => import("../3d/OfficeScene.jsx"));

function TickerTabs({ tickers, selected, onSelect }) {
  return (
    <div className="ticker-tabs" role="tablist" aria-label="Tickers">
      {tickers.map((t) => (
        <button key={t.symbol} role="tab" className="ticker-tab" aria-selected={t.symbol === selected} onClick={() => onSelect(t.symbol)}>
          <span className="sym">{t.symbol}</span>
          <span className={`chg ${changeClass(t.change_pct)}`}>{t.change_pct == null ? (t.type === "private" ? "private" : "—") : fmtPct(t.change_pct, 1)}</span>
        </button>
      ))}
    </div>
  );
}

function MobileKeyboard() {
  useKeyboardOffset();
  return null;
}

/** Agent office: laptop = robot + screens | content | chat. Phone = full-screen tabs + swipe + chat dock. */
export default function AgentOffice({ agent, tickers, selected, onSelect, onCoverageChange }) {
  const laptop = useMediaQuery(LAPTOP_QUERY);
  const player = usePlayer();
  const [prefs] = useVoicePrefs();
  const talking = player.agent === agent.key && player.status === "playing";
  const working = agent.status === "working";
  useEffect(() => {
    setAmbience(!!prefs.ambient, working);
    return () => setAmbience(false);
  }, [prefs.ambient, working]);
  const idx = tickers.findIndex((t) => t.symbol === selected);
  const swipe = useSwipe(
    () => idx < tickers.length - 1 && onSelect(tickers[idx + 1].symbol),
    () => idx > 0 && onSelect(tickers[idx - 1].symbol),
  );
  const chat = (
    <ChatPanel agentId={agent.key} agentName={agent.name} ticker={selected} color={agent.color} dock={!laptop} onCoverageChange={onCoverageChange} />
  );

  if (!laptop) {
    return (
      <div className="office">
        <MobileKeyboard />
        <div className="mobile-office-head">
          <div className="row">
            <Link to="/" className="btn icon small" aria-label="Back to town">←</Link>
            <span className="dot" style={{ background: agent.color }} aria-hidden="true" />
            <h1>{agent.name}</h1>
          </div>
          <TickerTabs tickers={tickers} selected={selected} onSelect={onSelect} />
        </div>
        <div className="office-content" {...swipe}>
          {selected ? <TickerScreen key={selected} agentId={agent.key} ticker={selected} color={agent.color} compact /> : <p className="empty">No tickers yet. Try /add TICKER to {agent.name}.</p>}
        </div>
        <div className="office-chat">{chat}</div>
      </div>
    );
  }

  return (
    <div className="office">
      <div className="office-left">
        <div className="office-title glass">
          <Link to="/" className="btn icon small back" aria-label="Back to town">←</Link>
          <span className="dot" style={{ background: agent.color }} aria-hidden="true" />
          <div>
            <h1>{agent.name}</h1>
            <div className="faint" style={{ fontSize: 12 }}>{tickers.length} tickers · {talking ? <b className="speaking-tag">speaking</b> : agent.status}</div>
          </div>
        </div>
        <div className="office-scene glass">
          <Suspense fallback={<div className="town-loading">Opening the office…</div>}>
            <OfficeScene agent={agent} tickers={tickers} selected={selected} onSelect={onSelect} talking={talking} />
          </Suspense>
        </div>
        <p className="faint" style={{ fontSize: 12, margin: 0 }}>Click a screen to switch tickers. Drag to look around.</p>
      </div>
      <div className="office-right">
        <div className="glass"><TickerTabs tickers={tickers} selected={selected} onSelect={onSelect} /></div>
        <div className="office-main">
          <div className="office-content">
            {selected ? <TickerScreen key={selected} agentId={agent.key} ticker={selected} color={agent.color} /> : <p className="empty">No tickers yet.</p>}
          </div>
          <div className="office-chat">{chat}</div>
        </div>
      </div>
    </div>
  );
}
