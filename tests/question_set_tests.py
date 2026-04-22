from tests.base import BaseTest
from automated_survey_flask.models import QuestionSet, User
from automated_survey_flask import db
from automated_survey_flask.question_service import read_question, get_questions_list


SAMPLE_CONTENT = {
    "title": "Test Survey",
    "questions": [
        {"body": "How are you feeling today?", "type": "voice"},
        {"body": "Did you sleep well?",         "type": "voice"},
        {"body": "Do you have energy?",          "type": "voice"},
    ],
    "messages": [
        {"body": "Sorry, not recorded.",  "type": "voice", "message": "sorry_failed"},
        {"body": "You said: '{}'",        "type": "voice", "message": "you_said"},
        {"body": "Thank you, goodbye!",   "type": "voice", "message": "thanks"},
        {"body": "Hello {}!",             "type": "voice", "message": "hello"},
    ],
    "config": [
        {"body": "Google.en-US-Wavenet-B", "message": "voice_algo"}
    ],
}


def _make_superuser(phone='+972501111111', nickname='Super', password='pass'):
    u = User(phone=phone, nickname=nickname, is_superuser=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return u


def _make_therapist(phone='+972502222222', nickname='Therapist', password='pass'):
    u = User(phone=phone, nickname=nickname, is_superuser=False)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return u


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------
class QuestionSetModelTest(BaseTest):

    def test_create_and_retrieve(self):
        qs = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        found = QuestionSet.query.filter_by(batch='test', lang='en-US').first()
        self.assertIsNotNone(found)
        self.assertEqual(found.batch, 'test')
        self.assertEqual(found.lang,  'en-US')

    def test_get_item_question(self):
        qs = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        self.assertEqual(qs.get_item('questions', 1), "How are you feeling today?")
        self.assertEqual(qs.get_item('questions', 3), "Do you have energy?")

    def test_get_item_message(self):
        qs = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        self.assertEqual(qs.get_item('messages', 3), "Thank you, goodbye!")
        self.assertEqual(qs.get_item('messages', 4), "Hello {}!")

    def test_get_item_config(self):
        qs = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        self.assertEqual(qs.get_item('config', 1), "Google.en-US-Wavenet-B")

    def test_get_item_out_of_range(self):
        qs = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        with self.assertRaises(IndexError):
            qs.get_item('questions', 99)

    def test_questions_list(self):
        qs = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        lst = qs.questions_list()
        self.assertEqual(len(lst), 3)
        self.assertEqual(lst[0], "How are you feeling today?")

    def test_unique_constraint(self):
        qs1 = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        qs2 = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs1)
        db.session.commit()
        db.session.add(qs2)
        with self.assertRaises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_different_lang_allowed(self):
        qs1 = QuestionSet(batch='test', lang='en-US', content=SAMPLE_CONTENT)
        qs2 = QuestionSet(batch='test', lang='he-IL', content=SAMPLE_CONTENT)
        db.session.add(qs1)
        db.session.add(qs2)
        db.session.commit()
        self.assertEqual(QuestionSet.query.count(), 2)


# ---------------------------------------------------------------------------
# question_service tests
# ---------------------------------------------------------------------------
class QuestionServiceTest(BaseTest):

    def test_service_reads_from_db(self):
        qs = QuestionSet(batch='svc', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        result = read_question('en-US', 'svc', 2, 'questions')
        self.assertEqual(result, "Did you sleep well?")

    def test_service_get_questions_list_from_db(self):
        qs = QuestionSet(batch='svc', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        lst = get_questions_list('en-US', 'svc')
        self.assertEqual(len(lst), 3)
        self.assertIn("Do you have energy?", lst)

    def test_service_raises_when_not_found(self):
        # No DB entry, no JSON file for this batch/lang
        with self.assertRaises(Exception):
            read_question('xx-XX', 'nonexistent', 1, 'questions')


# ---------------------------------------------------------------------------
# View (CRUD) tests
# ---------------------------------------------------------------------------
class QuestionSetViewTest(BaseTest):

    def _login(self, phone, password):
        self.client.post('/login', data={'phone': phone, 'password': password})

    def _post_form(self, url, batch='tb', lang='en-US', questions=None, follow=True):
        questions = questions or ['Q1?', 'Q2?']
        data = {
            'batch':           batch,
            'lang':            lang,
            'title':           'Test Survey',
            'voice_algo':      'Google.en-US-Wavenet-B',
            'msg_sorry_failed': 'Sorry.',
            'msg_you_said':    "You said: '{}'",
            'msg_thanks':      'Thank you.',
            'msg_hello':       'Hello',
        }
        for i, q in enumerate(questions):
            data[f'question_{i}'] = q
        return self.client.post(url, data=data, follow_redirects=follow)

    # --- list ---
    def test_list_requires_login(self):
        resp = self.client.get('/question-sets')
        self.assertEqual(resp.status_code, 302)

    def test_list_accessible_by_therapist(self):
        _make_therapist()
        self._login('+972502222222', 'pass')
        resp = self.client.get('/question-sets')
        self.assertEqual(resp.status_code, 200)

    def test_list_accessible_by_superuser(self):
        _make_superuser()
        self._login('+972501111111', 'pass')
        resp = self.client.get('/question-sets')
        self.assertEqual(resp.status_code, 200)

    # --- create ---
    def test_superuser_can_create(self):
        _make_superuser()
        self._login('+972501111111', 'pass')
        resp = self._post_form('/question-sets/new', batch='tb', lang='en-US')
        self.assertEqual(resp.status_code, 200)

        qs = QuestionSet.query.filter_by(batch='tb', lang='en-US').first()
        self.assertIsNotNone(qs)
        self.assertEqual(len(qs.content['questions']), 2)
        self.assertEqual(qs.content['questions'][0]['body'], 'Q1?')

    def test_therapist_can_create_own_set(self):
        therapist = _make_therapist()
        self._login('+972502222222', 'pass')
        resp = self._post_form('/question-sets/new', batch='tb', lang='en-US')
        self.assertEqual(resp.status_code, 200)

        qs = QuestionSet.query.filter_by(batch='tb', lang='en-US').first()
        self.assertIsNotNone(qs)
        self.assertEqual(qs.created_by_id, therapist.id)

    def test_duplicate_batch_lang_rejected(self):
        _make_superuser()
        self._login('+972501111111', 'pass')
        self._post_form('/question-sets/new', batch='tb', lang='en-US')
        resp = self._post_form('/question-sets/new', batch='tb', lang='en-US')
        self.assertIn(b'already exists', resp.data)
        self.assertEqual(QuestionSet.query.filter_by(batch='tb', lang='en-US').count(), 1)

    # --- edit ---
    def test_superuser_can_edit(self):
        qs = QuestionSet(batch='tb', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()

        _make_superuser()
        self._login('+972501111111', 'pass')
        self._post_form(f'/question-sets/{qs.id}/edit',
                        batch='tb', lang='en-US', questions=['Updated Q1?'])

        updated = QuestionSet.query.get(qs.id)
        self.assertEqual(updated.content['questions'][0]['body'], 'Updated Q1?')

    def test_therapist_cannot_edit_others_set(self):
        # set owned by superuser (created_by_id=superuser.id)
        superuser = _make_superuser()
        qs = QuestionSet(batch='tb', lang='en-US', content=SAMPLE_CONTENT,
                         created_by_id=superuser.id)
        db.session.add(qs)
        db.session.commit()

        _make_therapist()
        self._login('+972502222222', 'pass')
        resp = self._post_form(f'/question-sets/{qs.id}/edit', follow=False)
        self.assertEqual(resp.status_code, 403)

    def test_therapist_can_edit_own_set(self):
        therapist = _make_therapist()
        qs = QuestionSet(batch='tb', lang='en-US', content=SAMPLE_CONTENT,
                         created_by_id=therapist.id)
        db.session.add(qs)
        db.session.commit()

        self._login('+972502222222', 'pass')
        self._post_form(f'/question-sets/{qs.id}/edit',
                        batch='tb', lang='en-US', questions=['Updated Q1?'])
        updated = QuestionSet.query.get(qs.id)
        self.assertEqual(updated.content['questions'][0]['body'], 'Updated Q1?')

    # --- delete ---
    def test_superuser_can_delete(self):
        qs = QuestionSet(batch='tb', lang='en-US', content=SAMPLE_CONTENT)
        db.session.add(qs)
        db.session.commit()
        qs_id = qs.id

        _make_superuser()
        self._login('+972501111111', 'pass')
        self.client.post(f'/question-sets/{qs_id}/delete', follow_redirects=True)

        self.assertIsNone(QuestionSet.query.get(qs_id))

    def test_therapist_cannot_delete_others_set(self):
        superuser = _make_superuser()
        qs = QuestionSet(batch='tb', lang='en-US', content=SAMPLE_CONTENT,
                         created_by_id=superuser.id)
        db.session.add(qs)
        db.session.commit()

        _make_therapist()
        self._login('+972502222222', 'pass')
        resp = self.client.post(f'/question-sets/{qs.id}/delete')
        self.assertEqual(resp.status_code, 403)
        self.assertIsNotNone(QuestionSet.query.get(qs.id))

    def test_therapist_can_delete_own_set(self):
        therapist = _make_therapist()
        qs = QuestionSet(batch='tb', lang='en-US', content=SAMPLE_CONTENT,
                         created_by_id=therapist.id)
        db.session.add(qs)
        db.session.commit()
        qs_id = qs.id

        self._login('+972502222222', 'pass')
        self.client.post(f'/question-sets/{qs_id}/delete', follow_redirects=True)
        self.assertIsNone(QuestionSet.query.get(qs_id))
