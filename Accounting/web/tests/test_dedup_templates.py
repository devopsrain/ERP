"""
Template render tests — duplicate-entry protection forms.

Same stub-harness pattern as test_vat_templates.py: render each form template
touched by the dedup work with route-accurate contexts, both in the normal
state and in the duplicate-warning state (form_data preserved + force_create
checkbox), so undefined variables fail the build instead of 500ing. No DB.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from models.ethiopian_payroll import EmployeeCategory  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))


def _base_ctx(path="/x"):
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
    )


def _emp_form_data():
    """What add_employee_post re-renders with on a duplicate warning."""
    return dict(
        employee_id="EMP002", name="Abebe Kebede", category="Regular Employee",
        basic_salary="12000", hire_date="2026-07-01", department="Finance",
        position="Accountant", bank_account="1000123", tin_number="123456789",
        pension_number="PEN1", phone_number="+251-91-000-0000", manager="",
        date_of_birth="",
    )


def _quick_form_data():
    return dict(name="Abebe Kebede", email="abebe@example.com",
                phone_number="+251-91-000-0000", department="Finance",
                position="Accountant")


def _client_data():
    return dict(name="Acme Events", organization="Acme PLC", phone="0911000000",
                email="acme@example.com", tin="123456789", notes="VIP")


CASES = [
    # Full add-employee form: blank, and duplicate-warning re-render
    ("payroll/add_employee.html", lambda: dict(categories=EmployeeCategory)),
    ("payroll/add_employee.html", lambda: dict(categories=EmployeeCategory,
                                               form_data=_emp_form_data(),
                                               duplicate_warning=True)),
    # Quick-add form: blank, with form data, and duplicate-warning re-render
    ("payroll/quick_add_employee.html", lambda: {}),
    ("payroll/quick_add_employee.html", lambda: dict(form_data=_quick_form_data())),
    ("payroll/quick_add_employee.html", lambda: dict(form_data=_quick_form_data(),
                                                     duplicate_warning=True)),
    # EMS client form: blank create, and duplicate-warning re-render
    ("ems/client_form.html", lambda: dict(client={}, action="create")),
    ("ems/client_form.html", lambda: dict(client=_client_data(), action="create",
                                          duplicate_warning=True)),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_dedup_template_renders(template, ctx_fn):
    ctx = {**_base_ctx(), **ctx_fn()}
    html = env.get_template(template).render(**ctx)
    assert len(html) > 1000  # sanity: a real page came out
    if ctx.get("duplicate_warning"):
        assert 'name="force_create"' in html  # override control is offered
    else:
        assert 'name="force_create"' not in html  # hidden in normal state
