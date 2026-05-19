"""Seed additional prosody parameters (top-level fields and VQS extras)

Revision ID: a4f7c821b9e3
Revises: e7d8c9b0a1f2
Create Date: 2026-05-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a4f7c821b9e3'
down_revision = 'e7d8c9b0a1f2'
branch_labels = None
depends_on = None


# (parameter_key, parameter_name, explanation, category)
NEW_PARAMETERS = [
    (
        'pitch_sd_hz',
        'Pitch SD (Hz)',
        'Top-level standard deviation of pitch across the entire utterance in Hertz. '
        'High SD indicates expressive / emotional speech; very low SD suggests monotone '
        'delivery (a potential depression marker).',
        'basic',
    ),
    (
        'pitch_range_hz',
        'Pitch Range (Hz)',
        'Distance in Hertz between the lowest and highest pitch observed in the recording. '
        'A compressed range can indicate flat affect; a very wide range can reflect '
        'agitation or strong emotional content.',
        'basic',
    ),
    (
        'mean_pitch',
        'Mean Pitch (VQS)',
        'Praat-reported mean fundamental frequency within the voice_quality_stats block, '
        'in Hertz. Closely tracks top-level mean_pitch_hz but is computed from the '
        'voice report rather than the pitch object.',
        'quality',
    ),
    (
        'jitter_local,_absolute',
        'Jitter Local (Absolute)',
        'Absolute cycle-to-cycle variation of the pitch period in microseconds. '
        'Unlike jitter_local (a percentage), this is an absolute time-domain measure '
        'of period instability.',
        'jitter',
    ),
    (
        'voice_health_score',
        'Voice Health Score',
        'Weighted composite score from 0.0 (clean / healthy voice) to 1.0 (very hoarse / '
        'broken voice). Combines jitter, shimmer, HNR and degree_of_voice_breaks into a '
        'single severity indicator.',
        'basic',
    ),
    (
        'speaking_ratio',
        'Speaking Ratio',
        'Fraction (0-1) of the recording that contained active speech versus silence. '
        'For example, 0.55 means the speaker was actively talking ~55% of the time. '
        'Very low values indicate long pauses, reluctance, or interviewer-dominated audio.',
        'basic',
    ),
    (
        'total_duration',
        'Total Duration (s)',
        'Length of the analyzed recording in seconds (top-level). Longer samples yield '
        'more reliable prosody statistics; very short samples should be interpreted with '
        'caution.',
        'basic',
    ),
]


def upgrade():
    conn = op.get_bind()
    prosody_parameters = sa.table(
        'prosody_parameters',
        sa.column('parameter_key', sa.String),
        sa.column('parameter_name', sa.String),
        sa.column('explanation', sa.Text),
        sa.column('category', sa.String),
    )

    for param_key, param_name, explanation, category in NEW_PARAMETERS:
        existing = conn.execute(
            sa.text('SELECT id FROM prosody_parameters WHERE parameter_key = :k'),
            {'k': param_key},
        ).fetchone()
        if existing:
            continue
        op.bulk_insert(prosody_parameters, [{
            'parameter_key': param_key,
            'parameter_name': param_name,
            'explanation': explanation,
            'category': category,
        }])


def downgrade():
    conn = op.get_bind()
    for param_key, _, _, _ in NEW_PARAMETERS:
        conn.execute(
            sa.text('DELETE FROM prosody_parameters WHERE parameter_key = :k'),
            {'k': param_key},
        )
