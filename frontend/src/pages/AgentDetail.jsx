import { useEffect } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import AgentOffice from "../components/AgentOffice.jsx";
import { usePolling } from "../hooks.js";

export default function AgentDetail() {
  const { agentId } = useParams();
  const [params, setParams] = useSearchParams();
  const agents = usePolling(() => api.agents(), [], 30000);
  const agent = agents.data?.find((a) => a.id === agentId);
  const tickers = agent?.tickers || [];
  const selected = params.get("t") && tickers.some((t) => t.symbol === params.get("t")) ? params.get("t") : tickers[0]?.symbol;

  useEffect(() => {
    if (agent) document.title = `${agent.name} · Analyst Town`;
    return () => { document.title = "Analyst Town"; };
  }, [agent]);

  if (agents.error && !agents.data) return <div className="empty"><p className="down">{agents.error.message}</p></div>;
  if (!agents.data) return <div className="empty" role="status">Opening the office…</div>;
  if (!agent) return <div className="empty"><p>No agent “{agentId}”.</p><Link className="btn" to="/">Back to town</Link></div>;

  return (
    <AgentOffice
      agent={agent}
      tickers={tickers}
      selected={selected}
      onSelect={(t) => setParams({ t }, { replace: true })}
      onCoverageChange={agents.reload}
    />
  );
}
