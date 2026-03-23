from tests.base import BaseTest
from automated_survey_flask.models import User
from automated_survey_flask import db


class UserModelTest(BaseTest):
    def test_create_user(self):
        user = User(phone='+972501234567', nickname='Test User')
        user.set_password('testpass')
        db.session.add(user)
        db.session.commit()

        self.assertIsNotNone(user.id)
        self.assertEqual(user.phone, '+972501234567')
        self.assertTrue(user.check_password('testpass'))
        self.assertFalse(user.check_password('wrongpass'))

    def test_user_password_hashing(self):
        user = User(phone='+972501234567', nickname='Test')
        user.set_password('testpass')

        # Password should be hashed, not stored as plain text
        self.assertNotEqual(user.password_hash, 'testpass')
        self.assertTrue(user.check_password('testpass'))

    def test_soft_delete(self):
        user = User(phone='+972501234567', nickname='Test')
        user.set_password('pass')
        db.session.add(user)
        db.session.commit()

        user_id = user.id
        user.deleted = True
        db.session.commit()

        # Should not find deleted user with active() classmethod
        found = User.active().filter_by(id=user_id).first()
        self.assertIsNone(found)

        # Should find with direct query
        found = User.query.filter_by(id=user_id).first()
        self.assertIsNotNone(found)
        self.assertTrue(found.deleted)

    def test_superuser_flag(self):
        superuser = User(phone='+972501234567', nickname='Super', is_superuser=True)
        superuser.set_password('pass')
        regular_user = User(phone='+972502345678', nickname='Regular', is_superuser=False)
        regular_user.set_password('pass')

        db.session.add(superuser)
        db.session.add(regular_user)
        db.session.commit()

        self.assertTrue(superuser.is_superuser)
        self.assertFalse(regular_user.is_superuser)
