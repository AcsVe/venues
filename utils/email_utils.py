import requests
from flask import current_app


def _send(to_email, to_name, subject, html_body, bcc_list=None):
    """Send email via Brevo API (synchronous)."""
    api_key = current_app.config.get('BREVO_API_KEY', '')
    if not api_key:
        return False

    sender_email = current_app.config.get('SENDER_EMAIL', '')
    sender_name  = current_app.config.get('SENDER_NAME', 'ACS Booking')
    org_ar       = current_app.config.get('ORG_AR', '')

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
            timeout=15
        )
        return r.status_code in (200, 201, 202)
    except Exception:
        return False


def _date_label(date_str, end_date_str=None, full_day=False, start_time='', end_time=''):
    """Build human-readable date/time label in Arabic."""
    label = date_str or ''
    if end_date_str and end_date_str != date_str:
        label += f' — {end_date_str}'
    if not full_day and start_time and end_time:
        label += f'  ({start_time} - {end_time})'
    elif full_day:
        label += '  (يوم كامل)'
    return label


def _base_html(content, color='#247680'):
    logo = current_app.config.get('LOGO_URL', '')
    org  = current_app.config.get('ORG_AR', 'Arab Cultural Society')
    return f"""
<div style="font-family:Tajawal,Arial,sans-serif;direction:rtl;max-width:600px;margin:auto;border:1px solid #e0e0e0;border-radius:12px;overflow:hidden">
  <div style="background:{color};padding:20px 24px;text-align:center">
    {'<img src="'+logo+'" height="50" style="margin-bottom:8px"><br>' if logo else ''}
    <span style="color:#fff;font-size:20px;font-weight:700">{org}</span>
  </div>
  <div style="padding:28px 24px;background:#fff">{content}</div>
  <div style="background:#f5f5f5;padding:12px 24px;text-align:center;font-size:12px;color:#888">
    هذا البريد مُرسَل تلقائياً من نظام حجز القاعات — يُرجى عدم الرد عليه
  </div>
</div>"""


def send_confirm(data):
    date_label = _date_label(data.get('date'), data.get('endDate'),
                              data.get('fullDay'), data.get('startTime'), data.get('endTime'))
    content = f"""
    <h2 style="color:#247680;margin-top:0">✅ تم استلام طلب حجزك</h2>
    <p>شكراً <strong>{data['name']}</strong>، تم استلام طلب حجزك وهو قيد المراجعة.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee"><strong style="color:#247680">{data['reqId']}</strong></td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">المناسبة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['title']}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">القاعة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['hall']}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">التاريخ والوقت</td><td style="padding:8px">{date_label}</td></tr>
    </table>
    <p style="color:#555">سيتم إشعارك بقرار الإدارة في أقرب وقت ممكن. احتفظ برقم الطلب للاستعلام والتعديل.</p>
    """
    return _send(data['email'], data['name'],
                 f"[ACS] تم استلام طلب الحجز #{data['reqId']}",
                 _base_html(content))


def send_approve(data):
    date_label = _date_label(data.get('date'), data.get('endDate'),
                              data.get('fullDay'), data.get('startTime'), data.get('endTime'))
    content = f"""
    <h2 style="color:#27ae60;margin-top:0">✅ تمت الموافقة على حجزك</h2>
    <p>عزيزي <strong>{data['name']}</strong>، يسعدنا إخبارك بأنه تمت الموافقة على طلب حجزك.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee"><strong style="color:#247680">{data['reqId']}</strong></td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">المناسبة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['title']}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">القاعة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['hall']}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">التاريخ والوقت</td><td style="padding:8px">{date_label}</td></tr>
    </table>
    <p style="color:#555">نتمنى لكم تجربة ممتازة. في حال الحاجة لأي تعديل يُرجى التواصل معنا.</p>
    """
    return _send(data['email'], data['name'],
                 f"[ACS] ✅ تمت الموافقة على حجزك #{data['reqId']}",
                 _base_html(content, '#27ae60'),
                 bcc_list=data.get('bcc', []))


def send_reject(data):
    content = f"""
    <h2 style="color:#c0392b;margin-top:0">❌ اعتذار — تعذّر اعتماد طلب الحجز</h2>
    <p>عزيزي <strong>{data['name']}</strong>، نأسف لإخبارك بأنه تعذّر اعتماد طلب حجزك.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#fdf0f0;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee">{data['reqId']}</td></tr>
      <tr><td style="padding:8px;background:#fdf0f0;font-weight:600">المناسبة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['title']}</td></tr>
      <tr><td style="padding:8px;background:#fdf0f0;font-weight:600">سبب الرفض</td><td style="padding:8px;color:#c0392b;font-weight:600">{data.get('reason','')}</td></tr>
    </table>
    <p style="color:#555">يمكنك تقديم طلب جديد باختيار تاريخ أو وقت آخر. نعتذر عن الإزعاج.</p>
    """
    return _send(data['email'], data['name'],
                 f"[ACS] بخصوص طلب الحجز #{data['reqId']}",
                 _base_html(content, '#c0392b'))


def send_cancel(data):
    content = f"""
    <h2 style="color:#e67e22;margin-top:0">🚫 تم إلغاء الحجز</h2>
    <p>عزيزي <strong>{data['name']}</strong>، تم إلغاء طلب الحجز التالي.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee">{data['reqId']}</td></tr>
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600">المناسبة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['title']}</td></tr>
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600">القاعة</td><td style="padding:8px">{data['hall']}</td></tr>
    </table>
    """
    return _send(data['email'], data['name'],
                 f"[ACS] إلغاء الحجز #{data['reqId']}",
                 _base_html(content, '#e67e22'))


def send_update(data):
    date_label = _date_label(data.get('date'), data.get('endDate'),
                              data.get('fullDay'), data.get('startTime'), data.get('endTime'))
    content = f"""
    <h2 style="color:#247680;margin-top:0">✏️ تم تعديل بيانات الحجز</h2>
    <p>عزيزي <strong>{data['name']}</strong>، تم تعديل بيانات طلب حجزك.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee"><strong style="color:#247680">{data['reqId']}</strong></td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">المناسبة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['title']}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">القاعة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['hall']}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">التاريخ والوقت</td><td style="padding:8px">{date_label}</td></tr>
    </table>
    <p style="color:#555">إذا تم التعديل على حجز معتمد، سيُعاد الحجز إلى قيد المراجعة وسيتم إخطارك بالقرار.</p>
    """
    return _send(data['email'], data['name'],
                 f"[ACS] تعديل الحجز #{data['reqId']}",
                 _base_html(content),
                 bcc_list=data.get('bcc', []))


def send_pending(data):
    date_label = _date_label(data.get('date'), data.get('endDate'))
    content = f"""
    <h2 style="color:#e67e22;margin-top:0">🔄 إعادة الحجز لقيد المراجعة</h2>
    <p>عزيزي <strong>{data['name']}</strong>، تم إعادة طلب حجزك إلى قيد المراجعة.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee">{data['reqId']}</td></tr>
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600">المناسبة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['title']}</td></tr>
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600">القاعة</td><td style="padding:8px;border-bottom:1px solid #eee">{data['hall']}</td></tr>
      <tr><td style="padding:8px;background:#fef9f0;font-weight:600">التاريخ</td><td style="padding:8px">{date_label}</td></tr>
    </table>
    """
    return _send(data['email'], data['name'],
                 f"[ACS] إعادة مراجعة طلب الحجز #{data['reqId']}",
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

    date_label = _date_label(
        data.get('date'), data.get('endDate'),
        data.get('fullDay'), data.get('startTime'), data.get('endTime')
    )

    content = f"""
    <h2 style="color:{color};margin-top:0">{icon} {title}</h2>
    <p style="color:#555;font-size:13px">هذا إشعار داخلي — لا تقم بالرد على هذا البريد</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600;width:40%">رقم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee"><strong style="color:#247680">{data.get('reqId','')}</strong></td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">مقدم الطلب</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('name','')}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">البريد</td><td style="padding:8px;border-bottom:1px solid #eee" dir="ltr">{data.get('email','')}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">المناسبة</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('title','')}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">القاعة</td><td style="padding:8px;border-bottom:1px solid #eee">{data.get('hall','')}</td></tr>
      <tr><td style="padding:8px;background:#f0f7f8;font-weight:600">التاريخ والوقت</td><td style="padding:8px;border-bottom:1px solid #eee">{date_label}</td></tr>
      {f'<tr><td style="padding:8px;background:#fdf0f0;font-weight:600">سبب الرفض</td><td style="padding:8px;color:#c0392b;font-weight:600">{data.get("reason","")}</td></tr>' if data.get("reason") else ""}
    </table>
    <p style="text-align:center;margin-top:16px">
      <a href="https://acsv.onrender.com/admin" style="background:{color};color:#fff;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:700">
        فتح لوحة التحكم
      </a>
    </p>
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
