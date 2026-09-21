"""
Per-tenant API keys — generation, hashing and the FastAPI dependency.

Key format
    ebms_<43 url-safe random chars>        (shown in plaintext exactly ONCE)

Storage
    Only ``sha256(salt + ":" + key)`` is stored, with a random per-key salt,
    plus a short display ``prefix`` used as the lookup index. A leaked
    database therefore never yields a usable key.

Usage — protect an endpoint::

    from api_keys import require_api_key, require_scopes

    @router.get("/api/v3/invoices")
    async def invoices(request: Request, key=Depends(require_api_key)):
        cid = key["company_id"]

    @router.post("/api/v3/invoices")
    async def create(request: Request, key=Depends(require_scopes("write"))):
        ...

Clients send either
    X-API-Key: ebms_...
or
    Authorization: Bearer ebms_...

The dependency returns ``{"company_id", "key_id", "scopes", "name"}`` and
also sets ``request.state.api_key`` / ``request.state.company_id``.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime
from typing import Iterable, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

KEY_PREFIX = "ebms_"
# Display prefix stored alongside the hash. "ebms_" + 7 random chars is enough
# to make the lookup nearly unique while never revealing usable key material.
DISPLAY_PREFIX_LEN = 12

# Scope vocabulary offered in the UI. Additional "resource:action" scopes are
# accepted freely; matching supports "*" and "resource:*" wildcards.
SCOPES = [
    ("read",     "Read access to all resources"),
    ("write",    "Create and update resources"),
    ("webhooks", "Manage webhook endpoints and deliveries"),
    ("*",        "Full access (all current and future scopes)"),
]
SCOPE_NAMES = [s for s, _ in SCOPES]


# ── Pure helpers (no DB) ──────────────────────────────────────────────────────

def generate_api_key() -> str:
    """Return a fresh plaintext key: ``ebms_`` + 43 url-safe characters."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def generate_salt() -> str:
    return secrets.token_hex(16)


def hash_api_key(plaintext: str, salt: str) -> str:
    """sha256 over ``salt:plaintext`` — salted so equal keys never share a hash."""
    return hashlib.sha256(f"{salt}:{plaintext}".encode("utf-8")).hexdigest()


def verify_api_key(plaintext: str, salt: str, key_hash: str) -> bool:
    if not plaintext or not salt or not key_hash:
        return False
    return hmac.compare_digest(hash_api_key(plaintext, salt), key_hash)


def key_display_prefix(plaintext: str) -> str:
    return (plaintext or "")[:DISPLAY_PREFIX_LEN]


def looks_like_api_key(token: str) -> bool:
    return bool(token) and token.startswith(KEY_PREFIX) and len(token) > len(KEY_PREFIX) + 8


def normalise_scopes(raw) -> list[str]:
    """Accept a list, a comma/space separated string, or None → clean list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.replace(",", " ").split()
    else:
        parts = []
        for item in raw:
            if item is None:
                continue
            parts.extend(str(item).replace(",", " ").split())
    seen: list[str] = []
    for p in parts:
        p = p.strip().lower()
        if p and p not in seen:
            seen.append(p)
    return seen


def scope_allows(granted: Iterable[str], required: str) -> bool:
    """
    True when any granted scope satisfies ``required``.

      "*"           → everything
      "invoices:*"  → "invoices:read", "invoices:write", ...
      exact match   → itself
    """
    required = (required or "").strip().lower()
    if not required:
        return True
    for g in granted or []:
        g = (g or "").strip().lower()
        if not g:
            continue
        if g == "*" or g == required:
            return True
        if g.endswith(":*") and required.startswith(g[:-1]):
            return True
    return False


def extract_key_from_request(request: Request) -> Optional[str]:
    """``X-API-Key`` header first, then ``Authorization: Bearer ebms_...``."""
    key = (request.headers.get("X-API-Key") or "").strip()
    if key:
        return key
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if looks_like_api_key(token):
            return token
    return None


# ── Resolution against the store ──────────────────────────────────────────────

def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail,
                         headers={"WWW-Authenticate": 'Bearer realm="ebms", X-API-Key'})


def resolve_api_key(plaintext: str) -> Optional[dict]:
    """
    Look the key up by display prefix and verify the salted hash.
    Returns the api_keys row (dict) or None. Never raises.
    """
    if not looks_like_api_key(plaintext):
        return None
    try:
        from webhook_data_store import webhook_store
        candidates = webhook_store.find_api_keys_by_prefix(key_display_prefix(plaintext))
    except Exception as e:  # pragma: no cover — DB down
        logger.error("resolve_api_key lookup failed: %s", e)
        return None
    for row in candidates:
        if verify_api_key(plaintext, row.get("salt") or "", row.get("key_hash") or ""):
            return row
    return None


def _check_row(row: dict) -> None:
    if row.get("revoked_at"):
        raise _unauthorized("API key revoked")
    exp = row.get("expires_at")
    if exp:
        now = datetime.utcnow() if getattr(exp, "tzinfo", None) is None else datetime.now(exp.tzinfo)
        if exp < now:
            raise _unauthorized("API key expired")


def authenticate_request(request: Request) -> dict:
    """Shared body of the dependencies — raises 401 on any failure."""
    plaintext = extract_key_from_request(request)
    if not plaintext:
        raise _unauthorized("API key required (X-API-Key or Authorization: Bearer ebms_...)")
    row = resolve_api_key(plaintext)
    if not row:
        raise _unauthorized("Invalid API key")
    _check_row(row)

    scopes = list(row.get("scopes") or [])
    principal = {
        "company_id": row.get("company_id") or "default",
        "key_id": row.get("id"),
        "scopes": scopes,
        "name": row.get("name") or "",
    }
    request.state.api_key = principal
    request.state.company_id = principal["company_id"]
    try:
        from webhook_data_store import webhook_store
        webhook_store.touch_api_key(row["id"])
    except Exception:
        pass
    return principal


async def require_api_key(request: Request) -> dict:
    """
    FastAPI dependency: ``key=Depends(require_api_key)``.

    Returns ``{"company_id", "key_id", "scopes", "name"}``;
    raises 401 when the key is missing, unknown, revoked or expired.
    """
    return authenticate_request(request)


def require_scopes(*required: str):
    """
    Dependency factory: ``key=Depends(require_scopes("write"))``.
    401 as above; 403 when the key lacks any of the required scopes.
    """
    async def _dep(request: Request) -> dict:
        principal = authenticate_request(request)
        missing = [s for s in required if not scope_allows(principal["scopes"], s)]
        if missing:
            raise HTTPException(status_code=403,
                                detail=f"API key lacks required scope(s): {', '.join(missing)}")
        return principal
    return _dep
