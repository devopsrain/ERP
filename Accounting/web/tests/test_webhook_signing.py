"""
Pure unit tests — webhooks & API keys. No database, no network.

Covers: outbound HMAC signature compute/verify, inbound signature formats,
API-key generation/hash/verify + scope matching + the dependency's 401/403
paths, backoff schedule, wildcard event matching, SSRF guard, job
registration and a full send_delivery() round-trip with a stubbed transport.
"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import api_keys as ak  # noqa: E402
import webhook_data_store as wds  # noqa: E402
import webhook_jobs  # noqa: E402


# ── Outbound signature ────────────────────────────────────────────────────────

SECRET = "whsec_test_secret"
BODY = '{"data":{"invoice_id":"INV-1"},"event":"invoice.created","id":"d1"}'


def test_signature_header_format_and_roundtrip():
    header = wds.compute_signature(SECRET, BODY, timestamp=1_700_000_000)
    assert header.startswith("t=1700000000,v1=")
    t, sigs = wds.parse_signature_header(header)
    assert t == 1_700_000_000 and len(sigs) == 1 and len(sigs[0]) == 64
    assert wds.verify_signature(SECRET, BODY, header, now=1_700_000_010)


def test_signature_is_deterministic_for_same_inputs():
    assert wds.compute_signature(SECRET, BODY, 1) == wds.compute_signature(SECRET, BODY, 1)
    assert wds.compute_signature(SECRET, BODY, 1) != wds.compute_signature(SECRET, BODY, 2)


@pytest.mark.parametrize("tamper", [
    lambda h, b: (h, b + " "),                          # body changed
    lambda h, b: (h.replace("v1=", "v1=0"), b),         # sig changed
    lambda h, b: (h.replace("t=1700000000", "t=1700000001"), b),  # ts changed
    lambda h, b: ("garbage", b),
    lambda h, b: ("t=abc,v1=00", b),
    lambda h, b: ("", b),
])
def test_signature_rejects_tampering(tamper):
    header = wds.compute_signature(SECRET, BODY, timestamp=1_700_000_000)
    h, b = tamper(header, BODY)
    assert not wds.verify_signature(SECRET, b, h, now=1_700_000_000)


def test_signature_rejects_wrong_secret_and_stale_timestamp():
    header = wds.compute_signature(SECRET, BODY, timestamp=1_700_000_000)
    assert not wds.verify_signature("other", BODY, header, now=1_700_000_000)
    assert not wds.verify_signature(SECRET, BODY, header, now=1_700_000_000 + 301)
    assert wds.verify_signature(SECRET, BODY, header, now=1_700_000_000 + 299)
    # tolerance can be disabled for offline re-verification
    assert wds.verify_signature(SECRET, BODY, header, tolerance=None, now=1_800_000_000)


def test_signature_accepts_multiple_v1_values_after_rotation():
    old = wds.compute_signature("old", BODY, 5).split("v1=")[1]
    new = wds.compute_signature("new", BODY, 5).split("v1=")[1]
    header = f"t=5,v1={old},v1={new}"
    assert wds.verify_signature("new", BODY, header, now=5)
    assert wds.verify_signature("old", BODY, header, now=5)


def test_inbound_signature_accepts_three_formats():
    import hashlib
    import hmac as _hmac
    raw = _hmac.new(SECRET.encode(), BODY.encode(), hashlib.sha256).hexdigest()
    assert wds.verify_inbound_signature(SECRET, BODY, raw)
    assert wds.verify_inbound_signature(SECRET, BODY, raw.upper())
    assert wds.verify_inbound_signature(SECRET, BODY, f"sha256={raw}")
    assert wds.verify_inbound_signature(SECRET, BODY, wds.compute_signature(SECRET, BODY))
    assert not wds.verify_inbound_signature(SECRET, BODY, "sha256=" + "0" * 64)
    assert not wds.verify_inbound_signature("", BODY, raw)
    assert not wds.verify_inbound_signature(SECRET, BODY, "")


def test_canonical_body_is_stable_and_compact():
    a = wds.canonical_body({"b": 1, "a": {"y": 2, "x": 1}})
    b = wds.canonical_body({"a": {"x": 1, "y": 2}, "b": 1})
    assert a == b == '{"a":{"x":1,"y":2},"b":1}'


# ── API keys ──────────────────────────────────────────────────────────────────

def test_api_key_generate_hash_verify():
    key = ak.generate_api_key()
    assert key.startswith("ebms_") and len(key) > 40
    assert ak.looks_like_api_key(key)
    salt = ak.generate_salt()
    h = ak.hash_api_key(key, salt)
    assert len(h) == 64 and h != key
    assert ak.verify_api_key(key, salt, h)
    assert not ak.verify_api_key(key + "x", salt, h)
    assert not ak.verify_api_key(key, ak.generate_salt(), h)       # salt matters
    assert not ak.verify_api_key("", salt, h)
    assert ak.hash_api_key(key, "s1") != ak.hash_api_key(key, "s2")  # same key, different salt


def test_api_key_prefix_is_short_and_not_secret():
    key = ak.generate_api_key()
    prefix = ak.key_display_prefix(key)
    assert key.startswith(prefix) and len(prefix) == ak.DISPLAY_PREFIX_LEN < len(key) / 2


def test_two_generated_keys_differ():
    assert ak.generate_api_key() != ak.generate_api_key()


@pytest.mark.parametrize("granted,required,expected", [
    (["*"], "anything:write", True),
    (["read"], "read", True),
    (["read"], "write", False),
    (["invoices:*"], "invoices:read", True),
    (["invoices:*"], "payments:read", False),
    (["READ"], "read", True),
    ([], "read", False),
    (["read"], "", True),
])
def test_scope_matching(granted, required, expected):
    assert ak.scope_allows(granted, required) is expected


def test_normalise_scopes():
    assert ak.normalise_scopes("read, write  invoices:*") == ["read", "write", "invoices:*"]
    assert ak.normalise_scopes(["Read", "read", None, "write,admin"]) == ["read", "write", "admin"]
    assert ak.normalise_scopes(None) == []


def _req(headers=None):
    return SimpleNamespace(headers=headers or {}, state=SimpleNamespace())


def test_extract_key_from_headers():
    assert ak.extract_key_from_request(_req({"X-API-Key": "ebms_abcdefghijklmnop"})) == "ebms_abcdefghijklmnop"
    assert ak.extract_key_from_request(_req({"Authorization": "Bearer ebms_abcdefghijklmnop"})) == "ebms_abcdefghijklmnop"
    # legacy opaque tokens / JWTs are NOT picked up — they belong to the old auth path
    assert ak.extract_key_from_request(_req({"Authorization": "Bearer abc.def"})) is None
    assert ak.extract_key_from_request(_req({})) is None


def test_dependency_401_and_403_paths(monkeypatch):
    good = {"id": "k1", "company_id": "acme", "scopes": ["read"], "name": "n",
            "revoked_at": None, "expires_at": None}
    rows = {"ebms_valid_key_0001": good,
            "ebms_revoked_key_01": {**good, "id": "k2", "revoked_at": "2026-01-01"}}
    monkeypatch.setattr(ak, "resolve_api_key", lambda pt: rows.get(pt))

    # missing
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ak.require_api_key(_req({})))
    assert ei.value.status_code == 401
    # unknown
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ak.require_api_key(_req({"X-API-Key": "ebms_nope_nope_nope"})))
    assert ei.value.status_code == 401
    # revoked
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ak.require_api_key(_req({"X-API-Key": "ebms_revoked_key_01"})))
    assert ei.value.status_code == 401
    # valid
    r = _req({"Authorization": "Bearer ebms_valid_key_0001"})
    principal = asyncio.run(ak.require_api_key(r))
    assert principal == {"company_id": "acme", "key_id": "k1", "scopes": ["read"], "name": "n"}
    assert r.state.company_id == "acme" and r.state.api_key is principal
    # scope enforcement → 403
    assert asyncio.run(ak.require_scopes("read")(r))["key_id"] == "k1"
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ak.require_scopes("write")(r))
    assert ei.value.status_code == 403


# ── Backoff ───────────────────────────────────────────────────────────────────

def test_backoff_schedule_is_exponential_and_capped():
    delays = [wds.backoff_seconds(n) for n in range(1, 7)]
    assert delays[:4] == [60, 240, 960, 3840]
    assert all(b > a for a, b in zip(delays, delays[1:-1]))
    assert max(delays) <= 24 * 3600
    assert wds.backoff_seconds(50) == 24 * 3600
    assert wds.backoff_seconds(0) == 60


def test_next_state_after_failure_goes_dead_at_max_attempts():
    assert wds.MAX_ATTEMPTS == 6
    for n in range(1, wds.MAX_ATTEMPTS):
        status, delay = wds.next_state_after_failure(n)
        assert status == "failed" and delay == wds.backoff_seconds(n)
    assert wds.next_state_after_failure(wds.MAX_ATTEMPTS) == ("dead", None)
    assert wds.next_state_after_failure(wds.MAX_ATTEMPTS + 3) == ("dead", None)


# ── Event matching ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pattern,event,expected", [
    ("invoice.created", "invoice.created", True),
    ("invoice.created", "invoice.paid", False),
    ("invoice.*", "invoice.created", True),
    ("invoice.*", "invoice.paid", True),
    ("invoice.*", "payment.received", False),
    ("invoice.*", "invoices.created", False),
    ("invoice.*", "invoice", False),
    ("payment.*", "payment.reconciled", True),
    ("*", "anything.at.all", True),
    ("*.created", "employee.created", True),
    ("*.created", "employee.updated", False),
    ("INVOICE.*", "invoice.created", True),
    ("", "invoice.created", False),
    ("invoice.*", "", False),
])
def test_event_matches(pattern, event, expected):
    assert wds.event_matches(pattern, event) is expected


def test_endpoint_subscribed_accepts_list_or_json_string():
    assert wds.endpoint_subscribed(["payment.*", "bid.won"], "payment.received")
    assert wds.endpoint_subscribed('["invoice.*"]', "invoice.paid")
    assert not wds.endpoint_subscribed([], "invoice.paid")
    assert not wds.endpoint_subscribed(None, "invoice.paid")
    assert wds.endpoint_subscribed("invoice.*, bid.won", "bid.won")


def test_dedupe_key_prefers_event_id_else_buckets_payload():
    assert wds._dedupe_key("a.b", {"event_id": "E1", "x": 1}) == "a.b:E1"
    k1 = wds._dedupe_key("a.b", {"x": 1})
    k2 = wds._dedupe_key("a.b", {"x": 1})
    k3 = wds._dedupe_key("a.b", {"x": 2})
    assert k1 == k2 != k3 and len(k1) == 64


# ── SSRF guard ────────────────────────────────────────────────────────────────

def _resolver(mapping):
    def _r(host):
        if host not in mapping:
            raise OSError("nxdomain")
        return mapping[host]
    return _r


def test_url_guard(monkeypatch):
    monkeypatch.delenv("WEBHOOKS_ALLOW_PRIVATE", raising=False)
    res = _resolver({"public.example": ["93.184.216.34"], "internal.example": ["10.0.0.5"],
                     "dual.example": ["93.184.216.34", "192.168.1.1"], "v6.example": ["::ffff:127.0.0.1"]})
    assert wds.is_url_allowed("https://public.example/hook", res) == (True, "")
    assert not wds.is_url_allowed("https://internal.example/hook", res)[0]
    assert not wds.is_url_allowed("https://dual.example/hook", res)[0]       # any private IP → refuse
    assert not wds.is_url_allowed("https://v6.example/hook", res)[0]
    assert not wds.is_url_allowed("http://127.0.0.1:8000/x", res)[0]
    assert not wds.is_url_allowed("http://localhost/x", res)[0]
    assert not wds.is_url_allowed("http://169.254.169.254/latest/meta-data", res)[0]  # cloud metadata
    assert not wds.is_url_allowed("ftp://public.example/x", res)[0]
    assert not wds.is_url_allowed("not a url", res)[0]
    assert not wds.is_url_allowed("https://missing.example/x", res)[0]
    # explicit override / env override
    assert wds.is_url_allowed("http://127.0.0.1:8000/x", res, allow_private=True)[0]
    monkeypatch.setenv("WEBHOOKS_ALLOW_PRIVATE", "1")
    assert wds.is_url_allowed("http://10.0.0.5/x", res)[0]


# ── Jobs ──────────────────────────────────────────────────────────────────────

def test_register_jobs_adds_retry_and_prune():
    calls = []

    class _Sched:
        def add_job(self, func, trigger=None, **kw):
            calls.append((func, trigger, kw))

    webhook_jobs.register_jobs(_Sched())
    ids = {kw["id"] for _, _, kw in calls}
    assert ids == {"webhook_retry_pending", "webhook_prune_logs"}
    retry = next(c for c in calls if c[2]["id"] == "webhook_retry_pending")
    assert retry[0] is webhook_jobs.retry_pending_deliveries
    assert type(retry[1]).__name__ == "IntervalTrigger" and retry[1].interval.total_seconds() == 120
    prune = next(c for c in calls if c[2]["id"] == "webhook_prune_logs")
    assert type(prune[1]).__name__ == "CronTrigger"
    assert all(kw.get("replace_existing") for _, _, kw in calls)


# ── send_delivery round-trip with stubbed transport ──────────────────────────

def _stub_store(monkeypatch):
    recorded = {}

    def _record(delivery_id, ok, status_code, error, response_text, entry):
        recorded.update(id=delivery_id, ok=ok, status_code=status_code, error=error,
                        response=response_text, entry=entry)
        return {"id": delivery_id, "status": "success" if ok else "failed"}

    monkeypatch.setattr(wds.webhook_store, "record_attempt", _record)
    monkeypatch.setattr(wds, "is_url_allowed", lambda url, *a, **k: (True, ""))
    return recorded


def test_send_delivery_signs_body_and_sets_headers(monkeypatch):
    recorded = _stub_store(monkeypatch)
    seen = {}

    def _post(url, body, headers, timeout=10):
        seen.update(url=url, body=body, headers=headers, timeout=timeout)
        return 200, "ok"

    monkeypatch.setattr(wds, "_http_post", _post)
    delivery = {"id": "d-1", "company_id": "acme", "event": "invoice.created",
                "payload": {"invoice_id": "INV-1", "total": 10.5}, "created_at": "2026-09-11T10:00:00"}
    endpoint = {"url": "https://receiver.example/hook", "secret": SECRET}
    out = wds.send_delivery(delivery, endpoint)

    assert out["status"] == "success" and recorded["ok"] and recorded["status_code"] == 200
    h = seen["headers"]
    assert h["X-EBMS-Event"] == "invoice.created"
    assert h["X-EBMS-Delivery-Id"] == "d-1"
    assert h["Content-Type"] == "application/json"
    assert seen["timeout"] == wds.HTTP_TIMEOUT == 10
    # the signature verifies against the exact bytes that were sent
    body = seen["body"].decode("utf-8")
    assert wds.verify_signature(SECRET, body, h["X-EBMS-Signature"])
    assert not wds.verify_signature("wrong", body, h["X-EBMS-Signature"])
    import json
    env = json.loads(body)
    assert env["id"] == "d-1" and env["event"] == "invoice.created" and env["data"]["invoice_id"] == "INV-1"
    assert recorded["entry"]["request_headers"]["X-EBMS-Signature"] == h["X-EBMS-Signature"]


def test_send_delivery_records_http_failure_and_transport_error(monkeypatch):
    recorded = _stub_store(monkeypatch)
    delivery = {"id": "d-2", "company_id": "acme", "event": "bid.won", "payload": {}}
    endpoint = {"url": "https://receiver.example/hook", "secret": SECRET}

    monkeypatch.setattr(wds, "_http_post", lambda *a, **k: (503, "down"))
    out = wds.send_delivery(delivery, endpoint)
    assert out["status"] == "failed" and recorded["status_code"] == 503 and recorded["error"] == "HTTP 503"

    def _boom(*a, **k):
        raise TimeoutError("read timed out")
    monkeypatch.setattr(wds, "_http_post", _boom)
    out = wds.send_delivery(delivery, endpoint)
    assert out["status"] == "failed" and recorded["status_code"] is None
    assert "TimeoutError" in recorded["error"]


def test_send_delivery_blocks_private_url_without_network(monkeypatch):
    recorded = _stub_store(monkeypatch)
    monkeypatch.setattr(wds, "is_url_allowed", lambda url, *a, **k: (False, "private"))
    called = []
    monkeypatch.setattr(wds, "_http_post", lambda *a, **k: called.append(1) or (200, ""))
    out = wds.send_delivery({"id": "d-3", "company_id": "acme", "event": "x.y", "payload": {}},
                            {"url": "http://10.0.0.1/", "secret": SECRET})
    assert not called and out["status"] == "failed" and recorded["error"].startswith("Blocked")


def test_emit_returns_empty_when_no_endpoint_listens(monkeypatch):
    monkeypatch.setattr(wds.webhook_store, "active_endpoints_for_event", lambda cid, ev: [])
    assert wds.emit("acme", "invoice.created", {"a": 1}) == []
    assert wds.emit("acme", "", {"a": 1}) == []


def test_emit_creates_one_delivery_per_matching_endpoint(monkeypatch):
    eps = [{"id": "e1", "events": ["invoice.*"]}, {"id": "e2", "events": ["*"]}]
    monkeypatch.setattr(wds.webhook_store, "active_endpoints_for_event", lambda cid, ev: eps)
    created = []

    def _create(endpoint_id, company_id, event, payload, dedupe_key=None, delay_seconds=None):
        created.append((endpoint_id, company_id, event, dedupe_key, delay_seconds))
        return {"id": f"d-{endpoint_id}", "status": "pending"}

    monkeypatch.setattr(wds.webhook_store, "create_delivery", _create)
    rows = wds.emit("acme", "invoice.paid", {"event_id": "E9"}, deliver_now=False)
    assert [r["id"] for r in rows] == ["d-e1", "d-e2"]
    assert all(c[1] == "acme" and c[2] == "invoice.paid" and c[3] == "invoice.paid:E9" and c[4] is None for c in created)
