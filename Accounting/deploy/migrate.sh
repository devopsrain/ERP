#!/bin/bash
# deploy/migrate.sh — Explicit, loud schema migration step.
#
# Run this after EVERY `git pull`, before/after `docker compose up -d --build`:
#
#     cd /opt/ebms/Accounting
#     git pull
#     bash deploy/migrate.sh
#     docker compose up -d --build web api
#
# Why: the app also runs init_db.sql at startup (web/db_setup.py), but it does
# so in ONE transaction — a single bad statement silently rolls back the whole
# file and strands every later column/table.  This script applies the same
# file statement-by-statement (ON_ERROR_STOP=0), so one failing statement
# doesn't block the rest, and it prints a clear PASS/FAIL summary of every
# ERROR psql reports.  Exits nonzero on any error so CI/deploy scripts stop.
set -u

cd "$(dirname "$0")/.." || exit 1

SQL_FILE="aws-deployment/init_db.sql"
if [ ! -f "$SQL_FILE" ]; then
    echo "FAIL: $SQL_FILE not found (run from the repo root: /opt/ebms/Accounting)" >&2
    exit 1
fi

echo "── Applying $SQL_FILE via docker compose exec postgres ──"

ERR_LOG="$(mktemp)"
trap 'rm -f "$ERR_LOG"' EXIT

# Pipe the file into psql with -f - : psql splits statements properly,
# including DO $$ ... $$ blocks (a naive per-line split would not).
# ON_ERROR_STOP=0 → keep going past individual failures so every other
# statement still lands; we grade the run from stderr afterwards.
docker compose exec -T postgres psql -U ebms -d ebms \
    -v ON_ERROR_STOP=0 -f - < "$SQL_FILE" 2> "$ERR_LOG"
PSQL_RC=$?

# Show psql's stderr (notices, errors) so nothing is hidden.
if [ -s "$ERR_LOG" ]; then
    echo "── psql stderr ──"
    cat "$ERR_LOG"
fi

ERRORS=$(grep -c 'ERROR' "$ERR_LOG" || true)

echo
echo "══ MIGRATION SUMMARY ═══════════════════════════════════"
if [ "$PSQL_RC" -ne 0 ]; then
    echo "FAIL: psql exited with code $PSQL_RC (could not run migration — is the postgres container up?)"
    exit "$PSQL_RC"
elif [ "$ERRORS" -gt 0 ]; then
    echo "FAIL: $ERRORS ERROR line(s) reported by psql — see stderr above."
    echo "      Other statements were still applied (ON_ERROR_STOP=0),"
    echo "      but fix the failing statement(s) in $SQL_FILE."
    exit 1
else
    echo "PASS: schema applied cleanly, 0 errors."
fi
