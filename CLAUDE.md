# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

eMolter is a Flask-based voice prosody analytics system for mental health research. It automates phone-based surveys using Twilio, records patient responses, and analyzes voice characteristics (pitch, jitter, shimmer, HNR) using Praat/Parselmouth to detect potential indicators of mental health conditions.

## Development Commands

### Environment Setup
```bash
# Activate virtual environment
setvenv  # Alias to activate the virtual environment

# Install dependencies
pip install -r requirements.txt

# Note: TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are already configured in the environment

# Update allowed phones for WhatsApp reports (if needed)
export ALLOWED_PHONES=972503220778,972524519706,972546646637,4917640500988
```

### Database Management
```bash
# Initialize database migrations (first time only)
python manage.py db init

# Create a migration after model changes
python manage.py db migrate -m "description of changes"

# Apply migrations to database
python manage.py db upgrade

# Seed survey data
python manage.py dbseed

# Seed patients from patients.json
python manage.py seed_patients
```

### Running Tests
```bash
# Run all unit tests
python manage.py test

# Run specific test file
python -m unittest tests.survey_view_tests
```

### Application Operations
```bash
# Start Flask development server
flask run

# Initiate a survey call
python callMe.py --phone +1234567890 --lang he-IL --name "PatientName" --batch basic

# Process prosody analysis for unprocessed recordings
python prosody.py

# Analyze a specific recording URL
python prosody.py "https://api.twilio.com/recording.wav" 1  # channel 1 or 2

# Export prosody data to CSV
python exp_prosody_2_csv.py
```

### Code Quality
```bash
# Format code with Black
black . --config black.toml

# Run flake8 linter
flake8
```

## Architecture Overview

### Application Structure
- **`automated_survey_flask/__init__.py`**: App factory, Flask/SQLAlchemy initialization
- **`automated_survey_flask/models.py`**: Database models (Survey, Question, Answer, Patient, Call)
- **`automated_survey_flask/views.py`**: Main routes, imports view modules
- **`automated_survey_flask/survey_view.py`**: Core Twilio integration, voice survey flow, WhatsApp reports
- **`automated_survey_flask/config.py`**: Environment configurations (development, testing, production)
- **`manage.py`**: CLI management commands via Flask-Script

Note: `question_view.py` and `answer_view.py` are unused legacy files from original Twilio template code.

### Key External Scripts
- **`callMe.py`**: Initiates outbound Twilio calls, creates initial Call records
- **`prosody.py`**: Praat/Parselmouth analysis - extracts voice features (pitch, jitter, shimmer, HNR, formants)
- **`exp_prosody_2_csv.py`**: Exports Call table with flattened prosody_results JSON to CSV

### Database Models
- **Call**: Central model storing call metadata, recording URLs, transcriptions, and prosody analysis results (JSON)
- **Patient**: Patient information (name, phone, language)
- **Survey/Question/Answer**: Generic survey framework (can be extended)

### Twilio Integration Flow
1. **Call Initiation** (`callMe.py`): Creates outbound call via Twilio API, stores Call record with `call_sid`
2. **Voice Survey** (`/voice` endpoint):
   - Starts real-time transcription listener
   - Reads questions from `questions_{batch}_{lang}.json`
   - Uses TwiML to record patient responses (dual-channel, timeout=2s)
3. **Speech Handling** (`/handle-speech`):
   - Receives recording URL and RecordingSid
   - Updates Call record with recording metadata
   - Redirects to next question or hangs up
4. **Real-time Transcription** (`/handle-realtime-text`):
   - Receives streaming transcription chunks
   - Updates `conversation_text` field in Call model
   - Sends WhatsApp message with transcription (optional)

### Prosody Analysis
- **parselmouth**: Python wrapper for Praat phonetics software
- **Key Metrics**:
  - `mean_pitch_hz`, `pitch_sd_hz`, `pitch_range_hz`: Voice melody and variability
  - `jitter_local`, `shimmer_local`: Voice stability (hoarseness indicators)
  - `mean_hnr_db`: Harmonics-to-noise ratio (voice quality)
  - `degree_of_voice_breaks`: Fluency measure
  - `f1_mean_hz`, `f2_mean_hz`: Vocal tract resonance (formants)
  - `voice_health_score`: 0.0 (clean) to 1.0 (very hoarse), weighted calculation
- **Processing**: `prosody.py` queries `Call.is_processed=False`, downloads recordings, analyzes with Praat, stores results in `Call.prosody_results` JSON field

### Question Configuration
Questions are stored in JSON files: `questions_{batch}_{lang}.json`
- Structure: `{"questions": [...], "messages": [...], "config": {...}}`
- `messages[0-3]`: System messages (SORRY_FAILED, YOU_SAID, THANKS, HELLO)
- `config["voice_algo"]`: Voice model for TTS (e.g., "Google.he-IL-Standard-A")

## Important Notes

### Twilio Considerations
- Recording uses `dual-channel` format - channel 1 is typically patient voice
- Transcription language must match `language_code` in Start transcription
- `transcribe=False` on Record to avoid 15-second processing delay
- Base URL must be HTTPS for production (use ngrok for local testing)

### Security
- WhatsApp reports use `ALLOWED_PHONES` whitelist for access control
- Temporary download links expire after 6000 seconds (TTL_SECONDS)
- Store Twilio credentials in environment variables, never commit

### Database
- `Call.prosody_results` stores complex JSON - use `json_normalize` for CSV export
- `Call.is_processed` flag prevents duplicate prosody analysis
- `Call.is_inexcel` flag tracks export status

### Testing
- Tests use in-memory SQLite (`:memory:`)
- Test environment configured in `config_env_files['testing']`
- Tests located in `tests/` directory with `*_tests.py` naming convention

## Common Development Patterns

### Adding a New Question Batch
1. Create `questions_{batch}_{lang}.json` with proper structure
2. Update `callMe.py` to use new batch name
3. Test with `python callMe.py --batch newbatch ...`

### Adding Prosody Metrics
1. Update `get_prosody_features()` in `prosody.py` to extract new metric
2. Reprocess recordings: set `is_processed=False` in database, run `python prosody.py`
3. Update CSV export if needed in `exp_prosody_2_csv.py`

### Modifying Database Schema
1. Update models in `models.py`
2. Create migration: `python manage.py db migrate -m "description"`
3. Review generated migration in `migrations/versions/`
4. Apply: `python manage.py db upgrade`

### Adding New Language Support
1. Create `questions_{batch}_{lang}.json` with translated questions
2. Update Twilio TTS voice model in config section
3. Set correct `language_code` for transcription (e.g., "he-IL", "de-DE")

## TODO: Planned Changes

### User Management System

**Goal**: Implement therapist user management with patient assignment capabilities.

#### Database Schema Changes
1. **Update Patient model** (`models.py`):
   - Ensure Patient has: `phone`, `nickname`, `batch`, `language`
   - Add `therapist_id` foreign key to link patients to their therapist
   - Add `deleted` boolean field (default False) for soft deletes

2. **Create User/Therapist model** (`models.py`):
   - Fields: `phone`, `nickname`, `batch`, `language` (same as Patient)
   - Add `is_superuser` boolean field (default False)
   - Add `password_hash` for authentication
   - Add `deleted` boolean field (default False) for soft deletes
   - Add relationship to patients: `patients = db.relationship('Patient', backref='therapist')`

3. **Implement soft delete pattern**:
   - Never actually delete rows from Patient or User/Therapist tables
   - Set `deleted=True` instead of using `db.session.delete()`
   - Update queries to filter out deleted records: `.filter_by(deleted=False)`
   - Consider adding `deleted_at` timestamp for audit trail

4. **Create migrations**:
   ```bash
   python manage.py db migrate -m "Add User model and update Patient with therapist relationship"
   python manage.py db upgrade
   ```

#### New Controllers/Views
1. **Create `automated_survey_flask/auth_view.py`**:
   - `/login` - Therapist login
   - `/logout` - Therapist logout
   - `/register` - Superuser-only endpoint to create new therapists

2. **Create `automated_survey_flask/therapist_view.py`**:
   - `/therapist/dashboard` - Therapist dashboard showing their patients
   - `/therapist/patients` - List all patients for logged-in therapist
   - `/therapist/patients/add` - Form to add new patient
   - `/therapist/patients/<id>/edit` - Edit patient details
   - `/therapist/patients/<id>/delete` - Delete patient
   - `/therapist/patients/<id>/call` - Initiate call to specific patient

3. **Create `automated_survey_flask/admin_view.py`**:
   - `/admin/therapists` - List all therapists (superuser only)
   - `/admin/therapists/add` - Add new therapist (superuser only)
   - `/admin/therapists/<id>/edit` - Edit therapist (superuser only)
   - `/admin/therapists/<id>/delete` - Delete therapist (superuser only)

#### Authentication & Authorization
- Implement Flask-Login for session management
- Add login_required decorator for protected routes
- Add superuser_required decorator for admin routes
- Store password hashes using werkzeug.security

#### UI Changes
- Create login page template
- Create therapist dashboard template
- Create patient management forms (add/edit)
- Add navigation menu for authenticated users

#### Security Considerations
- Hash passwords before storing in database
- Implement CSRF protection (currently commented out in `__init__.py`)
- Add rate limiting for login attempts
- Validate phone numbers before storage
- Ensure therapists can only access their own patients' data

#### Tests
Create comprehensive test coverage in `tests/` directory:

1. **Model Tests** (`tests/user_model_tests.py`, `tests/patient_model_tests.py`):
   - Test User/Therapist creation and validation
   - Test Patient creation with therapist relationship
   - Test soft delete functionality (deleted flag behavior)
   - Test password hashing and verification
   - Test is_superuser permissions

2. **Authentication Tests** (`tests/auth_view_tests.py`):
   - Test login with valid/invalid credentials
   - Test logout functionality
   - Test registration (superuser only)
   - Test unauthorized access to protected routes
   - Test session management

3. **Therapist View Tests** (`tests/therapist_view_tests.py`):
   - Test therapist can view only their own patients
   - Test adding new patient
   - Test editing patient details
   - Test soft deleting patient
   - Test therapist cannot access other therapists' patients
   - Test initiating call to patient

4. **Admin View Tests** (`tests/admin_view_tests.py`):
   - Test superuser can view all therapists
   - Test superuser can add/edit/delete therapists
   - Test non-superuser cannot access admin routes
   - Test soft delete for therapists

5. **Integration Tests**:
   - Test complete workflow: login → add patient → initiate call
   - Test soft delete cascading behavior
   - Test queries properly filter deleted records
