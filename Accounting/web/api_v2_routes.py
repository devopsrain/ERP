"""
REST API v2 — Mobile / SPA optimised endpoints.

New in v2 vs v1:
  - JWT-based authentication (access + refresh token pair)
  - Refresh token rotation (30-day sliding window)
  - Consistent pagination envelope for all list endpoints:
        { "data": [...], "total": N, "page": 1, "per_page": 50, "has_more": bool }
  - multipart/form-data receipt image upload with optional OCR pre-fill
  - Presigned S3 URLs for large file downloads (DOCX, XLSX)

Auth flow for mobile clients:
  1.  POST /api/v2/auth/token   { username, password }
      → { access_token (JWT, 15 min), refresh_token (opaque, 30 days), expires_in }

  2.  Attach to every request:
      Authorization: Bearer <access_token>

  3.  When access token expires:
      POST /api/v2/auth/refresh  { refresh_token }
      → new { access_token, refresh_token, expires_in }

  4.  On logout:
      POST /api/v2/auth/revoke  { refresh_token }
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from deps import current_company

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["API v2 (mobile)"])

# ── Helpers ────────────────────────────────────────────────────────────────────

def _ok(data, **meta) -> dict:
    return {"status": "ok", "data": data, **meta}


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"status": "error", "error": msg}, status_code=code)


def _paginate(items: list, page: int, per_page: int) -> dict:
    """Return a consistent mobile pagination envelope."""
    total  = len(items)
    offset = (page - 1) * per_page
    chunk  = items[offset : offset + per_page]
    return {
        "data":     chunk,
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "has_more": (offset + per_page) < total,
    }


def _company(request: Request) -> str:
    return getattr(request.state, "company_id", None) or current_company(request)


# ── JWT dependency ─────────────────────────────────────────────────────────────

async def jwt_required(request: Request) -> dict:
    """
    FastAPI dependency.  Validates the JWT Bearer token and returns the payload.
    The `bearer_auth_middleware` in api_app.py handles opaque tokens for v1;
    v2 endpoints call this dependency directly so they can distinguish JWT vs opaque.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise _http401("Missing Authorization: Bearer <token> header")

    token = auth[7:].strip()

    # Try JWT first (v2 native)
    try:
        from auth_data_store import auth_store
        payload = auth_store.validate_jwt(token)
        if payload:
            request.state.company_id = payload.get("company_id", "default")
            return payload
    except Exception:
        pass

    # Fall back to opaque token for interoperability with v1 clients
    try:
        from auth_data_store import auth_store
        user = auth_store.validate_api_token(token)
        if user:
            request.state.company_id = user.get("company_id", "default")
            return user
    except Exception:
        pass

    raise _http401("Token invalid or expired")


def _http401(detail: str):
    from fastapi import HTTPException
    return HTTPException(status_code=401, detail=detail)


# ── Auth endpoints ─────────────────────────────────────────────────────────────

@router.post("/auth/token", name="v2_auth_token", include_in_schema=True)
async def issue_token(
    request: Request,
    username: str = Form(..., description="Username or e-mail address"),
    password: str = Form(..., description="Account password"),
    device_hint: str = Form("", description="Human-readable device description (optional)"),
):
    """
    Exchange credentials for a JWT access + refresh token pair.

    Returns:
        200 { access_token, refresh_token, token_type, expires_in }
        401 when credentials are invalid / account locked
    """
    if not username or not password:
        return _err("username and password are required", 400)

    try:
        from auth_data_store import auth_store
        user = auth_store.authenticate(username, password, request)
    except Exception as e:
        logger.error("v2_auth_token authenticate error: %s", e)
        return _err("Authentication service unavailable", 503)

    if not user:
        # Avoid disclosing whether username or password was wrong
        return JSONResponse(
            {"status": "error", "error": "Invalid credentials or account locked"},
            status_code=401,
        )

    try:
        pair = auth_store.issue_jwt_pair(user, device_hint=device_hint)
    except Exception as e:
        logger.error("v2_auth_token issue_jwt_pair error: %s", e)
        return _err("Could not issue tokens", 500)

    return _ok(pair)


@router.post("/auth/refresh", name="v2_auth_refresh", include_in_schema=True)
async def refresh_token(
    request: Request,
    refresh_token: str = Form(..., description="Refresh token received from /auth/token"),
):
    """
    Exchange a refresh token for a new access + refresh pair (token rotation).

    The submitted refresh token is immediately revoked on success.

    Returns:
        200 { access_token, refresh_token, token_type, expires_in }
        401 when the refresh token is invalid, expired, or already used
    """
    if not refresh_token:
        return _err("refresh_token is required", 400)

    try:
        from auth_data_store import auth_store
        pair = auth_store.refresh_access_token(refresh_token)
    except Exception as e:
        logger.error("v2_auth_refresh error: %s", e)
        return _err("Token refresh service unavailable", 503)

    if not pair:
        return JSONResponse(
            {"status": "error", "error": "Refresh token invalid, expired, or already used"},
            status_code=401,
        )
    return _ok(pair)


@router.post("/auth/revoke", name="v2_auth_revoke", include_in_schema=True)
async def revoke_token(
    request: Request,
    refresh_token: str = Form(..., description="Refresh token to invalidate"),
    user=Depends(jwt_required),
):
    """
    Revoke a refresh token (logout / device sign-out).

    Requires a valid access token in Authorization header so we can
    bind the revocation to the authenticated user (prevents cross-user attacks).
    """
    user_id = user.get("sub") or user.get("user_id", "")
    try:
        from auth_data_store import auth_store
        auth_store.revoke_refresh_token(refresh_token, user_id)
    except Exception as e:
        logger.error("v2_auth_revoke error: %s", e)
    return _ok({"revoked": True})


# ── Paginated list mirrors of v1 endpoints ─────────────────────────────────────

@router.get("/accounts", name="v2_list_accounts")
async def list_accounts(
    request: Request,
    account_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    user=Depends(jwt_required),
):
    """Paginated chart-of-accounts list."""
    try:
        from chart_of_accounts_data_store import chart_store
        df = chart_store.read_all_accounts(company_id=_company(request))
        items = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
        if account_type:
            items = [a for a in items if str(a.get("account_type", "")).upper() == account_type.upper()]
        return _ok(**_paginate(items, page, per_page))
    except Exception as e:
        logger.error("v2 list_accounts: %s", e)
        return _err(str(e), 500)


@router.get("/transactions", name="v2_list_transactions")
async def list_transactions(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    flagged: bool = Query(False),
    user=Depends(jwt_required),
):
    """Paginated transaction list."""
    try:
        from transaction_data_store import transaction_store
        raw = transaction_store.get_transactions(company_id=_company(request))
        items = raw.to_dict(orient="records") if hasattr(raw, "to_dict") else list(raw)
        if flagged:
            items = [t for t in items if t.get("is_flagged")]
        return _ok(**_paginate(items, page, per_page))
    except Exception as e:
        logger.error("v2 list_transactions: %s", e)
        return _err(str(e), 500)


@router.get("/journal", name="v2_list_journal")
async def list_journal(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    user=Depends(jwt_required),
):
    """Paginated journal entries."""
    try:
        from journal_entry_data_store import journal_store
        df = journal_store.read_journal_entries(company_id=_company(request))
        items = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
        return _ok(**_paginate(items, page, per_page))
    except Exception as e:
        logger.error("v2 list_journal: %s", e)
        return _err(str(e), 500)


@router.get("/payroll/employees", name="v2_list_employees")
async def list_employees(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user=Depends(jwt_required),
):
    """Paginated employee list."""
    try:
        from services.payroll_service import payroll_service
        items = payroll_service.list_employees(_company(request))
        return _ok(**_paginate(items, page, per_page))
    except Exception as e:
        logger.error("v2 list_employees: %s", e)
        return _err(str(e), 500)


@router.get("/inventory/items", name="v2_list_inventory")
async def list_inventory(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    low_stock: bool = Query(False),
    user=Depends(jwt_required),
):
    """Paginated inventory list."""
    try:
        from inventory_data_store import inventory_store
        raw = inventory_store.get_all_items(company_id=_company(request))
        items = raw.to_dict(orient="records") if hasattr(raw, "to_dict") else list(raw or [])
        if low_stock:
            items = [i for i in items if (i.get("quantity") or 0) <= (i.get("reorder_level") or 0)]
        return _ok(**_paginate(items, page, per_page))
    except Exception as e:
        logger.error("v2 list_inventory: %s", e)
        return _err(str(e), 500)


# ── Receipt upload ─────────────────────────────────────────────────────────────

@router.post("/vat/receipts", name="v2_upload_receipt")
async def upload_receipt(
    request: Request,
    file: UploadFile = File(..., description="Receipt image (JPEG/PNG/PDF, max 5 MB)"),
    tin: str = Form("", description="Supplier TIN (pre-filled if known)"),
    user=Depends(jwt_required),
):
    """
    Upload a receipt image and receive a pre-filled VAT income/expense draft.

    The image is stored in S3 (or local fallback) and a presigned URL is
    returned alongside any values extracted from the filename / metadata.
    Full OCR is a pluggable extension point: install pytesseract + Pillow
    and set ENABLE_OCR=1 to activate.

    Accepted content types: image/jpeg, image/png, application/pdf
    Max size: 5 MB

    Returns:
        {
          "receipt_url":   "https://…",   // S3 presigned URL or /static/… path
          "draft": {                       // pre-filled VAT form fields
            "tin":          "...",
            "description":  "...",
            "amount":       null,          // null until OCR fills it
            "vat_amount":   null,
            "date":         "2026-02-18",
          }
        }
    """
    _ALLOWED_TYPES = {"image/jpeg", "image/png", "application/pdf"}
    _MAX_BYTES = 5 * 1024 * 1024  # 5 MB

    if file.content_type not in _ALLOWED_TYPES:
        return _err(
            f"Unsupported file type '{file.content_type}'. "
            "Allowed: image/jpeg, image/png, application/pdf",
            415,
        )

    content = await file.read()
    if len(content) > _MAX_BYTES:
        return _err("File exceeds the 5 MB limit", 413)

    company_id  = _company(request)
    upload_path = None
    receipt_url = None

    # ── S3 upload (preferred) ──────────────────────────────────────
    bucket = os.environ.get("S3_RECEIPTS_BUCKET", "")
    if bucket:
        try:
            import boto3
            from datetime import datetime as _dt
            import mimetypes

            ext = mimetypes.guess_extension(file.content_type or "") or ".bin"
            ext = ext.replace(".jpe", ".jpg")
            key = f"receipts/{company_id}/{_dt.utcnow().strftime('%Y/%m/%d')}/{file.filename or 'receipt'}{ext}"

            s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType=file.content_type,
                ServerSideEncryption="aws:kms",
            )
            receipt_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=3600,
            )
            upload_path = key
        except Exception as e:
            logger.warning("S3 receipt upload failed, falling back to local: %s", e)

    # ── Local fallback ─────────────────────────────────────────────
    if not receipt_url:
        import pathlib
        import secrets as _sec
        uploads_dir = pathlib.Path(__file__).parent / "static" / "uploads" / "receipts"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _sec.token_hex(8) + "_" + pathlib.Path(file.filename or "receipt").name
        # Strip any path traversal attempts
        safe_name = pathlib.Path(safe_name).name
        dest = uploads_dir / safe_name
        dest.write_bytes(content)
        receipt_url = f"/static/uploads/receipts/{safe_name}"
        upload_path = str(dest)

    # ── Optional OCR ──────────────────────────────────────────────
    extracted: dict = {"amount": None, "vat_amount": None, "description": None}
    if os.environ.get("ENABLE_OCR", "0") == "1":
        try:
            import io
            from PIL import Image
            import pytesseract

            if file.content_type in ("image/jpeg", "image/png"):
                img   = Image.open(io.BytesIO(content))
                text  = pytesseract.image_to_string(img)
                import re
                # Naïve amount extraction — matches ETB amounts like "1,234.56" or "1234.56"
                amounts = re.findall(r"\b(\d[\d,]*\.\d{2})\b", text)
                if amounts:
                    extracted["amount"] = float(amounts[0].replace(",", ""))
                    extracted["vat_amount"] = round(extracted["amount"] * 0.15, 2)
                if not extracted["description"]:
                    # Use first non-empty line as description
                    for line in text.splitlines():
                        clean = line.strip()
                        if len(clean) > 3:
                            extracted["description"] = clean[:120]
                            break
        except ImportError:
            pass
        except Exception as e:
            logger.warning("OCR failed: %s", e)

    draft = {
        "tin":         tin or None,
        "description": extracted["description"],
        "amount":      extracted["amount"],
        "vat_amount":  extracted["vat_amount"],
        "date":        datetime.utcnow().strftime("%Y-%m-%d"),
    }

    return _ok({"receipt_url": receipt_url, "draft": draft})


# ── Presigned S3 URL for large file downloads ──────────────────────────────────

@router.get("/files/presigned", name="v2_presigned_url")
async def presigned_url(
    request: Request,
    key: str = Query(..., description="S3 object key (relative path within bucket)"),
    expires: int = Query(3600, ge=60, le=86400, description="URL lifetime in seconds"),
    user=Depends(jwt_required),
):
    """
    Generate a short-lived presigned S3 GET URL for a stored file.

    Used by mobile clients to download DOCX payslips, XLSX exports, and
    backup archives without proxying the payload through the API server.

    The `key` must start with an allowed prefix to prevent SSRF-style
    access to arbitrary S3 objects.

    Allowed prefixes:
        exports/    — data exports
        payslips/   — generated payslip documents
        reports/    — generated reports
        receipts/   — uploaded receipt images
        backups/    — backup archives
    """
    _ALLOWED_PREFIXES = ("exports/", "payslips/", "reports/", "receipts/", "backups/")

    # Sanitise key — reject path traversal and disallowed prefixes
    if ".." in key or not any(key.startswith(p) for p in _ALLOWED_PREFIXES):
        return _err(
            f"Key must start with one of: {', '.join(_ALLOWED_PREFIXES)}",
            403,
        )

    bucket = os.environ.get("S3_RECEIPTS_BUCKET", "") or os.environ.get("S3_BUCKET", "")
    if not bucket:
        return _err("File storage not configured (S3_RECEIPTS_BUCKET not set)", 503)

    try:
        import boto3
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )
        return _ok({"url": url, "expires_in": expires})
    except Exception as e:
        logger.error("presigned_url failed: %s", e)
        return _err("Could not generate presigned URL", 500)
