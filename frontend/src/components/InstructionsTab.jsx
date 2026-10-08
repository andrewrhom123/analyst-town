const COMMANDS = [
  ["/briefing TICKER", "Quick summary of the agent's current thinking (instant, free)"],
  ["/ask TICKER: question", "The covering agent responds, e.g. /ask TTD: What if earnings miss 20%?"],
  ["/meeting", "Trigger agent collaboration: they challenge and update each other's theses"],
  ["/add TICKER to AGENT", "Add coverage, e.g. /add MSTR to Fintech"],
  ["/remove TICKER from AGENT", "Stop coverage, e.g. /remove MARA from Fintech (files are archived)"],
  ["/reassign TICKER from AGENT to AGENT", "Move coverage, e.g. /reassign AMZN from AI to Internet Platforms"],
  ["/coverage AGENT", "See all tickers an agent covers, e.g. /coverage Fintech"],
  ["/deepdive TICKER", "Full research refresh: SEC filings, model, memo (~$1)"],
];

export default function InstructionsTab({ onClose }) {
  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Instructions" onClick={onClose}>
      <div className="modal glass" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>How Analyst Town works</h2>
          <button className="btn icon" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <h3>Overview</h3>
        <p className="muted">
          4 AI agents (Macro, Fintech, Internet Platforms, AI) cover 27 fintech and tech names. Each one keeps a live
          trading thesis, a financial model and a research memo per ticker, and you can talk to them directly.
        </p>

        <h3>How to navigate</h3>
        <ul className="list muted">
          <li><b>Laptop:</b> the town sits in Central Park. Click a house to enter that agent's office; the town hall or the glass tower opens the boardroom.</li>
          <li><b>Phone:</b> swipe the cards and tap one to enter the office.</li>
          <li>Inside an office, click a screen (or a ticker tab) to switch tickers. On a phone, swipe left/right on the content.</li>
          <li>Use the chat to talk to the agent. Plain text goes to the ticker you're looking at.</li>
        </ul>

        <h3>Voice</h3>
        <ul className="list muted">
          <li><b>🎙 Mic</b> (next to the chat box): tap and talk. It sends when you stop talking; your words are transcribed by ElevenLabs.</li>
          <li><b>▶</b> on any reply, or <b>Hear the pitch</b>: the analyst reads it aloud in their own voice (four distinct ElevenLabs voices, one per desk).</li>
          <li><b>🔊 Voice replies</b>: every answer is read aloud. <b>🎙 Hands-free</b>: talk, hear the answer, and the mic reopens so you can keep going.</li>
          <li><b>Boardroom</b> (town hall or the tower on the skyline): replay the latest meeting with each analyst speaking in turn, or ask the pod a question out loud.</li>
          <li><b>Daily town hall</b> (boardroom, 4:30pm ET): rundowns in order Macro, AI, Internet Platforms, Fintech; pitches with charts, data, catalysts and news; debate; a research memo. Listen live or replay.</li>
          <li><b>Ad-hoc town hall</b> (boardroom): a <b>strategy session</b> sets the research charter every agent follows; a <b>research conversation</b> takes broad questions. Also /strategy and /research in chat.</li>
          <li><b>Office</b>: the chat in each office is a one-on-one about anything (not just the ticker on screen); transcripts and key insights are saved. The boardroom <b>Archive</b> tab has every memo, conversation and charter.</li>
          <li><b>All-hands strategy</b> (older name for the strategy session): Macro briefs the room on rates, the Fed, sentiment and beta; you set the strategy by voice or text; each analyst aligns or respectfully questions it with data; you answer; then the pod signs a strategy memo and updates every thesis to match. Every session is saved.</li>
          <li><b>🔈/🔊</b> (header): office sounds (keyboards clacking while agents work).</li>
          <li>Voice clips are cached, so replays are free; new speech is capped per day on the backend.</li>
        </ul>

        <h3>Commands</h3>
        <div className="table-wrap">
          <table className="data">
            <tbody>
              {COMMANDS.map(([cmd, what]) => (
                <tr key={cmd}>
                  <td className="mono" style={{ color: "var(--cyan-ink)" }}>{cmd}</td>
                  <td style={{ textAlign: "left", whiteSpace: "normal" }}>{what}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3>What you can do</h3>
        <ul className="list muted">
          <li>View elevator pitches, conviction and entry/exit levels</li>
          <li>Read full research memos and see financial models</li>
          <li>Download research (PDF memo, CSV/Excel model, JSON)</li>
          <li>Talk to agents and track prices and market moves</li>
        </ul>

        <h3>How the agents think</h3>
        <ul className="list muted">
          <li>Prices are tracked every 30 minutes in market hours.</li>
          <li>A move of more than 5% since the last thesis, or a hit on an entry/target/stop level, triggers a thesis update.</li>
          <li>They debate each other daily at 4:30pm ET and keep the lessons.</li>
          <li>New filings or earnings trigger a full deep dive. Conclusions stay current, not static reports.</li>
          <li>Spending is capped at $5/day; the header shows today's spend.</li>
        </ul>

        <h3>Buttons</h3>
        <ul className="list muted">
          <li><b>Pitch / Memo / Model / Chart</b>: switch views for the selected ticker</li>
          <li><b>Chat</b>: talk to the agent</li>
          <li><b>Download</b>: PDF memo, CSV and Excel model, JSON research</li>
          <li><b>Model versions</b> (Model tab): download the latest model, edit the blue cells in Excel, upload it. Your version becomes the one the agent uses (and re-anchors its levels on); every version stays downloadable.</li>
          <li><b>Price chart</b>: 30-day trend with entry zone, target and stop lines (tap to expand, pinch to zoom)</li>
          <li><b>🗣️</b> (header): start a meeting or read the latest minutes · <b>🔈</b>: office sounds · <b>⚙︎</b>: add your access key</li>
        </ul>
      </div>
    </div>
  );
}
