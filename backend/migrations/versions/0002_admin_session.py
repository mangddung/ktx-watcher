"""admin_session

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27 04:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('admin_session',
    sa.Column('uid', sa.BigInteger(), nullable=False),
    sa.Column('sid', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('uid')
    )


def downgrade() -> None:
    op.drop_table('admin_session')
