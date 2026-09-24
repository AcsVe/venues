"""Background job: nudge teachers who haven't filled the device-handover
form a short while after their approved booking's period has ended."""
from datetime import datetime, timedelta, date, timezone


def _combine_date_time(booking_date_str, time_str):
    """Combine a booking's date with a time string (its period's start or
    end time). Returns None if either piece is missing (caller then falls
    back to a day-based check)."""
    if not time_str:
        return None
    try:
        y, m, d = (int(x) for x in booking_date_str.split('-'))
        hh, mm = (int(x) for x in time_str.split(':'))
        return datetime(y, m, d, hh, mm)
    except (ValueError, TypeError):
        return None


# Kept as an alias — older call sites in this module use this name.
_period_end_datetime = _combine_date_time


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


def check_and_send_daily_staff_summary(app):
    """Runs periodically. Once a day, at or after the admin-configured time,
    emails everyone opted in to the staff reminder list a summary of which
    stages/trolleys have at least one approved booking today. Sends at most
    once per calendar day (tracked in app_settings), and only if enabled."""
    with app.app_context():
        from models import AppSetting, Booking
        from utils.helpers import get_staff_reminder_emails, weekday_name
        from utils.email_utils import send_daily_staff_summary

        if not AppSetting.get_bool('staff_daily_summary_enabled', False):
            return

        jordan_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)
        today_str = jordan_now.strftime('%Y-%m-%d')
        send_at = AppSetting.get_str('staff_daily_summary_time', '07:00')
        try:
            hh, mm = (int(x) for x in send_at.split(':'))
        except ValueError:
            hh, mm = 7, 0

        if (jordan_now.hour, jordan_now.minute) < (hh, mm):
            return
        if AppSetting.get_str('staff_daily_summary_last_date', '') == today_str:
            return  # already sent today

        emails = get_staff_reminder_emails()
        if not emails:
            AppSetting.set_str('staff_daily_summary_last_date', today_str)
            return

        bookings_today = Booking.query.filter(
            Booking.booking_date == today_str,
            Booking.status.in_(['approved', 'completed']),
        ).all()

        counts = {}
        for b in bookings_today:
            key = b.stage_name or ''
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1

        if counts:
            stage_lines = [{'stage': stage, 'count': n} for stage, n in counts.items()]
            try:
                send_daily_staff_summary(emails, weekday_name(today_str, 'ar'), today_str, stage_lines)
            except Exception as e:
                print(f"[email] daily staff summary failed: {e}", flush=True)

        # Mark as sent for today either way — a quiet day shouldn't retry
        # every 10 minutes and there is nothing to report on it anyway.
        AppSetting.set_str('staff_daily_summary_last_date', today_str)


def check_and_send_upcoming_period_reminders(app):
    """Runs periodically. For each of today's approved bookings, sends
    staff-reminder recipients a heads-up once the booking's period is due
    to start within the admin-configured lead time — a one-shot nudge,
    never repeated for the same booking."""
    with app.app_context():
        from models import db, AppSetting, Booking
        from utils.helpers import get_staff_reminder_emails
        from utils.email_utils import send_upcoming_booking_reminder

        lead_raw = AppSetting.get_str('staff_reminder_lead_minutes', '')
        try:
            lead_minutes = int(lead_raw)
        except ValueError:
            return  # feature is off until the admin sets a lead time
        if lead_minutes <= 0:
            return

        jordan_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)
        today_str = jordan_now.strftime('%Y-%m-%d')

        candidates = Booking.query.filter(
            Booking.booking_date == today_str,
            Booking.status.in_(['approved', 'completed']),
            Booking.staff_reminder_sent.is_(False),
        ).all()

        if not candidates:
            return

        emails = get_staff_reminder_emails()
        if not emails:
            return

        sent_count = 0
        for b in candidates:
            start_dt = _combine_date_time(b.booking_date, b.start_time)
            if start_dt is None:
                continue
            remind_at = start_dt - timedelta(minutes=lead_minutes)
            if jordan_now < remind_at:
                continue  # too early still

            ctx = {
                'reqId': b.req_id, 'name': b.name, 'title': b.event_title,
                'stage': b.stage_name, 'grade': b.grade_name, 'section': b.section_name,
                'periodLabel': f'الحصة {b.period_number}' if b.period_number else '',
                'date': b.booking_date, 'startTime': b.start_time, 'endTime': b.end_time,
            }
            try:
                send_upcoming_booking_reminder(emails, ctx, lead_minutes)
            except Exception as e:
                print(f"[email] upcoming booking reminder failed for {b.req_id}: {e}", flush=True)

            b.staff_reminder_sent = True
            sent_count += 1

        if sent_count:
            db.session.commit()
            print(f"[reminders] sent {sent_count} upcoming-booking staff reminder(s)", flush=True)


def check_and_send_scheduled_reminders(app):
    """Runs periodically. Sends any admin-scheduled reminder (exact date +
    time, set via the booking detail modal) once its time has arrived.
    Compared in Jordan local time (UTC+3, no DST) — the same wall-clock
    time the admin picked in the date/time field."""
    with app.app_context():
        from models import db, Booking, BookingReminder
        from utils.email_utils import send_scheduled_reminder

        jordan_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)

        due = BookingReminder.query.filter(
            BookingReminder.sent.is_(False),
            BookingReminder.remind_at <= jordan_now,
        ).all()

        sent_count = 0
        for reminder in due:
            b = Booking.query.get(reminder.booking_id)
            if b and reminder.recipient_email:
                ctx = {
                    'reqId': b.req_id, 'name': b.name, 'email': reminder.recipient_email,
                    'title': b.event_title, 'stage': b.stage_name, 'grade': b.grade_name,
                    'section': b.section_name,
                    'periodLabel': f'الحصة {b.period_number}' if b.period_number else '',
                    'date': b.booking_date, 'startTime': b.start_time, 'endTime': b.end_time,
                    'note': reminder.note or '',
                }
                try:
                    send_scheduled_reminder(ctx)
                except Exception as e:
                    print(f"[email] scheduled reminder failed for {b.req_id}: {e}", flush=True)
            reminder.sent = True
            sent_count += 1

        if sent_count:
            db.session.commit()
            print(f"[reminders] sent {sent_count} scheduled reminder(s)", flush=True)
