// API client for the analyst pod backend.
const RAW_BASE = import.meta.env.REACT_APP_API_URL || import.meta.env.VITE_API_URL || "http://localhost:8000";
export const API_BASE = RAW_BASE.replace(/\/+$/, "");

const KEY_STORAGE = "analyst-town:access-key";

export function getAccessKey() {
  try {
    return localStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setAccessKey(value) {
  try {
    if (value) localStorage.setItem(KEY_STORAGE, value);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable (private mode): key lives for this session only */
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = "GET", body, signal } = {}) {
  const headers = { Accept: "application/json" };
  const key = getAccessKey();
  if (key) headers["X-API-Key"] = key;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, `Can't reach the backend at ${API_BASE}. Is it running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401) detail = "This action needs your access key. Add it under Settings (gear icon).";
    throw new ApiError(res.status, detail);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res.text();
}

/** Binary-capable POST (voice): returns the raw Response so callers can read a Blob. */
async function rawPost(path, { body, contentType, json } = {}) {
  const headers = { "Content-Type": json ? "application/json" : contentType };
  const key = getAccessKey();
  if (key) headers["X-API-Key"] = key;
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { method: "POST", headers, body: json ? JSON.stringify(json) : body });
  } catch {
    throw new ApiError(0, `Can't reach the backend at ${API_BASE}. Is it running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* non-JSON error body */ }
    if (res.status === 401) detail = "Voice needs your access key. Add it under Settings (gear icon).";
    throw new ApiError(res.status, detail);
  }
  return res;
}

const enc = encodeURIComponent;
const t = (agentId, ticker) => `/agents/${enc(agentId)}/ticker/${enc(ticker)}`;

export const api = {
  status: () => request("/status"),
  agents: () => request("/agents"),
  dashboard: () => request("/dashboard"),
  agentTickers: (agentId) => request(`/agents/${enc(agentId)}/tickers`),
  briefing: (agentId, ticker) => request(`${t(agentId, ticker)}/briefing`),
  memo: (agentId, ticker) => request(`${t(agentId, ticker)}/memo`),
  model: (agentId, ticker) => request(`${t(agentId, ticker)}/model`),
  price: (agentId, ticker) => request(`${t(agentId, ticker)}/price`),
  chart: (agentId, ticker, days = 30) => request(`${t(agentId, ticker)}/chart?days=${days}`),
  chatHistory: (agentId, ticker) => request(`${t(agentId, ticker)}/chat`),
  ask: (agentId, ticker, question) => request(`${t(agentId, ticker)}/ask`, { method: "POST", body: { question } }),
  command: (text, ticker) => request("/command", { method: "POST", body: { text, ticker } }),
  meeting: () => request("/meeting", { method: "POST" }),
  latestMeeting: () => request("/meetings/latest"),
  meetingScript: () => request("/meetings/latest/script"),
  voiceStatus: () => request("/voice/status"),
  /** MP3 Blob of `text` spoken in the agent's voice (agent = analyst key or "chair"). */
  speak: async (text, agent) => (await rawPost("/voice/tts", { json: { text, agent } })).blob(),
  /** Speech-to-text (ElevenLabs Scribe) of a recorded Blob: { text }. */
  transcribe: async (blob) => (await rawPost("/voice/stt", { body: blob, contentType: blob.type || "audio/webm" })).json(),
  coverage: (agentName) => request(`/coverage/${enc(agentName)}`),
  downloadUrl: (agentId, ticker, kind) => `${API_BASE}${t(agentId, ticker)}/download/${kind}`,
};
