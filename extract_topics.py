"""Offline batch topic extractor for completed calls.

Mirrors prosody.py: lock file, app context, loop over unprocessed rows.
A Call is "unprocessed" for topics when `topics IS NULL`.

Usage:
    python extract_topics.py            # process all NULL-topics calls
    python extract_topics.py --limit 20 # cap per run (cron-friendly)
"""
import sys
import json
import argparse
import fcntl

from automated_survey_flask import app
from automated_survey_flask.models import db, Call
from automated_survey_flask.topic_extractor import extract_topics

LOCK_FILE = '/tmp/extract_topics.lock'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None,
                        help='Max number of calls to process this run')
    args = parser.parse_args()

    lock_fp = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("extract_topics.py is already running. Exiting.")
        sys.exit(0)

    processed = 0
    with app.app_context():
        db.init_app(app)
        while True:
            if args.limit is not None and processed >= args.limit:
                print(f"Reached --limit {args.limit}. Stopping.")
                break

            call_row = (Call.query
                        .filter(Call.topics.is_(None))
                        .filter(Call.conversation_text.isnot(None))
                        .order_by(Call.created_at)
                        .first())
            if not call_row:
                print("No calls left to process.")
                break

            try:
                topics = extract_topics(call_row.conversation_text)
                call_row.topics = topics
                db.session.commit()
                print(f"call_id={call_row.id} sid={call_row.call_sid} -> {topics}")
                processed += 1
            except Exception as e:
                # On failure, store empty dict so we don't loop on the same
                # row. Reset to NULL by hand to retry.
                call_row.topics = {}
                db.session.commit()
                print(json.dumps({
                    "error": str(e),
                    "call_id": call_row.id,
                    "call_sid": call_row.call_sid,
                }))
                processed += 1

    print(f"Done. processed={processed}")


if __name__ == "__main__":
    main()
