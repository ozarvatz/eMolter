"""Add `name` to llm_prompts (persona key) and `prompt_name` FK on question_sets

Revision ID: d4a7c2e8b9f1
Revises: c8f3d7a2b1e0
Create Date: 2026-06-20 12:00:00.000000

Multiple named prompts ("personas") per language. The migration:
  1. Adds the `name` column (default 'default').
  2. DISAMBIGUATES existing data so UNIQUE(lang, name) can hold:
       - inactive rows are renamed to 'archived_<id>'
       - if multiple active rows exist per lang (shouldn't, but defensive),
         the smallest-id one keeps 'default', others become 'duplicate_<id>'
  3. Adds the UNIQUE(lang, name) constraint.
  4. Adds `prompt_name` to question_sets (null = use the lang's default).
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4a7c2e8b9f1'
down_revision = 'c8f3d7a2b1e0'
branch_labels = None
depends_on = None


def upgrade():
    # ---- Step 1: add `name` column to llm_prompts. ----
    # Done in its own batch so the table rebuild commits before we mutate
    # the data. Without this, the UPDATEs below couldn't see the new column.
    with op.batch_alter_table('llm_prompts', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'name', sa.String(length=50),
            nullable=False, server_default='default',
        ))

    # ---- Step 2: disambiguate so UNIQUE(lang, name) can hold. ----
    # Existing data: typically one active + several inactive per lang.
    # After step 1 they all have name='default', which violates the
    # upcoming UNIQUE constraint. Rename so each row's (lang, name) is
    # unique.

    # 2a. Inactive rows get renamed to archived_<id> (unique per row).
    op.execute(
        "UPDATE llm_prompts "
        "SET name = 'archived_' || CAST(id AS TEXT) "
        "WHERE active = 0"
    )

    # 2b. If, for any reason, more than one ACTIVE row exists for a lang
    # (the app shouldn't allow this, but the DB has no constraint forcing
    # it), keep the smallest-id one as 'default' and rename the others.
    op.execute(
        "UPDATE llm_prompts "
        "SET name = 'duplicate_' || CAST(id AS TEXT) "
        "WHERE active = 1 "
        "  AND id NOT IN ("
        "      SELECT MIN(id) FROM llm_prompts WHERE active = 1 GROUP BY lang"
        "  )"
    )

    # ---- Step 3: now safe to add the unique constraint. ----
    with op.batch_alter_table('llm_prompts', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_llm_prompts_lang_name', ['lang', 'name'],
        )

    # ---- Step 4: add prompt_name to question_sets. ----
    with op.batch_alter_table('question_sets', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'prompt_name', sa.String(length=50), nullable=True,
        ))


def downgrade():
    with op.batch_alter_table('question_sets', schema=None) as batch_op:
        batch_op.drop_column('prompt_name')

    with op.batch_alter_table('llm_prompts', schema=None) as batch_op:
        batch_op.drop_constraint('uq_llm_prompts_lang_name', type_='unique')
        batch_op.drop_column('name')
