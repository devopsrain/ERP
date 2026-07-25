# AICC ERP Security Compliance Mapping (Sections 6.1-6.8)

This document maps implemented controls in the system to AICC security requirements.

## 6.1 System Security Controls and Risk Management
- Input validation controls: Form and JSON validation across route handlers; central password policy (`validate_password` in `web/auth_data_store.py`) enforced on registration, self-service password change, admin password reset, and token-based reset.
- Output data protection controls: Security response headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy).
- Encryption in transit: TLS terminated at nginx with Let's Encrypt certificates (auto-renewed); HSTS enabled with secure cookies.
- Authentication controls: bcrypt password hashing (legacy SHA-256 hashes transparently upgraded on login), account lockout after 5 failed attempts (30-minute lockout), token-based API authentication, JWT with refresh-token rotation.
- Authorization controls: role-based access control via privilege levels and admin/login dependency guards.
- Session management controls: server-side sessions stored in Redis with 30-minute idle expiry; logout destroys the session.
- Logging and auditing: SIEM logging hooks for auth, upload, admin, and mutation events; request-level structured logging.
- Data isolation: PostgreSQL Row-Level Security (RLS) enforces tenant/company isolation at the database layer.

## 6.2 Security Risk and Vulnerability Mitigation
- Broken authentication/session controls: lockout thresholds, Redis server-side session expiry (30-minute idle), session clearing on logout, refresh/API token revocation.
- Password-related risk reduction: minimum 10-character passwords with upper/lower/digit complexity; reuse of the last 3 passwords rejected (bcrypt comparison against `auth_password_history`); passwords older than 180 days trigger a change prompt at login.
- IDOR mitigation: tenant/company scoping in data stores using company-aware cursors, backed by Postgres RLS.
- Security misconfiguration reduction: startup health checks, secure defaults, middleware enforcement.
- Sensitive data exposure reduction: only bcrypt hashes stored; passwords never written to logs or SIEM events.
- Function-level access control: route-level privilege checks (`login_required`, `admin_required`, `super_admin_required`).
- Component vulnerability process: all Python dependencies pinned; Azure Pipelines CI rebuilds and tests on every change (see 6.5.4).

## 6.3 Secure System Interaction

| Threat | Implemented Control |
|---|---|
| SQL injection | Parameterized queries (psycopg2 placeholders / asyncpg positional parameters) throughout all data stores — no string-concatenated SQL with user input. |
| XSS | Jinja2 autoescaping on all templates plus Content-Security-Policy and related security response headers. |
| CSRF | Dual token strategy — per-session synchronizer token embedded in forms (`csrf_token()` from `web/deps.py`) plus request-header token check for AJAX/HTMX calls. |
| Malicious file upload | Central `validate_upload()` helper (`web/deps.py`): rejects dangerous executable/script extensions (`.exe .bat .cmd .sh .ps1 .php .js .jar .msi .dll .scr .vbs`) even when whitelisted, enforces per-route extension whitelists (e.g. bid documents), and rejects empty files; applied to communication and bid uploads alongside the request size limiter middleware. |
| Open redirect | Login `next` parameter validated as a safe internal path (must start with `/`, must not start with `//`) before any redirect (`web/auth_routes.py`). |
| OS command injection | No shell invocation in user-controlled route handlers. |

## 6.4 Resource and System Protection
- File access via ID-mapped database lookups, never raw client-supplied paths: downloads (bid documents, communication files) resolve an opaque file/document ID to a server-side storage path stored in the database, preventing path traversal; uploads are stored under random server-generated names in controlled app directories.
- Request size limiting via middleware (`MAX_REQUEST_MB`, default 25MB) protects against resource-exhaustion uploads and oversized payloads.
- Database statement timeouts bound the runtime of any single query, preventing runaway or maliciously expensive statements from exhausting the database.
- Restricted file operations to controlled app directories and export paths.
- Input checks before processing uploaded content (`validate_upload`, see 6.3).
- Avoidance of unsafe runtime function execution in route code.
- Nightly backups with automated restore verification.

## 6.5 User and Access Management
### 6.5.1 Separation of Duties
- Role hierarchy enforces segregation (viewer/data_entry/operator/manager/admin/super_admin), with per-module minimum privilege levels and route-level dependency guards.
- Approval workflows split preparation from authorization:
  - **Procurement** — purchase requests and procurement plans are created by requesters and require approval by a separate, higher-privileged approver before execution.
  - **Leave management** — leave requests are submitted by employees and approved by managers.
  - **Journal/accounting** — journal entry creation (data_entry) is separated from posting/approval (higher privilege), so no single role both records and authorizes financial entries.
- No single role controls approve + execute + monitor: execution roles (data_entry/operator) cannot approve, approval roles do not perform data entry of their own approvals, and security monitoring (SIEM module) is restricted to admin-level users — providing independent oversight of both.
- Self-service restrictions reinforce SoD: users cannot delete their own accounts; user deletion requires super_admin.

### 6.5.2 Authentication and Authorization
- Unique credentials per user; bcrypt password hashing.
- Central password policy (`validate_password`): minimum 10 characters, at least one uppercase letter, one lowercase letter, and one digit; passwords may not contain the username. Enforced server-side on registration, password change, admin reset, and email-token reset; mirrored client-side in the auth templates.
- Password reuse prevention: new passwords are bcrypt-compared against the last 3 hashes in the `auth_password_history` table and rejected on match.
- Password expiry: `users.password_changed_at` is stamped on every change; at login, passwords older than 180 days set a session flag and prompt the user to change (warn, not block).
- Least privilege via RBAC and module-level access checks; SIEM module restricted to admins.

### 6.5.3 User Access Control
- Admin APIs for user creation, role assignment, activation/deactivation, deletion, and password reset — all restricted to admin/super_admin roles.
- Every sensitive admin action (user creation, role change, activation toggle, deletion, admin password reset) is logged to the SIEM event log and raises a severity-`medium` SIEM alert with actor, target, IP, and timestamp.
- Access restrictions by module and route privilege dependency; tenant isolation via Postgres RLS.

### 6.5.4 Security Updates and Patch Management
- All Python dependencies are version-pinned; upgrades are deliberate, reviewed changes.
- Azure Pipelines CI builds and tests every change before deployment, providing a controlled patch/update channel.
- Periodic security patching and vulnerability remediation tracked in the operations runbook; OS/nginx patches applied via the documented server update workflow.

## 6.6 Audit Logging and Monitoring
- Activity logging includes login attempts (success and failure with reason), logouts, admin actions, key transaction events, and system mutations.
- Event metadata includes username, source IP (X-Forwarded-For aware), user agent, endpoint, status, and timestamp.
- Passwords and secrets are never logged — only bcrypt/SHA-256 hashes are stored, and SIEM event details contain no credential material.
- SIEM alerting: rule-based alerts (large uploads, rapid uploads) plus severity-`medium` alerts for all sensitive admin actions; alerts carry severity, rule, message, IP, and acknowledgement state.
- SIEM module access restricted to admin-level users only.
- Login history retained per user (timestamp, IP, device).

## 6.7 Administrative Security Controls
- Admin accounts governed by the same password policy, reuse prevention, expiry prompt, and lockout policy as all users.
- Admin actions (user create/delete, role changes, activation toggles, password resets) are logged through SIEM pathways and additionally raise severity-`medium` SIEM alerts for monitoring and review.
- Failed login attempts recorded; lockout after 5 failures for 30 minutes.
- Deletion of one's own account is blocked; user deletion requires super_admin.

## 6.8 Session Management
- Unique session identifiers: each session is keyed by a 256-bit random ID (`secrets.token_hex(32)`); the cookie carries only this opaque ID — all session data lives server-side in Redis (`web/session_store.py`), so no session state is exposed to the client.
- **Session-fixation protection:** a new session ID is issued on every successful login — `set_session` sets a rotation flag honored by `RedisSessionMiddleware`, which deletes the old Redis key, generates a fresh sid, re-saves the session under the new key, and sets the new cookie on the same response, so a pre-authentication session ID can never be replayed.
- Encryption in transit: session cookies travel only over TLS (nginx termination, Let's Encrypt, HSTS enabled).
- Idle timeout: 30-minute session freshness window enforced on the authenticated portal; stale sessions are cleared and re-authentication required.
- Logout invalidation destroys the server-side session — the session is cleared and its Redis key deleted, immediately invalidating the session ID.
- Cookie hardening: `HttpOnly` (no JavaScript access), `SameSite=Lax` (CSRF containment), and `Secure` in production behind nginx TLS.

## Verification Endpoints
- General health: `/health`
- DB write probe: `/api/v1/health/db-write`
- Security compliance checklist: `/api/v1/security/compliance`
