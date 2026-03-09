"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
# import sqlalchemy as sa  # uncomment if needed for DDL helpers

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    # Write raw SQL changes here, e.g.:
    # op.execute("ALTER TABLE my_table ADD COLUMN new_col TEXT NOT NULL DEFAULT ''")
    pass


def downgrade() -> None:
    # op.execute("ALTER TABLE my_table DROP COLUMN new_col")
    pass
