"""أدوات مشتركة للخادم المحلي (الكمبيوتر) والهاتف: قراءة .env ومسارات البيانات والنسخ."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EN = os.environ.get('IAJ_LANG', os.environ.get('LANG_UI', '')) == 'en'


def t(ar, en):
    return en if EN else ar


def load_env():
    """يقرأ .env (لا يستبدل متغيراً موجوداً مسبقاً). الافتراضي: قاعدة SQLite ومجلد data داخل المشروع."""
    path = os.path.join(BASE, '.env')
    if os.path.isfile(path):
        with open(path, encoding='utf-8-sig', errors='ignore') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == 'VAPID_PRIVATE_KEY':
                    v = v.replace('\\n', '\n')   # مفتاح PEM مكتوب في سطر واحد
                if k and k not in os.environ:
                    os.environ[k] = v
    os.environ.setdefault('DATA_DIR', os.path.join(BASE, 'data'))
    if not os.environ.get('DATABASE_URL'):
        os.environ.pop('DATABASE_URL', None)   # فارغ = SQLite المحلية


def data_dir():
    return os.environ.get('DATA_DIR') or os.path.join(BASE, 'data')


def db_path():
    return os.path.join(data_dir(), 'acs_booking.db')


def uploads_dir():
    return os.path.join(data_dir(), 'uploads')


def backup_root():
    return os.environ.get('BACKUP_DIR') or os.path.join(BASE, 'backups')


def stamp():
    """وقت عمّان (للترتيب ولأسماء الملفات)"""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Amman')).strftime('%Y-%m-%d_%H%M%S')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d_%H%M%S')


def die(msg, code=1):
    print('\n✖ ' + msg)
    sys.exit(code)
