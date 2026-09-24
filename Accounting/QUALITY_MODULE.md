# EBMS Quality Management Module

**URL prefix:** `/quality` · **Menu:** Quality Management · **Files:** `web/quality_*.py`, `web/templates/quality/`, `web/i18n_catalogue_quality.py`

The Quality Management module gives a cable manufacturer one place to plan
inspections, record every quality form the plant uses (raw material, in-process,
insulation, final product, packing, AAC/ABC delivery), manage calibration of
measuring equipment, handle customer complaints, non-conformance reports (NCR)
and corrective / preventive actions (CAPA), run internal audits and produce the
statistical reports a QA department is asked for — Certificate of Analysis,
SPC control charts, supplier compliance, yield, defect rates and lot history.

Everything works standalone (free-text order numbers, product types, machine
and supplier names) and links itself automatically to the Manufacturing,
Commercial and Procurement modules when they are present. All UI text is
available in English and Amharic; dates are shown with their Ethiopian
calendar equivalent.

---

## How the tender requirements are covered

| Tender item | Where in EBMS |
|---|---|
| **Inspection plans & specifications** — sampling / test plan per product, industry standard + Ethiopian Standard (ES) + ISO values, usable for raw & packaging materials, finished products, process and internal audits | *Inspection Plans & Specs* (`/quality/specs`). A specification set has a name, what it applies to (raw material / in-process / final / packaging / audit), product code / type, standard reference (IEC, ES, ISO), version and status (draft → active → obsolete). Each set holds parameters with unit, acceptance kind (range / min / max / nominal ± tolerance %), method, sample size and a mandatory flag. An active plan can be chosen on any inspection form and pre-loads its parameter lines. |
| **Raw material inspection** — type of material, code, supplier, PR no., invoice no./date, sample type, quantity, standard required, lot, result, disposition; prepared / received / inspected / approved by | `/quality/inspections/rm`. Disposition accept / reject / return to supplier / request replacement. Supplier names come from Procurement vendors when available. Feeds the Supplier compliance report and `latest_rm_result()` (used by Manufacturing to gate order release). |
| **Cable in-process inspection** — machine, order, product type mm², process / production type, description, next process, spec vs actual for wire Ø, conductor Ø, resistance Ω/km, insulation thickness / Ø, laid-up Ø, bedding Ø, sheath thickness / Ø, total length | `/quality/inspections/inprocess`. The ten tender pairs are the default parameter lines (extensible — add / remove lines on the form). Each line is evaluated automatically against min / max / nominal ± tolerance. |
| **Wire insulation inspection** — machine, order, product type, process / production type, colour code, next process, resistance, insulation thickness / Ø spec vs actual, actual length, product defect rework / reject / none | `/quality/inspections/insulation`. A "reject" defect makes the overall result fail. |
| **Final product inspection** — customer, cable type mm², IEC/ES standard, description, date, RM code, rated / test voltage kV, order number, total length, drum number, test result, certificate number | `/quality/inspections/final`. Certificate number `COA-YYYY-000001` is issued gaplessly on creation. The **Certificate of Analysis** (printable HTML → PDF via the browser) lists every test with requirement, result, accuracy of determination and verdict, plus the four signatures. |
| **Wire packing summary** — order, product type, description, colour, input length, standard roll length, under-length, total length, scrap kg, rolls | `/quality/inspections/packing`. Each record shows its yield (input vs output vs loss vs scrap) and feeds the Material yield report. |
| **AAC / ABC delivery report** — order, size, description, pitch length, wire Ø, conductor Ø spec vs actual, resistance, length km / m, drums, customer | `/quality/inspections/conductor` (kind AAC or ABC). |
| **Signatures** | Every inspection carries prepared by / received by / inspected by / checked by / approved by as the tender lists them per form, and a status flow draft → submitted → approved (approval needs manager privileges; approved records are read-only and can be reopened by a manager). |
| **Attachments** — photos, test results, calibration certificates | Every record (inspections, specs, equipment, complaints, CAPA, NCR, audits) has an Attachments panel backed by the shared document storage (Nextcloud or local disk). |
| **Calibration management** — equipment ID, name, location, frequency, last / next date, standard, internal / external, provider, certificate, status | *Calibration* (`/quality/calibration`). Status is computed: **valid**, **due soon** (within 30 days), **expired**, unknown (no date). Each calibration is kept as a history record; uploading a file with kind "Certificate" links it to the latest record. A daily 07:00 job refreshes the status and sends reminders. |
| **Customer complaints** — customer, product, batch, type (electrical / physical / packaging / other), description, date received, status, root cause, responsible department, supporting data | *Customer Complaints* (`/quality/complaints`), numbered `CMP-YYYY-000001`. Status open → investigating → resolved → closed (closing needs a manager). "Raise CAPA" creates a linked CAPA pre-filled from the complaint. |
| **CAPA** — initiated date, problem, actual / potential NC, cause, proposed action, corrective / preventive, conditions for closing, responsible person, target / completion date, status, approval | *CAPA* (`/quality/capa`), numbered `CAPA-YYYY-000001`. Status open / in progress / pending verification / closed, with **overdue** computed automatically when the target date passes. Approval either through the approval workflow engine (if a workflow for `capa` is configured) or directly by a manager. Closing records the effectiveness check. |
| **CAPA reports** — past-due responses, pending implementations, by requestor, by assignee | *CAPA Reminders* (`/quality/capa/reminders`) + Excel export; the daily job also sends the overdue list. |
| **NCR** — date, type (inspection / measurement / analysis), product / material, raw material / packaging / finished good / process, location, measurement, description, objective evidence, inspector signature, disposition | *Non-Conformance* (`/quality/ncr`), numbered `NCR-YYYY-000001`. **Raise NCR** appears on every failed inspection and pre-fills product, lot, failed parameters and evidence. Disposition (use as is / rework / reject / return to supplier / scrap) needs a manager; NCRs close only after a disposition. "Raise CAPA" links a CAPA. |
| **Internal audits** — schedule, standard, auditor, auditee, checklist results | *Internal Audits* (`/quality/audits`), numbered `AUD-YYYY-000001`, status planned → done → closed. Checklist items carry requirement clause, result (conforming / minor NC / major NC / observation / n.a.), evidence, and can raise a CAPA directly. |
| **Reports** (all with date range and product / supplier / machine filters, HTML + Excel) | *Reports & SPC* (`/quality/reports`) — see below. |

### Report suite

| Report | What it shows |
|---|---|
| Certificate of Analysis | Per final inspection — all test results, requirement, accuracy of determination, verdict, signatures (print / save as PDF). |
| Periodic mean & standard deviation | Daily / weekly / monthly / quarterly / annual mean, σ, min, max of every measured parameter per product. |
| Product stability analysis | Parameter trend per product with running mean, drift % between first and last value, coefficient of variation; one chart per product/parameter. |
| Supplier compliance & reputability | Inspections, pass rate, rejections, returns, replacement requests and an A/B/C rating per supplier. |
| Material yield & analysis | Input vs output vs loss vs scrap per packing summary and per product. |
| SPC control chart | Individuals chart with mean, UCL/LCL at 3σ, optional LSL/USL, Cp / Cpk, out-of-control and out-of-spec points. JSON at `/quality/reports/spc/data.json` for Chart.js. |
| Volume vs percentage defective | Inspections per period / product against the share that failed. |
| Lot history card | Every inspection, NCR and complaint touching a lot, order or drum number in time order. |
| Non-conforming summary | NCRs by item kind, disposition and status; failed inspections per inspection type. |
| Audit summary | Audit schedule (with overdue), history, checklist outcome totals and CAPAs raised from audits. |
| CAPA reminder report | Past due, pending, by requestor, by assignee, closed in last 30 days. |

Every list page (inspections, calibration, complaints, CAPA, NCR, audits) also exports to Excel.

---

## Daily reminders (07:00)

`quality_jobs.run_daily_reminders()` refreshes equipment statuses, flags overdue
CAPAs, and — per company — sends one digest listing expired / due-soon
calibrations, overdue CAPAs and complaints open longer than 14 days. Channels
are best-effort: Telegram topic *daily digest* when the bot is configured and
e-mail to `QUALITY_ALERT_EMAIL` (or `ADMIN_EMAIL`) when Resend is configured.

---

## Integration points for other modules

```python
from quality_data_store import (latest_rm_result, inspections_for_order,
                                open_quality_issues, record_procurement_sample_result)

latest_rm_result("default", material_code="CU-ROD-8", lot_no="L-0917")
# → {"released": True/False, "overall_result", "disposition", "status", "ref_no", ...} or None

inspections_for_order("default", "PO-2026-0042")
# → {"in_process": [...], "insulation": [...], "final": [...], "packing": [...], "conductor": [...]}

open_quality_issues("default")
# → {"open_ncrs", "open_capas", "overdue_capas", "open_complaints",
#    "equipment_due_soon", "equipment_expired", "failed_inspections_30d", ...}

record_procurement_sample_result("default", supplier="X", material_code="CU-ROD-8",
                                 pr_no="PR-12", result="pass", lot_no="L-0917")
# → creates a submitted raw-material inspection
```

* JSON: `GET /quality/api/open-issues`, `GET /quality/api/order/{order_number}`.
* Links into forms: `/quality/inspections/{kind}/new?order_number=…&product_type_mm2=…&machine_name=…`
  pre-fills any header field from the query string.
* Webhook events `quality.inspection_failed` and `quality.ncr_raised` are emitted
  when the webhook module is available.
* Manufacturing products / machines / production orders, Commercial customers
  and Procurement vendors populate the form pick-lists and are matched by name
  to fill the hidden id columns — only when those tables exist.

---

## Roles

* Any logged-in user: create / edit drafts, submit inspections, register
  complaints, open CAPAs, raise NCRs, plan audits, add checklist items, upload files.
* **Manager and above:** approve / reopen inspections, activate or retire
  specifications, close complaints, approve and close CAPAs, disposition and
  close NCRs, change audit status.

## Numbering

Gapless per company and year, taken inside the same transaction as the record:
inspection references (`RMI-`, `IPI-`, `WII-`, `FPI-`, `WPS-`, `CDR-`),
`COA-` (certificates), `CMP-` (complaints), `CAPA-`, `NCR-`, `AUD-`.

## Tests

```
cd web
python -m pytest tests/test_quality_logic.py tests/test_quality_templates.py -q
```
