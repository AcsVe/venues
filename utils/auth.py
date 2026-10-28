"""Admin-panel accounts: the permission catalogue, the signed-in user, the per-endpoint guard and the stage scope.

* ``sysadmin`` — every page, every setting, every stage, and the «المستخدمون» page.
* ``user``     — only the pages and settings ticked on the account, and (unless «كل المراحل») only the bookings and
  lists of the ticked stages. Anything outside the scope is answered as if it did not exist (404), so an id from
  another stage reveals nothing.

The guard runs before every admin endpoint (``guard_request``): an endpoint missing from ``ENDPOINT_PERMS`` is
allowed to the system administrator only, so a new route is closed until it is given a permission here.
"""
import json
from datetime import datetime

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

# ── the catalogue: (key, Arabic, English) — the order is the order of the checkboxes ──────────────────────────────
PAGE_PERMS = [
    ('bookings.view',   'عرض طلبات الحجز',                                   'View booking requests'),
    ('bookings.manage', 'اعتماد الحجوزات ورفضها وتعديلها والتعليق عليها',      'Approve, reject, edit and comment on bookings'),
    ('bookings.delete', 'حذف الحجوزات',                                       'Delete bookings'),
    ('calendar',        'التقويم',                                            'Calendar'),
    ('blocked',         'غير متاح (تعطيل الأيام والحصص)',                     'Unavailable days and periods'),
    ('teachers',        'المعلمون',                                           'Teachers'),
    ('students',        'الطلاب',                                             'Students'),
    ('reports',         'التقارير وتقرير التسليم',                            'Reports and handover report'),
    ('archive',         'الأرشفة',                                            'Archive'),
    ('structure',       'المراحل والعربات والصفوف والحصص',                     'Stages, trolleys, grades and periods'),
    ('contacts',        'جهات الاتصال',                                       'Contacts'),
    ('ticker',          'الشريط الإعلاني (#ticker)',                           'Announcement ticker (#ticker)'),
]
SETTINGS_PERMS = [
    ('settings.identity',  'الهوية والشعار وحالة البريد',                       'Identity, logo and mail status'),
    ('settings.approval',  'الموافقة التلقائية',                                 'Auto-approval'),
    ('settings.rules',     'قواعد الحجز (الحصص قبل الاستراحة والقيود)',          'Booking rules (pre-break periods, restrictions)'),
    ('settings.printing',  'الطباعة التلقائية لإيصال التسليم',                   'Auto-print of the delivery receipt'),
    ('settings.handover',  'رابط نموذج تسليم الأجهزة',                           'Handover form link'),
    ('settings.reminders', 'تذكيرات الحجوزات لموظفي النظام',                     'Staff booking reminders'),
]
ALL_PERMS = [k for k, _a, _e in PAGE_PERMS + SETTINGS_PERMS]
SETTINGS_KEYS = tuple(k for k, _a, _e in SETTINGS_PERMS)

# /api/settings keys → the permission that may change them
SETTING_FIELD_PERMS = {
    'autoApproveBookings': 'settings.approval',
    'preBreakPeriodsEnabled': 'settings.rules',
    'blockConsecutivePeriods': 'settings.rules',
    'blockSectionMultiPerDay': 'settings.rules',
    'autoPrintReceipts': 'settings.printing',
    'disableCheckoutFormLink': 'settings.handover',
    'staffDailySummaryEnabled': 'settings.reminders',
    'staffDailySummaryTime': 'settings.reminders',
    'staffReminderLeadMinutes': 'settings.reminders',
}

ANY = None              # any signed-in account
SYS = 'sysadmin'        # the system administrator only

# endpoint → permission (a string), several permissions any of which is enough (a tuple), ANY, or SYS
ENDPOINT_PERMS = {
    'admin.dashboard': ANY, 'admin.change_password': ANY, 'admin.api_me': ANY,
    'admin.save_brand': 'settings.identity',
    'admin.api_bookings': ('bookings.view', 'calendar', 'reports'),
    'admin.api_booking_comments': 'bookings.view',
    'admin.api_add_comment': 'bookings.manage',
    'admin.api_get_settings': SETTINGS_KEYS,
    'admin.api_update_settings': SETTINGS_KEYS,          # each key is checked in the route
    'admin.api_print_queue': ANY,                        # answers [] without settings.printing
    'admin.api_mark_printed': 'settings.printing',
    'admin.api_vapid_public_key': ANY, 'admin.api_push_subscribe': ANY, 'admin.api_push_unsubscribe': ANY,
    'admin.api_push_debug': SYS,
    'admin.api_stats': ANY,
    'admin.api_approve': 'bookings.manage', 'admin.api_complete': 'bookings.manage',
    'admin.api_reject': 'bookings.manage', 'admin.api_cancel': 'bookings.manage',
    'admin.api_set_pending': 'bookings.manage', 'admin.api_update_booking': 'bookings.manage',
    'admin.api_delete_booking': 'bookings.delete', 'admin.api_bulk_delete': 'bookings.delete',
    'admin.api_halls': ANY, 'admin.api_admin_periods': ANY, 'admin.api_grade_period_times': ANY,
    'admin.api_add_stage': 'structure', 'admin.api_update_stage': 'structure', 'admin.api_delete_stage': 'structure',
    'admin.api_add_grade': 'structure', 'admin.api_update_grade': 'structure', 'admin.api_delete_grade': 'structure',
    'admin.api_add_section': 'structure', 'admin.api_update_section': 'structure',
    'admin.api_delete_section': 'structure',
    'admin.api_add_period': 'structure', 'admin.api_update_period': 'structure', 'admin.api_delete_period': 'structure',
    'admin.api_set_grade_period_times': 'structure', 'admin.api_copy_grade_period_times': 'structure',
    'admin.api_blocked': ('blocked', 'bookings.view', 'calendar'),
    'admin.api_add_blocked': 'blocked', 'admin.api_delete_blocked': 'blocked',
    'admin.api_contacts': 'contacts', 'admin.api_add_contacts': 'contacts',
    'admin.api_update_contact': 'contacts', 'admin.api_delete_contact': 'contacts',
    'admin.api_report_data': 'reports',
    'admin.api_teachers': ('teachers', 'bookings.view', 'bookings.manage'),
    'admin.api_teachers_export': 'teachers',
    'admin.api_add_teacher': 'teachers', 'admin.api_bulk_add_teachers': 'teachers',
    'admin.api_update_teacher': 'teachers', 'admin.api_block_teachers_bulk': 'teachers',
    'admin.api_delete_teacher': 'teachers', 'admin.api_delete_teachers_bulk': 'teachers',
    'admin.api_move_teachers_bulk': 'teachers',
    'admin.api_get_ticker_settings': 'ticker', 'admin.api_set_ticker_settings': 'ticker',
    'admin.api_students': ('students', 'bookings.view'),
    'admin.api_add_student': 'students', 'admin.api_bulk_add_students': 'students',
    'admin.api_import_roster_autocreate': 'students',
    'admin.api_delete_student': 'students', 'admin.api_delete_students_bulk': 'students',
    'admin.api_move_student': 'students', 'admin.api_move_students_bulk': 'students',
    'admin.api_get_checkout': 'bookings.view',
    'admin.api_checkout_report': 'reports', 'admin.api_send_checkout_report': 'reports',
    'admin.api_resend_checkout_form': 'bookings.manage',
    'admin.api_archive_preview': 'archive', 'admin.api_archive_export': 'archive', 'admin.api_archive_delete': 'archive',
    'admin.api_get_booking_reminder': 'bookings.view',
    'admin.api_schedule_reminder': 'bookings.manage', 'admin.api_cancel_reminder': 'bookings.manage',
    'admin.api_users': SYS, 'admin.api_save_user': SYS, 'admin.api_users_bulk': SYS,
}
PUBLIC_ENDPOINTS = {'admin.login', 'admin.logout', 'admin.forgot_password', 'admin.reset_password', 'admin.static'}

# endpoints whose payload must stay inside the user's stages (see ``_payload_stages``)
NEEDS_ALL_STAGES = {'admin.api_add_stage', 'admin.api_import_roster_autocreate', 'admin.api_bulk_add_students',
                    'admin.api_bulk_add_teachers', 'admin.api_archive_delete', 'admin.api_add_period',
                    'admin.api_update_period', 'admin.api_delete_period', 'admin.api_copy_grade_period_times'}


# ── passwords and the signed-in account ───────────────────────────────────────────────────────────────────────────
def hash_password(raw):
    return generate_password_hash(raw)


def password_ok(user, raw):
    try:
        return bool(user and raw and check_password_hash(user.password_hash, raw))
    except (ValueError, TypeError):
        return False


def current_user():
    if 'me' in g:
        return g.me
    from models import AdminUser
    uid = session.get('admin_uid')
    user = AdminUser.query.get(uid) if uid else None
    if user is not None and (not user.active or session.get('admin_pw') != user.password_hash[-12:]):
        user = None          # disabled, or the password was changed elsewhere: sign in again
    g.me = user
    return user


def can(perm):
    u = current_user()
    if u is None:
        return False
    if perm == SYS:
        return u.is_sysadmin
    if isinstance(perm, (tuple, list)):
        return any(u.can(p) for p in perm)
    return u.can(perm)


def allowed_stage_ids():
    """None = every stage; otherwise the set of stage ids the signed-in account may see."""
    u = current_user()
    if u is None:
        return set()
    if u.is_sysadmin or u.all_stages:
        return None
    return set(u.stage_list)


def in_scope(stage_id):
    ids = allowed_stage_ids()
    return ids is None or (stage_id is not None and int(stage_id) in ids)


def scope_query(query, column):
    """Limit a query to the account's stages (no-op for «all stages»)."""
    ids = allowed_stage_ids()
    if ids is None:
        return query
    return query.filter(column.in_(list(ids) or [-1]))


def allowed_trolley_codes():
    from models import Stage
    ids = allowed_stage_ids()
    if ids is None:
        return None
    return {s.trolley_code for s in Stage.query.filter(Stage.id.in_(list(ids) or [-1])).all()}


def me_json():
    """What the dashboard script needs to show only the allowed pages, settings and stages."""
    u = current_user()
    if u is None:
        return {}
    perms = ALL_PERMS if u.is_sysadmin else [p for p in u.perm_list if p in ALL_PERMS]
    ids = allowed_stage_ids()
    return {'id': u.id, 'username': u.username, 'fullName': u.full_name, 'role': u.role,
            'sysadmin': u.is_sysadmin, 'perms': perms, 'stageIds': None if ids is None else sorted(ids)}


def sign_in(user, remember):
    session.clear()
    session.permanent = bool(remember)
    session['admin_uid'] = user.id
    session['admin_pw'] = user.password_hash[-12:]
    session['admin_logged_in'] = True          # kept for older code paths
    from models import db
    user.last_login = datetime.now()
    db.session.commit()


# ── the guard ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _deny(status, msg_ar, msg_en):
    if request.path.startswith('/admin/api/'):
        return jsonify({'success': False, 'error': msg_ar, 'errorEn': msg_en}), status
    if status == 401:
        return redirect(url_for('admin.login', next=request.full_path if request.method == 'GET' else None))
    abort(status)


def _json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _booking_stages(req_ids):
    from models import Booking
    req_ids = [str(r) for r in req_ids if r not in (None, '')]
    if not req_ids:
        return set()
    return {b.stage_id for b in Booking.query.filter(Booking.req_id.in_(req_ids)).all()}


def _payload_stages(endpoint):
    """The stages a request touches (to check them against the account's scope); None when nothing to check."""
    from models import Contact, Grade, Section, Student, Teacher
    d, a = _json(), request.args
    out = set()

    def ids(*keys):
        vals = []
        for k in keys:
            v = d.get(k, a.get(k))
            if isinstance(v, list):
                vals.extend(v)
            elif v not in (None, ''):
                vals.append(v)
        out_ = []
        for v in vals:
            try:
                out_.append(int(v))
            except (TypeError, ValueError):
                pass
        return out_

    def grade_stage(gid):
        gr = Grade.query.get(gid)
        return gr.stage_id if gr else None

    def section_stage(sid):
        sec = Section.query.get(sid)
        return grade_stage(sec.grade_id) if sec else None

    name = endpoint.split('.', 1)[-1]
    if name in ('api_approve', 'api_complete', 'api_reject', 'api_cancel', 'api_set_pending', 'api_update_booking',
                'api_delete_booking', 'api_bulk_delete', 'api_add_comment', 'api_booking_comments',
                'api_resend_checkout_form', 'api_get_booking_reminder', 'api_schedule_reminder',
                'api_cancel_reminder', 'api_mark_printed', 'api_get_checkout'):
        refs = []
        for k in ('reqId', 'ids', 'reqIds'):
            v = d.get(k, a.get(k))
            refs.extend(v if isinstance(v, list) else [v])
        if request.view_args and request.view_args.get('req_id'):
            refs.append(request.view_args['req_id'])
        out |= _booking_stages(refs)
        if name == 'api_update_booking' and d.get('stageId'):
            out |= set(ids('stageId'))
    elif name in ('api_update_stage', 'api_delete_stage'):
        out |= set(ids('id'))
    elif name in ('api_add_grade',):
        out |= set(ids('stageId'))
    elif name in ('api_update_grade', 'api_delete_grade'):
        out |= {grade_stage(i) for i in ids('id', 'gradeId')}
    elif name in ('api_add_section',):
        out |= {grade_stage(i) for i in ids('gradeId')}
    elif name in ('api_update_section', 'api_delete_section'):
        out |= {section_stage(i) for i in ids('id')}
    elif name in ('api_set_grade_period_times', 'api_grade_period_times'):
        out |= {grade_stage(i) for i in ids('gradeId')}
    elif name.startswith('api_') and 'teacher' in name:
        out |= {t.stage_id for t in Teacher.query.filter(Teacher.id.in_(ids('id', 'ids') or [-1])).all()}
        out |= set(ids('stageId'))
        out |= {grade_stage(i) for i in ids('gradeId')} | {section_stage(i) for i in ids('sectionId')}
        for row in d.get('list') or []:
            if isinstance(row, dict) and row.get('stageId'):
                out.add(int(row['stageId']))
    elif name.startswith('api_') and 'student' in name:
        out |= {s.stage_id for s in Student.query.filter(Student.id.in_(ids('id', 'ids') or [-1])).all()}
        out |= {section_stage(i) for i in ids('sectionId', 'fromSectionId', 'toSectionId')}
        out |= {grade_stage(i) for i in ids('gradeId')} | set(ids('stageId'))
    elif name in ('api_update_contact', 'api_delete_contact'):
        out |= {c.stage_id for c in Contact.query.filter(Contact.id.in_(ids('id', 'ids') or [-1])).all()}
    elif name == 'api_add_contacts':
        for row in d.get('list') or []:
            if isinstance(row, dict):
                out.add(int(row['stageId']) if row.get('stageId') else None)
    elif name == 'api_add_blocked':
        from models import Stage
        hall = d.get('hall') or ''
        st = Stage.query.filter_by(trolley_code=hall).first() if hall else None
        out.add(st.id if st else None)
    elif name == 'api_delete_blocked':
        from models import BlockedPeriod, Stage
        for b in BlockedPeriod.query.filter(BlockedPeriod.id.in_(ids('id') or [-1])).all():
            st = Stage.query.filter_by(trolley_code=b.hall).first() if b.hall else None
            out.add(st.id if st else None)
    else:
        return None
    return out


def guard_request():
    """before_request of the admin blueprint."""
    ep = request.endpoint or ''
    if ep in PUBLIC_ENDPOINTS or not ep.startswith('admin.'):
        return None
    user = current_user()
    if user is None:
        return _deny(401, 'انتهت الجلسة — سجّل الدخول من جديد', 'Your session has ended — sign in again')
    need = ENDPOINT_PERMS.get(ep, SYS)
    if need is not ANY and not can(need):
        return _deny(403, 'لا تملك صلاحية لهذا الإجراء', 'You do not have permission for this action')
    ids = allowed_stage_ids()
    if ids is None:
        return None
    if ep in NEEDS_ALL_STAGES:
        return _deny(403, 'هذا الإجراء متاح لمن يملك صلاحية كل المراحل', 'This action needs access to every stage')
    touched = _payload_stages(ep)
    if touched and any(s is None or int(s) not in ids for s in touched):
        return _deny(404, 'غير موجود', 'Not found')
    return None


# ── first start ───────────────────────────────────────────────────────────────────────────────────────────────────
def ensure_first_admin(app):
    """On the first start after this update the account from .env (ADMIN_USER / ADMIN_PASS) becomes the first
    system administrator, so the same sign-in keeps working; later it is managed from «المستخدمون»."""
    from models import AdminUser, db
    if AdminUser.query.count():
        return
    username = (app.config.get('ADMIN_USER') or 'admin').strip() or 'admin'
    password = app.config.get('ADMIN_PASS') or 'acs2024'
    db.session.add(AdminUser(username=username, full_name='مدير النظام', role='sysadmin', active=True,
                             all_stages=True, stage_ids='[]', permissions=json.dumps(ALL_PERMS),
                             password_hash=hash_password(password)))
    db.session.commit()
    print('[auth] first system administrator created from .env', flush=True)


def catalogue(lang='ar'):
    i = 1 if lang == 'ar' else 2
    return {'pages': [(p[0], p[i]) for p in PAGE_PERMS], 'settings': [(p[0], p[i]) for p in SETTINGS_PERMS]}
