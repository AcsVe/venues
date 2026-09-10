import os
import secrets
from datetime import date, datetime, timedelta
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, send_from_directory, current_app)
from models import (db, Booking, Stage, Grade, Section, Period, BlockedPeriod,
                    Student, Teacher, BookingCheckout, CheckoutLine, BookingReminder)
from utils.helpers import (gen_req_id, check_conflict, check_blocked,
                            save_upload, get_all_contact_emails,
                            get_blocked_for_date, is_valid_email, sanitize_email)
from utils.email_utils import send_confirm, send_cancel, send_update, send_staff_notification, send_approve, send_reject

public_bp = Blueprint('public', __name__)


# ── Serve uploaded files ──────────────────────────────────────────────────
@public_bp.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


# ── Calendar ──────────────────────────────────────────────────────────────
@public_bp.route('/')
def index():
    return redirect(url_for('public.calendar'))


# ── Academic structure (stages → grades → sections) + periods ────────────
@public_bp.route('/api/structure')
def api_structure():
    stages = Stage.query.filter_by(active=True).order_by(Stage.sort_order).all()
    return jsonify([s.to_dict(with_grades=True) for s in stages])


@public_bp.route('/api/stages-list')
def api_stages_list():
    stages = Stage.query.filter_by(active=True).order_by(Stage.sort_order).all()
    return jsonify([s.to_dict() for s in stages])


@public_bp.route('/api/periods')
def api_periods():
    periods = Period.query.filter_by(active=True).order_by(Period.number).all()
    return jsonify([p.to_dict() for p in periods])


@public_bp.route('/calendar')
def calendar():
    lang = request.args.get('lang', 'ar')
    return render_template('calendar.html', lang=lang)


@public_bp.route('/api/month-data')
def month_data():
    try:
        y = int(request.args.get('y', date.today().year))
        m = int(request.args.get('m', date.today().month))
    except (ValueError, TypeError):
        return jsonify({'error': 'invalid params'}), 400

    month_start = f'{y:04d}-{m:02d}-01'
    if m == 12:
        month_end = f'{y+1:04d}-01-01'
    else:
        month_end = f'{y:04d}-{m+1:02d}-01'

    bookings = Booking.query.filter(
        Booking.status.notin_(['rejected', 'cancelled']),
        Booking.booking_date >= month_start,
        Booking.booking_date < month_end,
    ).all()

    blocked_map = {}
    for blk in BlockedPeriod.query.all():
        cur = blk.from_date
        while cur <= blk.to_date:
            if month_start <= cur < month_end:
                blocked_map.setdefault(cur, []).append({
                    'reason': blk.reason or 'غير متاح',
                    'fromTime': blk.from_time or '',
                    'toTime': blk.to_time or '',
                    'hall': blk.hall or '',
                })
            parts = cur.split('-')
            d_obj = date(int(parts[0]), int(parts[1]), int(parts[2]))
            from datetime import timedelta
            d_obj += timedelta(days=1)
            cur = d_obj.strftime('%Y-%m-%d')
            if cur > blk.to_date:
                break

    # Public view: hide sensitive details for privacy
    public_bookings = []
    for b in bookings:
        public_bookings.append({
            'date': b.booking_date,
            'hall': b.trolley_code or b.hall or '',
            'stage': b.stage_name or '',
            'startTime': b.start_time or '',
            'endTime': b.end_time or '',
            'periodNumber': b.period_number,
            'status': b.status,
        })
    return jsonify({
        'bookings': public_bookings,
        'blocked': blocked_map,
    })


# ── Booking form ──────────────────────────────────────────────────────────
@public_bp.route('/book')
def book():
    lang = request.args.get('lang', 'ar')
    pre_date = request.args.get('date', '')
    today = date.today().strftime('%Y-%m-%d')

    if pre_date and pre_date < today:
        msg = 'لا يمكن الحجز بتاريخ سابق' if lang == 'ar' else 'Cannot book a past date'
        return render_template('error.html', msg=msg, lang=lang)

    if pre_date:
        blk = check_blocked(pre_date, '', '', '')
        if blk['blocked'] and blk.get('fullBlock'):
            msg = (f'هذا اليوم غير متاح بالكامل: {blk["reason"]}' if lang == 'ar'
                   else f'This day is fully unavailable: {blk["reason"]}')
            return render_template('error.html', msg=msg, lang=lang)

    stages = Stage.query.filter_by(active=True).order_by(Stage.sort_order).all()
    periods = Period.query.filter_by(active=True).order_by(Period.number).all()
    return render_template('book.html', lang=lang, pre_date=pre_date,
                           stages=stages, periods=periods, today=today)


def _resolve_selection(data):
    """Look up Stage/Grade/Section/Period objects from posted ids.
    Returns (stage, grade, section, period, error)."""
    try:
        stage_id  = int(data.get('stageId'))
        grade_id  = int(data.get('gradeId'))
        section_id = int(data.get('sectionId'))
        period_id  = int(data.get('periodId'))
    except (TypeError, ValueError):
        return None, None, None, None, 'يرجى اختيار المرحلة والصف والشعبة والحصة'

    stage = Stage.query.get(stage_id)
    grade = Grade.query.get(grade_id)
    section = Section.query.get(section_id)
    period = Period.query.get(period_id)
    if not (stage and grade and section and period):
        return None, None, None, None, 'اختيار غير صحيح للمرحلة/الصف/الشعبة/الحصة'
    if grade.stage_id != stage.id or section.grade_id != grade.id:
        return None, None, None, None, 'اختيار غير متطابق للمرحلة/الصف/الشعبة'
    return stage, grade, section, period, None


@public_bp.route('/api/check-slot', methods=['POST'])
def check_slot():
    data = request.get_json(silent=True) or {}
    bdate = data.get('date', '')
    exclude = data.get('excludeReqId')

    if not bdate:
        return jsonify({'ok': False, 'error': 'بيانات ناقصة'})

    stage, grade, section, period, err = _resolve_selection(data)
    if err:
        return jsonify({'ok': False, 'error': err})

    blk = check_blocked(bdate, period.start_time or '', period.end_time or '', stage.trolley_code)
    if blk['blocked']:
        msg = f'التاريخ غير متاح: {blk["reason"]}'
        if blk.get('blkFromT'):
            msg += f' ({blk["blkFromT"]} - {blk["blkToT"]})'
        return jsonify({'ok': False, 'error': msg})

    conflict = check_conflict(stage.trolley_code, bdate, period.number, exclude)
    if conflict:
        return jsonify({'ok': False, 'error': conflict})

    return jsonify({'ok': True})


@public_bp.route('/api/submit-booking', methods=['POST'])
def submit_booking():
    today = date.today().strftime('%Y-%m-%d')

    if request.content_type and ('multipart' in request.content_type or
                                  'form' in request.content_type):
        f = request.form
        files = request.files.getlist('attachments')
    else:
        f = request.get_json(silent=True) or {}
        files = []

    required = ['fullName', 'email', 'bookingDate', 'stageId', 'gradeId', 'sectionId', 'periodId']
    for field in required:
        if not f.get(field):
            return jsonify({'success': False, 'error': f'الحقل {field} مطلوب'}), 400

    booking_date = f.get('bookingDate')
    if booking_date < today:
        return jsonify({'success': False, 'error': 'لا يمكن الحجز بتاريخ سابق'}), 400

    stage, grade, section, period, err = _resolve_selection(f)
    if err:
        return jsonify({'success': False, 'error': err}), 400

    blk = check_blocked(booking_date, period.start_time or '', period.end_time or '', stage.trolley_code)
    if blk['blocked']:
        msg = f'التاريخ غير متاح: {blk["reason"]}'
        if blk.get('blkFromT'):
            msg += f' ({blk["blkFromT"]} - {blk["blkToT"]})'
        return jsonify({'success': False, 'error': msg}), 400

    conflict = check_conflict(stage.trolley_code, booking_date, period.number)
    if conflict:
        return jsonify({'success': False, 'error': conflict}), 400

    att_urls = []
    for file_obj in files:
        if file_obj and file_obj.filename:
            url = save_upload(file_obj)
            if url:
                att_urls.append(url)

    req_id = gen_req_id()
    action_token = secrets.token_urlsafe(32)
    booking = Booking(
        req_id        = req_id,
        action_token  = action_token,
        name          = f.get('fullName'),
        email         = sanitize_email(f.get('email')),
        phone         = f.get('phone', ''),
        on_behalf     = f.get('onBehalf', ''),
        event_title   = f.get('eventTitle', ''),
        booking_date  = booking_date,
        stage_id      = stage.id,
        grade_id      = grade.id,
        section_id    = section.id,
        period_id     = period.id,
        trolley_code  = stage.trolley_code,
        stage_name    = stage.name_ar,
        grade_name    = grade.name_ar,
        section_name  = section.name_ar,
        period_number = period.number,
        start_time    = period.start_time or '',
        end_time      = period.end_time or '',
        notes         = f.get('notes', ''),
        attachments   = ','.join(att_urls),
        status        = 'pending',
    )
    db.session.add(booking)
    db.session.commit()

    # Optional: the teacher can ask to be reminded about their own booking
    # at a specific date/time. Best-effort — an invalid/missing value is
    # silently skipped rather than failing the whole booking submission.
    reminder_at_str = (f.get('reminderAt') or '').strip()
    if reminder_at_str:
        try:
            reminder_at = datetime.strptime(reminder_at_str, '%Y-%m-%dT%H:%M')
            if reminder_at > datetime.utcnow() + timedelta(hours=3):  # Jordan local "now"
                db.session.add(BookingReminder(
                    booking_id=booking.id, remind_at=reminder_at,
                    recipient_email=booking.email, kind='teacher',
                ))
                db.session.commit()
        except ValueError:
            pass

    email_ctx = {
        'reqId': req_id, 'name': booking.name, 'email': booking.email,
        'title': booking.event_title, 'stage': stage.name_ar, 'grade': grade.name_ar,
        'section': section.name_ar, 'periodLabel': period.label_ar or f'الحصة {period.number}',
        'date': booking_date, 'startTime': booking.start_time, 'endTime': booking.end_time,
    }
    base_url = current_app.config.get('BASE_URL', '')
    staff_ctx = dict(email_ctx)
    if base_url and action_token:
        staff_ctx['approveUrl'] = f"{base_url}/action/approve/{req_id}?token={action_token}"
        staff_ctx['rejectUrl']  = f"{base_url}/action/reject/{req_id}?token={action_token}"
    try:
        contacts = [{'email': e} for e in get_all_contact_emails(stage.id)]
        send_staff_notification('new', staff_ctx, contacts)
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)
    try:
        send_confirm(email_ctx)
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)

    return jsonify({'success': True, 'reqId': req_id})


# ── Booking lookup / cancel / amend (public) ─────────────────────────────
@public_bp.route('/lookup')
def lookup():
    lang = request.args.get('lang', 'ar')
    stages = Stage.query.filter_by(active=True).order_by(Stage.sort_order).all()
    periods = Period.query.filter_by(active=True).order_by(Period.number).all()
    return render_template('lookup.html', lang=lang, stages=stages, periods=periods)


@public_bp.route('/api/lookup-booking', methods=['POST'])
def api_lookup():
    data = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '').strip()
    email  = sanitize_email(data.get('email', ''))

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b or b.email.lower() != email.lower():
        return jsonify({'success': False, 'error': 'لم يتم العثور على الحجز أو البريد غير مطابق'}), 404
    if b.status == 'cancelled':
        return jsonify({'success': False, 'error': 'هذا الحجز ملغي بالفعل'}), 400

    return jsonify({'success': True, 'booking': b.to_dict()})


@public_bp.route('/api/cancel-booking', methods=['POST'])
def api_cancel_by_user():
    data = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '').strip()
    email  = sanitize_email(data.get('email', ''))

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b or b.email.lower() != email.lower():
        return jsonify({'success': False, 'error': 'غير موجود أو البريد غير مطابق'}), 404
    if b.status == 'cancelled':
        return jsonify({'success': False, 'error': 'الحجز ملغي بالفعل'}), 400
    if b.status == 'completed':
        return jsonify({'success': False, 'error': 'لا يمكن إلغاء حجز مكتمل'}), 400

    b.status      = 'cancelled'
    b.action_date = datetime.utcnow()
    db.session.commit()

    period = Period.query.get(b.period_id) if b.period_id else None
    period_label = (period.label_ar if period else None) or (f'الحصة {b.period_number}' if b.period_number else '')
    email_ctx = {'reqId': req_id, 'name': b.name, 'email': b.email,
                 'title': b.event_title, 'stage': b.stage_name, 'grade': b.grade_name,
                 'section': b.section_name, 'date': b.booking_date,
                 'startTime': b.start_time, 'endTime': b.end_time, 'periodLabel': period_label}
    try:
        send_cancel(email_ctx)
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)
    try:
        contacts = [{'email': e} for e in get_all_contact_emails(b.stage_id)]
        send_staff_notification('cancel', email_ctx, contacts)
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)

    return jsonify({'success': True})


@public_bp.route('/api/amend-booking', methods=['POST'])
def api_amend_by_user():
    if request.content_type and ('multipart' in request.content_type or
                                  'form' in request.content_type):
        f = request.form
        files = request.files.getlist('attachments')
    else:
        f = request.get_json(silent=True) or {}
        files = []

    req_id = f.get('reqId', '').strip()
    email  = sanitize_email(f.get('email', ''))

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b or b.email.lower() != email.lower():
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if b.status == 'cancelled':
        return jsonify({'success': False, 'error': 'الحجز ملغي'}), 400
    if b.status == 'completed':
        return jsonify({'success': False, 'error': 'لا يمكن تعديل حجز مكتمل'}), 400

    booking_date = f.get('bookingDate', b.booking_date)

    if f.get('stageId') or f.get('gradeId') or f.get('sectionId') or f.get('periodId'):
        stage, grade, section, period, err = _resolve_selection({
            'stageId': f.get('stageId') or b.stage_id,
            'gradeId': f.get('gradeId') or b.grade_id,
            'sectionId': f.get('sectionId') or b.section_id,
            'periodId': f.get('periodId') or b.period_id,
        })
        if err:
            return jsonify({'success': False, 'error': err}), 400
    else:
        stage = Stage.query.get(b.stage_id)
        grade = Grade.query.get(b.grade_id)
        section = Section.query.get(b.section_id)
        period = Period.query.get(b.period_id)
        if not (stage and grade and section and period):
            return jsonify({'success': False, 'error': 'تعذر إيجاد بيانات الحجز الأصلية'}), 400

    conflict = check_conflict(stage.trolley_code, booking_date, period.number, req_id)
    if conflict:
        return jsonify({'success': False, 'error': conflict}), 400

    was_approved = b.status == 'approved'
    b.name          = f.get('fullName', b.name)
    b.phone         = f.get('phone', b.phone or '')
    b.on_behalf     = f.get('onBehalf', b.on_behalf or '')
    b.event_title   = f.get('eventTitle', b.event_title or '')
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
    if was_approved:
        b.status = 'pending'

    for file_obj in files:
        if file_obj and file_obj.filename:
            url = save_upload(file_obj)
            if url:
                b.attachments = (b.attachments or '') + ',' + url

    db.session.commit()

    email_ctx = {'reqId': req_id, 'name': b.name, 'email': b.email,
                 'title': b.event_title, 'stage': b.stage_name, 'grade': b.grade_name,
                 'section': b.section_name,
                 'periodLabel': period.label_ar or f'الحصة {period.number}',
                 'date': booking_date, 'startTime': b.start_time, 'endTime': b.end_time}
    try:
        send_update(email_ctx)
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)
    try:
        contacts = [{'email': e} for e in get_all_contact_emails(b.stage_id)]
        send_staff_notification('update', email_ctx, contacts)
    except Exception as e:
        print(f"[email] notification failed: {e}", flush=True)

    return jsonify({'success': True})


# ── Teacher name autocomplete (for the booking form) ──────────────────────
@public_bp.route('/api/teacher-names')
def api_teacher_names():
    stage_id = request.args.get('stageId')
    q = Teacher.query
    if stage_id:
        q = q.filter_by(stage_id=stage_id)
    names = [t.name for t in q.order_by(Teacher.name).all()]
    return jsonify(names)


@public_bp.route('/api/teacher-lookup')
def api_teacher_lookup():
    """Used by the booking form: when a name matches a teacher on the
    roster, auto-fill their assigned stage/grade/section, if any."""
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({'found': False})
    teacher = Teacher.query.filter(Teacher.name.ilike(name)).first()
    if not teacher:
        return jsonify({'found': False})
    return jsonify({
        'found': True,
        'stageId': teacher.stage_id,
        'gradeId': teacher.grade_id,
        'sectionId': teacher.section_id,
    })


# ── Laptop handover / checkout form (per approved booking) ────────────────
LAPTOP_COUNT = 25

@public_bp.route('/checkout/<req_id>')
def checkout_form(req_id):
    lang = request.args.get('lang', 'ar')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        msg = 'رقم الحجز غير موجود' if lang == 'ar' else 'Booking number not found'
        return render_template('error.html', msg=msg, lang=lang)
    if b.status not in ('approved', 'completed'):
        msg = ('نموذج تسليم الأجهزة متاح فقط بعد اعتماد الحجز من الإدارة' if lang == 'ar'
               else 'The device handover form is only available after the booking is approved')
        return render_template('error.html', msg=msg, lang=lang)

    students = (Student.query.filter_by(section_id=b.section_id)
                .order_by(Student.name).all())

    existing = BookingCheckout.query.filter_by(booking_id=b.id).first()
    existing_map = {}
    if existing:
        for line in existing.lines:
            existing_map[line.student_id] = line.laptop_number

    return render_template('checkout.html', b=b, students=students, lang=lang,
                           existing_map=existing_map, laptop_count=LAPTOP_COUNT)


@public_bp.route('/api/submit-checkout', methods=['POST'])
def api_submit_checkout():
    data = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    entries = data.get('entries', [])  # [{studentId, laptopNumber}]

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'الحجز غير موجود'}), 404
    if b.status not in ('approved', 'completed'):
        return jsonify({'success': False, 'error': 'الحجز غير معتمد بعد'}), 400

    # Validate laptop numbers: within range, and no duplicate assignment
    seen_numbers = {}
    clean_entries = []
    for e in entries:
        try:
            student_id = int(e.get('studentId'))
        except (TypeError, ValueError):
            continue
        laptop_number = e.get('laptopNumber')
        if laptop_number in (None, '', 'null'):
            clean_entries.append((student_id, None))
            continue
        try:
            laptop_number = int(laptop_number)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'رقم جهاز غير صحيح'}), 400
        if not (1 <= laptop_number <= LAPTOP_COUNT):
            return jsonify({'success': False, 'error': f'رقم الجهاز يجب أن يكون بين 1 و {LAPTOP_COUNT}'}), 400
        if laptop_number in seen_numbers:
            return jsonify({'success': False,
                            'error': f'رقم الجهاز {laptop_number} مستخدم لأكثر من طالب'}), 400
        seen_numbers[laptop_number] = student_id
        clean_entries.append((student_id, laptop_number))

    checkout = BookingCheckout.query.filter_by(booking_id=b.id).first()
    if checkout:
        CheckoutLine.query.filter_by(checkout_id=checkout.id).delete()
    else:
        checkout = BookingCheckout(booking_id=b.id)
        db.session.add(checkout)
        db.session.flush()
    checkout.submitted_at = datetime.utcnow()

    students_map = {s.id: s.name for s in Student.query.filter(
        Student.id.in_([sid for sid, _ in clean_entries])).all()}

    for i, (student_id, laptop_number) in enumerate(clean_entries, start=1):
        db.session.add(CheckoutLine(
            checkout_id=checkout.id, seq=i, student_id=student_id,
            student_name=students_map.get(student_id, ''),
            laptop_number=laptop_number,
        ))

    db.session.commit()
    return jsonify({'success': True})


# ── Available periods for a given stage + date (avoid failed submissions) ──
@public_bp.route('/api/available-periods')
def api_available_periods():
    stage_id = request.args.get('stageId')
    booking_date = request.args.get('date', '')

    if not stage_id or not booking_date:
        return jsonify({'error': 'بيانات ناقصة'}), 400

    stage = Stage.query.get(stage_id)
    if not stage:
        return jsonify({'error': 'المرحلة غير موجودة'}), 400

    periods = Period.query.filter_by(active=True).order_by(Period.number).all()

    booked_numbers = {
        b.period_number for b in Booking.query.filter(
            Booking.trolley_code == stage.trolley_code,
            Booking.booking_date == booking_date,
            Booking.status.notin_(['rejected', 'cancelled'])
        ).all()
    }

    result = []
    for p in periods:
        blk = check_blocked(booking_date, p.start_time or '', p.end_time or '', stage.trolley_code)
        available = (p.number not in booked_numbers) and not blk['blocked']
        result.append({
            'id': p.id, 'number': p.number,
            'label': p.label_ar or f'الحصة {p.number}',
            'startTime': p.start_time or '', 'endTime': p.end_time or '',
            'available': available,
        })

    return jsonify(result)


# ── One-click approve/reject from the staff notification email ────────────
# No admin login required — secured by a per-booking random token instead,
# so an admin can act straight from their inbox without opening the panel.
def _booking_action_ctx(b, **extra):
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


def _action_result_page(title_ar, title_en, msg_ar, msg_en, color='#27ae60'):
    return f"""<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_ar}</title>
<style>
  body{{font-family:Tajawal,Arial,sans-serif;background:#eef3f5;margin:0;min-height:100vh;
       display:flex;align-items:center;justify-content:center;padding:20px}}
  .box{{background:#fff;border-radius:16px;padding:36px 28px;text-align:center;max-width:420px;
       box-shadow:0 8px 30px rgba(0,0,0,.1)}}
  h1{{font-size:1.2rem;color:{color};margin-bottom:10px}}
  p{{color:#555;font-size:.92rem;line-height:1.7}}
  .en{{color:#888;font-size:.82rem;margin-top:6px;direction:ltr}}
</style></head><body>
<div class="box">
  <h1>{title_ar}</h1>
  <p>{msg_ar}</p>
  <p class="en">{msg_en}</p>
</div></body></html>"""


def _action_error_page(msg_ar, msg_en):
    return _action_result_page('تعذّر تنفيذ الإجراء', 'Action Failed', msg_ar, msg_en, '#c0392b')


@public_bp.route('/action/approve/<req_id>')
def quick_approve(req_id):
    token = request.args.get('token', '')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b or not b.action_token or token != b.action_token:
        return _action_error_page('رابط غير صالح أو منتهي الصلاحية.', 'Invalid or expired link.'), 403

    if b.status != 'pending':
        return _action_result_page(
            'تم التعامل مع هذا الحجز مسبقاً', 'Already Handled',
            f'حالة الحجز الحالية: {b.status}. لا حاجة لأي إجراء إضافي.',
            f'Current status: {b.status}. No further action needed.', '#247680')

    base_url = current_app.config.get('BASE_URL', '')
    checkout_url = f"{base_url}/checkout/{b.req_id}" if base_url else ''

    b.status = 'approved'
    b.action_date = datetime.utcnow()
    db.session.commit()

    try:
        send_staff_notification('approve', _booking_action_ctx(b), [{'email': e} for e in get_all_contact_emails(b.stage_id)])
    except Exception as e:
        print(f"[email] quick-approve staff notification failed: {e}", flush=True)
    try:
        send_approve(_booking_action_ctx(b, checkoutUrl=checkout_url))
    except Exception as e:
        print(f"[email] quick-approve teacher email failed: {e}", flush=True)

    return _action_result_page(
        'تم اعتماد الحجز ✓', 'Booking Approved ✓',
        f'تم اعتماد الحجز رقم {b.req_id} بنجاح، وتم إشعار {b.name}.',
        f'Booking #{b.req_id} has been approved, and {b.name} has been notified.')


@public_bp.route('/action/reject/<req_id>', methods=['GET', 'POST'])
def quick_reject(req_id):
    token = request.args.get('token', '')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b or not b.action_token or token != b.action_token:
        return _action_error_page('رابط غير صالح أو منتهي الصلاحية.', 'Invalid or expired link.'), 403

    if b.status != 'pending':
        return _action_result_page(
            'تم التعامل مع هذا الحجز مسبقاً', 'Already Handled',
            f'حالة الحجز الحالية: {b.status}. لا حاجة لأي إجراء إضافي.',
            f'Current status: {b.status}. No further action needed.', '#247680')

    if request.method == 'GET':
        return f"""<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>رفض الحجز</title>
<style>
  body{{font-family:Tajawal,Arial,sans-serif;background:#eef3f5;margin:0;min-height:100vh;
       display:flex;align-items:center;justify-content:center;padding:20px}}
  .box{{background:#fff;border-radius:16px;padding:30px 26px;max-width:440px;width:100%;
       box-shadow:0 8px 30px rgba(0,0,0,.1)}}
  h1{{font-size:1.1rem;color:#c0392b;margin-bottom:14px}}
  p{{color:#555;font-size:.88rem;margin-bottom:16px;line-height:1.6}}
  textarea{{width:100%;min-height:90px;padding:10px;border:1.5px solid #cfe0e3;border-radius:9px;
           font-family:inherit;font-size:.9rem;box-sizing:border-box;margin-bottom:14px}}
  button{{background:#c0392b;color:#fff;border:none;padding:11px 26px;border-radius:9px;
         font-family:inherit;font-size:.9rem;font-weight:700;cursor:pointer;width:100%}}
</style></head><body>
<div class="box">
  <h1>رفض الحجز #{b.req_id}</h1>
  <p>يرجى كتابة سبب الرفض ليُرسَل للمعلم/ة <strong>{b.name}</strong>.<br>
     <span style="direction:ltr;display:inline-block;color:#888;font-size:.8rem">Please enter a rejection reason to send to the teacher.</span></p>
  <form method="POST">
    <textarea name="reason" required placeholder="سبب الرفض..."></textarea>
    <button type="submit">تأكيد الرفض</button>
  </form>
</div></body></html>"""

    reason = (request.form.get('reason') or '').strip()
    if not reason:
        return _action_error_page('سبب الرفض مطلوب.', 'A rejection reason is required.')

    b.status = 'rejected'
    b.reject_reason = reason
    b.action_date = datetime.utcnow()
    db.session.commit()

    try:
        send_staff_notification('reject', _booking_action_ctx(b, reason=reason), [{'email': e} for e in get_all_contact_emails(b.stage_id)])
    except Exception as e:
        print(f"[email] quick-reject staff notification failed: {e}", flush=True)
    try:
        send_reject(_booking_action_ctx(b, reason=reason))
    except Exception as e:
        print(f"[email] quick-reject teacher email failed: {e}", flush=True)

    return _action_result_page(
        'تم رفض الحجز', 'Booking Rejected',
        f'تم رفض الحجز رقم {b.req_id} وإشعار {b.name} بالسبب.',
        f'Booking #{b.req_id} has been rejected, and {b.name} has been notified.', '#c0392b')
