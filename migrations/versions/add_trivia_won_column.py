"""Add won column to trivia_wins table

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6g7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trivia_wins", sa.Column("won", sa.Boolean(), nullable=True, server_default=sa.text("false")))


def downgrade():
    op.drop_column("trivia_wins", "won")
