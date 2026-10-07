"""Financial model: the schema Claude fills in (drivers + assumptions) and the deterministic math.

Claude supplies the judgment (segment revenue build, margins, WACC, scenarios, which comps bucket the
market is using). Python does the arithmetic (P&L roll-up, DCF, comps / SOTP / scenario valuation,
sensitivities) so every number in the memo and the Excel export is internally consistent.

Structure mirrors the team's BLSH / PUBM models: Summary (capitalization + football field),
Revenue Build, P&L, Scenarios, DCF, Comps (bucketed by valuation logic), Model vs. Street,
Historical Tracker.

Units: USD millions unless noted; percentages are whole numbers (25.0 = 25%).
"""

import re
import statistics
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

YEAR_LABEL = re.compile(r"^(\d{4})([AE])$")


class ModelInputError(ValueError):
    """Raised when Claude's model inputs are inconsistent; the message is sent back for a fix."""


# --- Schema (tool input for submit_financial_model) ------------------------------

class DriverRow(BaseModel):
    name: str = Field(description="Driver name, e.g. 'Spot volume', 'Impressions processed', 'Take rate'")
    unit: str = Field(description="e.g. '$B', 'bps', 'trillions', '%', '#'")
    values: list[float | None] = Field(description="One value per fiscal_years entry; null where not applicable")
    logic: str = Field(description="Source for actuals and reasoning for the forecast values")


class RevenueSegment(BaseModel):
    name: str
    driver_logic: str = Field(description="How the segment's revenue is built, e.g. 'Volume ($B) x average spread (bps)'")
    drivers: list[DriverRow] = Field(description="Operating drivers behind the segment (may be empty if none disclosed)")
    revenue_mm: list[float | None] = Field(description="Segment revenue in $mm, one per fiscal_years entry")
    sotp_ev_to_revenue: float | None = Field(description="EV/Revenue multiple for sum-of-the-parts, ideally a comps bucket median; null to exclude")
    sotp_rationale: str = Field(description="Which comps bucket / logic justifies the SOTP multiple")


class Capitalization(BaseModel):
    share_price: float = Field(description="Current share price ($) from the market data")
    diluted_shares_mm: float
    total_debt_mm: float
    cash_and_investments_mm: float
    other_ev_adjustments_mm: float = Field(description="Added to EV (minority interest, preferred, pro forma deal debt); 0 if none")
    source_notes: str = Field(description="Where each figure comes from (filing, date) and any pro forma adjustments")


class DCFAssumptions(BaseModel):
    wacc_pct: float
    terminal_growth_pct: float
    exit_multiple_ev_ebitda: float
    terminal_method: Literal["perpetuity_growth", "exit_multiple"]
    sbc_as_cash_cost: bool = Field(description="True (preferred) treats stock comp as a real cost in unlevered FCF")
    rationale: str


class ScenarioPath(BaseModel):
    probability_pct: float
    description: str
    revenue_growth_pct: list[float] = Field(description="Total revenue growth, one value per forecast (E) year")
    adj_ebitda_margin_pct: list[float] = Field(description="Adjusted EBITDA margin, one value per forecast (E) year")
    key_drivers: str = Field(description="Which driver moves vs. base and why")


class Scenarios(BaseModel):
    bull: ScenarioPath
    bear: ScenarioPath
    base_probability_pct: float
    base_description: str = Field(description="The base case is the segment build itself; describe it")


class CompsView(BaseModel):
    primary_bucket: str = Field(description="Name of the comps bucket the market is pricing the stock on today (must match a provided bucket)")
    rationale: str = Field(description="Why that bucket, and which bucket the thesis argues it should migrate toward")


class PrivateMark(BaseModel):
    name: str = Field(description="Private company round or transaction, e.g. 'Kalshi Series E' or 'Take-private of X by Y'")
    kind: Literal["funding_round", "secondary", "acquisition", "take_private", "ipo"]
    date: str
    valuation_mm: float = Field(description="Post-money valuation or deal enterprise value, $mm")
    revenue_mm: float | None = Field(description="Revenue or run-rate at the time, $mm, if known (null if not)")
    source: str = Field(description="Where it comes from (a filing or headline in the data); 'general knowledge, unverified' otherwise")
    relevance: str = Field(description="Why this mark is comparable to the business being valued")


class PrivateMarketView(BaseModel):
    marks: list[PrivateMark] = Field(description="2-6 private-market reference points: private peers' funding rounds or "
                                                 "secondaries, precedent acquisitions, take-privates. Empty if none credible")
    applied_ev_to_revenue: float | None = Field(description="EV/revenue a private-market or strategic buyer would pay for this "
                                                            "business today (after control premium / illiquidity), or null")
    rationale: str = Field(description="How the marks translate into that multiple, and where private marks sit vs public comps")


class NextPrint(BaseModel):
    period: str = Field(description="Next reported quarter as a calendar frame matching the XBRL table, e.g. 'CY2026Q3'")
    revenue_estimate_mm: float
    adj_ebitda_estimate_mm: float | None
    company_guidance: str = Field(description="Company guidance for that quarter, quoted with source, or 'none given'")
    key_kpi_name: str
    key_kpi_estimate: str
    why_we_differ: str = Field(description="The specific data point or logic behind any gap vs. guidance")


class ModelInputs(BaseModel):
    fiscal_years: list[str] = Field(
        description="Calendar-year labels, oldest first: 2 actual years then 4 forecast years, e.g. "
        "['2024A','2025A','2026E','2027E','2028E','2029E']. Calendarize non-December fiscal years."
    )
    revenue_segments: list[RevenueSegment]
    gross_margin_pct: list[float | None]
    adj_ebitda_margin_pct: list[float | None]
    sbc_pct_of_revenue: list[float | None]
    da_pct_of_revenue: list[float | None]
    capex_pct_of_revenue: list[float | None] = Field(description="Capex including capitalized software")
    nwc_pct_of_revenue_change: float = Field(description="Change in net working capital as % of the change in revenue")
    cash_tax_rate_pct: float
    capitalization: Capitalization
    dcf: DCFAssumptions
    scenarios: Scenarios
    comps: CompsView
    private_market: PrivateMarketView
    next_print: NextPrint
    key_assumptions: list[str] = Field(description="The 4-8 assumptions that drive the answer, each with its basis")
    model_notes: str = Field(description="Caveats: accounting basis, calendarization, pro forma treatment, data gaps")


PER_YEAR_FIELDS = ["gross_margin_pct", "adj_ebitda_margin_pct", "sbc_pct_of_revenue", "da_pct_of_revenue", "capex_pct_of_revenue"]


# --- Helpers ------------------------------------------------------------------------

def _f(pct: float | None) -> float | None:
    return None if pct is None else pct / 100.0


def _fill_forward(values: list[float | None]) -> list[float | None]:
    out, last = [], None
    for v in values:
        last = v if v is not None else last
        out.append(last)
    return out


def _median(values) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float)) and v > 0]
    return round(statistics.median(clean), 2) if clean else None


def _mean(values) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float)) and v > 0]
    return round(statistics.fmean(clean), 2) if clean else None


def fmt_money_mm(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    x = abs(x)
    return f"{sign}${x / 1000:,.2f}B" if x >= 1000 else f"{sign}${x:,.1f}M"


def fmt_pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}%"


def fmt_price(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


# --- Validation ---------------------------------------------------------------------

def validate_inputs(m: ModelInputs, comps: dict) -> None:
    errors = []
    n = len(m.fiscal_years)
    years = []
    for label in m.fiscal_years:
        match = YEAR_LABEL.match(label)
        if not match:
            errors.append(f"fiscal_years label {label!r} must look like '2026E' or '2025A'")
        else:
            years.append((int(match.group(1)), match.group(2)))
    if years:
        if [y for y, _ in years] != list(range(years[0][0], years[0][0] + len(years))):
            errors.append("fiscal_years must be consecutive calendar years, oldest first")
        kinds = "".join(k for _, k in years)
        if "EA" in kinds or "A" not in kinds or "E" not in kinds:
            errors.append("fiscal_years needs actual (A) years followed by forecast (E) years")
    n_forecast = sum(1 for _, k in years if k == "E")

    for seg in m.revenue_segments:
        if len(seg.revenue_mm) != n:
            errors.append(f"segment {seg.name!r}: revenue_mm has {len(seg.revenue_mm)} values, expected {n}")
        for d in seg.drivers:
            if len(d.values) != n:
                errors.append(f"segment {seg.name!r} driver {d.name!r}: {len(d.values)} values, expected {n}")
    for name in PER_YEAR_FIELDS:
        values = getattr(m, name)
        if len(values) != n:
            errors.append(f"{name} has {len(values)} values, expected {n}")
        elif any(values[i] is None for i, (_, k) in enumerate(years) if k == "E"):
            errors.append(f"{name} must be filled for every forecast year")
    if not m.revenue_segments:
        errors.append("at least one revenue segment is required")
    for label, path in (("bull", m.scenarios.bull), ("bear", m.scenarios.bear)):
        if len(path.revenue_growth_pct) != n_forecast or len(path.adj_ebitda_margin_pct) != n_forecast:
            errors.append(f"{label} scenario needs exactly {n_forecast} growth and margin values (one per E year)")
    total_p = m.scenarios.bull.probability_pct + m.scenarios.bear.probability_pct + m.scenarios.base_probability_pct
    if abs(total_p - 100) > 0.5:
        errors.append(f"scenario probabilities sum to {total_p}, must be 100")
    if m.dcf.terminal_method == "perpetuity_growth" and m.dcf.wacc_pct <= m.dcf.terminal_growth_pct:
        errors.append("wacc_pct must exceed terminal_growth_pct")
    if m.capitalization.diluted_shares_mm <= 0:
        errors.append("diluted_shares_mm must be positive")
    if comps and m.comps.primary_bucket not in comps:
        errors.append(f"comps.primary_bucket {m.comps.primary_bucket!r} must be one of {sorted(comps)}")
    if errors:
        raise ModelInputError("; ".join(errors))


# --- P&L and DCF math -------------------------------------------------------------

def build_rows(years: list[str], revenue: list[float | None], m: ModelInputs,
               growth_override: list[float] | None = None, margin_override: list[float] | None = None) -> list[dict]:
    """Annual P&L / cash flow rows. Overrides (forecast years only) are used for bull/bear scenarios."""
    gm = _fill_forward(m.gross_margin_pct)
    em = _fill_forward(m.adj_ebitda_margin_pct)
    sbc = _fill_forward(m.sbc_pct_of_revenue)
    da = _fill_forward(m.da_pct_of_revenue)
    capex = _fill_forward(m.capex_pct_of_revenue)
    tax = m.cash_tax_rate_pct / 100.0

    rows, prev_rev, e_index = [], None, 0
    for i, label in enumerate(years):
        is_forecast = label.endswith("E")
        rev = revenue[i]
        margin = em[i]
        if is_forecast and growth_override is not None:
            rev = prev_rev * (1 + growth_override[e_index] / 100.0) if prev_rev is not None else rev
            margin = margin_override[e_index]
        if is_forecast:
            e_index += 1
        row = {"year": label, "forecast": is_forecast, "revenue": rev}
        row["revenue_growth_pct"] = (rev / prev_rev - 1) * 100 if rev is not None and prev_rev else None
        row["gross_profit"] = rev * _f(gm[i]) if rev is not None and gm[i] is not None else None
        row["gross_margin_pct"] = gm[i]
        row["adj_ebitda"] = rev * _f(margin) if rev is not None and margin is not None else None
        row["adj_ebitda_margin_pct"] = margin
        row["sbc"] = rev * _f(sbc[i]) if rev is not None and sbc[i] is not None else 0.0
        row["da"] = rev * _f(da[i]) if rev is not None and da[i] is not None else 0.0
        row["capex"] = rev * _f(capex[i]) if rev is not None and capex[i] is not None else 0.0
        if row["adj_ebitda"] is not None:
            row["ebit"] = row["adj_ebitda"] - row["sbc"] - row["da"]
            row["cash_taxes"] = max(0.0, row["ebit"]) * tax
            delta_rev = (rev - prev_rev) if (rev is not None and prev_rev is not None) else 0.0
            row["change_nwc"] = delta_rev * m.nwc_pct_of_revenue_change / 100.0
            row["fcf"] = row["adj_ebitda"] - row["cash_taxes"] - row["capex"] - row["change_nwc"]
            row["fcf_margin_pct"] = row["fcf"] / rev * 100 if rev else None
            row["ufcf"] = row["fcf"] - (row["sbc"] if m.dcf.sbc_as_cash_cost else 0.0)
        else:
            row.update(ebit=None, cash_taxes=None, change_nwc=None, fcf=None, fcf_margin_pct=None, ufcf=None)
        rows.append(row)
        prev_rev = rev
    return rows


def run_dcf(rows: list[dict], wacc_pct: float, g_pct: float, exit_multiple: float, method: str,
            valuation_date: date) -> dict:
    """Mid-year-convention DCF over forecast rows; the current year is prorated for the remaining stub."""
    wacc, g = wacc_pct / 100.0, g_pct / 100.0
    lines, t_end, sum_pv = [], 0.0, 0.0
    for row in rows:
        if not row["forecast"] or row["ufcf"] is None:
            continue
        year_end = date(int(row["year"][:4]), 12, 31)
        if year_end <= valuation_date:
            continue
        fraction = min(1.0, (year_end - valuation_date).days / 365.0)
        t_end += fraction
        period = t_end - 0.5 * fraction
        factor = 1 / (1 + wacc) ** period
        pv = row["ufcf"] * fraction * factor
        sum_pv += pv
        lines.append({"year": row["year"], "ufcf": row["ufcf"], "fraction": fraction, "discount_period": period,
                      "discount_factor": factor, "pv": pv})
    if not lines:
        return {"lines": [], "enterprise_value": None}
    last = next(r for r in reversed(rows) if r["forecast"])
    if method == "perpetuity_growth":
        tv = last["ufcf"] * (1 + g) / (wacc - g) if wacc > g else None
    else:
        tv = exit_multiple * last["adj_ebitda"] if last["adj_ebitda"] is not None else None
    if tv is None:
        return {"lines": lines, "enterprise_value": None}
    pv_tv = tv / (1 + wacc) ** t_end
    ev = sum_pv + pv_tv
    return {
        "lines": lines,
        "sum_pv_fcf": sum_pv,
        "terminal_value": tv,
        "pv_terminal_value": pv_tv,
        "enterprise_value": ev,
        "terminal_pct_of_ev": pv_tv / ev * 100 if ev else None,
        "implied_terminal_ev_ebitda": tv / last["adj_ebitda"] if last["adj_ebitda"] else None,
    }


def _equity_and_price(ev: float | None, cap: Capitalization) -> tuple[float | None, float | None]:
    if ev is None:
        return None, None
    equity = ev - cap.total_debt_mm + cap.cash_and_investments_mm - cap.other_ev_adjustments_mm
    return equity, equity / cap.diluted_shares_mm


# --- Main entry point ----------------------------------------------------------------

def compute_model(m: ModelInputs, comps: dict | None = None, valuation_date: date | None = None, symbol: str = "SUBJECT") -> dict:
    """Validate and compute everything. `comps` = {bucket: [peer dicts with ev_to_revenue / ev_to_ebitda / growth]}."""
    comps = comps or {}
    validate_inputs(m, comps)
    valuation_date = valuation_date or date.today()
    years = m.fiscal_years
    n = len(years)
    cap = m.capitalization

    revenue = []
    for i in range(n):
        vals = [s.revenue_mm[i] for s in m.revenue_segments if s.revenue_mm[i] is not None]
        revenue.append(sum(vals) if vals else None)
    rows = build_rows(years, revenue, m)
    first_e = next(i for i, y in enumerate(years) if y.endswith("E"))
    cur = rows[first_e]

    # Capitalization (Summary tab)
    market_cap = cap.share_price * cap.diluted_shares_mm
    net_debt = cap.total_debt_mm - cap.cash_and_investments_mm
    ev_now = market_cap + net_debt + cap.other_ev_adjustments_mm
    capitalization = {
        "share_price": cap.share_price,
        "diluted_shares_mm": cap.diluted_shares_mm,
        "market_cap": market_cap,
        "total_debt": cap.total_debt_mm,
        "cash_and_investments": cap.cash_and_investments_mm,
        "net_debt": net_debt,
        "other_ev_adjustments": cap.other_ev_adjustments_mm,
        "enterprise_value": ev_now,
        "ev_to_revenue_current_year": ev_now / cur["revenue"] if cur["revenue"] else None,
        "ev_to_ebitda_current_year": ev_now / cur["adj_ebitda"] if cur["adj_ebitda"] and cur["adj_ebitda"] > 0 else None,
        "current_year": cur["year"],
    }

    # DCF (base case) + sensitivities
    d = m.dcf
    dcf = run_dcf(rows, d.wacc_pct, d.terminal_growth_pct, d.exit_multiple_ev_ebitda, d.terminal_method, valuation_date)
    dcf["equity_value"], dcf["implied_price"] = _equity_and_price(dcf["enterprise_value"], cap)
    dcf["valuation_date"] = valuation_date.isoformat()
    wacc_axis = [d.wacc_pct + k for k in (-2, -1, 0, 1, 2)]
    g_axis = [d.terminal_growth_pct + k for k in (-1, -0.5, 0, 0.5, 1)]
    mult_axis = [d.exit_multiple_ev_ebitda + k for k in (-2, -1, 0, 1, 2)]

    def price_at(w, g, mult, method):
        if method == "perpetuity_growth" and w <= g:
            return None
        return _equity_and_price(run_dcf(rows, w, g, mult, method, valuation_date)["enterprise_value"], cap)[1]

    sensitivities = {
        "wacc_vs_terminal_growth": {"wacc_pct": wacc_axis, "terminal_growth_pct": g_axis,
                                    "prices": [[price_at(w, g, 0, "perpetuity_growth") for g in g_axis] for w in wacc_axis]},
        "wacc_vs_exit_multiple": {"wacc_pct": wacc_axis, "exit_multiple": mult_axis,
                                  "prices": [[price_at(w, 0, x, "exit_multiple") for x in mult_axis] for w in wacc_axis]},
    }

    # Comps (bucketed by valuation logic)
    buckets = {}
    for name, peers in comps.items():
        buckets[name] = {
            "peers": peers,
            "median_ev_to_revenue": _median(p.get("ev_to_revenue") for p in peers),
            "median_ev_to_ebitda": _median(p.get("ev_to_ebitda") for p in peers),
            "mean_ev_to_revenue": _mean(p.get("ev_to_revenue") for p in peers),
            "mean_ev_to_ebitda": _mean(p.get("ev_to_ebitda") for p in peers),
        }
    primary = buckets.get(m.comps.primary_bucket, {})

    # Football field
    methods = []

    def add(method, ev, basis):
        equity, price = _equity_and_price(ev, cap)
        methods.append({"method": method, "enterprise_value": ev, "equity_value": equity, "implied_price": price,
                        "upside_pct": (price / cap.share_price - 1) * 100 if price is not None else None, "basis": basis})

    add("DCF", dcf["enterprise_value"],
        f"{d.terminal_method.replace('_', ' ')}, WACC {d.wacc_pct:.1f}%, "
        + (f"g {d.terminal_growth_pct:.1f}%" if d.terminal_method == "perpetuity_growth" else f"{d.exit_multiple_ev_ebitda:.1f}x exit"))
    if primary.get("median_ev_to_revenue") and cur["revenue"]:
        add("Comps - EV/Revenue", primary["median_ev_to_revenue"] * cur["revenue"],
            f"{m.comps.primary_bucket} median {primary['median_ev_to_revenue']:.1f}x LTM x {cur['year']} revenue")
    if primary.get("median_ev_to_ebitda") and cur["adj_ebitda"] and cur["adj_ebitda"] > 0:
        add("Comps - EV/EBITDA", primary["median_ev_to_ebitda"] * cur["adj_ebitda"],
            f"{m.comps.primary_bucket} median {primary['median_ev_to_ebitda']:.1f}x LTM x {cur['year']} adj. EBITDA")
    # Private market: funding rounds, secondaries, precedent M&A / take-privates
    pm = m.private_market
    marks = [{**k.model_dump(), "implied_ev_to_revenue": k.valuation_mm / k.revenue_mm if k.revenue_mm else None} for k in pm.marks]
    private_market = {"marks": marks, "median_mark_ev_to_revenue": _median(k["implied_ev_to_revenue"] for k in marks),
                      "applied_ev_to_revenue": pm.applied_ev_to_revenue, "rationale": pm.rationale}
    if pm.applied_ev_to_revenue and cur["revenue"]:
        add("Private market / M&A", pm.applied_ev_to_revenue * cur["revenue"],
            f"{pm.applied_ev_to_revenue:.1f}x {cur['year']} revenue from private marks and precedent deals")
    sotp_parts = [(s.name, s.revenue_mm[first_e], s.sotp_ev_to_revenue) for s in m.revenue_segments
                  if s.sotp_ev_to_revenue and s.revenue_mm[first_e]]
    sotp_ev = sum(r * x for _, r, x in sotp_parts) if sotp_parts else None
    if sotp_ev:
        add("Sum-of-the-parts", sotp_ev, " + ".join(f"{name} {x:.1f}x" for name, _, x in sotp_parts) + f" on {cur['year']} revenue")

    # Scenarios: bull / bear re-run the DCF on their own growth & margin paths; base is the segment build.
    scenarios = []
    for name, path in (("bull", m.scenarios.bull), ("base", None), ("bear", m.scenarios.bear)):
        if path is None:
            s_rows, prob, desc, drivers = rows, m.scenarios.base_probability_pct, m.scenarios.base_description, "Segment build"
        else:
            s_rows = build_rows(years, revenue, m, path.revenue_growth_pct, path.adj_ebitda_margin_pct)
            prob, desc, drivers = path.probability_pct, path.description, path.key_drivers
        s_dcf = run_dcf(s_rows, d.wacc_pct, d.terminal_growth_pct, d.exit_multiple_ev_ebitda, d.terminal_method, valuation_date)
        equity, price = _equity_and_price(s_dcf["enterprise_value"], cap)
        scenarios.append({
            "name": name, "probability_pct": prob, "description": desc, "key_drivers": drivers,
            "revenue_path": [r["revenue"] for r in s_rows if r["forecast"]],
            "revenue_growth_pct": [r["revenue_growth_pct"] for r in s_rows if r["forecast"]],
            "adj_ebitda_margin_pct": [r["adj_ebitda_margin_pct"] for r in s_rows if r["forecast"]],
            "enterprise_value": s_dcf["enterprise_value"], "equity_value": equity, "implied_price": price,
            "upside_pct": (price / cap.share_price - 1) * 100 if price is not None else None,
        })
    priced = [s for s in scenarios if s["implied_price"] is not None]
    weighted = sum(s["implied_price"] * s["probability_pct"] for s in priced) / 100 if len(priced) == 3 else None
    for s in scenarios:
        add(f"Scenario - {s['name']}", s["enterprise_value"], f"{s['probability_pct']:.0f}% probability")
    if weighted is not None:
        methods.append({"method": "Probability-weighted", "enterprise_value": None, "equity_value": weighted * cap.diluted_shares_mm,
                        "implied_price": weighted, "upside_pct": (weighted / cap.share_price - 1) * 100,
                        "basis": "Bull/base/bear DCF prices x probabilities"})

    bear_eq = scenarios[2]["equity_value"]
    bull_eq = scenarios[0]["equity_value"]
    valuation_range = None
    if bear_eq is not None and bull_eq is not None:
        lo, hi = sorted([bear_eq, bull_eq])
        valuation_range = {"low_equity_value": lo, "high_equity_value": hi,
                           "low_price": lo / cap.diluted_shares_mm, "high_price": hi / cap.diluted_shares_mm}

    outputs = {
        "fiscal_years": years,
        "current_year": cur["year"],
        "rows": rows,
        "segments": [{"name": s.name, "revenue_mm": s.revenue_mm} for s in m.revenue_segments],
        "capitalization": capitalization,
        "dcf": dcf,
        "sensitivities": sensitivities,
        "comps": {"primary_bucket": m.comps.primary_bucket, "buckets": buckets},
        "private_market": private_market,
        "football_field": methods,
        "scenarios": scenarios,
        "probability_weighted_price": weighted,
        "valuation_range": valuation_range,
    }
    outputs["relative_value"] = relative_value(outputs, symbol)
    return outputs


# --- Relative value: who is rich vs cheap inside each comps bucket ------------------------------

RICH_CHEAP_BAND_PCT = 15.0  # beyond +/- this vs fair multiple: rich / cheap
# A multiple more than this many times away from the bucket median usually means a different revenue basis
# (e.g. gross crypto trading revenue vs net), not mispricing: flag it and keep it out of the fit and ranking.
OUTLIER_FACTOR = 5.0


def _fit_multiple_on_growth(points: list[tuple[float, float]]) -> dict | None:
    """OLS of EV/revenue on revenue growth across a bucket. Needs 3+ names and an upward slope to be meaningful."""
    if len(points) < 3 or len({g for g, _ in points}) < 2:
        return None
    n = len(points)
    mg = sum(g for g, _ in points) / n
    mm = sum(x for _, x in points) / n
    sxx = sum((g - mg) ** 2 for g, _ in points)
    slope = sum((g - mg) * (x - mm) for g, x in points) / sxx
    if slope <= 0:
        return None
    intercept = mm - slope * mg
    ss_tot = sum((x - mm) ** 2 for _, x in points)
    ss_res = sum((x - (intercept + slope * g)) ** 2 for g, x in points)
    return {"intercept": intercept, "slope": slope, "n": n, "r2": 1 - ss_res / ss_tot if ss_tot else None}


def score_bucket(names: list[dict]) -> dict:
    """Rank names (dicts with symbol, ev_to_revenue, revenue_growth_pct, optional is_subject) rich-to-cheap:
    EV/revenue vs the bucket median (subject excluded) and vs a growth-adjusted fair multiple fitted across the bucket."""
    raw_median = _median(n.get("ev_to_revenue") for n in names if not n.get("is_subject"))

    def outlier(n):
        evr = n.get("ev_to_revenue")
        return bool(evr and raw_median and (evr > raw_median * OUTLIER_FACTOR or evr < raw_median / OUTLIER_FACTOR))

    clean = [n for n in names if not outlier(n)]
    fit = _fit_multiple_on_growth([(n["revenue_growth_pct"], n["ev_to_revenue"]) for n in clean
                                   if n.get("revenue_growth_pct") is not None and n.get("ev_to_revenue")])
    median = _median(n.get("ev_to_revenue") for n in clean if not n.get("is_subject"))
    rows = []
    for n in names:
        evr, g = n.get("ev_to_revenue"), n.get("revenue_growth_pct")
        if outlier(n):
            rows.append({**{k: v for k, v in n.items()}, "symbol": n.get("symbol"), "name": n.get("name"),
                         "is_subject": bool(n.get("is_subject")), "ev_to_revenue": evr, "ev_to_ebitda": n.get("ev_to_ebitda"),
                         "revenue_growth_pct": g, "growth_adjusted_ev_to_revenue": None, "fair_ev_to_revenue": None,
                         "vs_median_pct": None, "vs_fair_pct": None, "gap_pct": None, "basis": None, "verdict": None,
                         "flag": f"{evr:.1f}x is >{OUTLIER_FACTOR:.0f}x away from the bucket median: check revenue basis (gross vs net)"})
            continue
        fair = fit["intercept"] + fit["slope"] * g if fit and g is not None else None
        fair = fair if fair and fair > 0 else None
        vs_median = (evr / median - 1) * 100 if evr and median else None
        vs_fair = (evr / fair - 1) * 100 if evr and fair else None
        gap = vs_fair if vs_fair is not None else vs_median
        verdict = None if gap is None else "rich" if gap > RICH_CHEAP_BAND_PCT else "cheap" if gap < -RICH_CHEAP_BAND_PCT else "in line"
        rows.append({**{k: v for k, v in n.items() if k not in ("ev_to_revenue", "revenue_growth_pct")},
                     "symbol": n.get("symbol"), "name": n.get("name"), "is_subject": bool(n.get("is_subject")),
                     "ev_to_revenue": evr, "ev_to_ebitda": n.get("ev_to_ebitda"), "revenue_growth_pct": g,
                     "growth_adjusted_ev_to_revenue": evr / g if evr and g and g > 0 else None,
                     "fair_ev_to_revenue": fair, "vs_median_pct": vs_median, "vs_fair_pct": vs_fair, "gap_pct": gap,
                     "basis": "growth-adjusted" if vs_fair is not None else "bucket median" if vs_median is not None else None,
                     "verdict": verdict})
    ranked = sorted([r for r in rows if r["verdict"]], key=lambda r: -r["gap_pct"])
    for i, r in enumerate(ranked, 1):
        r["rank_rich_to_cheap"] = i
    return {"median_ev_to_revenue": median, "fit": fit, "rows": ranked + [r for r in rows if not r["verdict"]]}


def relative_value(outputs: dict, symbol: str, peer_growth: dict | None = None) -> list[dict]:
    """Per comps bucket of one model: the subject (current-year EV/revenue and growth from the model) ranked
    against its peers (LTM). `peer_growth` fills peers' growth when the stored comps predate growth data."""
    cap = outputs.get("capitalization") or {}
    cur = next((r for r in outputs.get("rows", []) if r.get("year") == cap.get("current_year")), {})
    subject = {"symbol": symbol, "name": "this company", "is_subject": True,
               "ev_to_revenue": cap.get("ev_to_revenue_current_year"), "ev_to_ebitda": cap.get("ev_to_ebitda_current_year"),
               "revenue_growth_pct": cur.get("revenue_growth_pct")}
    result = []
    for bucket, b in ((outputs.get("comps") or {}).get("buckets") or {}).items():
        names = [subject]
        for p in b.get("peers", []):
            if p.get("symbol") == symbol:
                continue
            p = {k: p.get(k) for k in ("symbol", "name", "ev_to_revenue", "ev_to_ebitda", "revenue_growth_pct")}
            if p.get("revenue_growth_pct") is None and peer_growth:
                p["revenue_growth_pct"] = peer_growth.get(p["symbol"])
            names.append(p)
        scored = score_bucket(names)
        result.append({"bucket": bucket, "primary": bucket == (outputs.get("comps") or {}).get("primary_bucket"), **scored,
                       "subject": next((r for r in scored["rows"] if r["is_subject"]), None)})
    return result


def summary_for_research(outputs: dict) -> dict:
    """The compact `financial_model` block of the research JSON (first two forecast years)."""
    forecast = [r for r in outputs["rows"] if r["forecast"]]
    summary = {}
    for r in forecast[:2]:
        summary[f"revenue_{r['year']}"] = fmt_money_mm(r["revenue"])
    cur = forecast[0]
    summary["ebitda_margin"] = fmt_pct(cur["adj_ebitda_margin_pct"])
    summary["fcf"] = fmt_money_mm(cur["fcf"])
    vr = outputs.get("valuation_range")
    summary["valuation_range"] = (
        f"${vr['low_equity_value'] / 1000:,.1f}-{vr['high_equity_value'] / 1000:,.1f}B" if vr else "n/a"
    )
    rel = [f"{b['bucket']}: {b['subject']['verdict']} ({b['subject']['vs_fair_pct'] if b['subject']['vs_fair_pct'] is not None else b['subject']['vs_median_pct']:+.0f}% vs "
           f"{'growth-adjusted fair' if b['subject']['vs_fair_pct'] is not None else 'median'})"
           for b in outputs.get("relative_value") or [] if b.get("subject") and b["subject"].get("verdict")]
    if rel:
        summary["relative_value"] = "; ".join(rel)
    return summary


def describe_for_claude(outputs: dict) -> dict:
    """Rounded view of the computed model, returned to Claude as the tool result."""
    def r(x, nd=1):
        return round(x, nd) if isinstance(x, (int, float)) else x

    rows = [{k: r(v) for k, v in row.items()} for row in outputs["rows"]]
    return {
        "note": "Computed by the model engine from your inputs. Use these exact figures in the memo.",
        "current_year": outputs["current_year"],
        "annual_model": [{k: row[k] for k in ("year", "revenue", "revenue_growth_pct", "gross_margin_pct", "adj_ebitda",
                                              "adj_ebitda_margin_pct", "fcf", "fcf_margin_pct", "ufcf")} for row in rows],
        "capitalization": {k: r(v, 2) for k, v in outputs["capitalization"].items()},
        "dcf": {k: r(v, 2) for k, v in outputs["dcf"].items() if k != "lines"},
        "football_field": [{k: r(v, 2) for k, v in row.items()} for row in outputs["football_field"]],
        "scenarios": [{k: (r(v, 2) if not isinstance(v, list) else [r(x) for x in v]) for k, v in s.items()}
                      for s in outputs["scenarios"]],
        "probability_weighted_price": r(outputs["probability_weighted_price"], 2),
        "valuation_range": {k: r(v, 2) for k, v in (outputs["valuation_range"] or {}).items()},
        "comps_primary_bucket": outputs["comps"]["primary_bucket"],
        "comps_bucket_medians": {name: {"ev_to_revenue": b["median_ev_to_revenue"], "ev_to_ebitda": b["median_ev_to_ebitda"]}
                                 for name, b in outputs["comps"]["buckets"].items()},
        "private_market": {k: (r(v, 2) if not isinstance(v, list) else v) for k, v in (outputs.get("private_market") or {}).items()},
        "relative_value": [{"bucket": b["bucket"], "median_ev_to_revenue": r(b["median_ev_to_revenue"], 2),
                            "fit": {k: r(v, 3) for k, v in (b["fit"] or {}).items()} or None,
                            "rank_rich_to_cheap": [{k: r(v, 1) for k, v in row.items() if k in (
                                "symbol", "ev_to_revenue", "revenue_growth_pct", "fair_ev_to_revenue", "vs_median_pct",
                                "vs_fair_pct", "verdict", "is_subject")} for row in b["rows"]]}
                           for b in outputs.get("relative_value") or []],
        "research_json_financial_model": summary_for_research(outputs),
    }
