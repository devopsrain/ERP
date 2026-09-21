"""
Language switcher + Ethiopian calendar helper endpoints.

    GET  /i18n/set/{lang}?next=/vat/dashboard   → remember language, redirect
    GET  /i18n/convert?g=2026-09-11             → {"ethiopian": {...}}
    GET  /i18n/convert?e=2019-01-01             → {"gregorian": "2026-09-11"}
    GET  /i18n/catalogue.json                   → current language catalogue (for JS)

``/i18n/`` is public (works on the login page) and read-only, so it is
also CSRF-exempt.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

import i18n
from ethiopian_calendar import parse_ethiopian, to_ethiopian

router = APIRouter(prefix="/i18n", tags=["i18n"])


def _safe_next(next_url: str | None, fallback: str = "/") -> str:
    """Only allow same-site relative redirects (no scheme/host)."""
    if not next_url:
        return fallback
    p = urlparse(next_url)
    if p.scheme or p.netloc or not next_url.startswith("/") or next_url.startswith("//"):
        return fallback
    return next_url


@router.get("/set/{lang}", name="i18n_set")
async def set_language(request: Request, lang: str, next: str | None = None):
    code = i18n.set_locale(request, lang)
    target = _safe_next(next or request.headers.get("referer", ""), "/")
    # Referer may be absolute (same host) — reduce it to a path.
    if target.startswith("http"):
        target = _safe_next(urlparse(target).path or "/", "/")
    resp = RedirectResponse(url=target, status_code=303)
    resp.set_cookie(i18n.COOKIE_NAME, code, max_age=365 * 24 * 3600,
                    httponly=False, samesite="lax", path="/")
    return resp


@router.get("/convert", name="i18n_convert")
async def convert(request: Request, g: str | None = None, e: str | None = None):
    out: dict = {}
    if g:
        eth = to_ethiopian(g)
        if eth is None:
            return JSONResponse({"error": "invalid gregorian date"}, status_code=400)
        out["ethiopian"] = {
            "year": eth.year, "month": eth.month, "day": eth.day,
            "iso": eth.iso(), "am": eth.format("am"), "en": eth.format("en"),
        }
    if e:
        gd = parse_ethiopian(e)
        if gd is None:
            return JSONResponse({"error": "invalid ethiopian date"}, status_code=400)
        out["gregorian"] = gd.isoformat()
    if not out:
        return JSONResponse({"error": "pass g=YYYY-MM-DD or e=YYYY-MM-DD"}, status_code=400)
    return JSONResponse(out)


@router.get("/catalogue.json", name="i18n_catalogue")
async def catalogue(request: Request):
    lang = i18n.get_locale(request)
    return JSONResponse({"lang": lang, "strings": i18n.CATALOGUE.get(lang, {})})
