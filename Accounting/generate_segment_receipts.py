"""
Generate 9 separate sales receipts (PDF) from the Proforma Invoice,
one per product segment (1-9).
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
import os

# ── Shared invoice header data ─────────────────────────────────────────────────
INVOICE_NUMBER = "#1605450029"
INVOICE_DATE   = "18 February 2026"
ORDER_DATE     = "18 February 2026"
VALID_UNTIL    = "18 March 2026"

SELLER = {
    "name":    "LTC",
    "address": "Nelikkokuja 8\n02230, Espoo\nFinland",
    "phone":   "+358 41 544 6513",
}

BUYER = {
    "name":    "Devopsrain Technologies\nFanuel Desalegn",
    "address": "Mina Mall, Third Floor\nAddis Ababa, Ethiopia",
    "phone":   "+251 911 609 001",
    "email":   "fanuel.desalegn@devopsrain.com",
}

BANK = {
    "name":    "Wise Bank",
    "address": "Rue du Trône 100, 3rd Floor, Brussels, 1050, Belgium",
    "iban":    "BE71 9054 9372 6569",
    "swift":   "TRWIBEB1XXX",
}

SHIPPING = [
    ("Country of Origin",  "Multiple (CN, US, EU)"),
    ("Loading Port",       "Helsinki, Finland"),
    ("Final Destination",  "Addis Ababa, Ethiopia"),
    ("Terms of Delivery",  "FCA (Free Carrier)"),
    ("Payment Method",     "TT (Telegraphic Transfer)"),
]

TERMS = [
    "1. Payment must be made via telegraphic transfer (TT) to the bank account specified above.",
    "2. This proforma invoice is valid until the date specified above.",
    "3. Prices are quoted in EUR (Euro) and are FOB (Free On Board) from the loading port.",
    "4. Buyer is responsible for all customs duties, taxes, and import fees in the destination country.",
    "5. Delivery terms are FCA (Free Carrier) as per Incoterms 2020.",
    "6. All goods remain the property of the seller until full payment is received.",
]

# ── Nine segments ──────────────────────────────────────────────────────────────
# Each entry: (segment_no, segment_title, [ (item_no, part_no, description, qty, unit_price) ])
SEGMENTS = [
    (1, "SERVERS", [
        (1, "R430-BASE",      "Dell PowerEdge R430 Rack Server",                            1, 3450.00),
    ]),
    (2, "SWITCHES", [
        (2, "C9200L-48P-4G",  "Cisco Catalyst 9200L Switch - 48 Port Data Only",            2, 4850.00),
        (3, "C9300-24T",      "Cisco Catalyst C9300-24T Switch - 24-Port Gigabit",          1, 6750.00),
        (4, "MS225-48LP",     "Cisco Meraki MS225-48LP PoE+ - 48x GbE, 2x SFP, RPS",       1, 3700.00),
        (5, "MS210-48LP",     "Cisco Meraki MS210-48LP PoE+ 48x PoE+ GbE, 2x SFP, RPS",    2, 1900.00),
        (6, "MS125-48LP",     "Cisco Meraki MS125-48LP - 48x PoE+ GbE, 1x SFP, RPS",       1, 1700.00),
    ]),
    (3, "ROUTERS", [
        (7, "MX75-HW",        "Cisco Meraki MX75 Security Appliance",                       2, 2250.00),
        (8, "ISR4331/K9",     "Cisco ISR4331 Integrated Services Router 3-Port Gigabit",    1, 2600.00),
        (9, "C8200-1N-4T",    "Cisco Catalyst 8200L Router",                                1, 2150.00),
       (10, "C1111-4P",       "Cisco Catalyst C1111-4P Router - 4-Port",                    1,  795.00),
    ]),
    (4, "WIRELESS NETWORKING EQUIPMENT", [
       (11, "CW9164I-MR",     "Cisco Catalyst 9164I Access Point - Wi-Fi 6E Internal Antenna", 4, 1900.00),
       (12, "MR36-HW",        "Cisco Meraki MR36 Access Point - Wi-Fi 6 Cloud-Managed",     2, 1050.00),
       (13, "MR44-HW",        "Cisco Meraki MR44 Access Point - Wi-Fi 6",                   1, 1500.00),
    ]),
    (5, "CABLING AND RELATED ACCESSORIES", [
       (14, "MA-CBL-40G-50CM","Cisco 40GbE QSFP+ Cable - 0.5m Direct Attach",              10,   78.00),
       (15, "OM4-LC-LC",      "Fiber Optic Cable OM4 LC-LC",                                5,   52.00),
    ]),
    (6, "BACKUP AND STORAGE SYSTEMS", [
       (16, "1HSUPERV11U",    "Peli Hardigg Super-V Rack Case 11U 24\" Rack Length",        2, 3950.00),
       (17, "RACK-151-DC",    "Rack Solutions Server Rack 16U Enclosed Rack Cabinet",       1, 2050.00),
    ]),
    (7, "MONITORS & DISPLAYS", [
       (18, "P2419H",         "DELL P2419H Monitor - 24\" Professional",                    4,  230.00),
       (19, "GL2580H",        "BenQ GL2580H Monitor - 24.5\" Full HD",                      3,  135.00),
       (20, "65EEGAC1EU",     "Lenovo L24e-20 Monitor - 23.8\" Full HD",                    1,  145.00),
    ]),
    (8, "PRINTERS", [
       (21, "IMC3000",        "Nashuatec IMC3000 MFP - Multifunction Color Printer",        1, 2850.00),
       (22, "ZD421-T",        "Zebra ZD421 Printer - Thermal Transfer Desktop",             1,  460.00),
    ]),
    (9, "PERIPHERALS & ACCESSORIES", [
       (23, "26599-999-999",  "Jabra Evolve2 65 MS - Wireless Headset",                     1,  240.00),
       (24, "920-002478",     "Logitech K120 Keyboard - USB Wired",                        15,   19.00),
    ]),
]

# ── Colour palette ──────────────────────────────────────────────────────────────
DARK_BLUE   = colors.HexColor("#1B3A6B")
MID_BLUE    = colors.HexColor("#2E5FA3")
LIGHT_BLUE  = colors.HexColor("#D6E4F7")
ACCENT      = colors.HexColor("#F0A500")
LIGHT_GRAY  = colors.HexColor("#F7F7F7")
MID_GRAY    = colors.HexColor("#CCCCCC")
WHITE       = colors.white
BLACK       = colors.black


def _style(name, **kw):
    base = getSampleStyleSheet()["Normal"]
    kw.setdefault("fontName",  base.fontName)
    kw.setdefault("fontSize",  base.fontSize)
    kw.setdefault("leading",   base.leading)
    return ParagraphStyle(name, **kw)


def build_receipt(seg_no, seg_title, items, out_path):
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=12*mm,  bottomMargin=15*mm,
    )

    W = A4[0] - 30*mm     # usable width
    story = []

    # ── Header bar ────────────────────────────────────────────────────────────
    hdr_data = [[
        Paragraph(
            "<font color='white'><b>DEVOPSRAIN TECHNOLOGIES</b></font>",
            _style("hdrL", fontSize=14, leading=18, textColor=WHITE, fontName="Helvetica-Bold")
        ),
        Paragraph(
            "<font color='white'><b>SALES RECEIPT</b></font>",
            _style("hdrR", fontSize=14, leading=18, textColor=WHITE,
                   fontName="Helvetica-Bold", alignment=TA_RIGHT)
        ),
    ]]
    hdr_tbl = Table(hdr_data, colWidths=[W*0.55, W*0.45])
    hdr_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), DARK_BLUE),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ("RIGHTPADDING",  (0,0), (-1,-1), 10),
    ]))
    story.append(hdr_tbl)
    story.append(Spacer(1, 3*mm))

    # ── Segment ribbon ─────────────────────────────────────────────────────────
    seg_label = f"Segment {seg_no} — {seg_title}"
    seg_data  = [[Paragraph(
        f"<font color='white'><b>{seg_label}</b></font>",
        _style("segR", fontSize=10, leading=14, textColor=WHITE,
               fontName="Helvetica-Bold", alignment=TA_CENTER)
    )]]
    seg_tbl = Table(seg_data, colWidths=[W])
    seg_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), MID_BLUE),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    story.append(seg_tbl)
    story.append(Spacer(1, 4*mm))

    # ── Invoice meta + seller/buyer ───────────────────────────────────────────
    receipt_num = f"{INVOICE_NUMBER}-S{seg_no:02d}"
    meta_left = (
        f"<b>Receipt No:</b>  {receipt_num}<br/>"
        f"<b>Invoice Ref:</b> {INVOICE_NUMBER}<br/>"
        f"<b>Date:</b>        {INVOICE_DATE}<br/>"
        f"<b>Order Date:</b>  {ORDER_DATE}<br/>"
        f"<b>Valid Until:</b> {VALID_UNTIL}"
    )

    seller_text = (
        f"<b>SELLER</b><br/>"
        f"{SELLER['name']}<br/>"
        + SELLER['address'].replace('\n', '<br/>') +
        f"<br/>Phone: {SELLER['phone']}"
    )
    buyer_text = (
        f"<b>BUYER</b><br/>"
        + BUYER['name'].replace('\n', '<br/>') + "<br/>"
        + BUYER['address'].replace('\n', '<br/>') +
        f"<br/>Phone: {BUYER['phone']}<br/>Email: {BUYER['email']}"
    )

    cell_style = _style("cell", fontSize=8, leading=12)
    info_data = [[
        Paragraph(meta_left,   cell_style),
        Paragraph(seller_text, cell_style),
        Paragraph(buyer_text,  cell_style),
    ]]
    info_tbl = Table(info_data, colWidths=[W*0.30, W*0.35, W*0.35])
    info_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (0,0), LIGHT_BLUE),
        ("BACKGROUND",    (1,0), (1,0), LIGHT_GRAY),
        ("BACKGROUND",    (2,0), (2,0), LIGHT_GRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("BOX",           (0,0), (-1,-1), 0.5, MID_GRAY),
        ("INNERGRID",     (0,0), (-1,-1), 0.5, MID_GRAY),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 5*mm))

    # ── Banking information ────────────────────────────────────────────────────
    bank_text = (
        f"<b>BANKING INFORMATION</b> &nbsp;&nbsp; "
        f"Bank: {BANK['name']} &nbsp;|&nbsp; "
        f"Address: {BANK['address']} &nbsp;|&nbsp; "
        f"IBAN: {BANK['iban']} &nbsp;|&nbsp; "
        f"SWIFT/BIC: {BANK['swift']}"
    )
    bank_data = [[Paragraph(bank_text, _style("bank", fontSize=7, leading=10))]]
    bank_tbl  = Table(bank_data, colWidths=[W])
    bank_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), LIGHT_BLUE),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("BOX",           (0,0), (-1,-1), 0.5, MID_GRAY),
    ]))
    story.append(bank_tbl)
    story.append(Spacer(1, 5*mm))

    # ── Items table ────────────────────────────────────────────────────────────
    col_hdr_style = _style("chdr", fontSize=8, leading=11, textColor=WHITE,
                           fontName="Helvetica-Bold", alignment=TA_CENTER)
    cell_c  = _style("cc",  fontSize=8, leading=11, alignment=TA_CENTER)
    cell_l  = _style("cl",  fontSize=8, leading=11, alignment=TA_LEFT)
    cell_r  = _style("cr",  fontSize=8, leading=11, alignment=TA_RIGHT)

    table_data = [[
        Paragraph("No.",         col_hdr_style),
        Paragraph("Part Number", col_hdr_style),
        Paragraph("Description", col_hdr_style),
        Paragraph("Qty",         col_hdr_style),
        Paragraph("Unit Price",  col_hdr_style),
        Paragraph("Tax",         col_hdr_style),
        Paragraph("Total (EUR)", col_hdr_style),
    ]]

    subtotal = 0.0
    for idx, (item_no, part_no, desc, qty, unit_price) in enumerate(items):
        total = qty * unit_price
        subtotal += total
        bg = WHITE if idx % 2 == 0 else LIGHT_GRAY
        table_data.append([
            Paragraph(str(item_no),            cell_c),
            Paragraph(part_no,                 cell_c),
            Paragraph(desc,                    cell_l),
            Paragraph(str(qty),                cell_c),
            Paragraph(f"€ {unit_price:,.2f}",  cell_r),
            Paragraph("€ 0.00",                cell_r),
            Paragraph(f"€ {total:,.2f}",       cell_r),
        ])

    col_w = [W*0.04, W*0.13, W*0.38, W*0.05, W*0.12, W*0.10, W*0.12]   # must sum to W (≈ 6% slack → title col gets it)
    # Adjust to exactly W
    col_w[2] += W - sum(col_w)

    items_tbl = Table(table_data, colWidths=col_w, repeatRows=1)
    row_bg_cmds = [
        ("BACKGROUND", (0, i+1), (-1, i+1), WHITE if i % 2 == 0 else LIGHT_GRAY)
        for i in range(len(items))
    ]
    items_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), DARK_BLUE),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("BOX",           (0,0), (-1,-1), 0.5, MID_GRAY),
        ("INNERGRID",     (0,0), (-1,-1), 0.3, MID_GRAY),
        *row_bg_cmds,
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 3*mm))

    # ── Subtotal / Total row ────────────────────────────────────────────────────
    tot_style = _style("tot", fontSize=9, leading=13, fontName="Helvetica-Bold", alignment=TA_RIGHT)
    val_style = _style("val", fontSize=9, leading=13, fontName="Helvetica-Bold", textColor=WHITE, alignment=TA_RIGHT)

    tot_data = [
        [Paragraph("SEGMENT SUBTOTAL:", tot_style),
         Paragraph(f"€ {subtotal:,.2f}",  val_style)],
        [Paragraph("TOTAL (FOB):",        tot_style),
         Paragraph(f"€ {subtotal:,.2f}",  val_style)],
    ]
    tot_tbl = Table(tot_data, colWidths=[W*0.75, W*0.25])
    tot_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (1,0), (1,-1), DARK_BLUE),
        ("BACKGROUND",    (0,0), (0,-1), LIGHT_GRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("BOX",           (0,0), (-1,-1), 0.5, MID_GRAY),
        ("LINEBELOW",     (0,0), (-1,0), 0.3, MID_GRAY),
    ]))
    story.append(tot_tbl)
    story.append(Spacer(1, 6*mm))

    # ── Shipping information ────────────────────────────────────────────────────
    ship_hdr = [[Paragraph(
        "<font color='white'><b>SHIPPING INFORMATION</b></font>",
        _style("shH", fontSize=8, leading=12, textColor=WHITE, fontName="Helvetica-Bold")
    )]]
    ship_hdr_tbl = Table(ship_hdr, colWidths=[W])
    ship_hdr_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), MID_BLUE),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
    ]))
    story.append(ship_hdr_tbl)

    ship_rows = []
    for label, value in SHIPPING:
        ship_rows.append([
            Paragraph(f"<b>{label}:</b>", _style("sl", fontSize=8, leading=11)),
            Paragraph(value,               _style("sv", fontSize=8, leading=11)),
        ])
    ship_tbl = Table(ship_rows, colWidths=[W*0.30, W*0.70])
    ship_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), LIGHT_GRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("INNERGRID",     (0,0), (-1,-1), 0.3, MID_GRAY),
        ("BOX",           (0,0), (-1,-1), 0.5, MID_GRAY),
    ]))
    story.append(ship_tbl)
    story.append(Spacer(1, 5*mm))

    # ── Terms & conditions ──────────────────────────────────────────────────────
    tc_hdr = [[Paragraph(
        "<font color='white'><b>TERMS AND CONDITIONS</b></font>",
        _style("tcH", fontSize=8, leading=12, textColor=WHITE, fontName="Helvetica-Bold")
    )]]
    tc_hdr_tbl = Table(tc_hdr, colWidths=[W])
    tc_hdr_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), MID_BLUE),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
    ]))
    story.append(tc_hdr_tbl)

    tc_rows = [[Paragraph(f"● {t}", _style("tc", fontSize=7, leading=10))] for t in TERMS]
    tc_tbl  = Table(tc_rows, colWidths=[W])
    tc_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), LIGHT_GRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("BOX",           (0,0), (-1,-1), 0.5, MID_GRAY),
    ]))
    story.append(tc_tbl)
    story.append(Spacer(1, 5*mm))

    # ── Footer ──────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width=W, thickness=1, color=ACCENT))
    story.append(Spacer(1, 2*mm))
    footer_text = (
        f"For enquiries contact: "
        f"<b>{BUYER['email']}</b>  |  <b>{BUYER['phone']}</b>"
    )
    story.append(Paragraph(footer_text, _style("ft", fontSize=7.5, leading=10, alignment=TA_CENTER)))

    doc.build(story)
    print(f"  ✓  Segment {seg_no:02d} — {seg_title:<40}  €{subtotal:>10,.2f}  →  {os.path.basename(out_path)}")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    OUT_DIR = os.path.join(os.path.dirname(__file__), "segment_receipts")
    os.makedirs(OUT_DIR, exist_ok=True)

    print("\nGenerating 9 segment sales receipts …\n")
    grand_total = 0.0
    for seg_no, seg_title, items in SEGMENTS:
        subtotal = sum(qty * up for _, _, _, qty, up in items)
        grand_total += subtotal
        fname    = f"Receipt_Seg{seg_no:02d}_{seg_title.replace(' ', '_').replace('&', 'and').replace('/', '-')}.pdf"
        out_path = os.path.join(OUT_DIR, fname)
        build_receipt(seg_no, seg_title, items, out_path)

    print(f"\n{'─'*65}")
    print(f"  Grand Total (all 9 segments):  € {grand_total:>10,.2f}")
    print(f"  Output folder:  {OUT_DIR}")
    print(f"{'─'*65}\n")
