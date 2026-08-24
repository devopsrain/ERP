"""
create_admin.py — create or repair a super_admin account, idempotently.

Run inside the web container (from /opt/ebms/Accounting):

    docker compose exec web python create_admin.py admin 'YourPass123'

- If the username doesn't exist: creates it as an active super_admin.
- If it exists: resets the password, promotes to super_admin, reactivates,
  unlocks, and clears failed-login counters.
- Uses the app's own hashing (_hash_password) so the format always matches
  what login verification expects.
- Verifies the result by running the real credential check before exiting.
"""
import sys

from auth_data_store import auth_store, _hash_password, validate_password
from db import execute, fetchone


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python create_admin.py <username> <password>")
        return 2
    username, password = sys.argv[1].strip(), sys.argv[2]

    ok, err = validate_password(password)
    if not ok:
        print(f"REFUSED — password policy: {err}")
        return 2

    row = fetchone("SELECT user_id, username FROM users WHERE username=%s", (username,))
    if row:
        n = execute(
            """UPDATE users SET password_hash=%s, privilege_level='super_admin',
               is_active=TRUE, failed_login_count=0, locked_until=''
               WHERE username=%s""",
            (_hash_password(password), username),
        )
        print(f"REPAIRED existing user '{username}' (rows updated: {n}) — "
              "password reset, promoted to super_admin, unlocked.")
    else:
        result = auth_store.create_user(
            username=username, password=password,
            full_name="System Administrator", email="",
            phone="", privilege_level="super_admin", company_id="default",
        )
        if not result.get("success"):
            print(f"FAILED to create user: {result.get('error')}")
            return 1
        print(f"CREATED new super_admin '{username}'.")

    # Prove it: run the app's actual login check
    user = auth_store.authenticate(username, password)
    if user:
        print(f"VERIFIED — login check passes for '{username}' "
              f"(privilege: {user.get('privilege_level')}). You can log in now.")
        return 0
    print("WARNING — user saved but the login check still fails. "
          "Run:  docker compose logs web --tail 30   and share the output.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
