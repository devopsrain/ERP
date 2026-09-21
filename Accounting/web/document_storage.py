"""
Document storage facade — the ONE API other EBMS modules call to attach files
to their records (contracts, letters, projects, procurement, channels, ...).

    from document_storage import store_document, open_document, documents_for

    doc = store_document(cid, "contracts", contract_id, upload.filename, content,
                         upload.content_type, uploaded_by=username, tags=["signed"])
    data, filename, content_type = open_document(doc["id"], company_id=cid, by=username)
    docs = documents_for(cid, "contracts", contract_id)

Backend selection
    * Nextcloud (WebDAV) when NEXTCLOUD_URL / NEXTCLOUD_USER / NEXTCLOUD_APP_PASSWORD
      are set — files land in  <NEXTCLOUD_ROOT>/<company>/<module>/<entity>/<uuid>_<name>
    * otherwise local disk under DOCUMENTS_LOCAL_DIR (default <tmp>/ebms_documents/)
      using the same relative layout.
    * If Nextcloud is configured but the upload fails (outage), the file is
      written locally instead and the returned dict carries ``fallback_error``
      — an outage must never lose an upload or 500 a page.

Metadata (who/what/where, versions, share links, audit) lives in PostgreSQL via
``documents_data_store``; this module never talks to psycopg2 directly.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import tempfile
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote

from documents_data_store import doc_store as _default_store
from nextcloud_client import NextcloudError, NextcloudNotFound, get_client, join_path

logger = logging.getLogger(__name__)

# The store is a module attribute so tests (and future callers) can swap it.
doc_store = _default_store

MAX_UPLOAD_MB = int(os.environ.get("DOCUMENTS_MAX_MB", "100") or 100)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
_MAX_NAME = 150


# ── Errors ────────────────────────────────────────────────────────────────────

class DocumentStorageError(Exception):
    """Storage-layer failure that is not a Nextcloud transport error."""


class DocumentNotFound(DocumentStorageError):
    """No such document (or it belongs to another company)."""


# ── Naming / paths ────────────────────────────────────────────────────────────

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WS = re.compile(r"\s+")
_SEGMENT_BAD = re.compile(r"[^\w.\-]", re.UNICODE)


def safe_filename(name: Optional[str], default: str = "file") -> str:
    """
    Make a client-supplied filename safe for WebDAV *and* local disks while
    keeping it human-readable: Unicode (Amharic etc.) and spaces are kept,
    path separators / control characters / reserved characters are dropped.
    """
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    base = _BAD_CHARS.sub("", base)
    base = _WS.sub(" ", base).strip(" .")
    if not base or base in (".", ".."):
        return default
    if len(base) > _MAX_NAME:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 10 and stem:
            base = stem[: _MAX_NAME - len(ext) - 1].rstrip() + "." + ext
        else:
            base = base[:_MAX_NAME].rstrip()
    return base


def safe_segment(value, default: str = "_") -> str:
    """One folder-name segment: letters/digits/._- only (Unicode letters allowed)."""
    seg = _SEGMENT_BAD.sub("-", unicodedata.normalize("NFC", str(value or "").strip()))
    seg = seg.strip(".-")
    return seg[:80] or default


def build_relative_path(company_id, module, entity_id, doc_id, filename) -> str:
    """'<company>/<module>/<entity>/<uuid>_<safe filename>' (ROOT-relative)."""
    return join_path(safe_segment(company_id, "default"), safe_segment(module, "general"),
                     safe_segment(entity_id, "_"), f"{doc_id}_{safe_filename(filename)}")


def content_disposition(filename: str, inline: bool = False) -> str:
    """RFC 6266 / RFC 5987 header value that survives non-ASCII filenames."""
    kind = "inline" if inline else "attachment"
    name = safe_filename(filename)
    ascii_name = name.encode("ascii", "ignore").decode("ascii").replace('"', "")
    ascii_name = ascii_name.strip() or "download"
    if ascii_name == name:
        return f'{kind}; filename="{ascii_name}"'
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def guess_content_type(filename: str, given: Optional[str] = None) -> str:
    given = (given or "").split(";")[0].strip().lower()
    if given and given != "application/octet-stream":
        return given
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


# ── Backend plumbing ──────────────────────────────────────────────────────────

def local_dir() -> Path:
    configured = (os.environ.get("DOCUMENTS_LOCAL_DIR") or "").strip()
    return Path(configured) if configured else Path(tempfile.gettempdir()) / "ebms_documents"


def backend_name() -> str:
    return "nextcloud" if get_client().is_configured() else "local"


def describe_config() -> dict:
    """Non-secret view of the configuration for the settings page."""
    client = get_client()
    pw = client.password or ""
    return {
        "configured": client.is_configured(),
        "backend": backend_name(),
        "url": client.url or "",
        "user": client.user or "",
        "root": client.root or "",
        "password_set": bool(pw),
        "password_hint": f"set ({len(pw)} chars)" if pw else "not set",
        "local_dir": str(local_dir()),
        "local_dir_env": (os.environ.get("DOCUMENTS_LOCAL_DIR") or "").strip(),
        "max_upload_mb": MAX_UPLOAD_MB,
        "requests_available": _requests_available(),
    }


def _requests_available() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


def _to_bytes(data) -> bytes:
    if data is None:
        return b""
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if hasattr(data, "read"):
        return _to_bytes(data.read())
    if isinstance(data, str):
        return data.encode("utf-8")
    raise DocumentStorageError(f"Unsupported data type: {type(data).__name__}")


def _local_file(rel_path: str) -> Path:
    root = local_dir().resolve()
    target = (root / rel_path).resolve()
    if root != target and root not in target.parents:
        raise DocumentStorageError("Refusing to write outside the local documents directory")
    return target


def _write_blob(rel_path: str, data: bytes, content_type: str) -> dict:
    """Write bytes to Nextcloud (preferred) or local disk. Never raises on outage."""
    client = get_client()
    if client.is_configured():
        try:
            client.upload(rel_path, data, content_type)
            return {"backend": "nextcloud", "remote_path": rel_path, "local_path": None,
                    "fallback_error": None}
        except NextcloudError as e:
            logger.warning("Nextcloud upload failed for %s — storing locally: %s", rel_path, e)
            fallback = str(e)
    else:
        fallback = None
    target = _local_file(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"backend": "local", "remote_path": None, "local_path": str(target),
            "fallback_error": fallback}


def _read_blob(doc: dict) -> tuple[bytes, str]:
    ctype = doc.get("content_type") or "application/octet-stream"
    if doc.get("backend") == "nextcloud" and doc.get("remote_path"):
        data, remote_ct = get_client().download(doc["remote_path"])
        return data, (ctype if ctype != "application/octet-stream" else remote_ct)
    path = doc.get("local_path")
    if not path or not Path(path).is_file():
        raise DocumentNotFound(f"Stored file is missing on disk: {doc.get('filename')}")
    return Path(path).read_bytes(), ctype


def _delete_blob(doc: dict) -> None:
    """Best-effort removal of a blob whose metadata insert failed."""
    try:
        if doc.get("backend") == "nextcloud" and doc.get("remote_path"):
            get_client().delete(doc["remote_path"])
        elif doc.get("local_path"):
            Path(doc["local_path"]).unlink(missing_ok=True)
    except Exception as e:  # pragma: no cover - cleanup only
        logger.warning("blob cleanup failed: %s", e)


# ── Public API ────────────────────────────────────────────────────────────────

def store_document(company_id: str, module: str, entity_id: str, filename: str, data,
                   content_type: Optional[str] = None, uploaded_by: str = "",
                   tags: Optional[Iterable[str]] = None, previous_id: Optional[str] = None,
                   ip: str = "") -> dict:
    """
    Persist a file and return its metadata dict (``id``, ``backend``, ``size``,
    ``sha256`` ...). ``data`` may be bytes or a file-like object.
    Raises DocumentStorageError on empty/oversized input or metadata failure.
    """
    payload = _to_bytes(data)
    if not payload:
        raise DocumentStorageError("Uploaded file is empty")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise DocumentStorageError(f"File too large (max {MAX_UPLOAD_MB} MB)")

    company_id = company_id or "default"
    module = safe_segment(module, "general").lower()
    entity_id = safe_segment(entity_id, "_")
    doc_id = str(uuid.uuid4())
    clean_name = safe_filename(filename)
    ctype = guess_content_type(clean_name, content_type)
    version = 1
    if previous_id:
        prev = doc_store.get_document(previous_id, company_id)
        if not prev:
            raise DocumentNotFound("Previous version not found")
        version = int(prev.get("version") or 1) + 1
        module, entity_id = prev["module"], prev["entity_id"]
        if tags is None:
            tags = prev.get("tags") or []

    rel_path = build_relative_path(company_id, module, entity_id, doc_id, clean_name)
    blob = _write_blob(rel_path, payload, ctype)

    record = {
        "id": doc_id, "company_id": company_id, "module": module, "entity_id": entity_id,
        "filename": clean_name, "content_type": ctype, "size": len(payload),
        "backend": blob["backend"], "remote_path": blob["remote_path"],
        "local_path": blob["local_path"], "sha256": hashlib.sha256(payload).hexdigest(),
        "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
        "uploaded_by": uploaded_by or "", "version": version, "previous_id": previous_id,
    }
    saved = doc_store.create_document(record)
    if not saved:
        _delete_blob(blob)
        raise DocumentStorageError("Could not save document metadata (database error)")
    saved["fallback_error"] = blob["fallback_error"]
    doc_store.add_audit(company_id, doc_id, "upload", uploaded_by, ip)
    return saved


def get_document(doc_id: str, company_id: Optional[str] = None) -> dict:
    doc = doc_store.get_document(doc_id, company_id)
    if not doc:
        raise DocumentNotFound("Document not found")
    return doc


def open_document(doc_id: str, company_id: Optional[str] = None, by: str = "",
                  ip: str = "", audit: bool = True) -> tuple[bytes, str, str]:
    """Return (bytes, filename, content_type). Raises DocumentNotFound / NextcloudError."""
    doc = get_document(doc_id, company_id)
    try:
        data, ctype = _read_blob(doc)
    except NextcloudNotFound as e:
        raise DocumentNotFound(f"File is missing in Nextcloud: {doc['filename']}") from e
    if audit:
        doc_store.add_audit(doc["company_id"], doc_id, "download", by, ip)
    return data, doc["filename"], ctype


def delete_document(doc_id: str, by: str = "", company_id: Optional[str] = None,
                    ip: str = "") -> bool:
    """Soft delete — metadata is flagged, the file stays in Nextcloud/disk."""
    doc = get_document(doc_id, company_id)
    ok = doc_store.soft_delete(doc_id, doc["company_id"])
    if ok:
        doc_store.add_audit(doc["company_id"], doc_id, "delete", by, ip)
    return ok


def documents_for(company_id: str, module: str, entity_id: str) -> list[dict]:
    """Live documents attached to one record (latest version of each)."""
    return doc_store.documents_for(company_id or "default", safe_segment(module, "general").lower(),
                                   safe_segment(entity_id, "_"))


def make_share_link(doc_id: str, expire_days: Optional[int] = 7, by: str = "",
                    company_id: Optional[str] = None, password: Optional[str] = None,
                    ip: str = "") -> dict:
    """Create a public Nextcloud link. Raises DocumentStorageError for local files."""
    doc = get_document(doc_id, company_id)
    if doc.get("backend") != "nextcloud" or not doc.get("remote_path"):
        raise DocumentStorageError("Share links are only available for files stored in Nextcloud")
    if doc.get("deleted_at"):
        raise DocumentStorageError("Cannot share a deleted document")
    days = int(expire_days) if expire_days else None
    url = get_client().share_link(doc["remote_path"], expire_days=days, password=password)
    expires_at = (datetime.now() + timedelta(days=days)) if days else None
    link = doc_store.add_share_link(doc_id, url, expires_at, by) or {
        "document_id": doc_id, "url": url, "expires_at": expires_at, "created_by": by}
    doc_store.add_audit(doc["company_id"], doc_id, "share", by, ip)
    return link


# ── Nextcloud browsing / sync helpers (used by the Documents UI) ─────────────

def company_root(company_id: str) -> str:
    return safe_segment(company_id, "default")


def list_remote(company_id: str, subpath: str = "") -> list[dict]:
    """
    WebDAV listing of <ROOT>/<company>/<subpath>. Each entry gains ``rel_path``
    (ROOT-relative, what ``documents.remote_path`` stores). Raises NextcloudError;
    a missing folder yields [].
    """
    base = join_path(company_root(company_id), subpath)
    try:
        entries = get_client().list_dir(base)
    except NextcloudNotFound:
        return []
    for e in entries:
        e["rel_path"] = join_path(base, e["name"])
    return entries


def import_remote_file(company_id: str, rel_path: str, module: str, entity_id: str,
                       by: str = "", ip: str = "") -> dict:
    """
    Register a file that already exists in Nextcloud (uploaded via the desktop
    client, phone, web UI ...) as an EBMS document. Bytes are read once to
    compute size + sha256; the file is left where it is.
    """
    company_id = company_id or "default"
    rel_path = join_path(rel_path)
    if not rel_path.startswith(company_root(company_id) + "/"):
        raise DocumentStorageError("That file belongs to another company's folder")
    existing = doc_store.get_by_remote_path(company_id, rel_path)
    if existing and not existing.get("deleted_at"):
        return existing
    data, ctype = get_client().download(rel_path)
    if not data:
        raise DocumentStorageError("Remote file is empty")
    name = rel_path.rsplit("/", 1)[-1]
    record = {
        "id": str(uuid.uuid4()), "company_id": company_id,
        "module": safe_segment(module, "general").lower(), "entity_id": safe_segment(entity_id, "_"),
        "filename": safe_filename(name), "content_type": guess_content_type(name, ctype),
        "size": len(data), "backend": "nextcloud", "remote_path": rel_path, "local_path": None,
        "sha256": hashlib.sha256(data).hexdigest(), "tags": ["imported"],
        "uploaded_by": by or "", "version": 1, "previous_id": None,
    }
    saved = doc_store.create_document(record)
    if not saved:
        raise DocumentStorageError("Could not save document metadata (database error)")
    doc_store.add_audit(company_id, saved["id"], "sync", by, ip)
    return saved


def create_folder_structure(company_id: str, modules: Iterable[str]) -> int:
    """Create <ROOT>/<company>/<module> for every module. Raises NextcloudError."""
    client = get_client()
    created = client.mkdirs(company_root(company_id))
    for m in modules:
        created += client.mkdirs(join_path(company_root(company_id), safe_segment(m, "general")))
    return created


def storage_stats(company_id: str) -> dict:
    return doc_store.get_stats(company_id or "default")
