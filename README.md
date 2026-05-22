# ACS Booking System

نظام حجز قاعات — Flask + SQLite + Render

## الصفحات العامة

| المسار | الوصف |
|--------|-------|
| `/calendar` | الروزنامة الشهرية |
| `/book` | نموذج حجز جديد |
| `/lookup` | استعلام / تعديل / إلغاء الحجز |

## لوحة التحكم

| المسار | الوصف |
|--------|-------|
| `/admin` | لوحة التحكم الرئيسية |
| `/admin/login` | تسجيل الدخول |

---

## النشر على Render

### 1. رفع الكود إلى GitHub

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/YOUR_USER/acs-booking.git
git push -u origin main
```

### 2. إنشاء Web Service على Render

- اختر **Web Service** → اربطه بالـ repo
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn app:app --workers=1 --bind=0.0.0.0:$PORT --timeout=120`
- **Disk:** أضف Persistent Disk حجمه 1GB على المسار `/data`

### 3. متغيرات البيئة (Environment Variables)

| المتغير | الوصف |
|---------|-------|
| `SECRET_KEY` | مفتاح سري عشوائي (اضغط Generate) |
| `ADMIN_USER` | اسم مستخدم لوحة التحكم |
| `ADMIN_PASS` | كلمة مرور لوحة التحكم |
| `ORG_AR` | اسم المنظمة بالعربية |
| `ORG_EN` | اسم المنظمة بالإنجليزية |
| `LOGO_URL` | رابط شعار المنظمة |
| `BREVO_API_KEY` | مفتاح Brevo للبريد الإلكتروني |
| `SENDER_EMAIL` | بريد المرسل |
| `SENDER_NAME` | اسم المرسل |

---

## قاعدة البيانات

SQLite — يتم إنشاؤها تلقائياً عند أول تشغيل في:
- **Render:** `/data/acs_booking.db`
- **محلي:** `acs_booking.db` في مجلد المشروع

### الجداول

- `bookings` — طلبات الحجز
- `halls` — القاعات
- `blocked_periods` — الفترات المحظورة
- `contacts` — جهات الاتصال (BCC)

---

## التشغيل المحلي

```bash
pip install -r requirements.txt
python app.py
```

ثم افتح: http://localhost:5000
