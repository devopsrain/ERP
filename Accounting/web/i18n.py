"""
Lightweight i18n for EBMS — English (default) + Amharic (አማርኛ).

Design goals
------------
* Zero dependencies (no gettext/babel toolchain to run).
* Safe by construction: ``_("text")`` returns ``text`` unchanged when no
  translation exists, so wrapping a string can never break a page.
* Works with Jinja templates that are rendered both by the real app
  (``template_engine.templates``) and by the stub ``jinja2.Environment``
  used in ``web/tests/test_*_templates.py`` — ``install()`` registers the
  helpers into :mod:`jinja2.defaults` so *every* Environment gets them.

Locale resolution (``get_locale``): ``?lang=`` query → session ``lang`` →
cookie ``ebms_lang`` → ``Accept-Language`` header → ``en``.

Template helpers
----------------
    {{ _("Dashboard") }}                → translated string
    {{ _("Hello %(name)s", name=u) }}   → interpolated
    {{ some_date|et_date }}             → Ethiopian calendar date (locale-aware)
    {{ some_date|dual_date }}           → "12 Sep 2026 · መስከረም 2፣ 2019"
    {{ current_lang }}                  → "en" | "am"
    {{ eth_today }}                     → EthDate for today
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ethiopian_calendar import (
    EthDate, format_dual, format_ethiopian, to_ethiopian, today_ethiopian,
)

logger = logging.getLogger(__name__)

DEFAULT_LANG = "en"
LANGS: Dict[str, Dict[str, str]] = {
    "en": {"code": "en", "name": "English", "native": "English", "dir": "ltr"},
    "am": {"code": "am", "name": "Amharic", "native": "አማርኛ", "dir": "ltr"},
}
COOKIE_NAME = "ebms_lang"
SESSION_KEY = "lang"

# ─────────────────────────────────────────────────────────────────
#  Catalogue — key is the English source string.
#  Keep alphabetical-ish groups; add freely, missing keys fall back to English.
# ─────────────────────────────────────────────────────────────────
AM: Dict[str, str] = {
    # ── navigation / modules ──
    "Main": "ዋና",
    "Dashboard": "ዳሽቦርድ",
    "Management Overview": "የአመራር አጠቃላይ እይታ",
    "Company Portal": "የኩባንያ ፖርታል",
    "Accounting": "ሒሳብ",
    "Accounting & Finance": "ሒሳብ እና ፋይናንስ",
    "Chart of Accounts": "የሒሳብ ሰንጠረዥ",
    "Journal Entries": "የመዝገብ ግቤቶች",
    "Income & Expense": "ገቢ እና ወጪ",
    "Financial Statements": "የፋይናንስ መግለጫዎች",
    "All Transactions": "ሁሉም ግብይቶች",
    "Flagged Items": "ምልክት የተደረገባቸው",
    "Flagged Accounts": "ምልክት የተደረገባቸው ሒሳቦች",
    "Import Excel": "ኤክሴል አስገባ",
    "Export to Excel": "ወደ ኤክሴል ላክ",
    "VAT & Tax": "ተ.እ.ታ እና ግብር",
    "VAT Portal": "የተ.እ.ታ ፖርታል",
    "VAT Dashboard": "የተ.እ.ታ ዳሽቦርድ",
    "Data Entry": "መረጃ ማስገቢያ",
    "Add Income": "ገቢ ጨምር",
    "Add Expense": "ወጪ ጨምር",
    "Add Capital": "ካፒታል ጨምር",
    "Reports & Lists": "ሪፖርቶች እና ዝርዝሮች",
    "Income List": "የገቢ ዝርዝር",
    "Expense List": "የወጪ ዝርዝር",
    "Capital List": "የካፒታል ዝርዝር",
    "Financial Summary": "የፋይናንስ ማጠቃለያ",
    "Operations": "ኦፕሬሽን",
    "Operations & Assets": "ኦፕሬሽን እና ንብረቶች",
    "Inventory Management": "የዕቃ ክምችት አስተዳደር",
    "Payroll System": "የደመወዝ ሥርዓት",
    "Payroll Forecast": "የደመወዝ ትንበያ",
    "Employee Self-Service": "የሠራተኛ ራስ አገልግሎት",
    "HR Analytics": "የሰው ኃይል ትንተና",
    "Finance Forecast": "የፋይናንስ ትንበያ",
    "Learning (LMS)": "ትምህርት (LMS)",
    "Machinery": "ማሽነሪ",
    "CPO": "ሲፒኦ",
    "Bid Tracker": "የጨረታ መከታተያ",
    "Letters & E-Sign": "ደብዳቤዎች እና ኢ-ፊርማ",
    "Contracts": "ውሎች",
    "Fixed Assets": "ቋሚ ንብረቶች",
    "Fixed Assets & Depreciation": "ቋሚ ንብረቶች እና እርጅና ቅናሽ",
    "Mobile Money": "የሞባይል ገንዘብ",
    "Mobile Money Payments": "የሞባይል ገንዘብ ክፍያዎች",
    "ERCA Tax Forms": "የገቢዎች ግብር ቅጾች",
    "Tax Forms & E-Invoices": "የግብር ቅጾች እና ኢ-ደረሰኞች",
    "Approvals": "ማጽደቆች",
    "Approval Inbox": "የማጽደቅ ገቢ መልእክት",
    "Approval Workflows": "የማጽደቅ የሥራ ፍሰቶች",
    "Report Builder": "ሪፖርት ገንቢ",
    "Scheduled Reports": "የተመደቡ ሪፖርቶች",
    "Documents": "ሰነዶች",
    "Documents (Nextcloud)": "ሰነዶች (Nextcloud)",
    "Collaboration": "ትብብር",
    "Communication": "ግንኙነት",
    "Project Management": "የፕሮጀክት አስተዳደር",
    "Procurement": "ግዥ",
    "Procurement Plans": "የግዥ ዕቅዶች",
    "Event Management": "የዝግጅት አስተዳደር",
    "Event Reports": "የዝግጅት ሪፖርቶች",
    "Event Clients": "የዝግጅት ደንበኞች",
    "Admin": "አስተዳደር",
    "Administration": "አስተዳደር",
    "Multi-Company Settings": "የብዙ ኩባንያ ቅንብሮች",
    "Stakeholders": "ባለድርሻ አካላት",
    "SIEM / Security": "SIEM / ደህንነት",
    "Integrations": "ውህደቶች",
    "Webhooks & API Keys": "ዌብሁኮች እና የAPI ቁልፎች",
    "Telegram Bot": "ቴሌግራም ቦት",
    "Customer & Supplier Portal": "የደንበኛ እና አቅራቢ ፖርታል",
    "Portal Users": "የፖርታል ተጠቃሚዎች",
    "API Documentation": "የAPI ሰነድ",
    "My Account": "የእኔ መለያ",
    "Change Password": "የይለፍ ቃል ቀይር",
    "Logout": "ውጣ",
    "Login": "ግባ",
    "Register": "ተመዝገብ",
    "Language": "ቋንቋ",
    "Dark": "ጨለማ",
    "Light": "ብርሃን",
    "Toggle theme": "ገጽታ ቀይር",
    "Notifications": "ማሳወቂያዎች",
    "Mark all read": "ሁሉንም እንደተነበበ ምልክት አድርግ",
    "Recently Viewed": "በቅርብ የታዩ",
    "No recent items": "የቅርብ ጊዜ ንጥሎች የሉም",
    "Search": "ፈልግ",
    "Ethiopian Business Suite": "የኢትዮጵያ ንግድ ስብስብ",
    "Ethiopian Accounting Software": "የኢትዮጵያ የሒሳብ ሶፍትዌር",
    # ── sales landing page (v2.2 section) ──
    "Version 2.2 — Amharic UI · Ethiopian Calendar · Mobile Money · ERCA Forms":
        "ስሪት 2.2 — አማርኛ · የኢትዮጵያ አቆጣጠር · የሞባይል ገንዘብ · የገቢዎች ቅጾች",
    "New in Version 2.2": "በስሪት 2.2 አዲስ",
    "Version 2.3 — Manufacturing ERP · Amharic UI · Ethiopian Calendar · Mobile Money":
        "ስሪት 2.3 — የማኑፋክቸሪንግ ERP · አማርኛ · የኢትዮጵያ አቆጣጠር · የሞባይል ገንዘብ",
    "New in Version 2.3": "በስሪት 2.3 አዲስ",
    "Manufacturing ERP for Ethiopian Factories": "ለኢትዮጵያ ፋብሪካዎች የማኑፋክቸሪንግ ERP",
    "From customer purchase order to dispatch: production planning, shop-floor control, quality management and sales in one system, mapped to cable-manufacturing tender requirements.":
        "ከደንበኛ የግዥ ትዕዛዝ እስከ ማድረስ፦ የምርት ዕቅድ፣ የፋብሪካ ወለል ቁጥጥር፣ የጥራት አስተዳደር እና ሽያጭ በአንድ ሥርዓት፣ ከኬብል ማምረቻ ጨረታ መስፈርቶች ጋር የተጣጣመ።",
    "Built for Ethiopia. Open to Everyone.": "ለኢትዮጵያ የተሠራ። ለሁሉም ክፍት።",
    "Ten new capabilities: Amharic and the Ethiopian calendar everywhere, mobile-money reconciliation, ERCA-ready tax forms, portals, integrations and deeper workflow control.":
        "አሥር አዳዲስ አቅሞች፦ በሁሉም ቦታ አማርኛ እና የኢትዮጵያ አቆጣጠር፣ የሞባይል ገንዘብ ማስታረቅ፣ ለገቢዎች ዝግጁ የግብር ቅጾች፣ ፖርታሎች፣ ውህደቶች እና የጠለቀ የሥራ ፍሰት ቁጥጥር።",

    # ── manufacturing ERP navigation ──
    "Manufacturing": "ማኑፋክቸሪንግ",
    "Production": "ምርት",
    "Manufacturing Dashboard": "የማኑፋክቸሪንግ ዳሽቦርድ",
    "Process Map": "የሂደት ካርታ",
    "Production Orders": "የምርት ትዕዛዞች",
    "Production Reports": "የምርት ሪፖርቶች",
    "Quality Management": "የጥራት አስተዳደር",
    "Quality Dashboard": "የጥራት ዳሽቦርድ",
    "Quality Reports": "የጥራት ሪፖርቶች",
    "Sales & Marketing": "ሽያጭ እና ግብይት",
    "Commercial Dashboard": "የንግድ ዳሽቦርድ",
    "Sales Orders": "የሽያጭ ትዕዛዞች",
    "Sales Reports": "የሽያጭ ሪፖርቶች",

    # ── bid results & confidential supplier docs / contract files ──
    "Bid Results": "የጨረታ ውጤቶች",
    "Our bid": "የእኛ ጨረታ",
    "Position": "ደረጃ",
    "Lowest": "ዝቅተኛ",
    "Highest": "ከፍተኛ",
    "Bidder": "ተጫራች",
    "Tech.": "ቴክ.",
    "Tech. score": "የቴክኒክ ነጥብ",
    "Us": "እኛ",
    "Winner": "አሸናፊ",
    "Mark as winner": "አሸናፊ አድርግ",
    "No results recorded yet. Add each bidder and the price read out at the bid opening.":
        "እስካሁን ውጤት አልተመዘገበም። በጨረታ መክፈቻ የተነበበውን የእያንዳንዱን ተጫራች ስም እና ዋጋ ያስገቡ።",
    "Bidder / company name": "የተጫራች / የኩባንያ ስም",
    "Notes (optional)": "ማስታወሻ (አማራጭ)",
    "Add Result": "ውጤት ጨምር",
    "Supplier Confidential (admin only)": "የአቅራቢ ሚስጥራዊ (ለአስተዳዳሪ ብቻ)",
    "Supplier Confidential Documents": "የአቅራቢ ሚስጥራዊ ሰነዶች",
    "Admin only": "ለአስተዳዳሪ ብቻ",
    "supplier price information": "የአቅራቢ የዋጋ መረጃ",
    "Contract Documents": "የውል ሰነዶች",
    "No contract file uploaded yet. Upload the signed contract so it can be viewed whenever needed.":
        "እስካሁን የውል ፋይል አልተሰቀለም። በሚያስፈልግ ጊዜ እንዲታይ የተፈረመውን ውል ይስቀሉ።",
    "Document kind": "የሰነድ ዓይነት",
    "Signed contract": "የተፈረመ ውል",
    "Signed contract file": "የተፈረመ የውል ፋይል",
    "Annex / schedule": "አባሪ / ሰንጠረዥ",
    "Amendment": "ማሻሻያ",
    "Correspondence": "የደብዳቤ ልውውጥ",
    "File": "ፋይል",
    "Upload Contract File": "የውል ፋይል ስቀል",
    "You can also attach or replace files later from the contract page.":
        "ፋይሎችን በኋላ ከውሉ ገጽ ማያያዝ ወይም መተካት ይችላሉ።",
    "optional": "አማራጭ",
    "by": "በ",

    # ── common actions ──
    "Save": "አስቀምጥ",
    "Cancel": "ሰርዝ",
    "Edit": "አርትዕ",
    "Delete": "ሰርዝ",
    "Add": "ጨምር",
    "Add New": "አዲስ ጨምር",
    "Create": "ፍጠር",
    "Update": "አዘምን",
    "Back": "ተመለስ",
    "Next": "ቀጣይ",
    "Previous": "ቀዳሚ",
    "Submit": "አስገባ",
    "Approve": "አጽድቅ",
    "Reject": "ውድቅ አድርግ",
    "Export": "ላክ",
    "Import": "አስገባ",
    "Download": "አውርድ",
    "Upload": "ስቀል",
    "Print": "አትም",
    "Filter": "አጣራ",
    "Reset": "ዳግም አስጀምር",
    "Apply": "ተግብር",
    "Close": "ዝጋ",
    "View": "እይ",
    "Details": "ዝርዝሮች",
    "Actions": "ተግባራት",
    "Confirm": "አረጋግጥ",
    "Yes": "አዎ",
    "No": "አይ",
    "Select": "ምረጥ",
    "All": "ሁሉም",
    "None": "ምንም",
    "Refresh": "አድስ",
    "Send": "ላክ",
    "Preview": "ቅድመ እይታ",
    "Generate": "አመንጭ",
    "Run": "አሂድ",
    "Settings": "ቅንብሮች",
    "Help": "እገዛ",

    # ── common nouns / table headers ──
    "Date": "ቀን",
    "Ethiopian Date": "የኢትዮጵያ ቀን",
    "Gregorian Date": "የግሪጎሪያን ቀን",
    "Today": "ዛሬ",
    "Name": "ስም",
    "Full Name": "ሙሉ ስም",
    "Description": "መግለጫ",
    "Amount": "መጠን",
    "Total": "ጠቅላላ",
    "Subtotal": "ንዑስ ድምር",
    "Balance": "ቀሪ ሂሳብ",
    "Status": "ሁኔታ",
    "Type": "ዓይነት",
    "Category": "ምድብ",
    "Reference": "ማጣቀሻ",
    "Notes": "ማስታወሻዎች",
    "Currency": "ምንዛሬ",
    "Quantity": "ብዛት",
    "Unit Price": "የአንድ ዋጋ",
    "Price": "ዋጋ",
    "Income": "ገቢ",
    "Expense": "ወጪ",
    "Expenses": "ወጪዎች",
    "Revenue": "ገቢ",
    "Profit": "ትርፍ",
    "Loss": "ኪሳራ",
    "Net": "ተጣራ",
    "Gross": "ጠቅላላ",
    "Gross Amount": "ጠቅላላ መጠን",
    "Net Amount": "የተጣራ መጠን",
    "VAT": "ተ.እ.ታ",
    "VAT Amount": "የተ.እ.ታ መጠን",
    "VAT Rate": "የተ.እ.ታ መጣኔ",
    "Withholding": "ተቀናሽ ግብር",
    "Withholding Tax": "ተቀናሽ ግብር",
    "Tax": "ግብር",
    "TIN": "የግብር ከፋይ መለያ ቁጥር",
    "Customer": "ደንበኛ",
    "Customer Name": "የደንበኛ ስም",
    "Supplier": "አቅራቢ",
    "Supplier Name": "የአቅራቢ ስም",
    "Vendor": "አቅራቢ",
    "Employee": "ሠራተኛ",
    "Employees": "ሠራተኞች",
    "Department": "ክፍል",
    "Position": "የሥራ መደብ",
    "Salary": "ደመወዝ",
    "Basic Salary": "መሠረታዊ ደመወዝ",
    "Net Pay": "የተጣራ ክፍያ",
    "Pension": "ጡረታ",
    "Income Tax": "የገቢ ግብር",
    "Company": "ኩባንያ",
    "Company Name": "የኩባንያ ስም",
    "Project": "ፕሮጀክት",
    "Projects": "ፕሮጀክቶች",
    "Contract": "ውል",
    "Tender": "ጨረታ",
    "Tender ID": "የጨረታ መለያ",
    "Bid": "ጨረታ",
    "Invoice": "ደረሰኝ",
    "Invoices": "ደረሰኞች",
    "Invoice Number": "የደረሰኝ ቁጥር",
    "Receipt": "ደረሰኝ",
    "Payment": "ክፍያ",
    "Payments": "ክፍያዎች",
    "Payment Mode": "የክፍያ ዘዴ",
    "Payment Date": "የክፍያ ቀን",
    "Paid": "ተከፍሏል",
    "Unpaid": "አልተከፈለም",
    "Pending": "በመጠባበቅ ላይ",
    "Approved": "ጸድቋል",
    "Rejected": "ውድቅ ተደርጓል",
    "Completed": "ተጠናቋል",
    "Active": "ንቁ",
    "Inactive": "ንቁ ያልሆነ",
    "Draft": "ረቂቅ",
    "Open": "ክፍት",
    "Closed": "ዝግ",
    "Cancelled": "ተሰርዟል",
    "Failed": "አልተሳካም",
    "Success": "ተሳክቷል",
    "Error": "ስህተት",
    "Warning": "ማስጠንቀቂያ",
    "Information": "መረጃ",
    "Phone": "ስልክ",
    "Email": "ኢሜይል",
    "Address": "አድራሻ",
    "City": "ከተማ",
    "Region": "ክልል",
    "Username": "የተጠቃሚ ስም",
    "Password": "የይለፍ ቃል",
    "Confirm Password": "የይለፍ ቃል አረጋግጥ",
    "Remember me": "አስታውሰኝ",
    "Forgot password?": "የይለፍ ቃል ረሱ?",
    "Role": "ሚና",
    "User": "ተጠቃሚ",
    "Users": "ተጠቃሚዎች",
    "Created": "ተፈጥሯል",
    "Created At": "የተፈጠረበት ቀን",
    "Updated": "ተዘምኗል",
    "Start Date": "የመጀመሪያ ቀን",
    "End Date": "የመጨረሻ ቀን",
    "From": "ከ",
    "To": "እስከ",
    "Period": "ወቅት",
    "Month": "ወር",
    "Year": "ዓመት",
    "Fiscal Year": "የበጀት ዓመት",
    "This Month": "በዚህ ወር",
    "This Year": "በዚህ ዓመት",
    "Last 30 Days": "ያለፉት 30 ቀናት",
    "Summary": "ማጠቃለያ",
    "Report": "ሪፖርት",
    "Reports": "ሪፖርቶች",
    "Overview": "አጠቃላይ እይታ",
    "Statistics": "ስታቲስቲክስ",
    "Recent Activity": "የቅርብ ጊዜ እንቅስቃሴ",
    "Quick Actions": "ፈጣን ተግባራት",
    "Welcome": "እንኳን ደህና መጡ",
    "Welcome back": "እንኳን በደህና ተመለሱ",
    "Items": "ንጥሎች",
    "Item": "ንጥል",
    "Stock": "ክምችት",
    "Low Stock": "አነስተኛ ክምችት",
    "Inventory": "የዕቃ ክምችት",
    "Asset": "ንብረት",
    "Assets": "ንብረቶች",
    "Depreciation": "የእርጅና ቅናሽ",
    "Book Value": "የመዝገብ ዋጋ",
    "Cost": "ወጪ",
    "Budget": "በጀት",
    "Bank": "ባንክ",
    "Cash": "ጥሬ ገንዘብ",
    "Telebirr": "ቴሌብር",
    "CBE Birr": "ሲቢኢ ብር",
    "M-Pesa": "ኤም-ፔሳ",
    "Birr": "ብር",
    "ETB": "ብር",
    "Loading…": "በመጫን ላይ…",
    "No data available": "ምንም መረጃ የለም",
    "No records found": "ምንም መዝገብ አልተገኘም",
    "Showing": "የሚታየው",
    "of": "ከ",
    "records": "መዝገቦች",
    "Required": "ግዴታ",
    "Optional": "አማራጭ",
    "Are you sure?": "እርግጠኛ ነዎት?",
    "This action cannot be undone.": "ይህ ተግባር ሊቀለበስ አይችልም።",
    "Saved successfully": "በተሳካ ሁኔታ ተቀምጧል",
    "Deleted successfully": "በተሳካ ሁኔታ ተሰርዟል",
    "Invalid credentials": "የተሳሳተ መግቢያ",
    "Access denied": "መዳረሻ ተከልክሏል",
    "Page not found": "ገጽ አልተገኘም",
    "Search vendors, projects, POs, bookings...  (Ctrl+K for commands)":
        "አቅራቢዎችን፣ ፕሮጀክቶችን፣ የግዥ ትዕዛዞችን፣ ቦታ ማስያዣዎችን ፈልግ…  (ትዕዛዞች Ctrl+K)",
}

CATALOGUE: Dict[str, Dict[str, str]] = {"en": {}, "am": AM}


def _merge_extra_catalogues() -> None:
    """Merge every ``web/i18n_catalogue_*.py`` module exposing ``AM: dict``
    (one file per functional area keeps merges conflict-free)."""
    import glob
    import importlib
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    for path in sorted(glob.glob(os.path.join(here, "i18n_catalogue_*.py"))):
        mod_name = os.path.splitext(os.path.basename(path))[0]
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # pragma: no cover
            logger.warning("i18n: could not load %s: %s", mod_name, exc)
            continue
        for lang_code, table in (("am", getattr(mod, "AM", None)),):
            if isinstance(table, dict):
                # Explicit entries in this file win over area catalogues.
                for k, v in table.items():
                    CATALOGUE[lang_code].setdefault(k, v)


_merge_extra_catalogues()


# ─────────────────────────────────────────────────────────────────
#  Locale resolution
# ─────────────────────────────────────────────────────────────────

def normalize_lang(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    c = str(code).strip().lower().replace("_", "-").split("-")[0]
    return c if c in LANGS else None


def get_locale(request: Any) -> str:
    """Best-effort locale for a Starlette request (or any duck-typed object)."""
    if request is None:
        return DEFAULT_LANG
    try:
        qp = getattr(request, "query_params", None)
        if qp is not None:
            lang = normalize_lang(qp.get("lang") if hasattr(qp, "get") else None)
            if lang:
                return lang
    except Exception:
        pass
    try:
        sess = getattr(request, "session", None)
        if sess:
            lang = normalize_lang(sess.get(SESSION_KEY))
            if lang:
                return lang
    except Exception:
        pass
    try:
        cookies = getattr(request, "cookies", None)
        if cookies:
            lang = normalize_lang(cookies.get(COOKIE_NAME))
            if lang:
                return lang
    except Exception:
        pass
    try:
        headers = getattr(request, "headers", None)
        if headers:
            accept = headers.get("accept-language", "") or ""
            for part in accept.split(","):
                lang = normalize_lang(part.split(";")[0])
                if lang:
                    return lang
    except Exception:
        pass
    return DEFAULT_LANG


def set_locale(request: Any, lang: str) -> str:
    lang = normalize_lang(lang) or DEFAULT_LANG
    try:
        request.session[SESSION_KEY] = lang
    except Exception:
        pass
    return lang


# ─────────────────────────────────────────────────────────────────
#  Translation
# ─────────────────────────────────────────────────────────────────

def translate(text: Any, lang: str = DEFAULT_LANG, **kwargs: Any) -> str:
    if text is None:
        return ""
    key = str(text)
    out = CATALOGUE.get(lang, {}).get(key, key) if lang != "en" else key
    if kwargs:
        try:
            out = out % kwargs
        except Exception:
            try:
                out = out.format(**kwargs)
            except Exception:
                pass
    return out


def gettext_for(lang: str):
    """Return a plain ``_()`` bound to a fixed language (for jobs/emails)."""
    def _(text: Any, **kw: Any) -> str:
        return translate(text, lang, **kw)
    return _


# ─────────────────────────────────────────────────────────────────
#  Jinja integration
# ─────────────────────────────────────────────────────────────────

def _ctx_lang(context: Any) -> str:
    try:
        lang = context.get("current_lang") if hasattr(context, "get") else None
        if lang in LANGS:
            return lang
        return get_locale(context.get("request") if hasattr(context, "get") else None)
    except Exception:
        return DEFAULT_LANG


def _jinja_helpers() -> tuple[Dict[str, Any], Dict[str, Any]]:
    import jinja2

    @jinja2.pass_context
    def _gettext(context, text, **kw):
        return translate(text, _ctx_lang(context), **kw)

    @jinja2.pass_context
    def _current_lang(context):
        return _ctx_lang(context)

    @jinja2.pass_context
    def _et_date(context, value, with_weekday=False, lang=None):
        return format_ethiopian(value, lang or _ctx_lang(context), with_weekday=with_weekday)

    @jinja2.pass_context
    def _dual_date(context, value, lang=None):
        return format_dual(value, lang or _ctx_lang(context))

    def _eth(value):
        return to_ethiopian(value)

    globals_ = {
        "_": _gettext,
        "t": _gettext,
        "gettext": _gettext,
        "get_lang": _current_lang,
        "eth_today": today_ethiopian,
        "to_ethiopian": _eth,
        "LANGS": LANGS,
        "I18N_LANGS": list(LANGS.values()),
    }
    filters_ = {
        "t": _gettext,
        "et_date": _et_date,
        "dual_date": _dual_date,
        "to_ethiopian": _eth,
    }
    return globals_, filters_


_INSTALLED = False


def install(env: Any = None) -> None:
    """Register helpers on ``env`` and on jinja2's *default* namespace so that
    every Environment created afterwards (including the stub ones in tests)
    also has them. Idempotent."""
    global _INSTALLED
    import jinja2.defaults as _defaults

    g, f = _jinja_helpers()
    if not _INSTALLED:
        _defaults.DEFAULT_NAMESPACE.update(g)
        _defaults.DEFAULT_FILTERS.update(f)
        _INSTALLED = True
    if env is not None:
        env.globals.update(g)
        env.filters.update(f)


def ensure_schema() -> None:
    """No database objects — present so the central startup loop is uniform."""
    install()


# convenience for Python code paths that hold a request
def _(text: Any, request: Any = None, **kw: Any) -> str:
    return translate(text, get_locale(request), **kw)


__all__ = [
    "LANGS", "DEFAULT_LANG", "COOKIE_NAME", "SESSION_KEY", "AM", "CATALOGUE",
    "get_locale", "set_locale", "normalize_lang", "translate", "gettext_for",
    "install", "ensure_schema", "EthDate",
]
