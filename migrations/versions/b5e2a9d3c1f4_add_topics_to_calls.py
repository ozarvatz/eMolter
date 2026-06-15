"""Add topics JSON column to calls

Revision ID: b5e2a9d3c1f4
Revises: a4f7c821b9e3
Create Date: 2026-06-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b5e2a9d3c1f4'
down_revision = 'a4f7c821b9e3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('topics', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.drop_column('topics')
