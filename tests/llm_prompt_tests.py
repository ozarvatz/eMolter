from tests.base import BaseTest
from automated_survey_flask.models import LlmPrompt, QuestionSet, User
from automated_survey_flask import db
from automated_survey_flask.llm_survey_view import _build_llm_messages


SAMPLE_QUESTIONS = {
    "title": "Test",
    "questions": [
        {"body": "How are you feeling?", "type": "voice"},
        {"body": "Did you sleep well?",  "type": "voice"},
    ],
    "messages": [],
    "config": [{"body": "Google.en-US-Wavenet-B", "message": "voice_algo"}],
}

SIMPLE_SYSTEM = (
    "You are a survey bot for {lang}. Topics: {numbered}. "
    "This is question {q_num} of {max_questions}."
)
SIMPLE_FIRST    = "Start the survey, question {q_num}."
SIMPLE_FOLLOWUP = (
    "History:\n{history_lines}\n"
    "Patient just said: \"{last_answer}\". Ask question {q_num} of {max_questions}."
)


def _make_prompt(lang='en-US', active=True, system=SIMPLE_SYSTEM,
                 first=SIMPLE_FIRST, followup=SIMPLE_FOLLOWUP):
    p = LlmPrompt(
        lang=lang,
        system_template=system,
        user_template_first=first,
        user_template_followup=followup,
        active=active,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _make_question_set(batch='basic', lang='en-US'):
    qs = QuestionSet(batch=batch, lang=lang, content=SAMPLE_QUESTIONS)
    db.session.add(qs)
    db.session.commit()
    return qs


def _make_superuser(phone='+972501111111', password='pass'):
    u = User(phone=phone, nickname='Super', is_superuser=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return u


def _make_therapist(phone='+972502222222', password='pass'):
    u = User(phone=phone, nickname='Therapist', is_superuser=False)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return u


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class LlmPromptModelTest(BaseTest):

    def test_active_for_returns_active_row(self):
        _make_prompt(lang='en-US', active=False, system='old')
        active = _make_prompt(lang='en-US', active=True,  system='new')

        found = LlmPrompt.active_for('en-US')
        self.assertEqual(found.id, active.id)
        self.assertEqual(found.system_template, 'new')

    def test_active_for_returns_none_when_no_active(self):
        _make_prompt(lang='en-US', active=False)
        self.assertIsNone(LlmPrompt.active_for('en-US'))

    def test_active_for_filters_by_lang(self):
        _make_prompt(lang='en-US', active=True, system='en')
        _make_prompt(lang='he-IL', active=True, system='he')

        self.assertEqual(LlmPrompt.active_for('en-US').system_template, 'en')
        self.assertEqual(LlmPrompt.active_for('he-IL').system_template, 'he')


# ---------------------------------------------------------------------------
# _build_llm_messages
# ---------------------------------------------------------------------------
class BuildLlmMessagesTest(BaseTest):

    def setUp(self):
        super().setUp()
        _make_question_set(batch='basic', lang='en-US')

    def test_raises_when_no_active_prompt(self):
        with self.assertRaises(RuntimeError):
            _build_llm_messages('en-US', 'basic', [], 1, 5)

    def test_uses_first_template_when_no_history(self):
        _make_prompt(lang='en-US')
        system, user = _build_llm_messages('en-US', 'basic', [], 1, 5)
        self.assertIn('You are a survey bot for en-US', system)
        self.assertIn('Topics:', system)
        self.assertEqual(user, 'Start the survey, question 1.')

    def test_uses_followup_template_when_history_present(self):
        _make_prompt(lang='en-US')
        history = [{'q': 'How are you?', 'a': 'Tired.'}]
        system, user = _build_llm_messages('en-US', 'basic', history, 2, 5)
        self.assertIn('Patient just said: "Tired."', user)
        self.assertIn('Ask question 2 of 5', user)

    def test_substitutes_placeholders(self):
        _make_prompt(lang='en-US')
        system, _ = _build_llm_messages('en-US', 'basic', [], 3, 7)
        self.assertIn('question 3 of 7', system)
        self.assertIn('How are you feeling?', system)  # came from QuestionSet via {numbered}

    def test_unanswered_history_treated_as_first_turn(self):
        # History exists but last entry has empty answer — treated as first turn
        _make_prompt(lang='en-US')
        history = [{'q': 'Hello', 'a': ''}]
        _, user = _build_llm_messages('en-US', 'basic', history, 1, 5)
        self.assertEqual(user, 'Start the survey, question 1.')

    def test_inactive_prompt_not_used(self):
        _make_prompt(lang='en-US', active=False, system='OLD')
        with self.assertRaises(RuntimeError):
            _build_llm_messages('en-US', 'basic', [], 1, 5)


# ---------------------------------------------------------------------------
# Admin view
# ---------------------------------------------------------------------------
class PromptAdminViewTest(BaseTest):

    def _login(self, phone, password):
        return self.client.post('/login', data={'phone': phone, 'password': password})

    def test_list_requires_login(self):
        resp = self.client.get('/admin/prompts')
        self.assertEqual(resp.status_code, 302)

    def test_list_forbidden_for_therapist(self):
        _make_therapist()
        self._login('+972502222222', 'pass')
        resp = self.client.get('/admin/prompts')
        self.assertEqual(resp.status_code, 403)

    def test_list_accessible_for_superuser(self):
        _make_superuser()
        self._login('+972501111111', 'pass')
        _make_prompt(lang='en-US')
        resp = self.client.get('/admin/prompts')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'en-US', resp.data)

    def test_superuser_can_create_prompt(self):
        _make_superuser()
        self._login('+972501111111', 'pass')

        resp = self.client.post('/admin/prompts/new', data={
            'lang':                   'en-US',
            'system_template':        SIMPLE_SYSTEM,
            'user_template_first':    SIMPLE_FIRST,
            'user_template_followup': SIMPLE_FOLLOWUP,
            'notes':                  'test',
            'active':                 'on',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        p = LlmPrompt.query.filter_by(lang='en-US').first()
        self.assertIsNotNone(p)
        self.assertTrue(p.active)
        self.assertEqual(p.notes, 'test')

    def test_activating_one_deactivates_others_for_same_lang(self):
        _make_superuser()
        self._login('+972501111111', 'pass')

        old = _make_prompt(lang='en-US', active=True,  system='old')
        new = _make_prompt(lang='en-US', active=False, system='new')

        resp = self.client.post(f'/admin/prompts/{new.id}/activate', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        db.session.expire_all()
        self.assertFalse(LlmPrompt.query.get(old.id).active)
        self.assertTrue(LlmPrompt.query.get(new.id).active)

    def test_activating_does_not_affect_other_langs(self):
        _make_superuser()
        self._login('+972501111111', 'pass')

        en = _make_prompt(lang='en-US', active=True)
        he = _make_prompt(lang='he-IL', active=False)

        self.client.post(f'/admin/prompts/{he.id}/activate', follow_redirects=True)

        db.session.expire_all()
        self.assertTrue(LlmPrompt.query.get(en.id).active)
        self.assertTrue(LlmPrompt.query.get(he.id).active)

    def test_cannot_delete_active_prompt(self):
        _make_superuser()
        self._login('+972501111111', 'pass')
        p = _make_prompt(lang='en-US', active=True)

        resp = self.client.post(f'/admin/prompts/{p.id}/delete', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(LlmPrompt.query.get(p.id))

    def test_can_delete_inactive_prompt(self):
        _make_superuser()
        self._login('+972501111111', 'pass')
        p = _make_prompt(lang='en-US', active=False)
        pid = p.id

        self.client.post(f'/admin/prompts/{pid}/delete', follow_redirects=True)
        self.assertIsNone(LlmPrompt.query.get(pid))

    def test_therapist_cannot_create_prompt(self):
        _make_therapist()
        self._login('+972502222222', 'pass')

        resp = self.client.post('/admin/prompts/new', data={
            'lang':                   'en-US',
            'system_template':        SIMPLE_SYSTEM,
            'user_template_first':    SIMPLE_FIRST,
            'user_template_followup': SIMPLE_FOLLOWUP,
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(LlmPrompt.query.count(), 0)

    def test_edit_updates_fields(self):
        _make_superuser()
        self._login('+972501111111', 'pass')
        p = _make_prompt(lang='en-US', active=True, system='old system')

        self.client.post(f'/admin/prompts/{p.id}/edit', data={
            'lang':                   'en-US',
            'system_template':        'updated system',
            'user_template_first':    SIMPLE_FIRST,
            'user_template_followup': SIMPLE_FOLLOWUP,
            'active':                 'on',
        }, follow_redirects=True)

        db.session.expire_all()
        updated = LlmPrompt.query.get(p.id)
        self.assertEqual(updated.system_template, 'updated system')
        self.assertTrue(updated.active)
