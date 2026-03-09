# Alembic Database Migrations

## Setup

Install dependencies (already in requirements.txt):

    pip install alembic sqlalchemy

## Common Commands

Run all pending migrations against the live DB:

    cd web/
    alembic upgrade head

Roll back the last migration:

    cd web/
    alembic downgrade -1

Show current migration state:

    cd web/
    alembic current

Create a new migration file:

    cd web/
    alembic revision -m "add_some_column"

## Writing Migrations

Migrations use **plain SQL** via `op.execute()` — no SQLAlchemy ORM models needed.

```python
def upgrade() -> None:
    op.execute("ALTER TABLE bid_records ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'")

def downgrade() -> None:
    op.execute("ALTER TABLE bid_records DROP COLUMN priority")
```

## Notes

- `DATABASE_URL` env var must be set (same as the app uses).
- `init_db.sql` contains the initial schema. Use Alembic migrations for any changes **after** first deployment.
- Migrations are idempotent — use `IF NOT EXISTS` / `IF EXISTS` guards in SQL.
- The server's hotfix_server.ps1 will run `alembic upgrade head` as part of deployments.
