import time
import requests
from flask import current_app

# Simple in-memory access-token cache (per process). Avoids requesting a
# fresh token from Azure AD on every single email send.
_ms_token_cache = {'token': None, 'expires_at': 0}


def _get_ms_access_token():
    tenant_id     = current_app.config.get('MS_TENANT_ID', '')
    client_id     = current_app.config.get('MS_CLIENT_ID', '')
    client_secret = current_app.config.get('MS_CLIENT_SECRET', '')
    if not (tenant_id and client_id and client_secret):
        print('[email] MS Graph not configured: missing tenant_id/client_id/client_secret', flush=True)
        return None

    now = time.time()
    if _ms_token_cache['token'] and now < _ms_token_cache['expires_at'] - 60:
        return _ms_token_cache['token']

    try:
        r = requests.post(
            f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'scope': 'https://graph.microsoft.com/.default',
                'grant_type': 'client_credentials',
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f'[email] MS token request failed: HTTP {r.status_code} — {r.text[:500]}', flush=True)
            return None
        data = r.json()
        token = data.get('access_token')
        if not token:
            print(f'[email] MS token response had no access_token: {r.text[:500]}', flush=True)
            return None
        _ms_token_cache['token'] = token
        _ms_token_cache['expires_at'] = now + int(data.get('expires_in', 3600))
        return token
    except Exception as e:
        print(f'[email] MS token request raised exception: {e}', flush=True)
        return None


def _send_via_graph(to_email, to_name, subject, html_body, bcc_list=None):
    token = _get_ms_access_token()
    if not token:
        return False

    sender_email = current_app.config.get('MS_SENDER_EMAIL', '')
    if not sender_email:
        print('[email] MS_SENDER_EMAIL is not set', flush=True)
        return False

    message = {
        'subject': subject,
        'body': {'contentType': 'HTML', 'content': html_body},
        'toRecipients': [{'emailAddress': {'address': to_email, 'name': to_name or to_email}}],
    }
    if bcc_list:
        unique_bcc = list({e.lower(): e for e in bcc_list if e and '@' in e}.values())
        if unique_bcc:
            message['bccRecipients'] = [{'emailAddress': {'address': e}} for e in unique_bcc[:50]]

    try:
        r = requests.post(
            f'https://graph.microsoft.com/v1.0/users/{sender_email}/sendMail',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'message': message, 'saveToSentItems': True},
            timeout=15,
        )
        if r.status_code not in (200, 201, 202):
            print(f'[email] Graph sendMail failed: HTTP {r.status_code} — {r.text[:500]} (sender={sender_email}, to={to_email})', flush=True)
            return False
        print(f'[email] sent successfully via Graph (to={to_email}, subject={subject[:60]})', flush=True)
        return True
    except Exception as e:
        print(f'[email] Graph sendMail raised exception: {e}', flush=True)
        return False


def _send(to_email, to_name, subject, html_body, bcc_list=None):
    """Send an email via Microsoft 365 (Graph API)."""
    ms_secret = current_app.config.get('MS_CLIENT_SECRET')
    if not ms_secret:
        print(f"[email] MS_CLIENT_SECRET is NOT set — cannot send (to={to_email})", flush=True)
        return False
    return _send_via_graph(to_email, to_name, subject, html_body, bcc_list)


def _abs_logo_url():
    """Email clients need an absolute URL — /static/logo.jpg alone won't load."""
    logo = current_app.config.get('LOGO_URL', '')
    if not logo:
        return ''
    if logo.startswith('http://') or logo.startswith('https://'):
        return logo
    base = current_app.config.get('BASE_URL', '')
    if not base:
        return ''  # can't build an absolute URL — better to omit than show a broken image
    return base.rstrip('/') + logo


def _date_label(date_str, start_time='', end_time=''):
    label = date_str or ''
    if start_time and end_time:
        label += f'  ({start_time} - {end_time})'
    return label


def _class_label(data):
    parts = [p for p in [data.get('stage'), data.get('grade'), data.get('section')] if p]
    return ' - '.join(parts)


def _base_html(content_ar, content_en, color='#247680'):
    """Bilingual email shell: Arabic (RTL) section on top, English (LTR)
    section below, each with correct text direction.

    Uses a table with an explicit width attribute (not CSS max-width on a
    div) because Outlook desktop's Word-based rendering engine frequently
    ignores max-width/margin:auto on divs and stretches the email to fill
    the reading pane — the table+width+align combo is the reliable
    cross-client fix."""
    logo = _abs_logo_url()
    org_ar = current_app.config.get('ORG_AR', 'مدرسة الرائد العربي')
    org_en = current_app.config.get('ORG_EN', 'Al-Raed Al-Arabi School')
    logo_html = (f'<img src="{logo}" alt="logo" width="120" style="display:block;margin:0 auto 6px;max-width:120px;height:auto">'
                 if logo else '')

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef1f2">
  <tr><td align="center" style="padding:16px 10px">
    <table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0"
           style="width:480px;max-width:480px;background:#fff;border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;font-family:Tajawal,Arial,sans-serif;font-size:13px">
      <tr><td style="background:{color};padding:12px 16px;text-align:center">
        {logo_html}
        <span style="color:#fff;font-size:13px;font-weight:700">{org_ar} — {org_en}</span>
      </td></tr>
      <tr><td dir="rtl" style="padding:14px 16px 6px;text-align:right">{content_ar}</td></tr>
      <tr><td style="padding:0 16px"><div style="border-top:1px dashed #ddd"></div></td></tr>
      <tr><td dir="ltr" style="padding:6px 16px 14px;text-align:left;font-family:Arial,sans-serif">{content_en}</td></tr>
      <tr><td style="background:#f5f5f5;padding:8px 16px;text-align:center;font-size:10px;color:#888">
        <div dir="rtl">هذا البريد مُرسَل تلقائياً من نظام حجز عربات الحواسيب — يُرجى عدم الرد عليه</div>
        <div dir="ltr">This is an automated message from the laptop trolley booking system — please do not reply</div>
      </td></tr>
    </table>
  </td></tr>
</table>"""


def _build_rows(pairs, bg):
    """Render only rows that actually have a value — a missing
    stage/period/date shouldn't leave an awkward blank row in the email."""
    out = []
    for label, value in pairs:
        if value is None or value == '':
            continue
        out.append(
            f'<tr><td style="padding:6px 8px;background:{bg};font-weight:600;width:40%">{label}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee">{value}</td></tr>'
        )
    return ''.join(out)


def _rows_ar(data, bg='#f0f7f8'):
    date_label = _date_label(data.get('date'), data.get('startTime'), data.get('endTime'))
    return _build_rows([
        ('رقم الطلب', f'<strong style="color:#247680">{data.get("reqId","")}</strong>'),
        ('سبب الحجز', data.get('title')),
        ('المرحلة / الصف / الشعبة', _class_label(data)),
        ('الحصة', data.get('periodLabel')),
        ('التاريخ والوقت', date_label),
    ], bg)


def _rows_en(data, bg='#f0f7f8'):
    date_label = _date_label(data.get('date'), data.get('startTime'), data.get('endTime'))
    return _build_rows([
        ('Request ID', f'<strong style="color:#247680">{data.get("reqId","")}</strong>'),
        ('Reason', data.get('title')),
        ('Stage / Grade / Section', _class_label(data)),
        ('Period', data.get('periodLabel')),
        ('Date & Time', date_label),
    ], bg)


def send_confirm(data):
    content_ar = f"""
    <h2 style="color:#247680;margin-top:0">✅ تم استلام طلب حجزك</h2>
    <p>شكراً <strong>{data['name']}</strong>، تم استلام طلب حجز عربة الحواسيب وهو قيد المراجعة.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_ar(data)}</table>
    <p style="color:#555">سيتم إشعارك بقرار الإدارة في أقرب وقت ممكن. احتفظ برقم الطلب للاستعلام والتعديل.</p>
    """
    content_en = f"""
    <h2 style="color:#247680;margin-top:0">✅ Booking Request Received</h2>
    <p>Thank you <strong>{data['name']}</strong>, your laptop trolley booking request has been received and is pending review.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_en(data)}</table>
    <p style="color:#555">You will be notified of the administration's decision soon. Keep your request number for lookup or edits.</p>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي / Al-Raed] تم استلام طلب الحجز #{data['reqId']}",
                 _base_html(content_ar, content_en))


def send_approve(data):
    checkout_url = data.get('checkoutUrl', '')
    checkout_ar = f"""
    <div style="background:#eef7f0;border:1.5px solid #cfead6;border-radius:10px;padding:14px 16px;margin:16px 0">
      <div style="font-weight:700;color:#1f7a44;margin-bottom:6px">📋 نموذج تسليم الأجهزة</div>
      <div style="font-size:.9rem;color:#333;margin-bottom:10px">بعد استخدام العربة، يُرجى تعبئة نموذج تسليم الأجهزة لتسجيل رقم اللابتوب الذي استلمه كل طالب.</div>
      <a href="{checkout_url}" style="display:inline-block;background:#27ae60;color:#fff;padding:9px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:.88rem">فتح نموذج تسليم الأجهزة</a>
    </div>""" if checkout_url else ''
    checkout_en = f"""
    <div style="background:#eef7f0;border:1.5px solid #cfead6;border-radius:10px;padding:14px 16px;margin:16px 0">
      <div style="font-weight:700;color:#1f7a44;margin-bottom:6px">📋 Device Handover Form</div>
      <div style="font-size:.9rem;color:#333;margin-bottom:10px">After using the trolley, please fill out the handover form to record which laptop number each student received.</div>
      <a href="{checkout_url}" style="display:inline-block;background:#27ae60;color:#fff;padding:9px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:.88rem">Open Handover Form</a>
    </div>""" if checkout_url else ''

    content_ar = f"""
    <h2 style="color:#27ae60;margin-top:0">✅ تمت الموافقة على حجزك</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، يسعدنا إخبارك بأنه تمت الموافقة على طلب حجز العربة.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_ar(data)}</table>
    {checkout_ar}
    <p style="color:#555">نتمنى لكم حصة مفيدة. في حال الحاجة لأي تعديل يُرجى التواصل معنا.</p>
    """
    content_en = f"""
    <h2 style="color:#27ae60;margin-top:0">✅ Your Booking is Approved</h2>
    <p>Dear <strong>{data['name']}</strong>, we're happy to let you know your trolley booking request has been approved.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_en(data)}</table>
    {checkout_en}
    <p style="color:#555">We hope you have a great class. Please contact us if you need any changes.</p>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي / Al-Raed] ✅ تمت الموافقة على حجزك #{data['reqId']}",
                 _base_html(content_ar, content_en, '#27ae60'),
                 bcc_list=data.get('bcc', []))


def send_reject(data):
    content_ar = f"""
    <h2 style="color:#c0392b;margin-top:0">❌ اعتذار — تعذّر اعتماد طلب الحجز</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، نأسف لإخبارك بأنه تعذّر اعتماد طلب حجزك.</p>
    <table style="width:100%;border-collapse:collapse;margin:12px 0">
      <tr><td style="padding:6px 8px;background:#fdf0f0;font-weight:600;width:40%">رقم الطلب</td><td style="padding:6px 8px;border-bottom:1px solid #eee">{data['reqId']}</td></tr>
      <tr><td style="padding:6px 8px;background:#fdf0f0;font-weight:600">سبب الحجز</td><td style="padding:6px 8px;border-bottom:1px solid #eee">{data.get('title') or '—'}</td></tr>
      <tr><td style="padding:6px 8px;background:#fdf0f0;font-weight:600">سبب الرفض</td><td style="padding:6px 8px;color:#c0392b;font-weight:600">{data.get('reason','')}</td></tr>
    </table>
    <p style="color:#555">يمكنك تقديم طلب جديد باختيار تاريخ أو حصة أخرى. نعتذر عن الإزعاج.</p>
    """
    content_en = f"""
    <h2 style="color:#c0392b;margin-top:0">❌ Booking Could Not Be Approved</h2>
    <p>Dear <strong>{data['name']}</strong>, we're sorry to let you know your booking request could not be approved.</p>
    <table style="width:100%;border-collapse:collapse;margin:12px 0">
      <tr><td style="padding:6px 8px;background:#fdf0f0;font-weight:600;width:40%">Request ID</td><td style="padding:6px 8px;border-bottom:1px solid #eee">{data['reqId']}</td></tr>
      <tr><td style="padding:6px 8px;background:#fdf0f0;font-weight:600">Reason for booking</td><td style="padding:6px 8px;border-bottom:1px solid #eee">{data.get('title') or '—'}</td></tr>
      <tr><td style="padding:6px 8px;background:#fdf0f0;font-weight:600">Rejection reason</td><td style="padding:6px 8px;color:#c0392b;font-weight:600">{data.get('reason','')}</td></tr>
    </table>
    <p style="color:#555">You're welcome to submit a new request with a different date or period. Sorry for the inconvenience.</p>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي / Al-Raed] بخصوص طلب الحجز #{data['reqId']}",
                 _base_html(content_ar, content_en, '#c0392b'))


def send_cancel(data):
    content_ar = f"""
    <h2 style="color:#e67e22;margin-top:0">🚫 تم إلغاء الحجز</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، تم إلغاء طلب الحجز التالي.</p>
    <table style="width:100%;border-collapse:collapse;margin:12px 0">{_rows_ar(data, bg='#fef9f0')}</table>
    """
    content_en = f"""
    <h2 style="color:#e67e22;margin-top:0">🚫 Booking Cancelled</h2>
    <p>Dear <strong>{data['name']}</strong>, the following booking request has been cancelled.</p>
    <table style="width:100%;border-collapse:collapse;margin:12px 0">{_rows_en(data, bg='#fef9f0')}</table>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي / Al-Raed] إلغاء الحجز #{data['reqId']}",
                 _base_html(content_ar, content_en, '#e67e22'))


def send_update(data):
    content_ar = f"""
    <h2 style="color:#247680;margin-top:0">✏️ تم تعديل بيانات الحجز</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، تم تعديل بيانات طلب حجزك.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_ar(data)}</table>
    <p style="color:#555">إذا تم التعديل على حجز معتمد، سيُعاد الحجز إلى قيد المراجعة وسيتم إخطارك بالقرار.</p>
    """
    content_en = f"""
    <h2 style="color:#247680;margin-top:0">✏️ Booking Updated</h2>
    <p>Dear <strong>{data['name']}</strong>, your booking request details have been updated.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_en(data)}</table>
    <p style="color:#555">If an approved booking was edited, it will return to pending review and you'll be notified of the decision.</p>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي / Al-Raed] تعديل الحجز #{data['reqId']}",
                 _base_html(content_ar, content_en),
                 bcc_list=data.get('bcc', []))


def send_pending(data):
    content_ar = f"""
    <h2 style="color:#e67e22;margin-top:0">🔄 إعادة الحجز لقيد المراجعة</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، تم إعادة طلب حجزك إلى قيد المراجعة.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_ar(data, bg='#fef9f0')}</table>
    """
    content_en = f"""
    <h2 style="color:#e67e22;margin-top:0">🔄 Booking Reverted to Pending</h2>
    <p>Dear <strong>{data['name']}</strong>, your booking request has been reverted to pending review.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_en(data, bg='#fef9f0')}</table>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي / Al-Raed] إعادة مراجعة طلب الحجز #{data['reqId']}",
                 _base_html(content_ar, content_en, '#e67e22'),
                 bcc_list=data.get('bcc', []))


def send_checkout_reminder(data):
    """Sent automatically a short while after an approved booking's period
    ends, if the device-handover form is still empty."""
    checkout_url = data.get('checkoutUrl', '')
    content_ar = f"""
    <h2 style="color:#e67e22;margin-top:0">📋 تذكير — نموذج تسليم الأجهزة</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، يرجى العلم أنه لم يتم تسليم سجل استخدام الطلبة للأجهزة الخاص بحجزكم التالي. يُرجى العمل على تعبئته وإرساله في أقرب وقت ممكن.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_ar(data, bg='#fef9f0')}</table>
    <div style="text-align:center;margin:16px 0">
      <a href="{checkout_url}" style="display:inline-block;background:#27ae60;color:#fff;padding:9px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:.88rem">فتح نموذج تسليم الأجهزة</a>
    </div>
    """
    content_en = f"""
    <h2 style="color:#e67e22;margin-top:0">📋 Reminder — Device Handover Form</h2>
    <p>Dear <strong>{data['name']}</strong>, please note that the student device-usage record for your booking below has not been submitted yet. Please fill it out and send it as soon as possible.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">{_rows_en(data, bg='#fef9f0')}</table>
    <div style="text-align:center;margin:16px 0">
      <a href="{checkout_url}" style="display:inline-block;background:#27ae60;color:#fff;padding:9px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:.88rem">Open Handover Form</a>
    </div>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي / Al-Raed] تذكير — نموذج تسليم الأجهزة #{data['reqId']}",
                 _base_html(content_ar, content_en, '#e67e22'))


def send_staff_notification(event_type, data, contacts):
    """Send internal notification to all staff contacts."""
    if not contacts:
        return

    labels = {
        'new':     ('📋', '#247680', 'حجز جديد بانتظار المراجعة', 'New booking pending review'),
        'approve': ('✅', '#27ae60', 'تم اعتماد حجز', 'Booking approved'),
        'reject':  ('❌', '#c0392b', 'تم رفض حجز', 'Booking rejected'),
        'cancel':  ('🚫', '#e67e22', 'تم إلغاء حجز', 'Booking cancelled'),
        'update':  ('✏️', '#247680', 'تم تعديل حجز', 'Booking updated'),
        'revert':  ('🔄', '#e67e22', 'تم إرجاع الحجز لقيد المراجعة', 'Booking reverted to pending'),
    }
    icon, color, title_ar, title_en = labels.get(event_type, ('📌', '#247680', 'إشعار حجز', 'Booking notification'))

    content_ar = f"""
    <h2 style="color:{color};margin-top:0">{icon} {title_ar}</h2>
    <table style="width:100%;border-collapse:collapse;margin:12px 0">{_build_rows([
        ('رقم الطلب', f'<strong style="color:#247680">{data.get("reqId","")}</strong>'),
        ('مقدم الطلب', data.get('name')),
        ('البريد', f'<span dir="ltr">{data.get("email","")}</span>' if data.get('email') else None),
        ('سبب الحجز', data.get('title')),
        ('المرحلة / الصف / الشعبة', _class_label(data)),
        ('الحصة', data.get('periodLabel')),
        ('التاريخ', data.get('date')),
        ('سبب الرفض', f'<span style="color:#c0392b;font-weight:600">{data.get("reason","")}</span>' if data.get('reason') else None),
    ], '#f0f7f8')}</table>
    """
    content_en = f"""
    <h2 style="color:{color};margin-top:0">{icon} {title_en}</h2>
    <table style="width:100%;border-collapse:collapse;margin:12px 0">{_build_rows([
        ('Request ID', f'<strong style="color:#247680">{data.get("reqId","")}</strong>'),
        ('Requested by', data.get('name')),
        ('Email', f'<span dir="ltr">{data.get("email","")}</span>' if data.get('email') else None),
        ('Reason for booking', data.get('title')),
        ('Stage / Grade / Section', _class_label(data)),
        ('Period', data.get('periodLabel')),
        ('Date', data.get('date')),
        ('Rejection reason', f'<span style="color:#c0392b;font-weight:600">{data.get("reason","")}</span>' if data.get('reason') else None),
    ], '#f0f7f8')}</table>
    """

    subject_map = {
        'new':     f"[إشعار/Notice] حجز جديد بانتظار المراجعة #{data.get('reqId','')}",
        'approve': f"[إشعار/Notice] تم اعتماد الحجز #{data.get('reqId','')}",
        'reject':  f"[إشعار/Notice] تم رفض الحجز #{data.get('reqId','')}",
        'cancel':  f"[إشعار/Notice] تم إلغاء الحجز #{data.get('reqId','')}",
        'revert':  f"[إشعار/Notice] تم إرجاع الحجز لقيد المراجعة #{data.get('reqId','')}",
        'update':  f"[إشعار/Notice] تم تعديل الحجز #{data.get('reqId','')}",
    }
    subject = subject_map.get(event_type, f"[إشعار/Notice] حجز #{data.get('reqId','')}")
    html = _base_html(content_ar, content_en, color)

    for contact in contacts:
        email = contact.get('email') if isinstance(contact, dict) else contact
        if email and '@' in email:
            try:
                _send(email, '', subject, html)
            except Exception as e:
                print(f"[email] staff notification send failed for {email}: {e}", flush=True)
