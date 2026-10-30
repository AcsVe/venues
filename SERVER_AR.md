# تشغيل نظام الحجوزات على الكمبيوتر (2.iajaward.org) + الهاتف احتياطاً

نفس طريقة موقع الجائزة: الكمبيوتر يشغّل النظام، Cloudflare ينشره على `https://2.iajaward.org`،
نسخة احتياطية كل ساعة إلى Google Drive، والهاتف يتولّى عند انقطاع الكمبيوتر.

| | |
|---|---|
| المنفذ المحلي | `8002` (موقع الجائزة على 8000) |
| القاعدة | ملف SQLite في `data\acs_booking.db` + المرفقات `data\uploads` |
| النسخ | `BACKUP_DIR\db` (داخل مجلد Drive للجائزة: `IAJ-backups\venues`) |
| نفق الكمبيوتر | نفس نفق الجائزة `iajaward` |
| نفق الهاتف | `venues-standby` (يُنشأ تلقائياً) |

## الكمبيوتر (مرة واحدة)

```bat
git clone https://github.com/AcsVe/venues.git C:\venues
cd C:\venues
1_setup.bat            :: الإعدادات + نقل البيانات من Neon + ربط 2.iajaward.org
3_autostart.bat        :: كمسؤول: تشغيل مع Windows + نسخ كل ساعة + الحارس
```

| الملف | العمل |
|---|---|
| `update.bat` | تحديث الكود من GitHub وإعادة التشغيل |
| `site_where.bat` | أين يعمل 2.iajaward.org + تشخيص + حالة الحارس |
| `switch_to_server.bat` | إرجاع 2.iajaward.org للكمبيوتر فوراً |
| `back_to_server.bat` | بعد الطوارئ: أخذ بيانات الهاتف + إرجاع الموقع |
| `backup_now.bat` / `restore_backup.bat` | نسخ / استرجاع يدوي |

## الهاتف (Termux — بجانب موقع الجائزة)

```bash
cd ~ && curl -sL -H "Authorization: Bearer $(cat ~/.iaj_github_token)" \
  https://api.github.com/repos/AcsVe/venues/zipball/main -o r.zip && \
  unzip -q r.zip -d rtmp && mv rtmp/* venues && rm -rf rtmp r.zip && \
  bash ~/venues/standby/phone.sh setup && bash ~/venues/standby/phone.sh configure
```

رمز GitHub يجب أن يشمل مستودع VENUES. الأزرار في Termux:Widget: زر «VEN» يفتح قائمة النظام (حالته الآن، تشغيل الطوارئ وإنهاؤها، الإرجاع للكمبيوتر، تحديث الكود، النسخ إلى Drive)، و«★ لوحة الأنظمة» تعرض حالة كل المواقع، و«★ قفل أو فتح الأزرار» و«★ English - عربي» للقفل واللغة (`standby/widgets.sh` و`standby/panel.sh` في IAJ).
