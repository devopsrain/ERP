"""
Template render tests — Letters module.

Renders the letters templates with route-accurate contexts, both EMPTY and
POPULATED (composed + uploaded letters), following the pattern of
test_vat_templates.py. No database required.
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

SIGNATORIES = ["PM", "FM", "MD"]


def _base_ctx(path="/letters/"):
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


def _composed_letter(**over):
    letter = {
        "letter_id": "11111111-1111-1111-1111-111111111111",
        "ref_number": "REF-0001",
        "date": "2026-07-28",
        "to": "Ministry of Finance",
        "to_address": "1 Government Rd, Oslo",
        "subject": "Re: Annual accounts",
        "body": "First paragraph.\n\nSecond paragraph.",
        "cc": "CEO",
        "status": "signed",
        "created_by": "fde",
        "created_at": "2026-07-28T10:00:00",
        "signatures": {"PM": {"signed_by": "fde",
                              "signed_at": "2026-07-28T10:05:00"}},
        "sent_at": None,
        "sent_by": None,
        "company_id": "default",
    }
    letter.update(over)
    return letter


def _uploaded_letter(**over):
    return _composed_letter(
        letter_id="22222222-2222-2222-2222-222222222222",
        ref_number="REF-0002",
        subject="Signed contract letter",
        body="",
        status="draft",
        signatures={},
        source="uploaded",
        category="Contract",
        stored_filename="abc123.pdf",
        original_filename="contract-letter.pdf",
        **over,
    )


def _signatures():
    return {"PM": {"role": "PM",
                   "data_url": "data:image/png;base64,AAAA",
                   "saved_at": "2026-07-01T09:00:00",
                   "saved_by": "fde"}}


def _tracker(letter):
    return [{"tracker_id": "t1", "letter_id": letter["letter_id"],
             "ref_number": letter["ref_number"], "subject": letter["subject"],
             "to": letter["to"], "action": "uploaded", "actor": "fde",
             "details": "Ready letter uploaded",
             "timestamp": "2026-07-28T10:00:00"}]


def _dashboard_ctx(letters):
    return dict(letters=letters, signatures=_signatures(),
                signatories=SIGNATORIES)


def _view_ctx(letter):
    return dict(letter=letter, signatures=_signatures(),
                tracker=_tracker(letter), signatories=SIGNATORIES)


CASES = [
    ("letters/dashboard.html", lambda: _dashboard_ctx([])),
    ("letters/dashboard.html", lambda: _dashboard_ctx([_composed_letter(),
                                                       _uploaded_letter()])),
    ("letters/view.html",      lambda: _view_ctx(_composed_letter())),
    ("letters/view.html",      lambda: _view_ctx(_uploaded_letter())),
    ("letters/view.html",      lambda: _view_ctx(_composed_letter(
                                    status="sent",
                                    sent_at="2026-07-28T12:00:00",
                                    sent_by="fde"))),
    ("letters/upload.html",    lambda: dict(letter={})),
    ("letters/upload.html",    lambda: dict(letter={"subject": "Re: X",
                                                    "to": "Acme",
                                                    "category": "Notice"})),
    ("letters/compose.html",   lambda: dict(letter={},
                                            signatories=SIGNATORIES)),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_letter_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000  # sanity: a real page came out


def test_uploaded_letter_markers_present():
    """Uploaded letters must show the indicator and original-download link."""
    ltr = _uploaded_letter()
    html = env.get_template("letters/view.html").render(
        **_base_ctx(), **_view_ctx(ltr))
    assert "Uploaded document" in html
    assert f"/letters/{ltr['letter_id']}/download-original" in html
    assert "contract-letter.pdf" in html

    dash = env.get_template("letters/dashboard.html").render(
        **_base_ctx(), **_dashboard_ctx([ltr]))
    assert "Uploaded" in dash
    assert "/letters/upload" in dash
