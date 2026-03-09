"""
Alembic migration environment.

Migrations use raw SQL via op.execute() — no SQLAlchemy ORM models are required.
DATABASE_URL is read from os.environ (same variable the app uses).

Usage:
    cd web/
    alembic upgrade head        # apply all pending migrations
    alembic downgrade -1        # roll back last migration
    alembic revision -m "desc"  # create a new migration file
"""

import os
from logging.config import fileConfig
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from the same DATABASE_URL the Flask app uses
_db_url = os.environ.get('DATABASE_URL', '')
if not _db_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Export it before running alembic."
    )
config.set_main_option('sqlalchemy.url', _db_url)


def run_migrations_offline() -> None:
    """Generate a SQL script without connecting to the DB."""
    context.configure(
        url=_db_url,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live DB connection."""
    from sqlalchemy import create_engine

    engine = create_engine(_db_url)
    with engine.connect() as conn:
        context.configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
