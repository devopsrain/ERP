"""
Wiring smoke-tests for the 2.2.0 module batch.

No database needed: the app object is built, routers must be mounted under
their prefixes, route names must be unique, every new store must expose a
module-level ``ensure_schema`` and every jobs module a ``register_jobs`` that
accepts a scheduler and is side-effect free at import.
"""
import importlib
import os
import sys

import pytest

_WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_WEB, os.path.dirname(_WEB)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-for-pytest")

NEW_ROUTERS = {
    "approval_routes": "/approvals",
    "fixed_assets_routes": "/assets",
    "webhook_routes": "/webhooks",
    "payments_routes": "/payments",
    "erca_routes": "/erca",
    "reports_routes": "/reports",
    "portal_routes": "/portal",
    "telegram_routes": "/telegram",
    "documents_routes": "/documents",
    "i18n_routes": "/i18n",
}
NEW_STORES = [
    "approval_data_store", "fixed_assets_data_store", "webhook_data_store",
    "payments_data_store", "erca_data_store", "reports_data_store",
    "portal_data_store", "telegram_data_store", "documents_data_store", "i18n",
]
NEW_JOBS = [
    "reports_jobs", "webhook_jobs", "fixed_assets_jobs",
    "telegram_jobs", "payments_jobs", "approval_jobs",
]


@pytest.mark.parametrize("mod,prefix", NEW_ROUTERS.items())
def test_router_module_exposes_router_with_prefix(mod, prefix):
    m = importlib.import_module(mod)
    assert hasattr(m, "router"), f"{mod} has no `router`"
    paths = [getattr(r, "path", "") for r in m.router.routes]
    assert paths, f"{mod} router has no routes"
    assert all(p.startswith(prefix) for p in paths), f"{mod}: routes outside {prefix}: {paths}"


@pytest.mark.parametrize("mod", NEW_STORES)
def test_store_exposes_ensure_schema(mod):
    m = importlib.import_module(mod)
    assert callable(getattr(m, "ensure_schema", None)), f"{mod}.ensure_schema missing"


@pytest.mark.parametrize("mod", NEW_JOBS)
def test_jobs_register_without_side_effects(mod):
    m = importlib.import_module(mod)
    fn = getattr(m, "register_jobs", None)
    assert callable(fn), f"{mod}.register_jobs missing"

    class _FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, func, trigger=None, *args, **kwargs):
            self.jobs.append((func, trigger, kwargs.get("id")))

    sched = _FakeScheduler()
    fn(sched)
    assert sched.jobs, f"{mod}.register_jobs registered nothing"
    assert all(callable(j[0]) for j in sched.jobs)


def test_app_mounts_every_new_router_and_names_are_unique():
    from app import app  # noqa: WPS433

    paths = {getattr(r, "path", "") for r in app.routes}
    for mod, prefix in NEW_ROUTERS.items():
        assert any(p.startswith(prefix + "/") or p == prefix for p in paths), \
            f"{mod}: nothing mounted under {prefix}"

    names = [getattr(r, "name", None) for r in app.routes if getattr(r, "name", None)]
    new_names = [n for n in names if n.split("_")[0] in {
        "approval", "fixed", "webhook", "payments", "erca", "reports",
        "portal", "telegram", "documents", "i18n"}]
    dupes = {n for n in new_names if new_names.count(n) > 1}
    assert not dupes, f"duplicate route names in new modules: {sorted(dupes)}"


def test_public_and_csrf_exemptions_cover_external_endpoints():
    import inspect
    import app as app_module

    src = inspect.getsource(app_module)
    for needle in ('"/portal/"', '"/telegram/webhook"', '"/webhooks/inbound/"', '"/i18n/"'):
        assert src.count(needle) >= 2, f"{needle} must be in both _PUBLIC and _CSRF_SKIP"


def test_telegram_approval_bridge_matches_store_signature():
    """telegram_bot calls approval_store.decide by keyword aliases; make sure
    the real store accepts the names it will try."""
    import inspect
    import approval_data_store as ads

    sig = inspect.signature(ads.approval_store.decide)
    params = set(sig.parameters)
    assert "request_id" in params and "actor" in params and "action" in params
    assert {"company_id", "entity_type", "entity_id", "title", "amount", "requested_by"} <= \
        set(inspect.signature(ads.approval_store.submit).parameters)
    assert set(inspect.signature(ads.approval_store.pending_for).parameters) >= \
        {"company_id", "username", "roles"}
