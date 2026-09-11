from datetime import datetime, date
from functools import wraps
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, url_for, current_app, send_file)
from models import (db, Booking, Stage, Grade, Section, Period, BlockedPeriod, Contact,
                    Teacher, Student, BookingCheckout, CheckoutLine, BookingReminder)
from utils.helpers import (is_valid_email, sanitize_email, save_upload,
                            get_all_contact_emails, check_conflict, check_blocked,
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

    b.status      = 'approved'
    b.action_date = datetime.utcnow()
    db.session.commit()

    base_url = current_app.config.get('BASE_URL', '')
    checkout_url = f"{base_url}/checkout/{b.req_id}" if base_url else ''

    print(f"[email] DEBUG: approve started for {req_id}", flush=True)
    try:
        print("[email] DEBUG: building context...", flush=True)
        ctx = _booking_email_ctx(b)
        print(f"[email] DEBUG: context built, email={ctx.get('email')}", flush=True)
        contacts = _get_contacts(b.stage_id)
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
    c.notify_handover = bool(data.get('notifyHandover'))
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
    q = Student.query
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
    for co in checkouts:
        b = Booking.query.get(co.booking_id)
        if not b:
            continue
        covered_booking_ids.add(b.id)
        for line in co.lines:
            lines_out.append({
                'reqId': b.req_id, 'teacher': b.name, 'date': b.booking_date,
                'stage': b.stage_name, 'grade': b.grade_name, 'section': b.section_name,
                'periodNumber': b.period_number,
                'studentName': line.student_name, 'laptopNumber': line.laptop_number,
            })

    q = Booking.query.filter(Booking.status.in_(['approved', 'completed']))
    if covered_booking_ids:
        q = q.filter(~Booking.id.in_(covered_booking_ids))
    missing = q.order_by(Booking.booking_date.desc()).all()

    missing_out = [{
        'reqId': b.req_id, 'teacher': b.name, 'email': b.email, 'date': b.booking_date,
        'stage': b.stage_name, 'grade': b.grade_name, 'section': b.section_name,
        'status': b.status,
    } for b in missing]

    return jsonify({'lines': lines_out, 'missing': missing_out})


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

    bookings = Booking.query.filter(Booking.booking_date < before_date).all()
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

    bookings = Booking.query.filter(Booking.booking_date < before_date).order_by(Booking.booking_date).all()
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
