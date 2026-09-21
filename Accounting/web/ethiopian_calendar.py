"""
Ethiopian calendar (ዓመተ ምሕረት / Amete Mihret) ⇄ Gregorian conversion.

Pure-Python, dependency-free. Algorithm: Julian Day Number arithmetic
(Beyene & Kudlek), which is exact for every date in the Gregorian era.

    >>> to_ethiopian(date(2026, 9, 11))
    EthDate(year=2019, month=1, day=1)
    >>> to_gregorian(2019, 1, 1)
    datetime.date(2026, 9, 11)

Ethiopian year structure: 12 months × 30 days + Pagume (5 days, 6 in a leap
year). Ethiopian leap years are those with ``year % 4 == 3``. New Year
(Enkutatash, Meskerem 1) falls on 11 September, or 12 September in the
Gregorian year *preceding* a Gregorian leap year.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Union

# JDN of Meskerem 1, year 1 (Amete Mihret epoch) minus one day, so that the
# formulas below are 1-based in day and month.
_JD_EPOCH_OFFSET_AMETE_MIHRET = 1723856

MONTHS_AM = [
    "መስከረም", "ጥቅምት", "ኅዳር", "ታኅሣሥ", "ጥር", "የካቲት",
    "መጋቢት", "ሚያዝያ", "ግንቦት", "ሰኔ", "ሐምሌ", "ነሐሴ", "ጳጉሜን",
]
MONTHS_EN = [
    "Meskerem", "Tikimt", "Hidar", "Tahsas", "Tir", "Yekatit",
    "Megabit", "Miyazya", "Ginbot", "Sene", "Hamle", "Nehase", "Pagume",
]
# Monday-first to match datetime.weekday()
WEEKDAYS_AM = ["ሰኞ", "ማክሰኞ", "ረቡዕ", "ሐሙስ", "ዓርብ", "ቅዳሜ", "እሑድ"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Ge'ez numerals (used optionally for formal documents)
_GEEZ_ONES = ["", "፩", "፪", "፫", "፬", "፭", "፮", "፯", "፰", "፱"]
_GEEZ_TENS = ["", "፲", "፳", "፴", "፵", "፶", "፷", "፸", "፹", "፺"]


@dataclass(frozen=True)
class EthDate:
    year: int
    month: int   # 1..13
    day: int     # 1..30 (1..5/6 for Pagume)

    # ── formatting ────────────────────────────────────────────────
    def month_name(self, lang: str = "am") -> str:
        names = MONTHS_AM if lang == "am" else MONTHS_EN
        return names[self.month - 1]

    def weekday(self) -> int:
        """0 = Monday … 6 = Sunday (same convention as datetime)."""
        return to_gregorian(self.year, self.month, self.day).weekday()

    def weekday_name(self, lang: str = "am") -> str:
        names = WEEKDAYS_AM if lang == "am" else WEEKDAYS_EN
        return names[self.weekday()]

    def format(self, lang: str = "am", with_weekday: bool = False, geez: bool = False) -> str:
        """Human string, e.g. ``መስከረም 2፣ 2019`` or ``Meskerem 2, 2019``."""
        day = to_geez(self.day) if geez else str(self.day)
        year = to_geez(self.year) if geez else str(self.year)
        sep = "፣" if lang == "am" else ","
        s = f"{self.month_name(lang)} {day}{sep} {year}"
        if with_weekday:
            s = f"{self.weekday_name(lang)}{sep} {s}"
        return s

    def iso(self) -> str:
        """``YYYY-MM-DD`` in the Ethiopian calendar (sortable)."""
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.format("en")

    def to_gregorian(self) -> date:
        return to_gregorian(self.year, self.month, self.day)


# ── core arithmetic ───────────────────────────────────────────────

def _gregorian_to_jdn(y: int, m: int, d: int) -> int:
    a = (14 - m) // 12
    yy = y + 4800 - a
    mm = m + 12 * a - 3
    return d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045


def _jdn_to_gregorian(jdn: int) -> date:
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - 146097 * b // 4
    d = (4 * c + 3) // 1461
    e = c - 1461 * d // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return date(year, month, day)


def _ethiopian_to_jdn(year: int, month: int, day: int) -> int:
    return (_JD_EPOCH_OFFSET_AMETE_MIHRET + 365
            + 365 * (year - 1) + year // 4 + 30 * month + day - 31)


def _jdn_to_ethiopian(jdn: int) -> EthDate:
    r = (jdn - _JD_EPOCH_OFFSET_AMETE_MIHRET) % 1461
    n = r % 365 + 365 * (r // 1460)
    year = 4 * ((jdn - _JD_EPOCH_OFFSET_AMETE_MIHRET) // 1461) + r // 365 - r // 1460
    month = n // 30 + 1
    day = n % 30 + 1
    return EthDate(year, month, day)


# ── public API ────────────────────────────────────────────────────

DateLike = Union[date, datetime, str, None]


def _coerce(d: DateLike) -> Optional[date]:
    if d is None or d == "":
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        s = d.strip()[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
    return None


def to_ethiopian(d: DateLike) -> Optional[EthDate]:
    """Gregorian date / datetime / ISO string → EthDate (None if unparseable)."""
    g = _coerce(d)
    if g is None:
        return None
    return _jdn_to_ethiopian(_gregorian_to_jdn(g.year, g.month, g.day))


def to_gregorian(year: int, month: int, day: int) -> date:
    """Ethiopian Y/M/D → Gregorian date. Raises ValueError for invalid dates."""
    if not 1 <= month <= 13:
        raise ValueError("Ethiopian month must be 1..13")
    if month == 13:
        if not 1 <= day <= (6 if is_leap_year(year) else 5):
            raise ValueError("Invalid Pagume day for year %d" % year)
    elif not 1 <= day <= 30:
        raise ValueError("Ethiopian day must be 1..30")
    return _jdn_to_gregorian(_ethiopian_to_jdn(year, month, day))


def is_leap_year(eth_year: int) -> bool:
    return eth_year % 4 == 3


def days_in_month(eth_year: int, month: int) -> int:
    if month == 13:
        return 6 if is_leap_year(eth_year) else 5
    return 30


def today_ethiopian() -> EthDate:
    return to_ethiopian(date.today())  # type: ignore[return-value]


def ethiopian_year_for(d: DateLike) -> Optional[int]:
    e = to_ethiopian(d)
    return e.year if e else None


def fiscal_year_bounds(eth_year: int) -> tuple[date, date]:
    """Ethiopian government fiscal year runs Hamle 1 – Sene 30.

    ``fiscal_year_bounds(2018)`` → (2025-07-08, 2026-07-07): FY 2018 EC.
    """
    start = to_gregorian(eth_year - 1, 11, 1)   # Hamle 1 of the previous EC year
    end = to_gregorian(eth_year, 10, 30)        # Sene 30
    return start, end


def current_fiscal_year(d: DateLike = None) -> int:
    """The Ethiopian fiscal year that contains ``d`` (default: today)."""
    g = _coerce(d) or date.today()
    e = to_ethiopian(g)
    assert e is not None
    return e.year + 1 if e.month >= 11 else e.year


def format_dual(d: DateLike, lang: str = "en", geez: bool = False) -> str:
    """``12 Sep 2026 · መስከረም 2፣ 2019`` (order depends on lang)."""
    g = _coerce(d)
    if g is None:
        return ""
    e = to_ethiopian(g)
    assert e is not None
    greg = g.strftime("%d %b %Y")
    eth = e.format(lang, geez=geez)
    return f"{eth} · {greg}" if lang == "am" else f"{greg} · {eth}"


def format_ethiopian(d: DateLike, lang: str = "am", with_weekday: bool = False) -> str:
    e = to_ethiopian(d)
    return e.format(lang, with_weekday=with_weekday) if e else ""


def parse_ethiopian(s: str) -> Optional[date]:
    """Parse ``DD/MM/YYYY`` or ``YYYY-MM-DD`` given in the Ethiopian calendar."""
    if not s:
        return None
    s = s.strip()
    parts = None
    if "/" in s:
        p = s.split("/")
        if len(p) == 3:
            parts = (int(p[2]), int(p[1]), int(p[0]))
    elif "-" in s:
        p = s.split("-")
        if len(p) == 3:
            parts = (int(p[0]), int(p[1]), int(p[2]))
    if not parts:
        return None
    try:
        return to_gregorian(*parts)
    except ValueError:
        return None


def to_geez(n: int) -> str:
    """Integer → Ge'ez numeral string (0 has no Ge'ez numeral; returns '0')."""
    if n <= 0:
        return str(n)
    if n >= 10_000:
        high, low = divmod(n, 10_000)
        return (to_geez(high) if high > 1 else "") + "፼" + (to_geez(low) if low else "")
    out = ""
    hundreds, rest = divmod(n, 100)
    if hundreds:
        out += (to_geez(hundreds) if hundreds > 1 else "") + "፻"
    tens, ones = divmod(rest, 10)
    out += _GEEZ_TENS[tens] + _GEEZ_ONES[ones]
    return out


__all__ = [
    "EthDate", "MONTHS_AM", "MONTHS_EN", "WEEKDAYS_AM", "WEEKDAYS_EN",
    "to_ethiopian", "to_gregorian", "is_leap_year", "days_in_month",
    "today_ethiopian", "ethiopian_year_for", "fiscal_year_bounds",
    "current_fiscal_year", "format_dual", "format_ethiopian",
    "parse_ethiopian", "to_geez",
]
