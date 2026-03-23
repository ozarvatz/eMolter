from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from automated_survey_flask import app, db
from automated_survey_flask.models import User


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        phone = request.form.get('phone')
        password = request.form.get('password')
        remember = request.form.get('remember', False)

        user = User.query.filter_by(phone=phone, deleted=False).first()

        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))

        flash('Invalid phone number or password', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'success')
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if not current_user.is_superuser:
        flash('Only superusers can register new therapists', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        phone = request.form.get('phone')
        nickname = request.form.get('nickname')
        password = request.form.get('password')
        batch = request.form.get('batch', '')
        language = request.form.get('language', 'he-IL')
        is_superuser = request.form.get('is_superuser') == 'on'

        if User.query.filter_by(phone=phone).first():
            flash('User with this phone already exists', 'error')
        else:
            user = User(
                phone=phone,
                nickname=nickname,
                batch=batch,
                language=language,
                is_superuser=is_superuser
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'Therapist {nickname} created successfully', 'success')
            return redirect(url_for('therapist_list'))

    return render_template('register.html')
