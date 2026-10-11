#!/data/data/com.termux/files/usr/bin/bash
# ================================================================
#  نظام الحجوزات (2.iajaward.org) من الهاتف (Termux) — احتياط عند انقطاع الكمبيوتر
#  يعمل بجانب موقع الجائزة (~/iaj) دون تعارض: منفذ 8002 ونفق مستقل «venues-standby»
#
#   bash ~/venues/standby/phone.sh setup        # مرة واحدة: المتطلبات
#   bash ~/venues/standby/phone.sh configure    # مرة واحدة: الرموز (من إعدادات IAJ تلقائياً) + الأزرار VEN
#   bash ~/venues/standby/phone.sh update-code  # آخر نسخة من GitHub (أو من Drive)
#   bash ~/venues/standby/phone.sh takeover     # ★ الطوارئ: أحدث بيانات + تشغيل + تحويل 2.iajaward.org للهاتف
#   bash ~/venues/standby/phone.sh stop         # إنهاء الطوارئ: إرجاع للكمبيوتر أو رفع بيانات الهاتف
#   bash ~/venues/standby/phone.sh server       # إرجاع 2.iajaward.org للكمبيوتر فوراً (بدون نقل بيانات)
#   bash ~/venues/standby/phone.sh status       # أين يعمل الآن
#   bash ~/venues/standby/phone.sh backup       # رفع بيانات الهاتف إلى Drive
#   bash ~/venues/standby/phone.sh try          # تجربة على الهاتف فقط: http://127.0.0.1:8002
#
#  القفل واللغة مشتركان مع أزرار IAJ (IAJ 0 / IAJ 9).
# ================================================================
set -e
SITE="$HOME/venues"
LOCAL_BK="$HOME/venues-backups"
REMOTE="gdrive:IAJ-backups"
[ -f "$HOME/.iaj_standby" ] && . "$HOME/.iaj_standby"
RT_REMOTE="${RT_REMOTE:-$REMOTE/venues}"
[ -f "$HOME/.venues_standby" ] && . "$HOME/.venues_standby"
TOKEN_FILE="$HOME/.venues_tunnel_token"
export PYTHONUTF8=1
LANG_UI="${LANG_UI:-ar}"
export IAJ_LANG="$LANG_UI"
T() { if [ "$LANG_UI" = "en" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
say() { printf '\n\033[1;36m» %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✖ %s\033[0m\n' "$*"; exit 1; }
guard() {
  if [ -f "$HOME/.iaj_lock" ] && [ -z "$IAJ_NOGUARD" ]; then
    die "$(T "🔒 الأزرار الحساسة مقفلة — اضغط «IAJ 0 - قفل أو فتح الأزرار» لفتحها" "🔒 Sensitive buttons are locked - press IAJ 0 to unlock")"
  fi
}
need_code() { [ -f "$SITE/serve.py" ] || die "$(T "الكود غير موجود — شغّل: bash phone.sh update-code" "Code not found - run: bash phone.sh update-code")"; }
# إيقاف نفق هذا النظام فقط (لا يلمس نفق موقع الجائزة)
kill_my_tunnel() {
  [ -s "$TOKEN_FILE" ] || return 0
  local key; key=$(head -c 40 "$TOKEN_FILE")
  for p in $(pgrep -x cloudflared 2>/dev/null); do
    tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -qF "$key" && kill "$p" 2>/dev/null || true
  done
}
neon_on() { grep -q '^NEON_BACKUP_URL=.' "$SITE/.env" 2>/dev/null; }
ensure_pg8000() { python -c "import pg8000" 2>/dev/null || pip install -q pg8000 >/dev/null 2>&1 || true; }
other_takeover() { pgrep -f "(iaj|rastkt)/standby/phone.sh takeover" >/dev/null 2>&1; }

ensure_env() {
  if [ ! -f "$SITE/.env" ]; then
    KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
    printf 'SECRET_KEY=%s\nBASE_URL=https://2.iajaward.org\nPORT=8002\nTHREADS=4\n' "$KEY" > "$SITE/.env"
  fi
  # رمز Cloudflare: نفس رمز موقع الجائزة على هذا الهاتف
  if ! grep -q '^CF_API_TOKEN=.' "$SITE/.env" && grep -q '^CF_API_TOKEN=.' "$HOME/iaj/.env" 2>/dev/null; then
    grep '^CF_API_TOKEN=' "$HOME/iaj/.env" >> "$SITE/.env"
  fi
  chmod 600 "$SITE/.env" 2>/dev/null || true
}

case "$1" in
  setup)
    say "$(T "تثبيت المتطلبات" "Installing requirements")"
    pkg install -y python rclone cloudflared unzip python-cryptography >/dev/null || pkg install -y python rclone cloudflared unzip
    pip install "Flask==3.0.3" "Flask-SQLAlchemy==3.1.1" "SQLAlchemy==2.0.36" "Werkzeug==3.0.6" "requests" "waitress==3.0.2" "pg8000==1.31.5" "APScheduler==3.10.4" tzdata
    pip install "Flask-Compress==1.25" || echo "$(T "تخطي الضغط (اختياري)" "Skipping compression (optional)")"
    say "$(T "إشعارات Push (اختياري — إن فشل يعمل النظام بدونها)" "Push notifications (optional - the system works without them)")"
    AIOHTTP_NO_EXTENSIONS=1 pip install "pywebpush==2.5.0" "py-vapid==1.9.0" || echo "$(T "تخطي Push" "Skipping push")"
    say "$(T "تم. الخطوة التالية: bash ~/venues/standby/phone.sh configure" "Done. Next: bash ~/venues/standby/phone.sh configure")"
    ;;

  update-code)
    if [ -s "$HOME/.iaj_github_token" ] && [ -f "$SITE/standby/gh_update.py" ]; then
      say "$(T "تنزيل الكود من GitHub" "Downloading code from GitHub")"
      if python "$SITE/standby/gh_update.py"; then
        say "$(T "تم تحديث الكود" "Code updated")"; exit 0
      fi
      say "$(T "تعذّر GitHub (تأكد أن رمز GitHub يشمل مستودع VENUES) — أحاول من Drive" "GitHub failed (make sure the GitHub token includes the VENUES repo) - trying Drive")"
    fi
    mkdir -p "$LOCAL_BK/code"
    rclone copy "$RT_REMOTE/code" "$LOCAL_BK/code" --progress
    [ -f "$LOCAL_BK/code/venues-code.zip" ] || die "$(T "لم أجد venues-code.zip في Drive — شغّل backup_now.bat على الكمبيوتر" "venues-code.zip not in Drive - run backup_now.bat on the PC")"
    unzip -oq "$LOCAL_BK/code/venues-code.zip" -d "$HOME"
    sed -i 's/\r$//' "$SITE"/standby/*.sh 2>/dev/null || true
    say "$(T "تم تحديث الكود من Drive" "Code updated from Drive")"
    ;;

  restore)
    need_code; ensure_env
    say "$(T "تنزيل أحدث بيانات الحجوزات من Drive" "Downloading the latest booking data from Drive")"
    mkdir -p "$LOCAL_BK/db"
    rclone copy "$RT_REMOTE/db" "$LOCAL_BK/db" --include "*.db.gz" --max-age 30d --progress
    rclone copy "$RT_REMOTE/uploads" "$LOCAL_BK/uploads" --progress || true
    if neon_on; then
      ensure_pg8000
      say "$(T "جلب أحدث نسخة من Neon أيضاً (تُستخدم الأحدث بين Drive وNeon)" "Also fetching the latest copy from Neon (the newer of Drive/Neon is used)")"
      (cd "$SITE" && python ops/neon_store.py pull "$LOCAL_BK/db") || say "$(T "تعذّر Neon — أكمل بنسخة Drive" "Neon failed - continuing with the Drive copy")"
    fi
    cd "$SITE" && python ops/restore.py --latest "$LOCAL_BK" --uploads --yes
    ;;

  neon)
    guard; need_code; ensure_env
    neon_on || die "$(T "لا يوجد رابط Neon — شغّل configure وألصقه عند الطلب" "No Neon link - run configure and paste it when asked")"
    ensure_pg8000
    say "$(T "تنزيل أحدث نسخة من Neon وتثبيتها على الهاتف" "Downloading the latest copy from Neon onto the phone")"
    mkdir -p "$LOCAL_BK/neon"
    F=$(cd "$SITE" && python ops/neon_store.py pull "$LOCAL_BK/neon" | tail -1)
    echo "$F"
    N=$(ls -t "$LOCAL_BK/neon"/*.db.gz 2>/dev/null | head -1)
    [ -n "$N" ] || die "$(T "لم يُنزَّل شيء" "Nothing was downloaded")"
    pkill -f "$SITE/serve.py" 2>/dev/null || true
    cd "$SITE" && python ops/restore.py --file "$N" --yes
    say "$(T "تم. بيانات Neon على الهاتف الآن — للتشغيل للعامة: زر $L 1" "Done. Neon data is on the phone - to serve it publicly: $L 1")"
    ;;

  try)
    need_code; ensure_env
    say "$(T "النظام يعمل على الهاتف فقط: http://127.0.0.1:8002 — للإيقاف Ctrl+C" "Running on the phone only: http://127.0.0.1:8002 - Ctrl+C to stop")"
    cd "$SITE" && DISABLE_REMINDERS=1 python "$SITE/serve.py"
    ;;

  configure)
    need_code; ensure_env
    grep -q '^CF_API_TOKEN=.' "$SITE/.env" || {
      read -r -s -p "$(T "  الصق رمز Cloudflare API ثم Enter: " "  Paste the Cloudflare API token, then Enter: ")" TK; echo
      [ -n "$TK" ] && echo "CF_API_TOKEN=$TK" >> "$SITE/.env"; }
    say "$(T "جلب رمز نفق الهاتف venues-standby من Cloudflare (يُحفظ ولا يظهر)" "Fetching the phone tunnel token from Cloudflare (saved, not shown)")"
    (cd "$SITE" && python ops/cf.py token > "$TOKEN_FILE.tmp") && [ -s "$TOKEN_FILE.tmp" ] && mv "$TOKEN_FILE.tmp" "$TOKEN_FILE" && chmod 600 "$TOKEN_FILE" \
      || { rm -f "$TOKEN_FILE.tmp"; die "$(T "تعذّر — شغّل أولاً على الكمبيوتر 1_setup.bat (ينشئ النفق)" "Failed - run 1_setup.bat on the PC first (it creates the tunnel)")"; }
    if ! neon_on && [ ! -f "$SITE/.neon_skip" ]; then
      read -r -s -p "$(T "  (اختياري) الصق رابط Neon لهذا النظام — أو Enter للتخطي: " "  (optional) Paste this system's Neon link - or Enter to skip: ")" NU; echo
      if [ -n "$NU" ]; then echo "NEON_BACKUP_URL=$NU" >> "$SITE/.env"; ensure_pg8000; (cd "$SITE" && python ops/neon_store.py info) || true
      else touch "$SITE/.neon_skip"; fi
    fi
    mkdir -p "$HOME/.shortcuts"
    mk() { printf '#!/data/data/com.termux/files/usr/bin/bash\n%s\necho; read -r -p "Enter ↵" _\n' "$2" > "$HOME/.shortcuts/$1"; chmod +x "$HOME/.shortcuts/$1"; }
    mk "VEN 1 - تشغيل طوارئ الحجوزات"  "bash ~/venues/standby/phone.sh takeover"
    mk "VEN 1 - إنهاء طوارئ الحجوزات"  "bash ~/venues/standby/phone.sh stop"
    mk "VEN 2 - إرجاع الحجوزات للكمبيوتر" "bash ~/venues/standby/phone.sh server"
    mk "VEN 3 - أين يعمل نظام الحجوزات" "bash ~/venues/standby/phone.sh status"
    mk "VEN 4 - تحديث كود الحجوزات" "bash ~/venues/standby/phone.sh update-code && bash ~/venues/standby/phone.sh configure"
    mk "VEN 5 - نسخ بيانات الحجوزات إلى Drive" "bash ~/venues/standby/phone.sh backup"
    mk "VEN 6 - استرجاع من Neon إلى الهاتف" "bash ~/venues/standby/phone.sh neon"
    say "$(T "تم. حدّث أداة Termux:Widget لتظهر أزرار VEN" "Done. Refresh the Termux:Widget to see the VEN buttons")"
    cd "$SITE" && python ops/cf.py status || true
    ;;

  backup)
    guard; need_code
    cd "$SITE"
    say "$(T "رفع بيانات الهاتف إلى Drive" "Uploading the phone data to Drive")"
    python ops/backup.py --dir "$LOCAL_BK" --tag phone --no-code --no-uploads
    rclone copy "$LOCAL_BK/db" "$RT_REMOTE/db" --include "*-phone.db.gz" --max-age 1d --progress
    rclone copy "$SITE/data/uploads" "$RT_REMOTE/uploads" --progress || true
    say "$(T "تم. على الكمبيوتر: back_to_server.bat" "Done. On the PC: back_to_server.bat")"
    ;;

  takeover)
    guard; need_code; ensure_env
    grep -q '^CF_API_TOKEN=.' "$SITE/.env" || die "$(T "شغّل configure أولاً" "Run configure first")"
    [ -s "$TOKEN_FILE" ] || die "$(T "شغّل configure أولاً (رمز النفق)" "Run configure first (tunnel token)")"
    if (cd "$SITE" && python ops/cf.py server-up 2>/dev/null); then
      echo
      echo "$(T "  ✋ الكمبيوتر يعمل ونظام الحجوزات سليم — لا حاجة للطوارئ." "  ✋ The PC is up and the booking system is fine - no need for emergency mode.")"
      read -r -p "$(T "  للتشغيل رغم ذلك اكتب FORCE، أو Enter للإلغاء: " "  To run anyway type FORCE, or Enter to cancel: ")" ANS
      [ "$ANS" = "FORCE" ] || die "$(T "تم الإلغاء" "Cancelled")"
    fi
    pkill -f "$SITE/serve.py" 2>/dev/null || true
    kill_my_tunnel; sleep 1
    bash "$0" restore
    cd "$SITE"
    termux-wake-lock 2>/dev/null || true
    say "$(T "تشغيل نظام الحجوزات" "Starting the booking system")"
    python "$SITE/serve.py" > "$HOME/venues-serve.log" 2>&1 &
    WEB=$!
    OK=0
    for i in $(seq 1 45); do
      kill -0 $WEB 2>/dev/null || break
      python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8002/',timeout=2)" 2>/dev/null && { OK=1; break; }
      sleep 2
    done
    if [ $OK != 1 ]; then
      kill $WEB 2>/dev/null; tail -20 "$HOME/venues-serve.log"
      die "$(T "لم يعمل على الهاتف — لم يُحوَّل شيء" "Did not start on the phone - nothing was switched")"
    fi
    say "$(T "تشغيل نفق الهاتف" "Starting the phone tunnel")"
    cloudflared tunnel --no-autoupdate run --token "$(cat "$TOKEN_FILE")" > "$HOME/venues-cloudflared.log" 2>&1 &
    CF=$!
    FP0=$(python ops/fingerprint.py 2>/dev/null | tail -1)
    DONE=0
    finish() {
      [ $DONE = 1 ] && return; DONE=1
      kill $WEB $CF 2>/dev/null
      say "$(T "إيقاف نظام الحجوزات على الهاتف" "Stopping the booking system on the phone")"
      FP1=$(python ops/fingerprint.py 2>/dev/null | tail -1)
      if [ "$FP0" = "$FP1" ]; then
        if python ops/cf.py server; then
          python -c "import sys;sys.path.insert(0,'ops');from common import stamp;print('server',stamp())" > "$LOCAL_BK/db/ACTIVE_SITE.txt"
          rclone copyto "$LOCAL_BK/db/ACTIVE_SITE.txt" "$RT_REMOTE/db/ACTIVE_SITE.txt" 2>/dev/null || true
        else
          say "$(T "الكمبيوتر غير متصل — 2.iajaward.org سيبقى متوقفاً حتى يعود" "The PC is offline - 2.iajaward.org stays down until it is back")"
        fi
      else
        IAJ_NOGUARD=1 bash "$0" backup || true
        echo
        echo "$(T "  ⚠ سُجّلت حجوزات على الهاتف — على الكمبيوتر شغّل: back_to_server.bat" "  ⚠ Bookings were recorded on the phone - on the PC run: back_to_server.bat")"
      fi
      other_takeover || termux-wake-unlock 2>/dev/null || true
    }
    trap finish EXIT INT TERM
    say "$(T "تحويل 2.iajaward.org إلى الهاتف" "Switching 2.iajaward.org to the phone")"
    if python ops/cf.py phone; then
      python -c "import sys;sys.path.insert(0,'ops');from common import stamp;print('phone',stamp())" > "$LOCAL_BK/db/ACTIVE_SITE.txt"
      rclone copyto "$LOCAL_BK/db/ACTIVE_SITE.txt" "$RT_REMOTE/db/ACTIVE_SITE.txt" 2>/dev/null || true
    else
      die "$(T "لم يتم التحويل" "Switch failed")"
    fi
    say "$(T "✅ الهاتف يشغّل 2.iajaward.org — لا تغلق النافذة. للإنهاء: زر «VEN 1 - إنهاء» أو Ctrl+C" "✅ The phone serves 2.iajaward.org - keep this window. To finish: VEN 1 - Stop, or Ctrl+C")"
    while kill -0 $WEB 2>/dev/null && kill -0 $CF 2>/dev/null; do sleep 5; done
    say "$(T "توقف النظام أو النفق على الهاتف" "The system or tunnel stopped on the phone")"
    exit 0
    ;;

  stop)
    PID=$(pgrep -f "venues/standby/phone.sh takeover" | grep -v "^$$\$" | head -1)
    if [ -z "$PID" ]; then
      say "$(T "طوارئ الحجوزات ليست قيد التشغيل" "Booking emergency mode is not running")"
    else
      say "$(T "إنهاء الطوارئ… (قد يستغرق دقيقة)" "Stopping emergency mode... (may take a minute)")"
      kill -INT "$PID" 2>/dev/null || true
      for i in $(seq 1 45); do kill -0 "$PID" 2>/dev/null || break; sleep 2; done
    fi
    need_code; cd "$SITE" && python ops/cf.py status 2>/dev/null || true
    ;;

  server)
    guard; need_code
    read -r -p "$(T "  إرجاع 2.iajaward.org للكمبيوتر بدون نقل بيانات — اكتب YES: " "  Move 2.iajaward.org back to the PC without moving data - type YES: ")" OK
    [ "$OK" = "YES" ] || die "$(T "تم الإلغاء" "Cancelled")"
    cd "$SITE" && python ops/cf.py server
    ;;

  status)
    need_code; cd "$SITE" && python ops/cf.py status
    ;;

  *)
    sed -n '2,18p' "$0"
    ;;
esac
