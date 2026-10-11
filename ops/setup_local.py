"""إعداد ملف .env على هذا الجهاز (مرة واحدة). يقرأ إعدادات موقع الجائزة IAJ إن وجده (مجلد Drive + رمز Cloudflare)."""
import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE  # noqa: E402

ENV = os.path.join(BASE, '.env')


def read_env(path):
    out = {}
    if os.path.isfile(path):
        for raw in open(path, encoding='utf-8-sig', errors='ignore'):
            line = raw.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def find_iaj():
    """مجلد موقع الجائزة على نفس الجهاز (للاستفادة من BACKUP_DIR وCF_API_TOKEN)"""
    cands = [os.environ.get('IAJ_DIR', '')]
    parent = os.path.dirname(BASE)
    try:
        cands += [os.path.join(parent, d) for d in os.listdir(parent)]
    except OSError:
        pass
    for drive in 'CDEFG':
        cands += [f'{drive}:\\IAJ', f'{drive}:\\IAJ-main', f'{drive}:\\iaj']
    for d in cands:
        if d and os.path.isfile(os.path.join(d, 'standby', 'cf_switch.py')) and os.path.isfile(os.path.join(d, '.env')):
            return d
    return ''


def ask(label, default='', secret=False):
    shown = ' [مخفي]' if secret and default else (f' [{default}]' if default else '')
    v = (getpass.getpass if secret else input)(f'  {label}{shown}: ').strip()
    return v or default


def main():
    cur = read_env(ENV)
    iaj = find_iaj()
    ie = read_env(os.path.join(iaj, '.env')) if iaj else {}
    if iaj:
        print(f'  وُجد موقع الجائزة في: {iaj}  (سآخذ منه مجلد Drive ورمز Cloudflare)')
    bk_default = cur.get('BACKUP_DIR') or (os.path.join(ie['BACKUP_DIR'], 'venues') if ie.get('BACKUP_DIR') else '')
    print('\n  إعدادات نظام الحجوزات (اضغط Enter لقبول القيمة بين القوسين أو للتخطي)\n')
    vals = dict(cur)
    vals.setdefault('SECRET_KEY', secrets.token_urlsafe(48))
    vals['BASE_URL'] = ask('عنوان الموقع', cur.get('BASE_URL', 'https://2.iajaward.org'))
    vals['PORT'] = cur.get('PORT', '8002')
    vals['BACKUP_DIR'] = ask('مجلد النسخ الاحتياطية (داخل Google Drive)', bk_default)
    vals['CF_API_TOKEN'] = cur.get('CF_API_TOKEN') or ie.get('CF_API_TOKEN', '') or ask('رمز Cloudflare API', '', True)
    vals['ORG_AR'] = ask('اسم المؤسسة بالعربية', cur.get('ORG_AR', 'مدرسة الرائد العربي'))
    vals['ORG_EN'] = ask('اسم المؤسسة بالإنجليزية', cur.get('ORG_EN', 'Al-Raed Al-Arabi School'))
    print('\n  البريد (Microsoft 365) — انسخها من إعدادات Render (Environment). Enter للتخطي:')
    vals['MS_TENANT_ID'] = ask('MS_TENANT_ID', cur.get('MS_TENANT_ID', ''))
    vals['MS_CLIENT_ID'] = ask('MS_CLIENT_ID', cur.get('MS_CLIENT_ID', ''))
    vals['MS_CLIENT_SECRET'] = ask('MS_CLIENT_SECRET', cur.get('MS_CLIENT_SECRET', ''), True)
    vals['MS_SENDER_EMAIL'] = ask('MS_SENDER_EMAIL', cur.get('MS_SENDER_EMAIL', ''))
    print('\n  إشعارات Push — إن كانت في Render قيم VAPID خاصة انسخها (وإلا Enter):')
    pub = ask('VAPID_PUBLIC_KEY', cur.get('VAPID_PUBLIC_KEY', ''))
    if pub:
        vals['VAPID_PUBLIC_KEY'] = pub
        print('  الصق VAPID_PRIVATE_KEY في سطر واحد (\\n بين الأسطر) أو Enter:')
        prv = ask('VAPID_PRIVATE_KEY', cur.get('VAPID_PRIVATE_KEY', ''), True)
        if prv:
            vals['VAPID_PRIVATE_KEY'] = prv
    order = ['SECRET_KEY', 'BASE_URL', 'PORT', 'BACKUP_DIR', 'CF_API_TOKEN', 'ORG_AR', 'ORG_EN',
             'MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL', 'VAPID_PUBLIC_KEY', 'VAPID_PRIVATE_KEY']
    keys = order + [k for k in vals if k not in order]
    with open(ENV, 'w', encoding='utf-8') as fh:
        fh.write('# إعدادات نظام الحجوزات على هذا الجهاز — لا ترفع هذا الملف ولا تشاركه\n')
        for k in keys:
            if vals.get(k):
                fh.write(f'{k}={vals[k]}\n')
    print(f'\n  ✓ حُفظت الإعدادات في {ENV}')


if __name__ == '__main__':
    main()
