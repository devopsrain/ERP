"""
Template render tests — responsive/mobile behavior of both base layouts.

Renders trivial child templates of base.html and multicompany/base.html
(same stub-context harness as test_vat_templates.py) and asserts the
mobile/cross-browser markers added in the responsive polish pass:

- <meta name="theme-color">
- floating module-sidebar toggle (markup + JS presence check)
- module-sidebar mobile media query (translateX pattern, .show)
- iOS polish (16px inputs, tap-highlight, touch scrolling)
- collapsed-navbar dropdown scrolling (<=992px)
- Firefox scrollbar-width fallback
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))


def _base_ctx(path="/x"):
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


# Trivial child templates so each base renders exactly as modules use it,
# including a page-level .module-sidebar.
_CHILD = ('{%% extends "%s" %%}'
          '{%% block content %%}'
          '<div class="module-sidebar"><div class="module-nav">nav</div></div>'
          '<div class="content-with-sidebar"><p>Hello responsive world</p></div>'
          '{%% endblock %%}')

BASES = ["base.html", "multicompany/base.html"]

MARKERS = [
    # theme-color meta for mobile browser chrome
    'name="theme-color"',
    # floating module-sidebar toggle: markup, CSS class, and JS presence check
    'id="moduleSidebarToggle"',
    "module-sidebar-toggle",
    "querySelector('.module-sidebar')",
    # mobile module-sidebar pattern
    "translateX(-100%)",
    ".module-sidebar.show",
    # iOS / mobile polish
    "-webkit-tap-highlight-color",
    "-webkit-overflow-scrolling: touch",
    "font-size: 16px",
    # touch targets + table density on mobile
    ".table .btn-sm { min-width: 44px; min-height: 44px;",
    ".table { font-size: .85rem; }",
    # collapsed-navbar dropdown scrolling
    "max-width: 991.98px",
    # Firefox fallback alongside webkit scroll styling
    "scrollbar-width: thin",
    # navbar collapse markup (hamburger)
    "navbar-toggler",
    'data-bs-target="#topMegaNav"',
]


@pytest.mark.parametrize("base", BASES)
def test_base_renders_with_module_sidebar(base):
    html = env.from_string(_CHILD % base).render(**_base_ctx())
    assert len(html) > 1000
    assert "Hello responsive world" in html


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("marker", MARKERS)
def test_responsive_markers_present(base, marker):
    html = env.from_string(_CHILD % base).render(**_base_ctx())
    assert marker in html, f"{base} missing responsive marker: {marker!r}"


@pytest.mark.parametrize("base", BASES)
def test_content_with_sidebar_collapses_on_mobile(base):
    """The <=768px block must zero out the sidebar margin."""
    html = env.from_string(_CHILD % base).render(**_base_ctx())
    assert ".content-with-sidebar { margin-left: 0; width: 100%; }" in html


def test_no_chromium_only_css_in_bases():
    """Guard against Chromium-only CSS creeping into either base layout."""
    for base in BASES:
        css = (_WEB_DIR / "templates" / base).read_text(encoding="utf-8")
        assert ":has(" not in css, f"{base}: :has() lacks broad support"
        assert "text-wrap" not in css, f"{base}: text-wrap lacks broad support"
        # every -webkit-background-clip must be paired with the standard property
        webkit = css.count("-webkit-background-clip: text")
        plain = css.count("background-clip: text") - webkit
        assert plain >= webkit, f"{base}: unpaired -webkit-background-clip"


def test_feed_mobile_polish():
    """overview/feed.html — pills and feed items wrap on small screens."""
    src = (_WEB_DIR / "templates" / "overview" / "feed.html").read_text(encoding="utf-8")
    assert 'feed-pills d-flex flex-wrap' in src
    assert "flex-wrap: wrap" in src
    assert ".feed-item .min-width-0 { min-width: 0; }" in src
