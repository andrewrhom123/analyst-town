import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "./api/client.js";

/** True while the media query matches (e.g. laptop layout at >= 1200px). */
export function useMediaQuery(query) {
  const get = () => (typeof window !== "undefined" ? window.matchMedia(query).matches : false);
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    mql.addEventListener("change", onChange);
    onChange();
    return () => mql.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

export const LAPTOP_QUERY = "(min-width: 1200px)";

/** Fetch now and every `intervalMs` (default 30s, the spec's polling cadence). Pauses while the tab is hidden. */
export function usePolling(fetcher, deps = [], intervalMs = 30000) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  const load = useCallback(async () => {
    try {
      const data = await fetchRef.current();
      setState({ data, error: null, loading: false });
    } catch (error) {
      setState((s) => ({ data: s.data, error, loading: false }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    setState((s) => ({ ...s, loading: true }));
    load();
    if (!intervalMs) return undefined;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, intervalMs);
    return () => clearInterval(id);
  }, [load, intervalMs]);

  return { ...state, reload: load };
}

/** Keeps --kb-offset equal to the on-screen keyboard height (iOS Safari), so the chat dock rides above it. */
export function useKeyboardOffset() {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return undefined;
    const update = () => {
      const offset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      document.documentElement.style.setProperty("--kb-offset", `${Math.round(offset)}px`);
    };
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    update();
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
      document.documentElement.style.setProperty("--kb-offset", "0px");
    };
  }, []);
}

/** Horizontal swipe detection for touch (switching tickers). */
export function useSwipe(onLeft, onRight, threshold = 60) {
  const start = useRef(null);
  return {
    onTouchStart: (e) => {
      const t = e.touches[0];
      start.current = { x: t.clientX, y: t.clientY };
    },
    onTouchEnd: (e) => {
      if (!start.current) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - start.current.x;
      const dy = t.clientY - start.current.y;
      start.current = null;
      if (Math.abs(dx) > threshold && Math.abs(dx) > Math.abs(dy) * 1.5) (dx < 0 ? onLeft : onRight)?.();
    },
  };
}

/* Dashboard feed (news, themes, earnings): one shared fetch for every panel, refreshed every 5 minutes. */
const DASH_TTL = 5 * 60 * 1000;
let dash = { data: null, error: null, loading: false, fetchedAt: 0 };
const dashListeners = new Set();
const setDash = (next) => { dash = { ...dash, ...next }; dashListeners.forEach((fn) => fn()); };

function loadDashboard() {
  if (dash.loading || Date.now() - dash.fetchedAt < DASH_TTL) return;
  setDash({ loading: true });
  api.dashboard()
    .then((data) => setDash({ data, error: null, loading: false, fetchedAt: Date.now() }))
    // keep the last good copy; retry on the next subscriber or tick after a short backoff
    .catch((error) => setDash({ error, loading: false, fetchedAt: Date.now() - DASH_TTL + 30000 }));
}

export function useDashboard() {
  const state = useSyncExternalStore(
    (fn) => { dashListeners.add(fn); return () => dashListeners.delete(fn); },
    () => dash,
  );
  useEffect(() => {
    loadDashboard();
    const id = setInterval(() => document.visibilityState === "visible" && loadDashboard(), 60000);
    return () => clearInterval(id);
  }, []);
  return state;
}
