"""
Unified interface for reading survey question data.
Priority: DB (QuestionSet table)  →  JSON file fallback.
"""
import json


def read_question(lang, batch, n, entity):
    """Return the body of the nth item (1-based) from entity in (batch, lang) set."""
    from automated_survey_flask.models import QuestionSet
    qs = QuestionSet.query.filter_by(batch=batch, lang=lang).first()
    if qs:
        return qs.get_item(entity, n)
    return _json_item(lang, batch, n, entity)


def get_questions_list(lang, batch):
    """Return all question bodies as an ordered list."""
    from automated_survey_flask.models import QuestionSet
    qs = QuestionSet.query.filter_by(batch=batch, lang=lang).first()
    if qs:
        return qs.questions_list()
    json_path = f"questions_{batch}_{lang}.json"
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return [q['body'] for q in data.get('questions', [])]


def get_question_set_meta(lang, batch):
    """Return call-length-control settings + persona name for (lang, batch).
    When the QuestionSet row is missing (JSON-file fallback) or pre-dates
    the new columns, returns conservative defaults: by_count mode, no time
    target, cap of 10 questions, engagement strategy, default persona."""
    from automated_survey_flask.models import QuestionSet
    qs = QuestionSet.query.filter_by(batch=batch, lang=lang).first()
    if qs is None:
        return {
            'length_mode':        QuestionSet.LENGTH_BY_COUNT,
            'target_seconds':     None,
            'max_questions':      10,
            'extension_strategy': QuestionSet.EXT_ENGAGEMENT,
            'prompt_name':        None,
            'turn_instruction':   None,
        }
    return {
        'length_mode':        qs.length_mode or QuestionSet.LENGTH_BY_COUNT,
        'target_seconds':     qs.target_seconds,
        'max_questions':      qs.max_questions or 10,
        'extension_strategy': qs.extension_strategy or QuestionSet.EXT_ENGAGEMENT,
        'prompt_name':        qs.prompt_name,
        'turn_instruction':   qs.turn_instruction,
    }


def _json_item(lang, batch, n, entity):
    json_path = f"questions_{batch}_{lang}.json"
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    items = data.get(entity, [])
    if n < 1 or n > len(items):
        raise IndexError(f"{entity}[{n}] out of range (len={len(items)})")
    return items[n - 1]['body']
