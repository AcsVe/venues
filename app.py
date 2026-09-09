import os
import sqlite3
from flask import Flask
from models import db, init_db
from blueprints.public import public_bp
from blueprints.admin import admin_bp

def create_app():
    app = Flask(__name__)

    app.secret_key = os.environ.get('SECRET_KEY', 'acs-dev-secret-change-in-prod')

    if os.path.isdir('/data'):
        db_path = '/data/acs_booking.db'
        upload_dir = '/data/uploads'
    else:
        db_path = os.path.join(os.path.dirname(__file__), 'acs_booking.db')
        upload_dir = os.path.join(os.path.dirname(__file__), 'uploads')

    os.makedirs(upload_dir, exist_ok=True)

    database_url = os.environ.get('DATABASE_URL', f'sqlite:///{db_path}')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = upload_dir
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

    app.config['ADMIN_USER']    = os.environ.get('ADMIN_USER', 'admin')
    app.config['ADMIN_PASS']    = os.environ.get('ADMIN_PASS', 'acs2024')
    app.config['ORG_AR']        = os.environ.get('ORG_AR', 'مدرسة الرائد العربي')
    app.config['ORG_EN']        = os.environ.get('ORG_EN', 'Al-Raed Al-Arabi School')
    app.config['LOGO_URL']      = os.environ.get('LOGO_URL', '/static/logo.jpg')
    app.config['ACCENT_COLOR']  = os.environ.get('ACCENT_COLOR', '#3D5A80')
    app.config['BASE_URL']      = os.environ.get('BASE_URL', os.environ.get('RENDER_EXTERNAL_URL', '')).rstrip('/')

    # Microsoft 365 (Graph API) — sole email provider
    app.config['MS_TENANT_ID']     = os.environ.get('MS_TENANT_ID', '')
    app.config['MS_CLIENT_ID']     = os.environ.get('MS_CLIENT_ID', '')
    app.config['MS_CLIENT_SECRET'] = os.environ.get('MS_CLIENT_SECRET', '')
    app.config['MS_SENDER_EMAIL']  = os.environ.get('MS_SENDER_EMAIL', '')

    db.init_app(app)
    with app.app_context():
        init_db(app)

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Background job: nudge teachers who haven't submitted the device
    # handover form a while after their approved period ended.
    # NOTE: assumes a single worker process (WEB_CONCURRENCY=1) — running
    # multiple gunicorn workers would start one scheduler per worker and
    # could send duplicate reminders.
    if os.environ.get('DISABLE_REMINDERS') != '1':
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from utils.reminders import check_and_send_reminders

            scheduler = BackgroundScheduler(daemon=True)
            scheduler.add_job(
                func=lambda: check_and_send_reminders(app),
                trigger='interval',
                minutes=30,
                id='checkout_reminders',
                replace_existing=True,
            )
            scheduler.start()
        except Exception as e:
            print(f"[reminders] scheduler failed to start: {e}", flush=True)

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=False)
