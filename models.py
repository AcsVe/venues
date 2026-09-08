from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()


class Teacher(db.Model):
    """Roster of teachers, optionally linked to a stage — used as a reference
    list / autocomplete source for the booking form (item 2 of the request)."""
    __tablename__ = 'teachers'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(200), nullable=False)
    email      = db.Column(db.String(200))
    phone      = db.Column(db.String(50))
    stage_id   = db.Column(db.Integer, db.ForeignKey('stages.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'email': self.email or '',
            'phone': self.phone or '', 'stageId': self.stage_id,
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


class BookingCheckout(db.Model):
    """One laptop-handover manifest per booking, filled by the teacher after
    the booking is approved. Submitting is not conditioned on filling every
    row — a teacher can leave absent students blank."""
    __tablename__ = 'booking_checkouts'
    id           = db.Column(db.Integer, primary_key=True)
    booking_id   = db.Column(db.Integer, db.ForeignKey('bookings.id'), unique=True, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    status        = db.Column(db.String(20), default='pending')  # pending/approved/rejected/cancelled
    reject_reason = db.Column(db.Text)
    cc_emails     = db.Column(db.Text)   # semicolon-separated
    action_date   = db.Column(db.DateTime)

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
    id         = db.Column(db.Integer, primary_key=True)
    email      = db.Column(db.String(200), unique=True, nullable=False)
    name       = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name or '',
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
