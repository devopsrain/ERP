"""
Generate realistic UI mock screenshots for the EBMS Technical Capabilities PDF.
Each screenshot mimics the actual HTML template layout using Pillow.
"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.join(os.path.dirname(__file__), "screenshots_temp")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Colour palette (mirroring the real UI Bootstrap + custom CSS) ──────────────
BG       = (245, 247, 250)      # off-white page bg
NAV_BG   = (26,  58,  92)       # C_NAVY sidebar
NAV_ACT  = (13, 115, 119)       # C_TEAL active item
TOPBAR   = (255, 255, 255)
CARD     = (255, 255, 255)
CARD_HDR = (13, 115, 119)
BORDER   = (208, 216, 228)
GREEN    = (56,  161, 105)
RED      = (229, 62,  62)
GOLD     = (240, 165,  0)
TEXT     = (30,  45,  61)
SUBTEXT  = (74,  85, 104)
WHITE    = (255, 255, 255)
LIGHT    = (245, 247, 250)
TABLE_H  = (26,  58,  92)
TABLE_A  = (236, 240, 246)


def load_font(size, bold=False):
    """Best-effort font loading — falls back to default if no TTF found."""
    try:
        name = "arialbd.ttf" if bold else "arial.ttf"
        return ImageFont.truetype(name, size)
    except Exception:
        try:
            path = "C:/Windows/Fonts/calibri.ttf" if not bold else "C:/Windows/Fonts/calibrib.ttf"
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()


def draw_sidebar(draw, W, H, active_label):
    """Draw the left navigation sidebar."""
    SW = 190
    draw.rectangle([(0, 0), (SW, H)], fill=NAV_BG)
    # Brand
    draw.rectangle([(0, 0), (SW, 56)], fill=(15, 40, 68))
    f_brand = load_font(13, bold=True)
    draw.text((12, 10), "EBMS", fill=GOLD, font=f_brand)
    draw.text((12, 28), "Business Suite", fill=(160, 195, 220), font=load_font(9))

    nav_items = [
        ("Dashboard",      46),
        ("VAT Portal",     68),
        ("Journal Entry",  90),
        ("Income/Expense", 112),
        ("Transactions",   134),
        ("Payroll",        156),
        ("Inventory",      178),
        ("CPO",            200),
        ("Bid Tracker",    222),
        ("Letters",        244),
        ("SIEM",           266),
        ("Backup",         288),
    ]
    for label, y in nav_items:
        is_active = label.lower() in active_label.lower() or active_label.lower() in label.lower()
        bg = NAV_ACT if is_active else NAV_BG
        draw.rectangle([(0, y - 2), (SW, y + 18)], fill=bg)
        prefix = "▶ " if is_active else "  "
        draw.text((14, y + 2), prefix + label, fill=WHITE if is_active else (180, 210, 230),
                  font=load_font(9, bold=is_active))

    # User info at bottom
    draw.rectangle([(0, H - 40), (SW, H)], fill=(15, 40, 68))
    draw.text((12, H - 30), "admin  |  My Company", fill=(140, 175, 200), font=load_font(8))
    return SW


def draw_topbar(draw, W, SW, title):
    TH = 44
    draw.rectangle([(SW, 0), (W, TH)], fill=TOPBAR)
    draw.line([(SW, TH), (W, TH)], fill=BORDER, width=1)
    f_title = load_font(12, bold=True)
    draw.text((SW + 16, 14), title, fill=TEXT, font=f_title)
    # Breadcrumb
    draw.text((W - 180, 16), "Home  /  " + title, fill=SUBTEXT, font=load_font(8))


def draw_stat_card(draw, x, y, w, h, label, value, sub, color=CARD_HDR):
    draw.rectangle([(x, y), (x + w, y + h)], fill=CARD, outline=BORDER)
    draw.rectangle([(x, y), (x + w, y + 5)], fill=color)
    draw.text((x + 10, y + 12), label, fill=SUBTEXT, font=load_font(8))
    draw.text((x + 10, y + 28), value, fill=TEXT, font=load_font(14, bold=True))
    draw.text((x + 10, y + 50), sub, fill=SUBTEXT, font=load_font(8))


def draw_table_header(draw, x, y, cols, widths):
    cx = x
    for col, w in zip(cols, widths):
        draw.rectangle([(cx, y), (cx + w, y + 20)], fill=TABLE_H)
        draw.text((cx + 5, y + 5), col, fill=WHITE, font=load_font(8, bold=True))
        cx += w


def draw_table_row(draw, x, y, cells, widths, even=True):
    bg = TABLE_A if even else CARD
    cx = x
    row_h = 18
    for cell, w in zip(cells, widths):
        draw.rectangle([(cx, y), (cx + w, y + row_h)], fill=bg, outline=BORDER)
        draw.text((cx + 5, y + 4), str(cell)[:20], fill=TEXT, font=load_font(8))
        cx += w


def draw_badge(draw, x, y, text, color):
    tw = len(text) * 6 + 10
    draw.rounded_rectangle([(x, y), (x + tw, y + 14)], radius=4, fill=color)
    draw.text((x + 5, y + 2), text, fill=WHITE, font=load_font(7, bold=True))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MAIN DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
def screenshot_dashboard():
    W, H = 900, 580
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    SW = draw_sidebar(draw, W, H, "Dashboard")
    draw_topbar(draw, W, SW, "Dashboard")

    # Stat cards row 1
    cw = 155
    y0 = 60
    stats = [
        ("Total Employees", "47", "+3 this month", GREEN),
        ("VAT Payable (ETB)", "142,800", "YTD", CARD_HDR),
        ("Open CPOs", "23", "ETB 1.2M outstanding", GOLD),
        ("Inventory Items", "312", "18 low stock", RED),
    ]
    for i, (label, val, sub, col) in enumerate(stats):
        draw_stat_card(draw, SW + 12 + i * (cw + 8), y0, cw, 72, label, val, sub, col)

    # Second row cards
    stats2 = [
        ("Open Bids", "8", "3 deadlines <7 days", GOLD),
        ("Payroll (ETB)", "2,340,000", "March 2026", GREEN),
        ("Letters Pending", "4", "2 awaiting MD sign", RED),
        ("SIEM Alerts", "3", "1 high severity", RED),
    ]
    for i, (label, val, sub, col) in enumerate(stats2):
        draw_stat_card(draw, SW + 12 + i * (cw + 8), y0 + 82, cw, 72, label, val, sub, col)

    # Recent transactions table
    tx = SW + 12
    ty = y0 + 170
    draw.rectangle([(tx, ty), (W - 12, ty + 24)], fill=CARD_HDR)
    draw.text((tx + 10, ty + 6), "Recent Transactions", fill=WHITE, font=load_font(10, bold=True))

    cols = ["Date", "Description", "Account", "Debit (ETB)", "Credit (ETB)", "Status"]
    widths = [68, 155, 110, 82, 82, 65]
    draw_table_header(draw, tx, ty + 24, cols, widths)
    rows = [
        ("Mar 15, 2026", "Staff Salary Payment",     "1001 - CBE", "2,340,000", "",         "Posted"),
        ("Mar 14, 2026", "VAT Payment to ERCA",      "2201 - VAT", "142,800",   "",         "Posted"),
        ("Mar 13, 2026", "Office Supplies Purchase",  "5101 - Exp", "8,450",    "",         "Pending"),
        ("Mar 12, 2026", "Client Invoice #INV-0042",  "1201 - AR",  "",         "380,000",  "Posted"),
        ("Mar 11, 2026", "Equipment Rental Income",   "4001 - Rev", "",         "95,000",   "Posted"),
        ("Mar 10, 2026", "Internet & IT Services",    "5201 - IT",  "12,600",   "",         "Pending"),
        ("Mar 09, 2026", "CPO Returned - Bid #B-009", "1101 - Csh", "",         "45,000",   "Posted"),
    ]
    for i, row in enumerate(rows):
        draw_table_row(draw, tx, ty + 44 + i * 18, row, widths, even=(i % 2 == 0))

    img.save(os.path.join(OUT_DIR, "01_dashboard.png"), dpi=(150, 150))
    print("  01_dashboard.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PAYROLL MODULE
# ═══════════════════════════════════════════════════════════════════════════════
def screenshot_payroll():
    W, H = 900, 580
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    SW = draw_sidebar(draw, W, H, "Payroll")
    draw_topbar(draw, W, SW, "Payroll — Employee List")

    # Stats
    tx = SW + 12
    stats = [
        ("Total Employees", "47",           "Active", GREEN),
        ("Gross Payroll",   "ETB 2,340,000","March 2026", CARD_HDR),
        ("IT (Tax)",        "ETB 287,450",  "PAYE deducted", RED),
        ("Pension (Empr.)", "ETB 257,400",  "11% employer", GOLD),
    ]
    cw = 155
    for i, (label, val, sub, col) in enumerate(stats):
        draw_stat_card(draw, tx + i * (cw + 8), 56, cw, 72, label, val, sub, col)

    # Buttons row
    by = 136
    for bx, label, col in [(tx, "+ Add Employee", CARD_HDR), (tx + 150, "Org Chart", NAV_BG),
                            (tx + 260, "Calculate Payroll", GREEN), (tx + 400, "Export", GOLD)]:
        draw.rounded_rectangle([(bx, by), (bx + 125, by + 24)], radius=4, fill=col)
        draw.text((bx + 10, by + 6), label, fill=WHITE, font=load_font(9, bold=True))

    # Employee table
    ey = 170
    cols = ["Emp ID", "Name",             "Department",  "Position",           "Basic Salary", "Status"]
    widths = [55, 140, 100, 130, 95, 62]
    draw.rectangle([(tx, ey), (W - 12, ey + 24)], fill=CARD_HDR)
    draw.text((tx + 10, ey + 6), "Employee Register", fill=WHITE, font=load_font(10, bold=True))
    draw_table_header(draw, tx, ey + 24, cols, widths)

    emp_rows = [
        ("EMP-001", "Abebe Girma",       "Finance",    "Finance Manager",    "45,000",  "Active"),
        ("EMP-002", "Tigist Bekele",      "HR",         "HR Officer",         "32,000",  "Active"),
        ("EMP-003", "Yohannes Tadesse",   "IT",         "Systems Admin",      "38,500",  "Active"),
        ("EMP-004", "Hiwot Alemu",        "Operations", "Project Manager",    "52,000",  "Active"),
        ("EMP-005", "Solomon Mengesha",   "Finance",    "Accountant",         "28,000",  "Active"),
        ("EMP-006", "Marta Haile",        "Operations", "Operations Analyst", "30,000",  "Active"),
        ("EMP-007", "Dawit Worku",        "IT",         "Developer",          "42,000",  "Active"),
        ("EMP-008", "Selam Tesfaye",      "HR",         "HR Manager",         "48,000",  "Active"),
        ("EMP-009", "Bereket Assefa",     "Finance",    "Senior Accountant",  "36,000",  "Active"),
    ]
    for i, row in enumerate(emp_rows):
        draw_table_row(draw, tx, ey + 44 + i * 18, row, widths, even=(i % 2 == 0))
        # Status badge
        bx = tx + sum(widths[:5]) + 4
        by2 = ey + 44 + i * 18 + 2
        draw_badge(draw, bx, by2, row[5], GREEN)

    img.save(os.path.join(OUT_DIR, "02_payroll.png"), dpi=(150, 150))
    print("  02_payroll.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. VAT PORTAL
# ═══════════════════════════════════════════════════════════════════════════════
def screenshot_vat():
    W, H = 900, 580
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    SW = draw_sidebar(draw, W, H, "VAT Portal")
    draw_topbar(draw, W, SW, "VAT Portal — Financial Summary")

    tx = SW + 12
    # Summary cards
    summary = [
        ("Total Income (Gross)",    "ETB 3,820,000", "This year",     GREEN),
        ("Total Expenses (Gross)",  "ETB 1,245,600", "This year",     RED),
        ("VAT Collected",           "ETB 498,000",   "Output VAT",    CARD_HDR),
        ("VAT Paid",                "ETB 162,000",   "Input VAT",     NAV_BG),
    ]
    cw = 155
    for i, (label, val, sub, col) in enumerate(summary):
        draw_stat_card(draw, tx + i * (cw + 8), 56, cw, 72, label, val, sub, col)

    net_y = 136
    draw.rectangle([(tx, net_y), (tx + 645, net_y + 36)], fill=(230, 248, 234), outline=GREEN)
    draw.text((tx + 12, net_y + 6), "Net VAT Payable to ERCA:", fill=TEXT, font=load_font(10, bold=True))
    draw.text((tx + 250, net_y + 6), "ETB 336,000", fill=GREEN, font=load_font(14, bold=True))
    draw.text((tx + 400, net_y + 10), "(Output VAT – Input VAT)", fill=SUBTEXT, font=load_font(8))

    # Income table
    iy = net_y + 48
    draw.rectangle([(tx, iy), (W - 12, iy + 24)], fill=CARD_HDR)
    draw.text((tx + 10, iy + 6), "Income Records — March 2026", fill=WHITE, font=load_font(10, bold=True))

    cols = ["Date", "Description", "Client", "Gross (ETB)", "VAT Type", "VAT (ETB)", "Net (ETB)"]
    widths = [60, 148, 100, 75, 75, 70, 70]
    draw_table_header(draw, tx, iy + 24, cols, widths)
    inc_rows = [
        ("Mar 01", "Consultancy Invoice #042",  "Ethio Telecom", "380,000", "Standard 15%", "49,565", "330,435"),
        ("Mar 03", "Software License Supply",   "CBE Treasury",  "120,000", "Standard 15%", "15,652", "104,348"),
        ("Mar 08", "Training Services",         "Awash Bank",    "75,000",  "Exempt",        "0",      "75,000"),
        ("Mar 12", "Equipment Rental",          "MOF Ethiopia",  "95,000",  "Standard 15%", "12,391", "82,609"),
        ("Mar 15", "IT Support Contract Q1",    "MoBiSA",        "210,000", "Standard 15%", "27,391", "182,609"),
        ("Mar 15", "Audit Services — FY2025",   "Zemen Bank",    "450,000", "Standard 15%", "58,696", "391,304"),
    ]
    for i, row in enumerate(inc_rows):
        draw_table_row(draw, tx, iy + 44 + i * 18, row, widths, even=(i % 2 == 0))

    img.save(os.path.join(OUT_DIR, "03_vat.png"), dpi=(150, 150))
    print("  03_vat.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LETTERS / E-SIGNATURE
# ═══════════════════════════════════════════════════════════════════════════════
def screenshot_letters():
    W, H = 900, 580
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    SW = draw_sidebar(draw, W, H, "Letters")
    draw_topbar(draw, W, SW, "Letters & E-Signatures")

    tx = SW + 12

    # Stat cards
    stats = [
        ("Total Letters", "24", "All time", CARD_HDR),
        ("Fully Signed",  "19", "PM + FM + MD", GREEN),
        ("Pending Sign",  "4",  "Awaiting signatures", GOLD),
        ("Sent",          "21", "Dispatched", CARD_HDR),
    ]
    cw = 155
    for i, (label, val, sub, col) in enumerate(stats):
        draw_stat_card(draw, tx + i * (cw + 8), 56, cw, 72, label, val, sub, col)

    # Compose button
    draw.rounded_rectangle([(tx, 136), (tx + 140, 160)], radius=4, fill=CARD_HDR)
    draw.text((tx + 10, 142), "+ Compose Letter", fill=WHITE, font=load_font(9, bold=True))
    draw.rounded_rectangle([(tx + 148, 136), (tx + 280, 160)], radius=4, fill=NAV_BG)
    draw.text((tx + 158, 142), "Mail Tracker", fill=WHITE, font=load_font(9, bold=True))
    draw.rounded_rectangle([(tx + 288, 136), (tx + 400, 160)], radius=4, fill=GOLD)
    draw.text((tx + 298, 142), "Signatures", fill=WHITE, font=load_font(9, bold=True))

    # Letter table
    ly = 170
    draw.rectangle([(tx, ly), (W - 12, ly + 24)], fill=CARD_HDR)
    draw.text((tx + 10, ly + 6), "Letter Register", fill=WHITE, font=load_font(10, bold=True))
    cols = ["Ref", "Date", "Subject", "Recipient", "PM", "FM", "MD", "Status"]
    widths = [55, 65, 165, 110, 32, 32, 32, 67]
    draw_table_header(draw, tx, ly + 24, cols, widths)

    letters = [
        ("REF-0024", "Mar 15", "Equipment Procurement Approval",  "Ministry of Finance", "✓", "✓", "✓", "Sent"),
        ("REF-0023", "Mar 14", "Q1 Audit Report Submission",       "ERCA",                "✓", "✓", "✓", "Sent"),
        ("REF-0022", "Mar 13", "Staff Training Schedule FY2026",   "HR Department",       "✓", "✓", "–", "Pending"),
        ("REF-0021", "Mar 12", "Vendor Contract Renewal Notice",   "Ethio Telecom",       "✓", "–", "–", "Pending"),
        ("REF-0020", "Mar 10", "Board Meeting Agenda March 2026",  "Board Members",       "✓", "✓", "✓", "Sent"),
        ("REF-0019", "Mar 09", "Budget Reallocation Request",      "CFO Office",          "✓", "✓", "✓", "Sent"),
        ("REF-0018", "Mar 07", "Compliance Certificate Request",   "ERCA Office",         "✓", "✓", "✓", "Sent"),
    ]
    for i, row in enumerate(letters):
        draw_table_row(draw, tx, ly + 44 + i * 18, row, widths, even=(i % 2 == 0))
        # PM/FM/MD badges
        status_col = {"Sent": GREEN, "Pending": GOLD, "Draft": SUBTEXT}
        bx_start = tx + sum(widths[:4])
        for j, sig in enumerate([row[4], row[5], row[6]]):
            c = GREEN if sig == "✓" else SUBTEXT
            bx = bx_start + j * widths[4] + 3
            by_pos = ly + 44 + i * 18 + 2
            draw.ellipse([(bx, by_pos), (bx + 22, by_pos + 14)], fill=c)
            draw.text((bx + 6, by_pos + 2), sig, fill=WHITE, font=load_font(8, bold=True))
        # Status badge
        status_txt = row[7]
        bx_s = tx + sum(widths[:7]) + 3
        draw_badge(draw, bx_s, ly + 44 + i * 18 + 2, status_txt,
                   status_col.get(status_txt, SUBTEXT))

    img.save(os.path.join(OUT_DIR, "04_letters.png"), dpi=(150, 150))
    print("  04_letters.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. INVENTORY MODULE
# ═══════════════════════════════════════════════════════════════════════════════
def screenshot_inventory():
    W, H = 900, 580
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    SW = draw_sidebar(draw, W, H, "Inventory")
    draw_topbar(draw, W, SW, "Inventory Management")

    tx = SW + 12
    stats = [
        ("Total Items",     "312",         "Across all categories", CARD_HDR),
        ("Total Value",     "ETB 8.4M",    "At cost price",         GREEN),
        ("Low Stock",       "18",          "Below reorder point",   RED),
        ("Pending Reqs.",   "7",           "Requisitions open",     GOLD),
    ]
    cw = 155
    for i, (label, val, sub, col) in enumerate(stats):
        draw_stat_card(draw, tx + i * (cw + 8), 56, cw, 72, label, val, sub, col)

    iy = 140
    draw.rectangle([(tx, iy), (W - 12, iy + 24)], fill=CARD_HDR)
    draw.text((tx + 10, iy + 6), "Inventory Items", fill=WHITE, font=load_font(10, bold=True))

    cols = ["SKU", "Item Name", "Category", "Stock", "Min Stock", "Unit Price (ETB)", "Status"]
    widths = [60, 155, 90, 48, 58, 100, 67]
    draw_table_header(draw, tx, iy + 24, cols, widths)

    items = [
        ("ITM-001", "Dell Laptop 15\" i7",    "IT Equipment",   "12", "5",  "85,000",  "OK"),
        ("ITM-002", "Projector Epson EB-X51",  "AV Equipment",   "3",  "2",  "42,000",  "OK"),
        ("ITM-003", "HP Printer LaserJet",     "IT Equipment",   "2",  "3",  "28,500",  "Low"),
        ("ITM-004", "Office Chair Ergonomic",  "Furniture",      "8",  "4",  "7,200",   "OK"),
        ("ITM-005", "Generator 10KVA",         "Power",          "1",  "1",  "195,000", "OK"),
        ("ITM-006", "UPS 3KVA APC",            "Power",          "0",  "2",  "32,000",  "Low"),
        ("ITM-007", "Conference Table 12-Seat","Furniture",      "2",  "1",  "55,000",  "OK"),
        ("ITM-008", "Network Switch 24-Port",  "Networking",     "1",  "2",  "18,500",  "Low"),
    ]
    for i, row in enumerate(items):
        draw_table_row(draw, tx, iy + 44 + i * 18, row, widths, even=(i % 2 == 0))
        s_col = RED if row[6] == "Low" else GREEN
        bx_s = tx + sum(widths[:6]) + 3
        draw_badge(draw, bx_s, iy + 44 + i * 18 + 2, row[6], s_col)

    img.save(os.path.join(OUT_DIR, "05_inventory.png"), dpi=(150, 150))
    print("  05_inventory.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SIEM SECURITY MODULE
# ═══════════════════════════════════════════════════════════════════════════════
def screenshot_siem():
    W, H = 900, 580
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    SW = draw_sidebar(draw, W, H, "SIEM")
    draw_topbar(draw, W, SW, "SIEM — Security Event Log")

    tx = SW + 12
    stats = [
        ("Events Today",   "247",  "All modules", CARD_HDR),
        ("Active Alerts",  "3",    "1 high, 2 med", RED),
        ("Failed Logins",  "8",    "Last 24 hours", GOLD),
        ("File Imports",   "12",   "Today", GREEN),
    ]
    cw = 155
    for i, (label, val, sub, col) in enumerate(stats):
        draw_stat_card(draw, tx + i * (cw + 8), 56, cw, 72, label, val, sub, col)

    # Alerts box
    ay = 136
    draw.rectangle([(tx, ay), (tx + 645, ay + 60)], fill=(255, 245, 245), outline=RED)
    draw.text((tx + 8, ay + 6), "⚠  ACTIVE SECURITY ALERTS", fill=RED, font=load_font(9, bold=True))
    alerts = [
        ("[HIGH]  Brute-force detected — 8 failed logins from IP 41.94.xx.xx in 5 minutes"),
        ("[MED]   Large file import: transactions_march.xlsx  (42 MB, 18,450 rows)"),
        ("[MED]   User 'testuser' accessed /api/v1/export/employees outside business hours"),
    ]
    for j, alert in enumerate(alerts):
        c = RED if "[HIGH]" in alert else GOLD
        draw.text((tx + 8, ay + 22 + j * 13), alert, fill=c, font=load_font(8))

    # Events table
    ey = ay + 72
    draw.rectangle([(tx, ey), (W - 12, ey + 24)], fill=TABLE_H)
    draw.text((tx + 10, ey + 6), "Security Event Log", fill=WHITE, font=load_font(10, bold=True))

    cols = ["Timestamp",      "IP Address",   "User",   "Module",   "Endpoint",           "Status"]
    widths = [100, 90, 75, 75, 180, 62]
    draw_table_header(draw, tx, ey + 24, cols, widths)

    events = [
        ("2026-03-15 22:31", "192.168.1.10",  "admin",    "payroll",  "POST /payroll/calculate",  "success"),
        ("2026-03-15 22:28", "192.168.1.10",  "admin",    "letters",  "POST /letters/sign",        "success"),
        ("2026-03-15 21:55", "41.94.22.180",  "testuser", "api",      "GET /api/v1/export/employ", "success"),
        ("2026-03-15 21:42", "41.94.22.180",  "—",        "auth",     "POST /auth/login",          "failed"),
        ("2026-03-15 21:41", "41.94.22.180",  "—",        "auth",     "POST /auth/login",          "failed"),
        ("2026-03-15 20:14", "10.0.0.5",      "finance",  "vat",      "POST /vat/income/add",      "success"),
        ("2026-03-15 19:30", "10.0.0.5",      "finance",  "cpo",      "POST /cpo/import",          "success"),
    ]
    for i, row in enumerate(events):
        draw_table_row(draw, tx, ey + 44 + i * 18, row, widths, even=(i % 2 == 0))
        s_col = GREEN if row[5] == "success" else RED
        bx_s = tx + sum(widths[:5]) + 3
        draw_badge(draw, bx_s, ey + 44 + i * 18 + 2, row[5], s_col)

    img.save(os.path.join(OUT_DIR, "06_siem.png"), dpi=(150, 150))
    print("  06_siem.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. BID TRACKER
# ═══════════════════════════════════════════════════════════════════════════════
def screenshot_bids():
    W, H = 900, 560
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    SW = draw_sidebar(draw, W, H, "Bid Tracker")
    draw_topbar(draw, W, SW, "Bid Tracker")

    tx = SW + 12
    stats = [
        ("Open Bids",      "8",            "Active tenders",     CARD_HDR),
        ("Submitted",      "5",            "Awaiting result",    GOLD),
        ("Won",            "12",           "FY2025-26",          GREEN),
        ("Total Value Won","ETB 18.4M",    "Won contracts",      GREEN),
    ]
    cw = 155
    for i, (label, val, sub, col) in enumerate(stats):
        draw_stat_card(draw, tx + i * (cw + 8), 56, cw, 72, label, val, sub, col)

    by = 140
    draw.rectangle([(tx, by), (W - 12, by + 24)], fill=CARD_HDR)
    draw.text((tx + 10, by + 6), "Active Bids", fill=WHITE, font=load_font(10, bold=True))

    cols = ["Ref", "Title", "Organization", "Deadline", "Amount (ETB)", "Handler", "Status"]
    widths = [60, 170, 110, 70, 80, 80, 60]
    draw_table_header(draw, tx, by + 24, cols, widths)

    bids = [
        ("BID-024", "IT Infrastructure Upgrade",     "Ministry of Finance",   "Mar 22, 2026", "4,500,000",  "Hiwot A.",  "Open"),
        ("BID-023", "Accounting Software Supply",     "Awash Insurance",       "Mar 28, 2026", "850,000",    "Abebe G.",  "Open"),
        ("BID-022", "Security Audit Services FY26",   "Commercial Bank of ET", "Apr 05, 2026", "1,200,000",  "Solomon M.","Open"),
        ("BID-021", "Staff Training Programme Q2",    "ERCA",                  "Apr 12, 2026", "320,000",    "Tigist B.", "Submitted"),
        ("BID-020", "ERP Consultancy 2026-27",        "Ethio Telecom",         "Apr 20, 2026", "6,800,000",  "Dawit W.",  "Submitted"),
        ("BID-019", "Financial Reporting System",     "NBE Ethiopia",          "Mar 30, 2026", "2,100,000",  "Marta H.",  "Open"),
    ]
    status_colors = {"Open": CARD_HDR, "Submitted": GOLD, "Won": GREEN, "Lost": RED}
    for i, row in enumerate(bids):
        draw_table_row(draw, tx, by + 44 + i * 18, row, widths, even=(i % 2 == 0))
        bx_s = tx + sum(widths[:6]) + 3
        draw_badge(draw, bx_s, by + 44 + i * 18 + 2, row[6], status_colors.get(row[6], SUBTEXT))

    img.save(os.path.join(OUT_DIR, "07_bids.png"), dpi=(150, 150))
    print("  07_bids.png")


# ═══════════════════════════════════════════════════════════════════════════════
# RUN ALL
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Generating UI mockup screenshots...")
    screenshot_dashboard()
    screenshot_payroll()
    screenshot_vat()
    screenshot_letters()
    screenshot_inventory()
    screenshot_siem()
    screenshot_bids()
    print(f"Done — saved to {OUT_DIR}/")
