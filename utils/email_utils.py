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


def _send_via_brevo(to_email, to_name, subject, html_body, bcc_list=None):
    api_key = current_app.config.get('BREVO_API_KEY', '')
    if not api_key:
        return False

    sender_email = current_app.config.get('SENDER_EMAIL', '')
    sender_name  = current_app.config.get('SENDER_NAME', 'مدرسة الرائد العربي')

    payload = {
        'sender': {'name': sender_name, 'email': sender_email},
        'to': [{'email': to_email, 'name': to_name or to_email}],
        'subject': subject,
        'htmlContent': html_body,
    }
    if bcc_list:
        unique_bcc = list({e.lower(): e for e in bcc_list if e and '@' in e}.values())
        if unique_bcc:
            payload['bcc'] = [{'email': e} for e in unique_bcc[:50]]

    try:
        r = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={'api-key': api_key, 'Content-Type': 'application/json'},
            json=payload,
            timeout=15,
        )
        if r.status_code not in (200, 201, 202):
            print(f'[email] Brevo send failed: HTTP {r.status_code} — {r.text[:500]} (to={to_email})', flush=True)
            return False
        print(f'[email] sent successfully via Brevo (to={to_email}, subject={subject[:60]})', flush=True)
        return True
    except Exception:
        return False


def _send(to_email, to_name, subject, html_body, bcc_list=None):
    """Send an email. Uses Microsoft 365 (Graph API) when configured,
    otherwise falls back to Brevo."""
    if current_app.config.get('MS_CLIENT_SECRET'):
        return _send_via_graph(to_email, to_name, subject, html_body, bcc_list)
    return _send_via_brevo(to_email, to_name, subject, html_body, bcc_list)


def _date_label(date_str, start_time='', end_time=''):
    """Build human-readable date/time label in Arabic."""
    label = date_str or ''
    if start_time and end_time:
        label += f'  ({start_time} - {end_time})'
    return label


def _class_label(data):
    """Build 'stage - grade - section' label."""
    parts = [p for p in [data.get('stage'), data.get('grade'), data.get('section')] if p]
    return ' - '.join(parts)


def _base_html(content, color='#247680'):
    logo = current_app.config.get('LOGO_URL', '')
    org  = current_app.config.get('ORG_AR', 'مدرسة الرائد العربي')
    return f"""
<div style="font-family:Tajawal,Arial,sans-serif;direction:rtl;max-width:600px;margin:auto;border:1px solid #e0e0e0;border-radius:12px;overflow:hidden">
  <div style="background:{color};padding:20px 24px;text-align:center">
    {'<img src="'+logo+'" height="50" style="margin-bottom:8px"><br>' if logo else ''}
    <span style="color:#fff;font-size:20px;font-weight:700">{org}</span>
  </div>
  <div style="padding:28px 24px;background:#fff">{content}</div>
  <div style="background:#f5f5f5;padding:12px 24px;text-align:center;font-size:12px;color:#888">
    هذا البريد مُرسَل تلقائياً من نظام حجز عربات الحواسيب — يُرجى عدم الرد عليه
  </div>
</div>"""


def _booking_table_rows(data, bg='#f0f7f8'):
    date_label = _date_label(data.get('date'), data.get('startTime'), data.get('endTime'))
    rows = f"""
      <tr><td style="padding:8px;background:{bg};font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee"><strong style="color:#247680">{data.get('reqId','')}</strong></td></tr>
      <tr><td style="padding:8px;background:{bg};font-weight:600">الموضوع</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('title') or '—'}</td></tr>
      <tr><td style="padding:8px;background:{bg};font-weight:600">المرحلة / الصف / الشعبة</td><td style="padding:8px;border-bottom:1px solid #eee">{_class_label(data)}</td></tr>
      <tr><td style="padding:8px;background:{bg};font-weight:600">الحصة</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('periodLabel','')}</td></tr>
      <tr><td style="padding:8px;background:{bg};font-weight:600">التاريخ والوقت</td><td style="padding:8px">{date_label}</td></tr>
    """
    return rows


def send_confirm(data):
    content = f"""
    <h2 style="color:#247680;margin-top:0">✅ تم استلام طلب حجزك</h2>
    <p>شكراً <strong>{data['name']}</strong>، تم استلام طلب حجز عربة الحواسيب وهو قيد المراجعة.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      {_booking_table_rows(data)}
    </table>
    <p style="color:#555">سيتم إشعارك بقرار الإدارة في أقرب وقت ممكن. احتفظ برقم الطلب للاستعلام والتعديل.</p>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي] تم استلام طلب الحجز #{data['reqId']}",
                 _base_html(content))


def send_approve(data):
    checkout_url = data.get('checkoutUrl', '')
    checkout_block = f"""
    <div style="background:#eef7f0;border:1.5px solid #cfead6;border-radius:10px;padding:14px 16px;margin:16px 0">
      <div style="font-weight:700;color:#1f7a44;margin-bottom:6px">📋 نموذج تسليم الأجهزة</div>
      <div style="font-size:.9rem;color:#333;margin-bottom:10px">
        بعد استخدام العربة، يُرجى تعبئة نموذج تسليم الأجهزة لتسجيل رقم اللابتوب الذي استلمه كل طالب.
      </div>
      <a href="{checkout_url}" style="display:inline-block;background:#27ae60;color:#fff;padding:9px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:.88rem">
        فتح نموذج تسليم الأجهزة
      </a>
    </div>
    """ if checkout_url else ''

    content = f"""
    <h2 style="color:#27ae60;margin-top:0">✅ تمت الموافقة على حجزك</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، يسعدنا إخبارك بأنه تمت الموافقة على طلب حجز العربة.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      {_booking_table_rows(data)}
    </table>
    {checkout_block}
    <p style="color:#555">نتمنى لكم حصة مفيدة. في حال الحاجة لأي تعديل يُرجى التواصل معنا.</p>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي] ✅ تمت الموافقة على حجزك #{data['reqId']}",
                 _base_html(content, '#27ae60'),
                 bcc_list=data.get('bcc', []))


def send_reject(data):
    content = f"""
    <h2 style="color:#c0392b;margin-top:0">❌ اعتذار — تعذّر اعتماد طلب الحجز</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، نأسف لإخبارك بأنه تعذّر اعتماد طلب حجزك.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#fdf0f0;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee">{data['reqId']}</td></tr>
      <tr><td style="padding:8px;background:#fdf0f0;font-weight:600">الموضوع</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('title') or '—'}</td></tr>
      <tr><td style="padding:8px;background:#fdf0f0;font-weight:600">سبب الرفض</td><td style="padding:8px;color:#c0392b;font-weight:600">{data.get('reason','')}</td></tr>
    </table>
    <p style="color:#555">يمكنك تقديم طلب جديد باختيار تاريخ أو حصة أخرى. نعتذر عن الإزعاج.</p>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي] بخصوص طلب الحجز #{data['reqId']}",
                 _base_html(content, '#c0392b'))


def send_cancel(data):
    content = f"""
    <h2 style="color:#e67e22;margin-top:0">🚫 تم إلغاء الحجز</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، تم إلغاء طلب الحجز التالي.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee">{data['reqId']}</td></tr>
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600">الموضوع</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('title') or '—'}</td></tr>
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600">المرحلة / الصف / الشعبة</td><td style="padding:8px">{_class_label(data)}</td></tr>
    </table>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي] إلغاء الحجز #{data['reqId']}",
                 _base_html(content, '#e67e22'))


def send_update(data):
    content = f"""
    <h2 style="color:#247680;margin-top:0">✏️ تم تعديل بيانات الحجز</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، تم تعديل بيانات طلب حجزك.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      {_booking_table_rows(data)}
    </table>
    <p style="color:#555">إذا تم التعديل على حجز معتمد، سيُعاد الحجز إلى قيد المراجعة وسيتم إخطارك بالقرار.</p>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي] تعديل الحجز #{data['reqId']}",
                 _base_html(content),
                 bcc_list=data.get('bcc', []))


def send_pending(data):
    content = f"""
    <h2 style="color:#e67e22;margin-top:0">🔄 إعادة الحجز لقيد المراجعة</h2>
    <p>عزيزي/عزيزتي <strong>{data['name']}</strong>، تم إعادة طلب حجزك إلى قيد المراجعة.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      {_booking_table_rows(data, bg='#fef9f0')}
    </table>
    """
    return _send(data['email'], data['name'],
                 f"[الرائد العربي] إعادة مراجعة طلب الحجز #{data['reqId']}",
                 _base_html(content, '#e67e22'),
                 bcc_list=data.get('bcc', []))


def send_staff_notification(event_type, data, contacts):
    """Send internal notification to all staff contacts."""
    if not contacts:
        return

    icons = {
        'new':     ('📋', '#247680', 'حجز جديد بانتظار المراجعة'),
        'approve': ('✅', '#27ae60', 'تم اعتماد حجز'),
        'reject':  ('❌', '#c0392b', 'تم رفض حجز'),
        'cancel':  ('🚫', '#e67e22', 'تم إلغاء حجز'),
        'update':  ('✏️', '#247680', 'تم تعديل حجز'),
        'revert':  ('🔄', '#e67e22', 'تم إرجاع الحجز لقيد المراجعة'),
    }
    icon, color, title = icons.get(event_type, ('📌', '#247680', 'إشعار حجز'))

    content = f"""
    <h2 style="color:{color};margin-top:0">{icon} {title}</h2>

    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee"><strong style="color:#247680">{data.get('reqId','')}</strong></td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">مقدم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('name','')}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">البريد</td><td style="padding:8px;border-bottom:1px solid #eee" dir="ltr">{data.get('email','')}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">الموضوع</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('title') or '—'}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">المرحلة / الصف / الشعبة</td><td style="padding:8px;border-bottom:1px solid #eee">{_class_label(data)}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">الحصة</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('periodLabel','')}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">التاريخ</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('date','')}</td></tr>
      {f'<tr><td style="padding:8px;background:#fdf0f0;font-weight:600">سبب الرفض</td><td style="padding:8px;color:#c0392b;font-weight:600">{data.get("reason","")}</td></tr>' if data.get("reason") else ""}
    </table>

    """

    subject_map = {
        'new':     f"[إشعار] حجز جديد بانتظار المراجعة #{data.get('reqId','')}",
        'approve': f"[إشعار] تم اعتماد الحجز #{data.get('reqId','')}",
        'reject':  f"[إشعار] تم رفض الحجز #{data.get('reqId','')}",
        'cancel':  f"[إشعار] تم إلغاء الحجز #{data.get('reqId','')}",
        'revert':  f"[إشعار] تم إرجاع الحجز لقيد المراجعة #" + data.get('reqId','') + "",
        'update':  f"[إشعار] تم تعديل الحجز #{data.get('reqId','')}",
    }
    subject = subject_map.get(event_type, f"[إشعار] حجز #{data.get('reqId','')}")
    html = _base_html(content, color)

    for contact in contacts:
        email = contact.get('email') if isinstance(contact, dict) else contact
        if email and '@' in email:
            try:
                _send(email, '', subject, html)
            except Exception:
                pass
