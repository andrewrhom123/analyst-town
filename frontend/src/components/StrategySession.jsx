import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { timeAgo } from "../format.js";
import { speak, stopSpeaking, useVoicePrefs, useVoiceStatus } from "../voice/voice.js";
import AudioPlayer from "./AudioPlayer.jsx";
import Markdown from "./Markdown.jsx";
import PitchCard from "./PitchCard.jsx";
import VoiceInput from "./VoiceInput.jsx";

const STEPS = [["context", "Market context"], ["strategy", "Your strategy"], ["discussion", "Alignment"], ["memo", "Memo & sign-off"]];
const KIND = {
  context: { label: "Market context", cls: "context" },
  aligned: { label: "Aligned", cls: "aligned" },
  question: { label: "Question", cls: "question" },
  memo: { label: "Memo", cls: "memo" },
  signoff: { label: "Signed off", cls: "aligned" },
  observation: { label: "Observation", cls: "context" },
  pitch: { label: "Pitch", cls: "memo" },
  agree: { label: "Agrees", cls: "aligned" },
  disagree: { label: "Disagrees", cls: "question" },
};
const clipId = (sid, m) => `strategy-${sid}-${m.id}`;

function stepIndex(s) {
  if (!s) return -1;
  if (s.phase === "done") return 4;
  if (s.phase === "memo") return 3;
  if (s.strategy) return s.phase === "discussion" && s.status === "thinking" && !s.messages.some((m) => m.kind === "aligned" || m.kind === "question") ? 1 : 2;
  return s.status === "thinking" ? 0 : 1;
}

/**
 * Ad-hoc town hall, two modes. Strategy session: macro sets the scene, the PM gives the strategy (typed or
 * spoken), the pod aligns or questions it, then everyone signs the research charter and re-states their theses.
 * Research conversation: broad questions; each analyst answers with observations, pitches and reactions; the
 * chair wraps up with a research memo. New agent turns are read aloud in each agent's voice, one at a time;
 * `onSpeaking` reports the line being spoken (for boardroom captions).
 */
export default function StrategySession({ agents, onSpeaking }) {
  const [session, setSession] = useState(null);
  const [past, setPast] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [text, setText] = useState("");
  const [handsFree, setHandsFree] = useState(false);
  const [charter, setCharter] = useState(null);
  const [prefs, setPrefs] = useVoicePrefs();
  const voice = useVoiceStatus();
  const autoSpeak = voice.tts && prefs.strategyVoice !== false; // on by default for sessions
  const spoken = useRef(new Set());
  const queue = useRef([]);
  const pumping = useRef(false);
  const mic = useRef();
  const live = useRef({ session: null, handsFree: false });
  live.current = { session, handsFree };
  const byKey = Object.fromEntries([...agents.map((a) => [a.key, a]), ["chair", { name: "Chair", color: "#a855f7" }]]);
  const color = (k) => byKey[k]?.color || "#94a3b8";

  const markAllSpoken = (s) => s?.messages.forEach((m) => spoken.current.add(clipId(s.session_id, m)));

  const pump = async () => {
    if (pumping.current) return;
    pumping.current = true;
    while (queue.current.length) {
      const { sid, m } = queue.current.shift();
      onSpeaking?.({ speaker: m.speaker, agent_key: m.agent_key, text: m.text });
      try {
        const finished = await speak(m.text, m.agent_key, clipId(sid, m));
        if (!finished) queue.current.length = 0; // stopped: don't keep talking
      } catch (e) {
        setError(e.message);
        queue.current.length = 0;
      }
    }
    onSpeaking?.(null);
    pumping.current = false;
    const { session: s, handsFree: hf } = live.current;
    if (hf && s?.status === "awaiting_user") mic.current?.start();
  };

  /** Merge a fresh copy of the session; queue any new agent turns for speech. */
  const accept = (s, { silent = false } = {}) => {
    setSession(s);
    if (!s) return;
    const fresh = s.messages.filter((m) => !spoken.current.has(clipId(s.session_id, m)));
    fresh.forEach((m) => spoken.current.add(clipId(s.session_id, m)));
    if (!silent && autoSpeak) {
      fresh.filter((m) => m.role === "agent").forEach((m) => queue.current.push({ sid: s.session_id, m }));
      pump();
    } else if (live.current.handsFree && s.status === "awaiting_user" && fresh.some((m) => m.role === "agent") && !pumping.current) {
      mic.current?.start();
    }
  };

  // Load the latest session (history is not re-spoken) and past sessions.
  useEffect(() => {
    let alive = true;
    api.strategyLatest()
      .then((s) => { if (!alive) return; markAllSpoken(s); setSession(s); })
      .catch((e) => alive && e.status !== 404 && setError(e.message))
      .finally(() => alive && setLoaded(true));
    api.strategySessions().then((rows) => alive && setPast(rows)).catch(() => {});
    api.charter().then((c) => alive && setCharter(c)).catch(() => {});
    return () => { alive = false; queue.current.length = 0; stopSpeaking(); };
  }, []);

  // Poll quickly while the pod is talking.
  useEffect(() => {
    if (session?.status !== "thinking") return undefined;
    const id = setInterval(() => {
      api.strategy(session.session_id).then((s) => accept(s)).catch(() => {});
    }, 2500);
    return () => clearInterval(id);
  }, [session?.status, session?.session_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      accept(await fn());
    } catch (e) {
      setError(e.message);
      setHandsFree(false);
    } finally {
      setBusy(false);
    }
  };

  const start = (mode) => act(async () => {
    stopSpeaking();
    const s = await api.strategyStart(mode);
    setPast((p) => [{ session_id: s.session_id, mode, status: s.status, started_at: s.started_at, strategy: "", memo_title: "" }, ...p]);
    return s;
  });
  const send = (raw) => {
    const value = (raw ?? text).trim();
    if (!value || !session) return;
    setText("");
    stopSpeaking();
    queue.current.length = 0;
    act(() => api.strategySay(session.session_id, value));
  };
  const finalize = () => act(async () => {
    const s = await api.strategyFinalize(session.session_id);
    if (s.mode === "strategy") setTimeout(() => api.charter().then(setCharter).catch(() => {}), 1000);
    return s;
  });
  useEffect(() => {
    if (session?.mode === "strategy" && session.phase === "done") api.charter().then(setCharter).catch(() => {});
  }, [session?.phase, session?.mode]);
  const close = () => act(() => api.strategyClose(session.session_id));
  const open = (id) => act(async () => { const s = await api.strategy(id); markAllSpoken(s); return s; });

  const active = session && (session.status === "thinking" || session.status === "awaiting_user");
  const research = session?.mode === "research";
  const step = stepIndex(session);
  const owners = Object.fromEntries(agents.flatMap((a) => a.tickers.map((t) => [t.symbol, a.key])));
  const openNames = (session?.open_questions || []).map((k) => byKey[k]?.name?.replace(/ (Analyst|Strategist)$/, "") || k);
  const placeholder = research
    ? "Ask the pod anything, e.g. where do you see the biggest market mispricing?"
    : !session?.strategy
      ? "Set the research direction, e.g. focus on idiosyncratic fundamentals; find long/short pairs that avoid beta"
      : openNames.length ? `Answer ${openNames.join(" and ")}…` : "Add to the strategy, or generate the charter";

  if (!loaded) return <section className="section glass"><div className="skeleton" style={{ height: 120 }} /></section>;

  return (
    <div className="strategy">
      <section className="section glass">
        <div className="strategy-head">
          <h3 style={{ margin: 0 }}>{active ? (research ? "Research conversation" : "Strategy session") : "Ad-hoc town hall"}</h3>
          {voice.tts && (
            <div className="voice-toggles">
              <button type="button" className="btn small" aria-pressed={autoSpeak} onClick={() => setPrefs({ strategyVoice: !autoSpeak })}>🔊 Voices</button>
              {voice.stt && active && (
                <button type="button" className={`btn small ${handsFree ? "live" : ""}`} aria-pressed={handsFree}
                  onClick={() => { const next = !handsFree; setHandsFree(next); if (next && session?.status === "awaiting_user" && !pumping.current) mic.current?.start(); else if (!next) mic.current?.stop(); }}>
                  {handsFree ? "● Hands-free on" : "🎙 Hands-free"}
                </button>
              )}
            </div>
          )}
        </div>
        {charter && (
          <details className="context-card" style={{ marginTop: 10 }}>
            <summary><b>Research charter in force:</b> {charter.title}</summary>
            <ul className="list">{charter.directives.map((d) => <li key={d}>{d}</li>)}</ul>
          </details>
        )}
        {!active ? (
          <div className="mode-pick">
            <div>
              <h4>Strategy session</h4>
              <p className="muted">Reset the research direction. Macro opens with the backdrop (rates, the Fed, sentiment, beta); you set
                the directives; each analyst aligns or questions with data; the pod signs a <b>Research Charter</b> every agent follows from
                then on, and updates its theses. About $1.</p>
              <button className="btn primary" onClick={() => start("strategy")} disabled={busy}>Start a strategy session</button>
            </div>
            <div>
              <h4>Research conversation</h4>
              <p className="muted">Ask the pod broad questions ("what's the most interesting stock you cover?", "where's the biggest
                mispricing?"). Each analyst answers, pitches trades and reacts to colleagues; wrap up for a research memo. ~$0.40 a question.</p>
              <button className="btn primary" onClick={() => start("research")} disabled={busy}>Start a research conversation</button>
            </div>
          </div>
        ) : !research && (
          <ol className="steps" aria-label="Session progress">
            {STEPS.map(([key, label], i) => <li key={key} className={i < step ? "done" : i === step ? "now" : ""}>{label}</li>)}
          </ol>
        )}
        {error && <p className="down" role="alert">{error}</p>}
      </section>

      {session && (
        <section className="section glass">
          <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
            {research ? "Research conversation" : "Strategy session"} #{session.session_id} · {session.status === "closed" ? "closed" : session.phase === "done" ? "complete" : session.status === "thinking" ? "in progress" : "your turn"} · started {timeAgo(session.started_at)}
          </div>
          {session.market_context?.headline && (
            <details className="context-card">
              <summary><b>Backdrop:</b> {session.market_context.headline}</summary>
              <dl>
                {["rates", "fed", "sentiment", "beta"].map((k) => <div key={k}><dt>{k === "fed" ? "Fed" : k[0].toUpperCase() + k.slice(1)}</dt><dd>{session.market_context[k]}</dd></div>)}
              </dl>
            </details>
          )}
          <ol className="strategy-log" aria-live="polite">
            {session.messages.map((m) => {
              const kind = KIND[m.kind];
              if (m.role === "system") return <li key={m.id} className="turn system">{m.text}</li>;
              if (m.role === "user") return <li key={m.id} className="turn user"><b>You{m.kind === "strategy" && !research ? " · strategy" : ""}</b><p>{m.text}</p></li>;
              return (
                <li key={m.id} className="turn agent" style={{ "--c": color(m.agent_key) }}>
                  <div className="turn-head">
                    <b>{m.speaker}</b>
                    {kind && <span className={`stance ${kind.cls}`}>{m.kind === "signoff" && m.meta.signed === false ? "Did not sign" : kind.label}</span>}
                    <AudioPlayer text={m.text} agent={m.agent_key} color={color(m.agent_key)} id={clipId(session.session_id, m)} />
                  </div>
                  <p>{m.text}</p>
                  {m.meta?.conflicts?.length > 0 && <ul className="conflicts">{m.meta.conflicts.map((c) => <li key={c}>{c}</li>)}</ul>}
                  {m.meta?.implications?.length > 0 && (
                    <div className="ticker-chips">{m.meta.implications.map((x) => <span key={x.ticker} className="chip" title={x.implication}>{x.ticker}: {x.implication}</span>)}</div>
                  )}
                  {m.kind === "signoff" && m.meta.reservations && <p className="faint" style={{ fontSize: 13 }}>Reservation: {m.meta.reservations}</p>}
                  {m.meta?.references?.length > 0 && <ul className="conflicts refs">{m.meta.references.map((r) => <li key={r}>{r}</li>)}</ul>}
                  {m.meta?.pitch && <PitchCard pitch={m.meta.pitch} color={color(m.agent_key)} owners={owners} />}
                  {m.kind === "memo" && m.meta?.directives?.length > 0 && <ul className="list">{m.meta.directives.map((d) => <li key={d}>{d}</li>)}</ul>}
                </li>
              );
            })}
            {session.status === "thinking" && (
              <li className="turn system"><span className="typing"><span /><span /><span /></span> {session.progress || "The pod is thinking"}…</li>
            )}
          </ol>

          {active && session.phase !== "memo" && (
            <>
              <form className="chat-form" style={{ padding: "10px 0 0", border: 0 }} onSubmit={(e) => { e.preventDefault(); send(); }}>
                <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder}
                  aria-label="Your strategy or reply" disabled={session.status !== "awaiting_user"} />
                <VoiceInput ref={mic} disabled={session.status !== "awaiting_user" || busy} onTranscript={(said) => send(said)}
                  onError={(msg) => { setError(msg); setHandsFree(false); }} onCancel={() => setHandsFree(false)} />
                <button className="btn primary" type="submit" disabled={busy || !text.trim() || session.status !== "awaiting_user"} aria-label="Send">➤</button>
              </form>
              <div className="board-controls" style={{ marginTop: 10 }}>
                <button className="btn" onClick={finalize} disabled={busy || session.status !== "awaiting_user" || !session.strategy}
                  title={openNames.length ? `${openNames.join(", ")} still ${openNames.length > 1 ? "have" : "has"} a question; the charter will record it as dissent` : undefined}>
                  {research ? "✍ Wrap up: research memo" : `✍ Generate research charter${openNames.length ? ` (${openNames.length} open question${openNames.length > 1 ? "s" : ""})` : ""}`}
                </button>
                <button className="btn" onClick={close} disabled={busy || session.status !== "awaiting_user"}>Close without memo</button>
              </div>
            </>
          )}
        </section>
      )}

      {session?.memo_md && (
        <section className="section glass">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <h3 style={{ margin: 0 }}>{research ? "Research memo" : "Research charter"}: {session.memo_title}</h3>
            <AudioPlayer text={session.memo_md} agent="chair" color="#a855f7" label="Read memo" />
          </div>
          <Markdown>{session.memo_md}</Markdown>
          {session.signoffs.length > 0 && (
            <>
              <h3 style={{ marginTop: 14 }}>Sign-offs</h3>
              <ul className="signoffs">
                {session.signoffs.map((x) => (
                  <li key={x.agent_key} style={{ "--c": color(x.agent_key) }}>
                    <span className={x.signed ? "up" : "down"}>{x.signed ? "✓" : "✗"}</span> <b>{x.analyst}</b>
                    {x.reservations ? <span className="faint"> · reservation: {x.reservations}</span> : null}
                  </li>
                ))}
              </ul>
            </>
          )}
          {session.revisions.length > 0 && (
            <>
              <h3 style={{ marginTop: 14 }}>Thesis updates</h3>
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Ticker</th><th>Desk</th><th>Change</th></tr></thead>
                  <tbody>
                    {session.revisions.filter((r) => r.revised).map((r) => (
                      <tr key={r.ticker}><td className="mono">{r.ticker}</td><td>{r.analyst.replace(/ (Analyst|Strategist)$/, "")}</td>
                        <td style={{ textAlign: "left", whiteSpace: "normal" }}>{r.signal && <span className={`signal ${r.signal}`}>{r.signal}</span>} {r.change_summary}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="faint" style={{ fontSize: 13 }}>
                {session.revisions.filter((r) => r.revised).length} revised · {session.revisions.filter((r) => !r.revised).length} unchanged (already fit the strategy)
              </p>
            </>
          )}
        </section>
      )}

      {past.length > 1 && (
        <section className="section glass">
          <h3>Past sessions</h3>
          <ul className="past-sessions">
            {past.filter((p) => p.session_id !== session?.session_id).map((p) => (
              <li key={p.session_id}>
                <button type="button" className="btn small" onClick={() => open(p.session_id)}>#{p.session_id}</button>
                <span>{p.mode === "research" ? "🔎 " : "🧭 "}{p.memo_title || p.strategy || "(nothing asked yet)"}</span>
                <span className="faint">{timeAgo(p.started_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
