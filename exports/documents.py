"""PDF (memo) and CSV (model) downloads."""

import csv
import io
import re

from fpdf import FPDF

# Core PDF fonts are Latin-1 only; map common typography to ASCII-safe equivalents.
_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-",
    "…": "...", "•": "-", " ": " ", "≤": "<=", "≥": ">=", "→": "->", "←": "<-",
    "×": "x", "≈": "~", "↑": "up", "↓": "down", "·": "|",
})


def _latin1(text: str) -> str:
    return text.translate(_TRANSLATE).encode("latin-1", "replace").decode("latin-1")


def _strip_md(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)
    return re.sub(r"`(.+?)`", r"\1", text)


def memo_pdf(markdown: str, title: str) -> bytes:
    """Readable PDF of a markdown memo: headings, paragraphs, bullets and tables (as aligned text)."""
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()
    pdf.set_title(_latin1(title))
    width = pdf.w - pdf.l_margin - pdf.r_margin
    table: list[list[str]] = []

    def flush_table():
        if not table:
            return
        rows = [r for r in table if not all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in r)]
        cols = max(len(r) for r in rows)
        col_w = width / cols
        pdf.set_font("Helvetica", size=7.5)
        for i, row in enumerate(rows):
            pdf.set_font("Helvetica", "B" if i == 0 else "", 7.5)
            for c in range(cols):
                cell = _latin1(_strip_md(row[c].strip() if c < len(row) else ""))
                pdf.cell(col_w, 5, cell[: int(col_w / 1.6)], border="B" if i == 0 else 0)
            pdf.ln(5)
        pdf.ln(2)
        table.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            table.append(line.strip("|").split("|"))
            continue
        flush_table()
        if not line.strip() or line.strip() in ("---", "<!-- meeting -->"):
            pdf.ln(2)
            continue
        text = _latin1(_strip_md(line))
        if line.startswith("# "):
            pdf.set_font("Helvetica", "B", 16)
            pdf.multi_cell(width, 8, text[2:])
        elif line.startswith("## "):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 12.5)
            pdf.multi_cell(width, 7, text[3:])
        elif line.startswith("### "):
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(width, 6, text[4:])
        elif re.match(r"^(\s*[-*]|\s*\d+\.)\s", line):
            pdf.set_font("Helvetica", size=10)
            bullet = re.sub(r"^\s*[-*]\s", "- ", text)
            pdf.set_x(pdf.l_margin + 4)
            pdf.multi_cell(width - 4, 5.2, bullet)
        else:
            pdf.set_font("Helvetica", "I" if line.startswith("*") and line.endswith("*") else "", 10)
            pdf.multi_cell(width, 5.2, text)
    flush_table()
    return bytes(pdf.output())


def model_csv(ticker: str, outputs: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    rows = outputs["rows"]
    w.writerow([f"{ticker} financial model ($mm unless noted)"])
    w.writerow([])
    fields = [("revenue", "Revenue"), ("revenue_growth_pct", "Revenue growth %"), ("gross_margin_pct", "Gross margin %"),
              ("adj_ebitda", "Adj. EBITDA"), ("adj_ebitda_margin_pct", "Adj. EBITDA margin %"), ("sbc", "SBC"),
              ("da", "D&A"), ("ebit", "EBIT"), ("cash_taxes", "Cash taxes"), ("capex", "Capex"),
              ("change_nwc", "Change in NWC"), ("fcf", "Free cash flow"), ("fcf_margin_pct", "FCF margin %"),
              ("ufcf", "Unlevered FCF")]
    w.writerow(["Line item"] + [r["year"] for r in rows])
    for key, label in fields:
        w.writerow([label] + ["" if r.get(key) is None else round(r[key], 2) for r in rows])
    w.writerow([])
    w.writerow(["Valuation", "EV", "Equity value", "Per share", "Upside %", "Basis"])
    for m in outputs["football_field"]:
        w.writerow([m["method"]] + ["" if m.get(k) is None else round(m[k], 2) for k in
                                    ("enterprise_value", "equity_value", "implied_price", "upside_pct")] + [m["basis"]])
    w.writerow([])
    w.writerow(["Scenario", "Probability %", "Per share", "Description"])
    for s in outputs["scenarios"]:
        w.writerow([s["name"], s["probability_pct"], "" if s["implied_price"] is None else round(s["implied_price"], 2), s["description"]])
    return buf.getvalue()
