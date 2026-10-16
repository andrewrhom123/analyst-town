"""Edit cells inside an existing .xlsx without re-saving it through openpyxl.

openpyxl drops charts, images and other parts it doesn't understand when it saves a workbook, so the PM's
own workbook is patched at the XML level instead: only the touched cells (and the hidden model map) change,
everything else in the file is carried over byte for byte. Dependent formulas keep their stale cached values
until Excel recalculates, so edited workbooks are flagged to recalculate fully when opened.
"""

import io
import math
import posixpath
import re
import zipfile

from lxml import etree

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REF = re.compile(r"^([A-Z]{1,3})([1-9]\d{0,6})$")
# CT_Workbook child order: definedNames and calcPr must be inserted after these
_BEFORE_DEFINED = ("fileVersion", "fileSharing", "workbookPr", "AlternateContent", "workbookProtection", "bookViews",
                   "sheets", "functionGroups", "externalReferences")


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def split_ref(ref: str) -> tuple[str, int]:
    m = _REF.match(ref.replace("$", "").upper())
    if not m:
        raise ValueError(f"'{ref}' is not a cell reference")
    return m.group(1), int(m.group(2))


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def quote_sheet(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


class _Package:
    def __init__(self, data: bytes):
        self.zin = zipfile.ZipFile(io.BytesIO(data))
        self.parts: dict[str, bytes] = {}  # replaced parts
        self.removed: set[str] = set()

    def read(self, name: str) -> bytes:
        return self.parts[name] if name in self.parts else self.zin.read(name)

    def xml(self, name: str):
        return etree.fromstring(self.read(name))

    def put(self, name: str, root) -> None:
        self.parts[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def sheet_paths(self) -> dict[str, str]:
        wb = self.xml("xl/workbook.xml")
        rels = self.xml("xl/_rels/workbook.xml.rels")
        targets = {r.get("Id"): r.get("Target") for r in rels}
        out = {}
        for sh in wb.find(q("sheets")):
            target = targets.get(sh.get(f"{{{REL_NS}}}id"), "")
            out[sh.get("name")] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        return out

    def save(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in self.zin.infolist():
                if info.filename in self.removed:
                    continue
                zout.writestr(info, self.read(info.filename), compress_type=zipfile.ZIP_DEFLATED)
        return buf.getvalue()


def _set_cell(c, value) -> None:
    for child in list(c):
        if child.tag in (q("f"), q("v"), q("is")):
            c.remove(child)
    c.attrib.pop("t", None)
    if value is None:
        return
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, float) and not math.isfinite(value):
        return  # Excel has no NaN/inf: leave the cell empty
    if isinstance(value, (int, float)):
        v = etree.Element(q("v"))
        v.text = repr(float(value)) if isinstance(value, float) else str(value)
        c.insert(0, v)
    elif isinstance(value, str) and value.startswith("=") and len(value) > 1:
        f = etree.Element(q("f"))
        f.text = value[1:]
        c.insert(0, f)
    else:
        c.set("t", "inlineStr")
        is_ = etree.Element(q("is"))
        t = etree.SubElement(is_, q("t"))
        t.text = str(value)
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        c.insert(0, is_)


def _cell(sheet_data, ref: str):
    letters, r = split_ref(ref)
    ref = f"{letters}{r}"
    row = None
    for el in sheet_data.iterchildren(q("row")):
        n = int(el.get("r"))
        if n == r:
            row = el
            break
        if n > r:
            row = etree.Element(q("row"), r=str(r))
            el.addprevious(row)
            break
    if row is None:
        row = etree.SubElement(sheet_data, q("row"), r=str(r))
    row.attrib.pop("spans", None)  # an optimisation hint; stale once cells are added
    ci = col_index(letters)
    for c in row.iterchildren(q("c")):
        cr = c.get("r")
        if cr == ref:
            return c
        if cr and col_index(split_ref(cr)[0]) > ci:
            new = etree.Element(q("c"), r=ref)
            c.addprevious(new)
            return new
    return etree.SubElement(row, q("c"), r=ref)


def _map_sheet_xml(rows: list[list]):
    ws = etree.Element(q("worksheet"), nsmap={None: NS})
    sd = etree.SubElement(ws, q("sheetData"))
    for i, values in enumerate(rows, 1):
        row = etree.SubElement(sd, q("row"), r=str(i))
        for j, v in enumerate(values):
            if v is None or v == "":
                continue
            c = etree.SubElement(row, q("c"), r=f"{chr(65 + j)}{i}")
            _set_cell(c, v)
    return ws


def _insert_after(parent, el, before_tags: tuple[str, ...]) -> None:
    """Insert el after the last child whose local name is in before_tags (schema order)."""
    idx = 0
    for i, child in enumerate(parent):
        if isinstance(child.tag, str) and etree.QName(child).localname in before_tags:
            idx = i + 1
    parent.insert(idx, el)


def _drop_calc_chain(pkg: _Package) -> None:
    """Excel rebuilds the calculation chain; a stale one makes it 'repair' the file."""
    if "xl/calcChain.xml" not in pkg.zin.namelist():
        return
    pkg.removed.add("xl/calcChain.xml")
    rels = pkg.xml("xl/_rels/workbook.xml.rels")
    for r in list(rels):
        if (r.get("Target") or "").endswith("calcChain.xml"):
            rels.remove(r)
    pkg.put("xl/_rels/workbook.xml.rels", rels)
    ct = pkg.xml("[Content_Types].xml")
    for o in list(ct):
        if (o.get("PartName") or "").endswith("calcChain.xml"):
            ct.remove(o)
    pkg.put("[Content_Types].xml", ct)


def patch(data: bytes, cells: dict[tuple[str, str], object] | None = None, map_sheet: str | None = None,
          map_rows: list[list] | None = None, names: dict[str, str] | None = None, name_prefix: str = "") -> bytes:
    """Return the workbook with `cells` {(sheet, "D8"): value} written (number, text, "=formula" or None),
    the sheet `map_sheet` replaced by `map_rows`, and defined names starting with `name_prefix` replaced
    by `names` {name: "'Sheet'!$D$8"}. Writing cells flags the workbook to fully recalculate on open."""
    pkg = _Package(data)
    paths = pkg.sheet_paths()
    by_sheet: dict[str, list] = {}
    for (sheet, ref), value in (cells or {}).items():
        if sheet not in paths:
            raise ValueError(f"No sheet named '{sheet}'")
        by_sheet.setdefault(sheet, []).append((ref, value))
    for sheet, edits in by_sheet.items():
        root = pkg.xml(paths[sheet])
        sd = root.find(q("sheetData"))
        for ref, value in edits:
            _set_cell(_cell(sd, ref), value)
        pkg.put(paths[sheet], root)
    if map_sheet and map_rows is not None:
        if map_sheet not in paths:
            raise ValueError(f"No sheet named '{map_sheet}'")
        pkg.put(paths[map_sheet], _map_sheet_xml(map_rows))
    if names is not None or by_sheet:
        wb = pkg.xml("xl/workbook.xml")
        if names is not None:
            dn = wb.find(q("definedNames"))
            if dn is None:
                dn = etree.Element(q("definedNames"))
                _insert_after(wb, dn, _BEFORE_DEFINED)
            for el in list(dn):
                if name_prefix and (el.get("name") or "").startswith(name_prefix):
                    dn.remove(el)
            for name, ref in names.items():
                el = etree.SubElement(dn, q("definedName"), name=name)
                el.text = ref
            if not len(dn):
                wb.remove(dn)
        if by_sheet:
            calc = wb.find(q("calcPr"))
            if calc is None:
                calc = etree.Element(q("calcPr"))
                _insert_after(wb, calc, _BEFORE_DEFINED + ("definedNames",))
            calc.set("fullCalcOnLoad", "1")
            _drop_calc_chain(pkg)
        pkg.put("xl/workbook.xml", wb)
    return pkg.save()
