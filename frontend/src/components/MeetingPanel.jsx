import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { timeAgo } from "../format.js";
import AudioPlayer from "./AudioPlayer.jsx";
import Markdown from "./Markdown.jsx";

/** Start a meeting and/or read the latest minutes. Polls while a meeting is running. */
export default function MeetingPanel({ onClose }) {
  const [meeting, setMeeting] = useState(null);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);

  const load = async () => {
    try {
      setMeeting(await api.latestMeeting());
    } catch (e) {
      if (e.status !== 404) setError(e.message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (meeting?.status !== "running") return undefined;
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [meeting?.status]);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      await api.meeting();
      setMeeting({ status: "running", started_at: new Date().toISOString(), minutes_md: "" });
      setTimeout(load, 3000);
    } catch (e) {
      setError(e.message);
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Agent meeting" onClick={onClose}>
      <div className="modal glass" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Town hall meeting</h2>
          <button className="btn icon" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="muted">
          Agents present their theses, challenge each other, and update their calls. Runs daily at 4:30pm ET (~$1).
        </p>
        <button className="btn primary" onClick={start} disabled={starting || meeting?.status === "running"}>
          {meeting?.status === "running" ? "Meeting in progress…" : starting ? "Starting…" : "Start a meeting now"}
        </button>
        <Link className="btn" to="/boardroom" onClick={onClose} style={{ marginLeft: 8 }}>Enter the boardroom</Link>
        {error && <p className="down" role="alert">{error}</p>}
        {meeting && meeting.status === "running" && (
          <p className="muted" aria-live="polite"><span className="typing"><span /><span /><span /></span> Agents are debating. This takes 1-3 minutes.</p>
        )}
        {meeting?.status === "failed" && <p className="down">Last meeting failed: {meeting.error}</p>}
        {meeting?.minutes_md ? (
          <div style={{ marginTop: 16 }}>
            <p className="faint" style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 8 }}>
              Latest minutes · {timeAgo(meeting.finished_at || meeting.started_at)}
              <AudioPlayer text={meeting.minutes_md} agent="chair" color="#a855f7" label="Read aloud" />
            </p>
            <Markdown>{meeting.minutes_md}</Markdown>
          </div>
        ) : (
          !meeting && <p className="faint">No meetings yet.</p>
        )}
      </div>
    </div>
  );
}
