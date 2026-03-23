from flask_script import Manager
from flask_migrate import Migrate, MigrateCommand

# from flask_migrate import upgrade as upgrade_database
from automated_survey_flask import app, db, parsers, prepare_app
from automated_survey_flask.models import Survey, Question, Patient, User  # <--- Add Patient and User here

prepare_app()
migrate = Migrate(app, db)

manager = Manager(app)
manager.add_command('db', MigrateCommand)


@manager.command
def test():
    """Run the unit tests."""
    import sys
    import unittest

    prepare_app(environment='testing')
    tests = unittest.TestLoader().discover('.', pattern="*_tests.py")
    test_result = unittest.TextTestRunner(verbosity=2).run(tests)

    if not test_result.wasSuccessful():
        sys.exit(1)


@manager.command
def dbseed():
    with open('survey.json') as survey_file:
        db.session.add(parsers.survey_from_json(survey_file.read()))
        db.session.commit()

import json

@manager.command
def seed_patients():
    """Seeds the patients table from patients.json"""
    with open('patients.json', 'r', encoding='utf-8') as f:
        patients_data = json.load(f)
        
    for p in patients_data:
        # Avoid duplicates by checking phone number
        exists = Patient.query.filter_by(phone=p['phone']).first()
        if not exists:
            new_patient = Patient(
                name=p['name'],
                phone=p['phone'],
                language=p.get('language', 'he-IL')
            )
            db.session.add(new_patient)
    
    db.session.commit()
    print("✅ Patients seeded successfully!")


@manager.command
def create_superuser():
    """Create initial superuser account"""
    phone = input("Enter phone number (e.g., +972501234567): ")
    nickname = input("Enter nickname: ")
    password = input("Enter password: ")

    if User.query.filter_by(phone=phone).first():
        print("User with this phone already exists!")
        return

    superuser = User(
        phone=phone,
        nickname=nickname,
        is_superuser=True
    )
    superuser.set_password(password)
    db.session.add(superuser)
    db.session.commit()
    print(f"✅ Superuser created: {superuser.nickname} ({superuser.phone})")


if __name__ == "__main__":
    manager.run()
