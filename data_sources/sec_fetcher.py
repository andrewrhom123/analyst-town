"""SEC EDGAR fetcher (free, no API key; requires a descriptive User-Agent).

Pulls for a ticker:
  * the latest annual report (10-K / 20-F / 40-F, or the IPO prospectus for newly listed companies)
    split into Business, Risk Factors, MD&A and Financial Statements sections
  * the latest quarterly report (10-Q) MD&A
  * the latest earnings press release (8-K Item 2.02 / 6-K, exhibit 99.x) - the free stand-in
    for an earnings call transcript
  * XBRL "company facts": revenue, margins, cash flow, share count time series
Results are cached in DataCache for SEC_CACHE_TTL (1 week by default).
"""

import logging
import re
import threading
import time
from datetime import date
from html.parser import HTMLParser

import requests

from data_sources.cache import cached_fetch
from utils.config import SEC_CACHE_TTL, SEC_SECTION_CHAR_LIMIT, SEC_USER_AGENT

logger = logging.getLogger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{index}"

ANNUAL_FORMS = ["10-K", "20-F", "40-F"]
PROSPECTUS_FORMS = ["424B4", "S-1", "F-1", "S-1/A", "F-1/A"]
QUARTERLY_FORMS = ["10-Q"]

_lock = threading.Lock()
_last_request = 0.0


class SECError(Exception):
    pass


def _get(url: str) -> requests.Response:
    """GET with SEC fair-access throttling (< 10 requests/second)."""
    global _last_request
    if not SEC_USER_AGENT:
        raise SECError("SEC_USER_AGENT is not set (SEC requires 'Name email@domain' as User-Agent)")
    with _lock:
        wait = 0.15 - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    resp.raise_for_status()
    return resp


# --- HTML -> text -----------------------------------------------------------

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "ix:header", "head"}
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- Section extraction -----------------------------------------------------

_SEP = r"\.?\s*[\-–—:]?\s*"
SECTION_PATTERNS = {
    "10-K": {
        "business": (rf"item\s*1{_SEP}business", rf"item\s*1a{_SEP}risk\s+factors"),
        "risk_factors": (rf"item\s*1a{_SEP}risk\s+factors", rf"item\s*1b|item\s*1c|item\s*2{_SEP}properties"),
        "mdna": (rf"item\s*7{_SEP}management.{{0,3}}s\s+discussion", rf"item\s*7a|item\s*8{_SEP}financial\s+statements"),
        "financial_statements": (rf"item\s*8{_SEP}financial\s+statements", rf"item\s*9{_SEP}changes\s+in|item\s*9a"),
    },
    "10-Q": {
        "mdna": (rf"item\s*2{_SEP}management.{{0,3}}s\s+discussion", rf"item\s*3{_SEP}quantitative|item\s*4{_SEP}controls"),
        "risk_factors": (rf"item\s*1a{_SEP}risk\s+factors", rf"item\s*2{_SEP}unregistered|item\s*5{_SEP}other|item\s*6{_SEP}exhibits"),
    },
    "20-F": {
        "business": (rf"item\s*4{_SEP}information\s+on\s+the\s+company", rf"item\s*4a|item\s*5{_SEP}operating"),
        "risk_factors": (r"\n\s*(?:d\.\s*)?risk\s+factors\s*\n", rf"item\s*4{_SEP}information\s+on\s+the\s+company"),
        "mdna": (rf"item\s*5{_SEP}operating\s+and\s+financial\s+review", rf"item\s*6{_SEP}directors"),
    },
    # 40-F and IPO prospectuses use un-numbered headings.
    "generic": {
        "business": (r"\n\s*(?:our\s+)?business\s*\n", r"\n\s*management\s*\n|\n\s*executive\s+compensation\s*\n|\n\s*properties\s*\n"),
        "risk_factors": (r"\n\s*risk\s+factors\s*\n", r"\n\s*(?:special\s+note|cautionary\s+note|use\s+of\s+proceeds)"),
        "mdna": (r"\n\s*management.{0,3}s\s+discussion\s+and\s+analysis", r"\n\s*(?:our\s+)?business\s*\n|\n\s*quantitative\s+and\s+qualitative"),
        "financial_statements": (r"\n\s*(?:index\s+to\s+)?(?:consolidated\s+)?financial\s+statements\s*\n", r"\n\s*signatures\s*\n"),
    },
}


def _longest_span(text: str, start_pat: str, end_pat: str) -> str:
    """The table of contents also matches section headings, so keep the longest start->end span."""
    best = ""
    end_re = re.compile(end_pat, re.IGNORECASE)
    for m in re.finditer(start_pat, text, re.IGNORECASE):
        end = end_re.search(text, m.end())
        chunk = text[m.start(): end.start() if end else min(len(text), m.start() + SEC_SECTION_CHAR_LIMIT * 3)]
        if len(chunk) > len(best):
            best = chunk
    return best


def _truncate(text: str, limit: int = SEC_SECTION_CHAR_LIMIT) -> dict:
    return {
        "text": text[:limit],
        "total_chars": len(text),
        "truncated": len(text) > limit,
    }


def extract_sections(text: str, form: str) -> dict:
    family = next((f for f in ("10-K", "10-Q", "20-F") if form.startswith(f)), "generic")
    patterns = dict(SECTION_PATTERNS[family])
    if family == "20-F":  # 20-F financial statements usually sit after Item 19 with un-numbered headings
        patterns.setdefault("financial_statements", SECTION_PATTERNS["generic"]["financial_statements"])
    sections = {}
    for name, (start, end) in patterns.items():
        chunk = _longest_span(text, start, end)
        if len(chunk) > 500:  # anything shorter is a TOC line, not a section
            sections[name] = _truncate(chunk)
    if not sections:
        sections["document_excerpt"] = _truncate(text)
    return sections


# --- EDGAR lookups ----------------------------------------------------------

def _ticker_map() -> dict:
    def fetch():
        raw = _get(TICKER_MAP_URL).json()
        return {row["ticker"].upper(): {"cik": row["cik_str"], "name": row["title"]} for row in raw.values()}

    return cached_fetch("sec_tickers", "ALL", SEC_CACHE_TTL, fetch)


def lookup_cik(ticker: str) -> tuple[int, str]:
    entry = _ticker_map().get(ticker.upper())
    if not entry:
        raise SECError(f"Ticker {ticker} not found in SEC EDGAR")
    return int(entry["cik"]), entry["name"]


def _recent_filings(submissions: dict) -> list[dict]:
    recent = submissions.get("filings", {}).get("recent", {})
    keys = ["form", "accessionNumber", "filingDate", "reportDate", "primaryDocument", "items"]
    columns = [recent.get(k, []) for k in keys]
    return [dict(zip(keys, row)) for row in zip(*columns)]  # newest first


def _first(filings: list[dict], forms: list[str], predicate=lambda f: True) -> dict | None:
    return next((f for f in filings if f["form"] in forms and predicate(f)), None)


def _filing_url(cik: int, filing: dict, document: str | None = None) -> str:
    return ARCHIVE_URL.format(
        cik=cik,
        accession=filing["accessionNumber"].replace("-", ""),
        document=document or filing["primaryDocument"],
    )


def _fetch_document(cik: int, filing: dict) -> dict:
    url = _filing_url(cik, filing)
    text = html_to_text(_get(url).text)
    return {
        "form": filing["form"],
        "filing_date": filing["filingDate"],
        "report_date": filing.get("reportDate"),
        "url": url,
        "sections": extract_sections(text, filing["form"]),
    }


_EXHIBIT_ROW = re.compile(r'<a href="[^"]*/([^"/]+\.html?)">[^<]*</a>\s*</td>\s*<td[^>]*>\s*(EX-99[^<]*)</td>', re.I)
_RESULTS_TEXT = re.compile(r"financial results|results of operations|(first|second|third|fourth) quarter|quarter ended", re.I)


def _exhibit_99_documents(cik: int, filing: dict) -> list[str]:
    """EX-99.x HTML documents of a filing, read from the filing index page (file names vary by agent)."""
    accession = filing["accessionNumber"]
    index_url = FILING_INDEX_URL.format(cik=cik, accession=accession.replace("-", ""), index=f"{accession}-index.html")
    return [name for name, _type in _EXHIBIT_ROW.findall(_get(index_url).text)]


def _fetch_earnings_release(cik: int, filings: list[dict]) -> dict | None:
    """Latest 8-K Item 2.02 press release; for foreign filers, the latest 6-K exhibit that reads like results."""
    candidates = [f for f in filings if f["form"] == "8-K" and "2.02" in (f.get("items") or "")][:1]
    if not candidates:
        candidates = [f for f in filings if f["form"] == "6-K"][:5]
    for filing in candidates:
        for exhibit in _exhibit_99_documents(cik, filing):
            url = _filing_url(cik, filing, exhibit)
            text = html_to_text(_get(url).text)
            if filing["form"] == "8-K" or _RESULTS_TEXT.search(text[:5000]):
                return {"form": filing["form"], "filing_date": filing["filingDate"], "url": url, **_truncate(text)}
    return None


# --- XBRL financial facts ---------------------------------------------------

REVENUE_CONCEPTS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "Revenue",  # ifrs-full
]
METRIC_CONCEPTS = {
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "rnd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": ["SellingGeneralAndAdministrativeExpense"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "stock_comp": ["ShareBasedCompensation"],
}


_ANNUAL_FRAME = re.compile(r"^CY\d{4}$")
_QUARTER_FRAME = re.compile(r"^CY\d{4}Q[1-4]$")


def _series(facts: dict, concepts: list[str], quarterly: bool) -> dict[str, float]:
    """{frame: value} for the concept with the most recent data. Frames look like CY2024 / CY2024Q3.

    Companies rarely tag Q4 on its own (it is only in the 10-K), so a missing Q4 is derived as
    full year minus Q1-Q3 - valid because every metric here is a flow over the period.
    """
    best: dict[str, float] = {}
    best_latest = ""
    for taxonomy in ("us-gaap", "ifrs-full"):
        for concept in concepts:
            units = facts.get(taxonomy, {}).get(concept, {}).get("units", {})
            rows = units.get("USD") or next(iter(units.values()), [])
            annual = {r["frame"]: r["val"] for r in rows if _ANNUAL_FRAME.match(r.get("frame", ""))}
            if quarterly:
                values = {r["frame"]: r["val"] for r in rows if _QUARTER_FRAME.match(r.get("frame", ""))}
                for year, total in annual.items():
                    q = [values.get(f"{year}Q{i}") for i in (1, 2, 3)]
                    if f"{year}Q4" not in values and None not in q:
                        values[f"{year}Q4"] = total - sum(q)
            else:
                values = annual
            if values and max(values) > best_latest:
                best, best_latest = values, max(values)
    return best


def _pct(num: float | None, den: float | None) -> float | None:
    if num is None or not den:
        return None
    return round(100.0 * num / den, 2)


def _build_table(facts: dict, quarterly: bool, periods: int) -> list[dict]:
    revenue = _series(facts, REVENUE_CONCEPTS, quarterly)
    metrics = {name: _series(facts, concepts, quarterly) for name, concepts in METRIC_CONCEPTS.items()}
    rows = []
    for frame in sorted(revenue)[-periods:]:
        rev = revenue[frame]
        row = {"period": frame, "revenue": rev}
        for name, series in metrics.items():
            row[name] = series.get(frame)
        if row["gross_profit"] is None and row["cost_of_revenue"] is not None:
            row["gross_profit"] = rev - row["cost_of_revenue"]
        prior_frame = f"CY{int(frame[2:6]) - 1}{frame[6:]}"  # same period last year
        row["revenue_growth_yoy_pct"] = _pct(rev - revenue[prior_frame], revenue[prior_frame]) if prior_frame in revenue else None
        row["gross_margin_pct"] = _pct(row["gross_profit"], rev)
        row["operating_margin_pct"] = _pct(row["operating_income"], rev)
        row["net_margin_pct"] = _pct(row["net_income"], rev)
        if row["operating_cash_flow"] is not None:
            row["free_cash_flow"] = row["operating_cash_flow"] - (row["capex"] or 0)
            row["fcf_margin_pct"] = _pct(row["free_cash_flow"], rev)
        rows.append(row)
    return rows


def _fetch_financials(cik: int) -> dict:
    try:
        facts = _get(COMPANY_FACTS_URL.format(cik=cik)).json().get("facts", {})
    except requests.HTTPError as exc:  # very new registrants may have no XBRL yet
        return {"available": False, "error": str(exc)}
    return {
        "available": True,
        "annual": _build_table(facts, quarterly=False, periods=5),
        "quarterly": _build_table(facts, quarterly=True, periods=8),
    }


# --- Public API -------------------------------------------------------------

def _fetch_all(ticker: str) -> dict:
    cik, company_name = lookup_cik(ticker)
    filings = _recent_filings(_get(SUBMISSIONS_URL.format(cik=cik)).json())

    annual_filing = _first(filings, ANNUAL_FORMS) or _first(filings, PROSPECTUS_FORMS)
    quarterly_filing = _first(filings, QUARTERLY_FORMS)
    # Only include the 10-Q when it is newer than the annual report.
    if annual_filing and quarterly_filing and quarterly_filing["filingDate"] < annual_filing["filingDate"]:
        quarterly_filing = None

    result = {
        "available": True,
        "ticker": ticker.upper(),
        "cik": cik,
        "company_name": company_name,
        "fetched": date.today().isoformat(),
        "recent_filings": [
            {"form": f["form"], "filing_date": f["filingDate"], "items": f.get("items")} for f in filings[:15]
        ],
        "annual_report": None,
        "quarterly_report": None,
        "earnings_release": None,
        "financials": _fetch_financials(cik),
        "errors": [],
    }
    for key, loader in (
        ("annual_report", lambda: annual_filing and _fetch_document(cik, annual_filing)),
        ("quarterly_report", lambda: quarterly_filing and _fetch_document(cik, quarterly_filing)),
        ("earnings_release", lambda: _fetch_earnings_release(cik, filings)),
    ):
        try:
            result[key] = loader() or None
        except Exception as exc:  # one bad document should not sink the rest
            logger.warning("SEC %s for %s failed: %s", key, ticker, exc)
            result["errors"].append(f"{key}: {exc}")
    return result


MATERIAL_FORMS = {"10-K", "10-Q", "20-F", "40-F", "6-K", "424B4", "S-1", "F-1"}


def latest_material_filing(ticker: str) -> dict | None:
    """Newest filing that should trigger a deep dive: periodic reports, prospectuses, earnings 8-Ks.

    One uncached SEC request (free). Used by the filing watcher.
    """
    cik, _ = lookup_cik(ticker)
    filings = _recent_filings(_get(SUBMISSIONS_URL.format(cik=cik)).json())
    for f in filings:
        if f["form"] in MATERIAL_FORMS or (f["form"] == "8-K" and "2.02" in (f.get("items") or "")):
            return {"form": f["form"], "accession": f["accessionNumber"], "filing_date": f["filingDate"],
                    "items": f.get("items")}
    return None


def invalidate_sec_cache(ticker: str) -> None:
    from database.db import session_scope
    from database.models import DataCache

    with session_scope() as s:
        s.query(DataCache).filter(DataCache.data_type == "sec", DataCache.ticker == ticker.upper()).delete()


def get_sec_data(ticker: str) -> dict:
    """Structured SEC data for a ticker. Never raises: failures come back as {'available': False, ...}."""
    try:
        return cached_fetch("sec", ticker.upper(), SEC_CACHE_TTL, lambda: _fetch_all(ticker))
    except Exception as exc:
        logger.warning("SEC data unavailable for %s: %s", ticker, exc)
        return {"available": False, "ticker": ticker.upper(), "error": str(exc)}
