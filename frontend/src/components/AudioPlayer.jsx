import { useId, useState } from "react";
import { speak, stopSpeaking, usePlayer, useVoiceStatus } from "../voice/voice.js";

/**
 * Play/stop button that speaks `text` in an agent's voice (ElevenLabs via the backend).
 * Hidden when the backend has no TTS key. `label` adds visible text next to the icon. Pass `id` to share
 * playing state with code that calls speak() with the same id (e.g. replies read aloud automatically).
 */
export default function AudioPlayer({ text, agent, label, color, className = "", id: clipId }) {
  const { tts } = useVoiceStatus();
  const player = usePlayer();
  const autoId = useId();
  const id = clipId || autoId;
  const [error, setError] = useState(null);
  if (!tts || !text) return null;

  const mine = player.id === id;
  const state = mine ? player.status : "idle";
  const toggle = async () => {
    if (mine) return stopSpeaking();
    setError(null);
    try { await speak(text, agent, id); } catch (e) { setError(e.message); }
  };
  const name = state === "idle" ? `Listen${label ? `: ${label}` : ""}` : "Stop";

  return (
    <span className={`audio-player ${state} ${className}`} style={color ? { "--voice": color } : undefined}>
      <button type="button" className={`btn small voice-btn ${label ? "" : "icon-only"}`} onClick={toggle} aria-label={name} title={error || name}>
        <span className="voice-icon" aria-hidden="true">
          {state === "loading" ? <span className="spinner" /> : state === "playing" ? <span className="bars"><i /><i /><i /><i /></span> : "▶"}
        </span>
        {label && <span>{state === "playing" ? "Stop" : label}</span>}
      </button>
      {error && <span className="voice-error" role="alert">{error}</span>}
    </span>
  );
}
