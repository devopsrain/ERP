"""
Fixed Assets & Depreciation Data Store — PostgreSQL backend.

Tables: fixed_asset_categories, fixed_assets, fixed_asset_depreciation,
        fixed_asset_disposals, fixed_asset_maintenance

The depreciation math lives in pure, DB-free functions at the top of this
module (``schedule`` and helpers) so it can be unit-tested without Postgres.
"""
from __future__ import annotations

import calendar
import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

from db import get_conn

logger = logging.getLogger(__name__)

METHODS = ("straight_line", "declining_balance", "sum_of_years", "units_of_production")
STATUSES = ("active", "disposed", "fully_depreciated", "written_off")
DISPOSAL_METHODS = ("sale", "scrap", "donation", "write_off")
METHOD_LABELS = {
    "straight_line": "Straight line",
    "declining_balance": "Declining balance",
    "sum_of_years": "Sum of years' digits",
    "units_of_production": "Units of production",
}
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Ethiopian Income Tax Proclamation (979/2016) depreciation classes, used as
# lazy seed defaults for a company that has no categories yet.
DEFAULT_CATEGORIES = [
    {"code": "BLDG", "name": "Buildings and structures", "default_method": "straight_line",
     "default_useful_life_months": 240, "default_salvage_pct": 0,
     "gl_asset_account": "1500", "gl_depreciation_expense_account": "6500",
     "gl_accumulated_depreciation_account": "1590"},
    {"code": "INTG", "name": "Intangible assets", "default_method": "straight_line",
     "default_useful_life_months": 120, "default_salvage_pct": 0,
     "gl_asset_account": "1700", "gl_depreciation_expense_account": "6510",
     "gl_accumulated_depreciation_account": "1790"},
    {"code": "COMP", "name": "Computers, software and data storage",
     "default_method": "declining_balance", "default_useful_life_months": 48,
     "default_salvage_pct": 0, "default_declining_rate": 25,
     "gl_asset_account": "1520", "gl_depreciation_expense_account": "6520",
     "gl_accumulated_depreciation_account": "1592"},
    {"code": "OTHR", "name": "Other business assets", "default_method": "declining_balance",
     "default_useful_life_months": 60, "default_salvage_pct": 0, "default_declining_rate": 20,
     "gl_asset_account": "1530", "gl_depreciation_expense_account": "6530",
     "gl_accumulated_depreciation_account": "1593"},
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fixed_asset_categories (
    id                                  TEXT PRIMARY KEY,
    company_id                          TEXT NOT NULL DEFAULT 'default',
    name                                TEXT NOT NULL,
    code                                TEXT NOT NULL DEFAULT '',
    default_method                      TEXT NOT NULL DEFAULT 'straight_line',
    default_useful_life_months          INTEGER NOT NULL DEFAULT 60,
    default_salvage_pct                 NUMERIC(6,2) NOT NULL DEFAULT 0,
    default_declining_rate              NUMERIC(8,4),
    gl_asset_account                    TEXT NOT NULL DEFAULT '',
    gl_depreciation_expense_account     TEXT NOT NULL DEFAULT '',
    gl_accumulated_depreciation_account TEXT NOT NULL DEFAULT '',
    created_at                          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at                          TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fa_categories_company ON fixed_asset_categories(company_id);

CREATE TABLE IF NOT EXISTS fixed_assets (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL DEFAULT 'default',
    asset_tag           TEXT NOT NULL,
    name                TEXT NOT NULL,
    category_id         TEXT,
    description         TEXT NOT NULL DEFAULT '',
    serial_number       TEXT NOT NULL DEFAULT '',
    location            TEXT NOT NULL DEFAULT '',
    custodian           TEXT NOT NULL DEFAULT '',
    supplier            TEXT NOT NULL DEFAULT '',
    purchase_date       DATE,
    in_service_date     DATE,
    cost                NUMERIC(18,2) NOT NULL DEFAULT 0,
    salvage_value       NUMERIC(18,2) NOT NULL DEFAULT 0,
    useful_life_months  INTEGER NOT NULL DEFAULT 60,
    method              TEXT NOT NULL DEFAULT 'straight_line',
    declining_rate      NUMERIC(8,4),
    total_units         NUMERIC(18,2),
    status              TEXT NOT NULL DEFAULT 'active',
    currency            TEXT NOT NULL DEFAULT 'ETB',
    notes               TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, asset_tag)
);
CREATE INDEX IF NOT EXISTS idx_fixed_assets_company ON fixed_assets(company_id);
CREATE INDEX IF NOT EXISTS idx_fixed_assets_status ON fixed_assets(company_id, status);

CREATE TABLE IF NOT EXISTS fixed_asset_depreciation (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    asset_id    TEXT NOT NULL,
    period      TEXT NOT NULL,
    amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    accumulated NUMERIC(18,2) NOT NULL DEFAULT 0,
    book_value  NUMERIC(18,2) NOT NULL DEFAULT 0,
    units_used  NUMERIC(18,2),
    posted      BOOLEAN NOT NULL DEFAULT FALSE,
    journal_ref TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (asset_id, period)
);
CREATE INDEX IF NOT EXISTS idx_fa_depreciation_period ON fixed_asset_depreciation(company_id, period);

CREATE TABLE IF NOT EXISTS fixed_asset_disposals (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL DEFAULT 'default',
    asset_id               TEXT NOT NULL,
    disposed_on            DATE NOT NULL DEFAULT CURRENT_DATE,
    proceeds               NUMERIC(18,2) NOT NULL DEFAULT 0,
    book_value_at_disposal NUMERIC(18,2) NOT NULL DEFAULT 0,
    gain_loss              NUMERIC(18,2) NOT NULL DEFAULT 0,
    method                 TEXT NOT NULL DEFAULT 'sale',
    notes                  TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fa_disposals_asset ON fixed_asset_disposals(asset_id);

CREATE TABLE IF NOT EXISTS fixed_asset_maintenance (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    asset_id    TEXT NOT NULL,
    date        DATE NOT NULL DEFAULT CURRENT_DATE,
    description TEXT NOT NULL DEFAULT '',
    cost        NUMERIC(18,2) NOT NULL DEFAULT 0,
    vendor      TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fa_maintenance_asset ON fixed_asset_maintenance(asset_id);
"""


# ═══════════════════════════════════════════════════════════════════
#  Pure helpers (no DB) — unit tested in tests/test_depreciation.py
# ═══════════════════════════════════════════════════════════════════

TWO_PLACES = Decimal("0.01")


def q2(value) -> Decimal:
    """Quantize to 2 dp, half-up (accounting rounding)."""
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def D(value, default: Decimal = Decimal(0)) -> Decimal:
    """Coerce form/DB values to Decimal; '' and None become ``default``."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").strip())
    except Exception:
        return default


def parse_date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def period_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def shift_period(period: str, months: int) -> str:
    """'2026-01' shifted by +/- months."""
    y, m = int(period[:4]), int(period[5:7])
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def previous_period(today: Optional[date] = None) -> str:
    """The calendar month before ``today`` — what the monthly job runs for."""
    today = today or date.today()
    return shift_period(period_of(today), -1)


def normalize_rate(rate) -> Optional[Decimal]:
    """Accept 0.25 or 25 (percent) and return a fraction; None if blank."""
    r = D(rate, None)
    if r is None or r <= 0:
        return None
    return r / 100 if r > 1 else r


def month_units(in_service_date: date, life_months: int) -> List[Decimal]:
    """
    Fraction of a month covered by each depreciation period.

    The in-service month is prorated by days remaining; the tail spills into
    one extra period so the total always equals ``life_months``.
    """
    dim = calendar.monthrange(in_service_date.year, in_service_date.month)[1]
    first = Decimal(dim - in_service_date.day + 1) / Decimal(dim)
    units = [first] + [Decimal(1)] * (life_months - 1)
    if first < 1:
        units.append(Decimal(1) - first)
    return units


def _sl_amounts(depreciable: Decimal, life: int, units: List[Decimal]) -> List[Decimal]:
    monthly = depreciable / Decimal(life)
    return [q2(monthly * u) for u in units]


def _db_amounts(cost: Decimal, salvage: Decimal, life: int, units: List[Decimal],
                rate: Optional[Decimal]) -> List[Decimal]:
    """Declining balance, switching to straight-line once SL over the
    remaining life exceeds the DB charge (so the asset reaches salvage)."""
    annual = rate or (Decimal(2) * 12 / Decimal(life))       # default: double declining
    book = cost
    consumed = Decimal(0)
    out = []
    for u in units:
        remaining_units = Decimal(life) - consumed
        floor_room = book - salvage
        if floor_room <= 0 or remaining_units <= 0:
            out.append(Decimal(0))
            continue
        db_amt = book * annual / 12 * u
        sl_amt = floor_room / remaining_units * u
        amt = q2(min(max(db_amt, sl_amt), floor_room))
        out.append(amt)
        book -= amt
        consumed += u
    return out


def _syd_amounts(depreciable: Decimal, life: int, units: List[Decimal]) -> List[Decimal]:
    """Sum-of-years'-digits computed per service year, spread evenly over
    the months of that year (a partial final year gets a fractional digit)."""
    n_years = Decimal(life) / 12
    digits = []
    y = 0
    while Decimal(y) < n_years:
        digits.append(n_years - y)
        y += 1
    syd = sum(digits)
    rates = []   # charge per month-unit within service year y
    for y, dgt in enumerate(digits):
        months_in_year = min(Decimal(12), Decimal(life) - 12 * y)
        rates.append(depreciable * dgt / syd / months_in_year)
    out = []
    pos = Decimal(0)
    for u in units:
        start, end = pos, pos + u
        amt = Decimal(0)
        y = int(start // 12)
        while y < len(rates) and Decimal(12 * y) < end:
            overlap = min(end, Decimal(12 * (y + 1))) - max(start, Decimal(12 * y))
            if overlap > 0:
                amt += rates[y] * overlap
            y += 1
        out.append(q2(amt))
        pos = end
    return out


def _uop_schedule(cost: Decimal, salvage: Decimal, depreciable: Decimal,
                  total_units, units_by_period: Optional[Dict[str, object]]) -> List[dict]:
    total = D(total_units, None)
    if not total or total <= 0 or not units_by_period:
        return []
    rows = []
    acc = Decimal(0)
    used = Decimal(0)
    for period in sorted(units_by_period):
        u = D(units_by_period[period])
        used += u
        remaining = depreciable - acc
        amt = min(q2(depreciable * u / total), remaining)
        if used >= total:
            amt = remaining
        acc += amt
        rows.append({"period": period, "amount": amt, "accumulated": acc,
                     "book_value": cost - acc, "units_used": u})
    return rows


def schedule(cost, salvage, life_months, method, in_service_date,
             declining_rate=None, total_units=None, units_by_period=None) -> List[dict]:
    """
    Full depreciation schedule as a list of
    ``{"period": "YYYY-MM", "amount", "accumulated", "book_value"}`` rows.

    * straight_line        — first month prorated by days in service
    * declining_balance    — annual ``declining_rate`` (fraction or %), default
                             double-declining; switches to SL when SL > DB
    * sum_of_years         — SYD per service year, distributed monthly
    * units_of_production  — ``units_by_period`` {period: units} / ``total_units``

    Book value never drops below salvage; the final period plugs rounding so
    the last book value equals salvage exactly.
    """
    cost = D(cost)
    salvage = max(D(salvage), Decimal(0))
    depreciable = cost - salvage
    if depreciable <= 0:
        return []
    method = (method or "straight_line").strip().lower()
    if method == "units_of_production":
        return _uop_schedule(cost, salvage, depreciable, total_units, units_by_period)

    start = parse_date(in_service_date)
    life = int(D(life_months, Decimal(0)))
    if start is None or life <= 0:
        return []

    units = month_units(start, life)
    if method == "declining_balance":
        raw = _db_amounts(cost, salvage, life, units, normalize_rate(declining_rate))
    elif method in ("sum_of_years", "sum_of_years_digits", "syd"):
        raw = _syd_amounts(depreciable, life, units)
    else:
        raw = _sl_amounts(depreciable, life, units)

    rows = []
    acc = Decimal(0)
    last = len(raw) - 1
    for i, amt in enumerate(raw):
        remaining = depreciable - acc
        amt = remaining if i == last else min(max(amt, Decimal(0)), remaining)
        acc += amt
        rows.append({"period": shift_period(period_of(start), i), "amount": amt,
                     "accumulated": acc, "book_value": cost - acc})
    return rows


def schedule_for_asset(asset: dict, units_by_period=None) -> List[dict]:
    return schedule(asset.get("cost"), asset.get("salvage_value"), asset.get("useful_life_months"),
                    asset.get("method"), asset.get("in_service_date"),
                    declining_rate=asset.get("declining_rate"), total_units=asset.get("total_units"),
                    units_by_period=units_by_period)


def gain_loss(proceeds, book_value) -> Decimal:
    return q2(D(proceeds) - D(book_value))


# ═══════════════════════════════════════════════════════════════════
#  Form coercion
# ═══════════════════════════════════════════════════════════════════

def _opt(value):
    """Empty form fields arrive as '' — Postgres rejects '' for DATE/NUMERIC."""
    return value if value not in ("", None) else None


def _num(value):
    return str(D(value))


def _int(value, default=0):
    try:
        return int(D(value, Decimal(default)))
    except Exception:
        return default


def _str(value):
    return (value or "").strip()


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("fixed_assets schema ready")
    except Exception as e:
        logger.error("fixed_assets schema init failed: %s", e)


_LATEST_DEP = """LEFT JOIN LATERAL (
        SELECT d.accumulated, d.period FROM fixed_asset_depreciation d
        WHERE d.asset_id = a.id ORDER BY d.period DESC LIMIT 1) d ON TRUE"""

_ON_BOOKS = "a.status IN ('active','fully_depreciated')"


class FixedAssetDataStore:

    def __init__(self):
        self._columns_cache: Dict[str, set] = {}

    def ensure_schema(self):
        ensure_schema()

    def _table_columns(self, table_name: str) -> set:
        """Actual columns of a table (cached) — lets the GL posting tolerate
        a journal schema that differs from the one in init_db.sql."""
        cols = self._columns_cache.get(table_name)
        if cols:
            return cols
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s",
                                (table_name,))
                    cols = {r["column_name"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("_table_columns(%s): %s", table_name, e)
            cols = set()
        if cols:
            self._columns_cache[table_name] = cols
        return cols

    # ── Categories ───────────────────────────────────────────────────
    def get_categories(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""SELECT c.*, (SELECT COUNT(*) FROM fixed_assets a
                                                WHERE a.category_id=c.id) AS asset_count
                                   FROM fixed_asset_categories c WHERE c.company_id=%s
                                   ORDER BY c.code, c.name""", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_categories: %s", e); return []

    def get_category(self, category_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM fixed_asset_categories WHERE id=%s AND company_id=%s",
                                (category_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_category: %s", e); return None

    def create_category(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            method = data.get("default_method") if data.get("default_method") in METHODS else "straight_line"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO fixed_asset_categories(id,company_id,name,code,default_method,
                           default_useful_life_months,default_salvage_pct,default_declining_rate,
                           gl_asset_account,gl_depreciation_expense_account,gl_accumulated_depreciation_account)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id, _str(data.get("name")), _str(data.get("code")).upper(),
                         method, _int(data.get("default_useful_life_months"), 60),
                         _num(data.get("default_salvage_pct")), _opt(data.get("default_declining_rate")),
                         _str(data.get("gl_asset_account")), _str(data.get("gl_depreciation_expense_account")),
                         _str(data.get("gl_accumulated_depreciation_account"))))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_category: %s", e); return None

    def update_category(self, category_id: str, company_id: str, data: dict) -> bool:
        try:
            method = data.get("default_method") if data.get("default_method") in METHODS else "straight_line"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE fixed_asset_categories SET name=%s,code=%s,default_method=%s,
                           default_useful_life_months=%s,default_salvage_pct=%s,default_declining_rate=%s,
                           gl_asset_account=%s,gl_depreciation_expense_account=%s,
                           gl_accumulated_depreciation_account=%s,updated_at=NOW()
                           WHERE id=%s AND company_id=%s""",
                        (_str(data.get("name")), _str(data.get("code")).upper(), method,
                         _int(data.get("default_useful_life_months"), 60), _num(data.get("default_salvage_pct")),
                         _opt(data.get("default_declining_rate")), _str(data.get("gl_asset_account")),
                         _str(data.get("gl_depreciation_expense_account")),
                         _str(data.get("gl_accumulated_depreciation_account")), category_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_category: %s", e); return False

    def delete_category(self, category_id: str, company_id: str) -> tuple[bool, str]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM fixed_assets WHERE category_id=%s AND company_id=%s",
                                (category_id, company_id))
                    if cur.fetchone()["c"]:
                        return False, "Category is in use by one or more assets"
                    cur.execute("DELETE FROM fixed_asset_categories WHERE id=%s AND company_id=%s",
                                (category_id, company_id))
                    return cur.rowcount > 0, ""
        except Exception as e:
            logger.error("delete_category: %s", e); return False, str(e)

    def seed_default_categories(self, company_id: str) -> int:
        """Insert the Ethiopian tax-proclamation classes if the company has none."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM fixed_asset_categories WHERE company_id=%s",
                                (company_id,))
                    if cur.fetchone()["c"]:
                        return 0
                    for c in DEFAULT_CATEGORIES:
                        cur.execute(
                            """INSERT INTO fixed_asset_categories(id,company_id,name,code,default_method,
                               default_useful_life_months,default_salvage_pct,default_declining_rate,
                               gl_asset_account,gl_depreciation_expense_account,
                               gl_accumulated_depreciation_account)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (str(uuid.uuid4()), company_id, c["name"], c["code"], c["default_method"],
                             c["default_useful_life_months"], c["default_salvage_pct"],
                             c.get("default_declining_rate"), c["gl_asset_account"],
                             c["gl_depreciation_expense_account"], c["gl_accumulated_depreciation_account"]))
                    return len(DEFAULT_CATEGORIES)
        except Exception as e:
            logger.error("seed_default_categories: %s", e); return 0

    # ── Assets ───────────────────────────────────────────────────────
    def get_assets(self, company_id: str, category_id: str = None, status: str = None,
                   search: str = None) -> List[dict]:
        try:
            sql = f"""SELECT a.*, c.name AS category_name, c.code AS category_code,
                             COALESCE(d.accumulated,0) AS accumulated,
                             a.cost - COALESCE(d.accumulated,0) AS book_value, d.period AS last_period
                      FROM fixed_assets a
                      LEFT JOIN fixed_asset_categories c ON c.id=a.category_id
                      {_LATEST_DEP}
                      WHERE a.company_id=%s"""
            params: list = [company_id]
            if category_id:
                sql += " AND a.category_id=%s"; params.append(category_id)
            if status:
                sql += " AND a.status=%s"; params.append(status)
            if search:
                sql += " AND (a.asset_tag ILIKE %s OR a.name ILIKE %s OR a.serial_number ILIKE %s OR a.location ILIKE %s)"
                like = f"%{search.strip()}%"; params += [like, like, like, like]
            sql += " ORDER BY a.asset_tag"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_assets: %s", e); return []

    def get_asset(self, asset_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"""SELECT a.*, c.name AS category_name, c.code AS category_code,
                                           c.gl_asset_account, c.gl_depreciation_expense_account,
                                           c.gl_accumulated_depreciation_account,
                                           COALESCE(d.accumulated,0) AS accumulated,
                                           a.cost - COALESCE(d.accumulated,0) AS book_value,
                                           d.period AS last_period
                                    FROM fixed_assets a
                                    LEFT JOIN fixed_asset_categories c ON c.id=a.category_id
                                    {_LATEST_DEP}
                                    WHERE a.id=%s AND a.company_id=%s""", (asset_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_asset: %s", e); return None

    @staticmethod
    def _asset_values(data: dict) -> tuple:
        method = data.get("method") if data.get("method") in METHODS else "straight_line"
        return (_str(data.get("asset_tag")), _str(data.get("name")), _opt(data.get("category_id")),
                _str(data.get("description")), _str(data.get("serial_number")), _str(data.get("location")),
                _str(data.get("custodian")), _str(data.get("supplier")), _opt(data.get("purchase_date")),
                _opt(data.get("in_service_date")) or _opt(data.get("purchase_date")),
                _num(data.get("cost")), _num(data.get("salvage_value")),
                _int(data.get("useful_life_months"), 60), method,
                _opt(data.get("declining_rate")), _opt(data.get("total_units")),
                _str(data.get("currency")) or "ETB", _str(data.get("notes")))

    def create_asset(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            vals = self._asset_values(data)
            if not vals[0] or not vals[1]:
                return None
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO fixed_assets(id,company_id,asset_tag,name,category_id,description,
                           serial_number,location,custodian,supplier,purchase_date,in_service_date,cost,
                           salvage_value,useful_life_months,method,declining_rate,total_units,currency,notes)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id) + vals)
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_asset: %s", e); return None

    def update_asset(self, asset_id: str, company_id: str, data: dict) -> bool:
        try:
            vals = self._asset_values(data)
            if not vals[0] or not vals[1]:
                return False
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE fixed_assets SET asset_tag=%s,name=%s,category_id=%s,description=%s,
                           serial_number=%s,location=%s,custodian=%s,supplier=%s,purchase_date=%s,
                           in_service_date=%s,cost=%s,salvage_value=%s,useful_life_months=%s,method=%s,
                           declining_rate=%s,total_units=%s,currency=%s,notes=%s,updated_at=NOW()
                           WHERE id=%s AND company_id=%s""", vals + (asset_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_asset: %s", e); return False

    def set_status(self, asset_id: str, company_id: str, status: str) -> bool:
        if status not in STATUSES:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE fixed_assets SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                                (status, asset_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("set_status: %s", e); return False

    def next_asset_tag(self, company_id: str, prefix: str = "FA-") -> str:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM fixed_assets WHERE company_id=%s", (company_id,))
                    n = cur.fetchone()["c"] + 1
            return f"{prefix}{n:05d}"
        except Exception:
            return f"{prefix}{uuid.uuid4().hex[:6].upper()}"

    # ── Depreciation ─────────────────────────────────────────────────
    def get_depreciation(self, asset_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM fixed_asset_depreciation WHERE asset_id=%s ORDER BY period",
                                (asset_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_depreciation: %s", e); return []

    def get_units_by_period(self, asset_id: str) -> Dict[str, Decimal]:
        return {r["period"]: D(r["units_used"]) for r in self.get_depreciation(asset_id)
                if r.get("units_used") is not None}

    def full_schedule(self, asset: dict) -> List[dict]:
        """Projected schedule merged with what has actually been recorded."""
        if asset.get("method") == "units_of_production":
            rows = schedule_for_asset(asset, self.get_units_by_period(asset["id"]))
        else:
            rows = schedule_for_asset(asset)
        recorded = {r["period"]: r for r in self.get_depreciation(asset["id"])}
        for r in rows:
            rec = recorded.get(r["period"])
            r["recorded"] = bool(rec)
            r["posted"] = bool(rec and rec.get("posted"))
            r["journal_ref"] = rec.get("journal_ref") if rec else None
        return rows

    def preview_period(self, company_id: str, period: str, units: Dict[str, object] = None) -> List[dict]:
        """One line per active asset: what the run would record for ``period``."""
        if not PERIOD_RE.match(period or ""):
            return []
        units = units or {}
        out = []
        for a in self.get_assets(company_id, status="active"):
            line = {"asset": a, "period": period, "amount": Decimal(0), "accumulated": D(a["accumulated"]),
                    "book_value": D(a["book_value"]), "units_used": None, "existing": False,
                    "needs_units": False, "skip_reason": ""}
            recorded = {r["period"]: r for r in self.get_depreciation(a["id"])}
            if period in recorded:
                rec = recorded[period]
                line.update(amount=D(rec["amount"]), accumulated=D(rec["accumulated"]),
                            book_value=D(rec["book_value"]), units_used=rec.get("units_used"),
                            existing=True, posted=bool(rec.get("posted")))
                out.append(line); continue
            if a["method"] == "units_of_production":
                u = D(units.get(a["id"]), None)
                if u is None or u <= 0:
                    line.update(needs_units=True, skip_reason="Enter units used for this period")
                    out.append(line); continue
                prior = self.get_units_by_period(a["id"])
                rows = schedule_for_asset(a, {**prior, period: u})
            else:
                rows = schedule_for_asset(a)
            match = next((r for r in rows if r["period"] == period), None)
            if match is None:
                first = rows[0]["period"] if rows else None
                line["skip_reason"] = (f"Not in service until {first}" if first and first > period
                                       else "Outside depreciation schedule")
                out.append(line); continue
            line.update(amount=match["amount"], accumulated=match["accumulated"],
                        book_value=match["book_value"], units_used=match.get("units_used"))
            out.append(line)
        return out

    def run_period(self, company_id: str, period: str, units: Dict[str, object] = None) -> dict:
        """Record depreciation for every active asset for ``period``.
        Idempotent: UNIQUE(asset_id, period) + ON CONFLICT DO NOTHING."""
        result = {"period": period, "inserted": 0, "skipped": 0, "total": Decimal(0), "fully_depreciated": 0}
        lines = self.preview_period(company_id, period, units)
        if not lines and not PERIOD_RE.match(period or ""):
            result["error"] = "Invalid period"; return result
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for ln in lines:
                        if ln["existing"] or ln["skip_reason"]:
                            result["skipped"] += 1; continue
                        a = ln["asset"]
                        cur.execute(
                            """INSERT INTO fixed_asset_depreciation(id,company_id,asset_id,period,amount,
                               accumulated,book_value,units_used)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (asset_id, period) DO NOTHING""",
                            (str(uuid.uuid4()), company_id, a["id"], period, str(ln["amount"]),
                             str(ln["accumulated"]), str(ln["book_value"]),
                             str(ln["units_used"]) if ln["units_used"] is not None else None))
                        if cur.rowcount:
                            result["inserted"] += 1; result["total"] += ln["amount"]
                            if ln["book_value"] <= D(a["salvage_value"]):
                                cur.execute("UPDATE fixed_assets SET status='fully_depreciated', updated_at=NOW() "
                                            "WHERE id=%s AND status='active'", (a["id"],))
                                result["fully_depreciated"] += 1
                        else:
                            result["skipped"] += 1
        except Exception as e:
            logger.error("run_period: %s", e); result["error"] = str(e)
        return result

    def get_period_summaries(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""SELECT period, COUNT(*) AS asset_count, COALESCE(SUM(amount),0) AS total,
                                          BOOL_AND(posted) AS all_posted, BOOL_OR(posted) AS any_posted,
                                          MAX(journal_ref) AS journal_ref
                                   FROM fixed_asset_depreciation WHERE company_id=%s
                                   GROUP BY period ORDER BY period DESC""", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_period_summaries: %s", e); return []

    def post_period_to_gl(self, company_id: str, period: str, actor: str = "system") -> dict:
        """
        Create one journal entry (Dr depreciation expense / Cr accumulated
        depreciation, one pair per category) for the unposted rows of a period.
        Writes to the shared journal_entries tables only when their columns match.
        """
        res = {"posted": 0, "skipped": 0, "entry_id": None, "error": None}
        je_cols = self._table_columns("journal_entries")
        jl_cols = self._table_columns("journal_entry_lines")
        need_je = {"entry_id", "company_id", "entry_date", "description", "reference_number",
                   "total_debit", "total_credit", "created_by", "created_date", "status"}
        need_jl = {"line_id", "entry_id", "account_code", "account_name", "description",
                   "debit_amount", "credit_amount", "line_number", "created_date"}
        if not need_je <= je_cols or not need_jl <= jl_cols:
            res["error"] = "General ledger tables are not available in the expected shape"
            return res
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""SELECT d.id, d.amount, a.name AS asset_name, a.asset_tag,
                                          COALESCE(c.name,'Uncategorised') AS category_name,
                                          COALESCE(c.gl_depreciation_expense_account,'') AS exp_acct,
                                          COALESCE(c.gl_accumulated_depreciation_account,'') AS acc_acct
                                   FROM fixed_asset_depreciation d
                                   JOIN fixed_assets a ON a.id=d.asset_id
                                   LEFT JOIN fixed_asset_categories c ON c.id=a.category_id
                                   WHERE d.company_id=%s AND d.period=%s AND d.posted=FALSE AND d.amount<>0""",
                                (company_id, period))
                    rows = [dict(r) for r in cur.fetchall()]
                    groups: Dict[tuple, dict] = {}
                    postable_ids = []
                    for r in rows:
                        if not r["exp_acct"] or not r["acc_acct"]:
                            res["skipped"] += 1; continue
                        g = groups.setdefault((r["exp_acct"], r["acc_acct"], r["category_name"]), Decimal(0))
                        groups[(r["exp_acct"], r["acc_acct"], r["category_name"])] = g + D(r["amount"])
                        postable_ids.append(r["id"])
                    if not postable_ids:
                        res["error"] = ("Nothing to post" if not rows else
                                        "Categories of these assets have no GL accounts configured")
                        return res
                    total = sum(groups.values(), Decimal(0))
                    entry_id = str(uuid.uuid4())
                    y, m = int(period[:4]), int(period[5:7])
                    entry_date = date(y, m, calendar.monthrange(y, m)[1]).isoformat()
                    today = date.today().isoformat()
                    cur.execute(
                        """INSERT INTO journal_entries(entry_id,company_id,entry_date,description,
                           reference_number,total_debit,total_credit,created_by,created_date,status)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (entry_id, company_id, entry_date, f"Depreciation for {period}",
                         f"DEP-{period}", float(total), float(total), actor or "system", today, "posted"))
                    n = 0
                    for (exp_acct, acc_acct, cat_name), amt in groups.items():
                        for acct, dr, cr in ((exp_acct, amt, Decimal(0)), (acc_acct, Decimal(0), amt)):
                            n += 1
                            cur.execute(
                                """INSERT INTO journal_entry_lines(line_id,entry_id,account_code,account_name,
                                   description,debit_amount,credit_amount,line_number,created_date)
                                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (str(uuid.uuid4()), entry_id, acct, self._account_name(cur, company_id, acct),
                                 f"Depreciation {period} — {cat_name}", float(dr), float(cr), n, today))
                    cur.execute("UPDATE fixed_asset_depreciation SET posted=TRUE, journal_ref=%s WHERE id = ANY(%s)",
                                (entry_id, postable_ids))
                    res.update(posted=cur.rowcount, entry_id=entry_id, total=total)
        except Exception as e:
            logger.error("post_period_to_gl: %s", e); res["error"] = str(e)
        return res

    @staticmethod
    def _account_name(cur, company_id: str, code: str) -> str:
        try:
            cur.execute("SELECT account_name FROM chart_of_accounts WHERE company_id=%s AND account_code=%s",
                        (company_id, code))
            row = cur.fetchone()
            return row["account_name"] if row else ""
        except Exception:
            return ""

    def get_gl_accounts(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT account_code, account_name, account_type FROM chart_of_accounts "
                                "WHERE company_id=%s AND is_active=TRUE ORDER BY account_code", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_gl_accounts: %s", e); return []

    # ── Disposals ────────────────────────────────────────────────────
    def get_disposal(self, asset_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM fixed_asset_disposals WHERE asset_id=%s ORDER BY created_at DESC LIMIT 1",
                                (asset_id,))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_disposal: %s", e); return None

    def dispose_asset(self, asset_id: str, company_id: str, data: dict) -> Optional[dict]:
        asset = self.get_asset(asset_id, company_id)
        if not asset or asset["status"] in ("disposed", "written_off"):
            return None
        method = data.get("method") if data.get("method") in DISPOSAL_METHODS else "sale"
        proceeds = D(data.get("proceeds")) if method == "sale" else D(data.get("proceeds"))
        book_value = D(asset["book_value"])
        gl = gain_loss(proceeds, book_value)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO fixed_asset_disposals(id,company_id,asset_id,disposed_on,proceeds,
                           book_value_at_disposal,gain_loss,method,notes)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id, asset_id,
                         _opt(data.get("disposed_on")) or date.today().isoformat(),
                         str(proceeds), str(book_value), str(gl), method, _str(data.get("notes"))))
                    row = dict(cur.fetchone())
                    new_status = "written_off" if method == "write_off" else "disposed"
                    cur.execute("UPDATE fixed_assets SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                                (new_status, asset_id, company_id))
                    return row
        except Exception as e:
            logger.error("dispose_asset: %s", e); return None

    # ── Maintenance ──────────────────────────────────────────────────
    def get_maintenance(self, asset_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM fixed_asset_maintenance WHERE asset_id=%s ORDER BY date DESC, created_at DESC",
                                (asset_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_maintenance: %s", e); return []

    def add_maintenance(self, asset_id: str, company_id: str, data: dict) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO fixed_asset_maintenance(id,company_id,asset_id,date,description,cost,vendor)
                           VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id, asset_id,
                         _opt(data.get("date")) or date.today().isoformat(),
                         _str(data.get("description")), _num(data.get("cost")), _str(data.get("vendor"))))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("add_maintenance: %s", e); return None

    def delete_maintenance(self, entry_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM fixed_asset_maintenance WHERE id=%s AND company_id=%s",
                                (entry_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_maintenance: %s", e); return False

    # ── Dashboard / reports ──────────────────────────────────────────
    @staticmethod
    def empty_dashboard() -> dict:
        return {"count": 0, "cost": Decimal(0), "accumulated": Decimal(0), "book_value": Decimal(0),
                "by_status": {s: 0 for s in STATUSES}, "by_category": [], "monthly": [],
                "upcoming": [], "recent": [], "maintenance_cost": Decimal(0),
                "last_period": None, "unposted_periods": 0}

    def get_dashboard(self, company_id: str) -> dict:
        d = self.empty_dashboard()
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"""SELECT COUNT(*) AS n, COALESCE(SUM(a.cost),0) AS cost,
                                           COALESCE(SUM(COALESCE(d.accumulated,0)),0) AS accumulated
                                    FROM fixed_assets a {_LATEST_DEP}
                                    WHERE a.company_id=%s AND {_ON_BOOKS}""", (company_id,))
                    t = cur.fetchone()
                    d.update(count=t["n"], cost=D(t["cost"]), accumulated=D(t["accumulated"]),
                             book_value=D(t["cost"]) - D(t["accumulated"]))
                    cur.execute("SELECT status, COUNT(*) AS c FROM fixed_assets WHERE company_id=%s GROUP BY status",
                                (company_id,))
                    for r in cur.fetchall():
                        d["by_status"][r["status"]] = r["c"]
                    cur.execute(f"""SELECT COALESCE(c.name,'Uncategorised') AS name, COUNT(*) AS n,
                                           COALESCE(SUM(a.cost),0) AS cost,
                                           COALESCE(SUM(COALESCE(d.accumulated,0)),0) AS accumulated
                                    FROM fixed_assets a
                                    LEFT JOIN fixed_asset_categories c ON c.id=a.category_id
                                    {_LATEST_DEP}
                                    WHERE a.company_id=%s AND {_ON_BOOKS}
                                    GROUP BY c.name ORDER BY cost DESC""", (company_id,))
                    d["by_category"] = [{"name": r["name"], "count": r["n"], "cost": D(r["cost"]),
                                         "accumulated": D(r["accumulated"]),
                                         "book_value": D(r["cost"]) - D(r["accumulated"])}
                                        for r in cur.fetchall()]
                    cur.execute("""SELECT period, COALESCE(SUM(amount),0) AS total, BOOL_AND(posted) AS posted
                                   FROM fixed_asset_depreciation WHERE company_id=%s
                                   GROUP BY period ORDER BY period DESC LIMIT 12""", (company_id,))
                    monthly = [dict(r) for r in cur.fetchall()]
                    d["monthly"] = list(reversed(monthly))
                    d["last_period"] = monthly[0]["period"] if monthly else None
                    d["unposted_periods"] = sum(1 for m in monthly if not m["posted"])
                    cur.execute("""SELECT a.id, a.asset_tag, a.name, a.cost, a.in_service_date,
                                          (a.in_service_date + make_interval(months => a.useful_life_months))::date AS end_date
                                   FROM fixed_assets a
                                   WHERE a.company_id=%s AND a.status='active' AND a.in_service_date IS NOT NULL
                                     AND a.in_service_date + make_interval(months => a.useful_life_months)
                                         <= CURRENT_DATE + INTERVAL '6 months'
                                   ORDER BY end_date LIMIT 10""", (company_id,))
                    d["upcoming"] = [dict(r) for r in cur.fetchall()]
                    cur.execute("""SELECT a.*, c.name AS category_name FROM fixed_assets a
                                   LEFT JOIN fixed_asset_categories c ON c.id=a.category_id
                                   WHERE a.company_id=%s ORDER BY a.created_at DESC LIMIT 8""", (company_id,))
                    d["recent"] = [dict(r) for r in cur.fetchall()]
                    cur.execute("SELECT COALESCE(SUM(cost),0) AS c FROM fixed_asset_maintenance WHERE company_id=%s",
                                (company_id,))
                    d["maintenance_cost"] = D(cur.fetchone()["c"])
        except Exception as e:
            logger.error("get_dashboard: %s", e)
        return d

    def get_register(self, company_id: str, as_of: str = None) -> List[dict]:
        """Asset register with accumulated depreciation as of a period (YYYY-MM)."""
        as_of = as_of if as_of and PERIOD_RE.match(as_of) else None
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""SELECT a.*, c.name AS category_name, c.code AS category_code,
                                          COALESCE(d.accumulated,0) AS accumulated,
                                          a.cost - COALESCE(d.accumulated,0) AS book_value,
                                          d.period AS last_period,
                                          COALESCE(p.total,0) AS period_charge
                                   FROM fixed_assets a
                                   LEFT JOIN fixed_asset_categories c ON c.id=a.category_id
                                   LEFT JOIN LATERAL (
                                       SELECT d.accumulated, d.period FROM fixed_asset_depreciation d
                                       WHERE d.asset_id=a.id AND (%(as_of)s::text IS NULL OR d.period <= %(as_of)s)
                                       ORDER BY d.period DESC LIMIT 1) d ON TRUE
                                   LEFT JOIN LATERAL (
                                       SELECT SUM(amount) AS total FROM fixed_asset_depreciation d
                                       WHERE d.asset_id=a.id AND d.period = %(as_of)s) p ON TRUE
                                   WHERE a.company_id=%(cid)s
                                   ORDER BY c.code NULLS LAST, a.asset_tag""",
                                {"as_of": as_of, "cid": company_id})
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_register: %s", e); return []

    def companies_with_active_assets(self) -> List[str]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT company_id FROM fixed_assets WHERE status='active'")
                    return [r["company_id"] for r in cur.fetchall()]
        except Exception as e:
            logger.error("companies_with_active_assets: %s", e); return []


fixed_asset_store = FixedAssetDataStore()
