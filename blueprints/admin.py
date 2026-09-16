from datetime import datetime, date
from functools import wraps
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, url_for, current_app, send_file)
from models import (db, Booking, Stage, Grade, Section, Period, BlockedPeriod, Contact,
                    Teacher, Student, BookingCheckout, CheckoutLine, BookingReminder, PushSubscription, AppSetting)
from utils.helpers import (is_valid_email, sanitize_email, save_upload,
                            get_all_contact_emails, get_approved_notify_emails, check_conflict, check_blocked,
                            resolve_stage_grade_section_by_name)
from utils.email_utils import (send_approve, send_reject, send_cancel,
                                send_pending, send_update, send_staff_notification)

admin_bp = Blueprint('admin', __name__)

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
        'startTime': b.start_time, 'endTime': b.end_time,
    }
    period = Period.query.get(b.period_id) if b.period_id else None
    ctx['periodLabel'] = (period.label_ar if period else None) or (
        f'الحصة {b.period_number}' if b.period_number else '')
    ctx.update(extra)
    return ctx


# ── Auth decorator ────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated


# ── Login / Logout ────────────────────────────────────────────────────────
@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    lang = request.args.get('lang') or request.form.get('lang', 'ar')
    if request.method == 'POST':
        u = request.form.get('username', '')
        p = request.form.get('password', '')
        if (u == current_app.config['ADMIN_USER'] and
                p == current_app.config['ADMIN_PASS']):
            session.permanent = True
            session['admin_logged_in'] = True
            session['admin_lang'] = lang
            return redirect(url_for('admin.dashboard'))
        error = 'بيانات خاطئة' if lang == 'ar' else 'Invalid credentials'
    return render_template('admin/login.html', error=error, lang=lang)


@admin_bp.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin.login'))


@admin_bp.route('/')
@login_required
def dashboard():
    lang = session.get('admin_lang', 'ar')
    return render_template('admin/dashboard.html', lang=lang)


# ── Bookings API ──────────────────────────────────────────────────────────
@admin_bp.route('/api/bookings')
@login_required
def api_bookings():
    filt = request.args.get('filter', 'all')
    q = Booking.query
    if filt != 'all':
        q = q.filter_by(status=filt)
    bookings = q.order_by(Booking.created_at.desc()).all()
    return jsonify([b.to_dict() for b in bookings])


@admin_bp.route('/api/settings')
@login_required
def api_get_settings():
    return jsonify({
        'autoApproveBookings': AppSetting.get_bool('auto_approve_bookings', False),
        'autoPrintReceipts': AppSetting.get_bool('auto_print_receipts', False),
        'disableCheckoutFormLink': AppSetting.get_bool('disable_checkout_form_link', False),
    })


@admin_bp.route('/api/settings', methods=['POST'])
@login_required
def api_update_settings():
    data = request.get_json(silent=True) or {}
    if 'autoApproveBookings' in data:
        AppSetting.set_bool('auto_approve_bookings', bool(data['autoApproveBookings']))
    if 'autoPrintReceipts' in data:
        AppSetting.set_bool('auto_print_receipts', bool(data['autoPrintReceipts']))
    if 'disableCheckoutFormLink' in data:
        AppSetting.set_bool('disable_checkout_form_link', bool(data['disableCheckoutFormLink']))
    return jsonify({'success': True})


@admin_bp.route('/api/print-queue')
@login_required
def api_print_queue():
    if not AppSetting.get_bool('auto_print_receipts', False):
        return jsonify([])
    bookings = (Booking.query
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

    total   = Booking.query.count()
    pending = Booking.query.filter_by(status='pending').count()
    approved= Booking.query.filter_by(status='approved').count()
    completed=Booking.query.filter_by(status='completed').count()
    rejected= Booking.query.filter_by(status='rejected').count()
    cancelled=Booking.query.filter_by(status='cancelled').count()
    today_c = Booking.query.filter_by(booking_date=today).count()
    stages_c = Stage.query.filter_by(active=True).count()

    return jsonify({
        'total': total, 'pending': pending, 'approved': approved, 'completed': completed,
        'rejected': rejected, 'cancelled': cancelled,
        'today': today_c, 'halls': stages_c,
    })


@admin_bp.route('/api/approve', methods=['POST'])
@login_required
def api_approve():
    data   = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    b.status       = 'approved'
    b.action_date  = datetime.utcnow()
    b.approved_at      = datetime.utcnow()
    b.approved_by_name = current_app.config.get('ADMIN_USER', 'الإدارة')
    db.session.commit()

    base_url = current_app.config.get('BASE_URL', '')
    checkout_url = f"{base_url}/checkout/{b.req_id}" if base_url and not AppSetting.get_bool('disable_checkout_form_link', False) else ''

    print(f"[email] DEBUG: approve started for {req_id}", flush=True)
    try:
        ctx = _booking_email_ctx(b)
        contacts = get_approved_notify_emails(b.stage_id)
        contacts = [{'email': e} for e in contacts]
        send_staff_notification('approve', ctx, contacts)
        ctx2 = _booking_email_ctx(b, checkoutUrl=checkout_url)
        send_approve(ctx2)
    except Exception as e:
        import traceback
        print(f"[email] notification failed: {e}", flush=True)

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

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if b.status == 'cancelled':
        return jsonify({'success': False, 'error': 'الحجز ملغي بالفعل'}), 400

    b.status      = 'cancelled'
    b.action_date = datetime.utcnow()
    db.session.commit()

    try:
        send_staff_notification('cancel', _booking_email_ctx(b), _get_contacts(b.stage_id))
        send_cancel(_booking_email_ctx(b))
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
    b.period_number = period.number
    b.start_time    = period.start_time or ''
    b.end_time      = period.end_time or ''
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
    booking_ids = [b.id for b in bookings]
    if not booking_ids:
        return 0

    checkouts = BookingCheckout.query.filter(BookingCheckout.booking_id.in_(booking_ids)).all()
    for co in checkouts:
        CheckoutLine.query.filter_by(checkout_id=co.id).delete()
        db.session.delete(co)

    BookingReminder.query.filter(BookingReminder.booking_id.in_(booking_ids)).delete(synchronize_session=False)

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
    stages = Stage.query.order_by(Stage.sort_order).all()
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


# ── Blocked periods API ───────────────────────────────────────────────────
@admin_bp.route('/api/blocked')
@login_required
def api_blocked():
    blocks = BlockedPeriod.query.order_by(BlockedPeriod.from_date).all()
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
        hall      = data.get('hall', ''),
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
    return jsonify([c.to_dict() for c in contacts])


@admin_bp.route('/api/add-contacts', methods=['POST'])
@login_required
def api_add_contacts():
    data  = request.get_json(silent=True) or {}
    items = data.get('list', [])
    added = 0
    for item in items:
        em = sanitize_email(item.get('email', ''))
        stage_id = item.get('stageId') or None
        if is_valid_email(em):
            exists = Contact.query.filter_by(email=em, stage_id=stage_id).first()
            if not exists:
                db.session.add(Contact(email=em, name=item.get('name', ''), stage_id=stage_id,
                                        notify_handover=bool(item.get('notifyHandover'))))
                added += 1
    db.session.commit()
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
    bookings = Booking.query.filter(
        Booking.status.notin_(['rejected', 'cancelled'])
    ).all()
    return jsonify([b.to_dict() for b in bookings])


# ── Teachers API ──────────────────────────────────────────────────────────
@admin_bp.route('/api/teachers')
@login_required
def api_teachers():
    stage_id = request.args.get('stageId', type=int)
    q = Teacher.query
    if stage_id:
        q = q.filter_by(stage_id=stage_id)
    teachers = q.order_by(Teacher.name).all()
    return jsonify([t.to_dict() for t in teachers])


@admin_bp.route('/api/add-teacher', methods=['POST'])
@login_required
def api_add_teacher():
    data = request.get_json(silent=True) or {}
    if not data.get('name'):
        return jsonify({'success': False, 'error': 'اسم المعلم مطلوب'}), 400
    tch = Teacher(name=data['name'], email=sanitize_email(data.get('email', '')),
                  phone=data.get('phone', ''), stage_id=data.get('stageId') or None,
                  grade_id=data.get('gradeId') or None, section_id=data.get('sectionId') or None)
    db.session.add(tch)
    db.session.commit()
    return jsonify({'success': True, 'id': tch.id})


@admin_bp.route('/api/bulk-add-teachers', methods=['POST'])
@login_required
def api_bulk_add_teachers():
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
    db.session.commit()
    return jsonify({'success': True})


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
