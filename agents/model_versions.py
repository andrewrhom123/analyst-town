"""Financial model versions, and the PM's edit round trip.

Every model is a version (oldest = v1): agent deep dives and rebuilds create one, and so does the PM uploading
an edited workbook. The upload is taken whole (agents.model_workbook): the file itself becomes the model that
"download latest" returns, the input cells (found via the hidden "_model_map" tab, even after rows or tabs were
added) feed the engine, and every tab, plus what the PM changed beyond the inputs, goes to the agents. From then on
the agents work in the PM's workbook: their next deep dive / rebuild writes its updates into that same file
(a new version), thesis updates anchor on it, and the PM downloads the latest one, whoever changed it last.
"""

import copy
import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select

from agents import model_workbook as mw
from agents.coverage_files import refresh_files
from agents.financial_model import ModelInputError, is_quarterly, parse_and_compute, quarter_years
from agents.registry import get_context
from database.db import session_scope
from database.models import FinancialModel, ResearchOutput
from exports.excel_model import build_workbook


logger = logging.getLogger(__name__)


class ModelUploadError(ValueError):
    pass


def _source(fm: FinancialModel) -> str:
    return (fm.assumptions or {}).get("source") or "agent"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tz


def list_versions(symbol: str) -> list[dict]:
    """All model versions for a ticker, newest first."""
    ctx = get_context(symbol)
    if ctx is None:
        return []
    with session_scope() as s:
        rows = s.scalars(select(FinancialModel).where(FinancialModel.ticker_id == ctx.ticker_id).order_by(FinancialModel.date, FinancialModel.id)).all()
        with_wb = mw.ids_with_workbook([fm.id for fm in rows])
        out = []
        for v, fm in enumerate(rows, 1):
            a = fm.assumptions or {}
            o = a.get("outputs") or {}
            wv = a.get("workbook_valuation") or {}  # the PM workbook's own formulas, when it has them
            dcf = next((m for m in o.get("football_field", []) if m["method"] == "DCF"), {})
            out.append({"model_id": fm.id, "version": v, "date": _aware(fm.date).isoformat(), "source": _source(fm),
                        "note": a.get("note"), "changes": a.get("changes") or [], "structure": a.get("structure") or [],
                        "parent_model_id": a.get("parent_model_id"), "workbook": fm.id in with_wb,
                        "probability_weighted_price": wv.get("Probability-weighted", o.get("probability_weighted_price")),
                        "dcf_price": wv.get("DCF", dcf.get("implied_price")), "latest": v == len(rows)})
        return out[::-1]


def version_meta(symbol: str, model_id: int) -> dict:
    v = next((x for x in list_versions(symbol) if x["model_id"] == model_id), None)
    return {"model_id": model_id, "version": v["version"] if v else None,
            "source_label": "your edits" if v and v["source"] == "pm_upload" else "agent"}


def generated_workbook(symbol: str, model_id: int) -> bytes:
    """A version exported fresh from its inputs/outputs (versions that don't carry the PM's workbook)."""
    from agents.base_analyst import build_track_record
    from data_sources.cache import get_cached

    ctx = get_context(symbol)
    with session_scope() as s:
        fm = s.get(FinancialModel, model_id)
        research = s.get(ResearchOutput, fm.research_id) if fm.research_id else None
        record = {**(research.research_memo if research else {}), "date": fm.date.date().isoformat()}
        inputs, outputs = fm.assumptions["inputs"], fm.assumptions["outputs"]
    sec = get_cached("sec", (ctx.sec_ticker or ctx.symbol).upper(), None)
    return build_workbook(ctx.analyst_name, ctx.symbol, record, inputs, outputs, build_track_record(ctx.ticker_id, sec),
                          model_meta=version_meta(ctx.symbol, model_id))


def export_workbook(symbol: str, model_id: int) -> bytes:
    """The workbook to download for a version: the PM's own (as last edited by the PM or an agent) if the
    version carries one, else a fresh export."""
    wb = mw.get(model_id)
    return wb.data if wb is not None else generated_workbook(symbol, model_id)


# --- Reading an edited workbook ---------------------------------------------------------

def _set_path(obj, path: str, value) -> None:
    keys = [int(k) if k.isdigit() else k for k in path.split(".")]
    for k in keys[:-1]:
        obj = obj[k]
    obj[keys[-1]] = value


def _get_path(obj, path: str):
    for k in path.split("."):
        obj = obj[int(k)] if k.isdigit() else obj[k]
    return obj


def _convert(raw, kind: str, cell: str):
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            raw = float(raw.replace(",", "").replace("%", "").strip())
        except ValueError:
            raise ModelUploadError(f"{cell}: '{raw}' is not a number")
    if kind == "pct":
        return round(float(raw) * 100, 6)
    if kind == "method":
        return "exit_multiple" if round(float(raw)) == 2 else "perpetuity_growth"
    if kind == "flag":
        return bool(round(float(raw)))
    return float(raw)


def _label(path: str, inputs: dict) -> str:
    years = inputs.get("quarters") or inputs.get("fiscal_years", [])  # quarterly models index per-period inputs by quarter
    annual_years = quarter_years(years) if is_quarterly(inputs) else years
    parts = path.split(".")
    yr = lambda i: years[int(i)] if i.isdigit() and int(i) < len(years) else i  # noqa: E731
    names = {"gross_margin_pct": "Gross margin", "adj_ebitda_margin_pct": "Adj. EBITDA margin", "sbc_pct_of_revenue": "SBC % revenue",
             "da_pct_of_revenue": "D&A % revenue", "capex_pct_of_revenue": "Capex % revenue", "cash_tax_rate_pct": "Cash tax rate",
             "nwc_pct_of_revenue_change": "NWC % of revenue change", "wacc_pct": "WACC", "terminal_growth_pct": "Terminal growth",
             "exit_multiple_ev_ebitda": "Exit multiple", "terminal_method": "Terminal method", "sbc_as_cash_cost": "SBC as cash cost",
             "share_price": "Share price", "diluted_shares_mm": "Diluted shares", "total_debt_mm": "Total debt",
             "cash_and_investments_mm": "Cash", "other_ev_adjustments_mm": "Other EV adjustments",
             "base_probability_pct": "Base probability", "probability_pct": "probability",
             "revenue_growth_pct": "revenue growth", "adj_ebitda_margin_pct_s": "EBITDA margin"}
    if parts[0] == "revenue_segments":
        seg = inputs["revenue_segments"][int(parts[1])]
        if parts[2] == "revenue_mm":
            return f"{seg['name']} revenue {yr(parts[3])}"
        if parts[2] == "sotp_ev_to_revenue":
            return f"{seg['name']} SOTP multiple"
        return f"{seg['name']} · {seg['drivers'][int(parts[3])]['name']} {yr(parts[5])}"
    if parts[0] in ("dcf", "capitalization"):
        return f"{'DCF' if parts[0] == 'dcf' else 'Cap'} · {names.get(parts[1], parts[1])}"
    if parts[0] == "scenarios":
        if parts[1] == "base_probability_pct":
            return "Base case probability"
        e_years = [y for y in annual_years if y.endswith("E")]
        field = {"probability_pct": "probability", "revenue_growth_pct": "revenue growth", "adj_ebitda_margin_pct": "EBITDA margin"}[parts[2]]
        return f"{parts[1].title()} {field}" + (f" {e_years[int(parts[3])]}" if len(parts) > 3 and int(parts[3]) < len(e_years) else "")
    return f"{names.get(parts[0], parts[0])}" + (f" {yr(parts[1])}" if len(parts) > 1 else "")


# --- Applying an upload --------------------------------------------------------------------

def apply_upload(symbol: str, data: bytes, note: str | None = None) -> dict:
    ctx = get_context(symbol)
    if ctx is None:
        raise ModelUploadError(f"{symbol} is not covered")
    try:
        up = mw.Book(data)
        meta, _entries, up_names = mw.read_map(up)
    except mw.WorkbookError as exc:
        raise ModelUploadError(str(exc))
    if meta.get("ticker") and meta["ticker"].upper() != ctx.symbol:
        raise ModelUploadError(f"This workbook is for {meta['ticker']}, not {ctx.symbol}")
    with session_scope() as s:
        base = s.get(FinancialModel, meta.get("model_id")) if meta.get("model_id") else None
        if base is None or base.ticker_id != ctx.ticker_id:
            base = s.scalar(select(FinancialModel).where(FinancialModel.ticker_id == ctx.ticker_id).order_by(FinancialModel.date.desc()).limit(1))
        if base is None or not (base.assumptions or {}).get("inputs"):
            raise ModelUploadError(f"No model to edit for {ctx.symbol} yet")
        base_id, research_id = base.id, base.research_id
        base_inputs = copy.deepcopy(base.assumptions["inputs"])
        base_outputs = base.assumptions.get("outputs") or {}
        base_structure = base.assumptions.get("structure") or []
        margin_analysis = base.margin_analysis
    # the version the PM downloaded, as it was exported: where every input cell was, and what to diff against
    base_wb = mw.get(base_id)
    ref = mw.Book(base_wb.data if base_wb is not None else generated_workbook(ctx.symbol, base_id))
    _ref_meta, ref_entries, _ = mw.read_map(ref)
    entries, missing = mw.resolve_map(up, ref, ref_entries, up_names)

    inputs = copy.deepcopy(base_inputs)
    changes = []
    for sheet, cell, path, kind in entries:
        if kind == "out":
            continue
        new = _convert(up.value(sheet, cell), kind, f"{sheet}!{cell}")
        try:
            old = _get_path(base_inputs, path)
        except (KeyError, IndexError, TypeError):
            continue  # a map entry the base model doesn't have (older export)
        if new is None and old is None:
            continue
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and not isinstance(old, bool):
            if abs(new - old) <= 1e-6 * max(1.0, abs(old)):
                continue
        elif new == old:
            continue
        _set_path(inputs, path, new)
        changes.append({"path": path, "label": _label(path, base_inputs), "cell": f"{sheet}!{cell}", "old": old, "new": new})
    input_cells = {(sh, c) for sh, c, _p, k in entries if k != "out"}
    structure = mw.structure_changes(ref, up, input_cells)
    if not changes and not structure:
        raise ModelUploadError("No changes found compared with the version you downloaded")
    values = mw.recalc(data) if mw.needs_recalc(up) else None
    wb_valuation = mw.valuation(up, entries, values)
    # models built before an input existed: fill neutral defaults so they still validate
    inputs.setdefault("private_market", {"marks": [], "applied_ev_to_revenue": None,
                                         "rationale": "Not set (model predates private-market inputs)"})
    comps = {name: b.get("peers", []) for name, b in ((base_outputs.get("comps") or {}).get("buckets") or {}).items()}
    try:
        model, outputs = parse_and_compute(inputs, comps, symbol=ctx.symbol)
    except ValidationError as exc:
        raise ModelUploadError("Some inputs are invalid: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5]))
    except ModelInputError as exc:
        raise ModelUploadError(str(exc))
    latest_id = _latest_id(ctx.ticker_id)
    with session_scope() as s:
        fm = FinancialModel(ticker_id=ctx.ticker_id, research_id=research_id, revenue_projections=outputs["rows"],
                            margin_analysis=margin_analysis,
                            assumptions={"inputs": model.model_dump(), "outputs": outputs, "source": "pm_upload",
                                         "note": (note or "").strip()[:1000] or None, "parent_model_id": base_id,
                                         "changes": changes, "structure": structure or base_structure,
                                         "workbook_valuation": wb_valuation, "missing_inputs": missing,
                                         "uploaded_at": datetime.now(timezone.utc).isoformat()})
        s.add(fm)
        s.flush()
        new_id = fm.id
    versions = list_versions(ctx.symbol)
    mw.save(new_id, data, entries, {"ticker": ctx.symbol, **version_meta(ctx.symbol, new_id),
                                    "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            mw.digest(up, values), {"structure": structure or base_structure, "valuation": wb_valuation, "missing": missing,
                                    "sheets": up.sheets})
    refresh_files(ctx.symbol, ["financial_model.json"])
    queued = None
    try:  # re-anchor the trading levels on the PM's model (light model, ~$0.06, runs within research hours)
        from scheduler.jobs import enqueue
        summary = "; ".join([f"{c['label']}: {_fmt(c['old'])} -> {_fmt(c['new'])}" for c in changes[:6]] + structure[:4])
        queued = enqueue("thesis_update", ctx.symbol, f"PM edited the model: {summary}"[:500], "pm_model")
    except Exception:
        pass
    ff = outputs["football_field"]
    based_on = next((v["version"] for v in versions if v["model_id"] == base_id), None)
    return {"model_id": new_id, "version": versions[0]["version"], "based_on_version": based_on,
            "superseded_version": next((v["version"] for v in versions if v["model_id"] == latest_id), None) if latest_id != base_id else None,
            "changes": changes, "structure": structure,
            "missing_inputs": [p if p.startswith("valuation.") else _label(p, base_inputs) for p in missing],
            "football_field": [{k: m[k] for k in ("method", "implied_price", "upside_pct")} for m in ff],
            "probability_weighted_price": outputs["probability_weighted_price"], "workbook_valuation": wb_valuation,
            "thesis_update_queued": bool(queued)}


def _latest_id(ticker_id: int) -> int | None:
    with session_scope() as s:
        return s.scalar(select(FinancialModel.id).where(FinancialModel.ticker_id == ticker_id)
                        .order_by(FinancialModel.date.desc(), FinancialModel.id.desc()).limit(1))


def _fmt(v) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def model_for_agent(ticker_id: int) -> dict | None:
    """The latest model as the agent should see it at its next deep dive (inputs + whose version it is)."""
    with session_scope() as s:
        fm = s.scalar(select(FinancialModel).where(FinancialModel.ticker_id == ticker_id).order_by(FinancialModel.date.desc()).limit(1))
        if fm is None or not (fm.assumptions or {}).get("inputs"):
            return None
        a = fm.assumptions
        model_id = fm.id
        out = {"model_id": model_id, "source": a.get("source") or "agent", "date": _aware(fm.date).date().isoformat(),
               "note": a.get("note"), "inputs": a["inputs"],
               "pm_changes": [{k: c[k] for k in ("label", "old", "new")} for c in a.get("changes") or []]}
    out["workbook"] = mw.for_agent(model_id)  # the PM's workbook, once they've uploaded one
    return out


def save_agent_version(ctx, base: dict | None, wb_session: "mw.Session | None", inputs: dict, outputs: dict, comps: dict,
                       research_id: int | None, margin_analysis: dict, extra: dict | None = None) -> int | None:
    """Save an agent-built model as the next version. When the latest version carries the PM's workbook, the
    agent's inputs and staged cell edits are written into that workbook (the same file goes on). Returns the new
    model id, or None when the PM uploaded a newer version while the agent was working (theirs stays latest)."""
    latest = _latest_id(ctx.ticker_id)
    if base is not None and latest != base.get("model_id"):
        with session_scope() as s:
            newer = s.get(FinancialModel, latest)
            if newer is not None and _source(newer) == "pm_upload":
                logger.info("%s: PM uploaded v-new during the run; agent model not saved over it", ctx.symbol)
                return None
    assumptions = {"inputs": inputs, "outputs": outputs, **(extra or {})}
    built = None
    if wb_session is not None and base is not None:
        try:
            built = wb_session.build(base["inputs"], inputs, comps, ctx.symbol, _label)
            assumptions.update({"inputs": built["inputs"], "outputs": built["outputs"], "changes": built["changes"],
                                "structure": built["info"].get("structure") or [],
                                "workbook_valuation": built["info"].get("valuation") or {},
                                "parent_model_id": base["model_id"]})
        except Exception:
            logger.exception("%s: couldn't write the update into the PM's workbook; saving a fresh model", ctx.symbol)
            built = None
    with session_scope() as s:
        fm = FinancialModel(ticker_id=ctx.ticker_id, research_id=research_id, revenue_projections=assumptions["outputs"]["rows"],
                            margin_analysis=margin_analysis, assumptions=assumptions)
        s.add(fm)
        s.flush()
        new_id = fm.id
    if built is not None:
        mw.save(new_id, built["data"], built["entries"], {"ticker": ctx.symbol, **version_meta(ctx.symbol, new_id),
                                                         "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                built["digest"], built["info"])
    return new_id
