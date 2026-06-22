"""One-off recovery script for the d4a7c2e8b9f1 migration.

Some prod databases ended up half-migrated because the first version of the
migration tripped on a UNIQUE constraint and SQLite's batch-rebuild left a
`_alembic_tmp_llm_prompts` table behind, blocking later retries.

This script finishes the job idempotently:
  - drops any leftover _alembic_tmp_llm_prompts table
  - adds the UNIQUE(lang, name) index on llm_prompts if missing
  - adds the prompt_name column to question_sets if missing
  - prints what it did at each step

After running this, run:
    python manage.py db stamp d4a7c2e8b9f1
to tell alembic the migration is now applied.
"""
from sqlalchemy import inspect, text

from automated_survey_flask import app, db


def _column_exists(table, column):
    cols = [c['name'] for c in inspect(db.engine).get_columns(table)]
    return column in cols


def _index_exists(table, index_name):
    idxs = [i['name'] for i in inspect(db.engine).get_indexes(table)]
    return index_name in idxs


def _table_exists(name):
    return name in inspect(db.engine).get_table_names()


def main():
    with app.app_context():
        db.init_app(app)

        # 1) Drop leftover batch-rebuild temp table, if present.
        if _table_exists('_alembic_tmp_llm_prompts'):
            db.session.execute(text("DROP TABLE _alembic_tmp_llm_prompts"))
            db.session.commit()
            print("[ok] dropped leftover _alembic_tmp_llm_prompts")
        else:
            print("[skip] no leftover _alembic_tmp_llm_prompts")

        # 2) Add UNIQUE(lang, name) index on llm_prompts (= the constraint).
        if not _column_exists('llm_prompts', 'name'):
            raise SystemExit(
                "[FATAL] llm_prompts.name column is missing. "
                "Steps 1-2 of the migration didn't actually run. "
                "Don't run db stamp — investigate first."
            )

        if _index_exists('llm_prompts', 'uq_llm_prompts_lang_name'):
            print("[skip] uq_llm_prompts_lang_name index already exists")
        else:
            # The migration's step 2 UPDATEs got rolled back when step 3
            # failed (DDL committed but DML was in a transaction). Re-run
            # them here. Both statements are idempotent — running twice
            # produces the same result.

            # 2a. inactive rows → archived_<id>
            result = db.session.execute(text(
                "UPDATE llm_prompts "
                "SET name = 'archived_' || CAST(id AS TEXT) "
                "WHERE active = 0 AND name = 'default'"
            ))
            db.session.commit()
            print(f"[ok] renamed {result.rowcount} inactive rows to archived_<id>")

            # 2b. active duplicates per lang → duplicate_<id> (keep smallest-id as default)
            result = db.session.execute(text(
                "UPDATE llm_prompts "
                "SET name = 'duplicate_' || CAST(id AS TEXT) "
                "WHERE active = 1 "
                "  AND name = 'default' "
                "  AND id NOT IN ("
                "      SELECT MIN(id) FROM llm_prompts "
                "      WHERE active = 1 AND name = 'default' "
                "      GROUP BY lang"
                "  )"
            ))
            db.session.commit()
            print(f"[ok] renamed {result.rowcount} duplicate active rows to duplicate_<id>")

            # Now verify no duplicates remain before creating the index.
            rows = db.session.execute(text(
                "SELECT lang, name, COUNT(*) c FROM llm_prompts "
                "GROUP BY lang, name HAVING c > 1"
            )).fetchall()
            if rows:
                print("[FATAL] (lang, name) pairs still duplicated after disambiguation:")
                for r in rows:
                    print(f"    {dict(r._mapping)}")
                raise SystemExit(
                    "Manual intervention needed — investigate which rows are duplicated."
                )

            db.session.execute(text(
                "CREATE UNIQUE INDEX uq_llm_prompts_lang_name "
                "ON llm_prompts(lang, name)"
            ))
            db.session.commit()
            print("[ok] created UNIQUE index on llm_prompts(lang, name)")

        # 3) Add prompt_name column to question_sets.
        if _column_exists('question_sets', 'prompt_name'):
            print("[skip] question_sets.prompt_name already exists")
        else:
            db.session.execute(text(
                "ALTER TABLE question_sets ADD COLUMN prompt_name VARCHAR(50)"
            ))
            db.session.commit()
            print("[ok] added question_sets.prompt_name")

        # 4) Print final state for confirmation.
        print()
        print("Final llm_prompts rows:")
        for r in db.session.execute(text(
            "SELECT id, lang, name, active FROM llm_prompts "
            "ORDER BY lang, active DESC, id"
        )).fetchall():
            print(f"    {dict(r._mapping)}")

        print()
        print("Now run:  python manage.py db stamp d4a7c2e8b9f1")


if __name__ == "__main__":
    main()
