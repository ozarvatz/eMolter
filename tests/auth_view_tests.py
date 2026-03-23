from tests.base import BaseTest
from automated_survey_flask.models import User
from automated_survey_flask import db


class AuthViewTest(BaseTest):
    def test_login_page_loads(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Login', response.data)

    def test_login_success(self):
        user = User(phone='+972501234567', nickname='Test')
        user.set_password('testpass')
        db.session.add(user)
        db.session.commit()

        response = self.client.post('/login', data={
            'phone': '+972501234567',
            'password': 'testpass'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Dashboard', response.data)

    def test_login_invalid_credentials(self):
        user = User(phone='+972501234567', nickname='Test')
        user.set_password('testpass')
        db.session.add(user)
        db.session.commit()

        response = self.client.post('/login', data={
            'phone': '+972501234567',
            'password': 'wrongpass'
        })

        self.assertIn(b'Invalid phone number or password', response.data)

    def test_login_deleted_user(self):
        user = User(phone='+972501234567', nickname='Test')
        user.set_password('testpass')
        user.deleted = True
        db.session.add(user)
        db.session.commit()

        response = self.client.post('/login', data={
            'phone': '+972501234567',
            'password': 'testpass'
        })

        self.assertIn(b'Invalid phone number or password', response.data)

    def test_logout(self):
        user = User(phone='+972501234567', nickname='Test')
        user.set_password('testpass')
        db.session.add(user)
        db.session.commit()

        # Login first
        self.client.post('/login', data={
            'phone': '+972501234567',
            'password': 'testpass'
        })

        # Then logout
        response = self.client.get('/logout', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'logged out', response.data)

    def test_register_requires_superuser(self):
        regular_user = User(phone='+972501234567', nickname='Regular')
        regular_user.set_password('pass')
        db.session.add(regular_user)
        db.session.commit()

        # Login as regular user
        self.client.post('/login', data={
            'phone': '+972501234567',
            'password': 'pass'
        })

        # Try to access register page
        response = self.client.get('/register', follow_redirects=True)
        self.assertIn(b'Only superusers', response.data)

    def test_register_superuser_can_create_therapist(self):
        superuser = User(phone='+972501234567', nickname='Super', is_superuser=True)
        superuser.set_password('pass')
        db.session.add(superuser)
        db.session.commit()

        # Login as superuser
        self.client.post('/login', data={
            'phone': '+972501234567',
            'password': 'pass'
        })

        # Create new therapist
        response = self.client.post('/register', data={
            'phone': '+972502345678',
            'nickname': 'New Therapist',
            'password': 'newpass',
            'batch': 'basic',
            'language': 'he-IL'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        new_therapist = User.query.filter_by(phone='+972502345678').first()
        self.assertIsNotNone(new_therapist)
        self.assertEqual(new_therapist.nickname, 'New Therapist')
