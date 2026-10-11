"""نقل كل البيانات من قاعدة Neon (السحابية) إلى قاعدة هذا الجهاز (SQLite) — مرة واحدة.

    python ops/import_neon.py            # يطلب رابط Neon (لا يظهر أثناء اللصق)
    python ops/import_neon.py --yes      # بدون سؤال تأكيد

• الرابط من Render ← Environment ← DATABASE_URL (يبدأ بـ postgresql://).
• يستبدل بيانات هذا الجهاز بالكامل. المرفقات المخزنة على Render لا تُنقل (القاعدة فقط).
"""
import getpass
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'ops'))
from common import die, load_env  # noqa: E402


def main():
    load_env()
    url = os.environ.get('NEON_DATABASE_URL', '').strip() or getpass.getpass('  الصق رابط Neon (DATABASE_URL من Render) ثم Enter: ').strip()
    url = ''.join(ch for ch in url if ch.isprintable()).strip()
    if not url:
        die('تم الإلغاء')
    if '://' not in url:
        die('الرابط لم يُلصق صحيحاً. في PowerShell الصق بزر الفأرة الأيمن (وليس Ctrl+V) — لا يظهر شيء أثناء اللصق، ثم Enter')
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    if '--yes' not in sys.argv and input('  ستُستبدل بيانات هذا الجهاز ببيانات Neon. اكتب YES: ').strip() != 'YES':
        die('تم الإلغاء')

    from sqlalchemy import create_engine, inspect, select
    from app import app
    from models import db
    src = create_engine(url, pool_pre_ping=True)
    try:
        src_tables = set(inspect(src).get_table_names())
    except Exception as e:
        die(f'تعذّر الاتصال بـ Neon: {e.__class__.__name__}')
    with app.app_context():
        dst = db.engine
        tables = db.metadata.sorted_tables
        with dst.begin() as dc:
            dc.exec_driver_sql('PRAGMA foreign_keys=OFF')
            for tb in reversed(tables):
                dc.execute(tb.delete())
            total = 0
            for tb in tables:
                if tb.name not in src_tables:
                    print(f'  - {tb.name}: غير موجود في Neon (تخطي)')
                    continue
                src_cols = {c['name'] for c in inspect(src).get_columns(tb.name)}
                cols = [c for c in tb.columns if c.name in src_cols]
                with src.connect() as sc:
                    rows = [dict(r._mapping) for r in sc.execute(select(*[tb.c[c.name] for c in cols]))]
                if rows:
                    dc.execute(tb.insert(), rows)
                total += len(rows)
                print(f'  ✓ {tb.name}: {len(rows)}')
        print(f'\n  ✅ نُقل {total} سجل إلى هذا الجهاز.')


if __name__ == '__main__':
    main()
