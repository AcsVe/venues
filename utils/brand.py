"""هوية المنظمة: الاسمان واللون والشعار — تُضبط من لوحة التحكم ← الإعدادات.

• القيم في جدول app_settings (org_ar، org_en، accent_color، brand_logo)، وعند غيابها تُستخدم قيم .env
  (ORG_AR، ORG_EN، ACCENT_COLOR).
• الشعار المرفوع يُحفظ في مجلد المرفقات: uploads/brand/logo.<ext>، ويُعرض عبر /uploads/brand/...
• إلى أن يُرفع شعار تظهر أيقونة النظام static/brand/system.svg (الشريط الجانبي، صفحة الدخول، الصفحات العامة).
• ترحيل لمرة واحدة عند الإقلاع (ensure_brand): التثبيت القائم الذي فيه بيانات يحتفظ بشعاره السابق
  (LOGO_URL أو static/logo.png) كشعار مرفوع يمكن إزالته من الإعدادات؛ التثبيت الجديد يبدأ بأيقونة النظام.
"""
import os
import re
import shutil

from flask import current_app

SYSTEM_ICON = '/static/brand/system.svg'
SYSTEM_ICON_PNG = '/static/icon-192.png'      # للبريد: كثير من برامج البريد لا تعرض SVG
DEFAULT_ACCENT = '#0C67EC'
LOGO_EXTS = {'png', 'jpg', 'jpeg', 'webp', 'svg'}
_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')


def _setting(key, default=''):
    from models import AppSetting
    try:
        return AppSetting.get_str(key, default)
    except Exception:
        _rollback()
        return default


def _rollback():
    # PostgreSQL refuses every later query of the request after a failed one until the transaction is rolled back
    try:
        from models import db
        db.session.rollback()
    except Exception:
        pass


def _brand_dir():
    d = os.path.join(current_app.config['UPLOAD_FOLDER'], 'brand')
    os.makedirs(d, exist_ok=True)
    return d


def _logo_url(value):
    """brand_logo: اسم ملف داخل uploads/brand، أو رابط http(s) كامل (من LOGO_URL قديم)."""
    if not value:
        return None
    if value.startswith('http://') or value.startswith('https://'):
        return value
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'brand', value)
    if not os.path.isfile(path) and not _restore(value, path):
        # the files were wiped (e.g. a redeploy without a persistent disk): the school logo shipped with the
        # system (static/logo.png) is used rather than falling back to the system icon
        shipped = os.path.join(current_app.static_folder, value)
        if value.startswith('logo.') and os.path.isfile(shipped):
            return f'/static/{value}?v={int(os.path.getmtime(shipped))}'
        return None
    return f'/uploads/brand/{value}?v={int(os.path.getmtime(path))}'


def _restore(value, path):
    """Write the logo back from its database copy (models.BrandAsset); True when it now exists."""
    try:
        from models import BrandAsset
        row = BrandAsset.query.get(value)
        if row is None:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(row.data)
        return True
    except Exception:
        _rollback()
        return False


def _store(value, path):
    try:
        from models import BrandAsset, db
        with open(path, 'rb') as f:
            data = f.read()
        row = BrandAsset.query.get(value)
        if row is None:
            db.session.add(BrandAsset(name=value, data=data))
        else:
            row.data = data
        db.session.commit()
    except Exception as e:
        print(f'[brand] logo copy to the database skipped: {e}', flush=True)


def get_brand():
    org_ar = _setting('org_ar') or current_app.config.get('ORG_AR') or 'المؤسسة'
    org_en = _setting('org_en') or current_app.config.get('ORG_EN') or org_ar
    accent = _setting('accent_color') or os.environ.get('ACCENT_COLOR', '')
    if not _HEX.match(accent or ''):
        accent = DEFAULT_ACCENT
    logo = _logo_url(_setting('brand_logo'))
    return {
        'org_ar': org_ar, 'org_en': org_en, 'accent': accent,
        'logo_url': logo,                         # None = no organization logo yet
        'logo_src': logo or SYSTEM_ICON,          # what every page shows
        'is_icon': logo is None,
        'icon': SYSTEM_ICON,
        'sys_ar': 'نظام حجز عربات الحواسيب',
        'sys_en': 'Laptop Trolleys Booking System',
    }


def email_logo_url():
    """Absolute logo URL for emails (the PNG system icon until a logo is uploaded)."""
    logo = _logo_url(_setting('brand_logo')) or SYSTEM_ICON_PNG
    if logo.startswith('http://') or logo.startswith('https://'):
        return logo
    base = current_app.config.get('BASE_URL', '')
    return base.rstrip('/') + logo if base else ''


def save_logo(file_storage):
    """Store an uploaded logo; returns an error message (Arabic) or None."""
    name = (file_storage.filename or '').lower()
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    if ext not in LOGO_EXTS:
        return 'صيغة الشعار غير مدعومة (PNG أو JPG أو WEBP أو SVG).'
    d = _brand_dir()
    for f in os.listdir(d):
        if f.startswith('logo.'):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
    fn = f'logo.{"jpg" if ext == "jpeg" else ext}'
    file_storage.save(os.path.join(d, fn))
    _store(fn, os.path.join(d, fn))
    from models import AppSetting
    AppSetting.set_str('brand_logo', fn)
    return None


def remove_logo():
    from models import AppSetting
    AppSetting.set_str('brand_logo', '')


def set_identity(org_ar=None, org_en=None, accent=None):
    from models import AppSetting
    if org_ar is not None and org_ar.strip():
        AppSetting.set_str('org_ar', org_ar.strip()[:300])
    if org_en is not None:
        AppSetting.set_str('org_en', org_en.strip()[:300])
    if accent is not None and _HEX.match(accent):
        AppSetting.set_str('accent_color', accent)


def graph_status():
    """Microsoft Graph readiness for the settings card — booleans and the sender only, never the secrets."""
    c = current_app.config
    st = {
        'tenant': bool(c.get('MS_TENANT_ID')), 'client': bool(c.get('MS_CLIENT_ID')),
        'secret': bool(c.get('MS_CLIENT_SECRET')), 'sender': c.get('MS_SENDER_EMAIL') or '',
    }
    st['ready'] = st['tenant'] and st['client'] and st['secret'] and bool(st['sender'])
    return st


def ensure_brand(app):
    """One-time start-up step (no data is deleted): keep an existing install's logo, start new ones on the icon."""
    from models import AppSetting, Booking, Teacher
    try:
        if AppSetting.query.get('brand_init') is not None:
            return
        env_logo = os.environ.get('LOGO_URL', '').strip()
        has_data = Booking.query.first() is not None or Teacher.query.first() is not None
        value = ''
        if env_logo.startswith('http://') or env_logo.startswith('https://'):
            value = env_logo
        else:
            src = ''
            if env_logo.startswith('/static/'):
                src = os.path.join(app.static_folder, env_logo[len('/static/'):])
            elif has_data:
                src = os.path.join(app.static_folder, 'logo.png')
            if src and os.path.isfile(src):
                ext = src.rsplit('.', 1)[-1].lower()
                fn = f'logo.{ext}'
                shutil.copyfile(src, os.path.join(_brand_dir(), fn))
                _store(fn, os.path.join(_brand_dir(), fn))
                value = fn
        if value:
            AppSetting.set_str('brand_logo', value)
        AppSetting.set_str('brand_init', '1')
    except Exception as e:
        print(f'[brand] start-up step skipped: {e}', flush=True)

