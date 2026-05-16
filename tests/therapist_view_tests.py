from tests.base import BaseTest
from automated_survey_flask.models import User, Patient
from automated_survey_flask import db
from automated_survey_flask.therapist_view import _parse_utm_string, _format_utm_dict


class TherapistViewTest(BaseTest):
    def setUp(self):
        super().setUp()
        self.therapist = User(phone='+972501234567', nickname='Therapist')
        self.therapist.set_password('pass')
        db.session.add(self.therapist)
        db.session.commit()

        # Login
        self.client.post('/login', data={
            'phone': '+972501234567',
            'password': 'pass'
        })

    def test_dashboard_requires_login(self):
        # Logout first
        self.client.get('/logout')

        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_dashboard_loads(self):
        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Welcome', response.data)
        self.assertIn(self.therapist.nickname.encode(), response.data)

    def test_create_patient(self):
        response = self.client.post('/patients/new', data={
            'name': 'Test Patient',
            'nickname': 'Nick',
            'phone': '+972509999999',
            'batch': 'basic',
            'language': 'he-IL'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        patient = Patient.query.filter_by(phone='+972509999999').first()
        self.assertIsNotNone(patient)
        self.assertEqual(patient.therapist_id, self.therapist.id)
        self.assertEqual(patient.name, 'Test Patient')
        self.assertEqual(patient.nickname, 'Nick')

    def test_patient_list_shows_only_own_patients(self):
        # Create patient for this therapist
        patient1 = Patient(
            name='Patient 1',
            phone='+972509999991',
            therapist_id=self.therapist.id
        )
        db.session.add(patient1)

        # Create another therapist and their patient
        other_therapist = User(phone='+972502345678', nickname='Other')
        other_therapist.set_password('pass')
        db.session.add(other_therapist)
        db.session.commit()

        patient2 = Patient(
            name='Patient 2',
            phone='+972509999992',
            therapist_id=other_therapist.id
        )
        db.session.add(patient2)
        db.session.commit()

        response = self.client.get('/patients')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Patient 1', response.data)
        self.assertNotIn(b'Patient 2', response.data)

    def test_edit_patient(self):
        patient = Patient(
            name='Original Name',
            phone='+972509999999',
            therapist_id=self.therapist.id
        )
        db.session.add(patient)
        db.session.commit()

        response = self.client.post(f'/patients/{patient.id}/edit', data={
            'name': 'Updated Name',
            'nickname': 'Updated Nick',
            'phone': '+972509999999',
            'batch': 'basic',
            'language': 'de-DE'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        updated_patient = Patient.query.get(patient.id)
        self.assertEqual(updated_patient.name, 'Updated Name')
        self.assertEqual(updated_patient.nickname, 'Updated Nick')
        self.assertEqual(updated_patient.language, 'de-DE')

    def test_cannot_edit_other_therapist_patient(self):
        # Create another therapist and their patient
        other_therapist = User(phone='+972502345678', nickname='Other')
        other_therapist.set_password('pass')
        db.session.add(other_therapist)
        db.session.commit()

        other_patient = Patient(
            name='Other Patient',
            phone='+972509999999',
            therapist_id=other_therapist.id
        )
        db.session.add(other_patient)
        db.session.commit()

        # Try to edit other therapist's patient
        response = self.client.get(f'/patients/{other_patient.id}/edit')
        self.assertEqual(response.status_code, 403)

    def test_create_patient_with_utm_params(self):
        response = self.client.post('/patients/new', data={
            'name': 'UTM Patient',
            'phone': '+972509998888',
            'batch': 'basic',
            'language': 'he-IL',
            'utm_params': 'utm_source=google,utm_medium=cpc,campaign_id=42',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        patient = Patient.query.filter_by(phone='+972509998888').first()
        self.assertIsNotNone(patient)
        self.assertEqual(patient.utm_params, {
            'utm_source': 'google',
            'utm_medium': 'cpc',
            'campaign_id': '42',
        })

    def test_soft_delete_patient(self):
        patient = Patient(
            name='Test Patient',
            phone='+972509999999',
            therapist_id=self.therapist.id
        )
        db.session.add(patient)
        db.session.commit()

        patient_id = patient.id

        response = self.client.post(f'/patients/{patient_id}/delete', follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        # Patient should be soft deleted
        deleted_patient = Patient.query.get(patient_id)
        self.assertIsNotNone(deleted_patient)
        self.assertTrue(deleted_patient.deleted)

        # Should not appear in active query
        active_patient = Patient.active().filter_by(id=patient_id).first()
        self.assertIsNone(active_patient)


class UtmHelpersTest(BaseTest):
    def test_parse_basic(self):
        self.assertEqual(
            _parse_utm_string('utm_source=google,utm_medium=cpc'),
            {'utm_source': 'google', 'utm_medium': 'cpc'},
        )

    def test_parse_tolerates_whitespace(self):
        self.assertEqual(
            _parse_utm_string(' utm_source = google , foo = bar '),
            {'utm_source': 'google', 'foo': 'bar'},
        )

    def test_parse_empty_and_none(self):
        self.assertEqual(_parse_utm_string(''), {})
        self.assertEqual(_parse_utm_string(None), {})

    def test_parse_skips_junk_pairs(self):
        # "bogus" has no "=", "=2" has no key
        self.assertEqual(
            _parse_utm_string('a=1,bogus,=2,c=3'),
            {'a': '1', 'c': '3'},
        )

    def test_format_roundtrip(self):
        d = {'utm_source': 'google', 'utm_medium': 'cpc'}
        self.assertEqual(_parse_utm_string(_format_utm_dict(d)), d)

    def test_format_empty(self):
        self.assertEqual(_format_utm_dict(None), '')
        self.assertEqual(_format_utm_dict({}), '')
