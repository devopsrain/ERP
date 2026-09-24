"""
Template render tests — Contracts module (stub harness, no DB).

Covers the contract documents card (upload form, file list, PDF preview) on
the detail page and the optional file input on the create form.
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


def _base_ctx(path="/contract/c1"):
    request = SimpleNamespace(
        url=SimpleNamespace(path=path, query=""),
        query_params=SimpleNamespace(get=lambda k, d=None: d),
        session={}, form=SimpleNamespace(),
    )
    return dict(
        request=request, session={}, url_for=lambda *a, **k: "#",
        csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
        static_url=lambda p: p, static_cdn_url="", app_version="1.0",
        current_company_id="default", current_tenant=None, company=None, user=None,
    )


def _contract(**kw):
    c = dict(id="c1", company_id="default", title="Office Lease", party_type="vendor",
             party_name="Landlord PLC", party_reference="LL-1", contract_type="lease",
             value=120000.0, currency="ETB", status="active", start_date="2026-01-01",
             end_date="2026-12-31", terms="Net 30", renewal_of=None, created_by="admin",
             created_at="2026-01-01 09:00")
    c.update(kw)
    return c


def _doc(**kw):
    d = dict(id="d1", company_id="default", module="contract", entity_id="c1",
             filename="signed-lease.pdf", content_type="application/pdf", size=204800,
             backend="local", tags=["contract"], uploaded_by="admin",
             uploaded_at="2026-01-02 10:00", version=1)
    d.update(kw)
    return d


def _event(**kw):
    e = dict(event_type="document", note="Uploaded signed-lease.pdf", actor="admin",
             created_at="2026-01-02 10:00")
    e.update(kw)
    return e


CASES = [
    ("contracts/detail.html", lambda: dict(contract=_contract(), events=[], documents=[], preview_doc=None)),
    ("contracts/detail.html", lambda: dict(contract=_contract(), events=[_event()],
                                            documents=[_doc(), _doc(id="d2", filename="annex.docx",
                                                                    content_type="application/msword",
                                                                    tags=["annex"], version=2,
                                                                    backend="nextcloud")],
                                            preview_doc=_doc())),
    ("contracts/detail.html", lambda: dict(contract=_contract(status="draft"), events=[],
                                            documents=[], preview_doc=None)),
    ("contracts/form.html", lambda: dict(contract={})),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_contract_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000


def test_detail_lists_files_and_preview():
    html = env.get_template("contracts/detail.html").render(
        **_base_ctx(), contract=_contract(), events=[], documents=[_doc()], preview_doc=_doc())
    assert "signed-lease.pdf" in html
    assert "<iframe" in html
    assert "Upload Contract File" in html


def test_create_form_accepts_file():
    html = env.get_template("contracts/form.html").render(**_base_ctx(), contract={})
    assert 'enctype="multipart/form-data"' in html
    assert 'name="contract_file"' in html
