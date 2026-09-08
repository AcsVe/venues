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


def check_conflict(trolley_code, booking_date, period_number, exclude_req_id=None):
    """A trolley can only be used by one class at a time: same trolley + same
    date + same period = conflict. Returns an error string, or None."""
    from models import Booking
    q = Booking.query.filter(
        Booking.trolley_code == trolley_code,
        Booking.booking_date == booking_date,
        Booking.period_number == period_number,
        Booking.status.notin_(['rejected', 'cancelled'])
    )
    if exclude_req_id:
        q = q.filter(Booking.req_id != exclude_req_id)

    existing = q.first()
    if existing:
        return (f'العربة محجوزة مسبقاً في هذه الحصة بتاريخ {booking_date} '
                f'({existing.stage_name or ""} - {existing.grade_name or ""} {existing.section_name or ""})')
    return None


def check_blocked(booking_date, start_time, end_time, trolley_code):
    """Returns {'blocked': bool, 'reason': str, 'fullBlock': bool}.
    trolley_code may be '' to mean 'applies to all trolleys'."""
    from models import BlockedPeriod
    blocks = BlockedPeriod.query.all()
    for blk in blocks:
        if booking_date < blk.from_date or booking_date > blk.to_date:
            continue
        if blk.hall and trolley_code and blk.hall != trolley_code:
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


def get_all_contact_emails(stage_id=None):
    """Contacts assigned to a specific stage are notified only for that
    stage's bookings; contacts with no stage assigned are notified for all."""
    from models import Contact
    q = Contact.query
    if stage_id is not None:
        q = q.filter((Contact.stage_id == stage_id) | (Contact.stage_id.is_(None)))
    return [c.email for c in q.all() if is_valid_email(c.email)]


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
