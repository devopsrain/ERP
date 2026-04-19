# AICC Support and Maintenance Plan (Section 7)

## 7.1 Warranty Services
- Minimum warranty period target: 3 years from final acceptance.
- Coverage includes application modules, integrations, configuration, and data-layer fixes.
- Included during warranty: bug fixes, performance optimization, security patching, minor enhancements, DB issue resolution.

## 7.2 Technical Support Services
- Scope: all ERP modules, integration endpoints, DB and performance incidents.
- Includes troubleshooting, root-cause analysis, tuning, and backup verification.

## 7.3 Helpdesk and User Support
- Three-tier support model:
  - L1: user assistance, triage, issue logging.
  - L2: functional/technical troubleshooting.
  - L3: engineering fixes and deployment remediation.
- Channels: email, phone, ticket portal.

## 7.4 Support Strategy and Structure
- Shared support model between AICC internal support unit and implementation vendor.
- Clear escalation matrix for critical incidents.
- Knowledge transfer and handover checkpoints per release phase.

## 7.5 Service Levels and Performance Standards
- Proposed SLA targets:
  - Critical (P1): response <= 15 minutes, workaround <= 2 hours, resolution <= 8 hours.
  - High (P2): response <= 1 hour, resolution <= 1 business day.
  - Medium (P3): response <= 4 business hours, resolution <= 3 business days.
  - Low (P4): response <= 1 business day, resolution <= 10 business days.
- Availability target: 99.5% monthly (excluding planned maintenance).

## 7.6 Remote and On-Site Support
- Remote diagnostics and monitoring as default support mode.
- On-site engagement for critical outages, infrastructure incidents, and major upgrade windows.

## 7.7 Local Support Capability
- Requirement: Ethiopia-based qualified technical support team.
- On-site mobilization commitment for critical incidents.

## 7.8 Subcontractor and Third-Party Support
- Prime vendor retains full accountability for support outcomes and service continuity.

## 7.9 Documentation and Knowledge Transfer
- Deliverables:
  - Architecture and technical documentation.
  - Administrator and user guides.
  - Troubleshooting runbooks.
- Formal knowledge transfer sessions for administrators and support staff.

## Operational Evidence
- Security/compliance status endpoint: `/api/v1/security/compliance`
- System health endpoint: `/health`
- DB write verification endpoint: `/api/v1/health/db-write`
