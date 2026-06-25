"""add turn_instruction to question_sets

Revision ID: 5e92c4b81001
Revises: d4a7c2e8b9f1
Create Date: 2026-06-25 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '5e92c4b81001'
down_revision = 'd4a7c2e8b9f1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('question_sets', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'turn_instruction', sa.Text(), nullable=True,
        ))


def downgrade():
    with op.batch_alter_table('question_sets', schema=None) as batch_op:
        batch_op.drop_column('turn_instruction')
