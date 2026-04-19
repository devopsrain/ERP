# AICC ERP Security Compliance Mapping (Sections 6.1-6.8)

This document maps implemented controls in the system to AICC security requirements.

## 6.1 System Security Controls and Risk Management
- Input validation controls: Form and JSON validation across route handlers; password policy enforcement in authentication data store.
- Output data protection controls: Security response headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy).
- Authentication controls: bcrypt password hashing, login lockout thresholds, token-based API authentication.
- Authorization controls: role-based access control via privilege levels and admin/login dependency guards.
- Session management controls: secure session middleware, idle timeout in UI, logout session destruction.
- Logging and auditing: SIEM logging hooks for auth, upload, and mutation events; request-level structured logging.
- Encryption controls: HTTPS-ready headers with HSTS when secure cookies are enabled.

## 6.2 Security Risk and Vulnerability Mitigation
- Broken authentication/session controls: lockout thresholds, session clearing, token revocation support.
- IDOR mitigation: tenant/company scoping in data stores using company-aware cursors.
- Security misconfiguration reduction: startup health checks, secure defaults, middleware enforcement.
- Sensitive data exposure reduction: password hashes only, no plaintext credentials stored.
- Function-level access control: route-level privilege checks (`login_required`, `admin_required`, etc.).
- Component vulnerability process: dependency and patch process required in operations runbook.

## 6.3 Secure System Interaction
- SQL injection mitigation: parameterized SQL queries throughout data stores.
- OS command injection mitigation: no shell invocation in user-controlled route handlers.
- XSS mitigation: template escaping and strict output handling.
- CSRF mitigation: CSRF token middleware and request header checks for AJAX/HTMX.
- Malicious upload controls: extension/type controls and request size limiter middleware.
- Open redirect mitigation: validated `next` redirects (must be safe internal paths).

## 6.4 Resource and System Protection
- Request size limiting via middleware (`MAX_REQUEST_MB`, default 25MB).
- Restricted file operations to controlled app directories and export paths.
- Input checks before processing uploaded content.
- Avoidance of unsafe runtime function execution in route code.

## 6.5 User and Access Management
### 6.5.1 Separation of Duties
- Role levels support segregation (viewer/data_entry/operator/manager/admin/super_admin).

### 6.5.2 Authentication and Authorization
- Unique credentials per user, bcrypt hashing, strong password policy (length + complexity).
- Least privilege via RBAC and module-level access checks.

### 6.5.3 User Access Control
- Admin APIs for user role assignment and activation management.
- Access restrictions by module and route privilege dependency.

### 6.5.4 Security Updates and Patch Management
- Policy requirement: periodic security patching and vulnerability remediation tracking.

## 6.6 Audit Logging and Monitoring
- Activity logging includes login attempts, key transaction events, and system mutations.
- Event metadata includes user, endpoint, status, and timestamp.
- Sensitive values are not logged as plaintext secrets.
- SIEM module access restricted to authorized users.

## 6.7 Administrative Security Controls
- Admin accounts governed by the same password and lockout policy.
- Admin actions logged through audit middleware and SIEM pathways.
- Failed login attempts recorded and lockout logic enforced.

## 6.8 Session Management
- Server-managed sessions with logout invalidation.
- Inactivity timeout support in authenticated UI.
- Secure cookie and HSTS hardening available via production configuration.

## Verification Endpoints
- General health: `/health`
- DB write probe: `/api/v1/health/db-write`
- Security compliance checklist: `/api/v1/security/compliance`
