"""Add device_name column to login_history

Revision ID: 0001
Revises:
Create Date: 2026-03-11

Adds `device_name TEXT NOT NULL DEFAULT 'Unknown'` to `login_history`.
This column stores a human-readable device/browser string parsed from the
User-Agent header at login time (e.g. "Mobile · Android · Chrome").
"""
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE login_history
        ADD COLUMN IF NOT EXISTS device_name TEXT NOT NULL DEFAULT 'Unknown'
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE login_history
        DROP COLUMN IF EXISTS device_name
    """)
