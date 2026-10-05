import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import AudioPlayer from "../components/AudioPlayer.jsx";
import Markdown from "../components/Markdown.jsx";
import VoiceInput from "../components/VoiceInput.jsx";
import { timeAgo } from "../format.js";
import { LAPTOP_QUERY, useMediaQuery, usePolling } from "../hooks.js";
import { prefetch, speak, stopSpeaking, usePlayer, useVoiceStatus } from "../voice/voice.js";

const BoardroomScene = lazy(() => import("../3d/BoardroomScene.jsx"));
const CHAIR = { key: "chair", name: "Chair", color: "#a855f7" };
const ROUNDS = { open: "Opening", challenges: "Round 1 · Challenges", responses: "Round 2 · Responses", close: "Minutes" };

/** Meeting state: latest minutes + spoken script; polls while a meeting is running. */
function useMeeting() {
  const [meeting, setMeeting] = useState(null);
  const [script, setScript] = useState(null);
  const [error, setError] = useState(null);
  const load = async () => {
    try {
      const m = await api.latestMeeting();
      setMeeting(m);
      if (m.status === "done") setScript(await api.meetingScript());
    } catch (e) {
      if (e.status !== 404) setError(e.message);
    }
  };
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (meeting?.status !== "running") return undefined;
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [meeting?.status]);
  const start = async () => {
    setError(null);
    try {
      await api.meeting();
      setMeeting({ status: "running", started_at: new Date().toISOString() });
      setTimeout(load, 3000);
    } catch (e) {
      setError(e.message);
    }
  };
  return { meeting, script, error, start };
}

/** Plays the script line by line in each speaker's voice, prefetching the next clip. */
function usePlayback(lines) {
  const [index, setIndex] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState(null);
  const run = useRef(0);

  const playFrom = async (start) => {
    const mine = ++run.current;
    setPlaying(true);
    setError(null);
    for (let i = start; i < lines.length; i += 1) {
      if (mine !== run.current) return;
      setIndex(i);
      if (lines[i + 1]) prefetch(lines[i + 1].text, lines[i + 1].agent_key);
      try {
        const finished = await speak(lines[i].text, lines[i].agent_key, `boardroom-${i}`);
        if (!finished) { // stopped, or interrupted by another clip (e.g. an Ask the pod answer)
          if (mine === run.current) setPlaying(false);
          return;
        }
      } catch (e) {
        if (mine === run.current) { setError(e.message); setPlaying(false); }
        return;
      }
    }
    if (mine === run.current) { setPlaying(false); setIndex(-1); }
  };
  const stop = () => { run.current += 1; stopSpeaking(); setPlaying(false); };
  useEffect(() => () => { run.current += 1; stopSpeaking(); }, []);
  return { index, playing, error, playFrom, stop, setIndex };
}

function AskThePod({ agents }) {
  const tickers = agents.flatMap((a) => a.tickers.map((t) => ({ symbol: t.symbol, agent: a })));
  const [ticker, setTicker] = useState(tickers[0]?.symbol || "");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState(null);
  const [note, setNote] = useState(null);
  const { tts } = useVoiceStatus();
  const owner = tickers.find((t) => t.symbol === ticker)?.agent;

  const ask = async (q) => {
    const question = (q ?? text).trim();
    if (!question || !ticker || busy) return;
    setBusy(true);
    setNote(null);
    setText("");
    try {
      const res = await api.command(question, ticker);
      const md = res.answer || res.briefing_md || res.message || "";
      setAnswer({ question, md, agent: owner });
      if (tts && md && owner) speak(md, owner.key).catch((e) => setNote(e.message));
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="section glass ask-pod">
      <h3>Ask the pod</h3>
      <p className="faint" style={{ margin: "0 0 8px", fontSize: 13 }}>Pick a ticker; its analyst answers out loud. Tap the mic and just talk.</p>
      <form className="chat-form" style={{ padding: 0, border: 0 }} onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <select className="input" style={{ maxWidth: 110 }} value={ticker} onChange={(e) => setTicker(e.target.value)} aria-label="Ticker">
          {agents.map((a) => (
            <optgroup key={a.id} label={a.name}>{a.tickers.map((t) => <option key={t.symbol} value={t.symbol}>{t.symbol}</option>)}</optgroup>
          ))}
        </select>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder={ticker ? `Ask about ${ticker}` : "Ask a question"} aria-label="Question" />
        <VoiceInput disabled={busy} onTranscript={(said) => ask(said)} onError={setNote} />
        <button className="btn primary" type="submit" disabled={busy || !text.trim()} aria-label="Ask">➤</button>
      </form>
      {busy && <p className="muted"><span className="typing"><span /><span /><span /></span> {owner?.name || "The analyst"} is thinking…</p>}
      {note && <p className="down" role="alert">{note}</p>}
      {answer && (
        <div className="pod-answer" style={{ borderLeftColor: answer.agent?.color }}>
          <div className="faint" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 8 }}>
            You asked: “{answer.question}” <AudioPlayer text={answer.md} agent={answer.agent?.key} color={answer.agent?.color} />
          </div>
          <Markdown>{answer.md}</Markdown>
        </div>
      )}
    </section>
  );
}

/** Boardroom atop the tower on Central Park South: watch and hear the pod meeting, ask the room questions. */
export default function Boardroom() {
  const laptop = useMediaQuery(LAPTOP_QUERY);
  const agents = usePolling(() => api.agents(), [], 60000);
  const { meeting, script, error, start } = useMeeting();
  const lines = script?.lines || [];
  const playback = usePlayback(lines);
  const player = usePlayer();
  const { tts, loaded } = useVoiceStatus();
  const list = useRef();
  const roster = agents.data || [];
  const byKey = Object.fromEntries([...roster.map((a) => [a.key, a]), [CHAIR.key, CHAIR]]);
  const current = lines[playback.index];
  const speaker = player.status === "playing" ? player.agent : null;

  useEffect(() => {
    document.title = "Boardroom · Analyst Town";
    return () => { document.title = "Analyst Town"; };
  }, []);
  useEffect(() => {
    list.current?.querySelector(".line.current")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [playback.index]);

  const controls = (
    <div className="board-controls">
      {playback.playing ? (
        <button className="btn primary" onClick={playback.stop}>■ Stop</button>
      ) : (
        <button className="btn primary" onClick={() => playback.playFrom(Math.max(0, playback.index))} disabled={!lines.length || !tts}>
          ▶ {playback.index > 0 ? "Resume" : "Play the meeting"}
        </button>
      )}
      <button className="btn" onClick={() => playback.playFrom(Math.max(0, playback.index - 1))} disabled={!lines.length || !tts || playback.index <= 0} aria-label="Previous speaker">⏮</button>
      <button className="btn" onClick={() => playback.playFrom(Math.min(lines.length - 1, playback.index + 1))} disabled={!lines.length || !tts} aria-label="Next speaker">⏭</button>
      <button className="btn" onClick={start} disabled={meeting?.status === "running"}>{meeting?.status === "running" ? "Meeting in progress…" : "Start a new meeting"}</button>
    </div>
  );

  const panel = (
    <div className="board-panel">
      <div className="office-title glass">
        <Link to="/" className="btn icon small back" aria-label="Back to town">←</Link>
        <span className="dot" style={{ background: "#d946ef" }} aria-hidden="true" />
        <div>
          <h1>Boardroom</h1>
          <div className="faint" style={{ fontSize: 12 }}>
            {meeting ? `Meeting #${meeting.meeting_id ?? "…"} · ${meeting.status} · ${timeAgo(meeting.finished_at || meeting.started_at)}` : "No meetings yet"}
          </div>
        </div>
      </div>
      <section className="section glass">
        {controls}
        {loaded && !tts && <p className="faint" style={{ fontSize: 13 }}>Voices are off: set <span className="mono">ELEVENLABS_API_KEY</span> on the backend to hear the meeting. The transcript is below.</p>}
        {meeting?.status === "running" && <p className="muted" aria-live="polite"><span className="typing"><span /><span /><span /></span> The pod is debating. This takes 1-3 minutes.</p>}
        {meeting?.status === "failed" && <p className="down">Last meeting failed: {meeting.error}</p>}
        {(error || playback.error) && <p className="down" role="alert">{error || playback.error}</p>}
      </section>
      {lines.length > 0 && (
        <section className="section glass">
          <h3>Transcript</h3>
          <ol className="script" ref={list}>
            {lines.map((l, i) => {
              const who = byKey[l.agent_key] || { name: l.speaker, color: "#94a3b8" };
              const head = i === 0 || lines[i - 1].round !== l.round;
              return (
                <li key={i} className={`line ${i === playback.index ? "current" : ""}`} style={{ "--c": who.color }}>
                  {head && <div className="round">{ROUNDS[l.round] || l.round}</div>}
                  <button type="button" onClick={() => tts && playback.playFrom(i)} disabled={!tts} title={tts ? "Play from here" : undefined}>
                    <b>{l.speaker}</b> {l.text}
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      )}
      {roster.length > 0 && <AskThePod agents={roster} />}
      {meeting?.minutes_md && (
        <section className="section glass">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <h3 style={{ margin: 0 }}>Minutes</h3>
            <AudioPlayer text={meeting.minutes_md} agent="chair" color={CHAIR.color} label="Read aloud" />
          </div>
          <Markdown>{meeting.minutes_md}</Markdown>
        </section>
      )}
    </div>
  );

  const caption = current && (
    <div className="caption glass" aria-live="polite" style={{ borderLeftColor: (byKey[current.agent_key] || CHAIR).color }}>
      <b>{current.speaker}</b>
      <span>{current.text}</span>
    </div>
  );

  if (!laptop) {
    return (
      <div className="boardroom mobile">
        {roster.length > 0 && (
          <div className="speaker-strip" aria-label="Who's speaking">
            {[CHAIR, ...roster].map((a) => (
              <span key={a.key} className={`speaker-chip ${speaker === a.key ? "on" : ""}`} style={{ "--c": a.color }}>{a.name.replace(/ (Analyst|Strategist)$/, "")}</span>
            ))}
          </div>
        )}
        {caption}
        {panel}
      </div>
    );
  }
  return (
    <div className="boardroom">
      <div className="board-stage glass">
        {roster.length > 0 ? (
          <Suspense fallback={<div className="town-loading">Taking the elevator up…</div>}>
            <BoardroomScene agents={roster} speaker={speaker} />
          </Suspense>
        ) : <div className="town-loading">{agents.error ? agents.error.message : "Taking the elevator up…"}</div>}
        {caption}
      </div>
      {panel}
    </div>
  );
}
