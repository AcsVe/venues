"""تشغيل نظام الحجوزات على الكمبيوتر أو الهاتف بخادم Waitress (بديل gunicorn — يعمل على Windows وTermux).

    python serve.py

• العنوان المحلي: http://localhost:8002  (PORT في .env) — والعام عبر Cloudflare: https://2.iajaward.org
• البيانات: مجلد data (القاعدة acs_booking.db + المرفقات uploads) — DATA_DIR في .env لتغييره
• التذكيرات تعمل من داخل التطبيق نفسه (APScheduler)
• السجل: logs/server.log
"""
import logging
import logging.handlers
import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'ops'))
from common import load_env  # noqa: E402

load_env()
os.makedirs(os.path.join(BASE, 'logs'), exist_ok=True)
handler = logging.handlers.RotatingFileHandler(os.path.join(BASE, 'logs', 'server.log'),
                                               maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
log = logging.getLogger('venues')


def reminders_loop(app):
    every = max(60, int(os.environ.get('REMINDER_EVERY', '600') or 600))
    time.sleep(60)
    while True:
        try:
            from utils.reminder_utils import check_and_send_reminders
            with app.app_context():
                res = check_and_send_reminders()
            if res.get('reminded'):
                log.info('Reminders: %s', res)
        except Exception:
            log.exception('Reminder check failed')
        time.sleep(every)


def main():
    from app import app
    from waitress import serve
    port = int(os.environ.get('PORT') or 8002)
    log.info('VENUES running on http://localhost:%s  (site: %s)', port, app.config.get('BASE_URL') or '-')
    serve(
        app,
        host=os.environ.get('HOST', '0.0.0.0'),
        port=port,
        threads=int(os.environ.get('THREADS') or 8),
        trusted_proxy='127.0.0.1',            # Cloudflare Tunnel يتصل من نفس الجهاز ويمرّر https
        trusted_proxy_count=1,
        trusted_proxy_headers={'x-forwarded-proto', 'x-forwarded-host', 'x-forwarded-for'},
        clear_untrusted_proxy_headers=True,
        channel_timeout=300,
        ident='VENUES',
    )


if __name__ == '__main__':
    try:
        main()
    except Exception:
        log.exception('Server failed to start')
        raise
