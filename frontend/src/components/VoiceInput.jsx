import { useEffect, useImperativeHandle, useRef, useState } from "react";
import { api } from "../api/client.js";
import { stopSpeaking, useVoiceStatus } from "../voice/voice.js";

const MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
const SPEECH_RMS = 0.035; // level that counts as talking
const SILENCE_MS = 1400; // stop this long after you stop talking
const NO_SPEECH_MS = 8000; // give up if you never start
const MAX_MS = 45000;

const pickMime = () => (typeof MediaRecorder === "undefined" ? null : MIME_TYPES.find((t) => MediaRecorder.isTypeSupported(t)) || "");

/**
 * Mic button: tap to talk, tap again (or just stop talking) to send. The recording is transcribed by
 * ElevenLabs Scribe on the backend and handed to onTranscript(text). Parents can start it with ref.current.start()
 * (hands-free conversation). onCancel fires when a recording ends without sending (no speech, or stopped). Hidden when voice is off on the backend (no ELEVENLABS_API_KEY) or the browser can't record.
 */
export default function VoiceInput({ onTranscript, onError, onCancel, onStateChange, disabled = false, ref }) {
  const { stt } = useVoiceStatus();
  const [state, setState] = useState("idle"); // idle | listening | transcribing
  const rec = useRef(null);
  const btn = useRef(null);
  const supported = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && pickMime() !== null;

  const update = (s) => { setState(s); onStateChange?.(s); };

  const cleanup = () => {
    const r = rec.current;
    if (!r) return;
    cancelAnimationFrame(r.raf);
    clearTimeout(r.maxTimer);
    r.stream.getTracks().forEach((t) => t.stop());
    r.audioCtx?.close().catch(() => {});
    rec.current = null;
  };

  const stop = (send = true) => {
    const r = rec.current;
    if (!r) return;
    r.send = send;
    if (r.recorder.state !== "inactive") r.recorder.stop();
  };

  const start = async () => {
    if (rec.current || disabled || state === "transcribing") return;
    stopSpeaking(); // don't record the agent talking
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      onError?.(e.name === "NotAllowedError" ? "Microphone access was blocked. Allow it in the browser to talk." : `Microphone unavailable: ${e.message}`);
      return;
    }
    const mime = pickMime();
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks = [];
    const r = { recorder, stream, send: true, raf: 0, maxTimer: 0, audioCtx: null };
    rec.current = r;
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      const { send } = r;
      cleanup();
      const blob = new Blob(chunks, { type: recorder.mimeType || mime || "audio/webm" });
      if (!send || blob.size < 1200) {
        update("idle");
        return onCancel?.();
      }
      update("transcribing");
      try {
        const { text } = await api.transcribe(blob);
        if (text?.trim()) onTranscript(text.trim());
        else onError?.("Didn't catch that. Try again a little closer to the mic.");
      } catch (e) {
        onError?.(e.message);
      } finally {
        update("idle");
      }
    };

    // Level meter + silence detection
    try {
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      r.audioCtx = audioCtx;
      const buf = new Float32Array(analyser.fftSize);
      const began = performance.now();
      let heard = false;
      let lastLoud = began;
      const loop = () => {
        analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i += 1) sum += buf[i] * buf[i];
        const rms = Math.sqrt(sum / buf.length);
        const now = performance.now();
        btn.current?.style.setProperty("--level", Math.min(1, rms * 8).toFixed(2));
        if (rms > SPEECH_RMS) { heard = true; lastLoud = now; }
        if (heard && now - lastLoud > SILENCE_MS) return stop(true);
        if (!heard && now - began > NO_SPEECH_MS) return stop(false);
        r.raf = requestAnimationFrame(loop);
      };
      r.raf = requestAnimationFrame(loop);
    } catch {
      /* no Web Audio: manual stop only */
    }
    r.maxTimer = setTimeout(() => stop(true), MAX_MS);
    recorder.start(250);
    update("listening");
  };

  useImperativeHandle(ref, () => ({ start, stop: () => stop(false) }));
  useEffect(() => () => { stop(false); cleanup(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!stt || !supported) return null;
  const label = state === "listening" ? "Stop and send" : state === "transcribing" ? "Transcribing…" : "Talk (voice input)";
  return (
    <button
      ref={btn}
      type="button"
      className={`btn icon mic-btn ${state}`}
      onClick={() => (state === "listening" ? stop(true) : start())}
      disabled={disabled || state === "transcribing"}
      aria-label={label}
      aria-pressed={state === "listening"}
      title={label}
    >
      {state === "transcribing" ? <span className="spinner" aria-hidden="true" /> : <MicIcon />}
    </button>
  );
}

function MicIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  );
}
