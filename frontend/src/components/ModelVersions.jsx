import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { fmtPrice, timeAgo } from "../format.js";

const fmtVal = (v) => (typeof v === "number" ? (Math.abs(v) >= 100 ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : `${+v.toFixed(2)}`) : String(v));

/**
 * Model versions for a ticker: download any version as Excel, edit the blue input cells, upload. The engine
 * recomputes a new version that the agents use from then on (and re-anchors the thesis levels on it).
 */
export default function ModelVersions({ agentId, ticker, onUploaded }) {
  const [versions, setVersions] = useState(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const file = useRef();

  const load = () => api.modelVersions(agentId, ticker).then(setVersions).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [agentId, ticker]); // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (f) => {
    if (!f) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.uploadModel(agentId, ticker, f, note.trim());
      setResult(res);
      setNote("");
      await load();
      onUploaded?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
      if (file.current) file.current.value = "";
    }
  };

  if (!versions) return null;
  const latest = versions[0];
  return (
    <section className="section glass model-versions">
      <div className="mv-head">
        <h3 style={{ margin: 0 }}>Model versions</h3>
        {latest && (
          <span className="faint" style={{ fontSize: 13 }}>
            Using <b>v{latest.version}</b> · {latest.source === "pm_upload" ? "your edits" : "agent"} · {timeAgo(latest.date)}
          </span>
        )}
      </div>
      <p className="muted" style={{ fontSize: 13, margin: "6px 0 10px" }}>
        One model, passed back and forth. Download the latest, change anything (<b style={{ color: "#0000ff" }}>blue</b> inputs,
        formulas, new rows, segments or tabs), save, and upload it here. Your workbook becomes the model: the agent reads every
        tab, re-anchors its trading levels on it, and writes its own updates (new data, earnings) into that same file. Download
        latest always gives you the current workbook, whoever changed it last. Keep the tab names and the hidden _model_map tab.
      </p>
      <div className="mv-actions">
        <a className="btn" href={api.modelDownloadUrl(ticker)} download>⬇ Download latest (v{latest?.version})</a>
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the agent (optional), e.g. lower WACC: rates falling"
          aria-label="Note for the agent" />
        <input ref={file} type="file" accept=".xlsx" hidden onChange={(e) => upload(e.target.files?.[0])} />
        <button className="btn primary" onClick={() => file.current?.click()} disabled={busy}>{busy ? "Recomputing…" : "⬆ Upload edited model"}</button>
      </div>
      {error && <p className="down" role="alert" style={{ marginBottom: 0 }}>{error}</p>}
      {result && (
        <div className="mv-result" role="status">
          <b>Saved as v{result.version}</b> (from v{result.based_on_version}) · probability-weighted value{" "}
          <b>{fmtPrice(result.probability_weighted_price)}</b>
          {result.thesis_update_queued ? " · thesis update queued to re-anchor the levels" : ""}
          {result.superseded_version ? <div className="faint">Note: built on v{result.based_on_version}; v{result.superseded_version} came after it and its changes aren't in your file.</div> : null}
          <ul className="list">
            {result.changes.map((c) => <li key={c.path}>{c.label}: {fmtVal(c.old)} → <b>{fmtVal(c.new)}</b></li>)}
            {(result.structure || []).map((s) => <li key={s}>{s}</li>)}
          </ul>
          {result.missing_inputs?.length ? <div className="faint">Couldn't find these input cells (kept the prior value): {result.missing_inputs.join(", ")}</div> : null}
        </div>
      )}
      <div className="table-wrap">
      <table className="data mv-table">
        <thead><tr><th>Version</th><th>By</th><th>When</th><th>Prob.-weighted</th><th>DCF</th><th>Changes</th><th /></tr></thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.model_id} className={v.latest ? "latest" : ""}>
              <td className="mono">v{v.version}{v.latest ? " ●" : ""}</td>
              <td><span className={`chip ${v.source === "pm_upload" ? "you" : ""}`}>{v.source === "pm_upload" ? "You" : "Agent"}</span></td>
              <td>{timeAgo(v.date)}</td>
              <td className="mono">{fmtPrice(v.probability_weighted_price)}</td>
              <td className="mono">{fmtPrice(v.dcf_price)}</td>
              <td style={{ textAlign: "left" }}>
                {v.changes.length || (v.source === "pm_upload" && v.structure?.length) ? (
                  <button type="button" className="btn small" onClick={() => setOpen(open === v.model_id ? null : v.model_id)}>
                    {v.changes.length + (v.source === "pm_upload" ? v.structure.length : 0)} edit{v.changes.length + (v.source === "pm_upload" ? v.structure.length : 0) > 1 ? "s" : ""}
                  </button>
                ) : <span className="faint">deep dive</span>}
                {v.workbook && <span className="faint" title="Your workbook (edited by you or written into by the agent)"> · your workbook</span>}
                {v.note && <span className="faint" title={v.note}> · “{v.note.length > 40 ? `${v.note.slice(0, 40)}…` : v.note}”</span>}
                {open === v.model_id && (
                  <ul className="list" style={{ margin: "6px 0 0" }}>
                    {v.changes.map((c) => <li key={c.path}>{c.label}: {fmtVal(c.old)} → {fmtVal(c.new)}</li>)}
                    {v.source === "pm_upload" && v.structure.map((s) => <li key={s}>{s}</li>)}
                  </ul>
                )}
              </td>
              <td><a className="btn small" href={api.modelDownloadUrl(ticker, v.model_id)} download>⬇</a></td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </section>
  );
}
