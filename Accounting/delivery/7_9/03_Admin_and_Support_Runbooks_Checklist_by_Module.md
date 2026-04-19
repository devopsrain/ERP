# AICC ERP Section 7.9
# Admin and Support Runbooks Checklist by Module

## 1. Usage
This checklist is used by admin and support teams to confirm that each module has complete operational runbooks, recovery steps, and support coverage.

Legend:
- Y = Available
- P = Partial
- N = Not available

## 2. Core Runbook Requirements (Apply to All Modules)

| Check | Description | Status | Evidence |
|---|---|---|---|
| RB-01 | Access and role prerequisites documented | | |
| RB-02 | Daily operational checklist documented | | |
| RB-03 | Common errors and fixes documented | | |
| RB-04 | Incident escalation path documented | | |
| RB-05 | Backup/restore impact documented | | |
| RB-06 | Security controls and audit events documented | | |
| RB-07 | Data export and retention procedure documented | | |
| RB-08 | DR and rollback steps documented | | |

## 3. Module Checklist Matrix

| Module | Admin Guide | L1 Runbook | L2/L3 Runbook | Security Controls | Audit Events | Backup/Restore | Export Procedures | SLA Mapping |
|---|---|---|---|---|---|---|---|---|
| Authentication | | | | | | | | |
| Provider Admin | | | | | | | | |
| Sales | | | | | | | | |
| Multi-Company | | | | | | | | |
| VAT | | | | | | | | |
| Journal Entries | | | | | | | | |
| Chart of Accounts | | | | | | | | |
| Income and Expense | | | | | | | | |
| Transactions | | | | | | | | |
| CPO | | | | | | | | |
| Inventory | | | | | | | | |
| Bid Tracker | | | | | | | | |
| SIEM | | | | | | | | |
| Backup and Archive | | | | | | | | |
| Version Control | | | | | | | | |
| REST API v1 | | | | | | | | |
| Letters and E-Signatures | | | | | | | | |
| LMS | | | | | | | | |
| Machinery and Equipment | | | | | | | | |
| Human Resource Management | | | | | | | | |
| Finance Management | | | | | | | | |

## 4. Per-Module Runbook Template

Use this structure for each module runbook.

1. Scope and Purpose
2. Required Roles and Permissions
3. Startup and Health Verification Steps
4. Normal Daily Operations
5. Data Entry and Validation Rules
6. Common Failure Scenarios and Fixes
7. Escalation Triggers and Contacts
8. Security Events to Monitor
9. Log Sources and Queries
10. Backup, Restore, and Rollback Steps
11. Service Restart and Recovery Sequence
12. Known Limitations and Workarounds
13. Evidence and Reporting Artifacts

## 5. L1/L2/L3 Escalation Standard

| Severity | Example | L1 Action | L2 Action | L3 Action | Response Target | Resolution Target |
|---|---|---|---|---|---|---|
| Critical | System down, data corruption risk | Triage and notify | Restore service path | Code/config fix | 15 min | 8h |
| High | Major function unavailable | Collect logs and reproduce | Mitigate and patch | Permanent fix | 1h | 1 business day |
| Medium | Partial degradation | User workaround | Root cause analysis | Scheduled fix | 4h | 3 business days |
| Low | Cosmetic or minor issue | Record and queue | Validate scope | Bundled release | 1 business day | 10 business days |

## 6. Sign-Off Checklist

| Item | Module Owner | Support Lead | Security Lead | AICC Reviewer | Date |
|---|---|---|---|---|---|
| Admin Guide Approved | | | | | |
| L1/L2/L3 Runbooks Approved | | | | | |
| Security and Audit Coverage Approved | | | | | |
| DR and Recovery Drill Passed | | | | | |
| Production Readiness Approved | | | | | |
