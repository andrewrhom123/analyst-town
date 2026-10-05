export const fmtPrice = (v) =>
  v == null || Number.isNaN(v) ? "n/a" : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const fmtPct = (v, digits = 2) => (v == null || Number.isNaN(v) ? "n/a" : `${v > 0 ? "+" : ""}${Number(v).toFixed(digits)}%`);

export const changeClass = (v) => (v == null ? "faint" : v >= 0 ? "up" : "down");

export const fmtMoneyMm = (v) => {
  if (v == null || Number.isNaN(v)) return "n/a";
  const abs = Math.abs(v);
  const s = abs >= 1000 ? `$${(abs / 1000).toFixed(2)}B` : `$${abs.toFixed(1)}M`;
  return v < 0 ? `-${s}` : s;
};

export function timeAgo(iso) {
  if (!iso) return "never";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

/** 0..1: how fresh an agent's thinking is (1 = updated just now, 0 = a week or more). Drives window glow. */
export function freshness(iso) {
  if (!iso) return 0.08;
  const hours = (Date.now() - new Date(iso).getTime()) / 3.6e6;
  return Math.max(0.08, Math.min(1, 1 - hours / 168));
}

/** The ticker to feature on an agent card: highest conviction with a thesis, else the first. */
export function featuredTicker(agent) {
  const withThesis = agent.tickers.filter((t) => t.conviction_level != null);
  if (!withThesis.length) return agent.tickers[0];
  return withThesis.reduce((a, b) => (b.conviction_level > a.conviction_level ? b : a));
}
