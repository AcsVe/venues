from datetime import datetime, date
from functools import wraps
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, url_for, current_app)
from models import db, Booking, Hall, BlockedPeriod, Contact
from utils.helpers import (is_valid_email, sanitize_email, save_upload,
                            get_all_contact_emails, check_conflict, check_blocked)
from utils.email_utils import (send_approve, send_reject, send_cancel,
                                send_pending, send_update, send_staff_notification)

admin_bp = Blueprint('admin', __name__)

def _get_contacts():
    from models import Contact
    return [{'email': c.email} for c in Contact.query.all()]


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
    if request.method == 'POST':
        u = request.form.get('username', '')
        p = request.form.get('password', '')
        if (u == current_app.config['ADMIN_USER'] and
                p == current_app.config['ADMIN_PASS']):
            session['admin_logged_in'] = True
            return redirect(url_for('admin.dashboard'))
        error = 'بيانات خاطئة'
    return render_template('admin/login.html', error=error)


@admin_bp.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin.login'))


@admin_bp.route('/')
@login_required
def dashboard():
    return render_template('admin/dashboard.html')


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
    week_start = date.today().strftime('%Y-%m-%d')  # simplified

    total   = Booking.query.count()
    pending = Booking.query.filter_by(status='pending').count()
    approved= Booking.query.filter_by(status='approved').count()
    rejected= Booking.query.filter_by(status='rejected').count()
    cancelled=Booking.query.filter_by(status='cancelled').count()
    today_c = Booking.query.filter_by(booking_date=today).count()
    halls_c = Hall.query.filter_by(active=True).count()

    return jsonify({
        'total': total, 'pending': pending, 'approved': approved,
        'rejected': rejected, 'cancelled': cancelled,
        'today': today_c, 'halls': halls_c,
    })


@admin_bp.route('/api/approve', methods=['POST'])
@login_required
def api_approve():
    data   = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    extra  = data.get('extraBcc', '')

    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404

    b.status      = 'approved'
    b.action_date = datetime.utcnow()

    # Auto-calculate invoice amount
    if b.invoice_amount is None:
        hall = Hall.query.filter_by(name_ar=b.hall).first()
        if hall:
            try:
                from datetime import datetime as dt
                # Multi-day
                if b.end_date and b.end_date != b.booking_date:
                    d1 = dt.strptime(b.booking_date, '%Y-%m-%d')
                    d2 = dt.strptime(b.end_date, '%Y-%m-%d')
                    days = (d2 - d1).days + 1
                    if hall.price_multi_day:
                        b.invoice_amount = round(hall.price_multi_day * days, 2)
                # Full day
                elif b.full_day:
                    if hall.price_full_day:
                        b.invoice_amount = round(hall.price_full_day, 2)
                # Hourly
                elif b.start_time and b.end_time:
                    sh, sm = map(int, b.start_time.split(':'))
                    eh, em = map(int, b.end_time.split(':'))
                    hours = round((eh * 60 + em - sh * 60 - sm) / 60, 2)
                    if hall.price_per_hour:
                        b.invoice_amount = round(hall.price_per_hour * hours, 2)
            except Exception:
                pass

    db.session.commit()

    end = b.end_date or b.booking_date
    try:
        send_staff_notification('approve', {'reqId': req_id, 'name': b.name, 'email': b.email, 'title': b.event_title, 'hall': b.hall, 'date': b.booking_date, 'endDate': b.end_date, 'startTime': b.start_time, 'endTime': b.end_time, 'fullDay': b.full_day}, _get_contacts())
        send_approve({'reqId': req_id, 'name': b.name, 'email': b.email,
                      'title': b.event_title, 'hall': b.hall,
                      'date': b.booking_date, 'endDate': end,
                      'startTime': b.start_time, 'endTime': b.end_time,
                      'fullDay': b.full_day,
                      'invoiceAmount': b.invoice_amount,
                      'invoiceNotes': b.invoice_notes or ''})
    except Exception:
        pass

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
        send_staff_notification('reject', {'reqId': req_id, 'name': b.name, 'email': b.email, 'title': b.event_title, 'hall': b.hall, 'date': b.booking_date, 'reason': data.get('reason','')}, _get_contacts())
        send_reject({'reqId': req_id, 'name': b.name, 'email': b.email,
                     'title': b.event_title, 'reason': reason})
    except Exception:
        pass

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
        send_staff_notification('cancel', {'reqId': req_id, 'name': b.name, 'email': b.email, 'title': b.event_title, 'hall': b.hall, 'date': b.booking_date}, _get_contacts())
        send_cancel({'reqId': req_id, 'name': b.name, 'email': b.email,
                     'title': b.event_title, 'hall': b.hall})
    except Exception:
        pass

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
        send_staff_notification('revert', {'reqId': req_id, 'name': b.name, 'email': b.email, 'title': b.event_title, 'hall': b.hall, 'date': b.booking_date}, _get_contacts())
        send_pending({'reqId': req_id, 'name': b.name, 'email': b.email,
                      'title': b.event_title, 'hall': b.hall,
                      'date': b.booking_date, 'endDate': b.end_date or b.booking_date})
    except Exception:
        pass

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

    hall         = f.get('hall', b.hall)
    booking_date = f.get('bookingDate', b.booking_date)
    end_date     = f.get('endDate') or booking_date
    start_time   = f.get('startTime', b.start_time or '')
    end_time     = f.get('endTime', b.end_time or '')
    full_day     = f.get('fullDay') in (True, 'true', '1', 'on')

    b.name         = f.get('name', b.name)
    b.email        = sanitize_email(f.get('email', b.email))
    b.phone        = f.get('phone', b.phone or '')
    b.on_behalf    = f.get('behalf', b.on_behalf or '')
    b.event_title  = f.get('title', b.event_title)
    b.hall         = hall
    b.booking_date = booking_date
    b.end_date     = end_date
    b.start_time   = start_time
    b.end_time     = end_time
    b.full_day     = full_day
    b.notes        = f.get('notes', b.notes or '')
    b.action_date  = datetime.utcnow()

    for file_obj in files:
        if file_obj and file_obj.filename:
            url = save_upload(file_obj)
            if url:
                b.attachments = (b.attachments or '') + ',' + url

    db.session.commit()

    try:
        send_staff_notification('update', {'reqId': req_id, 'name': b.name, 'email': b.email, 'title': b.event_title, 'hall': b.hall, 'date': b.booking_date}, _get_contacts())
        send_update({'reqId': req_id, 'name': b.name, 'email': b.email,
                     'title': b.event_title, 'hall': b.hall,
                     'date': booking_date, 'endDate': end_date,
                     'startTime': start_time, 'endTime': end_time,
                     'fullDay': full_day})
    except Exception:
        pass

    return jsonify({'success': True})


@admin_bp.route('/api/delete-booking', methods=['POST'])
@login_required
def api_delete_booking():
    data   = request.get_json(silent=True) or {}
    req_id = data.get('reqId', '')
    b = Booking.query.filter_by(req_id=req_id).first()
    if not b:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    db.session.delete(b)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/bulk-delete', methods=['POST'])
@login_required
def api_bulk_delete():
    data = request.get_json(silent=True) or {}
    ids  = data.get('ids', [])
    if not ids:
        return jsonify({'success': False, 'error': 'لا توجد حجوزات محددة'}), 400
    Booking.query.filter(Booking.req_id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'success': True, 'count': len(ids)})


# ── Halls API ─────────────────────────────────────────────────────────────
@admin_bp.route('/api/halls')
@login_required
def api_halls():
    halls = Hall.query.all()
    return jsonify([h.to_dict() for h in halls])


@admin_bp.route('/api/add-hall', methods=['POST'])
@login_required
def api_add_hall():
    data = request.get_json(silent=True) or {}
    if not data.get('nameAr'):
        return jsonify({'success': False, 'error': 'الاسم مطلوب'}), 400
    h = Hall(
        name_ar           = data['nameAr'],
        name_en           = data.get('nameEn', ''),
        location          = data.get('location', ''),
        code              = data.get('code', ''),
        capacity          = str(data.get('capacity', '')),
        equipment         = data.get('equipment', ''),
        description       = data.get('description', ''),
        notes             = data.get('notes', ''),
        requires_approval = bool(data.get('requiresApproval')),
        active            = data.get('active', True) is not False,
        price_per_hour    = float(data['pricePerHour']) if data.get('pricePerHour') else None,
        price_full_day    = float(data['priceFullDay']) if data.get('priceFullDay') else None,
        price_multi_day   = float(data['priceMultiDay']) if data.get('priceMultiDay') else None,
        price_notes       = data.get('priceNotes', ''),
    )
    db.session.add(h)
    db.session.commit()
    return jsonify({'success': True, 'id': h.id})


@admin_bp.route('/api/update-hall', methods=['POST'])
@login_required
def api_update_hall():
    data = request.get_json(silent=True) or {}
    h = Hall.query.get(data.get('id'))
    if not h:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    h.name_ar           = data.get('nameAr', h.name_ar)
    h.name_en           = data.get('nameEn', h.name_en or '')
    h.location          = data.get('location', h.location or '')
    h.code              = data.get('code', h.code or '')
    h.capacity          = str(data.get('capacity', h.capacity or ''))
    h.equipment         = data.get('equipment', h.equipment or '')
    h.description       = data.get('description', h.description or '')
    h.notes             = data.get('notes', h.notes or '')
    h.requires_approval = bool(data.get('requiresApproval'))
    h.active            = data.get('active', True) is not False
    if data.get('pricePerHour')  is not None: h.price_per_hour  = float(data['pricePerHour'])  if data['pricePerHour']  else None
    if data.get('priceFullDay')  is not None: h.price_full_day  = float(data['priceFullDay'])  if data['priceFullDay']  else None
    if data.get('priceMultiDay') is not None: h.price_multi_day = float(data['priceMultiDay']) if data['priceMultiDay'] else None
    if data.get('priceNotes')    is not None: h.price_notes     = data['priceNotes']
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/api/delete-hall', methods=['POST'])
@login_required
def api_delete_hall():
    data = request.get_json(silent=True) or {}
    h = Hall.query.get(data.get('id'))
    if not h:
        return jsonify({'success': False, 'error': 'غير موجود'}), 404
    if Hall.query.filter_by(active=True).count() <= 1:
        return jsonify({'success': False, 'error': 'لا يمكن حذف آخر قاعة'}), 400
    db.session.delete(h)
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
        if is_valid_email(em):
            if not Contact.query.filter_by(email=em).first():
                db.session.add(Contact(email=em, name=item.get('name', '')))
                added += 1
    db.session.commit()
    return jsonify({'success': True, 'count': added})


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
