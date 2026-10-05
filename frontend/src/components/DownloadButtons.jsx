import { api } from "../api/client.js";

export default function DownloadButtons({ agentId, ticker, hasModel }) {
  const items = [
    ["memo.pdf", "PDF memo"],
    ...(hasModel ? [["model.csv", "CSV model"], ["model.xlsx", "Excel model"]] : []),
    ["research.json", "JSON research"],
  ];
  return (
    <div className="downloads">
      {items.map(([kind, label]) => (
        <a key={kind} className="btn small" href={api.downloadUrl(agentId, ticker, kind)} download>
          ⬇ {label}
        </a>
      ))}
    </div>
  );
}
