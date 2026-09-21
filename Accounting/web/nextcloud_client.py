"""
Nextcloud client for the EBMS Documents module.

Talks to a Nextcloud instance over plain HTTP:

  * WebDAV  (``/remote.php/dav/files/<user>/...``) for files and folders
  * OCS     (``/ocs/v2.php/apps/files_sharing/api/v1/shares``) for share links

Configuration comes from the environment (see docker-compose.yml):

    NEXTCLOUD_URL           e.g. http://host.docker.internal:8081
    NEXTCLOUD_USER          dedicated Nextcloud account (e.g. "ebms")
    NEXTCLOUD_APP_PASSWORD  app password from Settings -> Security
    NEXTCLOUD_ROOT          top-level folder inside that account (default "EBMS")

Every path passed to the client is *relative to NEXTCLOUD_ROOT* and uses
forward slashes ("default/contracts/42/file.pdf"). Segments are percent-encoded
per RFC 3986 so spaces, Amharic and other non-ASCII names are safe.

All failures raise :class:`NextcloudError` (typed, carries the HTTP status),
so callers can degrade gracefully instead of 500ing.

The HTTP layer uses ``requests`` when it is installed and falls back to the
standard library otherwise — the module never hard-depends on a package that
is not in requirements.txt. Tests inject a fake session via ``session=``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import quote, unquote, urlencode, urlparse

try:  # optional — see module docstring
    import requests  # type: ignore
except ImportError:  # pragma: no cover - exercised only where requests is absent
    requests = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15          # seconds, per request
HEALTH_TIMEOUT = 5            # seconds — dashboards must not hang on an outage
DAV = "{DAV:}"
OC = "{http://owncloud.org/ns}"

PROPFIND_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    b"<d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
    b"<d:getetag/><d:getcontenttype/><d:displayname/><oc:size/><oc:fileid/></d:prop>"
    b"</d:propfind>"
)


# ── Errors ────────────────────────────────────────────────────────────────────

class NextcloudError(Exception):
    """Any failure talking to Nextcloud (network, auth, HTTP status, parsing)."""

    def __init__(self, message: str, status: Optional[int] = None, path: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.path = path

    def __str__(self) -> str:  # pragma: no cover - trivial
        s = self.message
        if self.status:
            s += f" (HTTP {self.status})"
        return s


class NextcloudNotConfigured(NextcloudError):
    """Raised when a call is attempted without NEXTCLOUD_* configuration."""


class NextcloudNotFound(NextcloudError):
    """Remote path does not exist (HTTP 404)."""


# ── Configuration & path helpers ──────────────────────────────────────────────

def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    value = value.strip() if value else ""
    return value or default


def config_from_env() -> dict:
    """Read the NEXTCLOUD_* variables (blank → unset)."""
    return {
        "url": _env("NEXTCLOUD_URL").rstrip("/"),
        "user": _env("NEXTCLOUD_USER"),
        "password": _env("NEXTCLOUD_APP_PASSWORD"),
        "root": _env("NEXTCLOUD_ROOT", "EBMS").strip("/"),
    }


def split_path(path: Any) -> list[str]:
    """Split a slash path into clean segments. Drops empties and '.', rejects '..'."""
    segments: list[str] = []
    for seg in str(path or "").replace("\\", "/").split("/"):
        seg = seg.strip()
        if seg in ("", "."):
            continue
        if seg == "..":
            raise NextcloudError("Path traversal ('..') is not allowed", path=str(path))
        segments.append(seg)
    return segments


def join_path(*parts: Any) -> str:
    """Join path fragments into a normalised 'a/b/c' string (no leading slash)."""
    return "/".join(seg for part in parts for seg in split_path(part))


def encode_path(path: Any) -> str:
    """Percent-encode every segment (spaces → %20, Amharic → UTF-8 escapes)."""
    return "/".join(quote(seg, safe="") for seg in split_path(path))


# ── Minimal stdlib HTTP session (fallback when `requests` is missing) ─────────

class _Headers(dict):
    """Case-insensitive header lookup, enough for .get('Content-Type')."""

    def __init__(self, raw=None):
        super().__init__()
        for k, v in dict(raw or {}).items():
            self[k] = v

    def __setitem__(self, key, value):
        super().__setitem__(str(key).lower(), value)

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)

    def __getitem__(self, key):
        return super().__getitem__(str(key).lower())

    def __contains__(self, key):
        return super().__contains__(str(key).lower())


class _UrllibResponse:
    def __init__(self, status: int, content: bytes, headers):
        self.status_code = status
        self.content = content
        self.headers = _Headers(headers)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content or b"null")


class _UrllibSession:
    """Drop-in for the subset of requests.Session.request() the client uses."""

    def request(self, method, url, headers=None, data=None, timeout=None, auth=None, **_ignored):
        import urllib.error
        import urllib.request

        hdrs = dict(headers or {})
        body = data
        if isinstance(body, dict):
            body = urlencode(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        if auth:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
            hdrs["Authorization"] = f"Basic {token}"
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _UrllibResponse(resp.status, resp.read(), resp.headers)
        except urllib.error.HTTPError as e:
            return _UrllibResponse(e.code, e.read(), e.headers)


def _new_session():
    if requests is not None:
        session = requests.Session()
        session.headers.update({"User-Agent": "EBMS-Documents/1.0"})
        return session
    return _UrllibSession()


# ── Response parsers (pure functions — unit tested) ───────────────────────────

def _parse_http_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None


def parse_propfind(xml_bytes: bytes, self_href: Optional[str] = None) -> list[dict]:
    """
    Turn a WebDAV 207 multistatus body into a list of entries:
        {name, is_dir, size, modified, etag, content_type, href, file_id}
    The entry describing the requested collection itself (``self_href``) is
    dropped. Directories sort first, then names case-insensitively.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise NextcloudError(f"Unparseable PROPFIND response: {e}") from e

    self_norm = unquote(urlparse(self_href).path).rstrip("/") if self_href else None
    entries: list[dict] = []
    for resp in root.findall(f"{DAV}response"):
        href = resp.findtext(f"{DAV}href") or ""
        href_path = unquote(urlparse(href).path)
        if self_norm is not None and href_path.rstrip("/") == self_norm:
            continue
        prop = None
        for propstat in resp.findall(f"{DAV}propstat"):
            status = propstat.findtext(f"{DAV}status") or ""
            if " 200 " in f" {status} " or status.endswith("200 OK"):
                prop = propstat.find(f"{DAV}prop")
                break
        if prop is None:
            prop = resp.find(f".//{DAV}prop")
        if prop is None:
            continue
        is_dir = prop.find(f"{DAV}resourcetype/{DAV}collection") is not None
        name = href_path.rstrip("/").rsplit("/", 1)[-1]
        size_txt = prop.findtext(f"{DAV}getcontentlength") or prop.findtext(f"{OC}size") or "0"
        try:
            size = int(size_txt)
        except ValueError:
            size = 0
        etag = (prop.findtext(f"{DAV}getetag") or "").strip().strip('"')
        entries.append({
            "name": name,
            "is_dir": is_dir,
            "size": size,
            "modified": _parse_http_date(prop.findtext(f"{DAV}getlastmodified")),
            "etag": etag,
            "content_type": (prop.findtext(f"{DAV}getcontenttype") or "").split(";")[0].strip()
            or ("httpd/unix-directory" if is_dir else "application/octet-stream"),
            "href": href_path,
            "file_id": (prop.findtext(f"{OC}fileid") or "").strip() or None,
        })
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].casefold()))
    return entries


def parse_ocs_share(body: bytes, content_type: str = "") -> dict:
    """
    Parse an OCS share-creation response (JSON with ?format=json, or the XML
    default) into {id, url, token, expiration, status_code, message}.
    Raises NextcloudError when OCS reports a non-success status code.
    """
    meta: dict = {}
    data: dict = {}
    text = (body or b"").decode("utf-8", "replace").strip()
    if text.startswith("{") or "json" in (content_type or ""):
        try:
            ocs = json.loads(text).get("ocs", {})
        except (ValueError, AttributeError) as e:
            raise NextcloudError(f"Unparseable OCS JSON response: {e}") from e
        meta = ocs.get("meta") or {}
        data = ocs.get("data") or {}
        if isinstance(data, list):  # some endpoints wrap a single share in a list
            data = data[0] if data else {}
    else:
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise NextcloudError(f"Unparseable OCS XML response: {e}") from e
        meta_el = root.find("meta")
        data_el = root.find("data")
        if data_el is not None and data_el.find("element") is not None:
            data_el = data_el.find("element")
        meta = {c.tag: (c.text or "") for c in (meta_el if meta_el is not None else [])}
        data = {c.tag: (c.text or "") for c in (data_el if data_el is not None else [])}

    try:
        status_code = int(meta.get("statuscode") or 0)
    except (TypeError, ValueError):
        status_code = 0
    if status_code not in (100, 200):
        raise NextcloudError(
            f"Nextcloud share API error: {meta.get('message') or 'unknown'}", status=status_code)
    url = (data.get("url") or "").strip()
    if not url:
        raise NextcloudError("Nextcloud share API returned no URL", status=status_code)
    return {
        "id": str(data.get("id") or ""),
        "url": url,
        "token": data.get("token") or "",
        "expiration": data.get("expiration") or None,
        "status_code": status_code,
        "message": meta.get("message") or "",
    }


# ── Client ────────────────────────────────────────────────────────────────────

class NextcloudClient:
    """
    Thin WebDAV/OCS client. Construct with explicit values (tests) or let it
    read the environment. ``session`` may be any object exposing
    ``request(method, url, headers=, data=, timeout=, auth=)`` returning an
    object with ``status_code``, ``content`` and ``headers``.
    """

    def __init__(self, url: Optional[str] = None, user: Optional[str] = None,
                 password: Optional[str] = None, root: Optional[str] = None,
                 session=None, timeout: float = DEFAULT_TIMEOUT):
        env = config_from_env()
        self.url = (url if url is not None else env["url"]).rstrip("/")
        self.user = user if user is not None else env["user"]
        self.password = password if password is not None else env["password"]
        self.root = (root if root is not None else env["root"]).strip("/")
        self.timeout = timeout
        self._session = session

    # -- configuration -------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.url and self.user and self.password)

    @property
    def session(self):
        if self._session is None:
            self._session = _new_session()
        return self._session

    @property
    def dav_base(self) -> str:
        return f"{self.url}/remote.php/dav/files/{quote(self.user, safe='')}"

    def remote_path(self, path: Any = "") -> str:
        """Path relative to the user's home, i.e. '<ROOT>/<path>'."""
        return join_path(self.root, path)

    def _abs_url(self, home_relative: str) -> str:
        encoded = encode_path(home_relative)
        return f"{self.dav_base}/{encoded}" if encoded else self.dav_base

    def url_for(self, path: Any = "") -> str:
        """Absolute WebDAV URL for a ROOT-relative path (percent-encoded)."""
        return self._abs_url(self.remote_path(path))

    def web_url(self, path: Any = "") -> str:
        """Link to the folder/file in the Nextcloud web UI (Files app)."""
        return f"{self.url}/index.php/apps/files/?dir=/{encode_path(self.remote_path(path))}"

    # -- low level -----------------------------------------------------------

    def _request(self, method: str, path: str = "", *, url: Optional[str] = None,
                 ok=(200, 201, 204, 207), allow=(), headers=None, data=None,
                 timeout: Optional[float] = None):
        if not self.is_configured():
            raise NextcloudNotConfigured(
                "Nextcloud is not configured — set NEXTCLOUD_URL, NEXTCLOUD_USER and "
                "NEXTCLOUD_APP_PASSWORD", path=path)
        target = url or self.url_for(path)
        try:
            resp = self.session.request(
                method, target, headers=headers or {}, data=data,
                timeout=timeout or self.timeout, auth=(self.user, self.password))
        except NextcloudError:
            raise
        except Exception as e:  # requests.ConnectionError / Timeout / URLError ...
            raise NextcloudError(
                f"Nextcloud unreachable at {self.url}: {e.__class__.__name__}: {e}",
                path=path) from e
        status = int(getattr(resp, "status_code", 0) or 0)
        if status in ok or status in allow:
            return resp
        if status == 404:
            raise NextcloudNotFound(f"Not found in Nextcloud: {path or '/'}", status=404, path=path)
        if status in (401, 403):
            raise NextcloudError(
                "Nextcloud rejected the credentials — check NEXTCLOUD_USER and the app password",
                status=status, path=path)
        if status == 507:
            raise NextcloudError("Nextcloud storage quota exceeded", status=507, path=path)
        if status == 423:
            raise NextcloudError(f"File is locked in Nextcloud: {path}", status=423, path=path)
        raise NextcloudError(f"Nextcloud {method} {path or '/'} failed", status=status, path=path)

    # -- folders -------------------------------------------------------------

    def mkdirs(self, path: Any = "") -> int:
        """Create every missing folder along ROOT/<path>. Returns number created."""
        created = 0
        current: list[str] = []
        for seg in split_path(self.remote_path(path)):
            current.append(seg)
            rel = "/".join(current)
            # 405 = collection already exists; 301/302 = exists (trailing-slash redirect)
            resp = self._request("MKCOL", rel, url=self._abs_url(rel),
                                 ok=(201,), allow=(405, 301, 302))
            if resp.status_code == 201:
                created += 1
        return created

    def exists(self, path: Any = "") -> bool:
        try:
            self._request("PROPFIND", path, headers={"Depth": "0"}, data=PROPFIND_BODY, ok=(207,))
            return True
        except NextcloudNotFound:
            return False

    def stat(self, path: Any) -> Optional[dict]:
        """Single-entry PROPFIND (Depth 0). Returns an entry dict or None."""
        try:
            resp = self._request("PROPFIND", path, headers={"Depth": "0"},
                                 data=PROPFIND_BODY, ok=(207,))
        except NextcloudNotFound:
            return None
        entries = parse_propfind(resp.content)          # keep the self entry
        return entries[0] if entries else None

    def list_dir(self, path: Any = "") -> list[dict]:
        """PROPFIND Depth 1 → [{name, is_dir, size, modified, etag, ...}]."""
        url = self.url_for(path)
        resp = self._request("PROPFIND", path, url=url,
                             headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                             data=PROPFIND_BODY, ok=(207,))
        return parse_propfind(resp.content, self_href=url)

    # -- files ---------------------------------------------------------------

    def upload(self, path: Any, data, content_type: Optional[str] = None) -> str:
        """PUT bytes (or a file-like) to ROOT/<path>, creating parent folders on demand."""
        if hasattr(data, "read"):
            data = data.read()
        headers = {"Content-Type": content_type or "application/octet-stream"}
        # Optimistic PUT; 409 means a parent folder is missing → create and retry.
        resp = self._request("PUT", path, headers=headers, data=data,
                             ok=(200, 201, 204), allow=(409, 404))
        if resp.status_code in (409, 404):
            parent = "/".join(split_path(path)[:-1])
            self.mkdirs(parent)
            self._request("PUT", path, headers=headers, data=data, ok=(200, 201, 204))
        return self.remote_path(path)

    def download(self, path: Any) -> tuple[bytes, str]:
        """GET ROOT/<path> → (bytes, content_type)."""
        resp = self._request("GET", path, ok=(200,))
        ctype = (resp.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
        return resp.content, ctype or "application/octet-stream"

    def delete(self, path: Any, missing_ok: bool = True) -> bool:
        """DELETE ROOT/<path>. Returns False when it did not exist (missing_ok)."""
        try:
            self._request("DELETE", path, ok=(200, 204))
            return True
        except NextcloudNotFound:
            if missing_ok:
                return False
            raise

    def move(self, src: Any, dst: Any, overwrite: bool = False) -> str:
        """MOVE ROOT/<src> → ROOT/<dst> (parent folders of dst are created)."""
        parent = "/".join(split_path(dst)[:-1])
        if parent:
            self.mkdirs(parent)
        self._request("MOVE", src, headers={"Destination": self.url_for(dst),
                                            "Overwrite": "T" if overwrite else "F"},
                      ok=(201, 204))
        return self.remote_path(dst)

    # -- sharing (OCS) -------------------------------------------------------

    def create_share(self, path: Any, expire_days: Optional[int] = None,
                     password: Optional[str] = None, permissions: int = 1,
                     label: Optional[str] = None) -> dict:
        """Create a public link share (shareType 3, read-only by default)."""
        url = f"{self.url}/ocs/v2.php/apps/files_sharing/api/v1/shares?format=json"
        form: dict[str, Any] = {
            "path": "/" + self.remote_path(path),
            "shareType": 3,
            "permissions": int(permissions),
        }
        if expire_days:
            form["expireDate"] = (date.today() + timedelta(days=int(expire_days))).isoformat()
        if password:
            form["password"] = password
        if label:
            form["label"] = label
        resp = self._request("POST", path, url=url, data=form,
                             headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                             ok=(200,))
        return parse_ocs_share(resp.content, resp.headers.get("Content-Type") or "")

    def share_link(self, path: Any, expire_days: Optional[int] = None,
                   password: Optional[str] = None) -> str:
        """Public read-only link for ROOT/<path>."""
        return self.create_share(path, expire_days=expire_days, password=password)["url"]

    # -- health --------------------------------------------------------------

    def health(self, timeout: float = HEALTH_TIMEOUT) -> dict:
        """
        Never raises. Returns
        {configured, ok, url, user, root, version, maintenance, root_exists,
         latency_ms, error}
        """
        info: dict[str, Any] = {
            "configured": self.is_configured(), "ok": False, "url": self.url,
            "user": self.user, "root": self.root, "version": None, "maintenance": None,
            "root_exists": None, "latency_ms": None, "error": None,
        }
        if not info["configured"]:
            info["error"] = "Not configured (NEXTCLOUD_URL / NEXTCLOUD_USER / NEXTCLOUD_APP_PASSWORD)"
            return info
        t0 = time.perf_counter()
        try:
            resp = self._request("GET", "status.php", url=f"{self.url}/status.php",
                                 ok=(200,), timeout=timeout)
            try:
                status = json.loads(resp.content)
                info["version"] = status.get("versionstring") or status.get("version")
                info["maintenance"] = bool(status.get("maintenance"))
            except (ValueError, AttributeError):
                pass
            resp = self._request("PROPFIND", "", headers={"Depth": "0"}, data=PROPFIND_BODY,
                                 ok=(207,), allow=(404,), timeout=timeout)
            info["root_exists"] = resp.status_code == 207
            info["ok"] = True
        except NextcloudError as e:
            info["error"] = str(e)
        except Exception as e:  # pragma: no cover - defensive
            info["error"] = f"{e.__class__.__name__}: {e}"
        info["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return info


# ── Module-level singleton (re-built when the env changes) ───────────────────

_client: Optional[NextcloudClient] = None
_client_key: Optional[tuple] = None


def get_client() -> NextcloudClient:
    """Shared client built from the environment (cached per config)."""
    global _client, _client_key
    cfg = config_from_env()
    key = (cfg["url"], cfg["user"], cfg["password"], cfg["root"])
    if _client is None or key != _client_key:
        _client = NextcloudClient(url=cfg["url"], user=cfg["user"],
                                  password=cfg["password"], root=cfg["root"])
        _client_key = key
    return _client


def is_configured() -> bool:
    return get_client().is_configured()


def health() -> dict:
    return get_client().health()
