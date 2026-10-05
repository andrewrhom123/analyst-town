// Voice layer: backend capabilities, user preferences, one-voice-at-a-time playback, and office ambience.
import { useEffect, useSyncExternalStore } from "react";
import { api } from "../api/client.js";

/* Tiny external store helper. */
function store(initial) {
  let state = initial;
  const listeners = new Set();
  return {
    get: () => state,
    set: (patch) => { state = { ...state, ...patch }; listeners.forEach((fn) => fn()); },
    subscribe: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
  };
}
const useStore = (s) => useSyncExternalStore(s.subscribe, s.get);

/* --- Backend capabilities: { tts, stt, profiles } (fetched once) --------------------------------- */
const caps = store({ tts: false, stt: false, profiles: {}, loaded: false });
let capsRequested = false;

export function useVoiceStatus() {
  useEffect(() => {
    if (capsRequested) return;
    capsRequested = true;
    api.voiceStatus()
      .then((d) => caps.set({ ...d, loaded: true }))
      .catch(() => { caps.set({ loaded: true }); setTimeout(() => { capsRequested = false; }, 30000); });
  }, []);
  return useStore(caps);
}

/* --- Preferences (per browser) ------------------------------------------------------------------ */
const PREFS_KEY = "analyst-town:voice";
function readPrefs() {
  try { return { autoSpeak: false, ambient: false, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") }; } catch { return { autoSpeak: false, ambient: false }; }
}
const prefs = store(readPrefs());

export function useVoicePrefs() {
  return [useStore(prefs), (patch) => {
    prefs.set(patch);
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs.get())); } catch { /* storage unavailable */ }
  }];
}

/* --- Playback: a single <audio>, so agents never talk over each other ------------------------------ */
const player = store({ id: null, agent: null, status: "idle", error: null }); // status: idle | loading | playing
const clips = new Map(); // `${agent}|${text}` -> object URL (in-memory; the server also caches)
const MAX_CLIPS = 60;
let audio = null;
let token = 0;
let finish = null;

async function clipUrl(text, agent) {
  const key = `${agent}|${text}`;
  if (clips.has(key)) return clips.get(key);
  const url = URL.createObjectURL(await api.speak(text, agent));
  clips.set(key, url);
  if (clips.size > MAX_CLIPS) {
    const [oldKey, oldUrl] = clips.entries().next().value;
    URL.revokeObjectURL(oldUrl);
    clips.delete(oldKey);
  }
  return url;
}

/** Warm the cache for the next line of a script without playing it. */
export const prefetch = (text, agent) => clipUrl(text, agent).catch(() => {});

/** Stop whatever is playing (or loading). */
export function stopSpeaking() {
  token += 1;
  if (audio) { audio.pause(); audio.removeAttribute("src"); }
  finish?.(false);
  finish = null;
  player.set({ id: null, agent: null, status: "idle" });
}

/**
 * Speak `text` in `agent`'s voice. Interrupts anything already playing. Resolves true when the clip played
 * to the end, false if it was stopped; rejects on errors (no key, over the daily limit...).
 */
export async function speak(text, agent, id = `${agent}|${text}`) {
  stopSpeaking();
  const mine = ++token;
  player.set({ id, agent, status: "loading", error: null });
  try {
    const url = await clipUrl(text, agent);
    if (mine !== token) return false;
    audio = audio || new Audio();
    audio.src = url;
    player.set({ status: "playing" });
    return await new Promise((resolve, reject) => {
      finish = resolve;
      audio.onended = () => { finish = null; resolve(true); };
      audio.onerror = () => { finish = null; reject(new Error("Couldn't play the audio clip")); };
      audio.play().catch((e) => { finish = null; reject(e.name === "NotAllowedError" ? new Error("Tap play to allow audio in this browser") : e); });
    });
  } catch (e) {
    if (mine === token) player.set({ error: e.message });
    throw e;
  } finally {
    if (mine === token) player.set({ id: null, agent: null, status: "idle" });
  }
}

export const usePlayer = () => useStore(player);

/* --- Office ambience: soft keyboard clicks synthesized with Web Audio (no assets) -------------------- */
let ctx = null;
let ambienceTimer = null;

function click(ac, gain) {
  const len = Math.floor(ac.sampleRate * 0.025);
  const buf = ac.createBuffer(1, len, ac.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < len; i += 1) data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 6);
  const src = ac.createBufferSource();
  src.buffer = buf;
  const filter = ac.createBiquadFilter();
  filter.type = "bandpass";
  filter.frequency.value = 1800 + Math.random() * 1600;
  const g = ac.createGain();
  g.gain.value = gain;
  src.connect(filter).connect(g).connect(ac.destination);
  src.start();
}

/** Start/stop typing ambience. `busy` (agent working) types faster. Quiet while an agent is speaking. */
export function setAmbience(on, busy = false) {
  clearTimeout(ambienceTimer);
  if (!on) return;
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume();
  } catch {
    return;
  }
  const tick = () => {
    const talking = player.get().status === "playing";
    if (!talking && document.visibilityState === "visible") {
      const burst = 2 + Math.floor(Math.random() * (busy ? 7 : 4));
      for (let i = 0; i < burst; i += 1) setTimeout(() => click(ctx, 0.05 + Math.random() * 0.04), i * (70 + Math.random() * 60));
    }
    ambienceTimer = setTimeout(tick, (busy ? 500 : 1200) + Math.random() * (busy ? 900 : 2600));
  };
  tick();
}
