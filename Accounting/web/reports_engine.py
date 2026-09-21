"""
Report Builder engine — run a definition and render the result.

    result = run_report(defn, company_id)             # ReportResult
    render_csv(result) / render_xlsx(result, meta) / render_pdf(result, meta) / render_html(result, meta)
    path = save_output(content, report_name, "pdf")   # under <tempdir>/ebms_reports/

PDF: uses reportlab when it is importable, otherwise a small built-in PDF
writer (Helvetica, landscape A4, repeating header, page numbers) so scheduled
reports never depend on a package that may be missing in production.
"""
from __future__ import annotations

import csv
import html as _html
import io
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from reports_catalog import (NUMBER, DATE, DATETIME, DATETEXT, BOOL, SOURCES, CatalogError,
                             OutCol, compile_query, preset_label)

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(tempfile.gettempdir(), "ebms_reports")
PREVIEW_ROWS = 100
MIME = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "html": "text/html",
}


@dataclass
class ReportResult:
    columns: List[OutCol]
    rows: List[dict]
    totals: Dict[str, float] = field(default_factory=dict)
    row_count: int = 0
    truncated: bool = False
    filters_summary: str = ""
    date_range: Optional[tuple] = None
    dropped: List[str] = field(default_factory=list)
    sql: str = ""

    def to_json(self, max_rows: Optional[int] = None) -> dict:
        rows = self.rows if max_rows is None else self.rows[:max_rows]
        return {
            "columns": [{"key": c.key, "label": c.label, "type": c.type} for c in self.columns],
            "rows": [{c.key: _jsonable(r.get(c.key)) for c in self.columns} for r in rows],
            "totals": {k: _jsonable(v) for k, v in self.totals.items()},
            "row_count": self.row_count,
            "truncated": self.truncated,
            "filters_summary": self.filters_summary,
            "dropped": self.dropped,
        }


# ── helpers ─────────────────────────────────────────────────────────

def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _num(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def format_cell(value, col_type: str) -> str:
    """Human display of a cell (HTML/PDF/CSV)."""
    if value is None:
        return ""
    if col_type == NUMBER:
        n = _num(value)
        if n is None:
            return str(value)
        if n == int(n) and abs(n) < 1e15 and isinstance(value, (int,)) and not isinstance(value, bool):
            return f"{int(n):,}"
        return f"{n:,.2f}"
    if col_type == BOOL:
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def company_name(company_id: str) -> str:
    try:
        from tenant_data_store import tenant_store
        t = tenant_store.get_tenant(company_id)
        if t and t.get("company_name"):
            return str(t["company_name"])
    except Exception as e:
        logger.debug("company_name(%s): %s", company_id, e)
    return "Default Company" if company_id == "default" else company_id


def build_meta(defn: dict, company_id: str, result: ReportResult, generated_at: datetime = None) -> dict:
    source = SOURCES.get(defn.get("source_key") or "")
    return {
        "report_name": defn.get("name") or "Report",
        "description": defn.get("description") or "",
        "company_name": company_name(company_id),
        "source_label": source.label if source else defn.get("source_key", ""),
        "generated_at": (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        "filters_summary": result.filters_summary or "No filters",
        "date_preset": preset_label(defn.get("date_preset")),
        "row_count": result.row_count,
        "truncated": result.truncated,
    }


def safe_filename(name: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "report")).strip("_")[:60] or "report"
    return f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"


def save_output(content: bytes, report_name: str, ext: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, safe_filename(report_name, ext))
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def chart_data(result: ReportResult, chart: Optional[dict], max_points: int = 60) -> Optional[dict]:
    if not chart or not chart.get("x") or not chart.get("y"):
        return None
    keys = {c.key for c in result.columns}
    if chart["x"] not in keys or chart["y"] not in keys:
        return None
    rows = result.rows[:max_points]
    labels = [format_cell(r.get(chart["x"]), "text") for r in rows]
    values = [_num(r.get(chart["y"])) or 0 for r in rows]
    y_label = next((c.label for c in result.columns if c.key == chart["y"]), chart["y"])
    return {"type": chart["type"], "labels": labels, "values": values, "label": y_label}


# ── run ─────────────────────────────────────────────────────────────

def run_report(defn: dict, company_id: str, limit: Optional[int] = None,
               today: Optional[date] = None) -> ReportResult:
    """Compile + execute a definition for one company. Raises CatalogError on bad definitions."""
    from db import get_conn
    from reports_data_store import report_store

    source = SOURCES.get(str(defn.get("source_key") or ""))
    if source is None:
        raise CatalogError(f"unknown source {defn.get('source_key')!r}")
    available = report_store.table_columns(source.table)
    cq = compile_query(defn, company_id, today=today, available=available, limit=limit)

    rows: List[dict] = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(cq.sql, tuple(cq.params))
            rows = [dict(r) for r in cur.fetchall()]

    eff_limit = cq.params[-1]
    totals: Dict[str, float] = {}
    for c in cq.columns:
        if c.type == NUMBER and c.agg not in ("avg", "min", "max"):
            s = 0.0
            seen = False
            for r in rows:
                n = _num(r.get(c.key))
                if n is not None:
                    s += n; seen = True
            if seen:
                totals[c.key] = round(s, 2)
    return ReportResult(columns=cq.columns, rows=rows, totals=totals, row_count=len(rows),
                        truncated=len(rows) >= eff_limit, filters_summary=cq.filters_summary,
                        date_range=cq.date_range, dropped=cq.dropped, sql=cq.sql)


# ── CSV ─────────────────────────────────────────────────────────────

def render_csv(result: ReportResult) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c.label for c in result.columns])
    for r in result.rows:
        w.writerow([_csv_cell(r.get(c.key), c.type) for c in result.columns])
    if result.totals:
        w.writerow([("Total" if i == 0 else _csv_cell(result.totals.get(c.key), NUMBER) if c.key in result.totals else "")
                    for i, c in enumerate(result.columns)])
    import codecs
    return codecs.BOM_UTF8 + buf.getvalue().encode("utf-8")   # BOM so Excel opens UTF-8 correctly


def _csv_cell(v, col_type):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if col_type == NUMBER:
        if isinstance(v, int):
            return v
        if isinstance(v, Decimal):
            return int(v) if v == v.to_integral_value() else float(v)
        n = _num(v)
        return n if n is not None else v
    return _jsonable(v)


# ── Excel ───────────────────────────────────────────────────────────

def render_xlsx(result: ReportResult, meta: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = (meta.get("report_name") or "Report")[:28].replace("/", "-").replace("\\", "-") or "Report"

    bold = Font(bold=True)
    ws["A1"] = meta.get("company_name", ""); ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = meta.get("report_name", ""); ws["A2"].font = Font(bold=True, size=11)
    ws["A3"] = f"Generated {meta.get('generated_at', '')} — {meta.get('filters_summary', '')}"
    ws["A3"].font = Font(italic=True, color="666666")
    header_row = 5

    fill = PatternFill("solid", fgColor="DDE7F3")
    for i, c in enumerate(result.columns, start=1):
        cell = ws.cell(row=header_row, column=i, value=c.label)
        cell.font = bold; cell.fill = fill
        cell.alignment = Alignment(horizontal="right" if c.type == NUMBER else "left")

    for ri, r in enumerate(result.rows, start=header_row + 1):
        for ci, c in enumerate(result.columns, start=1):
            v = r.get(c.key)
            if c.type == NUMBER:
                n = _num(v)
                cell = ws.cell(row=ri, column=ci, value=n if n is not None else (None if v is None else str(v)))
                if n is not None:
                    cell.number_format = "#,##0" if c.agg == "count" else "#,##0.00"
            elif c.type == BOOL:
                ws.cell(row=ri, column=ci, value=("Yes" if v else "No") if v is not None else None)
            elif isinstance(v, Decimal):
                ws.cell(row=ri, column=ci, value=float(v))
            elif isinstance(v, datetime):
                ws.cell(row=ri, column=ci, value=v.replace(tzinfo=None)).number_format = "yyyy-mm-dd hh:mm"
            elif isinstance(v, date):
                ws.cell(row=ri, column=ci, value=v).number_format = "yyyy-mm-dd"
            else:
                ws.cell(row=ri, column=ci, value=None if v is None else str(v))

    last = header_row + len(result.rows)
    if result.totals:
        tr = last + 1
        ws.cell(row=tr, column=1, value="Total").font = bold
        for ci, c in enumerate(result.columns, start=1):
            if c.key in result.totals:
                cell = ws.cell(row=tr, column=ci, value=result.totals[c.key])
                cell.font = bold; cell.number_format = "#,##0.00"

    if result.columns:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(result.columns))}{max(last, header_row)}"
        ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
        for ci, c in enumerate(result.columns, start=1):
            longest = max([len(c.label)] + [len(format_cell(r.get(c.key), c.type)) for r in result.rows[:500]])
            ws.column_dimensions[get_column_letter(ci)].width = min(max(10, longest + 2), 50)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ── HTML (view / print) ─────────────────────────────────────────────

def render_html(result: ReportResult, meta: dict, chart: Optional[dict] = None, max_rows: int = 2000) -> str:
    """Render the printable report page (templates/reports/pdf_report.html)."""
    from template_engine import templates
    tpl = templates.env.get_template("reports/pdf_report.html")
    return tpl.render(meta=meta, result=result, rows=result.rows[:max_rows], format_cell=format_cell,
                      chart=chart_data(result, chart) if chart else None, NUMBER=NUMBER)


# ── PDF ─────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = 841.89, 595.28   # A4 landscape (points)
MARGIN = 28.0


def _col_widths(result: ReportResult, usable: float, sample: int = 300) -> List[float]:
    weights = []
    for c in result.columns:
        longest = max([len(c.label)] + [len(format_cell(r.get(c.key), c.type)) for r in result.rows[:sample]] or [4])
        weights.append(min(max(longest, 4), 40))
    total = float(sum(weights)) or 1.0
    return [usable * w / total for w in weights]


def render_pdf(result: ReportResult, meta: dict, force_builtin: bool = False) -> bytes:
    if not force_builtin:
        try:
            import reportlab  # noqa: F401
            return _render_pdf_reportlab(result, meta)
        except ImportError:
            pass
        except Exception as e:  # never let a renderer bug kill a scheduled run
            logger.warning("reportlab render failed (%s) — using built-in writer", e)
    return _render_pdf_builtin(result, meta)


def _render_pdf_reportlab(result: ReportResult, meta: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    class _NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__)); self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFont("Helvetica", 7.5)
                self.setFillColor(colors.HexColor("#666666"))
                self.drawString(MARGIN, 14, f"{meta.get('company_name', '')} — {meta.get('report_name', '')}")
                self.drawRightString(PAGE_W - MARGIN, 14, f"Page {self._pageNumber} of {total}")
                super().showPage()
            super().save()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN, title=meta.get("report_name", "Report"),
                            author=meta.get("company_name", "EBMS"))
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#555555"))
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7.5, leading=9)
    story = [Paragraph(_html.escape(meta.get("company_name", "")), styles["Title"]),
             Paragraph(_html.escape(meta.get("report_name", "")), styles["Heading2"])]
    if meta.get("description"):
        story.append(Paragraph(_html.escape(meta["description"]), small))
    story.append(Paragraph(f"Generated {meta.get('generated_at', '')} &nbsp;|&nbsp; Source: "
                           f"{_html.escape(meta.get('source_label', ''))} &nbsp;|&nbsp; "
                           f"{meta.get('row_count', 0):,} rows"
                           + (" (truncated)" if meta.get("truncated") else ""), small))
    story.append(Paragraph("Filters: " + _html.escape(meta.get("filters_summary", "")), small))
    story.append(Spacer(1, 4 * mm))

    if not result.columns:
        story.append(Paragraph("No columns.", styles["Normal"]))
    else:
        widths = _col_widths(result, PAGE_W - 2 * MARGIN)
        header = [Paragraph(f"<b>{_html.escape(c.label)}</b>", cell_style) for c in result.columns]
        num_idx = [i for i, c in enumerate(result.columns) if c.type == NUMBER]
        base_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDE7F3")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("LEADING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
        ] + [("ALIGN", (i, 1), (i, -1), "RIGHT") for i in num_idx]
        CHUNK = 400
        rows = result.rows or []
        chunks = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)] or [[]]
        for ci, chunk in enumerate(chunks):
            data = [header]
            for r in chunk:
                data.append([_pdf_text(format_cell(r.get(c.key), c.type), 60) for c in result.columns])
            if ci == len(chunks) - 1 and result.totals:
                data.append([("Total" if i == 0 else format_cell(result.totals.get(c.key), NUMBER)
                              if c.key in result.totals else "") for i, c in enumerate(result.columns)])
            t = Table(data, colWidths=widths, repeatRows=1)
            style = list(base_style)
            if ci == len(chunks) - 1 and result.totals:
                style += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                          ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEF2F7"))]
            t.setStyle(TableStyle(style))
            story.append(t)
        if not rows:
            story.append(Spacer(1, 3 * mm))
            story.append(Paragraph("No data for the selected period / filters.", small))

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()


def _pdf_text(s: str, max_len: int) -> str:
    s = (s or "").replace("\r", " ").replace("\n", " ")
    return s if len(s) <= max_len else s[:max_len - 1] + "…"


# ── built-in minimal PDF writer (no dependencies) ───────────────────

class _MiniPdf:
    """Tiny PDF 1.4 writer: Helvetica text, lines, filled rectangles, multiple pages."""

    def __init__(self):
        self.pages: List[List[str]] = []
        self.cur: List[str] = []

    def new_page(self):
        self.cur = []
        self.pages.append(self.cur)

    @staticmethod
    def esc(s: str) -> str:
        b = (s or "").encode("cp1252", "replace").decode("cp1252")
        return b.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    @staticmethod
    def width(s: str, size: float) -> float:
        return len(s or "") * size * 0.52

    def text(self, x: float, y: float, s: str, size: float = 8, bold: bool = False, gray: float = 0.0):
        font = "/F2" if bold else "/F1"
        self.cur.append(f"BT {gray:.2f} g {font} {size:.1f} Tf {x:.2f} {y:.2f} Td ({self.esc(s)}) Tj ET")

    def text_right(self, x_right: float, y: float, s: str, size: float = 8, bold: bool = False, gray: float = 0.0):
        self.text(x_right - self.width(s, size), y, s, size, bold, gray)

    def rect(self, x, y, w, h, gray: float = 0.9):
        self.cur.append(f"{gray:.2f} g {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f 0 g")

    def line(self, x1, y1, x2, y2, gray: float = 0.75, lw: float = 0.4):
        self.cur.append(f"{gray:.2f} G {lw:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S 0 G")

    def build(self, title: str = "Report") -> bytes:
        objs: List[bytes] = []

        def add(body: str | bytes) -> int:
            objs.append(body if isinstance(body, bytes) else body.encode("latin-1"))
            return len(objs)

        add("<< /Type /Catalog /Pages 2 0 R >>")          # 1
        add("")                                            # 2 placeholder (pages)
        add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")       # 3
        add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")  # 4
        add(f"<< /Title ({self.esc(title)}) /Producer (EBMS Report Builder) >>")                        # 5
        page_ids = []
        for content in self.pages or [[]]:
            stream = "\n".join(content).encode("cp1252", "replace")
            cid = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
            pid = add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.2f} {PAGE_H:.2f}] "
                      f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {cid} 0 R >>")
            page_ids.append(pid)
        kids = " ".join(f"{p} 0 R" for p in page_ids)
        objs[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")

        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(out.tell())
            out.write(f"{i} 0 obj\n".encode()); out.write(body); out.write(b"\nendobj\n")
        xref = out.tell()
        out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
        for off in offsets:
            out.write(f"{off:010d} 00000 n \n".encode())
        out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        return out.getvalue()


def _render_pdf_builtin(result: ReportResult, meta: dict) -> bytes:
    pdf = _MiniPdf()
    usable = PAGE_W - 2 * MARGIN
    widths = _col_widths(result, usable) if result.columns else []
    font, row_h, head_h = 7.5, 11.0, 14.0
    footer_y = 14.0
    bottom = MARGIN + 10

    def fit(s: str, w: float, size: float) -> str:
        s = (s or "").replace("\n", " ")
        maxc = max(int((w - 4) / (size * 0.52)), 1)
        return s if len(s) <= maxc else (s[:maxc - 1] + "~" if maxc > 1 else s[:1])

    rows = list(result.rows or [])
    if result.totals and result.columns:
        rows.append({"__total__": True, **{c.key: result.totals.get(c.key) for c in result.columns}})

    def header_block() -> float:
        y = PAGE_H - MARGIN - 4
        pdf.text(MARGIN, y, meta.get("company_name", ""), 13, True); y -= 15
        pdf.text(MARGIN, y, meta.get("report_name", ""), 11, True); y -= 12
        if meta.get("description"):
            pdf.text(MARGIN, y, fit(meta["description"], usable, 8), 8, False, 0.35); y -= 10
        pdf.text(MARGIN, y, fit(f"Generated {meta.get('generated_at', '')}  |  Source: {meta.get('source_label', '')}"
                                f"  |  {meta.get('row_count', 0):,} rows" + (" (truncated)" if meta.get("truncated") else ""),
                                usable, 8), 8, False, 0.35); y -= 10
        pdf.text(MARGIN, y, fit("Filters: " + meta.get("filters_summary", ""), usable, 8), 8, False, 0.35); y -= 12
        return y

    def table_header(y: float) -> float:
        pdf.rect(MARGIN, y - head_h + 3, usable, head_h, 0.87)
        x = MARGIN
        for c, w in zip(result.columns, widths):
            label = fit(c.label, w, font)
            if c.type == NUMBER:
                pdf.text_right(x + w - 2, y - 7, label, font, True)
            else:
                pdf.text(x + 2, y - 7, label, font, True)
            x += w
        pdf.line(MARGIN, y - head_h + 3, MARGIN + usable, y - head_h + 3, 0.5, 0.6)
        return y - head_h

    idx = 0
    page_no = 0
    while True:
        pdf.new_page(); page_no += 1
        y = header_block()
        if result.columns:
            y = table_header(y)
        if not rows and idx == 0:
            pdf.text(MARGIN, y - 12, "No data for the selected period / filters.", 8, False, 0.35)
        while idx < len(rows) and y - row_h > bottom:
            r = rows[idx]
            is_total = bool(r.get("__total__"))
            if is_total:
                pdf.rect(MARGIN, y - row_h + 3, usable, row_h, 0.93)
            x = MARGIN
            for i, (c, w) in enumerate(zip(result.columns, widths)):
                if is_total and i == 0 and c.key not in result.totals:
                    txt = "Total"
                elif is_total and c.key not in result.totals:
                    txt = ""
                else:
                    txt = format_cell(r.get(c.key), c.type)
                txt = fit(txt, w, font)
                if c.type == NUMBER:
                    pdf.text_right(x + w - 2, y - 8, txt, font, is_total)
                else:
                    pdf.text(x + 2, y - 8, txt, font, is_total)
                x += w
            pdf.line(MARGIN, y - row_h + 3, MARGIN + usable, y - row_h + 3, 0.82, 0.3)
            y -= row_h
            idx += 1
        pdf.text(MARGIN, footer_y, f"{meta.get('company_name', '')} - {meta.get('report_name', '')}", 7.5, False, 0.4)
        pdf.text_right(PAGE_W - MARGIN, footer_y, f"Page {page_no} of %%TOTAL%%", 7.5, False, 0.4)
        if idx >= len(rows):
            break

    total = str(page_no)
    for page in pdf.pages:
        for i, op in enumerate(page):
            if "%%TOTAL%%" in op:
                # width of the replaced text changes; re-anchor on the right margin
                page[i] = op.replace("%%TOTAL%%", total)
                m = re.search(r"\(Page (\d+) of (\d+)\)", page[i])
                if m:
                    s = m.group(0)[1:-1]
                    page[i] = re.sub(r"[-\d.]+ [-\d.]+ Td", f"{PAGE_W - MARGIN - pdf.width(s, 7.5):.2f} {footer_y:.2f} Td", page[i], count=1)
    return pdf.build(meta.get("report_name", "Report"))


def render(result: ReportResult, meta: dict, fmt: str, chart: Optional[dict] = None) -> bytes:
    fmt = (fmt or "pdf").lower()
    if fmt == "pdf":
        return render_pdf(result, meta)
    if fmt == "xlsx":
        return render_xlsx(result, meta)
    if fmt == "csv":
        return render_csv(result)
    if fmt == "html":
        return render_html(result, meta, chart).encode("utf-8")
    raise CatalogError(f"unknown format {fmt!r}")
