"""Offline batch topic extractor for completed calls.

Two modes:
  Global  (default) — find any Call with NULL topics, process FIFO.
  Per-patient       — for each patient, take their last N calls and process
                      those (only NULL topics unless --force).

Usage:
    python extract_topics.py
    python extract_topics.py --limit 20
    python extract_topics.py --per-patient-last 14
    python extract_topics.py --per-patient-last 14 --force
"""
import sys
import json
import argparse
import fcntl

from automated_survey_flask import app
from automated_survey_flask.models import db, Call, Patient
from automated_survey_flask.topic_extractor import extract_topics

LOCK_FILE = '/tmp/extract_topics.lock'


def _process_one(call_row):
    """Try to extract topics for one Call. Returns True on success."""
    try:
        topics = extract_topics(call_row.conversation_text)
        call_row.topics = topics
        db.session.commit()
        print(f"call_id={call_row.id} sid={call_row.call_sid} -> {topics}")
        return True
    except Exception as e:
        # Store empty dict so we don't loop on the same row.
        call_row.topics = {}
        db.session.commit()
        print(json.dumps({
            "error": str(e),
            "call_id": call_row.id,
            "call_sid": call_row.call_sid,
        }))
        return False


def _global_mode(limit):
    processed = 0
    while True:
        if limit is not None and processed >= limit:
            print(f"Reached --limit {limit}. Stopping.")
            break
        call_row = (Call.query
                    .filter(Call.topics.is_(None))
                    .filter(Call.conversation_text.isnot(None))
                    .order_by(Call.created_at)
                    .first())
        if not call_row:
            print("No calls left to process.")
            break
        _process_one(call_row)
        processed += 1
    return processed


def _per_patient_mode(last_n, force, limit):
    """For each active patient, take their last N calls (by created_at) and
    extract topics. When --force is off, only fills NULL topics; when on,
    re-runs every call in the window."""
    patients = Patient.query.filter_by(deleted=False).order_by(Patient.id).all()
    processed = 0
    for p in patients:
        if not p.phone:
            continue
        last_calls = (Call.query
                      .filter(Call.patient_phone == p.phone)
                      .filter(Call.conversation_text.isnot(None))
                      .order_by(Call.created_at.desc())
                      .limit(last_n)
                      .all())
        if not last_calls:
            continue
        targets = last_calls if force else [c for c in last_calls if c.topics is None]
        if not targets:
            print(f"patient_id={p.id} {p.phone}: nothing to do "
                  f"({len(last_calls)} recent calls, all already have topics)")
            continue
        print(f"patient_id={p.id} {p.phone}: {len(targets)} of last "
              f"{len(last_calls)} calls to process")
        for c in targets:
            if limit is not None and processed >= limit:
                print(f"Reached --limit {limit}. Stopping.")
                return processed
            _process_one(c)
            processed += 1
    return processed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None,
                        help='Max number of calls to process this run.')
    parser.add_argument('--per-patient-last', type=int, default=None,
                        help='Per-patient mode: process each patient\'s last N calls.')
    parser.add_argument('--force', action='store_true',
                        help='In per-patient mode, re-run even if topics already exist.')
    args = parser.parse_args()

    lock_fp = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("extract_topics.py is already running. Exiting.")
        sys.exit(0)

    with app.app_context():
        db.init_app(app)
        if args.per_patient_last:
            processed = _per_patient_mode(args.per_patient_last, args.force, args.limit)
        else:
            processed = _global_mode(args.limit)

    print(f"Done. processed={processed}")


if __name__ == "__main__":
    main()
