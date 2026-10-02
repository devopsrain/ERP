"""Momentum Screener card v3 (dashboard.html): static checks on the served page.

There is no Node in CI, so the inline <script> is sanity-checked from Python:
balanced brackets after stripping strings/comments, and every bare `name(`
call resolving to a function defined in the script (or a browser global).
Run from risk-sim/: python -m pytest tests -q
"""
import re

import pytest
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)


@pytest.fixture
def missing_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path / "nope")


@pytest.fixture
def body(missing_output_dir):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    return r.text


def _script(body):
    m = re.search(r"<script>(.*?)</script>", body, re.S)
    assert m, "inline <script> block missing"
    return m.group(1)


_STRIP = re.compile(
    r"'(?:\\.|[^'\\\n])*'"          # single-quoted string
    r"|\"(?:\\.|[^\"\\\n])*\""      # double-quoted string
    r"|`(?:\\.|[^`\\])*`"           # template literal
    r"|/\*[\s\S]*?\*/"              # block comment
    r"|//[^\n]*",                   # line comment
)


def _stripped(script):
    return _STRIP.sub(" ", script)


# ---- 1. structure: every new block id exists exactly once ----

NEW_IDS = [
    "regime-block", "regime-tiles",
    "activity-block", "activity-tiles", "activity-persistent",
    "hitdays-block", "hitdays-details", "hitdays-table", "hitdays-empty",
    "queue-block", "queue-grid", "queue-footnote",
    "bucket-fresh", "bucket-fresh-table", "bucket-fresh-empty",
    "bucket-established", "bucket-established-table", "bucket-established-empty",
    "bucket-winners", "bucket-winners-table", "bucket-winners-empty",
    "legacy-momentum", "screener-table", "doublers-block",
    "breadth-block", "breadth-table", "breadth-headline",
    "concentration-block", "dataquality-note",
    "report-card", "report-card-table",
    "screener-render-error",
    # ids the hit-day click-through behaviour depends on
    "screener-section", "screener-title", "screener-meta", "screener-viewing",
]


@pytest.mark.parametrize("el_id", NEW_IDS)
def test_new_ids_present_once(body, el_id):
    assert body.count(f'id="{el_id}"') == 1, el_id


def test_card_copy(body):
    assert "research-prioritisation only" in body           # reworded caveat
    assert "Hit-day history (click a date to view that snapshot)" in body
    assert "Today’s research queue" in body
    assert "Fresh momentum" in body and "Established momentum" in body
    assert "Long-term winners" in body and "Theme breadth" in body
    assert "Report card — how past picks did" in body
    assert "/api/v1/screener/hits" in body and "viewScreenerDate" in body
    assert "http://" not in body and "https://" not in body  # no external CDNs


# ---- 2. the old quality key is gone ----

def test_quality_column_gone(body):
    script = _script(body)
    assert "QUALITY_TIP" not in script
    assert ".quality" not in script
    assert "'Quality'" not in script and '"Quality"' not in script
    # the old doublers heading is still the legacy block's default label
    assert "Doublers (≥100% in 90d/270d)" in body


# ---- 3. balanced brackets in the stripped script ----

def test_script_brackets_balanced(body):
    s = _stripped(_script(body))
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = {v: k for k, v in pairs.items()}
    stack = []
    for i, ch in enumerate(s):
        if ch in pairs:
            stack.append((ch, i))
        elif ch in closers:
            assert stack, f"unmatched closer {ch!r} at offset {i}: {s[max(0, i - 60):i + 10]!r}"
            opener, j = stack.pop()
            assert pairs[opener] == ch, (
                f"mismatch: {opener!r} at {j} closed by {ch!r} at {i}: {s[max(0, i - 60):i + 10]!r}")
    assert not stack, f"unclosed {stack[-1][0]!r} at offset {stack[-1][1]}"


def test_strings_balanced_per_line(body):
    """Cheap guard against an unterminated quote: after stripping, no quote
    character may survive outside a comment/string."""
    s = _stripped(_script(body))
    assert "'" not in s, "unterminated single quote"
    assert '"' not in s, "unterminated double quote"
    assert "`" not in s, "unterminated template literal"


# ---- 4. every bare call resolves to a definition or a known global ----

JS_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof",
    "new", "await", "async", "else", "do", "try", "throw", "void", "delete",
    "in", "of", "instanceof", "yield", "super", "this", "let", "const", "var",
}
BROWSER_GLOBALS = {
    "fetch", "parseInt", "parseFloat", "isFinite", "isNaN", "Number", "String",
    "Boolean", "Array", "Object", "Set", "Map", "Date", "Error", "Option",
    "Promise", "Symbol", "RegExp", "encodeURIComponent", "decodeURIComponent",
    "getComputedStyle", "setTimeout", "clearTimeout", "requestAnimationFrame",
    "alert", "Node", "CSS",
}


def _definitions(script):
    s = _stripped(script)
    defs = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", s))
    defs |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", s))
    # arrow-function parameters are callable too (e.g. (tr,r)=>... passed as render)
    return defs


def _calls(script):
    s = _stripped(script)
    return set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", s))


def test_every_called_function_is_defined(body):
    script = _script(body)
    defs = _definitions(script)
    calls = _calls(script) - JS_KEYWORDS - BROWSER_GLOBALS
    missing = sorted(c for c in calls if c not in defs)
    assert not missing, f"called but never defined: {missing}"


EXPECTED_FUNCS = [
    "renderScreener", "renderScreenerSnapshot", "screenerMetaLine", "renderRegime",
    "renderActivity", "renderHitDays", "viewScreenerDate", "loadScreenerHits",
    "renderQueue", "queueRulesText", "focusBucketRow", "renderBuckets", "renderBucket",
    "bucketColumns", "toggleDetail", "detailRow", "whyPanel", "catalystInfo",
    "renderMomentumTable", "renderDoublers", "paceDetail", "renderBreadth",
    "renderConcentration", "renderDataQuality", "renderReportCard", "loadScreener",
]


@pytest.mark.parametrize("name", EXPECTED_FUNCS)
def test_expected_renderers_defined_once(body, name):
    script = _stripped(_script(body))
    assert len(re.findall(rf"\bfunction\s+{name}\s*\(", script)) == 1, name


def test_snapshot_renderer_is_guarded(body):
    """A malformed snapshot must not take the rest of the dashboard down."""
    script = _script(body)
    m = re.search(r"function renderScreenerSnapshot\(doc\)\{(.*?)\n\}\n", script, re.S)
    assert m
    fn = m.group(1)
    assert "try{" in fn and "}catch(err){" in fn
    assert "console.error('screener card failed to render'" in fn
    assert "screener-render-error" in fn


def test_contract_keys_referenced(body):
    """Each v3 contract block has a renderer that reads its top-level key."""
    script = _script(body)
    for key in ("doc.regime", "doc.benchmarks", "doc.buckets", "doc.counts",
                "doc.research_queue", "doc.theme_breadth", "doc.concentration",
                "doc.activity", "doc.report_card", "doc.analytics",
                "doc.candidates", "doc.doublers"):
        assert key in script, key
    for key in ("fresh_momentum", "established_momentum", "long_term_winners",
                "risk_flags", "confirmations", "data_quality", "liquidity_tier",
                "pct_from_52w_high", "rs_qqq_20d", "sector_rs_20d", "freshness",
                "catalyst", "acceleration", "rvol_class", "profit_factor",
                "max_drawdown", "benchmark_ret", "avg_winner", "avg_loser",
                "streak_days_without_hit", "persistent", "consecutive_days"):
        assert key in script, key


def test_theme_tokens_defined_for_both_modes(body):
    """New pill/dot colours go through tokens that the dark block re-steps."""
    css = re.search(r"<style>(.*?)</style>", body, re.S).group(1)
    for tok in ("--accent", "--warn", "--slate", "--purple"):
        assert css.count(f"{tok}:") == 2, f"{tok} must be defined for light and dark"
    for cls in ("setup-BREAKOUT", "setup-ACCELERATION", "setup-TREND", "setup-PULLBACK",
                "setup-EXHAUSTION", "setup-REVERSAL_WATCH", "setup-NONE",
                "tier-A", "tier-B", "tier-C", "tier-D",
                "tdot-strong_uptrend", "tdot-uptrend", "tdot-mixed", "tdot-downtrend",
                "regime-risk_on", "regime-risk_off", "regime-neutral",
                "breadth-broadening", "breadth-narrowing", "breadth-mixed"):
        assert f".{cls}" in css, cls
