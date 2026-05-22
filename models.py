from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()


class Booking(db.Model):
    __tablename__ = 'bookings'
    id            = db.Column(db.Integer, primary_key=True)
    req_id        = db.Column(db.String(32), unique=True, nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    name          = db.Column(db.String(200), nullable=False)
    email         = db.Column(db.String(200), nullable=False)
    phone         = db.Column(db.String(50))
    on_behalf     = db.Column(db.String(200))
    event_title   = db.Column(db.String(300), nullable=False)
    hall          = db.Column(db.String(200), nullable=False)
    booking_date  = db.Column(db.String(10), nullable=False)   # yyyy-MM-dd
    end_date      = db.Column(db.String(10))                   # yyyy-MM-dd
    start_time    = db.Column(db.String(5))
    end_time      = db.Column(db.String(5))
    full_day      = db.Column(db.Boolean, default=False)
    notes         = db.Column(db.Text)
    attachments   = db.Column(db.Text)   # comma-separated URLs
    status        = db.Column(db.String(20), default='pending')  # pending/approved/rejected/cancelled
    reject_reason = db.Column(db.Text)
    cc_emails     = db.Column(db.Text)   # semicolon-separated
    action_date   = db.Column(db.DateTime)

    def to_dict(self):
        end = self.end_date or self.booking_date
        att = [a for a in (self.attachments or '').split(',') if a and '[DEL]' not in a]
        return {
            'id': self.id,
            'reqId': self.req_id,
            'createdAt': self.created_at.isoformat() if self.created_at else '',
            'name': self.name,
            'email': self.email,
            'phone': self.phone or '',
            'behalf': self.on_behalf or '',
            'title': self.event_title,
            'hall': self.hall,
            'date': self.booking_date,
            'endDate': end,
            'startTime': self.start_time or '',
            'endTime': self.end_time or '',
            'fullDay': self.full_day,
            'notes': self.notes or '',
            'att': att,
            'status': self.status,
            'rejectReason': self.reject_reason or '',
            'cc': self.cc_emails or '',
            'multiDay': end != self.booking_date,
        }


class Hall(db.Model):
    __tablename__ = 'halls'
    id                = db.Column(db.Integer, primary_key=True)
    name_ar           = db.Column(db.String(200), nullable=False)
    name_en           = db.Column(db.String(200))
    location          = db.Column(db.String(200))
    code              = db.Column(db.String(50))
    capacity          = db.Column(db.String(20))
    equipment         = db.Column(db.Text)
    description       = db.Column(db.Text)
    notes             = db.Column(db.Text)
    requires_approval = db.Column(db.Boolean, default=False)
    active            = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nameAr': self.name_ar,
            'nameEn': self.name_en or self.name_ar,
            'location': self.location or '',
            'code': self.code or '',
            'capacity': self.capacity or '',
            'equipment': self.equipment or '',
            'description': self.description or '',
            'notes': self.notes or '',
            'requiresApproval': self.requires_approval,
            'active': self.active,
        }


class BlockedPeriod(db.Model):
    __tablename__ = 'blocked_periods'
    id         = db.Column(db.Integer, primary_key=True)
    from_date  = db.Column(db.String(10), nullable=False)
    to_date    = db.Column(db.String(10), nullable=False)
    hall       = db.Column(db.String(200))   # empty = all halls
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
    """Create tables and seed default halls if empty."""
    db.create_all()
    if Hall.query.count() == 0:
        db.session.add_all([
            Hall(name_ar='القاعة الرئيسية', name_en='Main Hall',
                 location='المبنى الرئيسي', code='H001', capacity='100',
                 equipment='جهاز عرض، سبورة ذكية', active=True),
            Hall(name_ar='قاعة الاجتماعات', name_en='Meeting Room',
                 location='المبنى الرئيسي', code='H002', capacity='30',
                 equipment='شاشة، ميكروفون', active=True),
            Hall(name_ar='قاعة متعددة الأغراض', name_en='Multi-Purpose Hall',
                 location='المبنى الفرعي', code='H003', capacity='80',
                 equipment='نظام صوت، إضاءة مسرحية', active=True),
        ])
        db.session.commit()
