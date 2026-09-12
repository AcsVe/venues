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
    # Neon (and most managed Postgres) silently close idle connections
    # after a short timeout; without pool_pre_ping, SQLAlchemy tries to
    # reuse that dead connection and raises "SSL connection has been
    # closed unexpectedly". pre_ping tests each connection with a cheap
    # query before use and transparently reconnects if it's gone stale.
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }
    app.config['UPLOAD_FOLDER'] = upload_dir
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

    app.config['ADMIN_USER']    = os.environ.get('ADMIN_USER', 'admin')
    app.config['ADMIN_PASS']    = os.environ.get('ADMIN_PASS', 'acs2024')
    app.config['ORG_AR']        = os.environ.get('ORG_AR', 'مدرسة الرائد العربي')
    app.config['ORG_EN']        = os.environ.get('ORG_EN', 'Al-Raed Al-Arabi School')
    app.config['LOGO_URL']      = os.environ.get('LOGO_URL', '/static/logo.png')
    app.config['ACCENT_COLOR']  = os.environ.get('ACCENT_COLOR', '#3D5A80')
    app.config['BASE_URL']      = os.environ.get('BASE_URL', os.environ.get('RENDER_EXTERNAL_URL', '')).rstrip('/')

    # Microsoft 365 (Graph API) — sole email provider
    app.config['MS_TENANT_ID']     = os.environ.get('MS_TENANT_ID', '')
    app.config['MS_CLIENT_ID']     = os.environ.get('MS_CLIENT_ID', '')
    app.config['MS_CLIENT_SECRET'] = os.environ.get('MS_CLIENT_SECRET', '')
    app.config['MS_SENDER_EMAIL']  = os.environ.get('MS_SENDER_EMAIL', '')

    # Web Push (VAPID) — lets the server send real push notifications that
    # arrive even when the admin's browser/PWA is fully closed. A working
    # key pair ships by default so this needs no extra setup; override via
    # env vars if you want your own keys.
    app.config['VAPID_PRIVATE_KEY'] = os.environ.get('VAPID_PRIVATE_KEY', """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg2ovCMiqrEluskjI5
qXlX0w2G814mqR4PxzhqSHHfdQWhRANCAAROV1WoRapPaAcYMgkZMIGk65s+IZpk
XEqPZq2IE3k9g455XAokrxI6N2LhLDhtu3SMlEulXPw9IcShiKeKf2xO
-----END PRIVATE KEY-----""")
    app.config['VAPID_PUBLIC_KEY'] = os.environ.get(
        'VAPID_PUBLIC_KEY', 'BE5XVahFqk9oBxgyCRkwgaTrmz4hmmRcSo9mrYgTeT2DjnlcCiSvEjo3YuEsOG27dIyUS6Vc_D0hxKGIp4p_bE4')
    app.config['VAPID_CLAIMS_EMAIL'] = os.environ.get('VAPID_CLAIMS_EMAIL', 'mailto:admin@example.com')

    db.init_app(app)
    with app.app_context():
        init_db(app)

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Served at the ROOT (not /static/sw.js) so its default scope covers the
    # whole site — a service worker's scope is limited to its own directory
    # unless served from root, which is why /admin/ never saw it as "ready".
    @app.route('/sw.js')
    def service_worker():
        from flask import send_from_directory, make_response
        resp = make_response(send_from_directory(app.static_folder, 'sw.js'))
        resp.headers['Content-Type'] = 'application/javascript'
        resp.headers['Service-Worker-Allowed'] = '/'
        return resp

    # Background job: nudge teachers who haven't submitted the device
    # handover form a while after their approved period ended.
    # NOTE: assumes a single worker process (WEB_CONCURRENCY=1) — running
    # multiple gunicorn workers would start one scheduler per worker and
    # could send duplicate reminders.
    if os.environ.get('DISABLE_REMINDERS') != '1':
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from utils.reminders import check_and_send_reminders, check_and_send_scheduled_reminders

            scheduler = BackgroundScheduler(daemon=True)
            scheduler.add_job(
                func=lambda: check_and_send_reminders(app),
                trigger='interval',
                minutes=30,
                id='checkout_reminders',
                replace_existing=True,
            )
            # Custom admin/teacher reminders name an exact time, so this
            # runs more often to actually fire close to that time.
            scheduler.add_job(
                func=lambda: check_and_send_scheduled_reminders(app),
                trigger='interval',
                minutes=5,
                id='scheduled_reminders',
                replace_existing=True,
            )
            scheduler.start()
        except Exception as e:
            print(f"[reminders] scheduler failed to start: {e}", flush=True)

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=False)
