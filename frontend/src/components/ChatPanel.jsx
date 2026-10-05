import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import Markdown from "./Markdown.jsx";

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
      return { role: "system", text: "Meeting started. Agents are debating; open the town hall (🗣️) for the minutes in 1-3 minutes." };
    case "deepdive":
    case "update":
      return { role: "system", text: res.status === "started" ? `${res.command === "deepdive" ? "Deep dive" : "Thesis update"} started for ${res.ticker}.` : `${res.ticker}: ${res.status}.` };
    case "help":
      return { role: "system", text: res.message };
    default:
      return { role: "agent", md: "```\n" + JSON.stringify(res, null, 2) + "\n```" };
  }
}

/**
 * Conversation with the agent. Plain text is asked about the current ticker; slash commands work too.
 * `dock` = phone layout: a sticky input bar that opens into a sheet riding above the keyboard.
 */
export default function ChatPanel({ agentId, agentName, ticker, color, dock = false, onCoverageChange }) {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(!dock);
  const log = useRef();

  useEffect(() => {
    let live = true;
    setMessages([]);
    if (!ticker) return undefined;
    api.chatHistory(agentId, ticker)
      .then((rows) => live && setMessages(rows.flatMap((r) => [{ role: "user", text: r.question }, { role: "agent", md: r.answer }])))
      .catch(() => {});
    return () => { live = false; };
  }, [agentId, ticker]);

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
      const res = await api.command(value, ticker);
      setMessages((m) => [...m, describe(res)]);
      if (["add", "remove", "reassign"].includes(res.command)) onCoverageChange?.();
    } catch (e) {
      setMessages((m) => [...m, { role: "error", text: e.message }]);
    } finally {
      setBusy(false);
    }
  };

  const suggestions = ticker
    ? [`/briefing ${ticker}`, "What's your bull case?", "What if earnings miss 20%?", "Is this a good entry point?", `/coverage ${agentName.replace(/ (Analyst|Strategist)$/, "")}`]
    : ["/help"];

  return (
    <div className={`chat glass ${dock ? (open ? "open" : "collapsed") : ""}`} style={{ borderTop: `2px solid ${color}` }}>
      <div className="chat-head">
        <span className="dot" style={{ background: color }} aria-hidden="true" />
        <span className="title">Chat with {agentName}{ticker ? ` · ${ticker}` : ""}</span>
        {dock && <button className="btn icon small" style={{ marginLeft: "auto" }} onClick={() => setOpen(false)} aria-label="Minimize chat">▾</button>}
      </div>
      <div className="chat-log" ref={log} aria-live="polite">
        {messages.length === 0 && !busy && (
          <div className="msg system">Ask about {ticker || "this coverage"}, or type /help for commands.</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>{m.md ? <Markdown>{m.md}</Markdown> : m.text}</div>
        ))}
        {busy && <div className="msg agent typing" aria-label="Agent is typing"><span /><span /><span /></div>}
      </div>
      <div className="chat-suggest">
        {suggestions.map((s) => <button key={s} className="btn small" onClick={() => send(s)} disabled={busy}>{s}</button>)}
      </div>
      <form className="chat-form" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input
          className="input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onFocus={() => dock && setOpen(true)}
          placeholder={ticker ? `Ask about ${ticker} or type /help` : "Type /help"}
          aria-label="Message the agent"
          enterKeyHint="send"
          autoComplete="off"
        />
        <button className="btn primary" type="submit" disabled={busy || !text.trim()} aria-label="Send">➤</button>
      </form>
    </div>
  );
}
