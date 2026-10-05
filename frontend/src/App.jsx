import { useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { API_BASE, api, getAccessKey, setAccessKey } from "./api/client.js";
import InstructionsTab from "./components/InstructionsTab.jsx";
import MeetingPanel from "./components/MeetingPanel.jsx";
import { timeAgo } from "./format.js";
import { usePolling } from "./hooks.js";
import AgentDetail from "./pages/AgentDetail.jsx";
import Home from "./pages/Home.jsx";

function SettingsModal({ onClose }) {
  const [key, setKey] = useState(getAccessKey());
  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Settings" onClick={onClose}>
      <div className="modal glass" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Settings</h2>
          <button className="btn icon" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="muted">
          Actions that spend Claude credits (asking agents, meetings, coverage changes) need the backend's
          <code> API_ACCESS_KEY</code>. It's stored only in this browser.
        </p>
        <div className="field">
          <label htmlFor="key">Access key</label>
          <input id="key" className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
        </div>
        <p className="faint" style={{ fontSize: 13 }}>Backend: <span className="mono">{API_BASE}</span></p>
        <button className="btn primary" onClick={() => { setAccessKey(key.trim()); onClose(); }}>Save</button>
      </div>
    </div>
  );
}

export default function App() {
  const [modal, setModal] = useState(null); // "help" | "settings" | "meeting"
  const status = usePolling(() => api.status(), [], 30000);
  const budget = status.data?.budget;

  return (
    <>
      <header className="app-header">
        <Link to="/" className="brand" aria-label="Analyst Town home">
          <div className="brand-mark" aria-hidden="true" />
          <span>Analyst Town</span>
        </Link>
        <div className="header-status" aria-live="polite">
          {status.error ? (
            <span className="down">Backend offline</span>
          ) : budget ? (
            <>
              <span className="budget mono" title="Claude spend today vs daily budget">
                ${budget.spent_today_usd.toFixed(2)} / ${budget.daily_budget_usd.toFixed(0)}
              </span>
              {budget.buildout?.pending_jobs > 0 && (
                <span className="budget mono buildout" title="One-time build-out pool: first deep dive on every ticker (separate from the daily budget)">
                  build-out ${budget.buildout.spent_usd.toFixed(2)} / ${budget.buildout.budget_usd.toFixed(0)} · {budget.buildout.running_jobs} running, {budget.buildout.pending_jobs} left
                </span>
              )}
              <span className="last-check faint">prices {timeAgo(status.data.last_price_check)}</span>
            </>
          ) : null}
        </div>
        <nav className="header-actions" aria-label="Main">
          <button className="btn icon" onClick={() => setModal("meeting")} aria-label="Agent meeting" title="Agent meeting">🗣️</button>
          <button className="btn icon" onClick={() => setModal("help")} aria-label="Instructions" title="Instructions">?</button>
          <button className="btn icon" onClick={() => setModal("settings")} aria-label="Settings" title="Settings">⚙︎</button>
        </nav>
      </header>

      <Routes>
        <Route path="/" element={<Home onOpenMeeting={() => setModal("meeting")} />} />
        <Route path="/agent/:agentId" element={<AgentDetail />} />
        <Route path="*" element={<Home onOpenMeeting={() => setModal("meeting")} />} />
      </Routes>

      {modal === "help" && <InstructionsTab onClose={() => setModal(null)} />}
      {modal === "settings" && <SettingsModal onClose={() => setModal(null)} />}
      {modal === "meeting" && <MeetingPanel onClose={() => setModal(null)} />}
    </>
  );
}
