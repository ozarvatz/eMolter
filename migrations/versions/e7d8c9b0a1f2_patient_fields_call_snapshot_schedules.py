"""Add patient fields (gender/birth_year/treatment/utm_params), call snapshot columns, and call_schedules table

Revision ID: e7d8c9b0a1f2
Revises: c9f1a2b3d4e5
Create Date: 2026-05-12 00:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e7d8c9b0a1f2'
down_revision = 'c9f1a2b3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gender', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('birth_year', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('treatment', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('utm_params', sa.JSON(), nullable=True))

    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('patient_gender', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('patient_birth_year', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('patient_treatment', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('patient_utm_params', sa.JSON(), nullable=True))

    op.create_table(
        'call_schedules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('frequency', sa.String(length=20), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], name='fk_call_schedules_patient'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_call_schedules_patient', 'call_schedules', ['patient_id'])
    op.create_index('ix_call_schedules_active_hour', 'call_schedules', ['active', 'hour'])


def downgrade():
    op.drop_index('ix_call_schedules_active_hour', table_name='call_schedules')
    op.drop_index('ix_call_schedules_patient', table_name='call_schedules')
    op.drop_table('call_schedules')

    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.drop_column('patient_utm_params')
        batch_op.drop_column('patient_treatment')
        batch_op.drop_column('patient_birth_year')
        batch_op.drop_column('patient_gender')

    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.drop_column('utm_params')
        batch_op.drop_column('treatment')
        batch_op.drop_column('birth_year')
        batch_op.drop_column('gender')
