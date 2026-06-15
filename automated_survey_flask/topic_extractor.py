"""Topic extraction from conversation transcripts.

Used in two places:
  - extract_topics.py (offline batch over the DB)
  - conversation_relay_view.py (real-time during a live call)

Output: list of short English topic labels (lowercase, snake_case).
"""
import os
import json

MODEL = 'llama-3.3-70b-versatile'
MIN_CHARS = 20

SYSTEM_PROMPT = (
    "You are a conversation analyst. You read a short phone conversation "
    "between a bot and a person. The person may be an elderly user, a "
    "recovery-program participant, a clinical patient, or anyone else — "
    "do not assume a population type. "
    "Lines starting with 'Q:' are the bot. Lines starting with 'A:' are "
    "the person. The conversation may be in Hebrew, English, or German.\n"
    "\n"
    "Produce TWO topic lists:\n"
    "  - bot_topics:     concrete topics RAISED BY the Q: lines.\n"
    "  - patient_topics: concrete topics the patient ACTUALLY talked about\n"
    "                    in the A: lines. If the patient only said yes/no or\n"
    "                    one word, this list may be empty.\n"
    "\n"
    "STRICT RULES — violating any rule means the output is wrong:\n"
    "  1. ONLY include topics that the speaker actually expressed. Do NOT\n"
    "     infer topics from the TYPE of conversation or the assumed\n"
    "     population. NEVER add a canonical clinical topic that has no\n"
    "     support in the text.\n"
    "  2. WHEN the speaker EXPLICITLY expressed a canonical concept, USE\n"
    "     the canonical English label, not an over-literal phrase. The\n"
    "     rule is: prefer the canonical label when the concept is\n"
    "     unambiguously present in the speaker's words.\n"
    "     SAFETY/CLINICAL canonicals:\n"
    "       'I want to die' / 'I want to end it all' / 'jump off a roof'\n"
    "          → 'suicidal_ideation'  (not 'wants_to_die', 'jumping_from_roof')\n"
    "       'I cut myself' / 'hurt myself'\n"
    "          → 'self_harm'\n"
    "       'I can't fall asleep' / 'tossing all night'\n"
    "          → 'insomnia'\n"
    "       'I drank again' / 'used last night' / 'relapsed'\n"
    "          → 'substance_use'\n"
    "       'no one calls me' / 'I have no one' / 'I'm alone all day'\n"
    "          → 'loneliness'\n"
    "     PHYSICAL canonicals — ALWAYS collapse body symptoms to one label:\n"
    "       'my X itches' / 'I have a headache' / 'my back hurts' / 'nausea'\n"
    "         / 'dizzy' / 'stomach ache' / 'sore' / 'aching'\n"
    "          → 'physical_discomfort'\n"
    "          (do NOT use 'itching', 'headache', 'back_pain' as separate\n"
    "           labels — collapse them all to 'physical_discomfort'.)\n"
    "     EMOTIONAL canonicals — when the speaker explicitly expresses an\n"
    "     emotion or feeling state, USE the canonical emotion label:\n"
    "       'I feel ashamed' / 'it's embarrassing' / 'מבייש'\n"
    "          → 'embarrassment'\n"
    "       'I'm so frustrated' / 'this is driving me crazy'\n"
    "          → 'frustration'\n"
    "       'I'm angry' / 'I'm furious' / 'pissed off'\n"
    "          → 'anger'\n"
    "       'I'm sad' / 'I cry all the time' / 'I feel down'\n"
    "          → 'sadness'\n"
    "       'I'm scared' / 'I'm afraid' / 'I'm terrified'\n"
    "          → 'fear'\n"
    "       'I'm so happy' / 'I feel great'\n"
    "          → 'joy'\n"
    "       'finally relaxed' / 'I can breathe again'\n"
    "          → 'relief'\n"
    "  3. Greetings and farewells are NEVER topics. Do not include 'hello',\n"
    "     'shalom', 'hi', 'goodbye', 'bye', etc.\n"
    "  4. If a Q: or A: line is truncated mid-word, IGNORE the truncated\n"
    "     fragment. Do not guess what the cut-off word was.\n"
    "  5. Maximum 4 topics per list. Fewer is better. Be selective.\n"
    "  6. Use STANDARD ENGLISH vocabulary only. If the speaker used another\n"
    "     language, TRANSLATE to the common English word. NEVER transliterate.\n"
    "     Examples: 'buses' (not 'autobuses' or 'otobusim'),\n"
    "     'shoes' (not 'naalayim'), 'food' (not 'okhel').\n"
    "  7. No semantic duplicates — collapse near-synonyms to ONE label.\n"
    "     'wants_to_die' + 'jumping_from_roof' + 'suicidal_ideation'\n"
    "          → just 'suicidal_ideation'.\n"
    "     'motivation' + 'motivational_topics' + 'motivation_from_discussion'\n"
    "          → just 'motivation'.\n"
    "  8. Each topic must be a CONCRETE SUBJECT discussed, not a meta-label\n"
    "     about the conversation. Forbidden examples: 'general_chat',\n"
    "     'discussion', 'motivation_from_discussion', 'previous_day_comparison'.\n"
    "  9. Use lowercase snake_case. Examples: 'sleep', 'family_conflict',\n"
    "     'work_stress', 'buses', 'engines', 'finances', 'suicidal_ideation'.\n"
    "\n"
    "Output strict JSON: "
    "{\"bot_topics\": [\"...\"], \"patient_topics\": [\"...\"]}"
)


_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        api_key = os.environ.get('GROQ_API_KEY')
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


def format_conversation(conversation_text):
    """Normalize either the LLM JSON array or the non-LLM 'Q1:/A1:' text
    into a single human-readable transcript."""
    if not conversation_text:
        return None
    text = conversation_text.strip() if isinstance(conversation_text, str) else conversation_text
    if not text:
        return None

    # If already a list (RT path passes history dict directly), serialize it
    if isinstance(text, list):
        lines = []
        for turn in text:
            q = (turn.get('q') or '').strip()
            a = (turn.get('a') or '').strip()
            if q:
                lines.append(f"Q: {q}")
            if a:
                lines.append(f"A: {a}")
        return "\n".join(lines) if lines else None

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return format_conversation(data)
    except (json.JSONDecodeError, AttributeError):
        pass

    return text


def _clean_list(items):
    return [str(t).strip().lower() for t in (items or []) if t]


def extract_topics(conversation):
    """Run Groq topic extraction. `conversation` may be:
      - the raw conversation_text TEXT field (JSON-array string or 'Q1:/A1:' text)
      - the live history list-of-dicts [{q, a}, ...]
    Returns a dict {"bot_topics": [...], "patient_topics": [...]}
    (either list may be empty). Returns {} when the transcript is too short."""
    transcript = format_conversation(conversation)
    if not transcript or len(transcript) < MIN_CHARS:
        return {}

    resp = _get_groq_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Conversation:\n{transcript}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=400,
    )
    raw = resp.choices[0].message.content
    data = json.loads(raw)
    return {
        "bot_topics":     _clean_list(data.get('bot_topics')),
        "patient_topics": _clean_list(data.get('patient_topics')),
    }
