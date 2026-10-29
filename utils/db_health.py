"""When the database cannot be reached, every page and request says so plainly (with the likely cause) instead
of a bare «Internal Server Error», and /healthz reports the state of the database for a quick check.

Only a short cause code and the error class are shown or logged — never the connection string or its password.
"""
from flask import jsonify, render_template_string, request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

CAUSES = {
    'quota': ('تجاوزت قاعدة البيانات السحابية حدّ وقت التشغيل المسموح به في خطتها لهذا الشهر.',
              'The cloud database has used up its compute-time allowance for this month.'),
    'auth': ('رُفضت بيانات الدخول إلى قاعدة البيانات (اسم المستخدم أو كلمة المرور).',
             'The database rejected the sign-in (user name or password).'),
    'not_found': ('قاعدة البيانات أو عنوانها غير موجود (ربما حُذفت أو تغيّر عنوانها).',
                  'The database or its address no longer exists (it may have been deleted or moved).'),
    'unreachable': ('تعذّر الاتصال بخادم قاعدة البيانات (متوقف أو لا يستجيب).',
                    'The database server could not be reached (stopped or not responding).'),
    'locked': ('قاعدة البيانات مقفلة من عملية أخرى.', 'The database is locked by another process.'),
    'other': ('حدث خطأ في قاعدة البيانات.', 'A database error occurred.'),
}


def classify(exc):
    m = str(getattr(exc, 'orig', None) or exc).lower()
    if 'quota' in m or 'compute time' in m or 'data transfer' in m:
        return 'quota'
    if 'password authentication' in m or 'authentication failed' in m or 'role "' in m:
        return 'auth'
    if 'does not exist' in m and ('database' in m or 'endpoint' in m) or 'could not translate host' in m \
            or 'endpoint could not be found' in m or 'unknown host' in m:
        return 'not_found'
    if 'database is locked' in m:
        return 'locked'
    if 'timeout' in m or 'timed out' in m or 'connection refused' in m or 'could not connect' in m \
            or 'server closed the connection' in m or 'connection reset' in m or 'ssl' in m:
        return 'unreachable'
    return 'other'


_PAGE = """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>الخدمة غير متاحة مؤقتاً</title>
<style>body{font-family:Tajawal,Tahoma,Arial,sans-serif;background:linear-gradient(135deg,#0C67EC,#0847B0 50%,#0a3a8a);
margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;box-sizing:border-box}
.box{background:#fff;border-radius:16px;padding:32px 26px;max-width:460px;width:100%;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.15)}
h1{font-size:1.25rem;margin:10px 0 12px;color:#b42318}p{color:#344054;line-height:1.8;margin:6px 0}
.en{direction:ltr;color:#667085;font-size:.9rem;border-top:1px solid #eaecf0;margin-top:16px;padding-top:12px}
code{background:#f2f4f7;border-radius:6px;padding:2px 8px;font-size:.85rem}</style></head><body><div class="box">
<div style="font-size:42px">&#9888;</div><h1>الخدمة غير متاحة مؤقتاً</h1>
<p>تعذّر الوصول إلى قاعدة البيانات.</p><p><b>{{ ar }}</b></p>
<div class="en"><b>Service temporarily unavailable</b><br>The database could not be reached. {{ en }}</div>
<p style="margin-top:14px"><code>{{ code }}</code></p></div></body></html>"""


def _response(code):
    ar, en = CAUSES.get(code, CAUSES['other'])
    if request.path.startswith('/api/') or '/api/' in request.path or request.is_json:
        return jsonify({'error': 'db_unavailable', 'cause': code, 'message': ar, 'message_en': en}), 503
    return render_template_string(_PAGE, ar=ar, en=en, code=f'db:{code}'), 503


def install(app):
    def _rollback():
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass

    @app.errorhandler(OperationalError)
    @app.errorhandler(DBAPIError)
    def _db_down(e):
        _rollback()
        code = classify(e)
        app.logger.error('database unavailable (%s): %s', code, type(getattr(e, 'orig', None) or e).__name__)
        return _response(code)

    @app.errorhandler(SQLAlchemyError)
    def _db_error(e):
        _rollback()
        app.logger.exception('database error')
        return _response(classify(e))

    @app.route('/healthz')
    def healthz():
        from models import db
        try:
            db.session.execute(text('SELECT 1'))
            db.session.rollback()
            return jsonify({'ok': True, 'database': 'ok'})
        except Exception as e:
            _rollback()
            code = classify(e)
            ar, en = CAUSES.get(code, CAUSES['other'])
            return jsonify({'ok': False, 'database': code, 'message': ar, 'message_en': en}), 503
