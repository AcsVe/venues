"""Background job: nudge teachers who haven't filled the device-handover
form a short while after their approved booking's period has ended."""
from datetime import datetime, timedelta, date, timezone


def _period_end_datetime(booking_date_str, end_time_str):
    """Combine a booking's date with its period's end time. Returns None if
    either piece is missing (caller then falls back to a day-based check)."""
    if not end_time_str:
        return None
    try:
        y, m, d = (int(x) for x in booking_date_str.split('-'))
        hh, mm = (int(x) for x in end_time_str.split(':'))
        return datetime(y, m, d, hh, mm)
    except (ValueError, TypeError):
        return None


def check_and_send_reminders(app):
    """Runs periodically. For every approved booking whose period ended a
    while ago, with no checkout submitted and no reminder sent yet, send one
    reminder email and mark it as sent (never repeats for the same booking)."""
    with app.app_context():
        from models import Booking, BookingCheckout
        from utils.email_utils import send_checkout_reminder
        from flask import current_app

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        today_str = date.today().strftime('%Y-%m-%d')
        base_url = current_app.config.get('BASE_URL', '')

        candidates = Booking.query.filter(
            Booking.status == 'approved',
            Booking.checkout_reminder_sent.is_(False),
            Booking.booking_date <= today_str,
        ).all()

        sent_count = 0
        for b in candidates:
            # Skip if a handover was already submitted for this booking.
            if BookingCheckout.query.filter_by(booking_id=b.id).first():
                continue

            end_dt = _period_end_datetime(b.booking_date, b.end_time)
            if end_dt is not None:
                eligible = now >= end_dt + timedelta(hours=1)
            else:
                # No period end time on record — only safe to assume the
                # period is over once its date has fully passed.
                eligible = b.booking_date < today_str

            if not eligible:
                continue

            checkout_url = f"{base_url}/checkout/{b.req_id}" if base_url else ''
            ctx = {
                'reqId': b.req_id, 'name': b.name, 'email': b.email,
                'title': b.event_title, 'stage': b.stage_name, 'grade': b.grade_name,
                'section': b.section_name,
                'periodLabel': f'الحصة {b.period_number}' if b.period_number else '',
                'date': b.booking_date, 'startTime': b.start_time, 'endTime': b.end_time,
                'checkoutUrl': checkout_url,
            }
            try:
                send_checkout_reminder(ctx)
            except Exception as e:
                print(f"[email] checkout reminder failed for {b.req_id}: {e}", flush=True)

            # Mark as sent regardless of email success — this is a one-shot
            # nudge, not a retry loop, to avoid ever spamming a teacher.
            b.checkout_reminder_sent = True
            sent_count += 1

        if sent_count:
            from models import db
            db.session.commit()
            print(f"[reminders] sent {sent_count} checkout reminder(s)", flush=True)
