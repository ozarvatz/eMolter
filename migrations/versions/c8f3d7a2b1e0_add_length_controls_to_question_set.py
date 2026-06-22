"""Add length controls to question_sets (adaptive call length feature)

Revision ID: c8f3d7a2b1e0
Revises: b5e2a9d3c1f4
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c8f3d7a2b1e0'
down_revision = 'b5e2a9d3c1f4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('question_sets', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'length_mode', sa.String(length=20),
            nullable=False, server_default='by_count',
        ))
        batch_op.add_column(sa.Column(
            'target_seconds', sa.Integer(), nullable=True,
        ))
        batch_op.add_column(sa.Column(
            'max_questions', sa.Integer(),
            nullable=False, server_default='10',
        ))
        batch_op.add_column(sa.Column(
            'extension_strategy', sa.String(length=20),
            nullable=False, server_default='engagement',
        ))


def downgrade():
    with op.batch_alter_table('question_sets', schema=None) as batch_op:
        batch_op.drop_column('extension_strategy')
        batch_op.drop_column('max_questions')
        batch_op.drop_column('target_seconds')
        batch_op.drop_column('length_mode')
