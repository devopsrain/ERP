# Security Operations module (manned guarding) — `/secops`

EBMS module for security-guard / manned-guarding companies: clients and guarded
sites, posts and shift templates, guard profiles with licences, weekly rosters
with conflict checking, attendance (supervisor sheet, guard self check-in,
Telegram bot API), kit (uniforms, radios, equipment), incidents, a monthly
attendance → payroll feed, and ten reports with Excel export. Every screen is
available in English and Amharic (አማርኛ).

Open it from **Security Operations** in the module menu or go to `/secops/`.

---

## Requirement → screen map

| # | Requirement | Where in EBMS |
|---|-------------|---------------|
| 1 | Client register with billing rate and contract link | **Master Data → Clients** (`/secops/clients`). Links to a client contract (Contract module) and, optionally, to a commercial customer. Billing rate per guard-hour feeds the billing summary report. |
| 2 | Site register with post orders, supervisor, GPS, service dates | **Master Data → Sites & Posts** (`/secops/sites`, `/secops/sites/{id}`). Post orders / standing instructions are kept on the site; signed post orders, site maps and contract annexes are uploaded under *Site documents*. |
| 3 | Posts per site (coverage, guards required, armed, grade) | Site page → **Posts** card: inline edit, add, remove (a post with roster history is deactivated instead of deleted). |
| 4 | Shift templates (Day 06-18 / Night 18-06 / Morning / Afternoon) | **Master Data → Shift Templates** (`/secops/shifts`). Defaults Day 06-18 and Night 18-06 are seeded on first use; a shift whose end ≤ start runs overnight. |
| 5 | Guard profile extending the employee record (grade, licence, firearm permit, medical fitness, training, home site, status) | **Master Data → Guards** (`/secops/guards`). A guard profile is created from an existing employee (HR / Payroll). The list highlights expired / expiring documents; scans are uploaded on the guard page. |
| 6 | Weekly roster per site, post × day grid, assign guards per shift | **Rosters** (`/secops/rosters`) → *Open builder* (`/secops/rosters/{id}`). Grid of posts × days with one slot per shift; each slot shows assigned / required, a relief marker and an inline *+ assign* picker (home-site guards marked ★). |
| 7 | Auto-fill from last week | Builder → **Auto-fill from last week** copies the previous roster of the same site forward by the period length; conflicts and already-filled slots are skipped and counted. |
| 8 | Conflict check (same guard, overlapping times, any site) | Enforced on every assignment: the builder refuses the assignment and tells you where the guard already is. Grade requirements of the post are checked too. |
| 9 | Publish → guards see duties, Telegram notice | Builder → **Publish** (manager). Published duties appear on each guard's **My Duties & Check-in** page; a per-guard Telegram message and a company digest are queued when the bot is configured. |
| 10 | Deployment dashboard (sites covered, posts uncovered, on duty now, absent/late, open incidents, expiring licences, per-site coverage cards) | **Deployment Dashboard** (`/secops/`), any day selectable. Each site card shows required / assigned / present, coverage %, links to the roster and the attendance sheet. |
| 11 | Coverage-gap report for a date range | **Coverage Gaps** (`/secops/coverage`), also as Excel. Lists every post × shift × day slot with fewer guards rostered than required. |
| 12 | Attendance: supervisor bulk sheet per site per day | **Attendance → Supervisor sheet** (`/secops/attendance/sheet`): everyone rostered is listed, *Mark all present*, then tick absences / late; check-in/out times default to the shift times. Walk-in relief guards are added underneath. |
| 13 | Attendance: guard self check-in / check-out with GPS | **My Duties & Check-in** (`/secops/me`): big Check-in / Check-out buttons, browser geolocation attached when allowed. The user is matched to an employee (e-mail / name) and then to the guard profile. |
| 14 | Attendance: Telegram bot API | `POST /secops/api/checkin` with JSON `{guard_no | employee_id | guard_id, action: "in"|"out", lat, lng}`; authenticated by session / Bearer token or the `X-SecOps-Token` header matching `SECOPS_API_TOKEN`. |
| 15 | Attendance: no-shows marked absent | Daily job at 06:00 marks yesterday's scheduled-but-unrecorded duties (published rosters) as absent. |
| 16 | Hours / night hours / overtime / lateness computation | Computed on every check-out and on the supervisor sheet: night hours inside the configurable night window (default 22:00–06:00), overtime beyond the employee's work hours per day, lateness after the grace period. |
| 17 | Kit catalogue (uniform, radio, torch, baton, firearm, vehicle) with inventory link | **Master Data → Kit Catalogue** (`/secops/kit`). Items linked to an inventory item post an inventory *issue* / *return* movement automatically (company-owned kit only). |
| 18 | Kit issue / return to guards and sites, per-guard printable kit card, per-site client-owned asset list | **Kit Issues & Returns** (`/secops/kit/issues`), *Issue kit* form, guard page → **Kit card** (printable with signature lines), site page → **Equipment at site**. Lost / damaged write-offs need a manager. |
| 19 | Outstanding-kit check when a guard exits | Guard page → Status → *Exited*: future duties are cancelled and any kit still issued is flagged for collection. |
| 20 | Incident register with gapless numbers, photos, police reference, client notification | **Incidents** (`/secops/incidents`): `INC-YYYY-000001` numbering, photo / document attachments, police reference, client notification note with optional e-mail to the client contact. High / critical incidents are pushed to the Telegram daily-digest topic. |
| 21 | Attendance → payroll feed (days present/absent, hours, night allowance, overtime pay) | **Finance → Payroll Feed** (`/secops/payroll-feed`): generate for a month (manager), review, **Export Excel**, **Push to payroll**. Also generated automatically on the 1st of each month for the previous month. |
| 22 | Overtime rules per Ethiopian labour proclamation | **Settings** (`/secops/settings`, manager): 1.5× normal day, 2.0× weekly rest day, 2.5× public holiday (proclamation 1156/2019 art. 68), weekly rest days, public-holiday list, night window and night allowance per hour, late grace. |
| 23 | Reports (HTML + Excel, date range + site / client / guard filters) | **Reports** (`/secops/reports`): coverage per site/day, attendance register, absenteeism & lateness, hours / overtime / night, kit outstanding per guard, client-owned equipment per site, licence & permit expiry, incidents by site/category/severity, incident register, client billing summary. |
| 24 | Licence / permit expiry reminders | Daily job: 30-day and 7-day reminders plus expired notices (Telegram digest + e-mail when configured); dashboard card and *Licence and permit expiry* report. |

---

## Daily workflow

1. **Set up** (once): add clients → sites → posts; check shift templates; create guard profiles from employees; add kit items; set overtime rules and holidays in Settings.
2. **Every week**: open the roster for each site, auto-fill from last week, adjust, publish.
3. **Every day**: dashboard shows uncovered posts and absentees; supervisors save the attendance sheet per site (or guards check in themselves / via Telegram); incidents are recorded as they happen.
4. **Every month**: the payroll feed is generated on the 1st; review it, export to Excel or push to payroll; run the client billing summary for invoicing.

## Payroll hand-off

The feed Excel has one row per guard: *Employee ID, Name, Guard No, Period,
Days Present, Days Absent, Hours Worked, Night Hours, Overtime Hours, Hourly
Rate, Overtime Pay, Night Allowance*. **Push to payroll** writes "Overtime
YYYY-MM" and "Night allowance YYYY-MM" lines into the payroll module's
employee-allowance table when that table has an amount column; otherwise the
module tells you that the exported file is the hand-off.

- Hourly rate = basic salary ÷ (work days per month × work hours per day) from the employee record.
- Overtime pay = overtime hours × hourly rate × multiplier for that day.
- Night allowance = night hours × allowance per hour (Settings).

## Scheduled jobs

| When | Job |
|------|-----|
| Daily 06:00 | Mark yesterday's no-shows absent; licence / permit / medical reminders (30 d, 7 d, expired); uncovered-posts digest for today. |
| 1st of month 06:30 | Build the previous month's payroll feed (draft) for every company. |

Notifications go to the Telegram `daily_digest` topic when the bot is
configured and to `SECOPS_ALERT_EMAIL` (or `ADMIN_EMAIL`) when e-mail is
configured; both are optional.

## Configuration

| Variable | Purpose |
|----------|---------|
| `SECOPS_API_TOKEN` | Shared secret for `POST /secops/api/checkin` (`X-SecOps-Token` header) used by the Telegram bot. |
| `SECOPS_ALERT_EMAIL` | Recipient of the daily digest e-mail (falls back to `ADMIN_EMAIL`). |

Access: all pages need a login; publishing a roster, settings, payroll-feed
generation / push, and kit write-offs need the *manager* privilege.

## For developers

- `web/secops_data_store.py` — schema (`secops_*` tables, created at startup) and the store; public Python API `guard_for_user`, `record_checkin`, `coverage_summary`, `open_incident_count`.
- `web/secops_logic.py` — pure logic (roster expansion, coverage gaps, conflicts, hours / night / overtime, pay multipliers, licence status).
- `web/secops_routes.py` — `/secops` router (route names `secops_*`).
- `web/secops_jobs.py` — `register_jobs(scheduler)`, `run_daily()`, `run_monthly()`.
- `web/i18n_catalogue_secops.py` — Amharic catalogue.
- Tests: `web/tests/test_secops_logic.py`, `web/tests/test_secops_templates.py`.
