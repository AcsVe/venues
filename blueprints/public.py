import os
from datetime import date, datetime
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, send_from_directory, current_app)
from models import db, Booking, Hall, BlockedPeriod
from utils.helpers import (gen_req_id, check_conflict, check_blocked,
                            save_upload, get_all_contact_emails,
                            get_blocked_for_date, is_valid_email, sanitize_email)
from utils.email_utils import send_confirm, send_cancel, send_update

public_bp = Blueprint('public', __name__)


# ── Serve uploaded files ──────────────────────────────────────────────────
@public_bp.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


# ── Calendar ──────────────────────────────────────────────────────────────
@public_bp.route('/')
def index():
    return redirect(url_for('public.calendar'))


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
        Booking.booking_date < month_end,
        db.or_(Booking.end_date >= month_start,
               Booking.booking_date >= month_start)
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
            # advance date
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
        end = b.end_date or b.booking_date
        public_bookings.append({
            'date': b.booking_date,
            'endDate': end,
            'hall': b.hall,
            'startTime': b.start_time or '',
            'endTime': b.end_time or '',
            'fullDay': b.full_day,
            'status': b.status,
            'multiDay': end != b.booking_date,
        })
    return jsonify({
        'bookings': public_bookings,
        'blocked': blocked_map,
    })


# ── Halls list (public) ───────────────────────────────────────────────────
@public_bp.route('/api/halls')
def api_halls():
    halls = Hall.query.filter_by(active=True).all()
    return jsonify([h.to_dict() for h in halls])


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
            msg = f'هذا اليوم غير متاح بالكامل: {blk["reason"]}'
            return render_template('error.html', msg=msg, lang=lang)

    halls = Hall.query.filter_by(active=True).all()
    return render_template('book.html', lang=lang, pre_date=pre_date,
                           halls=halls, today=today)


@public_bp.route('/api/check-slot', methods=['POST'])
def check_slot():
    data = request.get_json(silent=True) or {}
    hall      = data.get('hall', '')
    bdate     = data.get('date', '')
    end_date  = data.get('endDate') or bdate
    start     = data.get('startTime', '')
    end       = data.get('endTime', '')
    full_day  = bool(data.get('fullDay'))
    exclude   = data.get('excludeReqId')

    if not hall or not bdate:
        return jsonify({'ok': False, 'error': 'بيانات ناقصة'})

    blk = check_blocked(bdate, start, end, hall)
    if blk['blocked']:
        msg = f'التاريخ غير متاح: {blk["reason"]}'
        if blk.get('blkFromT'):
            msg += f' ({blk["blkFromT"]} - {blk["blkToT"]})'
        return jsonify({'ok': False, 'error': msg})

    conflict = check_conflict(hall, bdate, end_date, start, end, full_day, exclude)
    if conflict:
        return jsonify({'ok': False, 'error': conflict})

    return jsonify({'ok': True})


@public_bp.route('/api/submit-booking', methods=['POST'])
def submit_booking():
    today = date.today().strftime('%Y-%m-%d')

    # Handle multipart / urlencoded (with files) or JSON
    if request.content_type and ('multipart' in request.content_type or
                                  'form' in request.content_type):
        f = request.form
        files = request.files.getlist('attachments')
    else:
        f = request.get_json(silent=True) or {}
        files = []

    required = ['fullName', 'email', 'eventTitle', 'hall', 'bookingDate']
    for field in required:
        if not f.get(field):
            return jsonify({'success': False, 'error': f'الحقل {field} مطلوب'}), 400

    booking_date = f.get('bookingDate')
    end_date     = f.get('endDate') or booking_date
    start_time   = f.get('startTime', '')
    end_time     = f.get('endTime', '')
    full_day     = f.get('fullDay') in (True, 'true', '1', 'on')
    hall         = f.get('hall')

    if booking_date < today:
        return jsonify({'success': False, 'error': 'لا يمكن الحجز بتاريخ سابق'}), 400

    blk = check_blocked(booking_date, start_time, end_time, hall)
    if blk['blocked']:
        msg = f'التاريخ غير متاح: {blk["reason"]}'
        if blk.get('blkFromT'):
            msg += f' ({blk["blkFromT"]} - {blk["blkToT"]})'
        return jsonify({'success': False, 'error': msg}), 400

    conflict = check_conflict(hall, booking_date, end_date, start_time, end_time, full_day)
    if conflict:
        return jsonify({'success': False, 'error': conflict}), 400

    # Save attachments
    att_urls = []
    for file_obj in files:
        if file_obj and file_obj.filename:
            url = save_upload(file_obj)
            if url:
                att_urls.append(url)

    req_id = gen_req_id()
    booking = Booking(
        req_id       = req_id,
        name         = f.get('fullName'),
        email        = sanitize_email(f.get('email')),
        phone        = f.get('phone', ''),
        on_behalf    = f.get('onBehalf', ''),
        event_title  = f.get('eventTitle'),
        hall         = hall,
        booking_date = booking_date,
        end_date     = end_date,
        start_time   = start_time,
        end_time     = end_time,
        full_day     = full_day,
        notes        = f.get('notes', ''),
        attachments  = ','.join(att_urls),
        status       = 'pending',
    )
    db.session.add(booking)
    db.session.commit()

    try:
        send_confirm({
            'reqId': req_id, 'name': booking.name, 'email': booking.email,
            'title': booking.event_title, 'hall': booking.hall,
            'date': booking_date, 'endDate': end_date,
            'startTime': start_time, 'endTime': end_time, 'fullDay': full_day,
        })
    except Exception:
        pass

    return jsonify({'success': True, 'reqId': req_id})


# ── Booking lookup / cancel / amend (public) ─────────────────────────────
@public_bp.route('/lookup')
def lookup():
    lang = request.args.get('lang', 'ar')
    halls = Hall.query.filter_by(active=True).all()
    return render_template('lookup.html', lang=lang, halls=halls)


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

    b.status      = 'cancelled'
    b.action_date = datetime.utcnow()
    db.session.commit()

    try:
        send_cancel({'reqId': req_id, 'name': b.name, 'email': b.email,
                     'title': b.event_title, 'hall': b.hall})
    except Exception:
        pass

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

    hall         = f.get('hall', b.hall)
    booking_date = f.get('bookingDate', b.booking_date)
    end_date     = f.get('endDate') or booking_date
    start_time   = f.get('startTime', b.start_time or '')
    end_time     = f.get('endTime', b.end_time or '')
    full_day     = f.get('fullDay') in (True, 'true', '1', 'on')

    conflict = check_conflict(hall, booking_date, end_date, start_time, end_time, full_day, req_id)
    if conflict:
        return jsonify({'success': False, 'error': conflict}), 400

    was_approved = b.status == 'approved'
    b.name         = f.get('fullName', b.name)
    b.phone        = f.get('phone', b.phone or '')
    b.on_behalf    = f.get('onBehalf', b.on_behalf or '')
    b.event_title  = f.get('eventTitle', b.event_title)
    b.hall         = hall
    b.booking_date = booking_date
    b.end_date     = end_date
    b.start_time   = start_time
    b.end_time     = end_time
    b.full_day     = full_day
    b.notes        = f.get('notes', b.notes or '')
    b.action_date  = datetime.utcnow()
    if was_approved:
        b.status = 'pending'

    for file_obj in files:
        if file_obj and file_obj.filename:
            url = save_upload(file_obj)
            if url:
                b.attachments = (b.attachments or '') + ',' + url

    db.session.commit()

    try:
        send_update({'reqId': req_id, 'name': b.name, 'email': b.email,
                     'title': b.event_title, 'hall': b.hall,
                     'date': booking_date, 'endDate': end_date,
                     'startTime': start_time, 'endTime': end_time, 'fullDay': full_day})
    except Exception:
        pass

    return jsonify({'success': True})
