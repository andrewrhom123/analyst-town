"""Excel export of an agent's financial model, laid out like the team's BLSH / PUBM workbooks.

Tabs: Summary (capitalization + football field + thesis), Revenue Build, P&L, DCF, Scenarios, Comps,
Model vs. Street, Historical Tracker, Notes.

The P&L, DCF, capitalization and football field are live formulas off blue input cells, so assumptions
can be changed in Excel. Scenario prices and DCF sensitivity grids are engine outputs (static values).

Round trip: every blue input cell is registered in a hidden "_model_map" sheet (sheet, cell, model-input path,
kind) together with the model version it was exported from. exports/model_upload.py reads an edited workbook
back through that map, so the PM can edit blue cells, upload, and the engine recomputes a new model version.
Legend: blue = input, black = formula, green = link to another tab, yellow fill = key lever.
"""

import io
import json
from datetime import date, datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BLUE = Font(color="0000FF")
BLACK = Font(color="000000")
GREEN = Font(color="008000")
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=14)
NOTE = Font(italic=True, color="808080")
HEADER_FONT = Font(bold=True, color="FFFFFF")
ACTUAL_FILL = PatternFill("solid", fgColor="1F3864")  # navy = actual
FORECAST_FILL = PatternFill("solid", fgColor="00808A")  # teal = forecast
SECTION_FILL = PatternFill("solid", fgColor="D9E1F2")
LEVER_FILL = PatternFill("solid", fgColor="FFF2CC")
WRAP = Alignment(wrap_text=True, vertical="top")

MM = '#,##0.0;(#,##0.0);"-"'
PCT = '0.0%;(0.0%);"-"'
PRICE = '$#,##0.00'
MULT = '0.0"x"'
LEGEND = ("Legend: blue = input, black = formula, green = link to another tab, yellow fill = key lever. "
          "Navy headers = actual, teal headers = forecast.")


def _set(ws, ref, value, font=BLACK, fmt=None, fill=None, bold=False):
    cell = ws[ref]
    cell.value = value
    cell.font = Font(color=font.color, bold=bold or font.bold, italic=font.italic, size=font.size)
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    return cell


def _section(ws, row, text, width=8):
    for col in range(2, 2 + width):
        ws.cell(row=row, column=col).fill = SECTION_FILL
    _set(ws, f"B{row}", text, bold=True)


def _year_headers(ws, row, first_col, years):
    for i, y in enumerate(years):
        c = ws.cell(row=row, column=first_col + i, value=y)
        c.font = HEADER_FONT
        c.fill = FORECAST_FILL if y.endswith("E") else ACTUAL_FILL
        c.alignment = Alignment(horizontal="center")


def _pct(v):
    return None if v is None else v / 100.0


def _fill_forward(values):
    out, last = [], None
    for v in values:
        last = v if v is not None else last
        out.append(last)
    return out


MAP_SHEET = "_model_map"


def build_workbook(agent_name: str, ticker: str, research: dict, inputs: dict, outputs: dict,
                   track_record: list[dict], model_meta: dict | None = None) -> bytes:
    wb = Workbook()
    inmap: list[tuple] = []  # (sheet, cell, input path, kind) for every blue input cell

    def reg(ws, ref: str, path: str, kind: str = "num") -> None:
        """kind: num (as is) | pct (cell holds a fraction, input is a percent) | method (1/2) | flag (1/0)."""
        inmap.append((ws.title, ref, path, kind))

    years = inputs["fiscal_years"]
    n = len(years)
    first_e = next(i for i, y in enumerate(years) if y.endswith("E"))
    YC = 4  # first year column (D)
    col = {i: get_column_letter(YC + i) for i in range(n)}
    cur_col = col[first_e]

    # ---------------- Revenue Build ----------------
    rb = wb.active
    rb.title = "Revenue Build"
    _set(rb, "B2", f"{agent_name}: Revenue Build ($mm unless noted)", font=TITLE)
    _set(rb, "B3", LEGEND, font=NOTE)
    _year_headers(rb, 5, YC, years)
    _set(rb, "C5", "Unit", bold=True)
    notes_col = get_column_letter(YC + n + 1)
    _set(rb, f"{notes_col}5", "Logic / source", bold=True)
    row = 6
    seg_rev_rows = []
    for si, seg in enumerate(inputs["revenue_segments"]):
        _section(rb, row, f"{seg['name']}  ({seg['driver_logic']})", width=n + 3)
        row += 1
        for di, d in enumerate(seg["drivers"]):
            _set(rb, f"B{row}", d["name"])
            _set(rb, f"C{row}", d["unit"])
            for i, v in enumerate(d["values"]):
                if v is not None:
                    _set(rb, f"{col[i]}{row}", v, font=BLUE, fmt='#,##0.00')
                reg(rb, f"{col[i]}{row}", f"revenue_segments.{si}.drivers.{di}.values.{i}")
            _set(rb, f"{notes_col}{row}", d["logic"], font=NOTE)
            row += 1
        _set(rb, f"B{row}", f"{seg['name']} revenue", bold=True)
        _set(rb, f"C{row}", "$mm")
        for i, v in enumerate(seg["revenue_mm"]):
            if v is not None:
                _set(rb, f"{col[i]}{row}", v, font=BLUE, fmt=MM, fill=LEVER_FILL if years[i].endswith("E") else None)
            reg(rb, f"{col[i]}{row}", f"revenue_segments.{si}.revenue_mm.{i}")
        seg_rev_rows.append(row)
        row += 2
    total_row = row
    _set(rb, f"B{total_row}", "Total revenue", bold=True)
    for i in range(n):
        refs = ",".join(f"{col[i]}{r}" for r in seg_rev_rows)
        _set(rb, f"{col[i]}{total_row}", f"=SUM({refs})", fmt=MM, bold=True)
    # SOTP block
    row = total_row + 3
    _section(rb, row, f"Sum-of-the-parts ({years[first_e]} revenue x EV/Revenue)", width=6)
    row += 1
    for h, c in (("Segment", "B"), ("Revenue", "C"), ("EV/Revenue", "D"), ("EV ($mm)", "E"), ("Rationale", "F")):
        _set(rb, f"{c}{row}", h, bold=True)
    row += 1
    sotp_first = row
    for si, (seg, rev_row) in enumerate(zip(inputs["revenue_segments"], seg_rev_rows)):
        if not seg.get("sotp_ev_to_revenue"):
            continue
        _set(rb, f"B{row}", seg["name"])
        _set(rb, f"C{row}", f"={cur_col}{rev_row}", fmt=MM)
        _set(rb, f"D{row}", seg["sotp_ev_to_revenue"], font=BLUE, fmt=MULT, fill=LEVER_FILL)
        reg(rb, f"D{row}", f"revenue_segments.{si}.sotp_ev_to_revenue")
        _set(rb, f"E{row}", f"=C{row}*D{row}", fmt=MM)
        _set(rb, f"F{row}", seg["sotp_rationale"], font=NOTE)
        row += 1
    sotp_total = None
    if row > sotp_first:
        sotp_total = f"'Revenue Build'!E{row}"
        _set(rb, f"B{row}", "SOTP enterprise value", bold=True)
        _set(rb, f"E{row}", f"=SUM(E{sotp_first}:E{row - 1})", fmt=MM, bold=True)
    rb.column_dimensions["B"].width = 38
    rb.column_dimensions[notes_col].width = 70

    # ---------------- P&L ----------------
    pl = wb.create_sheet("P&L")
    _set(pl, "B2", f"{agent_name}: Annual P&L and cash flow ($mm)", font=TITLE)
    _set(pl, "B3", LEGEND, font=NOTE)
    _year_headers(pl, 5, YC, years)
    R = {"rev": 6, "growth": 7, "gm": 8, "gp": 9, "em": 10, "ebitda": 11, "sbcp": 12, "sbc": 13, "dap": 14, "da": 15,
         "ebit": 16, "tax": 17, "capexp": 18, "capex": 19, "nwc": 20, "fcf": 21, "fcfm": 22, "ufcf": 23}
    labels = {"rev": "Revenue", "growth": "  Growth, YoY %", "gm": "Gross margin %", "gp": "Gross profit",
              "em": "Adj. EBITDA margin %", "ebitda": "Adj. EBITDA", "sbcp": "SBC, % of revenue", "sbc": "Stock-based compensation",
              "dap": "D&A, % of revenue", "da": "Depreciation & amortization", "ebit": "EBIT (adj. EBITDA - SBC - D&A)",
              "tax": "Cash taxes", "capexp": "Capex incl. cap. software, % of revenue", "capex": "Capex",
              "nwc": "Change in net working capital", "fcf": "Free cash flow (adj. EBITDA - taxes - capex - NWC)",
              "fcfm": "  FCF margin %", "ufcf": "Unlevered FCF (less SBC if treated as cash)"}
    for key, r in R.items():
        _set(pl, f"B{r}", labels[key], bold=key in ("rev", "ebitda", "fcf", "ufcf"))
    # single-value assumptions
    A_TAX, A_NWC = 26, 27
    _section(pl, 25, "Model-wide assumptions", width=4)
    _set(pl, f"B{A_TAX}", "Cash tax rate (on positive EBIT)")
    _set(pl, f"C{A_TAX}", inputs["cash_tax_rate_pct"] / 100, font=BLUE, fmt=PCT, fill=LEVER_FILL)
    reg(pl, f"C{A_TAX}", "cash_tax_rate_pct", "pct")
    _set(pl, f"B{A_NWC}", "Change in NWC, % of change in revenue")
    _set(pl, f"C{A_NWC}", inputs["nwc_pct_of_revenue_change"] / 100, font=BLUE, fmt=PCT, fill=LEVER_FILL)
    reg(pl, f"C{A_NWC}", "nwc_pct_of_revenue_change", "pct")

    pct_inputs = {"gm": "gross_margin_pct", "em": "adj_ebitda_margin_pct", "sbcp": "sbc_pct_of_revenue",
                  "dap": "da_pct_of_revenue", "capexp": "capex_pct_of_revenue"}
    ff = {k: _fill_forward(inputs[v]) for k, v in pct_inputs.items()}
    for i in range(n):
        c, p = col[i], col[i - 1] if i else None
        is_e = years[i].endswith("E")
        _set(pl, f"{c}{R['rev']}", f"='Revenue Build'!{c}{total_row}", font=GREEN, fmt=MM, bold=True)
        if p:
            _set(pl, f"{c}{R['growth']}", f'=IF(OR({p}{R["rev"]}="",{p}{R["rev"]}=0),"",{c}{R["rev"]}/{p}{R["rev"]}-1)', fmt=PCT)
        for key, values in ff.items():
            if values[i] is not None:
                _set(pl, f"{c}{R[key]}", values[i] / 100, font=BLUE, fmt=PCT, fill=LEVER_FILL if is_e else None)
                reg(pl, f"{c}{R[key]}", f"{pct_inputs[key]}.{i}", "pct")
        _set(pl, f"{c}{R['gp']}", f'=IF({c}{R["gm"]}="","",{c}{R["rev"]}*{c}{R["gm"]})', fmt=MM)
        _set(pl, f"{c}{R['ebitda']}", f'=IF({c}{R["em"]}="","",{c}{R["rev"]}*{c}{R["em"]})', fmt=MM, bold=True)
        _set(pl, f"{c}{R['sbc']}", f'={c}{R["rev"]}*N({c}{R["sbcp"]})', fmt=MM)
        _set(pl, f"{c}{R['da']}", f'={c}{R["rev"]}*N({c}{R["dap"]})', fmt=MM)
        _set(pl, f"{c}{R['capex']}", f'={c}{R["rev"]}*N({c}{R["capexp"]})', fmt=MM)
        e = f"{c}{R['ebitda']}"
        _set(pl, f"{c}{R['ebit']}", f'=IF({e}="","",{e}-{c}{R["sbc"]}-{c}{R["da"]})', fmt=MM)
        _set(pl, f"{c}{R['tax']}", f'=IF({e}="","",MAX(0,{c}{R["ebit"]})*$C${A_TAX})', fmt=MM)
        delta = f"({c}{R['rev']}-{p}{R['rev']})" if p else "0"
        _set(pl, f"{c}{R['nwc']}", f'=IF({e}="","",{delta}*$C${A_NWC})', fmt=MM)
        _set(pl, f"{c}{R['fcf']}", f'=IF({e}="","",{e}-{c}{R["tax"]}-{c}{R["capex"]}-{c}{R["nwc"]})', fmt=MM, bold=True)
        _set(pl, f"{c}{R['fcfm']}", f'=IF({e}="","",{c}{R["fcf"]}/{c}{R["rev"]})', fmt=PCT)
        _set(pl, f"{c}{R['ufcf']}", f'=IF({e}="","",{c}{R["fcf"]}-{c}{R["sbc"]}*DCF!$C$10)', fmt=MM, bold=True)
    pl.column_dimensions["B"].width = 48

    # ---------------- Summary ----------------
    sm = wb.create_sheet("Summary", 0)
    d = research.get("details", {})
    cap_in = inputs["capitalization"]
    _set(sm, "B2", f"{agent_name} ({ticker}): Model Summary", font=TITLE)
    _set(sm, "B3", f"As of {research.get('date', date.today().isoformat())} · Stance: {str(d.get('stance', '')).title()} · "
                   f"Conviction: {research.get('conviction_level')}/10 · Valuation range: "
                   f"{(research.get('financial_model') or {}).get('valuation_range', 'n/a')}", font=NOTE)
    _section(sm, 5, "Capitalization ($mm)", width=6)
    cap_rows = [
        (6, "Current share price ($)", cap_in["share_price"], BLUE, PRICE),
        (7, "Diluted shares outstanding (mm)", cap_in["diluted_shares_mm"], BLUE, '#,##0.0'),
        (8, "Market capitalization", "=C6*C7", BLACK, MM),
        (9, "Total debt", cap_in["total_debt_mm"], BLUE, MM),
        (10, "Cash and investments", cap_in["cash_and_investments_mm"], BLUE, MM),
        (11, "Net debt", "=C9-C10", BLACK, MM),
        (12, "Other EV adjustments", cap_in["other_ev_adjustments_mm"], BLUE, MM),
        (13, "Enterprise value", "=C8+C11+C12", BLACK, MM),
        (14, f"EV / Revenue ({years[first_e]})", f"=C13/'P&L'!{cur_col}{R['rev']}", BLACK, MULT),
        (15, f"EV / Adj. EBITDA ({years[first_e]})",
         f"=IF(N('P&L'!{cur_col}{R['ebitda']})>0,C13/'P&L'!{cur_col}{R['ebitda']},\"NM\")", BLACK, MULT),
    ]
    cap_paths = {6: "share_price", 7: "diluted_shares_mm", 9: "total_debt_mm", 10: "cash_and_investments_mm", 12: "other_ev_adjustments_mm"}
    for r, label, value, font, fmt in cap_rows:
        _set(sm, f"B{r}", label)
        _set(sm, f"C{r}", value, font=font, fmt=fmt)
        if r in cap_paths:
            reg(sm, f"C{r}", f"capitalization.{cap_paths[r]}")
    _set(sm, "E6", cap_in["source_notes"], font=NOTE)
    sm["E6"].alignment = WRAP

    _section(sm, 17, "Valuation summary: football field", width=7)
    for h, c in (("Methodology", "B"), ("EV", "C"), ("Equity value", "D"), ("Per share", "E"), ("vs. current", "F"), ("Basis", "G")):
        _set(sm, f"{c}18", h, bold=True)

    # ---------------- DCF ----------------
    dc = wb.create_sheet("DCF")
    din = inputs["dcf"]
    _set(dc, "B2", f"{agent_name}: Discounted cash flow ($mm)", font=TITLE)
    _set(dc, "B3", LEGEND, font=NOTE)
    _section(dc, 4, "Assumptions", width=4)
    assumptions = [
        (5, "Valuation date", outputs["dcf"]["valuation_date"], "yyyy-mm-dd"),
        (6, "WACC", din["wacc_pct"] / 100, PCT),
        (7, "Terminal growth rate", din["terminal_growth_pct"] / 100, PCT),
        (8, "Exit multiple, EV / Adj. EBITDA", din["exit_multiple_ev_ebitda"], MULT),
        (9, "Terminal method (1 = perpetuity growth, 2 = exit multiple)", 1 if din["terminal_method"] == "perpetuity_growth" else 2, "0"),
        (10, "Treat SBC as a cash cost (1 = yes, 0 = no)", 1 if din["sbc_as_cash_cost"] else 0, "0"),
    ]
    for r, label, value, fmt in assumptions:
        _set(dc, f"B{r}", label)
        if r == 5:
            value = date.fromisoformat(value)
        _set(dc, f"C{r}", value, font=BLUE, fmt=fmt, fill=LEVER_FILL)
    reg(dc, "C6", "dcf.wacc_pct", "pct")
    reg(dc, "C7", "dcf.terminal_growth_pct", "pct")
    reg(dc, "C8", "dcf.exit_multiple_ev_ebitda")
    reg(dc, "C9", "dcf.terminal_method", "method")
    reg(dc, "C10", "dcf.sbc_as_cash_cost", "flag")
    _set(dc, "E6", din["rationale"], font=NOTE)
    dc["E6"].alignment = WRAP

    e_years = [i for i in range(n) if years[i].endswith("E")]
    _section(dc, 12, "Unlevered free cash flow projection (mid-year convention; current year prorated)", width=len(e_years) + 2)
    dcols = {i: get_column_letter(3 + k) for k, i in enumerate(e_years)}
    _year_headers(dc, 13, 3, [years[i] for i in e_years])
    for r, label in ((14, "Unlevered FCF"), (15, "Fraction of year after valuation date"), (16, "Years to year end"),
                     (17, "Discount period"), (18, "Discount factor"), (19, "PV of unlevered FCF")):
        _set(dc, f"B{r}", label)
    prev = None
    for i in e_years:
        c = dcols[i]
        y = int(years[i][:4])
        _set(dc, f"{c}14", f"='P&L'!{col[i]}{R['ufcf']}", font=GREEN, fmt=MM)
        _set(dc, f"{c}15", f"=MAX(0,MIN(1,(DATE({y},12,31)-$C$5)/365))", fmt='0.000')
        _set(dc, f"{c}16", f"={prev}16+{c}15" if prev else f"={c}15", fmt='0.000')
        _set(dc, f"{c}17", f"={c}16-0.5*{c}15", fmt='0.000')
        _set(dc, f"{c}18", f"=1/(1+$C$6)^{c}17", fmt='0.000')
        _set(dc, f"{c}19", f"={c}14*{c}15*{c}18", fmt=MM)
        prev = c
    last_c, first_c = dcols[e_years[-1]], dcols[e_years[0]]
    last_pl = col[e_years[-1]]
    _section(dc, 21, "Valuation", width=4)
    val_rows = [
        (22, "Sum of PV of explicit FCF", f"=SUM({first_c}19:{last_c}19)", MM),
        (23, "Terminal value (undiscounted)",
         f"=IF($C$9=1,{last_c}14*(1+$C$7)/($C$6-$C$7),$C$8*'P&L'!{last_pl}{R['ebitda']})", MM),
        (24, "PV of terminal value", f"=C23/(1+$C$6)^{last_c}16", MM),
        (25, "Implied enterprise value", "=C22+C24", MM),
        (26, "Less: net debt", "=-Summary!C11", MM),
        (27, "Less: other EV adjustments", "=-Summary!C12", MM),
        (28, "Implied equity value", "=C25+C26+C27", MM),
        (29, "Diluted shares (mm)", "=Summary!C7", '#,##0.0'),
        (30, "Implied value per share ($)", "=C28/C29", PRICE),
        (31, "Upside / (downside) vs current", "=C30/Summary!C6-1", PCT),
        (32, "Terminal value as % of EV", "=C24/C25", PCT),
    ]
    for r, label, formula, fmt in val_rows:
        _set(dc, f"B{r}", label, bold=r in (25, 30))
        _set(dc, f"C{r}", formula, font=GREEN if "Summary!" in formula else BLACK, fmt=fmt, bold=r in (25, 30))
    _set(dc, "E32", "Above ~75% means the answer is mostly the terminal assumption.", font=NOTE)

    row = 35
    for key, title, axis_key, axis_fmt in (
        ("wacc_vs_terminal_growth", "Sensitivity: WACC (rows) vs terminal growth (columns), perpetuity method", "terminal_growth_pct", PCT),
        ("wacc_vs_exit_multiple", "Sensitivity: WACC (rows) vs exit EV/EBITDA multiple (columns)", "exit_multiple", MULT),
    ):
        grid = outputs["sensitivities"][key]
        _section(dc, row, title + " - implied $/share (engine output, static)", width=7)
        row += 1
        for j, v in enumerate(grid[axis_key]):
            _set(dc, f"{get_column_letter(3 + j)}{row}", v / 100 if axis_fmt == PCT else v, fmt=axis_fmt, bold=True)
        for k, w in enumerate(grid["wacc_pct"]):
            _set(dc, f"B{row + 1 + k}", w / 100, fmt=PCT, bold=True)
            for j, price in enumerate(grid["prices"][k]):
                _set(dc, f"{get_column_letter(3 + j)}{row + 1 + k}", price, fmt=PRICE)
        row += len(grid["wacc_pct"]) + 3
    dc.column_dimensions["B"].width = 52
    dc.column_dimensions["C"].width = 14

    # ---------------- Scenarios ----------------
    sc = wb.create_sheet("Scenarios")
    _set(sc, "B2", f"{agent_name}: Scenario analysis", font=TITLE)
    _set(sc, "B3", "Bull and bear re-run the DCF on their own revenue growth and adj. EBITDA margin paths (blue = editable); "
                   "base is the segment build. Prices are engine outputs: upload your edits to recompute.", font=NOTE)
    e_labels = [years[i] for i in e_years]
    _section(sc, 5, "Scenario inputs and outputs", width=len(e_labels) + 3)
    row = 6
    sc_price_cells, sc_prob_cells, sc_rows = [], [], {}
    for s in outputs["scenarios"]:
        _set(sc, f"B{row}", s["name"].title(), bold=True)
        _set(sc, f"C{row}", "Probability")
        _set(sc, f"D{row}", s["probability_pct"] / 100, font=BLUE, fmt=PCT, fill=LEVER_FILL)
        reg(sc, f"D{row}", "scenarios.base_probability_pct" if s["name"] == "base" else f"scenarios.{s['name']}.probability_pct", "pct")
        sc_prob_cells.append(f"D{row}")
        _set(sc, f"E{row}", s["description"], font=NOTE)
        row += 1
        _year_headers(sc, row, 4, e_labels)
        row += 1
        for label, values, fmt, field in (("Revenue ($mm)", s["revenue_path"], MM, None),
                                          ("Revenue growth %", [_pct(v) for v in s["revenue_growth_pct"]], PCT, "revenue_growth_pct"),
                                          ("Adj. EBITDA margin %", [_pct(v) for v in s["adj_ebitda_margin_pct"]], PCT, "adj_ebitda_margin_pct")):
            _set(sc, f"C{row}", label)
            editable = field is not None and s["name"] in ("bull", "bear")  # base is the segment build itself
            for j, v in enumerate(values):
                ref = f"{get_column_letter(4 + j)}{row}"
                _set(sc, ref, v, fmt=fmt, font=BLUE if editable else BLACK, fill=LEVER_FILL if editable else None)
                if editable:
                    reg(sc, ref, f"scenarios.{s['name']}.{field}.{j}", "pct")
            row += 1
        for label, value, fmt in (("Enterprise value", s["enterprise_value"], MM), ("Equity value", s["equity_value"], MM),
                                  ("Implied value per share", s["implied_price"], PRICE)):
            _set(sc, f"C{row}", label)
            _set(sc, f"D{row}", value, fmt=fmt, bold=label.startswith("Implied"))
            if label.startswith("Implied"):
                sc_price_cells.append(f"D{row}")
            if label == "Enterprise value":
                sc_rows[s["name"]] = row
            row += 1
        _set(sc, f"C{row}", "Key drivers")
        _set(sc, f"D{row}", s["key_drivers"], font=NOTE)
        row += 2
    weighted_cell = f"D{row}"
    _set(sc, f"B{row}", "Probability-weighted value per share", bold=True)
    _set(sc, weighted_cell, "=" + "+".join(f"{p}*{v}" for p, v in zip(sc_prob_cells, sc_price_cells)), fmt=PRICE, bold=True)
    sc.column_dimensions["C"].width = 24
    sc.column_dimensions["E"].width = 14

    # ---------------- Comps ----------------
    cp = wb.create_sheet("Comps")
    comps = outputs["comps"]
    _set(cp, "B2", f"{agent_name}: Comparable companies", font=TITLE)
    _set(cp, "B3", "Bucketed by valuation logic, not industry label. Multiples are LTM from the market-data feed; "
                   "blank = not yet available.", font=NOTE)
    _set(cp, "B4", f"Primary bucket (how the market prices {ticker} today): {comps['primary_bucket']}. "
                   f"{inputs['comps']['rationale']}", font=NOTE)
    row = 6
    median_cells = {}
    for name, bucket in comps["buckets"].items():
        _section(cp, row, name + ("  [PRIMARY]" if name == comps["primary_bucket"] else ""), width=7)
        row += 1
        for h, c in (("Company", "B"), ("Ticker", "C"), ("Market value", "D"), ("Enterprise value", "E"),
                     ("EV / Revenue (LTM)", "F"), ("EV / EBITDA (LTM)", "G"), ("As of", "H")):
            _set(cp, f"{c}{row}", h, bold=True)
        row += 1
        first = row
        for p in bucket["peers"]:
            _set(cp, f"B{row}", p.get("name") or p["symbol"])
            _set(cp, f"C{row}", p["symbol"])
            for c, k, fmt in (("D", "market_cap_mm", MM), ("E", "enterprise_value_mm", MM),
                              ("F", "ev_to_revenue", MULT), ("G", "ev_to_ebitda", MULT)):
                if p.get(k) is not None:
                    _set(cp, f"{c}{row}", p[k], font=BLUE, fmt=fmt)
            _set(cp, f"H{row}", p.get("as_of") or "n/a", font=NOTE)
            row += 1
        last = row - 1
        for label, fn in (("Median", "MEDIAN"), ("Mean", "AVERAGE")):
            _set(cp, f"B{row}", label, bold=True)
            for c in ("F", "G"):
                _set(cp, f"{c}{row}", f'=IFERROR({fn}({c}{first}:{c}{last}),"na")', fmt=MULT, bold=True)
            if label == "Median":
                median_cells[name] = (f"Comps!F{row}", f"Comps!G{row}")
            row += 1
        row += 1
    cp.column_dimensions["B"].width = 34
    for c in "DEFG":
        cp.column_dimensions[c].width = 16

    # ---------------- Football field (needs DCF / Comps / Scenarios cells) ----------------
    ff_row = 19

    def ff(method, ev_formula, equity_formula, price_formula, basis, font=BLACK):
        nonlocal ff_row
        _set(sm, f"B{ff_row}", method)
        if ev_formula is not None:
            _set(sm, f"C{ff_row}", ev_formula, font=font, fmt=MM)
        _set(sm, f"D{ff_row}", equity_formula, font=font, fmt=MM)
        _set(sm, f"E{ff_row}", price_formula, font=font, fmt=PRICE, bold=True)
        _set(sm, f"F{ff_row}", f"=E{ff_row}/$C$6-1", fmt=PCT)
        _set(sm, f"G{ff_row}", basis, font=NOTE)
        ff_row += 1

    methods = {m["method"]: m for m in outputs["football_field"]}
    if "DCF" in methods:
        ff("DCF", "=DCF!C25", "=DCF!C28", "=DCF!C30", methods["DCF"]["basis"], font=GREEN)
    if comps["primary_bucket"] in median_cells:
        rev_med, ebitda_med = median_cells[comps["primary_bucket"]]
        if "Comps - EV/Revenue" in methods:
            r = ff_row
            ff("Comps - EV/Revenue", f"={rev_med}*'P&L'!{cur_col}{R['rev']}", f"=C{r}-$C$11-$C$12", f"=D{r}/$C$7",
               methods["Comps - EV/Revenue"]["basis"])
        if "Comps - EV/EBITDA" in methods:
            r = ff_row
            ff("Comps - EV/EBITDA", f"={ebitda_med}*'P&L'!{cur_col}{R['ebitda']}", f"=C{r}-$C$11-$C$12", f"=D{r}/$C$7",
               methods["Comps - EV/EBITDA"]["basis"])
    if sotp_total and "Sum-of-the-parts" in methods:
        r = ff_row
        ff("Sum-of-the-parts", f"={sotp_total}", f"=C{r}-$C$11-$C$12", f"=D{r}/$C$7", methods["Sum-of-the-parts"]["basis"])
    for s in outputs["scenarios"]:
        ev_row = sc_rows[s["name"]]
        ff(f"Scenario - {s['name']}", f"=Scenarios!D{ev_row}", f"=Scenarios!D{ev_row + 1}", f"=Scenarios!D{ev_row + 2}",
           f"{s['probability_pct']:.0f}% probability (engine output)", font=GREEN)
    ff("Probability-weighted", None, f"=E{ff_row}*$C$7", f"=Scenarios!{weighted_cell}", "Bull/base/bear x probabilities", font=GREEN)

    row = ff_row + 2
    _section(sm, row, "Thesis", width=7)
    row += 1
    for label, text in (("Executive summary", research.get("executive_summary")),
                        ("Conviction reasoning", research.get("reasoning")),
                        ("Key risks", "\n".join(f"- {r}" for r in research.get("key_risks", [])))):
        _set(sm, f"B{row}", label, bold=True)
        sm.merge_cells(f"C{row}:G{row}")
        _set(sm, f"C{row}", text or "")
        sm[f"C{row}"].alignment = WRAP
        sm.row_dimensions[row].height = max(45, min(400, 15 * (len(text or "") // 110 + 1)))
        row += 1
    sm.column_dimensions["B"].width = 34
    for c in "CDEF":
        sm.column_dimensions[c].width = 15
    sm.column_dimensions["G"].width = 60

    # ---------------- Model vs. Street ----------------
    ms = wb.create_sheet("Model vs. Street")
    np_ = inputs["next_print"]
    _set(ms, "B2", "Model vs. Street", font=TITLE)
    _set(ms, "B3", "Where the near-term numbers differ from guidance / consensus is often the edge into the next print. "
                   "Consensus is not in the data feed: fill the yellow cells to compute the variance.", font=NOTE)
    _section(ms, 5, f"Next print: {np_['period']}", width=6)
    for h, c in (("Metric", "B"), ("My estimate", "C"), ("Company guidance", "D"), ("Street consensus", "E"),
                 ("Variance vs. street", "F"), ("Why I differ", "G")):
        _set(ms, f"{c}6", h, bold=True)
    for r, (label, est, fmt) in enumerate((("Revenue ($mm)", np_["revenue_estimate_mm"], MM),
                                           ("Adj. EBITDA ($mm)", np_["adj_ebitda_estimate_mm"], MM),
                                           (np_["key_kpi_name"], np_["key_kpi_estimate"], None)), start=7):
        _set(ms, f"B{r}", label)
        _set(ms, f"C{r}", est, font=BLUE, fmt=fmt)
        _set(ms, f"E{r}", None, fill=LEVER_FILL)
        _set(ms, f"F{r}", f'=IFERROR(C{r}/E{r}-1,"-")', fmt=PCT)
    _set(ms, "D7", np_["company_guidance"], font=NOTE)
    _set(ms, "G7", np_["why_we_differ"], font=NOTE)
    ms["D7"].alignment = ms["G7"].alignment = WRAP
    ms.column_dimensions["B"].width = 30
    ms.column_dimensions["D"].width = 45
    ms.column_dimensions["G"].width = 60

    # ---------------- Historical Tracker ----------------
    ht = wb.create_sheet("Historical Tracker")
    _set(ht, "B2", "Historical Tracker: model vs. actual", font=TITLE)
    _set(ht, "B3", "Each run's next-quarter estimate, matched to reported results once filed. Marking the thesis to "
                   "market, not only looking forward.", font=NOTE)
    for h, c in (("Estimated on", "B"), ("Period", "C"), ("Metric", "D"), ("Estimate (pre-print)", "E"),
                 ("Actual reported", "F"), ("Variance", "G"), ("Company guidance at the time", "H")):
        _set(ht, f"{c}5", h, bold=True)
    for r, e in enumerate(track_record, start=6):
        _set(ht, f"B{r}", e["estimated_on"])
        _set(ht, f"C{r}", e["period"])
        _set(ht, f"D{r}", e["metric"])
        _set(ht, f"E{r}", e["estimate"], font=BLUE, fmt=MM)
        if e["actual"] is not None:
            _set(ht, f"F{r}", e["actual"], font=BLUE, fmt=MM)
            _set(ht, f"G{r}", f"=F{r}/E{r}-1", fmt=PCT)
        else:
            _set(ht, f"F{r}", "pending", font=NOTE)
        _set(ht, f"H{r}", e.get("company_guidance"), font=NOTE)
    if not track_record:
        _set(ht, "B6", "No estimates logged yet; the first run records its next-print estimate.", font=NOTE)
    ht.column_dimensions["H"].width = 60

    # ---------------- Notes ----------------
    nt = wb.create_sheet("Notes")
    _set(nt, "B2", "Key assumptions and notes", font=TITLE)
    row = 4
    for title, body in (("Key assumptions", "\n".join(f"- {a}" for a in inputs["key_assumptions"])),
                        ("Model notes", inputs["model_notes"]),
                        ("Capitalization sources", cap_in["source_notes"]),
                        ("DCF rationale", din["rationale"]),
                        ("Comps view", inputs["comps"]["rationale"]),
                        ("Legend", LEGEND)):
        _set(nt, f"B{row}", title, bold=True)
        _set(nt, f"C{row}", body)
        nt[f"C{row}"].alignment = WRAP
        nt.row_dimensions[row].height = max(30, min(400, 15 * (len(body) // 100 + body.count("\n") + 1)))
        row += 1
    nt.column_dimensions["B"].width = 24
    nt.column_dimensions["C"].width = 110

    meta = {"ticker": ticker, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **(model_meta or {})}
    _set(sm, "B4", f"Model version {meta.get('version', '?')} ({meta.get('source_label', 'agent')}). To change it: edit the blue cells "
                   "(Revenue Build, P&L, Summary capitalization, DCF assumptions, Scenarios), save, and upload in Analyst Town. "
                   "The engine recomputes everything else and the agents use your version from then on.", font=NOTE)
    mp = wb.create_sheet(MAP_SHEET)
    mp["A1"] = json.dumps(meta)
    mp.append(["sheet", "cell", "path", "kind"])
    for entry in inmap:
        mp.append(list(entry))
    mp.sheet_state = "hidden"

    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = False
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
