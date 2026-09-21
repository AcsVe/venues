from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()


class Teacher(db.Model):
    """Roster of teachers. stage/grade/section are all optional and
    independently settable — a teacher can be tied to a whole stage, a
    specific grade, or one exact section. Used both as a reference/contact
    list and to auto-fill the booking form when a matching name is entered."""
    __tablename__ = 'teachers'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(200), nullable=False)
    email      = db.Column(db.String(200))
    phone      = db.Column(db.String(50))
    stage_id   = db.Column(db.Integer, db.ForeignKey('stages.id'))
    grade_id   = db.Column(db.Integer, db.ForeignKey('grades.id'))
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'email': self.email or '',
            'phone': self.phone or '', 'stageId': self.stage_id,
            'gradeId': self.grade_id, 'sectionId': self.section_id,
        }


class Student(db.Model):
    """Roster of students, organized by stage/grade/section — used as the
    prefilled source list for the laptop-checkout form (item 3/4)."""
    __tablename__ = 'students'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(200), nullable=False)
    stage_id   = db.Column(db.Integer, db.ForeignKey('stages.id'), nullable=False)
    grade_id   = db.Column(db.Integer, db.ForeignKey('grades.id'), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'stageId': self.stage_id,
            'gradeId': self.grade_id, 'sectionId': self.section_id,
        }


class PushSubscription(db.Model):
    """A browser's Web Push subscription (from the admin's installed PWA),
    so the server can send a real push notification — one that arrives
    even if the app/browser is fully closed — when a new booking comes in."""
    __tablename__ = 'push_subscriptions'
    id         = db.Column(db.Integer, primary_key=True)
    endpoint   = db.Column(db.Text, nullable=False, unique=True)
    p256dh     = db.Column(db.String(255), nullable=False)
    auth       = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_push_dict(self):
        return {'endpoint': self.endpoint, 'keys': {'p256dh': self.p256dh, 'auth': self.auth}}


class BookingReminder(db.Model):
    """A one-off reminder email scheduled for an exact date/time. `kind`
    keeps the admin's personal follow-up completely independent from a
    teacher's own reminder about their booking — one has nothing to do
    with the other, and setting one never touches or replaces the other."""
    __tablename__ = 'booking_reminders'
    id              = db.Column(db.Integer, primary_key=True)
    booking_id      = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    kind            = db.Column(db.String(10), nullable=False, default='admin')  # 'admin' or 'teacher'
    remind_at       = db.Column(db.DateTime, nullable=False)
    recipient_email = db.Column(db.String(200), nullable=False)
    note            = db.Column(db.Text)
    sent            = db.Column(db.Boolean, default=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'bookingId': self.booking_id, 'kind': self.kind,
            'remindAt': self.remind_at.strftime('%Y-%m-%dT%H:%M') if self.remind_at else '',
            'recipientEmail': self.recipient_email or '',
            'note': self.note or '', 'sent': self.sent,
        }


class BookingCheckout(db.Model):
    """One laptop-handover manifest per booking, filled by the teacher after
    the booking is approved. Submitting is not conditioned on filling every
    row — a teacher can leave absent students blank."""
    __tablename__ = 'booking_checkouts'
    id           = db.Column(db.Integer, primary_key=True)
    booking_id   = db.Column(db.Integer, db.ForeignKey('bookings.id'), unique=True, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes        = db.Column(db.Text)  # optional teacher remarks about the devices/trolley

    lines = db.relationship('CheckoutLine', backref='checkout', cascade='all, delete-orphan',
                             order_by='CheckoutLine.seq')

    def to_dict(self):
        return {
            'id': self.id, 'bookingId': self.booking_id,
            'submittedAt': self.submitted_at.isoformat() if self.submitted_at else '',
            'lines': [l.to_dict() for l in self.lines],
        }


class CheckoutLine(db.Model):
    __tablename__ = 'checkout_lines'
    id             = db.Column(db.Integer, primary_key=True)
    checkout_id    = db.Column(db.Integer, db.ForeignKey('booking_checkouts.id'), nullable=False)
    seq            = db.Column(db.Integer, nullable=False)
    student_id     = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    student_name   = db.Column(db.String(200))   # snapshot, survives roster edits/deletes
    laptop_number  = db.Column(db.Integer)        # 1..25, nullable = not handed out

    def to_dict(self):
        return {
            'seq': self.seq, 'studentId': self.student_id,
            'studentName': self.student_name, 'laptopNumber': self.laptop_number,
        }


class Booking(db.Model):
    __tablename__ = 'bookings'
    id            = db.Column(db.Integer, primary_key=True)
    req_id        = db.Column(db.String(32), unique=True, nullable=False)
    action_token  = db.Column(db.String(43))  # secures one-click approve/reject links in staff emails
    receipt_printed = db.Column(db.Boolean, default=False)  # for the auto-print-on-approval station
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    name          = db.Column(db.String(200), nullable=False)
    email         = db.Column(db.String(200), nullable=False)
    phone         = db.Column(db.String(50))
    on_behalf     = db.Column(db.String(200))
    event_title   = db.Column(db.String(300))
    booking_date  = db.Column(db.String(10), nullable=False)   # yyyy-MM-dd

    # ── Academic-structure fields (stage/grade/section/period) ─────────────
    stage_id       = db.Column(db.Integer, db.ForeignKey('stages.id'))
    grade_id       = db.Column(db.Integer, db.ForeignKey('grades.id'))
    section_id     = db.Column(db.Integer, db.ForeignKey('sections.id'))
    period_id      = db.Column(db.Integer, db.ForeignKey('periods.id'))
    trolley_code   = db.Column(db.String(50))    # snapshot of the trolley identifier at booking time
    stage_name     = db.Column(db.String(200))   # snapshot labels (survive later edits/deletes)
    grade_name     = db.Column(db.String(100))
    section_name   = db.Column(db.String(100))
    period_number  = db.Column(db.Integer)
    start_time     = db.Column(db.String(5))     # derived from the period, kept for display/reports
    end_time       = db.Column(db.String(5))

    # ── Legacy hall-booking fields (kept only so old records keep displaying) ──
    hall          = db.Column(db.String(200))
    end_date      = db.Column(db.String(10))
    full_day      = db.Column(db.Boolean, default=False)

    notes         = db.Column(db.Text)
    attachments   = db.Column(db.Text)   # comma-separated URLs
    status        = db.Column(db.String(20), default='pending')  # pending/approved/completed/rejected/cancelled
    checkout_reminder_sent = db.Column(db.Boolean, default=False)
    reject_reason = db.Column(db.Text)
    cc_emails     = db.Column(db.Text)   # semicolon-separated
    action_date   = db.Column(db.DateTime)
    approved_by   = db.Column(db.String(200))  # name of whoever approved the booking

    def to_dict(self):
        return {
            'id': self.id,
            'reqId': self.req_id,
            'createdAt': self.created_at.isoformat() if self.created_at else '',
            'name': self.name,
            'email': self.email,
            'phone': self.phone or '',
            'behalf': self.on_behalf or '',
            'title': self.event_title or '',
            'stageId': self.stage_id,
            'gradeId': self.grade_id,
            'sectionId': self.section_id,
            'periodId': self.period_id,
            'trolleyCode': self.trolley_code or '',
            'stage': self.stage_name or self.hall or '',
            'grade': self.grade_name or '',
            'section': self.section_name or '',
            'periodNumber': self.period_number,
            'date': self.booking_date,
            'startTime': self.start_time or '',
            'endTime': self.end_time or '',
            'notes': self.notes or '',
            'att': [a for a in (self.attachments or '').split(',') if a and '[DEL]' not in a],
            'status': self.status,
            'rejectReason': self.reject_reason or '',
            'approvedBy': self.approved_by or '',
            'cc': self.cc_emails or '',
        }


class Stage(db.Model):
    """A study stage (e.g. أساسي / ثانوي). Each stage owns exactly one laptop
    trolley, identified by a unique trolley_code — this is the physical
    resource being booked."""
    __tablename__ = 'stages'
    id           = db.Column(db.Integer, primary_key=True)
    name_ar      = db.Column(db.String(200), nullable=False)
    name_en      = db.Column(db.String(200))
    trolley_code = db.Column(db.String(50), unique=True, nullable=False)
    active       = db.Column(db.Boolean, default=True)
    sort_order   = db.Column(db.Integer, default=0)

    grades = db.relationship('Grade', backref='stage', cascade='all, delete-orphan',
                              order_by='Grade.sort_order')

    def to_dict(self, with_grades=False):
        d = {
            'id': self.id,
            'nameAr': self.name_ar,
            'nameEn': self.name_en or self.name_ar,
            'trolleyCode': self.trolley_code,
            'active': self.active,
        }
        if with_grades:
            d['grades'] = [g.to_dict(with_sections=True) for g in self.grades]
        return d


class Grade(db.Model):
    __tablename__ = 'grades'
    id         = db.Column(db.Integer, primary_key=True)
    stage_id   = db.Column(db.Integer, db.ForeignKey('stages.id'), nullable=False)
    name_ar    = db.Column(db.String(100), nullable=False)
    name_en    = db.Column(db.String(100))
    sort_order = db.Column(db.Integer, default=0)

    sections = db.relationship('Section', backref='grade', cascade='all, delete-orphan',
                                order_by='Section.sort_order')

    def to_dict(self, with_sections=False):
        d = {
            'id': self.id,
            'stageId': self.stage_id,
            'nameAr': self.name_ar,
            'nameEn': self.name_en or self.name_ar,
        }
        if with_sections:
            d['sections'] = [s.to_dict() for s in self.sections]
        return d


class Section(db.Model):
    __tablename__ = 'sections'
    id         = db.Column(db.Integer, primary_key=True)
    grade_id   = db.Column(db.Integer, db.ForeignKey('grades.id'), nullable=False)
    name_ar    = db.Column(db.String(100), nullable=False)
    name_en    = db.Column(db.String(100))
    sort_order = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'gradeId': self.grade_id,
            'nameAr': self.name_ar,
            'nameEn': self.name_en or self.name_ar,
        }


class Period(db.Model):
    """One of the 8 daily class periods, shared by both stages."""
    __tablename__ = 'periods'
    id         = db.Column(db.Integer, primary_key=True)
    number     = db.Column(db.Integer, nullable=False, unique=True)  # 1..8
    label_ar   = db.Column(db.String(100))
    start_time = db.Column(db.String(5))
    end_time   = db.Column(db.String(5))
    active     = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'number': self.number,
            'label': self.label_ar or f'الحصة {self.number}',
            'startTime': self.start_time or '',
            'endTime': self.end_time or '',
            'active': self.active,
        }


class GradePeriodTime(db.Model):
    """Per-grade, per-weekday override of a period's start/end time. Grades
    within the same stage can run different bell schedules on different
    days (e.g. a shorter Tuesday, or a different timetable for grades 7-9
    vs 10-12) — this table lets each (grade, weekday, period) combination
    carry its own time, while `Period.start_time/end_time` stays as the
    shared fallback for any grade/day that has no override here.
    weekday follows Python's date.weekday(): Monday=0 .. Sunday=6."""
    __tablename__ = 'grade_period_times'
    id            = db.Column(db.Integer, primary_key=True)
    grade_id      = db.Column(db.Integer, db.ForeignKey('grades.id'), nullable=False)
    weekday       = db.Column(db.Integer, nullable=False)
    period_number = db.Column(db.Integer, nullable=False)
    start_time    = db.Column(db.String(5), nullable=False)
    end_time      = db.Column(db.String(5), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('grade_id', 'weekday', 'period_number', name='uq_grade_period_time'),
    )

    def to_dict(self):
        return {
            'id': self.id, 'gradeId': self.grade_id, 'weekday': self.weekday,
            'periodNumber': self.period_number,
            'startTime': self.start_time, 'endTime': self.end_time,
        }


class AppSetting(db.Model):
    """Simple key/value store for site-wide toggles (auto-approve bookings,
    etc.) — one row per setting, so new switches can be added later without
    a schema change."""
    __tablename__ = 'app_settings'
    key   = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(500))

    @staticmethod
    def get_bool(key, default=False):
        row = AppSetting.query.get(key)
        if row is None:
            return default
        return row.value == '1'

    @staticmethod
    def set_bool(key, value):
        row = AppSetting.query.get(key)
        if row is None:
            row = AppSetting(key=key, value='1' if value else '0')
            db.session.add(row)
        else:
            row.value = '1' if value else '0'
        db.session.commit()


class BlockedPeriod(db.Model):
    """A date/time range during which a stage's trolley (or all trolleys, if
    left blank) cannot be booked."""
    __tablename__ = 'blocked_periods'
    id         = db.Column(db.Integer, primary_key=True)
    from_date  = db.Column(db.String(10), nullable=False)
    to_date    = db.Column(db.String(10), nullable=False)
    hall       = db.Column(db.String(200))   # stores the trolley_code; empty = all stages
    from_time  = db.Column(db.String(5))
    to_time    = db.Column(db.String(5))
    reason     = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            's': self.from_date,
            'e': self.to_date,
            'hall': self.hall or '',
            'fromT': self.from_time or '',
            'toT': self.to_time or '',
            'r': self.reason or '',
        }


class Contact(db.Model):
    __tablename__ = 'contacts'
    id              = db.Column(db.Integer, primary_key=True)
    email           = db.Column(db.String(200), nullable=False)
    name            = db.Column(db.String(200))
    stage_id        = db.Column(db.Integer, db.ForeignKey('stages.id'))  # null = all stages
    notify_new      = db.Column(db.Boolean, default=True)   # new pending booking (includes one-click approve/reject links)
    new_readonly    = db.Column(db.Boolean, default=False)  # if notify_new is also True, strips the approve/reject links for this contact — informational only
    notify_approved = db.Column(db.Boolean, default=False)  # plain notice once a booking is approved (no action links)
    notify_handover = db.Column(db.Boolean, default=False)  # auto-notified when a teacher submits a device handover for this stage
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name or '',
            'stageId': self.stage_id,
            'notifyNew': self.notify_new if self.notify_new is not None else True,
            'newReadonly': self.new_readonly or False,
            'notifyApproved': self.notify_approved or False,
            'notifyHandover': self.notify_handover or False,
            'date': self.created_at.strftime('%Y-%m-%d') if self.created_at else '',
        }


def init_db(app):
    """Create tables and seed default academic structure if empty."""
    db.create_all()

    # Migration: add new columns to existing tables if they don't exist yet
    try:
        with db.engine.connect() as conn:
            for col, coltype in [
                ('stage_id', 'INTEGER'), ('grade_id', 'INTEGER'),
                ('section_id', 'INTEGER'), ('period_id', 'INTEGER'),
                ('trolley_code', 'VARCHAR(50)'), ('stage_name', 'VARCHAR(200)'),
                ('grade_name', 'VARCHAR(100)'), ('section_name', 'VARCHAR(100)'),
                ('period_number', 'INTEGER'),
            ]:
                conn.exec_driver_sql(f"ALTER TABLE bookings ADD COLUMN IF NOT EXISTS {col} {coltype}")
            conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS stage_id INTEGER")
            conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS checkout_reminder_sent BOOLEAN DEFAULT FALSE")
            conn.exec_driver_sql("ALTER TABLE booking_reminders ADD COLUMN IF NOT EXISTS recipient_email VARCHAR(200)")
            conn.exec_driver_sql("ALTER TABLE booking_reminders ADD COLUMN IF NOT EXISTS kind VARCHAR(10) DEFAULT 'admin'")
            conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS action_token VARCHAR(43)")
            conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS receipt_printed BOOLEAN DEFAULT FALSE")
            conn.exec_driver_sql("ALTER TABLE booking_checkouts ADD COLUMN IF NOT EXISTS notes TEXT")
            conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS notify_handover BOOLEAN DEFAULT FALSE")
            conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS notify_new BOOLEAN DEFAULT TRUE")
            conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS new_readonly BOOLEAN DEFAULT FALSE")
            conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS notify_approved BOOLEAN DEFAULT FALSE")
            conn.exec_driver_sql("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS grade_id INTEGER")
            conn.exec_driver_sql("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS section_id INTEGER")
            conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS approved_by VARCHAR(200)")
            # Contacts can now repeat the same email across different stages —
            # drop the old single-column unique constraint if present.
            try:
                conn.exec_driver_sql("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_email_key")
            except Exception:
                pass
            conn.commit()
    except Exception:
        pass

    if Stage.query.count() == 0:
        basic = Stage(name_ar='المرحلة الأساسية', name_en='Primary Stage',
                       trolley_code='TROLLEY-A', active=True, sort_order=1)
        secondary = Stage(name_ar='المرحلة الثانوية', name_en='Secondary Stage',
                           trolley_code='TROLLEY-B', active=True, sort_order=2)
        db.session.add_all([basic, secondary])
        db.session.flush()

        for i, num in enumerate([5, 6]):
            db.session.add(Grade(stage_id=basic.id, name_ar=f'الصف {_ordinal_ar_m(num)}',
                                  name_en=f'Grade {num}', sort_order=i))
        for i, num in enumerate(range(7, 13)):
            db.session.add(Grade(stage_id=secondary.id, name_ar=f'الصف {_ordinal_ar_m(num)}',
                                  name_en=f'Grade {num}', sort_order=i))
        db.session.commit()

        # One default section (أ) per grade — admin can add more from the panel
        for g in Grade.query.all():
            db.session.add(Section(grade_id=g.id, name_ar='أ', name_en='A', sort_order=0))
        db.session.commit()

    if Period.query.count() == 0:
        for n in range(1, 9):
            db.session.add(Period(number=n, label_ar=f'الحصة {_ordinal_ar(n)}', active=True))
        db.session.commit()

    _seed_grade_period_times()


_ORDINALS_AR_F = {  # feminine — used for الحصة (period)
    1: 'الأولى', 2: 'الثانية', 3: 'الثالثة', 4: 'الرابعة', 5: 'الخامسة',
    6: 'السادسة', 7: 'السابعة', 8: 'الثامنة', 9: 'التاسعة', 10: 'العاشرة',
    11: 'الحادية عشرة', 12: 'الثانية عشرة',
}
_ORDINALS_AR_M = {  # masculine — used for الصف (grade)
    1: 'الأول', 2: 'الثاني', 3: 'الثالث', 4: 'الرابع', 5: 'الخامس',
    6: 'السادس', 7: 'السابع', 8: 'الثامن', 9: 'التاسع', 10: 'العاشر',
    11: 'الحادي عشر', 12: 'الثاني عشر',
}

def _ordinal_ar(n):
    return _ORDINALS_AR_F.get(n, str(n))

def _ordinal_ar_m(n):
    return _ORDINALS_AR_M.get(n, str(n))


# ── One-time seed for the 2026/2027 grade 7-12 bell schedule ───────────────
# Regular days = Sunday, Monday, Wednesday, Thursday. Tuesday runs a shorter
# schedule. Friday/Saturday are not school days, so they carry no rows.
# Python weekday(): Monday=0, Tuesday=1, Wednesday=2, Thursday=3, ..., Sunday=6.
_REGULAR_WEEKDAYS = [6, 0, 2, 3]   # Sun, Mon, Wed, Thu
_TUESDAY_WEEKDAY  = [1]

_GRADE_710_SCHEDULE = {
    # grade numbers 7, 8, 9 share one timetable; 10, 11, 12 share another.
    (7, 8, 9): {
        'regular': {1: ('08:00', '08:45'), 2: ('08:45', '09:30'), 3: ('09:30', '10:15'),
                    4: ('10:35', '11:20'), 5: ('11:20', '12:05'), 6: ('12:05', '12:50'),
                    7: ('13:05', '13:50'), 8: ('13:50', '14:35')},
        'tuesday': {1: ('08:00', '08:35'), 2: ('08:35', '09:10'), 3: ('09:10', '09:45'),
                    4: ('10:00', '10:35'), 5: ('10:35', '11:10'), 6: ('11:10', '11:45'),
                    7: ('12:00', '12:35'), 8: ('12:35', '13:20')},
    },
    (10, 11, 12): {
        'regular': {1: ('08:00', '08:45'), 2: ('08:45', '09:30'), 3: ('09:30', '10:15'),
                    4: ('10:15', '11:00'), 5: ('11:40', '12:25'), 6: ('12:25', '13:10'),
                    7: ('13:10', '13:50'), 8: ('13:50', '14:35')},
        'tuesday': {1: ('08:00', '08:35'), 2: ('08:35', '09:10'), 3: ('09:10', '09:45'),
                    4: ('09:45', '10:20'), 5: ('10:50', '11:25'), 6: ('11:25', '12:00'),
                    7: ('12:00', '12:35'), 8: ('12:35', '13:20')},
    },
}


def _grade_number(g):
    """Best-effort extraction of the numeric grade (7, 8, 9...) from a Grade
    row, so the seed can match real grades however they were labeled."""
    import re
    for label in (g.name_en, g.name_ar):
        if not label:
            continue
        m = re.search(r'\d+', label)
        if m:
            return int(m.group())
    for num, word in _ORDINALS_AR_M.items():
        if g.name_ar and word in g.name_ar:
            return num
    return None


def _seed_grade_period_times():
    """Seed the grade 7-12 bell schedule once, matching existing Grade rows
    by their numeric label. If the expected grades 7-12 can't all be
    identified (e.g. they were renamed), seeding is skipped entirely rather
    than guessing — an admin can fill the schedule in from the panel."""
    if GradePeriodTime.query.count() > 0:
        return

    grades_by_number = {}
    for g in Grade.query.all():
        n = _grade_number(g)
        if n is not None and n not in grades_by_number:
            grades_by_number[n] = g

    for numbers, schedule in _GRADE_710_SCHEDULE.items():
        if not all(n in grades_by_number for n in numbers):
            continue  # can't confidently match this group — leave for manual entry
        for n in numbers:
            grade = grades_by_number[n]
            for weekday in _REGULAR_WEEKDAYS:
                for period_number, (start, end) in schedule['regular'].items():
                    db.session.add(GradePeriodTime(
                        grade_id=grade.id, weekday=weekday, period_number=period_number,
                        start_time=start, end_time=end))
            for weekday in _TUESDAY_WEEKDAY:
                for period_number, (start, end) in schedule['tuesday'].items():
                    db.session.add(GradePeriodTime(
                        grade_id=grade.id, weekday=weekday, period_number=period_number,
                        start_time=start, end_time=end))
    db.session.commit()
