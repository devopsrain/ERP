"""
Template render tests — Management Overview activity feed.

Renders overview/feed.html with route-accurate contexts, both EMPTY and
POPULATED (items spread across Today / Yesterday / an older day), following
the pattern of test_vat_templates.py. No database required.
"""
import sys
from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import activity_feed_store  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
OLDER = TODAY - timedelta(days=3)


def _base_ctx(path="/overview/"):
    request = SimpleNamespace(
        url=SimpleNamespace(path=path, query=""),
        query_params=SimpleNamespace(get=lambda k, d=None: d),
        session={}, form=SimpleNamespace(),
    )
    return dict(
        request=request, session={}, url_for=lambda *a, **k: "#",
        csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
        static_url=lambda p: p, static_cdn_url="", app_version="1.0",
        current_company_id="default", current_tenant=None,
    )


def _feed_item(module, title, day, hour=10, **over):
    meta = {m["key"]: m for m in activity_feed_store.MODULES}[module]
    item = {
        "module": module, "icon": meta["icon"], "color": meta["color"],
        "title": title, "detail": "Some detail", "actor": "fde",
        "ts": datetime.combine(day, time(hour, 30)), "link": "#",
    }
    item.update(over)
    return item


def _populated_items():
    return [
        _feed_item("bids", "Bid “Road maintenance tender” added", TODAY, 14),
        _feed_item("letters", "Letter REF-0031 uploaded", TODAY, 9,
                   detail="Re: contract award"),
        _feed_item("cpo", "CPO for payee Acme PLC — ETB 12,000.00", YESTERDAY, 16,
                   actor=""),
        _feed_item("employees", "Employee EMP-3F2A created", YESTERDAY, 11,
                   detail="Abebe Bikila"),
        _feed_item("procurement", "Purchase order “Laptops” — ETB 250,000.00", OLDER),
        _feed_item("dividends", "Dividend “FY2025 final” declared", OLDER, 8,
                   detail=""),
    ]


def _feed_ctx(items):
    groups = OrderedDict()
    for it in items:
        groups.setdefault(it["ts"].date(), []).append(it)
    return dict(
        groups=groups, modules=activity_feed_store.MODULES,
        active_module="", limit=60, total_items=len(items),
        today=TODAY, yesterday=YESTERDAY,
    )


CASES = [
    ("overview/feed.html", lambda: _feed_ctx([])),
    ("overview/feed.html", lambda: _feed_ctx(_populated_items())),
    ("overview/feed.html", lambda: {**_feed_ctx(_populated_items()),
                                    "active_module": "bids"}),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_overview_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000  # sanity: a real page came out


def test_day_grouping_headers():
    """Items across 3 days must produce Today / Yesterday / dated headers."""
    html = env.get_template("overview/feed.html").render(
        **_base_ctx(), **_feed_ctx(_populated_items()))
    assert "Today" in html
    assert "Yesterday" in html
    assert OLDER.strftime("%A, %d %B %Y") in html
    assert "Letter REF-0031 uploaded" in html
    assert "Employee EMP-3F2A created" in html


def test_empty_state():
    html = env.get_template("overview/feed.html").render(
        **_base_ctx(), **_feed_ctx([]))
    assert "No recent activity" in html
    # Filter pills still render for every module
    for m in activity_feed_store.MODULES:
        assert f"/overview/?module={m['key']}" in html
