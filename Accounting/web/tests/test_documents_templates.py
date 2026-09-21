"""
Template render tests — Documents (Nextcloud) module.

Renders every documents template with route-accurate contexts, both EMPTY and
POPULATED, following test_vat_templates.py. No database or network required.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from documents_routes import MODULES, MODULE_LABELS  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))

AMHARIC = "ውል ሰነድ 2026.pdf"
NOW = datetime(2026, 9, 11, 10, 30)


def _base_ctx(path="/documents/", is_admin=True, configured=True):
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
        # documents_routes._ctx() extras
        modules=MODULES, module_labels=MODULE_LABELS,
        backend="nextcloud" if configured else "local",
        nextcloud_configured=configured, is_admin=is_admin,
    )


def _doc(**over):
    d = {
        "id": "11111111-1111-1111-1111-111111111111", "company_id": "default",
        "module": "contracts", "entity_id": "C-42", "filename": AMHARIC,
        "content_type": "application/pdf", "size": 123456, "backend": "nextcloud",
        "remote_path": f"default/contracts/C-42/11111111-1111-1111-1111-111111111111_{AMHARIC}",
        "local_path": None, "sha256": "ab" * 32, "tags": ["signed", "2026"],
        "uploaded_by": "fde", "uploaded_at": NOW, "deleted_at": None, "version": 1,
        "previous_id": None,
    }
    d.update(over)
    return d


def _local_doc():
    return _doc(id="22222222-2222-2222-2222-222222222222", backend="local", remote_path=None,
                local_path="/tmp/ebms_documents/default/letters/L-1/2222_letter.docx",
                filename="letter.docx", module="letters", entity_id="L-1", tags=[],
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def _health(ok=True, configured=True):
    return {"configured": configured, "ok": ok and configured, "url": "http://host.docker.internal:8081",
            "user": "ebms", "root": "EBMS", "version": "31.0.5" if ok else None,
            "maintenance": False, "root_exists": ok, "latency_ms": 12.3 if configured else None,
            "error": None if (ok or not configured) else "Nextcloud unreachable at http://...: ConnectionError"}


def _stats(empty=False):
    if empty:
        return {"total": 0, "total_bytes": 0, "deleted": 0, "by_module": [],
                "by_backend": {"nextcloud": 0, "local": 0}, "shares": 0, "modules": 0}
    return {"total": 2, "total_bytes": 200000, "deleted": 1,
            "by_module": [{"module": "contracts", "count": 1, "bytes": 123456, "entities": 1, "last_upload": NOW},
                          {"module": "letters", "count": 1, "bytes": 76544, "entities": 1, "last_upload": NOW}],
            "by_backend": {"nextcloud": 1, "local": 1}, "shares": 1, "modules": 2}


def _audit():
    return [{"id": "a1", "company_id": "default", "document_id": _doc()["id"], "action": "upload",
             "actor": "fde", "ip": "10.0.0.5", "created_at": NOW, "filename": AMHARIC},
            {"id": "a2", "company_id": "default", "document_id": None, "action": "sync",
             "actor": "", "ip": "", "created_at": NOW, "filename": None}]


def _remote_entries():
    return [{"name": "scans", "is_dir": True, "size": 0, "modified": NOW, "etag": "d", "content_type": "",
             "href": "/x", "file_id": "1", "rel_path": "default/contracts/C-42/scans", "in_ebms": False},
            {"name": AMHARIC, "is_dir": False, "size": 123456, "modified": NOW, "etag": "e",
             "content_type": "application/pdf", "href": "/y",
             "file_id": "2", "rel_path": _doc()["remote_path"], "in_ebms": True},
            {"name": "phone-photo.jpg", "is_dir": False, "size": 4096, "modified": None, "etag": "f",
             "content_type": "image/jpeg", "href": "/z", "file_id": "3",
             "rel_path": "default/contracts/C-42/phone-photo.jpg", "in_ebms": False}]


def _dashboard_ctx(empty=False):
    return dict(health=_health(ok=not empty), stats=_stats(empty),
                recent=[] if empty else [_doc(), _local_doc()], audit=[] if empty else _audit())


def _browse_ctx(level, empty=False, remote_error=None):
    ctx = dict(module=None, entity_id=None, modules_summary=[], entities=[], documents=[],
               remote_entries=[], remote_error=remote_error, remote_path="EBMS/default",
               remote_web_url="http://nc/index.php/apps/files/?dir=/EBMS/default")
    if level >= 1:
        ctx["module"] = "contracts"
    if level >= 2:
        ctx["entity_id"] = "C-42"
    if not empty:
        if level == 0:
            ctx["modules_summary"] = _stats()["by_module"]
        elif level == 1:
            ctx["entities"] = [{"entity_id": "C-42", "count": 1, "bytes": 123456, "last_upload": NOW}]
        else:
            ctx["documents"] = [_doc(), _doc(id="3", version=2, tags=[])]
        if not remote_error:
            ctx["remote_entries"] = _remote_entries()
    return ctx


def _detail_ctx(doc=None, versions=None, shares=None, audit=None):
    doc = doc or _doc()
    return dict(doc=doc, versions=versions if versions is not None else [doc],
                shares=shares or [], audit=audit or [],
                remote_web_url="http://nc/index.php/apps/files/?dir=/EBMS/default/contracts/C-42"
                if doc.get("remote_path") else "")


def _shares():
    return [{"id": "s1", "document_id": _doc()["id"], "url": "http://nc/s/AbC123",
             "expires_at": NOW + timedelta(days=7), "created_by": "fde", "created_at": NOW, "expired": False},
            {"id": "s2", "document_id": _doc()["id"], "url": "http://nc/s/Old",
             "expires_at": NOW - timedelta(days=1), "created_by": "fde", "created_at": NOW, "expired": True},
            {"id": "s3", "document_id": _doc()["id"], "url": "http://nc/s/Forever",
             "expires_at": None, "created_by": "", "created_at": NOW, "expired": False}]


def _search_ctx(searched, results):
    return dict(filters=dict(q="ውል" if searched else "", module="contracts" if searched else "",
                             tag="", date_from="2026-01-01" if searched else "", date_to="", backend=""),
                results=results, searched=searched)


def _settings_ctx(configured=True, ok=True, empty=False):
    return dict(config={"configured": configured, "backend": "nextcloud" if configured else "local",
                        "url": "http://host.docker.internal:8081" if configured else "",
                        "user": "ebms" if configured else "", "root": "EBMS",
                        "password_set": configured, "password_hint": "set (29 chars)" if configured else "not set",
                        "local_dir": "/tmp/ebms_documents", "local_dir_env": "", "max_upload_mb": 100,
                        "requests_available": True},
                health=_health(ok=ok, configured=configured), stats=_stats(empty), company_root="default")


def _folders():
    return [{"id": "f1", "company_id": "default", "name": "Audit 2026", "module": "finance",
             "path": "default/finance/Audit-2026", "created_by": "fde", "created_at": NOW},
            {"id": "f2", "company_id": "default", "name": "Misc", "module": None,
             "path": "default/Misc", "created_by": "", "created_at": None}]


CASES = [
    ("documents/dashboard.html", lambda: _dashboard_ctx(empty=True)),
    ("documents/dashboard.html", lambda: _dashboard_ctx()),
    ("documents/browse.html",    lambda: _browse_ctx(0, empty=True)),
    ("documents/browse.html",    lambda: _browse_ctx(0)),
    ("documents/browse.html",    lambda: _browse_ctx(1)),
    ("documents/browse.html",    lambda: _browse_ctx(2)),
    ("documents/browse.html",    lambda: _browse_ctx(2, remote_error="Nextcloud unreachable")),
    ("documents/upload.html",    lambda: dict(preset_module="", preset_entity="", folders=[], max_upload_mb=100)),
    ("documents/upload.html",    lambda: dict(preset_module="letters", preset_entity="L-1",
                                              folders=_folders(), max_upload_mb=100)),
    ("documents/detail.html",    lambda: _detail_ctx()),
    ("documents/detail.html",    lambda: _detail_ctx(doc=_local_doc())),
    ("documents/detail.html",    lambda: _detail_ctx(doc=_doc(deleted_at=NOW),
                                                     versions=[_doc(), _doc(id="v2", version=2, deleted_at=NOW)],
                                                     shares=_shares(), audit=_audit())),
    ("documents/search.html",    lambda: _search_ctx(False, [])),
    ("documents/search.html",    lambda: _search_ctx(True, [])),
    ("documents/search.html",    lambda: _search_ctx(True, [_doc(), _local_doc()])),
    ("documents/settings.html",  lambda: _settings_ctx()),
    ("documents/settings.html",  lambda: _settings_ctx(configured=False, empty=True)),
    ("documents/settings.html",  lambda: _settings_ctx(ok=False)),
    ("documents/folders.html",   lambda: dict(folders=[], company_root="default")),
    ("documents/folders.html",   lambda: dict(folders=_folders(), company_root="default")),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_documents_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000


@pytest.mark.parametrize("template,ctx_fn", CASES[:1] + CASES[7:8] + CASES[18:19],
                         ids=["dashboard", "upload", "folders"])
def test_documents_templates_render_without_nextcloud_for_viewers(template, ctx_fn):
    """Local backend + non-admin: no settings link, no crash."""
    html = env.get_template(template).render(**_base_ctx(is_admin=False, configured=False), **ctx_fn())
    nav = html.split("nav nav-pills")[1].split("</ul>")[0]
    assert "Local disk" in html and "bi-gear" not in nav


def test_nav_partial_receives_active_page():
    """{% with active=... %}{% include %} must reach _nav.html (one active pill)."""
    html = env.get_template("documents/dashboard.html").render(**_base_ctx(), **_dashboard_ctx(empty=True))
    nav = html.split("nav nav-pills")[1].split("</ul>")[0]
    assert nav.count("nav-link active") == 1 and "Dashboard" in nav.split("nav-link active")[1][:60]
    assert "bi-gear" in nav                       # admin sees Settings


def test_browse_shows_import_only_for_unregistered_files():
    html = env.get_template("documents/browse.html").render(**_base_ctx(), **_browse_ctx(2))
    assert html.count("Import into EBMS") == 1
    assert "phone-photo.jpg" in html and AMHARIC in html
    assert 'name="rel_path" value="default/contracts/C-42/phone-photo.jpg"' in html


def test_browse_hides_nextcloud_panel_when_not_configured():
    html = env.get_template("documents/browse.html").render(
        **_base_ctx(configured=False), **_browse_ctx(2))
    assert "Nextcloud folder" not in html and "Import into EBMS" not in html


def test_detail_share_form_only_for_nextcloud_files():
    nc_html = env.get_template("documents/detail.html").render(**_base_ctx(), **_detail_ctx())
    assert "Create public link" in nc_html and "Upload new version" in nc_html
    local_html = env.get_template("documents/detail.html").render(
        **_base_ctx(), **_detail_ctx(doc=_local_doc()))
    assert "Create public link" not in local_html and "need the Nextcloud backend" in local_html
    deleted = env.get_template("documents/detail.html").render(
        **_base_ctx(), **_detail_ctx(doc=_doc(deleted_at=NOW)))
    assert "Upload new version" not in deleted and "was deleted" in deleted


def test_settings_lists_env_vars_and_actions():
    html = env.get_template("documents/settings.html").render(**_base_ctx(), **_settings_ctx())
    for needle in ("NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_APP_PASSWORD", "NEXTCLOUD_ROOT",
                   "Test connection", "Create folder structure", "set (29 chars)"):
        assert needle in html
    assert "app-pw" not in html


def test_templates_have_no_top_level_document_ready():
    for template, _ in CASES:
        source = (_WEB_DIR / "templates" / template).read_text(encoding="utf-8")
        assert "$(document).ready" not in source, template
