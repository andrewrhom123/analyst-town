import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import TownView from "../components/TownView.jsx";
import { usePolling } from "../hooks.js";

export default function Home() {
  const navigate = useNavigate();
  const { data: agents, error, loading } = usePolling(() => api.agents(), [], 30000);

  if (!agents) {
    return (
      <div className="empty" role="status">
        {error ? (
          <>
            <p className="down">{error.message}</p>
            <p className="faint">Start it with <span className="mono">uvicorn main:app</span>, or set REACT_APP_API_URL.</p>
          </>
        ) : loading ? "Loading the town…" : "No agents yet."}
      </div>
    );
  }
  const openTicker = (symbol) => {
    const owner = agents.find((a) => a.tickers.some((t) => t.symbol === symbol));
    if (owner) navigate(`/agent/${owner.id}?t=${encodeURIComponent(symbol)}`);
  };
  return <TownView agents={agents} onEnterAgent={(a) => navigate(`/agent/${a.id}`)} onOpenHall={() => navigate("/boardroom")} onTicker={openTicker} />;
}
