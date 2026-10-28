import re
from datetime import datetime, date
from functools import wraps
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, url_for, current_app, send_file)
from models import (db, Booking, Stage, Grade, Section, Period, BlockedPeriod, Contact,
                    Teacher, Student, BookingCheckout, CheckoutLine, BookingReminder, PushSubscription,
                    AppSetting, GradePeriodTime, BookingComment)
from utils.helpers import (is_valid_email, sanitize_email, save_upload, get_base_url,
                            get_all_contact_emails, get_approved_notify_emails, check_conflict, check_blocked,
                            resolve_stage_grade_section_by_name, get_period_time, weekday_name,
                            get_comment_notify_emails)
from utils.email_utils import (send_approve, send_reject, send_cancel,
                                send_pending, send_update, send_staff_notification,
                                send_comment_notification)

admin_bp = Blueprint('admin', __name__)

from utils.auth import (guard_request, current_user, can, allowed_stage_ids, allowed_trolley_codes, scope_query,
                        sign_in, password_ok, hash_password, me_json, catalogue, ALL_PERMS, SETTING_FIELD_PERMS)
admin_bp.before_request(guard_request)

def _get_contacts(stage_id=None):
    from models import Contact
    q = Contact.query
    if stage_id is not None:
        q = q.filter((Contact.stage_id == stage_id) | (Contact.stage_id.is_(None)))
    return [{'email': c.email} for c in q.all()]


def _booking_email_ctx(b, **extra):
    ctx = {
        'reqId': b.req_id, 'name': b.name, 'email': b.email,
        'title': b.event_title, 'stage': b.stage_name, 'grade': b.grade_name,
        'section': b.section_name, 'date': b.booking_date,
        'startTime': b.start_time, 'endTime': b.end_time, 'notes': b.notes,
    }
    period = Period.query.get(b.period_id) if b.period_id else None
    ctx['periodLabel'] = (period.label_ar if period else None) or (
        f'الحصة {b.period_number}' if b.period_number else '')
    ctx.update(extra)
    return ctx


# ── Auth decorator (the permission check itself is utils/auth.guard_request) ──
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user() is None:
            if request.path.startswith('/admin/api/'):
                return jsonify({'success': False, 'error': 'انتهت الجلسة — سجّل الدخول من جديد'}), 401
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated


def _find_user(login_name):
    from models import AdminUser
    from sqlalchemy import func
    login_name = (login_name or '').strip()
    if not login_name:
        return None
    return AdminUser.query.filter((func.lower(AdminUser.username) == login_name.lower()) |
                                  (func.lower(AdminUser.email) == login_name.lower())).first()


_FAILED = {}   # ip → [count, first failure] — slows down password guessing


def _too_many(ip):
    n, first = _FAILED.get(ip, (0, datetime.now()))
    if (datetime.now() - first).total_seconds() > 900:
        _FAILED.pop(ip, None)
        return False
    return n >= 10


def _server_key(username, password):
    """The ADMIN_USER / ADMIN_PASS of the server settings (.env, or Render's environment) always open the panel as
    system administrator — the way back in when every password is forgotten. Whoever can change those settings
    already controls the server."""
    import hmac
    env_u = (current_app.config.get('ADMIN_USER') or '').strip()
    env_p = current_app.config.get('ADMIN_PASS') or ''
    return bool(env_u and env_p and (username or '').strip().lower() == env_u.lower()
                and hmac.compare_digest(str(password or ''), str(env_p)))


def _recovery_admin():
    import json as _json
    from models import AdminUser
    from utils.auth import ALL_PERMS
    name = current_app.config.get('ADMIN_USER').strip()
    user = _find_user(name)
    if user is None:
        user = AdminUser(username=name, full_name='مدير النظام', password_hash=hash_password(current_app.config['ADMIN_PASS']))
        db.session.add(user)
    user.role, user.active, user.all_stages = 'sysadmin', True, True
    user.permissions = _json.dumps(ALL_PERMS)
    db.session.commit()
    return user


def _safe_next(target):
    return target if target and target.startswith('/admin') and not target.startswith('//') else None


# ── Login / Logout / password ─────────────────────────────────────────────
@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    lang = request.args.get('lang') or request.form.get('lang') or session.get('admin_lang', 'ar')
    AR = lang != 'en'
    ip = request.headers.get('CF-Connecting-IP') or request.remote_addr or ''
    if request.method == 'POST':
        if _too_many(ip):
            error = ('محاولات كثيرة غير صحيحة — انتظر 15 دقيقة ثم أعد المحاولة' if AR
                     else 'Too many failed attempts — wait 15 minutes and try again')
        else:
            user = _find_user(request.form.get('username'))
            if _server_key(request.form.get('username'), request.form.get('password', '')):
                user = _recovery_admin()
            if user and user.active and (password_ok(user, request.form.get('password', ''))
                                         or _server_key(request.form.get('username'), request.form.get('password', ''))):
                _FAILED.pop(ip, None)
                sign_in(user, request.form.get('remember') == '1')
                session['admin_lang'] = lang
                return redirect(_safe_next(request.form.get('next')) or url_for('admin.dashboard'))
            n, first = _FAILED.get(ip, (0, datetime.now()))
            _FAILED[ip] = (n + 1, first)
            if user and not user.active and password_ok(user, request.form.get('password', '')):
                error = 'هذا الحساب موقوف — راجع مدير النظام' if AR else 'This account is disabled — contact the system administrator'
            else:
                error = 'اسم المستخدم أو كلمة المرور غير صحيحة' if AR else 'Incorrect username or password'
    return render_template('admin/login.html', error=error, lang=lang, mode='login',
                           next=_safe_next(request.args.get('next') or request.form.get('next')) or '',
                           username=request.form.get('username', ''), info=request.args.get('info', ''))


@admin_bp.route('/logout')
def logout():
    lang = session.get('admin_lang', 'ar')
    session.clear()
    return redirect(url_for('admin.login', lang=lang))


def _reset_serializer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.secret_key, salt='admin-password-reset')


@admin_bp.route('/forgot', methods=['GET', 'POST'])
def forgot_password():
    lang = request.args.get('lang') or request.form.get('lang') or session.get('admin_lang', 'ar')
    AR = lang != 'en'
    sent = False
    if request.method == 'POST':
        user = _find_user(request.form.get('username'))
        if user and user.active and user.email:
            token = _reset_serializer().dumps({'u': user.id, 'p': user.password_hash[-12:]})
            link = get_base_url() + url_for('admin.reset_password', token=token, lang=lang)
            try:
                from utils.email_utils import send_password_reset
                send_password_reset(user.email, user.full_name, link)
            except Exception as e:
                print(f"[email] password reset failed: {e}", flush=True)
        sent = True      # the same answer whether the account exists or not
    return render_template('admin/login.html', lang=lang, mode='forgot', sent=sent, error=None)


@admin_bp.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    from models import AdminUser
    from itsdangerous import BadSignature, SignatureExpired
    lang = request.args.get('lang') or request.form.get('lang') or session.get('admin_lang', 'ar')
    AR = lang != 'en'
    user, error = None, None
    try:
        data = _reset_serializer().loads(token, max_age=3600)
        user = AdminUser.query.get(int(data.get('u')))
        if not user or not user.active or user.password_hash[-12:] != data.get('p'):
            user = None
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        user = None
    if user is None:
        return render_template('admin/login.html', lang=lang, mode='reset_bad', error=None)
    if request.method == 'POST':
        p1, p2 = request.form.get('password', ''), request.form.get('password2', '')
        if len(p1) < 8:
            error = 'كلمة المرور 8 أحرف على الأقل' if AR else 'The password needs at least 8 characters'
        elif p1 != p2:
            error = 'كلمتا المرور غير متطابقتين' if AR else 'The passwords do not match'
        else:
            user.password_hash = hash_password(p1)
            db.session.commit()
            return redirect(url_for('admin.login', lang=lang, info='reset'))
    return render_template('admin/login.html', lang=lang, mode='reset', token=token, error=error)


@admin_bp.route('/api/me')
@login_required
def api_me():
    return jsonify(me_json())


@admin_bp.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    user = current_user()
    if not password_ok(user, data.get('current', '')):
        return jsonify({'success': False, 'error': 'كلمة المرور الحالية غير صحيحة', 'errorEn': 'The current password is wrong'}), 400
    new = data.get('new', '')
    if len(new) < 8:
        return jsonify({'success': False, 'error': 'كلمة المرور 8 أحرف على الأقل', 'errorEn': 'At least 8 characters'}), 400
    user.password_hash = hash_password(new)
    db.session.commit()
    session['admin_pw'] = user.password_hash[-12:]
    return jsonify({'success': True})


# ── Users (system administrator only) ─────────────────────────────────────
@admin_bp.route('/api/users')
@login_required
def api_users():
    from models import AdminUser
    users = AdminUser.query.order_by(AdminUser.role.desc(), AdminUser.full_name).all()
    stages = Stage.query.order_by(Stage.sort_order).all()
    lang = request.args.get('lang', 'ar')
    return jsonify({'users': [u.to_dict() for u in users],
                    'stages': [{'id': s.id, 'nameAr': s.name_ar, 'nameEn': s.name_en or s.name_ar} for s in stages],
                    'catalogue': catalogue(lang), 'meId': current_user().id})


def _sysadmins_left(excluding_ids):
    from models import AdminUser
    return AdminUser.query.filter(AdminUser.role == 'sysadmin', AdminUser.active.is_(True),
                                  ~AdminUser.id.in_(list(excluding_ids) or [-1])).count()


@admin_bp.route('/api/save-user', methods=['POST'])
@login_required
def api_save_user():
    import json as _json
    from models import AdminUser
    d = request.get_json(silent=True) or {}
    uid = d.get('id')
    user = AdminUser.query.get(int(uid)) if uid else None
    if uid and not user:
        return jsonify({'success': False, 'error': 'المستخدم غير موجود'}), 404
    username = (d.get('username') or '').strip()
    full_name = (d.get('fullName') or '').strip()
    email = sanitize_email(d.get('email') or '') if (d.get('email') or '').strip() else ''
    role = 'sysadmin' if d.get('role') == 'sysadmin' else 'user'
    if not re.match(r'^[A-Za-z0-9_.@-]{3,80}$', username):
        return jsonify({'success': False, 'error': 'اسم المستخدم 3 أحرف على الأقل (أحرف إنجليزية وأرقام و . _ - @)',
                        'errorEn': 'Username: at least 3 characters (letters, digits, . _ - @)'}), 400
    if not full_name:
        return jsonify({'success': False, 'error': 'الاسم الكامل مطلوب', 'errorEn': 'The full name is required'}), 400
    if email and not is_valid_email(email):
        return jsonify({'success': False, 'error': 'البريد الإلكتروني غير صحيح', 'errorEn': 'Invalid email'}), 400
    clash = _find_user(username)
    if clash and (not user or clash.id != user.id):
        return jsonify({'success': False, 'error': 'اسم المستخدم مستخدم لحساب آخر', 'errorEn': 'The username is taken'}), 400
    if email:
        clash = _find_user(email)
        if clash and (not user or clash.id != user.id):
            return jsonify({'success': False, 'error': 'البريد مستخدم لحساب آخر', 'errorEn': 'The email is used by another account'}), 400
    password = d.get('password') or ''
    if not user and len(password) < 8:
        return jsonify({'success': False, 'error': 'كلمة المرور 8 أحرف على الأقل', 'errorEn': 'Password: at least 8 characters'}), 400
    if password and len(password) < 8:
        return jsonify({'success': False, 'error': 'كلمة المرور 8 أحرف على الأقل', 'errorEn': 'Password: at least 8 characters'}), 400
    active = bool(d.get('active', True))
    me = current_user()
    if user and user.is_sysadmin and (role != 'sysadmin' or not active) and _sysadmins_left({user.id}) == 0:
        return jsonify({'success': False, 'error': 'لا يمكن إيقاف آخر مدير نظام أو تغيير دوره',
                        'errorEn': 'The last system administrator cannot be disabled or demoted'}), 400
    if user and user.id == me.id and not active:
        return jsonify({'success': False, 'error': 'لا يمكن إيقاف الحساب الحالي', 'errorEn': 'You cannot disable your own account'}), 400
    perms = [p for p in (d.get('permissions') or []) if p in ALL_PERMS]
    valid_stages = {s.id for s in Stage.query.all()}
    stage_ids = sorted({int(x) for x in (d.get('stageIds') or []) if str(x).isdigit() and int(x) in valid_stages})
    all_stages = bool(d.get('allStages'))
    if role == 'user' and not all_stages and not stage_ids:
        return jsonify({'success': False, 'error': 'اختر مرحلة واحدة على الأقل أو «كل المراحل»',
                        'errorEn': 'Choose at least one stage, or «all stages»'}), 400
    if not user:
        user = AdminUser(username=username, password_hash=hash_password(password))
        db.session.add(user)
    user.username, user.full_name, user.email, user.role, user.active = username, full_name, email or None, role, active
    user.all_stages = all_stages or role == 'sysadmin'
    user.stage_ids = _json.dumps(stage_ids)
    user.permissions = _json.dumps(ALL_PERMS if role == 'sysadmin' else perms)
    if password and user.id:
        user.password_hash = hash_password(password)
    db.session.commit()
    if user.id == me.id:
        session['admin_pw'] = user.password_hash[-12:]
    return jsonify({'success': True, 'user': user.to_dict()})


@admin_bp.route('/api/users-bulk', methods=['POST'])
@login_required
def api_users_bulk():
    from models import AdminUser
    d = request.get_json(silent=True) or {}
    action = d.get('action')
    ids = {int(x) for x in (d.get('ids') or []) if str(x).isdigit()}
    me = current_user()
    users = AdminUser.query.filter(AdminUser.id.in_(list(ids) or [-1])).all()
    if not users or action not in ('activate', 'deactivate', 'delete'):
        return jsonify({'success': False, 'error': 'لم يُحدَّد شيء', 'errorEn': 'Nothing selected'}), 400
    skipped = []
    if action == 'activate':
        for u in users:
            u.active = True
    else:
        sys_ids = {u.id for u in users if u.is_sysadmin}
        for u in users:
            if u.id == me.id:
                skipped.append(u.username)
                continue
            if u.is_sysadmin and _sysadmins_left(sys_ids) == 0:
                skipped.append(u.username)
                sys_ids.discard(u.id)
                continue
            if action == 'deactivate':
                u.active = False
            else:
                db.session.delete(u)
    db.session.commit()
    return jsonify({'success': True, 'count': len(users) - len(skipped), 'skipped': skipped})


@admin_bp.route('/')
@login_required
def dashboard():
    lang = session.get('admin_lang', 'ar')
    from utils.brand import graph_status
    return render_template('admin/dashboard.html', lang=lang, graph=graph_status(),
                           brand_msg=request.args.get('brand', ''), me=current_user(), me_json=me_json(),
                           can=can)


# ── Identity: names, accent colour, logo (utils/brand.py) ─────────────────
@admin_bp.route('/brand', methods=['POST'])
@login_required
def save_brand():
    from utils.brand import set_identity, save_logo, remove_logo
    set_identity(request.form.get('org_ar'), request.form.get('org_en'), request.form.get('accent_color'))
    status = 'saved'
    if request.form.get('remove_logo') == '1':
        remove_logo()
    else:
        f = request.files.get('logo')
        if f and f.filename:
            if save_logo(f):
                status = 'badlogo'
    return redirect(url_for('admin.dashboard', brand=status) + '#settings')


# ── Bookings API ──────────────────────────────────────────────────────────
@admin_bp.route('/api/bookings')
@login_required
def api_bookings():
    filt = request.args.get('filter', 'all')
    q = scope_query(Booking.query, Booking.stage_id)
    if filt != 'all':
        q = q.filter_by(status=filt)
    bookings = q.order_by(Booking.created_at.desc()).all()

    # One batched query for which bookings already have a submitted device-
    # handover form, instead of a separate lookup per row.
    booking_ids = [b.id for b in bookings]
    submitted_ids = set()
    comment_counts = {}
    if booking_ids:
        submitted_ids = {row[0] for row in db.session.query(BookingCheckout.booking_id)
                          .filter(BookingCheckout.booking_id.in_(booking_ids)).all()}
        from sqlalchemy import func
        comment_counts = dict(db.session.query(BookingComment.booking_id, func.count(BookingComment.id))
                               .filter(BookingComment.booking_id.in_(booking_ids))
                               .group_by(BookingComment.booking_id).all())

    result = []
    for b in bookings:
        d = b.to_dict()
        d['checkoutSubmitted'] = b.id in submitted_ids
        d['commentCount'] = comment_counts.get(b.id, 0)
        result.append(d)
    return jsonify(result)


@admin_bp.route('/api/booking-comments')
@login_required
def api_booking_comments():
    req_id = (request.args.get('reqId') or '').strip()
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'الحجز غير موجود'}), 404
    comments = (BookingComment.query.filter_by(booking_id=b.id)
                .order_by(BookingComment.created_at.asc()).all())
    return jsonify({'success': True, 'comments': [c.to_dict() for c in comments]})


@admin_bp.route('/api/add-comment', methods=['POST'])
@login_required
def api_add_comment():
    data = request.get_json(force=True) or {}
    req_id = (data.get('reqId') or '').strip()
    body = (data.get('body') or '').strip()
    author = (data.get('authorName') or '').strip()
    if not req_id or not body:
        return jsonify({'success': False, 'error': 'الرجاء إدخال نص التعليق'}), 400

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'الحجز غير موجود'}), 404

    c = BookingComment(booking_id=b.id, author_name=author or 'الإدارة', body=body)
    db.session.add(c)
    db.session.commit()

    ctx = _booking_email_ctx(b, comment=body, commentAuthor=c.author_name)
    try:
        send_comment_notification(ctx)
    except Exception as e:
        print(f"[email] comment notification to teacher failed: {e}", flush=True)
    try:
        contacts = [{'email': e} for e in get_comment_notify_emails(b.stage_id)]
        send_staff_notification('comment', ctx, contacts)
    except Exception as e:
        print(f"[email] comment staff notification failed: {e}", flush=True)

    return jsonify({'success': True, 'comment': c.to_dict()})


@admin_bp.route('/api/settings')
@login_required
def api_get_settings():
    return jsonify({
        'autoApproveBookings': AppSetting.get_bool('auto_approve_bookings', False),
        'autoPrintReceipts': AppSetting.get_bool('auto_print_receipts', False),
        'disableCheckoutFormLink': AppSetting.get_bool('disable_checkout_form_link', False),
        'staffDailySummaryEnabled': AppSetting.get_bool('staff_daily_summary_enabled', False),
        'staffDailySummaryTime': AppSetting.get_str('staff_daily_summary_time', '07:00'),
        'staffReminderLeadMinutes': AppSetting.get_str('staff_reminder_lead_minutes', ''),
        'preBreakPeriodsEnabled': AppSetting.get_bool('pre_break_periods_enabled', True),
        'blockConsecutivePeriods': AppSetting.get_bool('block_consecutive_periods', False),
        'blockSectionMultiPerDay': AppSetting.get_bool('block_section_multi_per_day', False),
    })


@admin_bp.route('/api/settings', methods=['POST'])
@login_required
def api_update_settings():
    data = request.get_json(silent=True) or {}
    denied = [k for k in data if k in SETTING_FIELD_PERMS and not can(SETTING_FIELD_PERMS[k])]
    if denied:
        return jsonify({'success': False, 'error': 'لا تملك صلاحية لهذا الإعداد',
                        'errorEn': 'You do not have permission for this setting'}), 403
    if 'autoApproveBookings' in data:
        AppSetting.set_bool('auto_approve_bookings', bool(data['autoApproveBookings']))
    if 'autoPrintReceipts' in data:
        AppSetting.set_bool('auto_print_receipts', bool(data['autoPrintReceipts']))
    if 'disableCheckoutFormLink' in data:
        AppSetting.set_bool('disable_checkout_form_link', bool(data['disableCheckoutFormLink']))
    if 'preBreakPeriodsEnabled' in data:
        AppSetting.set_bool('pre_break_periods_enabled', bool(data['preBreakPeriodsEnabled']))
    if 'blockConsecutivePeriods' in data:
        AppSetting.set_bool('block_consecutive_periods', bool(data['blockConsecutivePeriods']))
    if 'blockSectionMultiPerDay' in data:
        AppSetting.set_bool('block_section_multi_per_day', bool(data['blockSectionMultiPerDay']))
    if 'staffDailySummaryEnabled' in data:
        AppSetting.set_bool('staff_daily_summary_enabled', bool(data['staffDailySummaryEnabled']))
    if 'staffDailySummaryTime' in data:
        val = str(data['staffDailySummaryTime'] or '').strip()
        if re.match(r'^\d{2}:\d{2}$', val):
            AppSetting.set_str('staff_daily_summary_time', val)
    if 'staffReminderLeadMinutes' in data:
        val = str(data['staffReminderLeadMinutes'] or '').strip()
        if val == '' or val.isdigit():
            AppSetting.set_str('staff_reminder_lead_minutes', val)
    return jsonify({'success': True})


@admin_bp.route('/api/print-queue')
@login_required
def api_print_queue():
    """Approved bookings whose delivery receipt hasn't been auto-printed
    yet — polled by whichever single browser is designated as the print
    station (see localStorage flag on the client side)."""
    if not AppSetting.get_bool('auto_print_receipts', False) or not can('settings.printing'):
        return jsonify([])
    bookings = (scope_query(Booking.query, Booking.stage_id)
                .filter(Booking.status.in_(['approved', 'completed']))
                .filter_by(receipt_printed=False)
                .order_by(Booking.action_date.asc())
                .limit(10)
                .all())
    return jsonify([b.to_dict() for b in bookings])


@admin_bp.route('/api/mark-printed', methods=['POST'])
@login_required
def api_mark_printed():
    data = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    b.receipt_printed = True
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/vapid-public-key')
@login_required
def api_vapid_public_key():
    return jsonify({'publicKey': current_app.config.get('VAPID_PUBLIC_KEY', '')})


@admin_bp.route('/api/push-subscribe', methods=['POST'])
@login_required
def api_push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint', '')
    keys = data.get('keys', {})
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'success': False, 'error': 'بيانات الاشتراك غير مكتملة'}), 400

    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if not existing:
        db.session.add(PushSubscription(endpoint=endpoint, p256dh=keys['p256dh'], auth=keys['auth']))
        db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/push-unsubscribe', methods=['POST'])
@login_required
def api_push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint', '')
    PushSubscription.query.filter_by(endpoint=endpoint).delete()
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/push-debug')
@login_required
def api_push_debug():
    """Quick diagnostic: how many push subscriptions are currently stored,
    and can the pywebpush library actually be imported on this server."""
    count = PushSubscription.query.count()
    try:
        import pywebpush
        pywebpush_ok = True
        pywebpush_err = None
    except Exception as e:
        pywebpush_ok = False
        pywebpush_err = str(e)
    return jsonify({
        'subscriptionCount': count,
        'pywebpushInstalled': pywebpush_ok,
        'pywebpushError': pywebpush_err,
        'vapidPublicKeySet': bool(current_app.config.get('VAPID_PUBLIC_KEY')),
    })


@admin_bp.route('/api/stats')
@login_required
def api_stats():
    today = date.today().strftime('%Y-%m-%d')

    if not can(('bookings.view', 'calendar', 'reports')):
        return jsonify({'total': 0, 'pending': 0, 'approved': 0, 'completed': 0, 'rejected': 0, 'cancelled': 0,
                        'today': 0, 'halls': 0})
    BQ = lambda: scope_query(Booking.query, Booking.stage_id)  # noqa: E731
    total   = BQ().count()
    pending = BQ().filter_by(status='pending').count()
    approved= BQ().filter_by(status='approved').count()
    completed=BQ().filter_by(status='completed').count()
    rejected= BQ().filter_by(status='rejected').count()
    cancelled=BQ().filter_by(status='cancelled').count()
    today_c = BQ().filter_by(booking_date=today).count()
    stages_c = scope_query(Stage.query, Stage.id).filter_by(active=True).count()

    return jsonify({
        'total': total, 'pending': pending, 'approved': approved, 'completed': completed,
        'rejected': rejected, 'cancelled': cancelled,
        'today': today_c, 'halls': stages_c,
    })


@admin_bp.route('/api/approve', methods=['POST'])
@login_required
def api_approve():
    data        = request.get_json(silent=True) or {}
    req_id      = data.get('reqId', '')
    approved_by = (data.get('approvedBy') or '').strip()

    if not approved_by:
        return jsonify({'success': False, 'error': 'اسم المعتمِد مطلوب'}), 400

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    b.status      = 'approved'
    b.action_date = datetime.utcnow()
    b.approved_by = approved_by
    db.session.commit()

    base_url = get_base_url()
    checkout_url = f"{base_url}/checkout/{b.req_id}" if base_url and not AppSetting.get_bool('disable_checkout_form_link', False) else ''

    print(f"[email] DEBUG: approve started for {req_id}", flush=True)
    try:
        print("[email] DEBUG: building context...", flush=True)
        ctx = _booking_email_ctx(b)
        print(f"[email] DEBUG: context built, email={ctx.get('email')}", flush=True)
        contacts = get_approved_notify_emails(b.stage_id)
        contacts = [{'email': e} for e in contacts]
        print(f"[email] DEBUG: contacts fetched, count={len(contacts)}", flush=True)
        send_staff_notification('approve', ctx, contacts)
        print("[email] DEBUG: send_staff_notification returned", flush=True)
        ctx2 = _booking_email_ctx(b, checkoutUrl=checkout_url)
        send_approve(ctx2)
        print("[email] DEBUG: send_approve returned", flush=True)
    except Exception as e:
        import traceback
        print(f"[email] notification failed: {e}", flush=True)
        print(f"[email] TRACEBACK: {traceback.format_exc()}", flush=True)

    try:
        from utils.push_utils import send_push_to_all
        send_push_to_all(
            current_app._get_current_object(),
            'تمت الموافقة على حجز', f'{b.name} — {b.stage_name} — {b.booking_date}',
            url='/admin/#bookings', event_type='booking-approved',
        )
    except Exception as e:
        print(f"[push] notification failed: {e}", flush=True)

    return jsonify({'success': True})


@admin_bp.route('/api/complete', methods=['POST'])
@login_required
def api_complete():
    """Manually close out an approved booking once the trolley has been
    returned and everything is settled."""
    data   = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if b.status != 'approved':
        return jsonify({'success': False, 'error': 'يمكن إغلاق الحجوزات المعتمدة فقط'}), 400

    b.status      = 'completed'
    b.action_date = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/reject', methods=['POST'])
@login_required
def api_reject():
    data   = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    reason = data.get('reason', '')

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    b.status        = 'rejected'
    b.reject_reason = reason
    b.action_date   = datetime.utcnow()
    db.session.commit()

    try:
        send_staff_notification('reject', _booking_email_ctx(b, reason=reason), _get_contacts(b.stage_id))
        send_reject({'reqId': req_id, 'name': b.name, 'email': b.email,
                     'title': b.event_title, 'reason': reason})
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)

    return jsonify({'success': True})


@admin_bp.route('/api/cancel', methods=['POST'])
@login_required
def api_cancel():
    data   = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    reason = (data.get('reason') or '').strip()

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if b.status == 'cancelled':
        return jsonify({'success': False, 'error': 'الحجز ملغي بالفعل'}), 400

    b.status        = 'cancelled'
    b.cancel_reason = reason
    b.action_date   = datetime.utcnow()
    db.session.commit()

    try:
        send_staff_notification('cancel', _booking_email_ctx(b, reason=reason), _get_contacts(b.stage_id))
        send_cancel(_booking_email_ctx(b, reason=reason))
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)

    return jsonify({'success': True})


@admin_bp.route('/api/set-pending', methods=['POST'])
@login_required
def api_set_pending():
    data   = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    b.status        = 'pending'
    b.reject_reason = ''
    b.approved_by   = ''
    b.action_date   = datetime.utcnow()
    db.session.commit()

    try:
        send_staff_notification('revert', _booking_email_ctx(b), _get_contacts(b.stage_id))
        send_pending(_booking_email_ctx(b))
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)

    return jsonify({'success': True})


@admin_bp.route('/api/update-booking', methods=['POST'])
@login_required
def api_update_booking():
    if request.content_type and ('multipart' in request.content_type or
                                  'form' in request.content_type):
        f = request.form
        files = request.files.getlist('attachments')
    else:
        f = request.get_json(silent=True) or {}
        files = []

    req_id = f.get('reqId', '')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    booking_date = f.get('bookingDate', b.booking_date)

    stage_id   = f.get('stageId') or b.stage_id
    grade_id   = f.get('gradeId') or b.grade_id
    section_id = f.get('sectionId') or b.section_id
    period_id  = f.get('periodId') or b.period_id
    stage   = Stage.query.get(stage_id)
    grade   = Grade.query.get(grade_id)
    section = Section.query.get(section_id)
    period  = Period.query.get(period_id)
    if not (stage and grade and section and period):
        return jsonify({'success': False, 'error': 'بيانات المرحلة/الصف/الشعبة/الحصة غير صحيحة'}), 400

    conflict = check_conflict(stage.trolley_code, booking_date, period.number, req_id)
    if conflict:
        return jsonify({'success': False, 'error': conflict}), 400

    b.name          = f.get('name', b.name)
    b.email         = sanitize_email(f.get('email', b.email))
    b.phone         = f.get('phone', b.phone or '')
    b.on_behalf     = f.get('behalf', b.on_behalf or '')
    b.event_title   = f.get('title', b.event_title or '')
    b.booking_date  = booking_date
    b.stage_id      = stage.id
    b.grade_id      = grade.id
    b.section_id    = section.id
    b.period_id     = period.id
    b.trolley_code  = stage.trolley_code
    b.stage_name    = stage.name_ar
    b.grade_name    = grade.name_ar
    b.section_name  = section.name_ar
    slot_start, slot_end = get_period_time(grade.id, period, booking_date)
    b.period_number = period.number
    b.start_time    = slot_start
    b.end_time      = slot_end
    b.notes         = f.get('notes', b.notes or '')
    b.action_date   = datetime.utcnow()

    for file_obj in files:
        if file_obj and file_obj.filename:
            url = save_upload(file_obj)
            if url:
                b.attachments = (b.attachments or '') + ',' + url

    db.session.commit()

    try:
        send_staff_notification('update', _booking_email_ctx(b), _get_contacts(b.stage_id))
        send_update(_booking_email_ctx(b))
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)

    return jsonify({'success': True})


def _delete_bookings_safely(bookings):
    """Deletes bookings along with everything that references them
    (device-handover records and scheduled reminders) — Postgres enforces
    the foreign keys strictly, so deleting a Booking directly while a
    BookingCheckout or BookingReminder still points to it raises an
    IntegrityError and aborts the whole request."""
    booking_ids = [b.id for b in bookings]
    if not booking_ids:
        return 0

    checkouts = BookingCheckout.query.filter(BookingCheckout.booking_id.in_(booking_ids)).all()
    for co in checkouts:
        CheckoutLine.query.filter_by(checkout_id=co.id).delete()
        db.session.delete(co)

    BookingReminder.query.filter(BookingReminder.booking_id.in_(booking_ids)).delete(synchronize_session=False)
    BookingComment.query.filter(BookingComment.booking_id.in_(booking_ids)).delete(synchronize_session=False)

    count = len(booking_ids)
    Booking.query.filter(Booking.id.in_(booking_ids)).delete(synchronize_session=False)
    return count


@admin_bp.route('/api/delete-booking', methods=['POST'])
@login_required
def api_delete_booking():
    data   = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    _delete_bookings_safely([b])
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/bulk-delete', methods=['POST'])
@login_required
def api_bulk_delete():
    data = request.get_json(silent=True) or {}
    ids  = data.get('ids', [])
    if not ids:
        return jsonify({'success': False, 'error': 'لا توجد حجوزات محددة'}), 400
    bookings = Booking.query.filter(Booking.req_id.in_(ids)).all()
    count = _delete_bookings_safely(bookings)
    db.session.commit()
    return jsonify({'success': True, 'count': count})


# ── Academic structure API (stages / grades / sections / periods) ─────────
@admin_bp.route('/api/halls')
@login_required
def api_halls():
    """Kept at the same URL for compatibility with the dashboard JS.
    Returns stages with their nested grades/sections."""
    stages = scope_query(Stage.query, Stage.id).order_by(Stage.sort_order).all()
    return jsonify([s.to_dict(with_grades=True) for s in stages])


@admin_bp.route('/api/add-hall', methods=['POST'])
@login_required
def api_add_stage():
    data = request.get_json(silent=True) or {}
    if not data.get('nameAr'):
        return jsonify({'success': False, 'error': 'اسم المرحلة مطلوب'}), 400
    if not data.get('trolleyCode'):
        return jsonify({'success': False, 'error': 'معرّف العربة (trolley) مطلوب'}), 400
    if Stage.query.filter_by(trolley_code=data['trolleyCode']).first():
        return jsonify({'success': False, 'error': 'معرّف العربة مستخدم مسبقاً'}), 400
    s = Stage(
        name_ar      = data['nameAr'],
        name_en      = data.get('nameEn', ''),
        trolley_code = data['trolleyCode'],
        active       = data.get('active', True) is not False,
        sort_order   = Stage.query.count(),
    )
    db.session.add(s)
    db.session.commit()
    return jsonify({'success': True, 'id': s.id})


@admin_bp.route('/api/update-hall', methods=['POST'])
@login_required
def api_update_stage():
    data = request.get_json(silent=True) or {}
    s = Stage.query.get(data.get('id'))
    if not s:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if data.get('trolleyCode') and data['trolleyCode'] != s.trolley_code:
        if Stage.query.filter_by(trolley_code=data['trolleyCode']).first():
            return jsonify({'success': False, 'error': 'معرّف العربة مستخدم مسبقاً'}), 400
        s.trolley_code = data['trolleyCode']
    s.name_ar = data.get('nameAr', s.name_ar)
    s.name_en = data.get('nameEn', s.name_en or '')
    s.active  = data.get('active', True) is not False
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-hall', methods=['POST'])
@login_required
def api_delete_stage():
    data = request.get_json(silent=True) or {}
    s = Stage.query.get(data.get('id'))
    if not s:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if Stage.query.filter_by(active=True).count() <= 1:
        return jsonify({'success': False, 'error': 'لا يمكن حذف آخر مرحلة'}), 400

    blockers = []
    n = Student.query.filter_by(stage_id=s.id).count()
    if n: blockers.append(f'{n} طالب/ة')
    n = Teacher.query.filter_by(stage_id=s.id).count()
    if n: blockers.append(f'{n} معلم/ة')
    n = Contact.query.filter_by(stage_id=s.id).count()
    if n: blockers.append(f'{n} جهة اتصال')
    n = Booking.query.filter_by(stage_id=s.id).count()
    if n: blockers.append(f'{n} حجز')
    if blockers:
        return jsonify({'success': False,
                        'error': 'لا يمكن حذف هذه المرحلة — يوجد بها: ' + '، '.join(blockers) +
                                 '. انقلهم أو احذفهم أولاً.'}), 400

    db.session.delete(s)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/add-grade', methods=['POST'])
@login_required
def api_add_grade():
    data = request.get_json(silent=True) or {}
    if not data.get('nameAr') or not data.get('stageId'):
        return jsonify({'success': False, 'error': 'اسم الصف والمرحلة مطلوبان'}), 400
    g = Grade(stage_id=data['stageId'], name_ar=data['nameAr'], name_en=data.get('nameEn', ''),
              sort_order=Grade.query.filter_by(stage_id=data['stageId']).count())
    db.session.add(g)
    db.session.commit()
    return jsonify({'success': True, 'id': g.id})


@admin_bp.route('/api/update-grade', methods=['POST'])
@login_required
def api_update_grade():
    data = request.get_json(silent=True) or {}
    g = Grade.query.get(data.get('id'))
    if not g:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if data.get('nameAr'):
        g.name_ar = data['nameAr']
    g.name_en = data.get('nameEn', g.name_en or '')
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-grade', methods=['POST'])
@login_required
def api_delete_grade():
    data = request.get_json(silent=True) or {}
    g = Grade.query.get(data.get('id'))
    if not g:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    blockers = []
    n = Student.query.filter_by(grade_id=g.id).count()
    if n: blockers.append(f'{n} طالب/ة')
    n = Teacher.query.filter_by(grade_id=g.id).count()
    if n: blockers.append(f'{n} معلم/ة')
    n = Booking.query.filter_by(grade_id=g.id).count()
    if n: blockers.append(f'{n} حجز')
    if blockers:
        return jsonify({'success': False,
                        'error': 'لا يمكن حذف هذا الصف — يوجد به: ' + '، '.join(blockers) +
                                 '. انقلهم أو احذفهم أولاً.'}), 400

    db.session.delete(g)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/add-section', methods=['POST'])
@login_required
def api_add_section():
    data = request.get_json(silent=True) or {}
    if not data.get('nameAr') or not data.get('gradeId'):
        return jsonify({'success': False, 'error': 'اسم الشعبة والصف مطلوبان'}), 400
    sec = Section(grade_id=data['gradeId'], name_ar=data['nameAr'], name_en=data.get('nameEn', ''),
                  sort_order=Section.query.filter_by(grade_id=data['gradeId']).count())
    db.session.add(sec)
    db.session.commit()
    return jsonify({'success': True, 'id': sec.id})


@admin_bp.route('/api/update-section', methods=['POST'])
@login_required
def api_update_section():
    data = request.get_json(silent=True) or {}
    sec = Section.query.get(data.get('id'))
    if not sec:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if data.get('nameAr'):
        sec.name_ar = data['nameAr']
    sec.name_en = data.get('nameEn', sec.name_en or '')
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-section', methods=['POST'])
@login_required
def api_delete_section():
    data = request.get_json(silent=True) or {}
    sec = Section.query.get(data.get('id'))
    if not sec:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    blockers = []
    n = Student.query.filter_by(section_id=sec.id).count()
    if n: blockers.append(f'{n} طالب/ة')
    n = Teacher.query.filter_by(section_id=sec.id).count()
    if n: blockers.append(f'{n} معلم/ة')
    n = Booking.query.filter_by(section_id=sec.id).count()
    if n: blockers.append(f'{n} حجز')
    if blockers:
        return jsonify({'success': False,
                        'error': 'لا يمكن حذف هذه الشعبة — يوجد بها: ' + '، '.join(blockers) +
                                 '. انقلهم أو احذفهم أولاً.'}), 400

    db.session.delete(sec)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/periods')
@login_required
def api_admin_periods():
    periods = Period.query.order_by(Period.number).all()
    return jsonify([p.to_dict() for p in periods])


@admin_bp.route('/api/add-period', methods=['POST'])
@login_required
def api_add_period():
    data = request.get_json(silent=True) or {}
    try:
        number = int(data.get('number'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'رقم الحصة مطلوب'}), 400
    if Period.query.filter_by(number=number).first():
        return jsonify({'success': False, 'error': 'رقم الحصة مستخدم مسبقاً'}), 400
    p = Period(number=number, label_ar=data.get('label') or f'الحصة {number}',
               start_time=data.get('startTime', ''), end_time=data.get('endTime', ''),
               active=data.get('active', True) is not False)
    db.session.add(p)
    db.session.commit()
    return jsonify({'success': True, 'id': p.id})


@admin_bp.route('/api/update-period', methods=['POST'])
@login_required
def api_update_period():
    data = request.get_json(silent=True) or {}
    p = Period.query.get(data.get('id'))
    if not p:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    p.label_ar   = data.get('label', p.label_ar)
    p.start_time = data.get('startTime', p.start_time or '')
    p.end_time   = data.get('endTime', p.end_time or '')
    p.active     = data.get('active', True) is not False
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-period', methods=['POST'])
@login_required
def api_delete_period():
    data = request.get_json(silent=True) or {}
    p = Period.query.get(data.get('id'))
    if not p:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    n = Booking.query.filter_by(period_id=p.id).count()
    if n:
        return jsonify({'success': False,
                        'error': f'لا يمكن حذف هذه الحصة — يوجد بها {n} حجز. احذف الحجوزات المرتبطة أولاً.'}), 400

    db.session.delete(p)
    db.session.commit()
    return jsonify({'success': True})


# ── Per-grade / per-weekday period times ───────────────────────────────────
# Lets a grade run a different bell schedule on different days (e.g. a
# shorter Tuesday, or a different timetable for one grade group vs another)
# instead of the single shared time on Period. A weekday follows Python's
# date.weekday(): Monday=0 .. Sunday=6.
@admin_bp.route('/api/grade-period-times')
@login_required
def api_grade_period_times():
    grade_id = request.args.get('gradeId', type=int)
    if not grade_id:
        return jsonify({'error': 'الصف مطلوب'}), 400
    rows = GradePeriodTime.query.filter_by(grade_id=grade_id).all()
    return jsonify([r.to_dict() for r in rows])


@admin_bp.route('/api/set-grade-period-times', methods=['POST'])
@login_required
def api_set_grade_period_times():
    """Batch upsert: set every period's time for one grade across one or
    more weekdays in a single call. A row with both times blank deletes
    any existing override for that (grade, weekday, period) — falling
    back to the shared Period time again."""
    data = request.get_json(silent=True) or {}
    grade_id = data.get('gradeId')
    weekdays = data.get('weekdays') or []
    times = data.get('times') or []  # [{periodNumber, startTime, endTime}]

    grade = Grade.query.get(grade_id)
    if not grade:
        return jsonify({'success': False, 'error': 'الصف غير موجود'}), 400
    try:
        weekdays = [int(w) for w in weekdays]
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'أيام غير صحيحة'}), 400
    if not weekdays:
        return jsonify({'success': False, 'error': 'يرجى اختيار يوم واحد على الأقل'}), 400

    for weekday in weekdays:
        if not (0 <= weekday <= 6):
            continue
        for row in times:
            try:
                period_number = int(row.get('periodNumber'))
            except (TypeError, ValueError):
                continue
            start_time = (row.get('startTime') or '').strip()
            end_time   = (row.get('endTime') or '').strip()

            existing = GradePeriodTime.query.filter_by(
                grade_id=grade.id, weekday=weekday, period_number=period_number).first()

            if not start_time or not end_time:
                if existing:
                    db.session.delete(existing)
                continue

            if existing:
                existing.start_time = start_time
                existing.end_time = end_time
            else:
                db.session.add(GradePeriodTime(
                    grade_id=grade.id, weekday=weekday, period_number=period_number,
                    start_time=start_time, end_time=end_time))

    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/copy-grade-period-times', methods=['POST'])
@login_required
def api_copy_grade_period_times():
    """Copy one grade's whole schedule (all weekdays/periods) onto one or
    more other grades — handy when several grades share the same bell
    schedule (e.g. grades 7, 8 and 9)."""
    data = request.get_json(silent=True) or {}
    from_grade_id = data.get('fromGradeId')
    to_grade_ids = data.get('toGradeIds') or []

    source_rows = GradePeriodTime.query.filter_by(grade_id=from_grade_id).all()
    if not source_rows:
        return jsonify({'success': False, 'error': 'لا يوجد جدول لنسخه لهذا الصف'}), 400

    try:
        to_grade_ids = [int(g) for g in to_grade_ids]
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'صفوف غير صحيحة'}), 400

    for grade_id in to_grade_ids:
        if grade_id == from_grade_id:
            continue
        if not Grade.query.get(grade_id):
            continue
        GradePeriodTime.query.filter_by(grade_id=grade_id).delete()
        for r in source_rows:
            db.session.add(GradePeriodTime(
                grade_id=grade_id, weekday=r.weekday, period_number=r.period_number,
                start_time=r.start_time, end_time=r.end_time))

    db.session.commit()
    return jsonify({'success': True})


# ── Blocked periods API ───────────────────────────────────────────────────
@admin_bp.route('/api/blocked')
@login_required
def api_blocked():
    blocks = BlockedPeriod.query.order_by(BlockedPeriod.from_date).all()
    codes = allowed_trolley_codes()
    if codes is not None:
        blocks = [b for b in blocks if not b.hall or b.hall in codes]
    return jsonify([b.to_dict() for b in blocks])


@admin_bp.route('/api/add-blocked', methods=['POST'])
@login_required
def api_add_blocked():
    data = request.get_json(silent=True) or {}
    if not data.get('fromDate') or not data.get('toDate'):
        return jsonify({'success': False, 'error': 'التاريخ مطلوب'}), 400
    blk = BlockedPeriod(
        from_date = data['fromDate'],
        to_date   = data['toDate'],
        hall      = data.get('hall', ''),   # trolley_code, or '' = all stages
        from_time = data.get('fromTime', ''),
        to_time   = data.get('toTime', ''),
        reason    = data.get('reason', ''),
    )
    db.session.add(blk)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-blocked', methods=['POST'])
@login_required
def api_delete_blocked():
    data = request.get_json(silent=True) or {}
    blk  = BlockedPeriod.query.get(data.get('id'))
    if not blk:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    db.session.delete(blk)
    db.session.commit()
    return jsonify({'success': True})


# ── Contacts API ──────────────────────────────────────────────────────────
@admin_bp.route('/api/contacts')
@login_required
def api_contacts():
    contacts = Contact.query.order_by(Contact.created_at.desc()).all()
    ids = allowed_stage_ids()
    if ids is not None:
        contacts = [c for c in contacts if c.stage_id in ids]
    return jsonify([c.to_dict() for c in contacts])


@admin_bp.route('/api/add-contacts', methods=['POST'])
@login_required
def api_add_contacts():
    data  = request.get_json(silent=True) or {}
    items = data.get('list', [])
    added = 0
    try:
        for item in items:
            em = sanitize_email(item.get('email', ''))
            # stageId arrives as a string from a <select> element's .value
            # (or as a plain string in the CSV-upload path) — cast it
            # explicitly instead of relying on the database to coerce it.
            raw_stage_id = item.get('stageId')
            try:
                stage_id = int(raw_stage_id) if raw_stage_id not in (None, '') else None
            except (TypeError, ValueError):
                stage_id = None
            if is_valid_email(em):
                exists = Contact.query.filter_by(email=em, stage_id=stage_id).first()
                if not exists:
                    db.session.add(Contact(email=em, name=item.get('name', ''), stage_id=stage_id,
                                            notify_handover=bool(item.get('notifyHandover')),
                                            notify_comment=bool(item.get('notifyComment'))))
                    added += 1
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[contacts] add-contacts failed: {e}", flush=True)
        return jsonify({'success': False, 'error': 'حدث خطأ أثناء إضافة جهة الاتصال — راجع سجلات الخادم للتفاصيل'}), 500
    return jsonify({'success': True, 'count': added})


@admin_bp.route('/api/update-contact', methods=['POST'])
@login_required
def api_update_contact():
    data = request.get_json(silent=True) or {}
    c = Contact.query.get(data.get('id'))
    if not c:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if 'notifyHandover' in data:
        c.notify_handover = bool(data['notifyHandover'])
    if 'notifyNew' in data:
        c.notify_new = bool(data['notifyNew'])
    if 'newReadonly' in data:
        c.new_readonly = bool(data['newReadonly'])
    if 'notifyApproved' in data:
        c.notify_approved = bool(data['notifyApproved'])
    if 'notifyStaffReminder' in data:
        c.notify_staff_reminder = bool(data['notifyStaffReminder'])
    if 'notifyComment' in data:
        c.notify_comment = bool(data['notifyComment'])
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-contact', methods=['POST'])
@login_required
def api_delete_contact():
    data = request.get_json(silent=True) or {}
    c    = Contact.query.get(data.get('id'))
    if not c:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    db.session.delete(c)
    db.session.commit()
    return jsonify({'success': True})


# ── Reports API ───────────────────────────────────────────────────────────
@admin_bp.route('/api/report-data')
@login_required
def api_report_data():
    bookings = scope_query(Booking.query, Booking.stage_id).filter(
        Booking.status.notin_(['rejected', 'cancelled'])
    ).all()
    return jsonify([b.to_dict() for b in bookings])


# ── Teachers API ──────────────────────────────────────────────────────────
@admin_bp.route('/api/teachers')
@login_required
def api_teachers():
    stage_id = request.args.get('stageId', type=int)
    q = scope_query(Teacher.query, Teacher.stage_id)
    if stage_id:
        q = q.filter_by(stage_id=stage_id)
    teachers = q.order_by(Teacher.name).all()
    return jsonify([t.to_dict() for t in teachers])


# ── Scrolling ticker (global announcement marquee — the same module as the tickets system) ─────────
@admin_bp.route('/api/ticker-settings')
@login_required
def api_get_ticker_settings():
    from utils.ticker_utils import get_ticker_config
    return jsonify(get_ticker_config())


@admin_bp.route('/api/ticker-settings', methods=['POST'])
@login_required
def api_set_ticker_settings():
    from utils.ticker_utils import set_ticker_config
    data = request.get_json(silent=True) or {}
    cfg = {}
    if 'enabled' in data:
        cfg['enabled'] = bool(data['enabled'])
    if 'html' in data:
        cfg['html'] = data['html'] or ''
    if 'htmlEn' in data:
        cfg['htmlEn'] = data['htmlEn'] or ''
    if 'speedSeconds' in data:
        try:
            cfg['speedSeconds'] = max(5, min(180, int(data['speedSeconds'])))
        except (TypeError, ValueError):
            pass
    if 'pulse' in data:
        cfg['pulse'] = bool(data['pulse'])
    if 'pulseSeconds' in data:
        try:
            cfg['pulseSeconds'] = max(0.3, min(10, float(data['pulseSeconds'])))
        except (TypeError, ValueError):
            pass
    if data.get('textShadow') in ('none', 'light', 'strong'):
        cfg['textShadow'] = data['textShadow']
    if 'rssColor' in data and re.fullmatch(r'#[0-9a-fA-F]{6}', str(data['rssColor'] or '')):
        cfg['rssColor'] = data['rssColor']
    if 'rssBold' in data:
        cfg['rssBold'] = bool(data['rssBold'])
    if 'rssFontSize' in data:
        try:
            cfg['rssFontSize'] = max(10, min(40, int(data['rssFontSize'])))
        except (TypeError, ValueError):
            pass
    if 'logoBetween' in data:
        cfg['logoBetween'] = bool(data['logoBetween'])
    for key in ('bgColor', 'textColor'):
        if key in data and re.fullmatch(r'#[0-9a-fA-F]{6}', str(data[key] or '')):
            cfg[key] = data[key]
    if 'bgOpacity' in data:
        try:
            cfg['bgOpacity'] = max(0, min(100, int(data['bgOpacity'])))
        except (TypeError, ValueError):
            pass
    if 'rssUrl' in data:
        url = sanitize_email(data['rssUrl'] or '')          # strips control / zero-width characters
        cfg['rssUrl'] = url if re.match(r'^https?://', url) else ''
    return jsonify(set_ticker_config(cfg))


@admin_bp.route('/api/teachers-export.csv')
@login_required
def api_teachers_export():
    """«تصدير المعلمين لنظام الغياب»: CSV (UTF-8, opens in Excel) with the column names the student-absence
    system (SAS, 8.iajaward.org) understands in «الموظفون ← استيراد». Optional ?stageId= filter."""
    import csv
    import io
    from flask import Response
    stage_id = request.args.get('stageId', type=int)
    q = scope_query(Teacher.query, Teacher.stage_id)
    if stage_id:
        q = q.filter_by(stage_id=stage_id)
    stages = {s.id: s.name_ar for s in Stage.query.all()}
    grades = {g.id: g.name_ar for g in Grade.query.all()}
    sections = {s.id: s.name_ar for s in Section.query.all()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['الاسم', 'البريد الإلكتروني', 'الهاتف', 'المسمى الوظيفي', 'المرحلة', 'الصف', 'الشعبة', 'الحالة',
                'المصدر'])
    for t in q.order_by(Teacher.name).all():
        w.writerow([t.name, t.email or '', t.phone or '', 'معلم', stages.get(t.stage_id, ''),
                    grades.get(t.grade_id, ''), sections.get(t.section_id, ''),
                    'معلّق عن الحجز' if t.blocked else 'فعّال', 'نظام القاعات'])
    name = 'teachers-for-SAS.csv'
    return Response('\ufeff' + buf.getvalue(), content_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename={name}'})


@admin_bp.route('/api/add-teacher', methods=['POST'])
@login_required
def api_add_teacher():
    data = request.get_json(silent=True) or {}
    if not data.get('name'):
        return jsonify({'success': False, 'error': 'اسم المعلم مطلوب'}), 400
    tch = Teacher(name=data['name'], email=sanitize_email(data.get('email', '')),
                  phone=data.get('phone', ''), stage_id=data.get('stageId') or None,
                  grade_id=data.get('gradeId') or None, section_id=data.get('sectionId') or None,
                  blocked=bool(data.get('blocked', False)))
    db.session.add(tch)
    db.session.commit()
    return jsonify({'success': True, 'id': tch.id})


@admin_bp.route('/api/bulk-add-teachers', methods=['POST'])
@login_required
def api_bulk_add_teachers():
    """Bulk import from a pasted/uploaded CSV-like list.
    Each item: {name, email, phone, stage, grade, section} — stage/grade/
    section are matched by NAME text (Arabic or English), so one file can
    mix teachers from different stages/sections. All optional except name."""
    data  = request.get_json(silent=True) or {}
    items = data.get('list', [])
    added = 0
    errors = []
    for item in items:
        name = (item.get('name') or '').strip()
        if not name:
            continue
        stage, grade, section, err = resolve_stage_grade_section_by_name(
            item.get('stage', ''), item.get('grade', ''), item.get('section', ''))
        if err:
            errors.append(f'{name}: {err}')
            continue
        db.session.add(Teacher(
            name=name, email=sanitize_email(item.get('email', '')),
            phone=(item.get('phone') or '').strip(),
            stage_id=stage.id if stage else None,
            grade_id=grade.id if grade else None,
            section_id=section.id if section else None,
        ))
        added += 1
    db.session.commit()
    return jsonify({'success': True, 'count': added, 'errors': errors})


@admin_bp.route('/api/update-teacher', methods=['POST'])
@login_required
def api_update_teacher():
    data = request.get_json(silent=True) or {}
    tch = Teacher.query.get(data.get('id'))
    if not tch:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if data.get('name'):
        tch.name = data['name']
    tch.email = sanitize_email(data.get('email', tch.email or ''))
    tch.phone = data.get('phone', tch.phone or '')
    tch.stage_id   = data.get('stageId') or None
    tch.grade_id   = data.get('gradeId') or None
    tch.section_id = data.get('sectionId') or None
    if 'blocked' in data:
        tch.blocked = bool(data['blocked'])
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/block-teachers-bulk', methods=['POST'])
@login_required
def api_block_teachers_bulk():
    """تعليق/رفع تعليق مجموعة من المعلمين عن الحجز دفعة واحدة."""
    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    blocked = bool(data.get('blocked', True))
    if not ids:
        return jsonify({'success': False, 'error': 'لم يتم تحديد أي معلم'}), 400
    count = (Teacher.query.filter(Teacher.id.in_(ids))
             .update({Teacher.blocked: blocked}, synchronize_session=False))
    db.session.commit()
    return jsonify({'success': True, 'count': count, 'blocked': blocked})


@admin_bp.route('/api/delete-teacher', methods=['POST'])
@login_required
def api_delete_teacher():
    data = request.get_json(silent=True) or {}
    tch = Teacher.query.get(data.get('id'))
    if not tch:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    db.session.delete(tch)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-teachers-bulk', methods=['POST'])
@login_required
def api_delete_teachers_bulk():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'success': False, 'error': 'لم يتم تحديد أي معلم'}), 400
    count = Teacher.query.filter(Teacher.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'success': True, 'count': count})


@admin_bp.route('/api/move-teachers-bulk', methods=['POST'])
@login_required
def api_move_teachers_bulk():
    """Reassign a batch of teachers to a different stage. Clears any
    grade/section they had, since those belonged to the old stage."""
    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    stage_id = data.get('stageId')
    if not ids:
        return jsonify({'success': False, 'error': 'لم يتم تحديد أي معلم'}), 400
    stage = Stage.query.get(stage_id) if stage_id else None
    if stage_id and not stage:
        return jsonify({'success': False, 'error': 'المرحلة غير موجودة'}), 400

    teachers = Teacher.query.filter(Teacher.id.in_(ids)).all()
    for tch in teachers:
        tch.stage_id = stage.id if stage else None
        tch.grade_id = None
        tch.section_id = None
    db.session.commit()
    return jsonify({'success': True, 'count': len(teachers)})


# ── Students API ──────────────────────────────────────────────────────────
@admin_bp.route('/api/students')
@login_required
def api_students():
    section_id = request.args.get('sectionId', type=int)
    grade_id = request.args.get('gradeId', type=int)
    stage_id = request.args.get('stageId', type=int)
    q = scope_query(Student.query, Student.stage_id)
    if section_id:
        q = q.filter_by(section_id=section_id)
    elif grade_id:
        q = q.filter_by(grade_id=grade_id)
    elif stage_id:
        q = q.filter_by(stage_id=stage_id)
    students = q.order_by(Student.name).all()
    return jsonify([s.to_dict() for s in students])


@admin_bp.route('/api/add-student', methods=['POST'])
@login_required
def api_add_student():
    data = request.get_json(silent=True) or {}
    if not data.get('name') or not data.get('sectionId'):
        return jsonify({'success': False, 'error': 'اسم الطالب والشعبة مطلوبان'}), 400
    section = Section.query.get(data['sectionId'])
    if not section:
        return jsonify({'success': False, 'error': 'الشعبة غير موجودة'}), 400
    stu = Student(name=data['name'], stage_id=section.grade.stage_id,
                  grade_id=section.grade_id, section_id=section.id)
    db.session.add(stu)
    db.session.commit()
    return jsonify({'success': True, 'id': stu.id})


@admin_bp.route('/api/bulk-add-students', methods=['POST'])
@login_required
def api_bulk_add_students():
    """Each item: {name, sectionId} (manual add flow, unchanged) OR
    {name, stage, grade, section} (CSV bulk import — matched by name text,
    so one file can mix students from different sections)."""
    data  = request.get_json(silent=True) or {}
    items = data.get('list', [])
    added = 0
    errors = []
    for item in items:
        name = (item.get('name') or '').strip()
        if not name:
            continue

        section_id = item.get('sectionId')
        if section_id:
            section = Section.query.get(section_id)
            if not section:
                errors.append(f'{name}: الشعبة غير موجودة')
                continue
            db.session.add(Student(
                name=name, stage_id=section.grade.stage_id,
                grade_id=section.grade_id, section_id=section.id,
            ))
            added += 1
            continue

        stage, grade, section, err = resolve_stage_grade_section_by_name(
            item.get('stage', ''), item.get('grade', ''), item.get('section', ''))
        if err or not section:
            errors.append(f'{name}: {err or "الشعبة مطلوبة"}')
            continue
        db.session.add(Student(
            name=name, stage_id=stage.id, grade_id=grade.id, section_id=section.id,
        ))
        added += 1
    db.session.commit()
    return jsonify({'success': True, 'count': added, 'errors': errors})


@admin_bp.route('/api/import-roster-autocreate', methods=['POST'])
@login_required
def api_import_roster_autocreate():
    """One-time bulk import for external rosters (e.g. RasjoNet exports).
    Accepts EITHER shape per row:
      - {name, classCode}                  e.g. classCode='8CSA'
      - {name, stage, grade, section}      e.g. stage='Secondary Stage', grade='Grade 9', section='CSA'
    Either way, any missing Stage/Grade/Section is auto-created and reused
    on repeat matches — nothing is ever duplicated across rows."""
    import re
    from sqlalchemy import func

    ARABIC_ORDINALS = {
        1: 'الأول', 2: 'الثاني', 3: 'الثالث', 4: 'الرابع', 5: 'الخامس',
        6: 'السادس', 7: 'السابع', 8: 'الثامن', 9: 'التاسع', 10: 'العاشر',
        11: 'الحادي عشر', 12: 'الثاني عشر',
    }

    data = request.get_json(silent=True) or {}
    items = data.get('list', [])
    primary_max = int(data.get('primaryMaxGrade', 6))  # grades <= this go to stage[0]

    stages = Stage.query.order_by(Stage.sort_order).all()
    if len(stages) < 2:
        return jsonify({'success': False, 'error': 'يجب أن يكون هناك مرحلتان على الأقل'}), 400
    default_primary, default_secondary = stages[0], stages[1]

    def find_stage_by_text(text):
        return Stage.query.filter(
            (func.lower(Stage.name_ar) == text.lower()) | (func.lower(Stage.name_en) == text.lower())
        ).first()

    grade_cache = {}    # (stage_id, key) -> Grade
    section_cache = {}  # (grade_id, key) -> Section
    added = 0
    errors = []

    for item in items:
        name = (item.get('name') or '').strip()
        class_code = (item.get('classCode') or '').strip()
        stage_text = (item.get('stage') or '').strip()
        grade_text = (item.get('grade') or '').strip()
        section_text = (item.get('section') or '').strip()
        if not name:
            continue

        grade_num = None
        section_code = None

        if class_code:
            m = re.match(r'^(\d{1,2})(.*)$', class_code)
            if not m:
                errors.append(f'{name}: تعذّر فهم رمز الصف "{class_code}"')
                continue
            grade_num = int(m.group(1))
            section_code = m.group(2).strip() or 'عام'
            grade_label = grade_num  # used only to build the default name_ar/name_en below
        elif grade_text and section_text:
            num_match = re.search(r'\d{1,2}', grade_text)
            grade_num = int(num_match.group()) if num_match else None
            section_code = section_text
            grade_label = grade_text
        else:
            errors.append(f'{name}: بيانات الصف ناقصة (لا رمز صف ولا مرحلة/صف/شعبة كاملة)')
            continue

        if stage_text:
            stage = find_stage_by_text(stage_text)
            if not stage:
                errors.append(f'{name}: المرحلة غير موجودة: {stage_text}')
                continue
        elif grade_num is not None:
            stage = default_primary if grade_num <= primary_max else default_secondary
        else:
            errors.append(f'{name}: تعذّر تحديد المرحلة لعدم وجود رقم صف واضح')
            continue

        gkey = (stage.id, grade_text or grade_num)
        grade = grade_cache.get(gkey)
        if not grade:
            if grade_num is not None and grade_num in ARABIC_ORDINALS:
                grade_name_ar = f'الصف {ARABIC_ORDINALS[grade_num]}'
            else:
                grade_name_ar = grade_label if isinstance(grade_label, str) else str(grade_label)
            grade_name_en = grade_text if grade_text else f'Grade {grade_num}'

            grade = Grade.query.filter_by(stage_id=stage.id).filter(
                (Grade.name_ar == grade_name_ar) | (Grade.name_en == grade_name_en)
            ).first()
            if not grade:
                grade = Grade(stage_id=stage.id, name_ar=grade_name_ar, name_en=grade_name_en,
                              sort_order=Grade.query.filter_by(stage_id=stage.id).count())
                db.session.add(grade)
                db.session.flush()
            grade_cache[gkey] = grade

        skey = (grade.id, section_code)
        section = section_cache.get(skey)
        if not section:
            section = Section.query.filter_by(grade_id=grade.id).filter(
                (Section.name_ar == section_code) | (Section.name_en == section_code)
            ).first()
            if not section:
                section = Section(grade_id=grade.id, name_ar=section_code, name_en=section_code,
                                   sort_order=Section.query.filter_by(grade_id=grade.id).count())
                db.session.add(section)
                db.session.flush()
            section_cache[skey] = section

        db.session.add(Student(name=name, stage_id=stage.id, grade_id=grade.id, section_id=section.id))
        added += 1

    db.session.commit()
    return jsonify({'success': True, 'count': added, 'errors': errors})


@admin_bp.route('/api/delete-student', methods=['POST'])
@login_required
def api_delete_student():
    data = request.get_json(silent=True) or {}
    stu = Student.query.get(data.get('id'))
    if not stu:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    db.session.delete(stu)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-students-bulk', methods=['POST'])
@login_required
def api_delete_students_bulk():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'success': False, 'error': 'لم يتم تحديد أي طالب'}), 400
    count = Student.query.filter(Student.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'success': True, 'count': count})


@admin_bp.route('/api/move-student', methods=['POST'])
@login_required
def api_move_student():
    """Transfer one student to a different section (e.g. mid-year class
    changes). Stage/grade are derived from the destination section."""
    data = request.get_json(silent=True) or {}
    stu = Student.query.get(data.get('id'))
    if not stu:
        return jsonify({'success': False, 'error': 'الطالب غير موجود'}), 404
    section = Section.query.get(data.get('sectionId'))
    if not section:
        return jsonify({'success': False, 'error': 'الشعبة الوجهة غير موجودة'}), 400

    stu.section_id = section.id
    stu.grade_id   = section.grade_id
    stu.stage_id   = section.grade.stage_id
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/move-students-bulk', methods=['POST'])
@login_required
def api_move_students_bulk():
    """Move every student currently in one section to another — handy for
    a whole-class transfer instead of one student at a time."""
    data = request.get_json(silent=True) or {}
    from_section = Section.query.get(data.get('fromSectionId'))
    to_section   = Section.query.get(data.get('toSectionId'))
    if not from_section or not to_section:
        return jsonify({'success': False, 'error': 'الشعبة غير موجودة'}), 400

    students = Student.query.filter_by(section_id=from_section.id).all()
    for stu in students:
        stu.section_id = to_section.id
        stu.grade_id   = to_section.grade_id
        stu.stage_id   = to_section.grade.stage_id
    db.session.commit()
    return jsonify({'success': True, 'count': len(students)})


# ── Checkout (laptop handover) viewing for audit ──────────────────────────
@admin_bp.route('/api/checkout/<req_id>')
@login_required
def api_get_checkout(req_id):
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    checkout = BookingCheckout.query.filter_by(booking_id=b.id).first()
    if not checkout:
        return jsonify({'success': True, 'checkout': None})
    return jsonify({'success': True, 'checkout': checkout.to_dict()})


@admin_bp.route('/api/checkout-report')
@login_required
def api_checkout_report():
    """Full device-handover history across all bookings, plus the list of
    approved/completed bookings that still have no handover record."""
    checkouts = BookingCheckout.query.all()
    lines_out = []
    covered_booking_ids = set()
    scope = allowed_stage_ids()
    for co in checkouts:
        b = Booking.query.get(co.booking_id)
        if not b or (scope is not None and b.stage_id not in scope):
            continue
        covered_booking_ids.add(b.id)
        for line in co.lines:
            lines_out.append({
                'reqId': b.req_id, 'teacher': b.name, 'date': b.booking_date,
                'dayName': weekday_name(b.booking_date, 'ar'),
                'stage': b.stage_name, 'grade': b.grade_name, 'section': b.section_name,
                'periodNumber': b.period_number,
                'studentName': line.student_name, 'laptopNumber': line.laptop_number,
                'notes': co.notes or '',
            })

    q = scope_query(Booking.query, Booking.stage_id).filter(Booking.status.in_(['approved', 'completed']))
    if covered_booking_ids:
        q = q.filter(~Booking.id.in_(covered_booking_ids))
    missing = q.order_by(Booking.booking_date.desc()).all()

    missing_out = [{
        'reqId': b.req_id, 'teacher': b.name, 'email': b.email, 'date': b.booking_date,
        'dayName': weekday_name(b.booking_date, 'ar'),
        'stage': b.stage_name, 'grade': b.grade_name, 'section': b.section_name,
        'status': b.status,
    } for b in missing]

    return jsonify({'lines': lines_out, 'missing': missing_out})


@admin_bp.route('/api/resend-checkout-form', methods=['POST'])
@login_required
def api_resend_checkout_form():
    """Manually re-sends the device-handover form reminder email to the
    teacher for one booking — for when the admin wants to nudge them again
    regardless of whether the automatic one-shot reminder already fired."""
    data = request.get_json(silent=True) or {}
    b = Booking.query.filter_by(req_id=data.get('reqId', '')).first()
    if not b:
        return jsonify({'success': False, 'error': 'الحجز غير موجود'}), 404
    if b.status not in ('approved', 'completed'):
        return jsonify({'success': False, 'error': 'الحجز غير معتمد بعد'}), 400
    if not is_valid_email(b.email):
        return jsonify({'success': False, 'error': 'لا يوجد بريد إلكتروني صحيح لهذا الحجز'}), 400

    base_url = get_base_url()
    checkout_url = f"{base_url}/checkout/{b.req_id}" if base_url else ''
    ctx = {
        'reqId': b.req_id, 'name': b.name, 'email': b.email,
        'title': b.event_title, 'stage': b.stage_name, 'grade': b.grade_name,
        'section': b.section_name,
        'periodLabel': f'الحصة {b.period_number}' if b.period_number else '',
        'date': b.booking_date, 'startTime': b.start_time, 'endTime': b.end_time,
        'checkoutUrl': checkout_url,
    }
    from utils.email_utils import send_checkout_reminder
    try:
        ok = send_checkout_reminder(ctx)
    except Exception as e:
        print(f"[email] resend checkout form failed for {b.req_id}: {e}", flush=True)
        ok = False

    return jsonify({'success': ok, 'error': None if ok else 'تعذّر إرسال البريد — تأكد من إعدادات البريد بالخادم'})


@admin_bp.route('/api/send-checkout-report', methods=['POST'])
@login_required
def api_send_checkout_report():
    data = request.get_json(silent=True) or {}
    emails = data.get('emails', [])
    lines = data.get('lines', [])

    valid_emails = [sanitize_email(e) for e in emails if is_valid_email(sanitize_email(e))]
    if not valid_emails:
        return jsonify({'success': False, 'error': 'لا يوجد بريد إلكتروني صحيح'}), 400
    if not lines:
        return jsonify({'success': False, 'error': 'لا توجد بيانات لإرسالها'}), 400

    from utils.email_utils import send_checkout_report_email
    try:
        ok = send_checkout_report_email(valid_emails, lines)
    except Exception as e:
        print(f"[email] send_checkout_report_email failed: {e}", flush=True)
        ok = False

    return jsonify({'success': ok, 'error': None if ok else 'تعذّر إرسال البريد — تأكد من إعدادات البريد بالخادم'})


# ── Archive old data (export-then-delete, to stay within storage limits) ──
@admin_bp.route('/api/archive-preview')
@login_required
def api_archive_preview():
    before_date = request.args.get('beforeDate', '')
    if not before_date:
        return jsonify({'error': 'التاريخ مطلوب'}), 400

    bookings = scope_query(Booking.query, Booking.stage_id).filter(Booking.booking_date < before_date).all()
    by_status = {}
    for b in bookings:
        by_status[b.status] = by_status.get(b.status, 0) + 1

    booking_ids = [b.id for b in bookings]
    checkout_count = (BookingCheckout.query.filter(BookingCheckout.booking_id.in_(booking_ids)).count()
                       if booking_ids else 0)

    return jsonify({'total': len(bookings), 'byStatus': by_status, 'checkoutCount': checkout_count})


@admin_bp.route('/api/archive-export')
@login_required
def api_archive_export():
    import io
    import csv
    import zipfile

    before_date = request.args.get('beforeDate', '')
    if not before_date:
        return jsonify({'error': 'التاريخ مطلوب'}), 400

    bookings = (scope_query(Booking.query, Booking.stage_id)
                .filter(Booking.booking_date < before_date).order_by(Booking.booking_date).all())
    booking_ids = [b.id for b in bookings]
    booking_map = {b.id: b for b in bookings}

    checkouts = (BookingCheckout.query.filter(BookingCheckout.booking_id.in_(booking_ids)).all()
                 if booking_ids else [])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        b_io = io.StringIO()
        b_io.write('\ufeff')  # BOM so Excel opens Arabic text correctly
        writer = csv.writer(b_io)
        writer.writerow(['reqId', 'name', 'email', 'phone', 'title', 'bookingDate', 'stage', 'grade',
                         'section', 'periodNumber', 'startTime', 'endTime', 'status', 'notes',
                         'rejectReason', 'createdAt'])
        for b in bookings:
            writer.writerow([
                b.req_id, b.name, b.email, b.phone or '', b.event_title or '', b.booking_date,
                b.stage_name or '', b.grade_name or '', b.section_name or '', b.period_number or '',
                b.start_time or '', b.end_time or '', b.status, b.notes or '', b.reject_reason or '',
                b.created_at.isoformat() if b.created_at else '',
            ])
        zf.writestr('bookings.csv', b_io.getvalue())

        c_io = io.StringIO()
        c_io.write('\ufeff')
        writer2 = csv.writer(c_io)
        writer2.writerow(['reqId', 'teacher', 'date', 'studentName', 'laptopNumber'])
        for co in checkouts:
            b = booking_map.get(co.booking_id)
            for line in co.lines:
                writer2.writerow([
                    b.req_id if b else '', b.name if b else '', b.booking_date if b else '',
                    line.student_name or '', line.laptop_number if line.laptop_number is not None else '',
                ])
        zf.writestr('checkout_lines.csv', c_io.getvalue())

    buf.seek(0)
    filename = f'archive_before_{before_date}.zip'
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name=filename)


@admin_bp.route('/api/archive-delete', methods=['POST'])
@login_required
def api_archive_delete():
    data = request.get_json(silent=True) or {}
    before_date = data.get('beforeDate', '')
    confirm = data.get('confirm', False)

    if not before_date:
        return jsonify({'success': False, 'error': 'التاريخ مطلوب'}), 400
    if not confirm:
        return jsonify({'success': False, 'error': 'يجب تأكيد العملية'}), 400

    bookings = Booking.query.filter(Booking.booking_date < before_date).all()
    booking_ids = [b.id for b in bookings]
    if not booking_ids:
        return jsonify({'success': True, 'deletedBookings': 0, 'deletedCheckouts': 0})

    # Delete dependent checkout data first — Booking has no ORM-level
    # cascade to BookingCheckout, and the FK would otherwise block deletion.
    checkouts = BookingCheckout.query.filter(BookingCheckout.booking_id.in_(booking_ids)).all()
    checkout_count = len(checkouts)
    for co in checkouts:
        CheckoutLine.query.filter_by(checkout_id=co.id).delete()
        db.session.delete(co)

    deleted_count = len(booking_ids)
    Booking.query.filter(Booking.id.in_(booking_ids)).delete(synchronize_session=False)
    db.session.commit()

    return jsonify({'success': True, 'deletedBookings': deleted_count, 'deletedCheckouts': checkout_count})


# ── Scheduled reminders (admin picks an exact date/time per booking) ──────
@admin_bp.route('/api/booking-reminder')
@login_required
def api_get_booking_reminder():
    """Return the pending (unsent) ADMIN reminder for a booking, if any —
    a teacher's own reminder on the same booking is entirely separate and
    never shown or touched here."""
    req_id = request.args.get('reqId', '')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'reminder': None})
    reminder = (BookingReminder.query.filter_by(booking_id=b.id, sent=False, kind='admin')
                .order_by(BookingReminder.remind_at.desc()).first())
    return jsonify({'reminder': reminder.to_dict() if reminder else None})


@admin_bp.route('/api/schedule-reminder', methods=['POST'])
@login_required
def api_schedule_reminder():
    data = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    remind_at_str = data.get('remindAt', '')  # expected "YYYY-MM-DDTHH:MM" from <input type="datetime-local">
    recipient_email = sanitize_email(data.get('recipientEmail', ''))
    note = (data.get('note') or '').strip()

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'الحجز غير موجود'}), 404
    if b.status not in ('approved', 'completed'):
        return jsonify({'success': False, 'error': 'التذكير متاح فقط للحجوزات المعتمدة'}), 400
    if not remind_at_str:
        return jsonify({'success': False, 'error': 'التاريخ والوقت مطلوبان'}), 400
    if not is_valid_email(recipient_email):
        return jsonify({'success': False, 'error': 'بريد إلكتروني صحيح مطلوب لاستلام التذكير'}), 400

    try:
        remind_at = datetime.strptime(remind_at_str, '%Y-%m-%dT%H:%M')
    except ValueError:
        return jsonify({'success': False, 'error': 'صيغة التاريخ/الوقت غير صحيحة'}), 400

    # The picker gives the admin's local wall-clock time (Jordan, UTC+3, no
    # DST) — compare against local "now", not UTC, or every reminder would
    # look 3 hours further in the future than the admin actually meant.
    from datetime import timedelta
    jordan_now = datetime.utcnow() + timedelta(hours=3)
    if remind_at <= jordan_now:
        return jsonify({'success': False, 'error': 'يجب أن يكون وقت التذكير بالمستقبل'}), 400

    # Replace any existing pending ADMIN reminder for this booking — a
    # teacher's own reminder on the same booking is untouched, by design.
    BookingReminder.query.filter_by(booking_id=b.id, sent=False, kind='admin').delete()
    db.session.add(BookingReminder(booking_id=b.id, remind_at=remind_at,
                                    recipient_email=recipient_email, note=note, kind='admin'))
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/cancel-reminder', methods=['POST'])
@login_required
def api_cancel_reminder():
    """Cancels the admin's own pending reminder only — never a teacher's."""
    data = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'الحجز غير موجود'}), 404
    BookingReminder.query.filter_by(booking_id=b.id, sent=False, kind='admin').delete()
    db.session.commit()
    return jsonify({'success': True})
