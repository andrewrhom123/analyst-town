import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { speak, stopSpeaking, useVoicePrefs, useVoiceStatus } from "../voice/voice.js";
import AudioPlayer from "./AudioPlayer.jsx";
import Markdown from "./Markdown.jsx";
import VoiceInput from "./VoiceInput.jsx";

function describe(res) {
  switch (res.command) {
    case "ask":
      return { role: "agent", md: res.answer };
    case "briefing":
      return { role: "agent", md: res.briefing_md };
    case "coverage":
      return { role: "agent", md: res.agents.map((a) => `**${a.name}**: ${a.tickers.map((t) => t.symbol).join(", ") || "no tickers"}`).join("\n\n") };
    case "add":
      return { role: "system", text: `Added ${res.symbol} (${res.name}, ${res.type}) to ${res.agent}. Files created, tracking started, initial deep dive queued.` };
    case "remove":
      return { role: "system", text: `Removed ${res.symbol} from ${res.agent}. Files archived.` };
    case "reassign":
      return { role: "system", text: `Moved ${res.symbol} from ${res.from} to ${res.to}.` };
    case "meeting":
      return { role: "system", text: "Research town hall started: rundowns, pitches and debate. Open the boardroom to listen live (3-7 minutes)." };
    case "strategy":
    case "research":
      return { role: "system", text: `${res.command === "strategy" ? "Strategy session" : "Research conversation"} #${res.session_id} is open in the boardroom (Ad-hoc town hall tab).` };
    case "deepdive":
    case "update":
      return { role: "system", text: res.status === "started" ? `${res.command === "deepdive" ? "Deep dive" : "Thesis update"} started for ${res.ticker}.` : `${res.ticker}: ${res.status}.` };
    case "help":
      return { role: "system", text: res.message };
    default:
      return { role: "agent", md: "```\n" + JSON.stringify(res, null, 2) + "\n```" };
  }
}

const fromConversation = (c) => (c?.messages || []).map((m, i) =>
  m.role === "user" ? { role: "user", text: m.text } : { role: "agent", md: m.text, id: `office-${c.conversation_id}-${i}` });

/**
 * One-on-one office conversation with the analyst: plain text is a general conversation about anything (the
 * ticker on screen is context, not a constraint), stored with key insights. Slash commands still work.
 * `dock` = phone layout: a sticky input bar that opens into a sheet riding above the keyboard.
 */
export default function ChatPanel({ agentId, agentName, ticker, color, dock = false, onCoverageChange }) {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(!dock);
  const [handsFree, setHandsFree] = useState(false);
  const [voiceNote, setVoiceNote] = useState(null);
  const [conversation, setConversation] = useState(null);
  const log = useRef();
  const mic = useRef();
  const voice = useVoiceStatus();
  const [prefs, setPrefs] = useVoicePrefs();
  const handsFreeRef = useRef(false);
  handsFreeRef.current = handsFree;

  // Leaving the office (or switching ticker) ends the spoken conversation.
  useEffect(() => () => stopSpeaking(), [agentId, ticker]);

  /** Read a reply aloud when voice replies or hands-free are on; hands-free then reopens the mic. */
  const voiceReply = async (reply) => {
    const talk = handsFreeRef.current || prefs.autoSpeak;
    let finished = true;
    if (talk && voice.tts && reply.role === "agent" && reply.md) {
      try { finished = await speak(reply.md, agentId, reply.id); } catch (e) { setVoiceNote(e.message); finished = false; }
    }
    if (handsFreeRef.current && finished) mic.current?.start();
  };

  const toggleHandsFree = () => {
    const next = !handsFree;
    setHandsFree(next);
    setVoiceNote(null);
    if (next) { setOpen(true); mic.current?.start(); } else { stopSpeaking(); mic.current?.stop(); }
  };

  // Pick up the latest office conversation with this analyst (it spans tickers).
  useEffect(() => {
    let live = true;
    setMessages([]);
    setConversation(null);
    api.officeLatest(agentId)
      .then((c) => { if (live) { setConversation(c); setMessages(fromConversation(c)); } })
      .catch(() => {});
    return () => { live = false; };
  }, [agentId]);

  const newConversation = () => {
    stopSpeaking();
    setConversation(null);
    setMessages([]);
  };

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy, open]);

  const send = async (raw) => {
    const value = (raw ?? text).trim();
    if (!value || busy) return;
    setText("");
    setOpen(true);
    setMessages((m) => [...m, { role: "user", text: value }]);
    setBusy(true);
    try {
      let reply;
      if (value.startsWith("/")) {
        const res = await api.command(value, ticker);
        reply = { ...describe(res), id: `reply-${Date.now()}` };
        if (["add", "remove", "reassign"].includes(res.command)) onCoverageChange?.();
      } else {
        const c = await api.officeSay(agentId, value, { conversationId: conversation?.conversation_id, ticker, fresh: !conversation });
        setConversation(c);
        reply = fromConversation(c).at(-1);
      }
      setMessages((m) => [...m, reply]);
      setBusy(false);
      voiceReply(reply);
    } catch (e) {
      setMessages((m) => [...m, { role: "error", text: e.message }]);
      setBusy(false);
      if (handsFreeRef.current) setHandsFree(false);
    }
  };

  const suggestions = [
    "What's the most interesting stock you're covering right now?",
    "Where do macro headwinds hit your coverage?",
    "What trade would you do with unlimited capital right now?",
    ...(ticker ? [`Walk me through your thesis on ${ticker}`, `/briefing ${ticker}`] : []),
    "Where is the market most wrong?",
  ];

  return (
    <div className={`chat glass ${dock ? (open ? "open" : "collapsed") : ""}`} style={{ borderTop: `2px solid ${color}` }}>
      <div className="chat-head">
        <span className="dot" style={{ background: color }} aria-hidden="true" />
        <span className="title">
          Office · {agentName}
          {conversation?.title && <span className="faint" style={{ fontWeight: 400 }}> · {conversation.title}</span>}
        </span>
        {messages.length > 0 && <button type="button" className="btn small" onClick={newConversation} title="Start a fresh conversation">New</button>}
        {voice.tts && (
          <div className="voice-toggles">
            <button type="button" className="btn small" aria-pressed={!!prefs.autoSpeak} onClick={() => setPrefs({ autoSpeak: !prefs.autoSpeak })}
              title="Read every reply aloud in the agent's voice">🔊 Voice replies</button>
            {voice.stt && (
              <button type="button" className={`btn small ${handsFree ? "live" : ""}`} aria-pressed={handsFree} onClick={toggleHandsFree}
                title="Talk, hear the answer, keep talking. The mic reopens after each reply.">
                {handsFree ? "● Hands-free on" : "🎙 Hands-free"}
              </button>
            )}
          </div>
        )}
        {dock && <button className="btn icon small" style={{ marginLeft: "auto" }} onClick={() => setOpen(false)} aria-label="Minimize chat">▾</button>}
      </div>
      <div className="chat-log" ref={log} aria-live="polite">
        {messages.length === 0 && !busy && (
          <div className="msg system">Talk about anything: your coverage, a name, the market. Slash commands work too (/help).</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.md ? <Markdown>{m.md}</Markdown> : m.text}
            {m.role === "agent" && m.md && <AudioPlayer text={m.md} agent={agentId} color={color} className="msg-audio" id={m.id} />}
          </div>
        ))}
        {busy && <div className="msg agent typing" aria-label="Agent is typing"><span /><span /><span /></div>}
      </div>
      {conversation?.insights?.length > 0 && (
        <details className="insights">
          <summary>Key insights ({conversation.insights.length}){conversation.trade_ideas?.length ? ` · trade ideas (${conversation.trade_ideas.length})` : ""}</summary>
          <ul>{conversation.insights.map((x) => <li key={x}>{x}</li>)}</ul>
          {conversation.trade_ideas?.length > 0 && (
            <ul className="ideas">{conversation.trade_ideas.map((t, i) => (
              <li key={i}><b>{[t.long && `long ${t.long}`, t.short && `short ${t.short}`].filter(Boolean).join(" / ")}</b>: {t.rationale}</li>
            ))}</ul>
          )}
        </details>
      )}
      {voiceNote && <div className="voice-note" role="alert">{voiceNote} <button className="btn small" onClick={() => setVoiceNote(null)}>OK</button></div>}
      <div className="chat-suggest">
        {suggestions.map((s) => <button key={s} className="btn small" onClick={() => send(s)} disabled={busy}>{s}</button>)}
      </div>
      <form className="chat-form" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input
          className="input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onFocus={() => dock && setOpen(true)}
          placeholder={`Talk to ${agentName.replace(/ (Analyst|Strategist)$/, "")} about anything`}
          aria-label="Message the agent"
          enterKeyHint="send"
          autoComplete="off"
        />
        <VoiceInput
          ref={mic}
          disabled={busy}
          onTranscript={(said) => send(said)}
          onError={(msg) => { setVoiceNote(msg); setHandsFree(false); }}
          onCancel={() => setHandsFree(false)}
        />
        <button className="btn primary" type="submit" disabled={busy || !text.trim()} aria-label="Send">➤</button>
      </form>
    </div>
  );
}
