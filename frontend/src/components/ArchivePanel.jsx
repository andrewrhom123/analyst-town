import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { timeAgo } from "../format.js";
import AudioPlayer from "./AudioPlayer.jsx";
import Markdown from "./Markdown.jsx";

const KIND = { town_hall: "Town hall", research: "Research conversation", strategy: "Research charter" };

/** Everything stored for review after the fact: memos, office conversations (transcript + insights), charters. */
export default function ArchivePanel({ agents }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null); // { type: "memo" | "office", item }
  const names = Object.fromEntries(agents.map((a) => [a.key, a.name]));

  useEffect(() => {
    api.archive().then(setData).catch((e) => setError(e.message));
  }, []);

  const show = async (type, id) => {
    setError(null);
    try {
      setOpen({ type, item: type === "memo" ? await api.memo(id) : await api.officeConversation(id) });
    } catch (e) {
      setError(e.message);
    }
  };

  if (error && !data) return <section className="section glass"><p className="down">{error}</p></section>;
  if (!data) return <section className="section glass"><div className="skeleton" style={{ height: 160 }} /></section>;

  return (
    <div className="strategy">
      {open && (
        <section className="section glass">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <h3 style={{ margin: 0 }}>{open.type === "memo" ? `${KIND[open.item.kind]} · ${open.item.title}` : `Office · ${names[open.item.analyst_key] || open.item.analyst_key}`}</h3>
            <button className="btn small" onClick={() => setOpen(null)}>Close</button>
          </div>
          {open.type === "memo" ? (
            <>
              <p className="faint" style={{ fontSize: 13 }}>{timeAgo(open.item.created_at)} <AudioPlayer text={open.item.memo_md} agent="chair" color="#a855f7" label="Read aloud" /></p>
              <Markdown>{open.item.memo_md}</Markdown>
            </>
          ) : (
            <>
              <p className="faint" style={{ fontSize: 13 }}>{open.item.title} · {timeAgo(open.item.updated_at)}</p>
              {open.item.insights.length > 0 && (
                <><h3>Key insights</h3><ul className="list">{open.item.insights.map((x) => <li key={x}>{x}</li>)}</ul></>
              )}
              {open.item.trade_ideas.length > 0 && (
                <><h3>Trade ideas</h3><ul className="list">{open.item.trade_ideas.map((t, i) => (
                  <li key={i}><b>{[t.long && `long ${t.long}`, t.short && `short ${t.short}`].filter(Boolean).join(" / ")}</b>: {t.rationale}</li>
                ))}</ul></>
              )}
              <h3>Transcript</h3>
              <ol className="strategy-log">
                {open.item.messages.map((m, i) => (
                  <li key={i} className={`turn ${m.role === "user" ? "user" : "agent"}`}>
                    <b>{m.role === "user" ? `You${m.focus ? ` · looking at ${m.focus}` : ""}` : names[open.item.analyst_key]}</b>
                    {m.role === "user" ? <p>{m.text}</p> : <Markdown>{m.text}</Markdown>}
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>
      )}

      <section className="section glass">
        <h3>Memos</h3>
        {data.memos.length === 0 ? <p className="faint">No memos yet. Town halls and sessions write them automatically.</p> : (
          <ul className="past-sessions">
            {data.memos.map((m) => (
              <li key={m.id}>
                <button type="button" className="btn small" onClick={() => show("memo", m.id)}>Open</button>
                <span><span className="chip">{KIND[m.kind]}</span> {m.title}</span>
                <span className="faint">{timeAgo(m.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="section glass">
        <h3>Office conversations</h3>
        {data.office_conversations.length === 0 ? <p className="faint">None yet. Walk into an office and start talking.</p> : (
          <ul className="past-sessions">
            {data.office_conversations.map((c) => (
              <li key={c.conversation_id}>
                <button type="button" className="btn small" onClick={() => show("office", c.conversation_id)}>Open</button>
                <span><b>{(names[c.analyst_key] || c.analyst_key).replace(/ (Analyst|Strategist)$/, "")}</b> · {c.title || "Conversation"} · {c.turns} exchanges, {c.insights} insights</span>
                <span className="faint">{timeAgo(c.updated_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="section glass">
        <h3>Research charters</h3>
        {data.charters.length === 0 ? <p className="faint">No charter yet. Run a strategy session to set one.</p> : (
          <ul className="signoffs">
            {data.charters.map((c) => (
              <li key={c.id} style={{ "--c": c.active ? "var(--lime)" : "var(--panel-border)" }}>
                <b>{c.title}</b> {c.active && <span className="stance aligned">In force</span>} <span className="faint">{timeAgo(c.created_at)}</span>
                <ul className="list">{c.directives.map((d) => <li key={d}>{d}</li>)}</ul>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
