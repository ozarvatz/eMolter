"""Smoke test for the topic extractor — runs against real Groq.

Usage:
    python try_topics.py                # default fixture (the crisis call)
    python try_topics.py --case engines # the engines/buses call
    python try_topics.py --json '[{"q":"...","a":"..."}, ...]'
    python try_topics.py --call-id 42   # pull from the DB

Requires GROQ_API_KEY in the environment.
"""
import argparse
import json
import sys
import time

from automated_survey_flask.topic_extractor import extract_topics


# Fixtures — the exact conversations we've been iterating on.
FIXTURES = {
    "crisis": [
        {"q": "שלום שלום Oziko, כאן מולטי. מה שלומך?",
         "a": "בסדר אני מרגישה בסדר אני רוצה לקפוץ מהגג"},
        {"q": "אני שומעת שיש לך רגעים קשים, וזה מובן. האם את מרגישה שיש לך אנרגיה?",
         "a": "יש לי אנרגיה אני ממש מרגישה וממש טוב אני חייב לגמור עם זה"},
        {"q": "אני שומעת שיש לך אנרגיה ותחושה טובה רגעית, אבל גם תחושת דחיפות לסיים משהו. איך היום ש",
         "a": "היום ש היום גרוע רוצה למות"},
        {"q": "אני שומעת שהיום הוא יום קשה מאוד עבורך, ואני כאן לשמוע אותך. האם את מתכננת לצאת היום ה",
         "a": "לא אני לא"},
        {"q": "אני מבינה שזה יום קשה מאוד עבורך, וזה מובן שאת לא מרגישה כרגע שאת רוצה לצאת החוצה. מ",
         "a": "לצאת אני מתכננת לצאת"},
    ],
    "engines": [
        {"q": "שלום שלום Oziko, כאן מולטי. מה שלומך?",
         "a": "בסדר רשלומי טוב אני נורא אוהב את מנועים שלא רוצה לדבר איתך רק על מנועים של אוטובוסים"},
        {"q": "נשמע שאת רגועה ומרוכזת בדברים שאת מעוניינת בהם, כמו מנועים. האם את מרגישה שיש לך אנרג",
         "a": "אני מרגיש שאין לי אני רוצה לדבר על מנועים מנועים זה לא מנועים של אוטובוסים"},
        {"q": "נשמע שאת מרגישה חוסר אנרגיה וגם חשה תסכול מהנושאים שאנחנו מדברים עליהם. האם את מת",
         "a": "אני לא חשת תסכול אני נורא אוהב את מנועים מה הבעיה זה עושה לי להרגיש טוב אני מרגישה שיש לי אנרגיות יותר טובות"},
        {"q": "נשמע שדיבור על מנועים מעורר בך רגשות חיוביים ומעניק לך אנרגיה. איך היום שלך היה בהשווא",
         "a": "בהשוואה"},
        {"q": "נשמע שאת עומדת להשוות את היום שלך, אולי לאתמול או לתקופה אחרת. האם את מתכננת לצאת היום החוצ",
         "a": "אני מתכוונת לצאת היום החוצה"},
    ],
    # Somatic complaint + colloquial "want to die" — the linguistic pattern
    # where physical discomfort co-occurs with hyperbolic suicidal phrasing.
    # The extractor's job is to surface BOTH so downstream code can judge.
    "somatic_vent": [
        {"q": "שלום שלום Oziko, כאן מולטי. מה שלומך?",
         "a": "מגרדיה תחת אני רוצה למות"},
        {"q": "אני שומע שאת מרגישה מאוד קשה ומיוסרת. האם את מרגישה שיש לך אנרגיה?",
         "a": "לא אין לי אנרגיה כי מגרד לי נורא בתחת ואני רוצה כבר לקפוץ מהגג"},
        {"q": "אני שומעת שאת מרגישה מאוד קשה ומיוסרת, וזה מובן שהתחושות האלו משפיעות על האנרגיה שלך",
         "a": "כן זה משפיע לי מאוד על האנרגיה"},
        {"q": "אני שומעת שהתחושות הקשות שאת מרגישה, ובמיוחד הגרידה בתחת, משפיעות מאוד על האנרגיה שלך",
         "a": "כן זה משפיע עליי ממש מה את חושבת לגרד בתחת זה מאוד קשה"},
        {"q": "אני שומעת שהגרידה בתחת מקשה עלייך מאוד וגורמת לך הרבה סבל. האם את מתכננת לצאת היום הח",
         "a": "אני לא רוצה לצאת החוצה היום אני מרגישה שבגרד לי נורא בתחת וזה מאוד מבייש"},
    ],
}

# What we EXPECT to see (informational checks, not hard asserts).
EXPECTATIONS = {
    "crisis": {
        "patient_must_have_any_of": ["suicidal_ideation", "self_harm"],
        "patient_must_not_have":    ["shalom", "hello", "hi"],
        "bot_must_not_have":        ["medication_side_effects", "shalom"],
    },
    "engines": {
        "patient_must_have_any_of": ["engines", "buses"],
        "patient_must_not_have":    ["shalom", "hello", "motivation_from_discussion"],
        "bot_must_not_have":        ["suicidal_ideation", "medication_side_effects"],
    },
    "somatic_vent": {
        # All three signals must surface so code can judge it's venting, not crisis:
        "patient_must_have_any_of": ["physical_discomfort"],
        "patient_must_not_have":    ["itching", "headache", "back_pain", "shalom"],
        "bot_must_not_have":        ["medication_side_effects", "shalom"],
    },
}


def _load_from_db(call_id):
    from automated_survey_flask import app
    from automated_survey_flask.models import Call
    with app.app_context():
        call = Call.query.get(call_id)
        if not call:
            sys.exit(f"No Call with id={call_id}")
        return call.conversation_text


def _check_any_of(label, present, needles):
    """At least one of `needles` must appear in `present`."""
    hits = [n for n in needles if n in present]
    ok = bool(hits)
    icon = "OK " if ok else "FAIL"
    print(f"    [{icon}] any of {needles} in {label}: {present}  (matched: {hits})")
    return ok


def _check_not_in(label, present, needle):
    """`needle` must NOT appear in `present`."""
    ok = needle not in present
    icon = "OK " if ok else "FAIL"
    print(f"    [{icon}] '{needle}' NOT in {label}: {present}")
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--case", choices=list(FIXTURES.keys()), default="crisis",
                   help="Which built-in conversation to extract from.")
    p.add_argument("--json", help="Inline JSON conversation (list of {q,a}). "
                                  "Overrides --case.")
    p.add_argument("--call-id", type=int,
                   help="Pull conversation_text from Call.id in the DB.")
    args = p.parse_args()

    if args.call_id is not None:
        conversation = _load_from_db(args.call_id)
        source = f"DB call_id={args.call_id}"
    elif args.json:
        conversation = json.loads(args.json)
        source = "inline --json"
    else:
        conversation = FIXTURES[args.case]
        source = f"fixture '{args.case}'"

    print(f"=== Input ({source}) ===")
    if isinstance(conversation, list):
        for i, turn in enumerate(conversation, 1):
            print(f"  Q{i}: {turn.get('q','')}")
            print(f"  A{i}: {turn.get('a','')}")
    else:
        print(conversation)

    print("\n=== Extracting... ===")
    t0 = time.time()
    result = extract_topics(conversation)
    print(f"  took {(time.time()-t0)*1000:.0f}ms")

    print("\n=== Output ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # Soft expectation checks
    exp = EXPECTATIONS.get(args.case) if not (args.json or args.call_id) else None
    if exp:
        print("\n=== Expectation checks ===")
        bot = result.get("bot_topics", [])
        pat = result.get("patient_topics", [])
        all_ok = True
        any_needles = exp.get("patient_must_have_any_of")
        if any_needles:
            all_ok &= _check_any_of("patient_topics", pat, any_needles)
        for needle in exp.get("patient_must_not_have", []):
            all_ok &= _check_not_in("patient_topics", pat, needle)
        for needle in exp.get("bot_must_not_have", []):
            all_ok &= _check_not_in("bot_topics", bot, needle)
        print(f"\n  Overall: {'PASS' if all_ok else 'FAIL'}")


if __name__ == "__main__":
    main()
