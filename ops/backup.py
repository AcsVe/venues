"""نسخة احتياطية: قاعدة البيانات (SQLite) + المرفقات + نسخة من الكود.

    python ops/backup.py                 # إلى BACKUP_DIR (مجلد Google Drive) أو backups
    python ops/backup.py --tag phone     # من الهاتف (اسم الملف ينتهي بـ -phone)
    python ops/backup.py --dir PATH --no-code --no-uploads

• القاعدة: BACKUP_DIR/db/venues-<وقت>.db.gz  (آخر 48 نسخة من كل نوع)
• المرفقات: BACKUP_DIR/uploads  (نسخة مطابقة — الجديد والمعدّل فقط)
• الكود: BACKUP_DIR/code/venues-code.zip  (احتياط لتحديث الهاتف بدون GitHub — بدون .env والبيانات)
"""
import argparse
import glob
import gzip
import os
import shutil
import sqlite3
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, backup_root, db_path, die, load_env, stamp, t, uploads_dir  # noqa: E402


def backup_db(dest_dir, tag, keep):
    src = db_path()
    if not os.path.isfile(src):
        die(t(f'لا توجد قاعدة بيانات بعد: {src}', f'No database yet: {src}'))
    os.makedirs(dest_dir, exist_ok=True)
    name = f'venues-{stamp()}{"-" + tag if tag else ""}.db'
    tmp = os.path.join(dest_dir, name + '.tmp')
    # نسخة متسقة حتى أثناء عمل الموقع (sqlite backup API)
    with sqlite3.connect(src, timeout=60) as s, sqlite3.connect(tmp) as d:
        s.backup(d)
    with open(tmp, 'rb') as fi, gzip.open(os.path.join(dest_dir, name + '.gz.part'), 'wb', compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)
    os.remove(tmp)
    final = os.path.join(dest_dir, name + '.gz')
    os.replace(final + '.part', final)
    # الاحتفاظ بآخر keep نسخة من نفس النوع
    pattern = '*-phone.db.gz' if tag == 'phone' else 'venues-*.db.gz'
    files = sorted(f for f in glob.glob(os.path.join(dest_dir, pattern)) if tag == 'phone' or not f.endswith('-phone.db.gz'))
    for old in files[:-keep] if keep > 0 else []:
        os.remove(old)
    return final


def mirror(src, dst):
    n = 0
    if not os.path.isdir(src):
        return 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out = os.path.join(dst, rel) if rel != '.' else dst
        os.makedirs(out, exist_ok=True)
        for f in files:
            a, b = os.path.join(root, f), os.path.join(out, f)
            if not os.path.exists(b) or os.path.getsize(a) != os.path.getsize(b) or os.path.getmtime(a) > os.path.getmtime(b) + 2:
                shutil.copy2(a, b)
                n += 1
    return n


def code_zip(dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    skip_dirs = {'.git', 'venv', '.venv', 'data', 'uploads', 'backups', 'logs', '__pycache__'}
    out = os.path.join(dest_dir, 'venues-code.zip')
    with zipfile.ZipFile(out + '.part', 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(BASE):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f in ('.env', 'pyexe.txt') or f.endswith(('.db', '.db-wal', '.db-shm', '.pyc')):
                    continue
                full = os.path.join(root, f)
                arc = os.path.join('venues', os.path.relpath(full, BASE))
                if f.endswith('.sh'):   # الهاتف يحتاج نهايات أسطر LF
                    z.writestr(arc, open(full, 'rb').read().replace(b'\r\n', b'\n'))
                else:
                    z.write(full, arc)
    os.replace(out + '.part', out)


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='')
    ap.add_argument('--tag', default='')
    ap.add_argument('--keep', type=int, default=48)
    ap.add_argument('--no-code', action='store_true')
    ap.add_argument('--no-uploads', action='store_true')
    a = ap.parse_args()
    root = a.dir or backup_root()
    f = backup_db(os.path.join(root, 'db'), a.tag, a.keep)
    print(t('✓ القاعدة: ', '✓ Database: ') + f)
    if not a.no_uploads:
        n = mirror(uploads_dir(), os.path.join(root, 'uploads'))
        print(t(f'✓ المرفقات: {n} ملف جديد', f'✓ Uploads: {n} new files'))
    if not a.no_code:
        code_zip(os.path.join(root, 'code'))
        print(t('✓ الكود', '✓ Code'))


if __name__ == '__main__':
    main()
