import re
import os
import uuid
from datetime import date, datetime
from werkzeug.utils import secure_filename
from flask import current_app


ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'pdf',
                      'doc', 'docx', 'xls', 'xlsx', 'zip', 'rar', 'txt', 'csv'}


def is_valid_email(email):
    if not email:
        return False
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email.strip()))


def sanitize_email(email):
    if not email:
        return ''
    return re.sub(r'[\x00-\x1F\x7F-\x9F\u200B-\u200D\uFEFF\u00A0]', '', str(email).strip())


def gen_req_id():
    import uuid
    return 'BK-' + uuid.uuid4().hex[:10].upper()


def check_conflict(hall, booking_date, end_date, start_time, end_time, full_day, exclude_req_id=None):
    """Returns error string if conflict found, else None."""
    from models import Booking
    end = end_date or booking_date
    q = Booking.query.filter(
        Booking.hall == hall,
        Booking.status.notin_(['rejected', 'cancelled'])
    )
    if exclude_req_id:
        q = q.filter(Booking.req_id != exclude_req_id)

    for b in q.all():
        b_end = b.end_date or b.booking_date
        # date range overlap?
        if booking_date > b_end or end < b.booking_date:
            continue
        # full-day conflicts
        if b.full_day:
            return f'القاعة محجوزة يوم كامل في هذا التاريخ: {b.event_title}'
        if full_day:
            return f'لا يمكن الحجز يوم كامل، يوجد حجز في هذا التاريخ: {b.event_title}'
        # time overlap
        if start_time and end_time and b.start_time and b.end_time:
            if start_time < b.end_time and end_time > b.start_time:
                return f'القاعة محجوزة في هذا الوقت ({b.start_time} - {b.end_time}) بتاريخ {b.booking_date}'
    return None


def check_blocked(booking_date, start_time, end_time, hall_name):
    """Returns {'blocked': bool, 'reason': str, 'fullBlock': bool}."""
    from models import BlockedPeriod
    blocks = BlockedPeriod.query.all()
    for blk in blocks:
        if booking_date < blk.from_date or booking_date > blk.to_date:
            continue
        if blk.hall and hall_name and blk.hall != hall_name:
            continue
        if not blk.from_time or not blk.to_time:
            return {'blocked': True, 'reason': blk.reason or 'فترة غير متاحة', 'fullBlock': True}
        if not start_time or not end_time:
            return {'blocked': True, 'reason': blk.reason or 'فترة غير متاحة',
                    'blkFromT': blk.from_time, 'blkToT': blk.to_time}
        if start_time < blk.to_time and end_time > blk.from_time:
            return {'blocked': True, 'reason': blk.reason or 'فترة غير متاحة',
                    'blkFromT': blk.from_time, 'blkToT': blk.to_time}
    return {'blocked': False}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file_obj):
    """Save uploaded file and return its URL path."""
    if not file_obj or not allowed_file(file_obj.filename):
        return None
    ext = file_obj.filename.rsplit('.', 1)[1].lower()
    fname = secure_filename(f'{uuid.uuid4().hex}.{ext}')
    upload_dir = current_app.config['UPLOAD_FOLDER']
    file_obj.save(os.path.join(upload_dir, fname))
    return f'/uploads/{fname}'


def get_all_contact_emails():
    from models import Contact
    return [c.email for c in Contact.query.all() if is_valid_email(c.email)]


def get_blocked_for_date(booking_date):
    """Return list of blocked info dicts for a given date."""
    from models import BlockedPeriod
    result = []
    for blk in BlockedPeriod.query.all():
        if booking_date < blk.from_date or booking_date > blk.to_date:
            continue
        result.append({
            'reason': blk.reason or 'غير متاح',
            'fromTime': blk.from_time or '',
            'toTime': blk.to_time or '',
            'hall': blk.hall or '',
        })
    return result
