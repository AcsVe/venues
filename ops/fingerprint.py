"""بصمة البيانات: تتغيّر إذا تغيّر أي سجل (يستخدمها الهاتف ليعرف هل سُجّل شيء أثناء الطوارئ)."""
import hashlib
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import db_path, load_env  # noqa: E402

load_env()
h = hashlib.sha256()
p = db_path()
if os.path.isfile(p):
    with sqlite3.connect(p, timeout=60) as c:
        for line in c.iterdump():
            h.update(line.encode('utf-8', 'ignore'))
print(h.hexdigest())
