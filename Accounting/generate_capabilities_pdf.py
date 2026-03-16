"""
Generate a professional Technical Capabilities PDF for the
Ethiopian Business Management System.
"""
import os
from datetime import date
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether, Image as RLImage
)
from reportlab.platypus.flowables import HRFlowable

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots_temp")

# ── Colour palette ────────────────────────────────────────────────────────────
C_NAVY    = colors.HexColor("#1a3a5c")
C_TEAL    = colors.HexColor("#0d7377")
C_GOLD    = colors.HexColor("#f0a500")
C_LIGHT   = colors.HexColor("#f5f7fa")
C_BORDER  = colors.HexColor("#d0d8e4")
C_TEXT    = colors.HexColor("#1e2d3d")
C_SUBTEXT = colors.HexColor("#4a5568")
C_GREEN   = colors.HexColor("#38a169")
C_RED     = colors.HexColor("#e53e3e")
C_WHITE   = colors.white

W, H = A4
MARGIN = 20 * mm

# ── Page numbering callback ───────────────────────────────────────────────────
def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(C_SUBTEXT)
    canvas.drawString(MARGIN, 10 * mm,
        "Ethiopian Business Management System — Technical Capabilities")
    canvas.drawRightString(W - MARGIN, 10 * mm,
        f"Page {doc.page}  |  Confidential  |  {date.today().strftime('%B %d, %Y')}")
    canvas.setStrokeColor(C_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 12 * mm, W - MARGIN, 12 * mm)
    canvas.restoreState()

def _cover_page(canvas, doc):
    """Full-bleed cover page drawn before the first flowable."""
    # Already called on page 1 by onFirstPage; skip if past page 1
    pass

# ── Style factory ─────────────────────────────────────────────────────────────
base = getSampleStyleSheet()

def S(name, **kw):
    return ParagraphStyle(name, **kw)

sTitleCover = S("TitleCover",
    fontName="Helvetica-Bold", fontSize=28, textColor=C_WHITE,
    alignment=TA_CENTER, spaceAfter=6)
sSubCover = S("SubCover",
    fontName="Helvetica", fontSize=13, textColor=colors.HexColor("#c8d8e8"),
    alignment=TA_CENTER, spaceAfter=4)
sH1 = S("H1",
    fontName="Helvetica-Bold", fontSize=15, textColor=C_NAVY,
    spaceBefore=10, spaceAfter=4, leading=20)
sH2 = S("H2",
    fontName="Helvetica-Bold", fontSize=11, textColor=C_TEAL,
    spaceBefore=8, spaceAfter=3, leading=15)
sH3 = S("H3",
    fontName="Helvetica-Bold", fontSize=9.5, textColor=C_TEXT,
    spaceBefore=4, spaceAfter=2, leading=13)
sBody = S("Body",
    fontName="Helvetica", fontSize=9, textColor=C_TEXT,
    spaceBefore=2, spaceAfter=2, leading=13, alignment=TA_JUSTIFY)
sBullet = S("Bullet",
    fontName="Helvetica", fontSize=9, textColor=C_TEXT,
    leftIndent=10, bulletIndent=0, spaceBefore=1, spaceAfter=1,
    leading=13, bulletFontName="Helvetica", bulletFontSize=9)
sCell = S("Cell",
    fontName="Helvetica", fontSize=8.5, textColor=C_TEXT, leading=11)
sCellBold = S("CellBold",
    fontName="Helvetica-Bold", fontSize=8.5, textColor=C_NAVY, leading=11)
sTag = S("Tag",
    fontName="Helvetica-Bold", fontSize=7.5, textColor=C_TEAL,
    alignment=TA_CENTER)
sNote = S("Note",
    fontName="Helvetica-Oblique", fontSize=8, textColor=C_SUBTEXT,
    leading=11, spaceBefore=2)

CW = W - 2 * MARGIN  # content width

# ── Helper builders ───────────────────────────────────────────────────────────
def hr(color=C_BORDER, thickness=0.5):
    return HRFlowable(width="100%", thickness=thickness, color=color,
                      spaceAfter=4, spaceBefore=2)

def section_header(text, icon="■"):
    return [
        Spacer(1, 4 * mm),
        Table(
            [[Paragraph(f"{icon}  {text}", S("SH",
                fontName="Helvetica-Bold", fontSize=13,
                textColor=C_WHITE, leading=16))]],
            colWidths=[CW],
            style=TableStyle([
                ("BACKGROUND", (0,0), (-1,-1), C_NAVY),
                ("TOPPADDING",    (0,0), (-1,-1), 7),
                ("BOTTOMPADDING", (0,0), (-1,-1), 7),
                ("LEFTPADDING",   (0,0), (-1,-1), 10),
                ("ROUNDEDCORNERS", [3]),
            ])
        ),
        Spacer(1, 3 * mm),
    ]

def module_card(title, prefix, description, features, tech_notes=None):
    """Build a styled module card as a Table."""
    rows = []

    # Header row
    header_text = f"<b>{title}</b>   <font color='#{C_TEAL.hexval()[2:]}' size=8>{prefix}</font>"
    rows.append([Paragraph(header_text, S("MH",
        fontName="Helvetica-Bold", fontSize=10.5,
        textColor=C_WHITE, leading=14))])

    # Description
    rows.append([Paragraph(description, S("MD",
        fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#e8f4f8"), leading=12))])

    # Features
    feat_items = []
    for f in features:
        feat_items.append(Paragraph(f"• &nbsp;{f}", S("MF",
            fontName="Helvetica", fontSize=8.5,
            textColor=C_TEXT, leading=12)))

    feat_table = Table(
        [[fi] for fi in feat_items],
        colWidths=[CW - 20],
        style=TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), C_LIGHT),
            ("TOPPADDING",    (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
            ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ])
    )
    rows.append([feat_table])

    if tech_notes:
        rows.append([Paragraph(f"<i>Technical notes: {tech_notes}</i>", sNote)])

    outer = Table(rows, colWidths=[CW],
        style=TableStyle([
            ("BACKGROUND",    (0,0), (0,0), C_TEAL),
            ("BACKGROUND",    (0,1), (0,1), colors.HexColor("#1a6e72")),
            ("BACKGROUND",    (0,2), (-1,-1), C_LIGHT),
            ("TOPPADDING",    (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 10),
            ("RIGHTPADDING",  (0,0), (-1,-1), 10),
            ("BOX",           (0,0), (-1,-1), 1, C_BORDER),
            ("ROWBACKGROUNDS",(0,2), (-1,-1), [C_LIGHT]),
        ])
    )
    return KeepTogether([outer, Spacer(1, 4 * mm)])

def feature_table(headers, rows_data, col_widths=None):
    """Generic two-or-three column table."""
    if col_widths is None:
        col_widths = [CW / len(headers)] * len(headers)
    data = [[Paragraph(h, sCellBold) for h in headers]]
    for row in rows_data:
        data.append([Paragraph(str(c), sCell) for c in row])
    ts = TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), C_NAVY),
        ("TEXTCOLOR",     (0,0), (-1,0), C_WHITE),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,0), 8.5),
        ("ALIGN",         (0,0), (-1,-1), "LEFT"),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LIGHT]),
        ("GRID",          (0,0), (-1,-1), 0.4, C_BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ])
    return Table(data, colWidths=col_widths, style=ts, repeatRows=1)

# ── Document build ─────────────────────────────────────────────────────────────
OUT = os.path.join(os.path.dirname(__file__), "EBMS_Technical_Capabilities.pdf")
doc = SimpleDocTemplate(
    OUT, pagesize=A4,
    leftMargin=MARGIN, rightMargin=MARGIN,
    topMargin=MARGIN, bottomMargin=18 * mm,
    title="Ethiopian Business Management System — Technical Capabilities",
    author="EBMS Platform Team",
)

story = []

# ═══════════════════════════════════════════════════════════════════════════════
# COVER PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def cover_page(canvas, doc):
    if doc.page != 1:
        _footer(canvas, doc)
        return
    canvas.saveState()
    # Background gradient rectangle (navy → teal)
    canvas.setFillColor(C_NAVY)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    # Accent bar
    canvas.setFillColor(C_GOLD)
    canvas.rect(0, H * 0.38, W, 3, fill=1, stroke=0)
    canvas.setFillColor(C_TEAL)
    canvas.rect(0, H * 0.38 - 6, W, 6, fill=1, stroke=0)

    # Logo text placeholder
    canvas.setFont("Helvetica-Bold", 42)
    canvas.setFillColor(C_GOLD)
    canvas.drawCentredString(W / 2, H * 0.72, "EBMS")

    canvas.setFont("Helvetica", 14)
    canvas.setFillColor(colors.HexColor("#aac8d8"))
    canvas.drawCentredString(W / 2, H * 0.67, "Ethiopian Business Management System")

    canvas.setFont("Helvetica-Bold", 22)
    canvas.setFillColor(C_WHITE)
    canvas.drawCentredString(W / 2, H * 0.54, "Technical Capabilities")
    canvas.setFont("Helvetica-Bold", 16)
    canvas.setFillColor(C_GOLD)
    canvas.drawCentredString(W / 2, H * 0.49, "& Module Reference Guide")

    canvas.setFont("Helvetica", 11)
    canvas.setFillColor(colors.HexColor("#88a8c0"))
    canvas.drawCentredString(W / 2, H * 0.43, f"Version 1.0  ·  {date.today().strftime('%B %Y')}")

    # Left column bottom metadata
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#c0d8e8"))
    items = [
        ("Platform",    "FastAPI + PostgreSQL + AWS EC2"),
        ("Modules",     "17 fully integrated business modules"),
        ("Deployment",  "AWS EC2 · af-south-1 · Auto-scaling"),
        ("Compliance",  "Ethiopian Tax Authority (ERCA) aligned"),
        ("Security",    "OWASP Top-10 hardened · SIEM built-in"),
    ]
    y = H * 0.30
    for label, val in items:
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(C_GOLD)
        canvas.drawString(MARGIN + 5, y, f"{label}:")
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#d0e8f0"))
        canvas.drawString(MARGIN + 65, y, val)
        y -= 14

    canvas.setFont("Helvetica-Oblique", 8)
    canvas.setFillColor(colors.HexColor("#607080"))
    canvas.drawCentredString(W / 2, 18 * mm,
        "Confidential — For authorised personnel only")
    canvas.restoreState()

# Cover page drawn by onFirstPage callback; just push to page 2
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# TABLE OF CONTENTS (page 2)
# ═══════════════════════════════════════════════════════════════════════════════
story.append(Paragraph("Table of Contents", S("TOC",
    fontName="Helvetica-Bold", fontSize=18, textColor=C_NAVY,
    spaceAfter=6, spaceBefore=4)))
story.append(hr(C_TEAL, 1.5))

toc_entries = [
    ("1", "System Overview",                                    "3"),
    ("2", "Architecture & Technology Stack",                   "4"),
    ("3", "Authentication & User Management",                  "5"),
    ("4", "VAT Portal Module",                                 "6"),
    ("5", "Journal Entry & Chart of Accounts",                 "7"),
    ("6", "Income & Expense Module",                           "8"),
    ("7", "Transaction Management",                            "9"),
    ("8", "Ethiopian Payroll Module",                          "10"),
    ("9", "Inventory Management",                              "11"),
    ("10", "CPO (Cash Payment Order) Module",                  "12"),
    ("11", "Bid Tracker Module",                               "13"),
    ("12", "Letter & E-Signature Module",                      "14"),
    ("13", "SIEM Security Module",                             "15"),
    ("14", "Backup & Archive System",                          "16"),
    ("15", "Version Control Module",                           "16"),
    ("16", "Multi-Company Portal",                             "17"),
    ("17", "REST API v1",                                      "18"),
    ("18", "Subscription Tiers & Licensing",                   "19"),
    ("19", "Security & Compliance",                            "20"),
    ("20", "Deployment & Infrastructure",                      "21"),
]

toc_data = []
for num, title, pg in toc_entries:
    toc_data.append([
        Paragraph(f"<b>{num}.</b>", sCell),
        Paragraph(title, sCell),
        Paragraph(pg, S("PG", fontName="Helvetica", fontSize=9,
                         textColor=C_SUBTEXT, alignment=TA_CENTER)),
    ])
toc_table = Table(toc_data, colWidths=[15 * mm, CW - 35 * mm, 20 * mm],
    style=TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [C_WHITE, C_LIGHT]),
        ("GRID",           (0, 0), (-1, -1), 0.3, C_BORDER),
        ("TOPPADDING",     (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 4),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
    ])
)
story.append(toc_table)
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 1. SYSTEM OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("1. System Overview", "◆")
story.append(Paragraph(
    "The <b>Ethiopian Business Management System (EBMS)</b> is a full-stack, cloud-deployed "
    "enterprise resource planning platform purpose-built for Ethiopian businesses. "
    "It integrates seventeen business modules — spanning financial accounting, tax compliance, "
    "HR & payroll, inventory, bid management, e-signatures, and security monitoring — "
    "into a single web application accessible from any browser.",
    sBody))
story.append(Spacer(1, 3 * mm))

overview_data = [
    ["Category",           "Details"],
    ["Platform",           "FastAPI (Python 3.12) · Jinja2 templates · PostgreSQL"],
    ["Hosting",            "AWS EC2 (af-south-1) behind Application Load Balancer"],
    ["Multi-tenancy",      "Per-company data isolation via PostgreSQL Row-Level Security"],
    ["Authentication",     "Session cookies + Bearer token API auth · bcrypt passwords"],
    ["Tax Compliance",     "Ethiopian Revenue & Customs Authority (ERCA) aligned VAT rules"],
    ["Languages",          "English UI — fully localizable"],
    ["Modules",            "17 integrated modules (see sections 3–17)"],
    ["API",                "REST API v1 under /api/v1/ — JSON, CSV, XLSX export"],
    ["Security",           "OWASP Top-10 hardened · CSRF protection · SIEM built-in"],
    ["Subscription model", "Starter / Professional / Enterprise tiers"],
]
story.append(feature_table(
    ["Category", "Details"],
    [r[0:2] for r in overview_data[1:]],
    col_widths=[55 * mm, CW - 55 * mm]
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 2. ARCHITECTURE
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("2. Architecture & Technology Stack", "◆")

story.append(Paragraph("<b>Backend Framework</b>", sH2))
story.append(Paragraph(
    "Built on <b>FastAPI</b> (ASGI) served by <b>Uvicorn</b> behind Nginx. "
    "Each business module is an independent <i>APIRouter</i> registered on startup "
    "via the <code>_reg()</code> factory, ensuring clean separation and safe fallback "
    "if a module fails to load. Starlette middleware layers handle authentication, "
    "CSRF, session management, structured logging, GZip compression, rate-limiting "
    "and audit-trail in a defined stack order.", sBody))

story.append(Paragraph("<b>Data Layer</b>", sH2))
story.append(Paragraph(
    "Primary storage is <b>PostgreSQL</b> (AWS RDS). "
    "Row-level security policies enforce per-tenant data isolation at the "
    "database level — even if an application WHERE clause is accidentally omitted. "
    "An async connection pool (<i>asyncpg</i>) handles high-concurrency reads. "
    "Letter / E-Signature module uses JSON file storage for lightweight, "
    "portable document management.", sBody))

arch_data = [
    ["Layer",          "Technology",               "Purpose"],
    ["Web server",     "Nginx",                    "TLS termination, static assets, reverse proxy"],
    ["App server",     "Uvicorn (ASGI)",            "Async HTTP server"],
    ["Framework",      "FastAPI + Starlette",       "Routing, middleware, dependency injection"],
    ["Templates",      "Jinja2",                   "Server-side HTML rendering"],
    ["Database",       "PostgreSQL (RDS)",          "Primary persistent storage with RLS"],
    ["Cache",          "Redis (ElastiCache)",       "Session store, rate-limit counters"],
    ["File storage",   "Local FS + S3",             "Backup archives, DOCX exports"],
    ["Background",     "APScheduler",              "Nightly backup at 01:00"],
    ["Monitoring",     "SIEM + structured JSON logs","Security events, audit trail"],
    ["Infra",          "Terraform + AWS",           "EC2, RDS, ALB, VPC, S3"],
]
story.append(feature_table(
    ["Layer", "Technology", "Purpose"],
    [r for r in arch_data[1:]],
    col_widths=[35 * mm, 55 * mm, CW - 90 * mm]
))

story.append(Paragraph("<b>Middleware Stack (innermost → outermost)</b>", sH2))
mw_items = [
    "GZip compression — compresses all responses ≥ 1 KB",
    "Session middleware — Redis-backed (cookie fallback)",
    "Rate limiter — SlowAPI enforces per-endpoint limits",
    "Audit trail — logs every successful POST/PUT/PATCH/DELETE to SIEM",
    "CSRF auto-inject — hidden token injected into every HTML form",
    "CSRF validation — validates X-CSRFToken on AJAX/HTMX mutations",
    "Module license check — blocks unlicensed module access per tenant",
    "Company context — sets request.state.company_id for multi-tenancy",
    "Auth gate — validates session or Bearer token on every request",
    "Structured logger — emits one JSON log line per HTTP request",
    "Request-ID tagger — X-Request-ID header on every response",
]
for item in mw_items:
    story.append(Paragraph(f"• {item}", sBullet))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 3. AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("3. Authentication & User Management", "◆")
story.append(module_card(
    "Authentication System", "/auth/  ·  /company/",
    "Secure multi-tenant login system with role-based access control, "
    "API token generation, and company-context switching.",
    [
        "Username/password login with bcrypt hashing (cost factor 12)",
        "Role-based access: super_admin → admin → manager → user",
        "Per-company multi-tenancy — users belong to one or more companies",
        "Bearer token generation for API / programmatic access",
        "Idle auto-logout after 5 minutes with 60-second warning toast",
        "Access-denied page with role requirement display",
        "Company registration and company selection portal",
        "CSRF-protected login form with rate-limiting",
        "Session stored in Redis (fallback to signed cookie)",
        "Audit log of every login / logout event in SIEM",
    ],
    tech_notes="Passwords hashed with bcrypt. Sessions use server-side Redis store "
               "with SameSite=Lax cookies. Bearer tokens are SHA-256 hashed in DB."
))

auth_routes = [
    ["Route",                   "Method", "Description"],
    ["/auth/login",             "GET/POST","Login form and authentication"],
    ["/auth/logout",            "GET",     "Invalidate session and redirect to login"],
    ["/auth/register",          "GET/POST","New user self-registration"],
    ["/auth/portal",            "GET",     "User dashboard / module selector"],
    ["/auth/access-denied",     "GET",     "Permission-denied landing page"],
    ["/auth/users",             "GET",     "Admin: list all users (admin+)"],
    ["/auth/api-tokens",        "GET",     "Manage personal API tokens"],
    ["/company/login",          "GET/POST","Company-specific login"],
    ["/company/register",       "GET/POST","Register new company"],
    ["/company/select",         "GET/POST","Switch between companies"],
]
story.append(Spacer(1, 2 * mm))
story.append(feature_table(
    ["Route", "Method", "Description"],
    [r for r in auth_routes[1:]],
    col_widths=[55 * mm, 25 * mm, CW - 80 * mm]
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 4. VAT PORTAL
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("4. VAT Portal Module", "◆")
story.append(module_card(
    "VAT Portal", "/vat/",
    "Comprehensive VAT management module aligned with Ethiopian Revenue & Customs "
    "Authority (ERCA) requirements. Handles income, expenses, and capital transactions "
    "with automatic VAT calculation.",
    [
        "Income recording with VAT types: standard (15%), exempt, zero-rated",
        "Expense recording with supplier TIN, receipt number, and VAT breakdown",
        "Capital investment tracking with investor details",
        "Automatic VAT calculation and running VAT liability balance",
        "Financial summary report: total income, expenses, capital, net position",
        "VAT compliance report exportable to Excel",
        "Dashboard with period-based filtering (monthly, quarterly, annual)",
        "Withholding Tax (WHT) support",
        "Gross / Net / VAT split on every record",
        "Created-by tracking for audit trail",
    ],
    tech_notes="VATType enum supports: standard_15, exempt, zero_rated, reverse_charge, "
               "withholding_2, withholding_15, withholding_30. IncomeCategory and ExpenseCategory "
               "enums map to ERCA schedule categories."
))
vat_routes_data = [
    ["/vat/",                  "GET",     "VAT dashboard with totals"],
    ["/vat/income",            "GET",     "Income list with filters"],
    ["/vat/income/add",        "GET/POST","Add new income record"],
    ["/vat/income/<id>",       "GET",     "Income detail view"],
    ["/vat/expenses",          "GET",     "Expense list"],
    ["/vat/expenses/add",      "GET/POST","Add new expense"],
    ["/vat/capital",           "GET",     "Capital transactions list"],
    ["/vat/capital/add",       "GET/POST","Add capital investment"],
    ["/vat/financial-summary", "GET",     "Full financial summary report"],
]
story.append(feature_table(
    ["Route", "Method", "Description"],
    vat_routes_data,
    col_widths=[55 * mm, 25 * mm, CW - 80 * mm]
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 5. JOURNAL ENTRY & CHART OF ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("5. Journal Entry & Chart of Accounts", "◆")
story.append(module_card(
    "Journal Entry System", "/journal/",
    "Double-entry bookkeeping engine with a structured chart of accounts "
    "supporting multi-level parent/child account hierarchies.",
    [
        "Double-entry journal entries — every debit must equal credit",
        "Multi-line journal entries with any number of debit/credit lines",
        "Account code auto-completion and validation",
        "Journal entry status: draft / posted / reversed",
        "Reference number tracking for external document linkage",
        "Trial balance generation — aggregates all posted entries",
        "General ledger view filterable by account, date, and period",
        "Reversal entries — automatically create offsetting entries",
        "Excel/CSV export of journal entries and trial balance",
        "Created-by audit trail on every entry",
    ],
    tech_notes="Uses the GeneralLedger core class. JournalEntryBuilder pattern "
               "constructs validated entry objects before persistence."
))
story.append(module_card(
    "Chart of Accounts", "/accounts/",
    "Structured account hierarchy following Ethiopian accounting standards, "
    "supporting assets, liabilities, equity, income, and expense account types.",
    [
        "Asset / Liability / Equity / Income / Expense account types",
        "Parent-child account hierarchy (sub-accounts)",
        "Normal balance tracking (debit vs credit)",
        "Current balance maintained in real-time",
        "Import accounts from Excel template",
        "25+ pre-seeded standard Ethiopian chart of accounts",
        "Account activation / deactivation",
        "Account code uniqueness enforced per company",
    ],
    tech_notes="AccountType and AccountSubType enums defined in models/account.py. "
               "Per-company isolation via company_id on every account record."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 6. INCOME & EXPENSE
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("6. Income & Expense Module", "◆")
story.append(module_card(
    "Income & Expense", "/income-expense/",
    "Simplified income and expense tracking separate from the full VAT portal, "
    "designed for day-to-day business operations with automatic tax calculations.",
    [
        "Income recording: client name, TIN, category, gross/net/tax split",
        "Expense recording: supplier, category, deductibility flag, tax breakdown",
        "Real-time profit/loss calculation on dashboard",
        "15% VAT standard rate auto-applied with override option",
        "Monthly salary data automatically pulled from payroll module",
        "One-click creation of standard monthly IT expenses (Internet, Software, Support)",
        "Excel import/export (openpyxl) for bulk data entry",
        "Session-persistent data — restored across browser sessions",
        "Category breakdown charts on dashboard",
        "Filterable by date range, category, and payment method",
    ],
    tech_notes="income_expense_data_store feeds data to both the HTML dashboard "
               "and the /api/v1/export/vat_income endpoint."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 7. TRANSACTION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("7. Transaction Management", "◆")
story.append(module_card(
    "Transaction System", "/transactions/",
    "Bank statement and ledger transaction import, review, and flagging system "
    "for identifying suspicious or out-of-policy transactions.",
    [
        "Bulk Excel/CSV import of bank transactions",
        "Import history log with row count, error count, and batch ID",
        "Transaction flagging: manual flag or auto-flag by rules",
        "Flagged accounts registry — automatically marks transactions from known accounts",
        "Individual-name detection: flags transactions with personal names in description",
        "Review workflow: pending → reviewed with reviewer notes",
        "Counterparty, account code, reference number fields",
        "Debit/credit/balance columns with currency support",
        "Filter by flag status, review status, date range, and account",
        "Export filtered transactions to CSV/XLSX",
        "Per-batch undo: delete all records from an import batch",
    ],
    tech_notes="Bulk import uses pandas for parsing. FlaggedAccounts table persisted "
               "in PostgreSQL. Auto-flag runs on import, not retroactively."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 8. PAYROLL
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("8. Ethiopian Payroll Module", "◆")
story.append(module_card(
    "Ethiopian Payroll", "/payroll/",
    "Full Ethiopian payroll calculation engine compliant with the Ethiopian Income "
    "Tax Proclamation, supporting all statutory deductions and employer contributions.",
    [
        "Ethiopian progressive income tax (PAYE) — 7 tax brackets per proclamation",
        "Pension fund: employee 7% + employer 11% contributions",
        "Overtime calculation: regular overtime, holiday, and weekend rates",
        "Allowances: transport, housing, and other taxable/non-taxable allowances",
        "Net pay and gross pay calculation with full deduction breakdown",
        "Employee master data: name, ID, department, position, bank account, TIN",
        "Date of birth and age tracking",
        "Manager field for org-chart hierarchy",
        "Phone number contact field",
        "Pension number tracking",
        "Work days per month and hours per day configuration",
        "CPO (Cash Payment Order) integration — deducts returned amounts from payroll",
        "Org chart — visual hierarchy tree built from manager field",
        "Payslip generation — formatted PDF payslip per employee",
        "Payroll reports: summary by department, cost centre analysis",
        "Bulk employee import from Excel",
        "Tax calculator tool — ad-hoc tax estimation without creating payroll",
        "Ethiopian calendar support",
    ],
    tech_notes="EthiopianPayrollCalculator in models/ethiopian_payroll.py implements "
               "the full PAYE algorithm. Employee model includes date_of_birth, "
               "phone_number, manager fields added 2026-03-15."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 9. INVENTORY
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("9. Inventory Management", "◆")
story.append(module_card(
    "Inventory Management", "/inventory/",
    "Full lifecycle inventory management supporting physical goods, "
    "rentable assets, and consumable items with movement tracking.",
    [
        "Item master: SKU, barcode, serial number, batch number",
        "Item categories with parent-child hierarchy",
        "Unit of measure, unit price, and cost price",
        "Current stock level with minimum stock and reorder point alerts",
        "FIFO / LIFO / Weighted Average valuation methods",
        "Inventory movements: purchase, sale, transfer, adjustment, return",
        "Location-based tracking (from / to location)",
        "Allocation management — assign items to events/projects",
        "Return tracking — expected vs actual return dates",
        "Maintenance scheduling and cost tracking per item",
        "Requisition management — request items with priority and approval workflow",
        "Excel/CSV bulk import with import history",
        "Low-stock dashboard alerts",
        "Rentable asset flag for event equipment",
    ],
    tech_notes="ValuationMethod enum: FIFO, LIFO, WEIGHTED_AVG. "
               "Movements and allocations stored in separate PostgreSQL tables "
               "for full history without overwriting balances."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 10. CPO
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("10. CPO (Cash Payment Order) Module", "◆")
story.append(module_card(
    "CPO Module", "/cpo/",
    "Tracks Cash Payment Orders issued to vendors and employees, "
    "including return deductions that reduce net outstanding balances.",
    [
        "CPO record: name, date, amount, bid reference, return status",
        "Bulk Excel import with import history and batch tracking",
        "Return (deduction) tracking: mark CPOs as returned with date",
        "Summary dashboard: total issued, returned (deducted), net outstanding",
        "Record count by status: returned vs outstanding",
        "Per-company isolation — CPOs belong to specific company context",
        "SIEM audit logging on every import action",
        "Export all CPO records to CSV/XLSX via API export endpoint",
        "List view with filtering and sorting",
    ],
    tech_notes="CPODataStore stores records in PostgreSQL cpo_records table. "
               "get_summary() computes returned_amount (sum of returned CPOs) and "
               "net_amount (total issued minus returned) in a single query pass."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 11. BID TRACKER
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("11. Bid Tracker Module", "◆")
story.append(module_card(
    "Bid Tracker", "/bid/",
    "End-to-end tender and bid management system for tracking open, submitted, "
    "won, and lost bids with automated deadline reminders.",
    [
        "Bid record: title, reference number, organization, category, status",
        "Status workflow: open → submitted → won / lost / cancelled",
        "Deadline tracking with configurable reminder days-before flag",
        "Automated email/notification reminder (reminder_sent flag)",
        "Bid amount and currency tracking",
        "Case handler assignment: name and email",
        "Document attachment: upload multiple files per bid",
        "Document type classification (technical, financial, legal)",
        "File size and upload timestamp tracking",
        "Per-bid activity notes",
        "Dashboard with status distribution and upcoming deadlines",
    ],
    tech_notes="bid_documents_meta stores file metadata; actual files stored on "
               "local FS under web/data/bids/documents/ (or S3 in production). "
               "reminder_days_before default is 3 days."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 12. LETTER & E-SIGNATURE
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("12. Letter & E-Signature Module", "◆")
story.append(module_card(
    "Letters & E-Signatures", "/letters/",
    "Sequential letter management with digital signatures from three designated "
    "organizational signatories (Project Manager, Finance Manager, Managing Director). "
    "Letters are tracked from composition through all signatures to dispatch.",
    [
        "Sequential auto-numbered reference IDs (REF-0001, REF-0002, …)",
        "Letter composition: subject, recipient, full body text, category",
        "Three-tier signature workflow: PM → FM → MD",
        "Canvas-based e-signature capture — draw signatures directly in browser",
        "Saved signature pads per role — sign once, reuse across letters",
        "Signature timestamp: exact date and time of each signature captured",
        "Letter status lifecycle: draft → signed → sent",
        "DOCX generation using company Templates.docx (or clean A4 fallback)",
        "Embedded base64 signature images in generated DOCX",
        "Mail tracker: full event log with timestamp for every action",
        "Signature preview panel on dashboard",
        "Download generated DOCX letter",
        "View all letters with status indicators and signatory completion",
    ],
    tech_notes="letter_docx.py uses python-docx>=1.1.0. Signatures stored as "
               "base64 PNG data-URLs in web/data/letters/. DOCX output at "
               "web/data/letters/docx/. Tracker events logged per letter."
))

sig_cols = ["Step", "Role", "Action", "Recorded Data"]
sig_rows = [
    ["1", "Author",            "Compose letter",     "Subject, body, recipient, category"],
    ["2", "Project Manager",   "Apply PM signature", "Base64 PNG, timestamp"],
    ["3", "Finance Manager",   "Apply FM signature", "Base64 PNG, timestamp"],
    ["4", "Managing Director", "Apply MD signature", "Base64 PNG, timestamp"],
    ["5", "Any authorized",    "Mark as sent",       "Sent timestamp, tracker event"],
]
story.append(Spacer(1, 2 * mm))
story.append(feature_table(sig_cols, sig_rows,
    col_widths=[10 * mm, 45 * mm, 45 * mm, CW - 100 * mm]))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 13. SIEM
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("13. SIEM — Security Information & Event Management", "◆")
story.append(module_card(
    "SIEM Security Module", "/siem/",
    "Built-in security monitoring and incident detection system that logs "
    "all user activity, file imports, and suspicious events with automated alert rules.",
    [
        "Real-time event log: IP address, username, module, endpoint, HTTP method",
        "File upload event tracking: filename, file size, import row counts",
        "Automated alert rules: brute-force detection, high-volume imports, anomalies",
        "Alert severity levels: low / medium / high / critical",
        "Alert acknowledgement workflow",
        "Event filtering: by IP, module, status, date range",
        "Dashboard: event count by module, alert counts by severity",
        "Event detail view with full request metadata",
        "Audit trail middleware — every POST/PUT/PATCH/DELETE auto-logged",
        "User agent and referer captured on every event",
        "Content-type tracking for file upload classification",
        "Export events to CSV for external SIEM integration",
    ],
    tech_notes="siem_data_store writes to PostgreSQL siem_events and siem_alerts "
               "tables. Audit trail middleware logs at the HTTP layer, independent "
               "of individual route logic."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 14. BACKUP
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("14. Backup & Archive System", "◆")
story.append(module_card(
    "Backup & Archive", "/backup/",
    "Automated and on-demand backup system that compresses and archives "
    "application data files to local storage or AWS S3.",
    [
        "Nightly automated backup scheduled at 01:00 via APScheduler",
        "on-demand manual backup trigger from dashboard",
        "Compressed archive format (gzip) with file count and size tracking",
        "Compression ratio reporting",
        "Backup log table: archive name, path, original/compressed size, timestamp",
        "Triggered-by field: 'scheduler', 'manual', or username",
        "Label support — annotate backups with human-readable notes",
        "Backup list view with restore status indicators",
        "AWS S3 upload capability for off-site archiving",
        "Retention management — configurable days to keep",
    ],
    tech_notes="BackupEngine in backup_data_store.py. BackupScheduler wraps APScheduler. "
               "Archives stored under /opt/ethiopian-business/web/data/backups/ (local) "
               "and optionally pushed to S3 bucket configured via S3_BUCKET env var."
))
story.append(Spacer(1, 2 * mm))

# ═══════════════════════════════════════════════════════════════════════════════
# 15. VERSION CONTROL
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("15. Version Control Module", "◆")
story.append(module_card(
    "Version Registry", "/version/",
    "Lightweight application version registry that tracks deployed versions "
    "with changelog and activation status directly in the database.",
    [
        "Version registry with unique version strings (semver)",
        "Version label and description",
        "Active / inactive status per version",
        "Changelog text per version entry",
        "Created-at timestamp",
        "Dashboard shows current active version",
        "Version history list",
        "Admin-only version management (promote / deactivate)",
    ],
    tech_notes="version_registry table in PostgreSQL. Managed via version_data_store.py. "
               "Useful for tracking which code version is running in multi-server deployments."
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 16. MULTI-COMPANY PORTAL
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("16. Multi-Company Portal", "◆")
story.append(module_card(
    "Multi-Company Portal", "/company/  ·  /multicompany/",
    "Allows a single user account to manage multiple companies from one login, "
    "with a full per-company subscription and module licensing engine.",
    [
        "Company registration with unique company_id",
        "Company selection switcher — switch context mid-session",
        "Per-company subscription: Starter / Professional / Enterprise tier",
        "Module licensing: enable or disable individual modules per company",
        "Subscription expiry date tracking",
        "Max users and max employees limits enforced per tier",
        "Tenant dashboard: see all companies you belong to",
        "Row-Level Security at PostgreSQL level — data never crosses company boundaries",
        "Company name displayed in navigation bar",
        "Audit log per company: who changed what and when",
    ],
    tech_notes="TenantDataStore in tenant_data_store.py manages all tenants. "
               "SUBSCRIPTION_TIERS dict defines module sets. ALWAYS_ALLOWED_MODULES "
               "= {auth, static} bypasses licensing check."
))

tier_data = [
    ["Tier",         "Price",          "Max Users", "Max Employees", "Modules Included"],
    ["Starter",      "Free",           "3",         "25",            "Auth, CoA, Journal, VAT"],
    ["Professional", "ETB 2,500/mo",   "15",        "200",           "Starter + Income/Expense, Transactions, CPO, Payroll"],
    ["Enterprise",   "ETB 7,500/mo",   "Unlimited", "Unlimited",     "Professional + Inventory, Bid, SIEM, Backup, Version, Multi-company"],
]
story.append(Spacer(1, 2*mm))
story.append(feature_table(
    tier_data[0],
    tier_data[1:],
    col_widths=[28*mm, 30*mm, 22*mm, 30*mm, CW - 110*mm]
))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 17. REST API
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("17. REST API v1", "◆")
story.append(Paragraph(
    "All API endpoints live under <b>/api/v1/</b>. They accept and return JSON. "
    "Authentication uses the same Bearer token issued from the portal. "
    "Interactive documentation is available at <b>/api/docs</b> (Swagger UI) "
    "and <b>/api/redoc</b> (ReDoc).",
    sBody))
story.append(Spacer(1, 2 * mm))

api_routes = [
    ["Endpoint",                      "Method", "Auth",  "Description"],
    ["/api/v1/health",                "GET",    "Public","Service health check — returns {status, service}"],
    ["/api/v1/whoami",                "GET",    "User",  "Current user info and company context"],
    ["/api/v1/employees",             "GET",    "User",  "List all employees for current company"],
    ["/api/v1/employees/{id}",        "GET",    "User",  "Single employee detail"],
    ["/api/v1/employees",             "POST",   "Admin", "Create new employee"],
    ["/api/v1/siem/events",           "GET",    "Admin", "List recent SIEM security events"],
    ["/api/v1/dashboard/stats",       "GET",    "User",  "Aggregate counts: accounts, transactions"],
    ["/api/v1/export/{module}",       "GET",    "User",  "Export module data to CSV or XLSX"],
]
story.append(feature_table(
    ["Endpoint", "Method", "Auth", "Description"],
    [r for r in api_routes[1:]],
    col_widths=[55*mm, 18*mm, 15*mm, CW - 88*mm]
))
story.append(Spacer(1, 3*mm))

story.append(Paragraph("<b>Export Endpoint — Supported Modules</b>", sH2))
export_data = [
    ["Module Key",   "Source",                       "Format"],
    ["employees",    "employee_data_store",           "CSV, XLSX"],
    ["cpo",          "cpo_data_store",                "CSV, XLSX"],
    ["transactions", "transaction_data_store",        "CSV, XLSX"],
    ["vat_income",   "vat_data_store → get_all_income", "CSV, XLSX"],
    ["vat_expenses", "vat_data_store → get_all_expenses","CSV, XLSX"],
    ["inventory",    "inventory_data_store",          "CSV, XLSX"],
]
story.append(feature_table(
    ["Module Key", "Source", "Format"],
    [r for r in export_data[1:]],
    col_widths=[35*mm, CW - 70*mm, 35*mm]
))
story.append(Paragraph(
    "Sensitive fields (password, password_hash, secret) are automatically stripped "
    "from all export files.",
    sNote))
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 18. SUBSCRIPTION TIERS
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("18. Subscription Tiers & Licensing", "◆")
story.append(Paragraph(
    "EBMS uses a per-company subscription model enforced at both the application "
    "and database layers. Each tier unlocks a defined set of modules.",
    sBody))

for tier_name, info in [
    ("Starter", {
        "price": "Free",
        "users": "3", "employees": "25",
        "modules": "Auth, Chart of Accounts, Journal Entries, VAT Portal",
        "desc": "Core accounting package for small businesses and startups.",
    }),
    ("Professional", {
        "price": "ETB 2,500 / month",
        "users": "15", "employees": "200",
        "modules": "Starter + Income & Expense, Transactions, CPO, Payroll",
        "desc": "Full financial operations suite for growing businesses.",
    }),
    ("Enterprise", {
        "price": "ETB 7,500 / month",
        "users": "Unlimited", "employees": "Unlimited",
        "modules": "Professional + Inventory, Bid Tracker, SIEM, Backup, Version Control, Multi-company",
        "desc": "Complete business platform for large organisations.",
    }),
]:
    story.append(Paragraph(f"<b>{tier_name} Tier</b>  —  {info['price']}", sH2))
    story.append(Paragraph(info["desc"], sBody))
    story.append(Paragraph(
        f"Max users: <b>{info['users']}</b>  ·  Max employees: <b>{info['employees']}</b>", sBody))
    story.append(Paragraph(f"Included modules: {info['modules']}", sBody))
    story.append(Spacer(1, 2 * mm))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 19. SECURITY & COMPLIANCE
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("19. Security & Compliance", "◆")
story.append(Paragraph(
    "EBMS is designed to meet OWASP Top-10 requirements and Ethiopian tax "
    "compliance standards. The following controls are implemented at the "
    "framework and database levels:", sBody))

security_items = [
    ("Broken Access Control",
     "Role hierarchy (super_admin > admin > manager > user). Module license gate "
     "enforced per tenant at middleware level. Row-Level Security in PostgreSQL prevents "
     "cross-tenant data access even if application WHERE clause is omitted."),
    ("Cryptographic Failures",
     "Passwords hashed with bcrypt (cost factor 12). API token secrets stored as SHA-256 hash. "
     "Session secret key: 256-bit random hex from os.environ. HTTPS enforced by ALB; "
     "SESSION_COOKIE_SECURE=true sets Secure cookie flag."),
    ("Injection (SQL)",
     "All database queries use parameterised statements via psycopg2 cursor.execute(). "
     "No string concatenation into SQL. ORM pattern via db.py helper."),
    ("Injection (XSS)",
     "Jinja2 auto-escaping on all templates. User input rendered with {{ var }} (escaped). "
     "Content-Security-Policy header set on static assets."),
    ("CSRF",
     "Dual CSRF strategy: hidden csrf_token field injected into every HTML POST form "
     "by middleware; X-CSRFToken header validated on all AJAX/HTMX mutations."),
    ("Rate Limiting",
     "SlowAPI (slowapi) middleware enforces per-endpoint rate limits. "
     "Login endpoint limited to prevent brute-force attacks."),
    ("Audit Trail",
     "Every POST/PUT/PATCH/DELETE request is logged to the SIEM events table with "
     "username, IP, path, and timestamp via audit_trail_middleware."),
    ("Session Security",
     "Sessions stored server-side in Redis (fallback to signed cookie). "
     "SameSite=Lax prevents CSRF via cross-site navigation. "
     "Auto-logout after 5 minutes of inactivity with 60-second warning."),
    ("Dependency Security",
     "requirements.txt pins minimum versions. No known CVEs in current dependencies. "
     "python-dotenv for secrets management (no secrets in code)."),
    ("Ethiopian Tax Compliance",
     "VAT calculations aligned with ERCA proclamation. "
     "Payroll PAYE follows Ethiopian Income Tax Proclamation No. 979/2016. "
     "Pension rates: employee 7%, employer 11% per labour proclamation."),
]

for title, desc in security_items:
    story.append(Paragraph(f"<b>{title}</b>", sH3))
    story.append(Paragraph(desc, sBody))

story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# 20. DEPLOYMENT & INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("20. Deployment & Infrastructure", "◆")
story.append(Paragraph(
    "EBMS is deployed on AWS in the <b>af-south-1</b> (Cape Town) region, "
    "chosen for low latency to Ethiopian users. Infrastructure is managed "
    "as code with Terraform.", sBody))

infra_rows = [
    ["Component",        "Service",              "Details"],
    ["Web server",       "AWS EC2",              "Ubuntu 22.04, t3.medium, businessapp user"],
    ["Load balancer",    "AWS ALB",              "HTTP:80 → EC2:5000 via Nginx:5000"],
    ["Database",         "AWS RDS PostgreSQL",   "db.t3.micro (production), Multi-AZ optional"],
    ["Cache",            "AWS ElastiCache Redis","Optional — app falls back to in-process cache"],
    ["Object storage",   "AWS S3",               "Backup archives and static assets"],
    ["Process manager",  "Supervisor",           "Program: ethiopian-business, auto-restart"],
    ["App startup",      "run_production.py",    "uvicorn app:app --host 0.0.0.0 --port 5000"],
    ["IaC",              "Terraform",            "aws-deployment/main.tf provisions all AWS resources"],
    ["CI deploy",        "deploy_today.ps1",     "SCP changed files → pip install → supervisorctl restart"],
]
story.append(feature_table(
    ["Component", "Service", "Details"],
    [r for r in infra_rows[1:]],
    col_widths=[35*mm, 45*mm, CW - 80*mm]
))
story.append(Spacer(1, 3 * mm))

story.append(Paragraph("<b>Environment Variables</b>", sH2))
env_rows = [
    ["Variable",              "Required", "Description"],
    ["FLASK_SECRET_KEY",      "Yes",      "256-bit session signing key"],
    ["DATABASE_URL",          "Yes",      "postgresql://user:pass@host:5432/db"],
    ["REDIS_URL",             "Optional", "redis://host:6379/0 — session and cache backend"],
    ["SESSION_COOKIE_SECURE", "Prod",     "'true' enforces HTTPS-only session cookie"],
    ["STATIC_CDN_URL",        "Optional", "CloudFront URL for static assets"],
    ["LOG_LEVEL",             "Optional", "DEBUG / INFO / WARNING / ERROR (default: INFO)"],
    ["S3_BUCKET",             "Optional", "S3 bucket name for off-site backup uploads"],
    ["PROVIDER_ADMIN_PASSWORD","Optional","Provider admin portal password (default: provider2026!)"],
]
story.append(feature_table(
    ["Variable", "Required", "Description"],
    [r for r in env_rows[1:]],
    col_widths=[55*mm, 20*mm, CW - 75*mm]
))

# ═══════════════════════════════════════════════════════════════════════════════
# APPENDIX A — APPLICATION SCREENSHOTS
# ═══════════════════════════════════════════════════════════════════════════════
story.append(PageBreak())
story += section_header("Appendix A — Application Screenshots", "◆")
story.append(Paragraph(
    "The following screenshots show the EBMS user interface across key modules. "
    "Each screen mirrors the live application layout including sidebar navigation, "
    "KPI cards, data tables, and status badges.",
    sBody))
story.append(Spacer(1, 3 * mm))

_screenshots = [
    ("01_dashboard.png",
     "Figure 1 — Main Dashboard",
     "The central dashboard aggregates live KPIs from all modules: employee count, "
     "VAT payable, open CPOs, inventory alerts, open bids, payroll totals, pending "
     "letters, and SIEM alerts. Recent transactions are shown with account codes and status badges."),
    ("02_payroll.png",
     "Figure 2 — Payroll Module — Employee List",
     "Employee register listing all staff with department, position, and basic salary. "
     "Summary cards display total gross payroll, PAYE income tax, and employer pension. "
     "Action buttons link to Add Employee, Org Chart, Calculate Payroll, and Excel Export."),
    ("03_vat.png",
     "Figure 3 — VAT Portal — Financial Summary",
     "VAT portal summary showing annual income, expenses, VAT collected (output), VAT paid "
     "(input), and net VAT payable to ERCA. The income register displays VAT type, gross/net "
     "split, and calculated VAT per transaction."),
    ("04_letters.png",
     "Figure 4 — Letters & E-Signatures",
     "Letter register with sequential REF-xxxx references, PM/FM/MD signature status "
     "indicators (green = signed, grey = pending), and status badges. "
     "Navigation links to Compose, Mail Tracker, and Signature drawing pads."),
    ("05_inventory.png",
     "Figure 5 — Inventory Management",
     "Inventory items list with SKU, category, current stock vs minimum stock threshold, "
     "unit price, and colour-coded status badges. Red 'Low' badges alert on items below "
     "reorder point. Summary cards show total items, total value, and open requisitions."),
    ("06_siem.png",
     "Figure 6 — SIEM Security Event Log",
     "Security dashboard showing active HIGH/MEDIUM alerts in a red alert panel, followed "
     "by the full event log with IP addresses, authenticated usernames, module paths, "
     "endpoint URLs, and success/failure status badges for every HTTP mutation."),
    ("07_bids.png",
     "Figure 7 — Bid Tracker",
     "Active bid register with organisation name, submission deadline, bid amount (ETB), "
     "assigned case handler, and colour-coded status badges (Open/Submitted/Won/Lost). "
     "Summary cards show open bids, submitted bids, won bids, and total won contract value."),
]

for idx, (fname, title, caption) in enumerate(_screenshots):
    img_path = os.path.join(SCREENSHOTS_DIR, fname)
    if not os.path.exists(img_path):
        story.append(Paragraph(f"[Screenshot not found: {fname}]", sNote))
        continue

    story.append(Paragraph(f"<b>{title}</b>", sH2))

    img_obj = RLImage(img_path, width=CW, height=CW * 580/900)
    img_table = Table(
        [[img_obj]],
        colWidths=[CW],
        style=TableStyle([
            ("BOX",           (0,0), (-1,-1), 1.5, C_TEAL),
            ("TOPPADDING",    (0,0), (-1,-1), 0),
            ("BOTTOMPADDING", (0,0), (-1,-1), 0),
            ("LEFTPADDING",   (0,0), (-1,-1), 0),
            ("RIGHTPADDING",  (0,0), (-1,-1), 0),
        ])
    )
    cap_table = Table(
        [[Paragraph(caption, S(f"Cap{idx}",
                               fontName="Helvetica-Oblique", fontSize=8,
                               textColor=C_SUBTEXT, leading=11))]],
        colWidths=[CW],
        style=TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), C_LIGHT),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 8),
            ("BOX",           (0,0), (-1,-1), 0.5, C_BORDER),
        ])
    )
    story.append(KeepTogether([img_table, cap_table]))
    story.append(PageBreak())

# ═══════════════════════════════════════════════════════════════════════════════
# APPENDIX B — SAMPLE DATA SHEET
# ═══════════════════════════════════════════════════════════════════════════════
story += section_header("Appendix B — Sample Data Sheet", "◆")
story.append(Paragraph(
    "Representative sample records from each major module, "
    "illustrating the data fields, value formats, and Ethiopian-specific "
    "content stored in EBMS.",
    sBody))
story.append(Spacer(1, 3 * mm))

# B1 Employees
story.append(Paragraph("B1. Employee Register (Sample)", sH2))
story.append(feature_table(
    ["Emp ID", "Name", "Department", "Position", "Basic Salary", "Hire Date", "TIN"],
    [
        ["EMP-001", "Abebe Girma",      "Finance",    "Finance Manager",  "ETB 45,000", "2021-03-01", "0021384"],
        ["EMP-002", "Tigist Bekele",    "HR",         "HR Officer",       "ETB 32,000", "2022-06-15", "0038271"],
        ["EMP-003", "Yohannes Tadesse", "IT",         "Systems Admin",    "ETB 38,500", "2020-09-01", "0049153"],
        ["EMP-004", "Hiwot Alemu",      "Operations", "Project Manager",  "ETB 52,000", "2019-01-10", "0061847"],
        ["EMP-005", "Solomon Mengesha", "Finance",    "Accountant",       "ETB 28,000", "2023-02-20", "0074392"],
    ],
    col_widths=[18*mm, 35*mm, 26*mm, 35*mm, 26*mm, 22*mm, 18*mm]
))
story.append(Spacer(1, 3 * mm))

# B2 Payroll calc
story.append(Paragraph("B2. Payroll Calculation — March 2026 (EMP-004: Hiwot Alemu)", sH2))
story.append(feature_table(
    ["Component", "Amount (ETB)", "Notes"],
    [
        ["Basic Salary",              "52,000",   "Gross monthly basic"],
        ["Transport Allowance",       "2,000",    "Non-taxable per proclamation"],
        ["Housing Allowance",         "3,000",    "Taxable allowance"],
        ["Overtime (8 hrs x 1.25)",   "2,500",    "Regular overtime rate"],
        ["Gross Taxable Income",      "57,500",   "Basic + taxable allowances + OT"],
        ["Income Tax (PAYE)",         "-14,575",  "35% bracket: ETB 7,800 threshold"],
        ["Employee Pension (7%)",     "-3,640",   "7% of basic salary"],
        ["CPO Deduction",             "0",        "No outstanding CPO this period"],
        ["NET PAY",                   "41,285",   "Deposited to CBE account"],
        ["Employer Pension (11%)",    "5,720",    "Additional cost to organisation"],
    ],
    col_widths=[65*mm, 38*mm, CW - 103*mm]
))
story.append(Spacer(1, 3 * mm))

# B3 VAT Income
story.append(Paragraph("B3. VAT Income Records (Sample — March 2026)", sH2))
story.append(feature_table(
    ["Date", "Description", "Client", "Gross (ETB)", "VAT Type", "VAT (ETB)", "Net (ETB)"],
    [
        ["2026-03-01", "Consultancy Invoice #042",  "Ethio Telecom", "380,000", "Std 15%", "49,565",  "330,435"],
        ["2026-03-03", "Software License Supply",   "CBE Treasury",  "120,000", "Std 15%", "15,652",  "104,348"],
        ["2026-03-08", "Training Services",         "Awash Bank",    "75,000",  "Exempt",  "0",       "75,000"],
        ["2026-03-12", "Equipment Rental",          "MOF Ethiopia",  "95,000",  "Std 15%", "12,391",  "82,609"],
        ["2026-03-15", "IT Support Contract Q1",    "MoBiSA",        "210,000", "Std 15%", "27,391",  "182,609"],
    ],
    col_widths=[22*mm, 46*mm, 28*mm, 24*mm, 18*mm, 20*mm, 22*mm]
))
story.append(Spacer(1, 3 * mm))

# B4 Letters
story.append(Paragraph("B4. Letter Register with Signature Timestamps (Sample)", sH2))
story.append(feature_table(
    ["Ref", "Date", "Subject", "Recipient", "PM Signed", "FM Signed", "MD Signed", "Status"],
    [
        ["REF-0024", "Mar 15", "Equipment Procurement Approval",  "Ministry of Finance", "15-Mar 21:00", "15-Mar 21:30", "15-Mar 22:00", "Sent"],
        ["REF-0023", "Mar 14", "Q1 Audit Report Submission",      "ERCA",                "14-Mar 16:00", "14-Mar 16:45", "14-Mar 17:30", "Sent"],
        ["REF-0022", "Mar 13", "Staff Training Schedule FY2026",  "HR Department",       "13-Mar 09:00", "13-Mar 09:30", "—",            "Pending"],
        ["REF-0021", "Mar 12", "Vendor Contract Renewal Notice",  "Ethio Telecom",       "12-Mar 14:00", "—",            "—",            "Pending"],
    ],
    col_widths=[16*mm, 14*mm, 44*mm, 28*mm, 24*mm, 24*mm, 24*mm, 20*mm]
))
story.append(Spacer(1, 3 * mm))

# B5 CPO
story.append(Paragraph("B5. CPO Records (Sample)", sH2))
story.append(feature_table(
    ["CPO ID", "Payee Name", "Date", "Amount (ETB)", "Bid Ref", "Returned", "Return Date"],
    [
        ["CPO-001", "Abebe Girma",        "2026-01-10", "45,000",  "BID-018", "Yes", "2026-03-05"],
        ["CPO-002", "Ethio Suppliers Ltd","2026-01-15", "120,000", "BID-019", "No",  "—"],
        ["CPO-003", "Solomon Mengesha",   "2026-02-01", "30,000",  "BID-020", "Yes", "2026-02-28"],
        ["CPO-004", "Global Tech PLC",    "2026-02-14", "280,000", "BID-021", "No",  "—"],
        ["CPO-005", "Tigist Bekele",      "2026-03-01", "22,500",  "BID-022", "No",  "—"],
    ],
    col_widths=[18*mm, 40*mm, 22*mm, 28*mm, 20*mm, 18*mm, 24*mm]
))
story.append(Spacer(1, 3 * mm))

# B6 Inventory
story.append(Paragraph("B6. Inventory Items (Sample)", sH2))
story.append(feature_table(
    ["SKU", "Item Name", "Category", "Stock", "Min", "Unit Price (ETB)", "Method", "Status"],
    [
        ["ITM-001", "Dell Laptop 15\" i7",    "IT Equipment", "12", "5",  "85,000",  "FIFO", "OK"],
        ["ITM-002", "Projector Epson EB-X51", "AV Equipment", "3",  "2",  "42,000",  "FIFO", "OK"],
        ["ITM-003", "HP Printer LaserJet",    "IT Equipment", "2",  "3",  "28,500",  "FIFO", "Low"],
        ["ITM-004", "Office Chair Ergonomic", "Furniture",    "8",  "4",  "7,200",   "FIFO", "OK"],
        ["ITM-005", "Generator 10KVA",        "Power",        "1",  "1",  "195,000", "FIFO", "OK"],
        ["ITM-006", "UPS 3KVA APC",           "Power",        "0",  "2",  "32,000",  "FIFO", "Low"],
    ],
    col_widths=[16*mm, 38*mm, 24*mm, 12*mm, 10*mm, 28*mm, 16*mm, 26*mm]
))
story.append(Spacer(1, 3 * mm))

# B7 SIEM Events
story.append(Paragraph("B7. SIEM Security Events (Sample)", sH2))
story.append(feature_table(
    ["Timestamp", "IP Address", "User", "Module", "Endpoint", "Status"],
    [
        ["2026-03-15 22:31:04", "192.168.1.10", "admin",    "payroll", "POST /payroll/calculate",        "success"],
        ["2026-03-15 22:28:17", "192.168.1.10", "admin",    "letters", "POST /letters/sign",              "success"],
        ["2026-03-15 21:55:42", "41.94.22.180", "testuser", "api",     "GET /api/v1/export/employees",    "success"],
        ["2026-03-15 21:42:11", "41.94.22.180", "—",        "auth",    "POST /auth/login",                "failed"],
        ["2026-03-15 21:41:55", "41.94.22.180", "—",        "auth",    "POST /auth/login",                "failed"],
        ["2026-03-15 20:14:33", "10.0.0.5",     "finance",  "vat",     "POST /vat/income/add",            "success"],
    ],
    col_widths=[36*mm, 26*mm, 20*mm, 18*mm, 52*mm, 18*mm]
))

# ── Final spacer ──────────────────────────────────────────────────────────────
story.append(Spacer(1, 8 * mm))
story.append(hr(C_GOLD, 1.5))
story.append(Paragraph(
    "Ethiopian Business Management System  ·  Technical Capabilities Reference  ·  "
    f"Generated {date.today().strftime('%B %d, %Y')}  ·  Confidential",
    S("Footer2", fontName="Helvetica-Oblique", fontSize=8,
      textColor=C_SUBTEXT, alignment=TA_CENTER)
))

# ── Build PDF ─────────────────────────────────────────────────────────────────
doc.build(story, onFirstPage=cover_page, onLaterPages=_footer)
print(f"PDF generated: {OUT}")
