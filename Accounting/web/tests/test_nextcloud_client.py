"""
Pure unit tests for the Nextcloud client and the document_storage facade.

No network, no database: the WebDAV/OCS calls go to a fake session object and
the metadata store is swapped for an in-memory stand-in. Covers PROPFIND XML
parsing, path building/escaping (Amharic + spaces), OCS share-link parsing,
error mapping and the local-disk fallback round trip.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import nextcloud_client as nc  # noqa: E402
import document_storage as ds  # noqa: E402
from nextcloud_client import (  # noqa: E402
    NextcloudClient, NextcloudError, NextcloudNotConfigured, NextcloudNotFound,
    encode_path, join_path, parse_ocs_share, parse_propfind, split_path,
)


# ── fake HTTP session ────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = {k: v for k, v in (headers or {}).items()}

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")


class FakeSession:
    """Records every request; answers from a queue or a rule function."""

    def __init__(self, responses=None, rule=None):
        self.calls = []
        self.queue = list(responses or [])
        self.rule = rule

    def request(self, method, url, headers=None, data=None, timeout=None, auth=None, **kw):
        self.calls.append(dict(method=method, url=url, headers=headers or {}, data=data,
                               timeout=timeout, auth=auth))
        if self.rule:
            return self.rule(method, url, headers or {}, data)
        if self.queue:
            return self.queue.pop(0)
        return FakeResponse(200)


def make_client(session=None, **kw):
    params = dict(url="http://nc.local:8081", user="ebms", password="app-pw", root="EBMS")
    params.update(kw)
    return NextcloudClient(session=session or FakeSession(), **params)


AMHARIC = "ውል ሰነድ 2026.pdf"      # "contract document 2026" with spaces
AMHARIC_ENC = "%E1%8B%8D%E1%88%8D%20%E1%88%B0%E1%8A%90%E1%8B%B5%202026.pdf"


# ── path helpers ─────────────────────────────────────────────────────────────

def test_split_join_normalises_slashes():
    assert split_path("/a//b/./c/") == ["a", "b", "c"]
    assert join_path("EBMS", "/default/", "contracts\\42") == "EBMS/default/contracts/42"
    assert join_path("", None) == ""


def test_split_rejects_traversal():
    with pytest.raises(NextcloudError):
        split_path("default/../other")


def test_encode_path_escapes_spaces_and_amharic():
    enc = encode_path(f"default/contracts/42/{AMHARIC}")
    assert enc == f"default/contracts/42/{AMHARIC_ENC}"
    assert " " not in enc and "ው" not in enc


def test_url_for_includes_root_user_and_encoding():
    c = make_client()
    url = c.url_for(f"default/contracts/42/{AMHARIC}")
    assert url == f"http://nc.local:8081/remote.php/dav/files/ebms/EBMS/default/contracts/42/{AMHARIC_ENC}"
    assert c.url_for("") == "http://nc.local:8081/remote.php/dav/files/ebms/EBMS"


def test_url_for_encodes_user_and_root():
    c = make_client(user="ebms user", root="/EBMS Docs/")
    assert c.dav_base.endswith("/files/ebms%20user")
    assert c.url_for("x") .endswith("/ebms%20user/EBMS%20Docs/x")


# ── configuration ────────────────────────────────────────────────────────────

def test_is_configured_from_env(monkeypatch):
    for k in ("NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_APP_PASSWORD", "NEXTCLOUD_ROOT"):
        monkeypatch.delenv(k, raising=False)
    assert NextcloudClient().is_configured() is False
    assert nc.is_configured() is False
    monkeypatch.setenv("NEXTCLOUD_URL", "http://h:8081/")
    monkeypatch.setenv("NEXTCLOUD_USER", "ebms")
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "  pw  ")
    monkeypatch.setenv("NEXTCLOUD_ROOT", "  ")
    c = nc.get_client()
    assert c.is_configured() and c.url == "http://h:8081" and c.root == "EBMS"


def test_unconfigured_client_raises_typed_error():
    c = NextcloudClient(url="", user="", password="", root="EBMS", session=FakeSession())
    with pytest.raises(NextcloudNotConfigured):
        c.list_dir("")
    h = c.health()
    assert h["configured"] is False and h["ok"] is False and "Not configured" in h["error"]


def test_session_factory_uses_requests_when_available(monkeypatch):
    if nc.requests is None:
        pytest.skip("requests not installed")
    created = {}

    class _S:
        def __init__(self):
            self.headers = {}
            created["yes"] = True

    monkeypatch.setattr(nc.requests, "Session", _S)
    c = make_client(session=None)
    c._session = None
    assert isinstance(c.session, _S) and created["yes"]


# ── PROPFIND parsing ─────────────────────────────────────────────────────────

PROPFIND_XML = f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:response>
  <d:href>/remote.php/dav/files/ebms/EBMS/default/contracts/42/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype><d:getlastmodified>Fri, 11 Sep 2026 10:00:00 GMT</d:getlastmodified><d:getetag>"root"</d:getetag><oc:size>4096</oc:size></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
 <d:response>
  <d:href>/remote.php/dav/files/ebms/EBMS/default/contracts/42/{AMHARIC_ENC}</d:href>
  <d:propstat><d:prop><d:resourcetype/><d:getcontentlength>1234</d:getcontentlength><d:getlastmodified>Thu, 10 Sep 2026 08:30:00 GMT</d:getlastmodified><d:getetag>"abc123"</d:getetag><d:getcontenttype>application/pdf</d:getcontenttype><oc:fileid>777</oc:fileid></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  <d:propstat><d:prop><d:displayname/></d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>
 </d:response>
 <d:response>
  <d:href>/remote.php/dav/files/ebms/EBMS/default/contracts/42/scans/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype><d:getlastmodified>Wed, 09 Sep 2026 07:00:00 GMT</d:getlastmodified><d:getetag>"dir"</d:getetag></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
</d:multistatus>"""


def test_parse_propfind_drops_self_and_decodes_names():
    self_href = "/remote.php/dav/files/ebms/EBMS/default/contracts/42"
    entries = parse_propfind(PROPFIND_XML.encode("utf-8"), self_href=self_href)
    assert [e["name"] for e in entries] == ["scans", AMHARIC]      # dirs first
    folder, pdf = entries
    assert folder["is_dir"] is True and folder["etag"] == "dir"
    assert pdf["is_dir"] is False and pdf["size"] == 1234 and pdf["etag"] == "abc123"
    assert pdf["content_type"] == "application/pdf" and pdf["file_id"] == "777"
    assert isinstance(pdf["modified"], datetime) and pdf["modified"].day == 10


def test_parse_propfind_keeps_self_when_not_given():
    entries = parse_propfind(PROPFIND_XML.encode("utf-8"))
    assert len(entries) == 3 and entries[0]["name"] == "42"
    assert entries[0]["size"] == 4096       # oc:size fallback for collections


def test_parse_propfind_bad_xml():
    with pytest.raises(NextcloudError):
        parse_propfind(b"<not xml")


def test_list_dir_sends_depth_1_and_auth():
    sess = FakeSession([FakeResponse(207, PROPFIND_XML.encode("utf-8"))])
    c = make_client(sess)
    entries = c.list_dir("default/contracts/42")
    call = sess.calls[0]
    assert call["method"] == "PROPFIND" and call["headers"]["Depth"] == "1"
    assert call["auth"] == ("ebms", "app-pw") and call["timeout"] == 15
    assert call["url"].endswith("/EBMS/default/contracts/42")
    assert [e["name"] for e in entries] == ["scans", AMHARIC]


# ── folders / files ──────────────────────────────────────────────────────────

def test_mkdirs_walks_segments_and_treats_405_as_existing():
    sess = FakeSession([FakeResponse(405), FakeResponse(405), FakeResponse(201), FakeResponse(201)])
    c = make_client(sess)
    created = c.mkdirs("default/contracts/42")
    assert created == 2
    urls = [call["url"] for call in sess.calls]
    assert urls == [
        "http://nc.local:8081/remote.php/dav/files/ebms/EBMS",
        "http://nc.local:8081/remote.php/dav/files/ebms/EBMS/default",
        "http://nc.local:8081/remote.php/dav/files/ebms/EBMS/default/contracts",
        "http://nc.local:8081/remote.php/dav/files/ebms/EBMS/default/contracts/42",
    ]
    assert all(call["method"] == "MKCOL" for call in sess.calls)


def test_upload_puts_bytes_with_content_type():
    sess = FakeSession([FakeResponse(201)])
    c = make_client(sess)
    remote = c.upload(f"default/letters/7/{AMHARIC}", b"%PDF-1.4", "application/pdf")
    assert remote == f"EBMS/default/letters/7/{AMHARIC}"
    call = sess.calls[0]
    assert call["method"] == "PUT" and call["data"] == b"%PDF-1.4"
    assert call["headers"]["Content-Type"] == "application/pdf"
    assert call["url"].endswith(AMHARIC_ENC)


def test_upload_creates_parents_on_409_then_retries():
    sess = FakeSession([FakeResponse(409), FakeResponse(405), FakeResponse(201), FakeResponse(201),
                        FakeResponse(201)])
    c = make_client(sess)
    c.upload("default/x/file.txt", b"hi", "text/plain")
    methods = [call["method"] for call in sess.calls]
    assert methods == ["PUT", "MKCOL", "MKCOL", "MKCOL", "PUT"]


def test_upload_accepts_file_like():
    import io
    sess = FakeSession([FakeResponse(204)])
    make_client(sess).upload("a/b.txt", io.BytesIO(b"stream"), "text/plain")
    assert sess.calls[0]["data"] == b"stream"


def test_download_returns_bytes_and_content_type():
    sess = FakeSession([FakeResponse(200, b"data", {"Content-Type": "image/png; charset=binary"})])
    data, ctype = make_client(sess).download("default/p.png")
    assert data == b"data" and ctype == "image/png"
    assert sess.calls[0]["method"] == "GET"


def test_delete_and_move():
    # delete a (204), delete gone (404), MKCOL EBMS (405), MKCOL EBMS/b (201), MOVE (201)
    sess = FakeSession([FakeResponse(204), FakeResponse(404), FakeResponse(405), FakeResponse(201),
                        FakeResponse(201)])
    c = make_client(sess)
    assert c.delete("a.txt") is True
    assert c.delete("gone.txt") is False
    with pytest.raises(NextcloudNotFound):
        make_client(FakeSession([FakeResponse(404)])).delete("g", missing_ok=False)
    c.move("a.txt", "b/c d.txt")
    assert [call["method"] for call in sess.calls] == ["DELETE", "DELETE", "MKCOL", "MKCOL", "MOVE"]
    mv = sess.calls[-1]
    assert mv["method"] == "MOVE" and mv["headers"]["Overwrite"] == "F"
    assert mv["headers"]["Destination"].endswith("/EBMS/b/c%20d.txt")


# ── error mapping ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,exc,needle", [
    (401, NextcloudError, "credentials"),
    (403, NextcloudError, "credentials"),
    (404, NextcloudNotFound, "Not found"),
    (507, NextcloudError, "quota"),
    (500, NextcloudError, "failed"),
])
def test_http_errors_become_typed(status, exc, needle):
    c = make_client(FakeSession([FakeResponse(status)]))
    with pytest.raises(exc) as ei:
        c.download("x")
    assert ei.value.status == status and needle in str(ei.value)


def test_connection_error_becomes_nextcloud_error():
    def boom(*a, **k):
        raise ConnectionError("refused")
    c = make_client(FakeSession(rule=boom))
    with pytest.raises(NextcloudError) as ei:
        c.list_dir("")
    assert "unreachable" in str(ei.value) and ei.value.status is None


# ── OCS share links ──────────────────────────────────────────────────────────

OCS_JSON = {"ocs": {"meta": {"status": "ok", "statuscode": 200, "message": "OK"},
                    "data": {"id": "12", "share_type": 3, "token": "AbC123",
                             "url": "http://nc.local:8081/s/AbC123", "expiration": "2026-09-19 00:00:00"}}}

OCS_XML = b"""<?xml version="1.0"?><ocs><meta><status>ok</status><statuscode>100</statuscode><message/></meta>
<data><id>13</id><token>XyZ</token><url>http://nc.local:8081/s/XyZ</url></data></ocs>"""


def test_share_link_posts_ocs_form_and_parses_json():
    sess = FakeSession([FakeResponse(200, json.dumps(OCS_JSON).encode(), {"Content-Type": "application/json"})])
    c = make_client(sess)
    url = c.share_link(f"default/contracts/42/{AMHARIC}", expire_days=7, password="s3cret")
    assert url == "http://nc.local:8081/s/AbC123"
    call = sess.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://nc.local:8081/ocs/v2.php/apps/files_sharing/api/v1/shares?format=json"
    assert call["headers"]["OCS-APIRequest"] == "true"
    assert call["data"]["shareType"] == 3 and call["data"]["permissions"] == 1
    assert call["data"]["path"] == f"/EBMS/default/contracts/42/{AMHARIC}"
    assert call["data"]["password"] == "s3cret"
    assert len(call["data"]["expireDate"]) == 10       # YYYY-MM-DD


def test_parse_ocs_share_xml_and_errors():
    info = parse_ocs_share(OCS_XML, "text/xml")
    assert info["url"] == "http://nc.local:8081/s/XyZ" and info["token"] == "XyZ" and info["id"] == "13"
    bad = {"ocs": {"meta": {"statuscode": 404, "message": "Wrong path"}, "data": []}}
    with pytest.raises(NextcloudError) as ei:
        parse_ocs_share(json.dumps(bad).encode(), "application/json")
    assert "Wrong path" in str(ei.value)
    with pytest.raises(NextcloudError):
        parse_ocs_share(b"<ocs><meta><statuscode>100</statuscode></meta><data/></ocs>")


# ── health ───────────────────────────────────────────────────────────────────

def test_health_reports_version_root_and_latency():
    status = json.dumps({"installed": True, "maintenance": False, "versionstring": "31.0.5"}).encode()
    sess = FakeSession([FakeResponse(200, status), FakeResponse(207, PROPFIND_XML.encode())])
    h = make_client(sess).health()
    assert h["ok"] and h["version"] == "31.0.5" and h["root_exists"] is True
    assert h["latency_ms"] is not None and h["error"] is None
    assert sess.calls[0]["url"] == "http://nc.local:8081/status.php"
    assert sess.calls[1]["headers"]["Depth"] == "0" and sess.calls[1]["timeout"] == 5


def test_health_never_raises():
    h = make_client(FakeSession([FakeResponse(401)])).health()
    assert h["ok"] is False and "credentials" in h["error"]
    sess = FakeSession([FakeResponse(200, b"{}"), FakeResponse(404)])
    h = make_client(sess).health()
    assert h["ok"] is True and h["root_exists"] is False


# ── document_storage: naming + local fallback round trip ─────────────────────

def test_safe_filename_keeps_unicode_and_spaces():
    assert ds.safe_filename(AMHARIC) == AMHARIC
    assert ds.safe_filename("../../etc/passwd") == "passwd"
    assert ds.safe_filename('C:\\Users\\x\\re"port:v1?.xlsx') == "reportv1.xlsx"
    assert ds.safe_filename("   ") == "file"
    assert ds.safe_filename("a" * 300 + ".pdf").endswith(".pdf")
    assert len(ds.safe_filename("a" * 300 + ".pdf")) <= 150


def test_build_relative_path_layout():
    rel = ds.build_relative_path("default", "Contracts", "PR/2026 001", "uuid-1", AMHARIC)
    assert rel == f"default/Contracts/PR-2026-001/uuid-1_{AMHARIC}"
    assert ds.build_relative_path("", "", "", "u", "").startswith("default/general/_/u_file")


def test_content_disposition_rfc5987():
    assert ds.content_disposition("report.pdf") == 'attachment; filename="report.pdf"'
    cd = ds.content_disposition(AMHARIC)
    assert cd.startswith('attachment; filename="') and "filename*=UTF-8''" in cd
    assert AMHARIC_ENC in cd
    assert ds.content_disposition("a.png", inline=True).startswith("inline;")


class FakeStore:
    """In-memory stand-in for documents_data_store.doc_store."""

    def __init__(self):
        self.docs = {}
        self.audit = []
        self.shares = []

    def create_document(self, doc):
        d = dict(doc)
        d.setdefault("uploaded_at", datetime.now())
        d["deleted_at"] = None
        self.docs[d["id"]] = d
        return dict(d)

    def get_document(self, doc_id, company_id=None, include_deleted=True):
        d = self.docs.get(doc_id)
        if not d or (company_id and d["company_id"] != company_id):
            return None
        return dict(d)

    def get_by_remote_path(self, company_id, remote_path):
        for d in self.docs.values():
            if d["company_id"] == company_id and d.get("remote_path") == remote_path:
                return dict(d)
        return None

    def documents_for(self, company_id, module, entity_id):
        return [dict(d) for d in self.docs.values()
                if (d["company_id"], d["module"], d["entity_id"]) == (company_id, module, entity_id)
                and not d["deleted_at"]]

    def soft_delete(self, doc_id, company_id):
        d = self.docs.get(doc_id)
        if not d or d["deleted_at"]:
            return False
        d["deleted_at"] = datetime.now()
        return True

    def add_audit(self, company_id, document_id, action, actor="", ip=""):
        self.audit.append((company_id, document_id, action, actor, ip))

    def add_share_link(self, document_id, url, expires_at, created_by):
        link = dict(document_id=document_id, url=url, expires_at=expires_at, created_by=created_by)
        self.shares.append(link)
        return link


@pytest.fixture
def local_backend(tmp_path, monkeypatch):
    for k in ("NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DOCUMENTS_LOCAL_DIR", str(tmp_path / "docs"))
    store = FakeStore()
    monkeypatch.setattr(ds, "doc_store", store)
    return store


def test_local_fallback_round_trip(local_backend, tmp_path):
    assert ds.backend_name() == "local"
    doc = ds.store_document("default", "contracts", "42", AMHARIC, b"%PDF-1.4 body",
                            "application/octet-stream", uploaded_by="fde", tags=["signed", " ", "2026"],
                            ip="10.0.0.1")
    assert doc["backend"] == "local" and doc["remote_path"] is None
    assert doc["filename"] == AMHARIC and doc["content_type"] == "application/pdf"   # guessed from name
    assert doc["size"] == 13 and len(doc["sha256"]) == 64 and doc["tags"] == ["signed", "2026"]
    assert doc["fallback_error"] is None
    on_disk = Path(doc["local_path"])
    assert on_disk.is_file() and on_disk.read_bytes() == b"%PDF-1.4 body"
    assert on_disk.parent == tmp_path / "docs" / "default" / "contracts" / "42"
    assert on_disk.name == f"{doc['id']}_{AMHARIC}"

    data, name, ctype = ds.open_document(doc["id"], "default", by="fde")
    assert (data, name, ctype) == (b"%PDF-1.4 body", AMHARIC, "application/pdf")
    assert [d["id"] for d in ds.documents_for("default", "contracts", "42")] == [doc["id"]]

    with pytest.raises(ds.DocumentStorageError):
        ds.make_share_link(doc["id"], 7, "fde", "default")          # local files cannot be shared
    with pytest.raises(ds.DocumentNotFound):
        ds.open_document(doc["id"], "other-company")                 # company scoping

    assert ds.delete_document(doc["id"], "fde", "default") is True
    assert ds.documents_for("default", "contracts", "42") == []
    assert on_disk.is_file()                                          # soft delete keeps the blob
    actions = [a[2] for a in local_backend.audit]
    assert actions == ["upload", "download", "delete"]


def test_store_document_rejects_empty_and_oversized(local_backend, monkeypatch):
    with pytest.raises(ds.DocumentStorageError):
        ds.store_document("default", "general", "", "x.txt", b"")
    monkeypatch.setattr(ds, "MAX_UPLOAD_BYTES", 4)
    with pytest.raises(ds.DocumentStorageError):
        ds.store_document("default", "general", "", "x.txt", b"12345")


def test_versioning_chains_previous(local_backend):
    v1 = ds.store_document("default", "letters", "L-1", "letter.docx", b"one", uploaded_by="a", tags=["draft"])
    v2 = ds.store_document("default", "letters", "L-1", "letter-final.docx", b"two", uploaded_by="b",
                           previous_id=v1["id"])
    assert v2["version"] == 2 and v2["previous_id"] == v1["id"] and v2["tags"] == ["draft"]
    with pytest.raises(ds.DocumentNotFound):
        ds.store_document("default", "letters", "L-1", "x", b"y", previous_id="missing")


def test_nextcloud_outage_falls_back_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENTS_LOCAL_DIR", str(tmp_path / "docs"))
    store = FakeStore()
    monkeypatch.setattr(ds, "doc_store", store)

    def boom(*a, **k):
        raise ConnectionError("down")
    broken = make_client(FakeSession(rule=boom))
    monkeypatch.setattr(ds, "get_client", lambda: broken)

    assert ds.backend_name() == "nextcloud"
    doc = ds.store_document("default", "projects", "P1", "plan.txt", b"plan", "text/plain")
    assert doc["backend"] == "local" and "unreachable" in doc["fallback_error"]
    assert Path(doc["local_path"]).read_bytes() == b"plan"


def test_nextcloud_backend_upload_and_share(monkeypatch, tmp_path):
    store = FakeStore()
    monkeypatch.setattr(ds, "doc_store", store)
    sess = FakeSession([FakeResponse(201),
                        FakeResponse(200, json.dumps(OCS_JSON).encode(), {"Content-Type": "application/json"}),
                        FakeResponse(200, b"bytes", {"Content-Type": "text/plain"})])
    monkeypatch.setattr(ds, "get_client", lambda: make_client(sess))

    doc = ds.store_document("acme", "procurement", "PR 9", "quote 1.txt", b"bytes", "text/plain", "fde")
    assert doc["backend"] == "nextcloud"
    assert doc["remote_path"] == f"acme/procurement/PR-9/{doc['id']}_quote 1.txt"
    assert sess.calls[0]["url"].endswith(f"/EBMS/acme/procurement/PR-9/{doc['id']}_quote%201.txt")

    link = ds.make_share_link(doc["id"], 7, "fde", "acme")
    assert link["url"] == "http://nc.local:8081/s/AbC123" and link["expires_at"] is not None
    assert sess.calls[1]["data"]["path"] == "/EBMS/" + doc["remote_path"]

    data, name, ctype = ds.open_document(doc["id"], "acme")
    assert data == b"bytes" and name == "quote 1.txt" and ctype == "text/plain"
    assert [a[2] for a in store.audit] == ["upload", "share", "download"]


def test_list_remote_and_import(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(ds, "doc_store", store)
    sess = FakeSession([FakeResponse(207, PROPFIND_XML.encode()), FakeResponse(200, b"pdfbytes",
                                                                                {"Content-Type": "application/pdf"})])
    monkeypatch.setattr(ds, "get_client", lambda: make_client(sess))

    entries = ds.list_remote("default", "contracts/42")
    assert [e["rel_path"] for e in entries] == ["default/contracts/42/scans", f"default/contracts/42/{AMHARIC}"]

    doc = ds.import_remote_file("default", f"default/contracts/42/{AMHARIC}", "contracts", "42", by="fde")
    assert doc["backend"] == "nextcloud" and doc["filename"] == AMHARIC and doc["tags"] == ["imported"]
    assert doc["size"] == 8 and store.audit[-1][2] == "sync"
    # idempotent: second import returns the existing row without touching the network
    again = ds.import_remote_file("default", f"default/contracts/42/{AMHARIC}", "contracts", "42")
    assert again["id"] == doc["id"] and len(sess.calls) == 2
    with pytest.raises(ds.DocumentStorageError):
        ds.import_remote_file("default", "other/contracts/1/x.pdf", "contracts", "1")
