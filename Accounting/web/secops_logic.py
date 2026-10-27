"""
Security Operations — pure business logic (no database, no FastAPI).

Everything here works on plain dicts / datetimes so it can be unit-tested
without PostgreSQL and reused by the data store, the routes and the jobs.

Contents
    vocabularies         GRADES, SITE_TYPES, COVERAGES, ... (option lists for forms)
    time helpers         parse_time, parse_date, parse_month, shift_bounds, week_range
    roster expansion     expand_roster, slots_for_post, auto_fill_from_previous
    conflicts / gaps     find_conflicts, coverage_gaps, coverage_pct
    hours                compute_hours, night_overlap_hours, late_minutes_for
    pay                  overtime_multiplier, hourly_rate, payroll_feed_row
    documents            licence_status, expiring_documents
    numbering            format_incident_no

Ethiopian labour proclamation 1156/2019 (art. 68) overtime rates are used as
defaults: 1.5x on a normal working day, 2.0x on the weekly rest day, 2.5x on
a public holiday.  Night work (22:00-06:00) earns a per-hour night allowance
configured in ``secops_settings``.  All of these are configurable per company.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

# ── Vocabularies ──────────────────────────────────────────────────
GRADES = ("trainee", "guard", "senior_guard", "supervisor", "inspector")
GRADE_RANK = {g: i for i, g in enumerate(GRADES)}
SITE_TYPES = ("office", "residence", "bank", "warehouse", "construction", "event", "other")
SITE_STATUSES = ("active", "suspended", "closed")
COVERAGES = ("24h", "day", "night", "custom")
GUARD_STATUSES = ("active", "on_leave", "suspended", "exited")
ROSTER_STATUSES = ("draft", "published", "closed")
ASSIGNMENT_STATUSES = ("scheduled", "swapped", "cancelled")
ATTENDANCE_STATUSES = ("present", "late", "absent", "relief", "leave", "off")
PRESENT_STATUSES = ("present", "late", "relief")
ATTENDANCE_SOURCES = ("manual", "self", "supervisor", "telegram", "system")
KIT_KINDS = ("uniform", "radio", "torch", "baton", "firearm", "vehicle", "other")
KIT_STATUSES = ("issued", "returned", "lost", "damaged")
INCIDENT_CATEGORIES = ("theft", "intrusion", "fire", "medical", "assault", "property_damage",
                       "policy_breach", "other")
SEVERITIES = ("low", "medium", "high", "critical")
INCIDENT_STATUSES = ("open", "investigating", "closed")
FEED_STATUSES = ("draft", "exported")
TRAINING_LEVELS = ("basic", "intermediate", "advanced", "armed", "firearms_certified")

DEFAULT_SETTINGS = {
    "night_start": "22:00",
    "night_end": "06:00",
    "night_allowance_per_hour": Decimal("0"),
    "overtime_weekday": Decimal("1.5"),
    "overtime_weekend": Decimal("2.0"),
    "overtime_holiday": Decimal("2.5"),
    "weekend_days": "6",            # ISO weekday numbers, comma separated (6 = Sunday)
    "late_grace_minutes": 15,
    "default_hours_per_day": Decimal("8"),
    "billing_currency": "ETB",
}

TWOPLACES = Decimal("0.01")


def q2(v) -> Decimal:
    return D(v).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def D(v) -> Decimal:
    """Decimal from anything form-ish ('' and None → 0)."""
    if v in (None, ""):
        return Decimal(0)
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(0)


def opt(v):
    """'' → None (Postgres rejects '' for DATE / NUMERIC / TIMESTAMP)."""
    return None if v in ("", None) else v


# ── Time helpers ──────────────────────────────────────────────────
def parse_time(v) -> Optional[time]:
    if v in (None, ""):
        return None
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    s = str(v).strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%H%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def parse_date(v) -> Optional[date]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def parse_datetime(v) -> Optional[datetime]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, time.min)
    s = str(v).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def parse_month(period: str) -> tuple[date, date]:
    """'2026-10' → (2026-10-01, 2026-10-31). Raises ValueError on bad input."""
    y, m = str(period).strip().split("-")[:2]
    y, m = int(y), int(m)
    if not 1 <= m <= 12:
        raise ValueError("month out of range")
    start = date(y, m, 1)
    end = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return start, end


def previous_month(today: Optional[date] = None) -> str:
    today = today or date.today()
    first = today.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def week_range(anchor: Optional[date] = None) -> tuple[date, date]:
    """Monday..Sunday week containing ``anchor`` (default today)."""
    anchor = anchor or date.today()
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=6)


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def shift_bounds(duty_date: date, shift: dict) -> tuple[datetime, datetime]:
    """Absolute start/end for a shift template on a duty date. A shift whose
    end time is not after its start time (e.g. 18:00 → 06:00) ends the next day."""
    st = parse_time(shift.get("start_time")) or time(6, 0)
    et = parse_time(shift.get("end_time")) or time(18, 0)
    start = datetime.combine(duty_date, st)
    end = datetime.combine(duty_date, et)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def shift_hours(shift: dict) -> Decimal:
    start, end = shift_bounds(date(2026, 1, 1), shift)
    minutes = (end - start).total_seconds() / 60 - float(shift.get("break_minutes") or 0)
    return q2(Decimal(max(minutes, 0)) / 60)


# ── Roster expansion ──────────────────────────────────────────────
def slots_for_post(post: dict, shift_templates: Iterable[dict]) -> list[dict]:
    """Which shift templates a post needs each day, derived from its coverage.

    24h    → every template (day + night) — a full rotation
    day    → templates that are not flagged ``is_night``
    night  → templates flagged ``is_night``
    custom → every template; ``hours_per_day`` on the post is informational
    """
    templates = [t for t in shift_templates if t.get("is_active", True)]
    cov = (post.get("coverage") or "24h").lower()
    if cov == "day":
        chosen = [t for t in templates if not t.get("is_night")]
    elif cov == "night":
        chosen = [t for t in templates if t.get("is_night")]
    else:
        chosen = templates
    return chosen


def expand_roster(posts: Iterable[dict], shift_templates: Iterable[dict],
                  period_start: date, period_end: date) -> list[dict]:
    """Every (post, duty_date, shift_template) slot the roster must fill, with
    the number of guards required. Inactive posts are skipped."""
    out = []
    templates = list(shift_templates)
    for post in posts:
        if not post.get("is_active", True):
            continue
        shifts = slots_for_post(post, templates)
        for d in date_range(period_start, period_end):
            for sh in shifts:
                start, end = shift_bounds(d, sh)
                out.append({
                    "post_id": post["id"], "post_name": post.get("name", ""),
                    "site_id": post.get("site_id"),
                    "duty_date": d, "shift_template_id": sh["id"], "shift_name": sh.get("name", ""),
                    "start_at": start, "end_at": end,
                    "guards_required": int(post.get("guards_required") or 1),
                })
    return out


def _slot_key(a: dict) -> tuple:
    return (str(a.get("post_id")), str(parse_date(a.get("duty_date"))), str(a.get("shift_template_id")))


def coverage_gaps(slots: Iterable[dict], assignments: Iterable[dict]) -> list[dict]:
    """Slots whose assigned (non-cancelled) guards fall short of guards_required."""
    counts: dict[tuple, int] = {}
    for a in assignments:
        if (a.get("status") or "scheduled") == "cancelled":
            continue
        k = _slot_key(a)
        counts[k] = counts.get(k, 0) + 1
    gaps = []
    for s in slots:
        assigned = counts.get(_slot_key(s), 0)
        if assigned < s["guards_required"]:
            g = dict(s)
            g["assigned"] = assigned
            g["gap"] = s["guards_required"] - assigned
            gaps.append(g)
    return gaps


def coverage_pct(required: int, assigned: int) -> Decimal:
    if not required:
        return Decimal(100)
    return q2(Decimal(min(assigned, required)) * 100 / Decimal(required))


def _interval(a: dict) -> tuple[datetime, datetime]:
    s, e = parse_datetime(a.get("start_at")), parse_datetime(a.get("end_at"))
    if s and e:
        return s, e
    d = parse_date(a.get("duty_date")) or date.today()
    return shift_bounds(d, {"start_time": a.get("start_time"), "end_time": a.get("end_time")})


def overlaps(a: dict, b: dict) -> bool:
    s1, e1 = _interval(a)
    s2, e2 = _interval(b)
    return s1 < e2 and s2 < e1


def find_conflicts(candidate: dict, existing: Iterable[dict]) -> list[dict]:
    """Assignments of the same guard that overlap the candidate in time (any
    post / any site). A guard can't be in two places at once."""
    out = []
    gid = str(candidate.get("guard_id"))
    for e in existing:
        if str(e.get("guard_id")) != gid or (e.get("status") or "scheduled") == "cancelled":
            continue
        if e.get("id") and e.get("id") == candidate.get("id"):
            continue
        if overlaps(candidate, e):
            out.append(e)
    return out


def auto_fill_from_previous(previous: Iterable[dict], period_start: date, period_end: date,
                            prev_start: date) -> list[dict]:
    """Shift last period's assignments forward so they land in the new period.
    Returns plain dicts (post_id, guard_id, shift_template_id, duty_date, is_relief)."""
    offset = (period_start - prev_start).days
    out = []
    for a in previous:
        if (a.get("status") or "scheduled") == "cancelled":
            continue
        d = parse_date(a.get("duty_date"))
        if not d:
            continue
        nd = d + timedelta(days=offset)
        if period_start <= nd <= period_end:
            out.append({"post_id": a["post_id"], "guard_id": a["guard_id"],
                        "shift_template_id": a.get("shift_template_id"), "duty_date": nd,
                        "is_relief": bool(a.get("is_relief")), "notes": a.get("notes") or ""})
    return out


# ── Hours ─────────────────────────────────────────────────────────
def night_overlap_hours(start: datetime, end: datetime,
                        night_start: time = time(22, 0), night_end: time = time(6, 0)) -> Decimal:
    """Hours of [start, end) falling inside the nightly window night_start→night_end
    (which may wrap midnight)."""
    if not start or not end or end <= start:
        return Decimal(0)
    total = timedelta(0)
    day = start.date() - timedelta(days=1)
    last = end.date() + timedelta(days=1)
    while day <= last:
        ws = datetime.combine(day, night_start)
        we = datetime.combine(day, night_end)
        if we <= ws:
            we += timedelta(days=1)
        lo, hi = max(start, ws), min(end, we)
        if hi > lo:
            total += hi - lo
        day += timedelta(days=1)
    return q2(Decimal(total.total_seconds()) / 3600)


def late_minutes_for(check_in: datetime, scheduled_start: Optional[datetime], grace_minutes: int = 0) -> int:
    if not check_in or not scheduled_start:
        return 0
    late = int((check_in - scheduled_start).total_seconds() // 60)
    return max(0, late - int(grace_minutes or 0)) if late > int(grace_minutes or 0) else 0


def compute_hours(check_in, check_out, shift_template: Optional[dict] = None,
                  work_hours_per_day=8, night_start=time(22, 0), night_end=time(6, 0),
                  grace_minutes: int = 0, duty_date: Optional[date] = None) -> dict:
    """Hours worked / night hours / overtime / lateness for one attendance.

    * hours_worked  = check-out − check-in minus the template's break
    * night_hours   = part of the worked interval inside night_start..night_end
    * overtime_hours = hours_worked beyond work_hours_per_day (never negative)
    * late_minutes  = check-in after scheduled start (minus grace)
    Missing check-out → everything 0 except late_minutes.
    """
    ci, co = parse_datetime(check_in), parse_datetime(check_out)
    ns = parse_time(night_start) or time(22, 0)
    ne = parse_time(night_end) or time(6, 0)
    sched_start = None
    brk = 0
    if shift_template:
        brk = int(shift_template.get("break_minutes") or 0)
        dd = duty_date or (ci.date() if ci else None)
        if dd:
            sched_start, _ = shift_bounds(dd, shift_template)
    out = {"hours_worked": Decimal(0), "night_hours": Decimal(0),
           "overtime_hours": Decimal(0), "late_minutes": late_minutes_for(ci, sched_start, grace_minutes)}
    if not ci or not co or co <= ci:
        return out
    minutes = (co - ci).total_seconds() / 60 - brk
    hours = q2(Decimal(max(minutes, 0)) / 60)
    out["hours_worked"] = hours
    out["night_hours"] = min(night_overlap_hours(ci, co, ns, ne), hours)
    std = D(work_hours_per_day) if D(work_hours_per_day) > 0 else Decimal(8)
    out["overtime_hours"] = q2(max(hours - std, Decimal(0)))
    return out


# ── Pay ───────────────────────────────────────────────────────────
def weekend_day_set(spec) -> set[int]:
    """'5,6' → {5, 6} (Python weekday numbers, Monday = 0 … Sunday = 6)."""
    out = set()
    for part in str(spec or "6").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            out.add(int(part))
    return out or {6}


def overtime_multiplier(duty_date: date, settings: Optional[dict] = None,
                        holidays: Optional[Iterable] = None) -> Decimal:
    """2.5x public holiday, 2.0x weekly rest day, 1.5x otherwise (configurable)."""
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    hol = {parse_date(h if not isinstance(h, dict) else h.get("holiday_date")) for h in (holidays or [])}
    if duty_date in hol:
        return D(s.get("overtime_holiday"))
    if duty_date.weekday() in weekend_day_set(s.get("weekend_days")):
        return D(s.get("overtime_weekend"))
    return D(s.get("overtime_weekday"))


def hourly_rate(basic_salary, work_days_per_month=26, work_hours_per_day=8) -> Decimal:
    days = D(work_days_per_month) or Decimal(26)
    hrs = D(work_hours_per_day) or Decimal(8)
    if days <= 0 or hrs <= 0:
        return Decimal(0)
    return q2(D(basic_salary) / (days * hrs))


def payroll_feed_row(attendance: Iterable[dict], guard: dict, settings: Optional[dict] = None,
                     holidays: Optional[Iterable] = None) -> dict:
    """Aggregate one guard's attendance rows for a period into a payroll feed line."""
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    rate = hourly_rate(guard.get("basic_salary"), guard.get("work_days_per_month"), guard.get("work_hours_per_day"))
    row = {"guard_id": guard.get("id"), "employee_id": guard.get("employee_id"), "name": guard.get("name", ""),
           "days_present": 0, "days_absent": 0, "hours_worked": Decimal(0), "night_hours": Decimal(0),
           "overtime_hours": Decimal(0), "overtime_pay": Decimal(0), "night_allowance": Decimal(0),
           "hourly_rate": rate}
    seen_days: dict[date, str] = {}
    for a in attendance:
        d = parse_date(a.get("duty_date"))
        st = a.get("status") or "present"
        if d is not None:
            # a day counts once; present beats absent when both are recorded
            if st in PRESENT_STATUSES or seen_days.get(d) not in PRESENT_STATUSES:
                seen_days[d] = st
        row["hours_worked"] += D(a.get("hours_worked"))
        row["night_hours"] += D(a.get("night_hours"))
        ot = D(a.get("overtime_hours"))
        row["overtime_hours"] += ot
        if ot and d:
            row["overtime_pay"] += ot * rate * overtime_multiplier(d, s, holidays)
    row["days_present"] = sum(1 for st in seen_days.values() if st in PRESENT_STATUSES)
    row["days_absent"] = sum(1 for st in seen_days.values() if st == "absent")
    row["night_allowance"] = q2(row["night_hours"] * D(s.get("night_allowance_per_hour")))
    for k in ("hours_worked", "night_hours", "overtime_hours", "overtime_pay"):
        row[k] = q2(row[k])
    return row


def billable_amount(hours, rate) -> Decimal:
    return q2(D(hours) * D(rate))


# ── Documents / licences ──────────────────────────────────────────
def licence_status(expiry, today: Optional[date] = None, warn_days: int = 30) -> str:
    """'missing' | 'expired' | 'expiring' (within warn_days) | 'valid'."""
    d = parse_date(expiry)
    if not d:
        return "missing"
    today = today or date.today()
    if d < today:
        return "expired"
    if (d - today).days <= warn_days:
        return "expiring"
    return "valid"


DOCUMENT_FIELDS = (("licence_expiry", "Guard licence"), ("firearm_permit_expiry", "Firearm permit"),
                   ("medical_fitness_expiry", "Medical fitness"))


def expiring_documents(guard: dict, today: Optional[date] = None, warn_days: int = 30) -> list[dict]:
    """Documents on a guard profile that are expired or expiring within warn_days."""
    today = today or date.today()
    out = []
    for field, label in DOCUMENT_FIELDS:
        exp = parse_date(guard.get(field))
        if not exp:
            continue
        st = licence_status(exp, today, warn_days)
        if st in ("expired", "expiring"):
            out.append({"field": field, "label": label, "expiry": exp, "status": st,
                        "days": (exp - today).days})
    return out


def worst_document_status(guard: dict, today: Optional[date] = None, warn_days: int = 30) -> str:
    docs = expiring_documents(guard, today, warn_days)
    if any(d["status"] == "expired" for d in docs):
        return "expired"
    if docs:
        return "expiring"
    return "valid" if parse_date(guard.get("licence_expiry")) else "missing"


def reminder_due(expiry, today: Optional[date] = None, horizons=(30, 7)) -> Optional[int]:
    """Return the horizon (30 / 7) hit exactly today, 0 when expired today/earlier, else None."""
    d = parse_date(expiry)
    if not d:
        return None
    today = today or date.today()
    delta = (d - today).days
    if delta in horizons:
        return delta
    if delta <= 0:
        return 0
    return None


# ── Numbering ─────────────────────────────────────────────────────
def format_incident_no(year: int, n: int) -> str:
    return f"INC-{year}-{int(n):06d}"


def guard_no_for(seq: int, prefix: str = "G") -> str:
    return f"{prefix}-{int(seq):04d}"


def grade_meets(guard_grade: str, required: str) -> bool:
    if not required:
        return True
    return GRADE_RANK.get(guard_grade or "guard", 1) >= GRADE_RANK.get(required, 0)
