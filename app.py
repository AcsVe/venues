import os
import sqlite3
from datetime import datetime
from flask import Flask
from models import db, init_db
from blueprints.public import public_bp
from blueprints.admin import admin_bp

def create_app():
    app = Flask(__name__)

    app.secret_key = os.environ.get('SECRET_KEY', 'acs-dev-secret-change-in-prod')

    # Compress every text response (HTML/CSS/JS/JSON/SVG) with gzip before it
    # leaves the server. This is the single biggest bandwidth win available
    # without touching page content or behavior — the admin dashboard alone
    # is ~220KB of highly repetitive HTML/JS that gzip typically shrinks by
    # 75-85%. It costs a small amount of CPU per request, which is never the
    # bottleneck here, so there's no speed trade-off in practice; if anything
    # pages load faster on slower connections since there's less to transfer.
    # Already-compressed binary types (images, PDFs, zips) are left alone —
    # Flask-Compress only targets its configured text mimetypes by default,
    # so no CPU is wasted trying to re-compress something that won't shrink.
    try:
        from flask_compress import Compress
        Compress(app)
    except ImportError:
        # Falls back to uncompressed responses if the package isn't
        # installed yet (e.g. requirements.txt not redeployed) — never a
        # reason for the whole app to fail to start.
        print("[compress] Flask-Compress not installed — responses will not be gzipped", flush=True)

    # Static files (logo, css, js, sw.js) get a long browser cache instead
    # of being re-downloaded on every visit. sw.js overrides this itself
    # (service workers must never be cached) via its own route below.
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 60 * 60 * 24 * 7  # 7 days

    # A long-lived session so a device logged in once (e.g. the print
    # station) stays logged in across browser/computer restarts instead of
    # needing someone to re-enter credentials every time.
    from datetime import timedelta
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)

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
        # Never cache the service worker itself — the 7-day default static
        # cache set above would otherwise delay every future PWA update by
        # up to a week. Browsers already re-check this file on their own
        # schedule; this just stops any long-lived caching from adding to it.
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    # Background job: nudge teachers who haven't submitted the device
    # handover form a while after their approved period ended.
    # NOTE: assumes a single worker process (WEB_CONCURRENCY=1) — running
    # multiple gunicorn workers would start one scheduler per worker and
    # could send duplicate reminders.
    if os.environ.get('DISABLE_REMINDERS') != '1':
        try:
            from zoneinfo import ZoneInfo
            from apscheduler.schedulers.background import BackgroundScheduler
            from utils.Reminders import (check_and_send_reminders, check_and_send_scheduled_reminders,
                                          check_and_send_daily_staff_summary, check_and_send_upcoming_period_reminders)

            def _within_active_hours():
                """No one uses the system between 11pm and 6am. Skipping the
                scheduled jobs entirely during that window (a plain clock
                check, no DB access at all) gives the database several
                unbroken idle hours every night instead of being pinged
                every few minutes around the clock — on a host that bills
                by active compute time (e.g. Neon's free tier, which
                auto-suspends after a few idle minutes), that's the
                difference between it suspending nightly and never
                suspending at all."""
                hour = datetime.now(ZoneInfo('Asia/Amman')).hour
                return not (hour >= 23 or hour < 6)

            def _guarded(fn):
                return lambda: (fn(app) if _within_active_hours() else None)

            scheduler = BackgroundScheduler(daemon=True)
            scheduler.add_job(
                func=_guarded(check_and_send_reminders),
                trigger='interval',
                minutes=30,
                id='checkout_reminders',
                replace_existing=True,
            )
            # Custom admin/teacher reminders name an exact time, so this
            # runs fairly often to actually fire close to that time — but
            # a 15-minute margin is plenty, no one needs it to the minute.
            scheduler.add_job(
                func=_guarded(check_and_send_scheduled_reminders),
                trigger='interval',
                minutes=15,
                id='scheduled_reminders',
                replace_existing=True,
            )
            # Once-a-day "bookings today" summary for opted-in staff.
            scheduler.add_job(
                func=_guarded(check_and_send_daily_staff_summary),
                trigger='interval',
                minutes=10,
                id='staff_daily_summary',
                replace_existing=True,
            )
            # Per-booking "your period starts soon" nudge for opted-in staff —
            # runs fairly often so it fires reasonably close to the
            # configured lead time, but doesn't need to be exact.
            scheduler.add_job(
                func=_guarded(check_and_send_upcoming_period_reminders),
                trigger='interval',
                minutes=15,
                id='staff_upcoming_reminders',
                replace_existing=True,
            )
            scheduler.start()
        except Exception as e:
            print(f"[reminders] scheduler failed to start: {e}", flush=True)

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=False)
