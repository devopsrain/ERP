"""
Mobile-money payments routes — Telebirr / CBE Birr / M-Pesa / bank / cash.

Prefix /payments. Route names payments_* (templates use url_for('payments.x')).
Static paths are registered BEFORE /{payment_id}.
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse

from deps import current_company, flash, login_required, require_auth, template_context, validate_upload
from payment_providers import (
    ADAPTERS, DIRECTIONS, NOTIFYING_PROVIDERS, PROVIDER_LABELS, PROVIDERS, STATUSES,
    get_adapter, provider_choices,
)
from payments_data_store import MATCH_TYPES, payments_store
from template_engine import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])

entry_required = require_auth("data_entry")   # record / import / confirm a match
manager_required = require_auth("manager")    # accounts, settings, reverse, unmatch, batch jobs

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_UPLOAD_EXTS = (".xlsx", ".xls", ".csv")


@router.on_event("startup")
async def _startup():
    payments_store.ensure_schema()


def _actor(request: Request) -> str:
    return request.session.get("username", "") or ""


def _base_ctx(request: Request, active: str) -> dict:
    ctx = template_context(request)
    ctx.update(active_page=active, providers=provider_choices(), provider_labels=PROVIDER_LABELS,
               directions=DIRECTIONS, statuses=STATUSES, match_types=MATCH_TYPES)
    return ctx


def _provider_or_none(value):
    v = (value or "").strip().lower()
    return v if v in PROVIDERS else None


# ── Dashboard ────────────────────────────────────────────────────

@router.get("/", name="payments_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = _base_ctx(request, "dashboard")
    ctx.update(stats=payments_store.get_stats(cid),
               balances=payments_store.account_balances(cid),
               recent=payments_store.recent_payments(cid, 10),
               inbound_available=payments_store.inbound_log_available(),
               inbound_pending=payments_store.pending_inbound_count())
    return templates.TemplateResponse("payments/dashboard.html", ctx)


# ── List + export ────────────────────────────────────────────────

def _filters_from(request: Request) -> dict:
    q = request.query_params
    return {
        "provider": _provider_or_none(q.get("provider")),
        "direction": q.get("direction") if q.get("direction") in DIRECTIONS else None,
        "status": q.get("status") if q.get("status") in STATUSES else None,
        "reconciled": q.get("reconciled") if q.get("reconciled") in ("yes", "no") else None,
        "matched": q.get("matched") if q.get("matched") in ("yes", "no") else None,
        "account_id": q.get("account_id") or None,
        "date_from": q.get("date_from") or None,
        "date_to": q.get("date_to") or None,
        "q": (q.get("q") or "").strip() or None,
    }


@router.get("/list", name="payments_list")
async def payments_list(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    filters = _filters_from(request)
    rows = payments_store.list_payments(cid, filters)
    ctx = _base_ctx(request, "list")
    ctx.update(payments=rows, filters={k: (v or "") for k, v in filters.items()},
               accounts=payments_store.get_accounts(cid),
               total_in=sum((Decimal(p["amount"]) for p in rows if p["direction"] == "in" and p["status"] == "completed"), Decimal(0)),
               total_out=sum((Decimal(p["amount"]) for p in rows if p["direction"] == "out" and p["status"] == "completed"), Decimal(0)),
               query_string=str(request.url.query or ""))
    return templates.TemplateResponse("payments/list.html", ctx)


@router.get("/export", name="payments_export")
async def payments_export(request: Request, user=Depends(login_required)):
    import pandas as pd
    cid = current_company(request)
    rows = payments_store.list_payments(cid, _filters_from(request), limit=5000)
    cols = ["paid_at", "provider", "direction", "status", "amount", "fee", "currency", "payer_name",
            "payer_msisdn", "payee_name", "payee_msisdn", "provider_txn_id", "reference", "narration",
            "account_name", "source", "matched_type", "matched_id", "reconciled", "created_by", "id"]
    data = [{c: (float(r[c]) if isinstance(r.get(c), Decimal) else r.get(c)) for c in cols} for r in rows]
    df = pd.DataFrame(data, columns=cols)
    fd, path = tempfile.mkstemp(suffix=".xlsx"); os.close(fd)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Payments")
    return FileResponse(path, filename=f"payments_{cid}_{date.today().isoformat()}.xlsx", media_type=_XLSX)


# ── Manual record ────────────────────────────────────────────────

@router.get("/new", name="payments_new_get")
async def new_payment_get(request: Request, user=Depends(entry_required)):
    cid = current_company(request)
    ctx = _base_ctx(request, "new")
    q = request.query_params
    ctx.update(payment={"provider": _provider_or_none(q.get("provider")) or "telebirr",
                        "direction": q.get("direction") if q.get("direction") in DIRECTIONS else "in",
                        "matched_type": q.get("matched_type") or "", "matched_id": q.get("matched_id") or "",
                        "amount": q.get("amount") or "", "reference": q.get("reference") or ""},
               accounts=payments_store.get_accounts(cid, active_only=True),
               today=datetime.now().strftime("%Y-%m-%dT%H:%M"))
    return templates.TemplateResponse("payments/form.html", ctx)


@router.post("/new", name="payments_new_post")
async def new_payment_post(request: Request, user=Depends(entry_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["created_by"] = _actor(request)
    data["source"] = "manual"
    if data.get("matched_type") and not data.get("matched_id"):
        data["matched_type"] = ""
    rec = payments_store.record_payment(cid, **data)
    if not rec:
        flash(request, "Could not save the payment — check provider, direction and amount", "error")
        return RedirectResponse("/payments/new", status_code=303)
    if rec.get("_duplicate"):
        flash(request, f"A {PROVIDER_LABELS.get(rec['provider'], rec['provider'])} payment with transaction id "
                       f"{rec['provider_txn_id']} already exists — opened the existing record", "warning")
    else:
        flash(request, "Payment recorded", "success")
    return RedirectResponse(f"/payments/{rec['id']}", status_code=303)


# ── Reconcile ────────────────────────────────────────────────────

@router.get("/reconcile", name="payments_reconcile")
async def reconcile_page(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = _base_ctx(request, "reconcile")
    ctx.update(queue=payments_store.reconcile_queue(cid, 50),
               stats=payments_store.get_stats(cid),
               income_available=payments_store.table_exists("vat_income"),
               expense_available=payments_store.table_exists("vat_expenses"))
    return templates.TemplateResponse("payments/reconcile.html", ctx)


@router.post("/reconcile/auto", name="payments_auto_match")
async def reconcile_auto(request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    res = payments_store.auto_match(cid, by=_actor(request) or "auto-match")
    flash(request, f"Auto-match: {res['linked']} linked, {res['skipped']} left for review "
                   f"(of {res['scanned']} unmatched)", "success" if res["linked"] else "info")
    return RedirectResponse("/payments/reconcile", status_code=303)


# ── Statement import ─────────────────────────────────────────────

@router.get("/import", name="payments_import_get")
async def import_get(request: Request, user=Depends(entry_required)):
    cid = current_company(request)
    ctx = _base_ctx(request, "import")
    ctx.update(accounts=payments_store.get_accounts(cid, active_only=True),
               imports=payments_store.get_imports(cid),
               adapters=[(p, ADAPTERS[p]) for p in PROVIDERS],
               selected_provider=_provider_or_none(request.query_params.get("provider")) or "telebirr",
               report=None)
    return templates.TemplateResponse("payments/import_statement.html", ctx)


@router.get("/import/template", name="payments_import_template")
async def import_template(request: Request, user=Depends(login_required)):
    import pandas as pd
    provider = _provider_or_none(request.query_params.get("provider")) or "telebirr"
    adapter = get_adapter(provider)
    cols = list(adapter.STATEMENT_COLUMNS)
    sample = pd.DataFrame([{c: adapter.STATEMENT_SAMPLE.get(c, "") for c in cols}], columns=cols)
    notes = pd.DataFrame({"column": cols, "notes": [adapter.STATEMENT_NOTES.get(c, "") for c in cols]})
    fd, path = tempfile.mkstemp(suffix=".xlsx"); os.close(fd)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        sample.to_excel(writer, index=False, sheet_name="Statement")
        notes.to_excel(writer, index=False, sheet_name="Field Descriptions")
    return FileResponse(path, filename=f"{provider}_statement_template.xlsx", media_type=_XLSX)


@router.post("/import", name="payments_import_post")
async def import_post(request: Request, user=Depends(entry_required)):
    import pandas as pd
    cid = current_company(request)
    form = await request.form()
    provider = _provider_or_none(form.get("provider"))
    account_id = (form.get("account_id") or "").strip() or None
    upload = form.get("file")
    if not provider:
        flash(request, "Choose a provider for the statement", "error")
        return RedirectResponse("/payments/import", status_code=303)
    if upload is None or not getattr(upload, "filename", ""):
        flash(request, "Please choose a statement file (.xlsx, .xls or .csv)", "error")
        return RedirectResponse(f"/payments/import?provider={provider}", status_code=303)
    content = await upload.read()
    ok, err = validate_upload(upload.filename, content, allowed_exts=_UPLOAD_EXTS)
    if not ok:
        flash(request, err, "error")
        return RedirectResponse(f"/payments/import?provider={provider}", status_code=303)
    if account_id and not payments_store.get_account(account_id, cid):
        account_id = None
    try:
        if upload.filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content))
    except Exception as e:
        flash(request, f"Could not read the file: {e}", "error")
        return RedirectResponse(f"/payments/import?provider={provider}", status_code=303)
    rows = [{str(k): ("" if pd.isna(v) else v) for k, v in r.items()} for _, r in df.iterrows()]
    report = payments_store.import_statement(cid, provider, account_id, upload.filename, rows, _actor(request))
    if report["imported"]:
        flash(request, f"Imported {report['imported']} payment(s) from {upload.filename}", "success")
    if report["duplicates"]:
        flash(request, f"{report['duplicates']} row(s) skipped as duplicates (same transaction id)", "warning")
    if report["errors"]:
        shown = "; ".join(report["errors"][:5]) + (f" (+{len(report['errors']) - 5} more)" if len(report["errors"]) > 5 else "")
        flash(request, f"{len(report['errors'])} row(s) failed — {shown}", "warning")
    if not report["total"]:
        flash(request, "The file contained no data rows", "warning")
    try:
        from siem_data_store import siem_store
        siem_store.log_upload_event(
            request, module="payments", endpoint="/payments/import", filename=upload.filename,
            records_imported=report["imported"],
            status="success" if not report["errors"] else ("partial" if report["imported"] else "failed"),
            details=f"{provider} statement: {report['imported']} ok, {report['duplicates']} dup, {len(report['errors'])} errors")
    except Exception:
        pass
    ctx = _base_ctx(request, "import")
    ctx.update(accounts=payments_store.get_accounts(cid, active_only=True), imports=payments_store.get_imports(cid),
               adapters=[(p, ADAPTERS[p]) for p in PROVIDERS], selected_provider=provider, report=report)
    return templates.TemplateResponse("payments/import_statement.html", ctx)


# ── Accounts ─────────────────────────────────────────────────────

@router.get("/accounts", name="payments_accounts")
async def accounts_page(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = _base_ctx(request, "accounts")
    ctx.update(accounts=payments_store.account_balances(cid))
    return templates.TemplateResponse("payments/accounts.html", ctx)


@router.post("/accounts/new", name="payments_account_new")
async def account_new(request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    form = await request.form()
    acct = payments_store.create_account(cid, {k: v for k, v in form.items()})
    flash(request, "Account created" if acct else "Failed to create account", "success" if acct else "error")
    return RedirectResponse("/payments/accounts", status_code=303)


@router.post("/accounts/{account_id}/edit", name="payments_account_edit")
async def account_edit(account_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    form = await request.form()
    ok = payments_store.update_account(account_id, cid, {k: v for k, v in form.items()})
    flash(request, "Account updated" if ok else "Failed to update account", "success" if ok else "error")
    return RedirectResponse("/payments/accounts", status_code=303)


@router.post("/accounts/{account_id}/toggle", name="payments_account_toggle")
async def account_toggle(account_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    ok = payments_store.toggle_account(account_id, cid)
    flash(request, "Account status changed" if ok else "Account not found", "success" if ok else "error")
    return RedirectResponse("/payments/accounts", status_code=303)


# ── Provider settings ────────────────────────────────────────────

def _callback_base(request: Request) -> str:
    base = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("APP_BASE_URL") or ""
    if not base:
        try:
            proto = request.headers.get("x-forwarded-proto") or request.url.scheme
            host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
            base = f"{proto}://{host}"
        except Exception:
            base = ""
    return base.rstrip("/")


def _settings_ctx(request: Request, cid: str, test_result=None) -> dict:
    ctx = _base_ctx(request, "settings")
    base = _callback_base(request)
    rows = []
    for p in PROVIDERS:
        a = ADAPTERS[p]
        rows.append({
            "provider": p, "label": a.label, "adapter": a,
            "env": a.env_status(), "required": list(a.ENV_REQUIRED), "optional": list(a.ENV_OPTIONAL),
            "configured": a.is_configured(), "missing": a.missing_env(),
            "notifies": p in NOTIFYING_PROVIDERS,
            "callback_url": f"{base}/webhooks/inbound/{p}" if p in NOTIFYING_PROVIDERS else "",
            "settings": payments_store.get_settings(cid, p),
        })
    ctx.update(provider_rows=rows, callback_base=base, test_result=test_result,
               inbound_available=payments_store.inbound_log_available(),
               inbound_outcomes=payments_store.recent_inbound_outcomes(15))
    return ctx


@router.get("/settings", name="payments_settings")
async def settings_page(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    return templates.TemplateResponse("payments/settings.html", _settings_ctx(request, cid))


@router.post("/settings/{provider}", name="payments_settings_save")
async def settings_save(provider: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    p = _provider_or_none(provider)
    if not p:
        flash(request, "Unknown provider", "error")
        return RedirectResponse("/payments/settings", status_code=303)
    form = await request.form()
    settings = {
        "short_code": (form.get("short_code") or "").strip(),
        "merchant_name": (form.get("merchant_name") or "").strip(),
        "callback_secret_ref": (form.get("callback_secret_ref") or "").strip(),
        "require_signature": "1" if form.get("require_signature") else "0",
        "enabled": "1" if form.get("enabled") else "0",
        "notes": (form.get("notes") or "").strip(),
    }
    ok = payments_store.save_settings(cid, p, settings, _actor(request))
    flash(request, f"{PROVIDER_LABELS[p]} settings saved" if ok else "Failed to save settings",
          "success" if ok else "error")
    return RedirectResponse("/payments/settings", status_code=303)


@router.post("/settings/{provider}/test", name="payments_settings_test")
async def settings_test(provider: str, request: Request, user=Depends(manager_required)):
    """Dry-run initiate(): shows whether credentials are detected and the request we would build."""
    cid = current_company(request)
    p = _provider_or_none(provider)
    if not p:
        flash(request, "Unknown provider", "error")
        return RedirectResponse("/payments/settings", status_code=303)
    form = await request.form()
    result = get_adapter(p).initiate(form.get("amount") or "1", form.get("msisdn") or "", form.get("reference") or "TEST")
    return templates.TemplateResponse("payments/settings.html",
                                      _settings_ctx(request, cid, test_result={"provider": p, "result": result}))


# ── Inbound notifications ────────────────────────────────────────

@router.post("/inbound/process", name="payments_process_inbound")
async def process_inbound(request: Request, user=Depends(manager_required)):
    summary = payments_store.process_inbound_notifications()
    cat = "success" if summary["created"] else ("warning" if summary["errors"] or summary["rejected"] else "info")
    flash(request, "; ".join(summary["messages"]), cat)
    try:
        nxt = (await request.form()).get("next")
    except Exception:
        nxt = None
    return RedirectResponse(nxt if nxt and str(nxt).startswith("/payments") else "/payments/", status_code=303)


# ── Detail + actions (parametric — keep LAST) ────────────────────

@router.get("/{payment_id}", name="payments_detail")
async def payment_detail(payment_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    p = payments_store.get_payment(payment_id, cid)
    if not p:
        flash(request, "Payment not found", "error")
        return RedirectResponse("/payments/list", status_code=303)
    ctx = _base_ctx(request, "list")
    raw = p.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            pass
    ctx.update(payment=p,
               raw_json=json.dumps(raw, indent=2, default=str, ensure_ascii=False) if raw not in (None, "") else "",
               candidates=payments_store.find_matches(cid, p) if not p.get("matched_id") and p.get("status") == "completed" else [],
               matched=payments_store.matched_record(cid, p["matched_type"], p["matched_id"]) if p.get("matched_id") else None)
    return templates.TemplateResponse("payments/detail.html", ctx)


@router.post("/{payment_id}/match", name="payments_match")
async def payment_match(payment_id: str, request: Request, user=Depends(entry_required)):
    cid = current_company(request)
    form = await request.form()
    mtype = (form.get("matched_type") or "").strip()
    mid = (form.get("matched_id") or "").strip()
    nxt = form.get("next") or f"/payments/{payment_id}"
    if mtype not in MATCH_TYPES or not mid:
        flash(request, "Choose a record type and id to link", "error")
    elif payments_store.link_payment(payment_id, mtype, mid, _actor(request), cid):
        flash(request, f"Payment linked to {mtype} {mid}", "success")
    else:
        flash(request, "Failed to link payment", "error")
    return RedirectResponse(nxt if str(nxt).startswith("/payments") else f"/payments/{payment_id}", status_code=303)


@router.post("/{payment_id}/unmatch", name="payments_unmatch")
async def payment_unmatch(payment_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    ok = payments_store.unlink_payment(payment_id, cid)
    flash(request, "Match removed" if ok else "Failed to remove match", "success" if ok else "error")
    return RedirectResponse(f"/payments/{payment_id}", status_code=303)


@router.post("/{payment_id}/reconcile", name="payments_reconcile_toggle")
async def payment_reconcile_toggle(payment_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    form = await request.form()
    flag = (form.get("reconciled") or "1") in ("1", "true", "on", "yes")
    ok = payments_store.set_reconciled(payment_id, cid, flag, _actor(request))
    flash(request, ("Marked reconciled" if flag else "Marked unreconciled") if ok else "Update failed",
          "success" if ok else "error")
    return RedirectResponse(f"/payments/{payment_id}", status_code=303)


@router.post("/{payment_id}/reverse", name="payments_reverse")
async def payment_reverse(payment_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    ok = payments_store.reverse_payment(payment_id, cid, _actor(request))
    flash(request, "Payment marked reversed" if ok else "Payment could not be reversed", "success" if ok else "error")
    return RedirectResponse(f"/payments/{payment_id}", status_code=303)


@router.post("/{payment_id}/status", name="payments_status")
async def payment_status(payment_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    form = await request.form()
    status = (form.get("status") or "").strip()
    if status not in STATUSES:
        flash(request, "Invalid status", "error")
    elif payments_store.set_status(payment_id, cid, status, _actor(request)):
        flash(request, f"Status set to {status}", "success")
    else:
        flash(request, "Failed to update status", "error")
    return RedirectResponse(f"/payments/{payment_id}", status_code=303)
