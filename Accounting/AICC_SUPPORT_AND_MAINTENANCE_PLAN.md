# AICC Support and Maintenance Plan (Section 7)

## 7.1 Warranty Services

### 7.1.1 Warranty Commitment
A **3-year (36-month) full warranty** is provided on the complete delivered system, commencing at **final acceptance**. During the warranty period all defects are resolved **at no cost to AICC** — no additional license, labor, travel, or material charges apply to warranty work.

### 7.1.2 Warranty Schedule
| Component | Warranty Start | Warranty End | Coverage |
|---|---|---|---|
| EBMS application (all functional modules) | Final acceptance | Final acceptance + 36 months | Full |
| REST APIs (v1 and v2/mobile) and integrations | Final acceptance | Final acceptance + 36 months | Full |
| Database layer (PostgreSQL schema, RLS policies, migrations) | Final acceptance | Final acceptance + 36 months | Full |
| Deployment/operations tooling (`deploy/*` scripts, backup, monitoring, CI pipeline) | Final acceptance | Final acceptance + 36 months | Full |
| Security components (auth, session management, SIEM module) | Final acceptance | Final acceptance + 36 months | Full |
| Subcontracted / third-party developed parts | Final acceptance | Final acceptance + 36 months | Full — prime contractor responsibility (see 7.8) |

### 7.1.3 Coverage Scope
- Coverage is **complete**: application modules, integrations, configuration, data-layer fixes, and any subcontracted or third-party developed components. The prime contractor remains the single warranty counterpart regardless of who built a component.
- Included during warranty, at no cost:
  - **Bug fixes** — code-level defect resolution with regression testing.
  - **Performance optimization** — query tuning, index maintenance, and application performance fixes when agreed thresholds are not met.
  - **Security patches** — application-level security fixes and dependency updates addressing identified vulnerabilities.
  - **Minor enhancements** — configuration changes, report adjustments, and small functional improvements.
  - **Database maintenance and issue resolution** — schema fixes, data-integrity corrections, and recovery support.
- Warranty work is delivered through the standard release process (git/Azure Pipelines CI with regression testing) and tracked under the SLA targets in 7.5.

## 7.2 Technical Support Services

### 7.2.1 Scope of Support
Technical support covers the complete EBMS ERP platform as delivered, including **all functional modules**: Chart of Accounts, Journal Entries, Income & Expense, VAT Portal, Transactions, Finance Management (statements, cost centers, AR/AP registers), Payroll (Ethiopian tax/pension rules), Inventory, Procurement, CPO, Bid Tracker, HRM, LMS, Machinery & Equipment, Letters & E-Signatures, Projects, Contracts, Events, Communication, Multi-Company administration, and the REST APIs (v1 and v2/mobile).

Support services include:

- **Troubleshooting and root-cause analysis** — application errors, integration failures, and data discrepancies, using container logs (`docker compose logs`), the SIEM audit-log module, and the built-in health endpoints (`/health`, `/api/v1/health/db-write`).
- **Database administration (DBA)** — PostgreSQL maintenance, index and query tuning, storage management, schema migration support, and recovery operations.
- **Performance monitoring** — proactive review of system health via the `deploy/status.sh` one-shot health report (system load, disk, memory, container status, app endpoints, TLS certificate expiry, DB size, error counts in the last 24 h), the Prometheus `/metrics` endpoint, and the SIEM security monitoring module.
- **Backup verification** — confirmation that the **nightly automated backups** (PostgreSQL `pg_dump` plus application-data archive covering uploaded bid documents, letters/signatures and exports, registered in cron by `deploy/setup-cron.sh`, with **30-day retention** and a disk-space guard) completed successfully, plus periodic test restores to a staging database.
- **Minor enhancements** — configuration changes, report adjustments, and small functional improvements delivered through the standard git-based release process (Azure Pipelines CI, git-based deploys).

### 7.2.2 Operational Tooling Underpinning Support
| Capability | Implementation |
|---|---|
| Health reporting | `deploy/status.sh` — single-command server + app health report |
| Backups | `deploy/backup.sh` — nightly `pg_dump` + app-data volume archive, 30-day retention |
| Security monitoring | Built-in SIEM module (audit logs, security events) |
| CI / release pipeline | Azure Pipelines (`azure-pipelines.yml`), git-based deploys to the server |
| Secure remote support | Tailscale-secured (WireGuard) private network access — no public admin ports |
| Compliance evidence | `/api/v1/security/compliance` endpoint |

## 7.3 Helpdesk and User Support

### 7.3.1 Three-Tier Support Model
| Tier | Owner | Responsibilities |
|---|---|---|
| **L1 — User Assistance & Logging** | AICC internal support unit | First point of contact; user guidance ("how do I…"), password/account assistance, issue logging and classification, ticket creation, resolution of known issues from the knowledge base and runbooks. |
| **L2 — Technical Support** | Vendor support engineers | Functional and technical troubleshooting, configuration fixes, data corrections, DB queries, log analysis, performance investigation, workaround provision. |
| **L3 — Code-Level Fixes** | Vendor engineering team | Source-code defect fixes, schema changes, security patches, and enhancement development; delivered via the git/Azure Pipelines CI pipeline and deployed through the standard release process with regression testing (including the template render-test harness). |

### 7.3.2 Support Channels
- **Phone hotline** — direct line to the L1 desk for urgent issues (Critical incidents must be phoned in to start the SLA clock immediately).
- **Email** — dedicated support mailbox; auto-acknowledged and converted to a ticket.
- **In-system ticket** — raised directly inside EBMS via the notifications module (in-app notification center with per-user, per-company routing), so users can report issues without leaving the application.

### 7.3.3 Coverage Hours
- **Standard coverage:** business hours **08:30–17:30 EAT**, Monday–Friday (Ethiopian public holidays excluded).
- **Extended coverage (optional):** on-call arrangement outside business hours for Critical (P1) incidents — phone hotline escalation to the on-call engineer, remote access via the Tailscale network.

## 7.4 Support Strategy and Structure

### 7.4.1 Shared Support Model
- **AICC internal support unit — L1.** Staffed by AICC administrators trained during knowledge transfer; owns user assistance, triage, ticket logging, and first-line resolution from runbooks.
- **Vendor — L2/L3.** Owns technical troubleshooting, DBA work, code-level fixes, security patching, and releases. Remote support is performed over the Tailscale-secured private network; on-site engagement per Section 7.6.

### 7.4.2 Escalation Chain
| Step | Escalated to | Trigger / Timeframe |
|---|---|---|
| 1 | L1 → L2 (vendor support engineer) | Immediately for Critical/High; within 4 business hours if L1 cannot resolve Medium/Low |
| 2 | L2 → L3 (vendor engineering) | Within 2 hours for Critical if no workaround; within 1 business day for High |
| 3 | L3 → Vendor delivery manager | Critical unresolved after 4 hours, or any SLA breach |
| 4 | Delivery manager → AICC management + vendor executive sponsor | Critical unresolved after 8 hours; repeated SLA breaches; monthly service review items |

### 7.4.3 Knowledge Transfer
- **Administrator training** — formal sessions for AICC system administrators and L1 staff covering module administration, user/role management, and multi-company setup.
- **Runbooks** — operational runbooks handed over and maintained, including the deployment/update workflow (git pull + rebuild), the `deploy/status.sh` health report, backup operation and **backup verification / test-restore procedures**, TLS certificate management, and Tailscale access management.
- **Quarterly service reviews** — joint AICC/vendor reviews of ticket trends, SLA performance, backup verification results, capacity/performance data, and the enhancement backlog; runbooks and the knowledge base updated after each review.
- **Handover checkpoints per release phase** — each release ships with updated documentation and a changelog; knowledge transfer is re-validated at each checkpoint.

## 7.5 Service Levels and Performance Standards

### 7.5.1 SLA Targets by Severity
| Severity | Definition | Response Time | Resolution Target |
|---|---|---|---|
| **Critical** | System down, data loss, or a core business process blocked for all users; no workaround | **1 hour** | **8 hours** |
| **High** | Major function impaired or a group of users blocked; workaround difficult | **4 hours** | **24 hours** |
| **Medium** | Single function or user affected; reasonable workaround exists | **1 business day** | **3 business days** |
| **Low** | Cosmetic issues, questions, minor enhancement requests | **2 business days** | **Next scheduled release** |

Notes:
- Response = qualified acknowledgement by the responsible tier with an action plan; times are measured within coverage hours (Critical incidents under the extended on-call option are measured continuously).
- Resolution = permanent fix or an agreed workaround with a scheduled permanent fix.

### 7.5.2 Performance Standards
- **Availability target:** 99.5% monthly (excluding planned maintenance windows, announced ≥ 48 h in advance).
- **Backup success:** nightly backup completion verified daily via `deploy/status.sh` (latest backups listed); test restore performed at least quarterly.
- **Monitoring evidence:** `/health`, `/api/v1/health/db-write`, Prometheus `/metrics`, and SIEM event review; monthly SLA and availability reporting at the service review.

### 7.5.3 Average Resolution Times by Failure Type
Beyond the per-severity SLA ceilings in 7.5.1, the following **average** resolution times are committed by failure type (measured and reported at the monthly service review):

| Failure Type | Typical Severity | Average Resolution Time |
|---|---|---|
| Application error / module malfunction (500s, broken workflow) | High | 8 working hours |
| Data discrepancy / report inaccuracy | Medium | 1–2 business days |
| Database issue (locking, corruption, migration failure) | Critical/High | 4–12 hours |
| Integration/API failure (REST v1/v2, mobile) | High | 8 working hours |
| Performance degradation (slow pages, slow queries) | Medium | 2 business days |
| Security incident (suspicious SIEM alert, vulnerability) | Critical/High | Containment within 4 hours; fix per severity SLA |
| Infrastructure/container failure (Docker, nginx, TLS) | Critical | 2–4 hours |
| User access / account issue | Low/Medium | Same business day |
| Cosmetic / UI defect | Low | Next scheduled release |

### 7.5.4 Critical Outage Procedure
1. **Report** — Critical (P1) incidents are phoned in to the hotline (7.3.2), which starts the SLA clock immediately; a ticket is created in parallel.
2. **Mobilize** — L2 engineer engages within the 1-hour response target; remote access established over the Tailscale network; L3 engaged within 2 hours if no workaround exists (escalation chain 7.4.2).
3. **Diagnose** — `deploy/status.sh` health report, container logs, `/health` and `/api/v1/health/db-write` probes, Prometheus `/metrics`, and SIEM events used to isolate the fault.
4. **Restore** — service restoration prioritized over root-cause elimination: rollback via the git-based release process, container restart, or restore from the nightly backup set as needed.
5. **Resolve and review** — permanent fix within the 8-hour resolution target; a post-incident report (timeline, root cause, corrective and preventive actions) is delivered within 3 business days and reviewed at the next service review.
6. **On-site dispatch** — if the outage cannot be resolved remotely, an engineer is dispatched on-site per the commitments in 7.6/7.7.

### 7.5.5 Incident Management
- Every incident is logged as a ticket with severity, timestamps, owner, and resolution notes — the ticket record is the SLA measurement source.
- Status updates to AICC: Critical — hourly until restored; High — twice daily; Medium/Low — on state change.
- Recurring incidents (same root cause ≥ 3 times per quarter) are escalated to problem management and tracked to permanent elimination.
- Incident trends, SLA attainment, and average resolution times per failure type are reported monthly and reviewed quarterly (7.4.3).

## 7.6 Remote and On-Site Support

### 7.6.1 Remote Support (default mode)
- Remote diagnostics and maintenance are performed over the **Tailscale-secured (WireGuard) private network** — no administration interfaces are exposed publicly; access is limited to enrolled, authenticated support devices (`deploy/setup-tailscale.sh`).
- **Online diagnostics** available to support engineers without user disruption:
  - `deploy/status.sh` — single-command health report (system load, disk, memory, container status, app endpoints, TLS expiry, DB size, last-24h error counts, latest backups).
  - Built-in **SIEM module** — audit logs, security events, and alert review.
  - **Prometheus `/metrics`** endpoint plus `/health` and `/api/v1/health/db-write` probes for live monitoring.
- Most L2/L3 work (troubleshooting, DBA, patches, releases) is completed remotely, keeping resolution times short.

### 7.6.2 On-Site Support
On-site engagement is provided for:
- **Critical system failures** that cannot be restored remotely (dispatch per 7.5.4 and the availability targets in 7.7).
- **Hardware and infrastructure incidents** at the hosting location.
- **Major upgrade windows** and planned maintenance requiring physical presence.
- Scheduled on-site visits (e.g. quarterly service reviews, training sessions) as agreed with AICC.

## 7.7 Local Support Capability (Ethiopia)
An **Ethiopia-based, qualified technical support team** is committed for the full support and maintenance period (aligned with the detailed capability package in `delivery/7_9/07_7_Local_Support_Capability_Ethiopia.md`):

- **Local team profile** — Support Lead, L1 analysts, L2 engineers, and a field engineer, all based in Ethiopia, covering business hours with on-call and on-site dispatch capability.
- **Capability commitments:**
  - Local first response for priority incidents.
  - Timely on-site intervention for critical cases.
  - Continuous staffing coverage during the contract period; qualified personnel retained or replaced with an equivalent profile.
- **On-site availability targets:** Critical — per the contract emergency window; High — same day where feasible; Medium/Low — scheduled support window.
- **Evidence package** provided with the bid: team CVs and certifications, local office proof and contact channels, employment/engagement letters, and OEM/developer support commitment letters where applicable.

## 7.8 Subcontractor and Third-Party Support
- The **prime contractor retains full, undivided responsibility** for all support and warranty outcomes, including work performed by subcontractors or third parties and any third-party-developed components — AICC always has a single accountable counterpart.
- Subcontractor obligations (SLAs, warranty terms, escalation duties, confidentiality/data-protection) are flowed down contractually; subcontractor performance is monitored by the prime contractor and reported at service reviews.
- **Service continuity guarantee:** if a subcontractor or third party fails, withdraws, or is replaced, the prime contractor assumes or re-assigns the work without interruption to support services or degradation of SLA commitments; AICC-facing contacts, escalation paths, and SLAs remain unchanged.
- Any change of subcontractor is notified to AICC in advance, with knowledge transfer completed before handover.

## 7.9 Documentation and Knowledge Transfer

### 7.9.1 Documentation Deliverables
| Deliverable | Content |
|---|---|
| **Administrator guide** | Installation/deployment, configuration, user and role management, multi-company setup, TLS and Tailscale access management |
| **User guides (per module)** | Task-oriented guides for each functional module (accounts, journal, VAT, payroll, inventory, procurement, CPO, bids, HRM, LMS, projects, contracts, communication, etc.) |
| **API documentation** | Interactive REST API reference published in-system at `/api/docs` (OpenAPI), covering v1 and v2/mobile endpoints |
| **Operations runbooks** | Deployment/update workflow (git pull + rebuild), `deploy/status.sh` health checks, backup operation and test-restore verification, incident and recovery procedures |
| **Database schema reference** | Table/column reference, RLS/tenancy model, and migration notes |
| **Training materials** | Slide decks, exercise data sets, and hands-on lab instructions used in the knowledge transfer sessions |

All documentation is versioned with the source code; each release ships with updated documentation and a changelog (see 7.4.3).

### 7.9.2 Knowledge Transfer Program
- **Formal training sessions** for AICC system administrators and L1 support staff: module administration, user/role management, multi-company setup, and operational tooling.
- **Operations handover** — supervised execution of the runbooks (deploy, health check, backup/restore) by AICC staff before acceptance.
- **Handover checkpoints per release phase** with re-validation of transferred knowledge; competence sign-off recorded per the knowledge transfer plan (`delivery/7_9/02_Knowledge_Transfer_Plan_Schedule_Curriculum_and_Signoff.md`).
- **Continuous transfer** — knowledge base and runbooks updated after each quarterly service review.

## Operational Evidence
- Security/compliance status endpoint: `/api/v1/security/compliance`
- System health endpoint: `/health`
- DB write verification endpoint: `/api/v1/health/db-write`
- Health report script: `deploy/status.sh`
- Nightly backup script (pg_dump + app data, 30-day retention): `deploy/backup.sh` (scheduled by `deploy/setup-cron.sh`)
- CI pipeline: `azure-pipelines.yml` (Azure Pipelines)
- Secure remote access: `deploy/setup-tailscale.sh` (Tailscale/WireGuard)
