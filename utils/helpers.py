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


def resolve_stage_grade_section_by_name(stage_name='', grade_name='', section_name=''):
    """Look up Stage/Grade/Section by their Arabic or English display name
    (case-insensitive, trimmed) — used for bulk CSV imports where the file
    names things instead of using internal IDs. Returns (stage, grade,
    section, error) where error is None on success, or a short message
    naming exactly what wasn't found."""
    from models import Stage, Grade, Section

    stage_name = (stage_name or '').strip()
    grade_name = (grade_name or '').strip()
    section_name = (section_name or '').strip()

    stage = grade = section = None

    if stage_name:
        stage = Stage.query.filter(
            db_ilike(Stage.name_ar, stage_name) | db_ilike(Stage.name_en, stage_name)
        ).first()
        if not stage:
            return None, None, None, f'المرحلة غير موجودة: {stage_name}'

    if grade_name:
        q = Grade.query.filter(
            db_ilike(Grade.name_ar, grade_name) | db_ilike(Grade.name_en, grade_name)
        )
        if stage:
            q = q.filter(Grade.stage_id == stage.id)
        grade = q.first()
        if not grade:
            return None, None, None, f'الصف غير موجود: {grade_name}'
        if not stage:
            stage = Stage.query.get(grade.stage_id)

    if section_name:
        q = Section.query.filter(
            db_ilike(Section.name_ar, section_name) | db_ilike(Section.name_en, section_name)
        )
        if grade:
            q = q.filter(Section.grade_id == grade.id)
        section = q.first()
        if not section:
            return None, None, None, f'الشعبة غير موجودة: {section_name}'
        if not grade:
            grade = Grade.query.get(section.grade_id)
        if not stage:
            stage = Stage.query.get(grade.stage_id)

    return stage, grade, section, None


def db_ilike(column, value):
    """Case-insensitive exact match helper (works the same on SQLite and
    Postgres, unlike raw ilike() which SQLite doesn't support natively)."""
    from sqlalchemy import func
    return func.lower(column) == value.lower()
