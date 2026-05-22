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
    app.config['ORG_AR']        = os.environ.get('ORG_AR', 'الجمعية الثقافية العربية')
    app.config['ORG_EN']        = os.environ.get('ORG_EN', 'Arab Cultural Society')
    app.config['LOGO_URL']      = os.environ.get('LOGO_URL', 'https://i.postimg.cc/S2W2hXZ6/acs.png')
    app.config['ACCENT_COLOR']  = os.environ.get('ACCENT_COLOR', '#EBB37B')
    app.config['BREVO_API_KEY'] = os.environ.get('BREVO_API_KEY', '')
    app.config['SENDER_EMAIL']  = os.environ.get('SENDER_EMAIL', 'acsvenues@gmail.com')
    app.config['SENDER_NAME']   = os.environ.get('SENDER_NAME', 'ACS Booking')

    db.init_app(app)
    with app.app_context():
        init_db(app)

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=False)
