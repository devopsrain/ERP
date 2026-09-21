"""Ethiopian calendar + i18n unit tests (no DB, no app)."""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_WEB = Path(__file__).resolve().parents[1]
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

import ethiopian_calendar as ec  # noqa: E402
import i18n  # noqa: E402


# ── calendar ────────────────────────────────────────────────────
@pytest.mark.parametrize("greg,eth", [
    (date(2000, 9, 11), (1993, 1, 1)),
    (date(2007, 9, 12), (2000, 1, 1)),     # Ethiopian millennium
    (date(2023, 9, 12), (2016, 1, 1)),     # year after an EC leap year
    (date(2024, 9, 11), (2017, 1, 1)),
    (date(2026, 9, 11), (2019, 1, 1)),
    (date(2026, 9, 12), (2019, 1, 2)),
    (date(2026, 9, 10), (2018, 13, 5)),    # Pagume 5, 2018
    (date(2027, 9, 11), (2019, 13, 6)),    # Pagume 6 — 2019 EC is leap
    (date(2026, 1, 1), (2018, 4, 23)),     # Tahsas 23, 2018
    (date(2025, 7, 8), (2017, 11, 1)),     # Hamle 1 — fiscal year start
])
def test_round_trip(greg, eth):
    e = ec.to_ethiopian(greg)
    assert (e.year, e.month, e.day) == eth
    assert ec.to_gregorian(*eth) == greg


def test_leap_and_days():
    assert ec.is_leap_year(2019) and not ec.is_leap_year(2018)
    assert ec.days_in_month(2019, 13) == 6
    assert ec.days_in_month(2018, 13) == 5
    assert ec.days_in_month(2018, 1) == 30
    with pytest.raises(ValueError):
        ec.to_gregorian(2018, 13, 6)


def test_formatting():
    e = ec.to_ethiopian("2026-09-12")
    assert e.format("am") == "መስከረም 2፣ 2019"
    assert e.format("en") == "Meskerem 2, 2019"
    assert e.iso() == "2019-01-02"
    assert ec.format_dual(date(2026, 9, 12), "en") == "12 Sep 2026 · Meskerem 2, 2019"
    assert ec.format_dual(None) == ""
    assert ec.to_ethiopian("") is None
    assert ec.to_ethiopian("garbage") is None
    assert ec.format_ethiopian(date(2026, 9, 12), "am", with_weekday=True).startswith("ቅዳሜ")


def test_parse_and_fiscal():
    assert ec.parse_ethiopian("01/01/2019") == date(2026, 9, 11)
    assert ec.parse_ethiopian("2019-01-01") == date(2026, 9, 11)
    assert ec.parse_ethiopian("31/01/2019") is None
    assert ec.fiscal_year_bounds(2018) == (date(2025, 7, 8), date(2026, 7, 7))
    assert ec.current_fiscal_year(date(2026, 7, 8)) == 2019
    assert ec.current_fiscal_year(date(2026, 7, 7)) == 2018


def test_geez_numerals():
    assert ec.to_geez(1) == "፩"
    assert ec.to_geez(10) == "፲"
    assert ec.to_geez(30) == "፴"
    assert ec.to_geez(100) == "፻"
    assert ec.to_geez(2019) == "፳፻፲፱"


# ── i18n ────────────────────────────────────────────────────────
def _req(session=None, cookies=None, accept="", query=None):
    return SimpleNamespace(
        session=session if session is not None else {},
        cookies=cookies or {},
        headers={"accept-language": accept},
        query_params=query or {},
    )


def test_locale_resolution_order():
    assert i18n.get_locale(None) == "en"
    assert i18n.get_locale(_req()) == "en"
    assert i18n.get_locale(_req(accept="am-ET,am;q=0.9,en;q=0.8")) == "am"
    assert i18n.get_locale(_req(cookies={"ebms_lang": "am"}, accept="en")) == "am"
    assert i18n.get_locale(_req(session={"lang": "en"}, cookies={"ebms_lang": "am"})) == "en"
    assert i18n.get_locale(_req(session={"lang": "en"}, query={"lang": "am"})) == "am"
    assert i18n.get_locale(_req(session={"lang": "xx"})) == "en"


def test_translate_fallback_and_interpolation():
    assert i18n.translate("Dashboard", "am") == "ዳሽቦርድ"
    assert i18n.translate("Not in catalogue", "am") == "Not in catalogue"
    assert i18n.translate("Dashboard", "en") == "Dashboard"
    assert i18n.translate("Hello %(name)s", "am", name="Abebe") == "Hello Abebe"
    assert i18n.translate(None, "am") == ""


def test_jinja_helpers_installed_in_fresh_environment():
    i18n.install()
    env = jinja2.Environment()  # stub-harness style: no explicit globals
    tpl = env.from_string("{{ _('Dashboard') }}|{{ d|et_date }}|{{ d|dual_date }}|{{ get_lang() }}")
    out = tpl.render(request=_req(session={"lang": "am"}), d=date(2026, 9, 12))
    assert out == "ዳሽቦርድ|መስከረም 2፣ 2019|መስከረም 2፣ 2019 · 12 Sep 2026|am"
    out_en = tpl.render(request=_req(), d=date(2026, 9, 12))
    assert out_en == "Dashboard|Meskerem 2, 2019|12 Sep 2026 · Meskerem 2, 2019|en"
    # No request at all must not raise
    assert env.from_string("{{ _('Save') }}").render() == "Save"


def test_safe_next():
    from i18n_routes import _safe_next
    assert _safe_next("/vat/dashboard") == "/vat/dashboard"
    assert _safe_next("https://evil.example/x") == "/"
    assert _safe_next("//evil.example") == "/"
    assert _safe_next(None, "/home") == "/home"
