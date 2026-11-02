#!/usr/bin/env bash
# Ubuntu sunucuda kurulum / güncelleme. Kod /opt/odemetakip içinde olmalı.
#   sudo bash deploy/install.sh
set -euo pipefail
APP=/opt/odemetakip
DATA=/var/lib/odemetakip

apt-get install -y python3-venv >/dev/null

id odemetakip >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin odemetakip
mkdir -p "$DATA"
chown -R odemetakip:odemetakip "$DATA"
chmod 750 "$DATA"

if [ ! -f "$APP/.env" ]; then
  cat > "$APP/.env" <<ENV
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
COOKIE_SECURE=1
DATA_DIR=$DATA
ENV
fi
chown root:odemetakip "$APP/.env"
chmod 640 "$APP/.env"

[ -d "$APP/.venv" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

cp "$APP"/deploy/odemetakip*.service "$APP"/deploy/odemetakip*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now odemetakip-backup.timer odemetakip-notify.timer
systemctl enable odemetakip
systemctl restart odemetakip
sleep 2
systemctl --no-pager --lines=5 status odemetakip
echo
echo "Kurulum tamam. İlk kullanıcıyı oluşturmak için:"
echo "  cd $APP && sudo -u odemetakip .venv/bin/python manage.py create-user KULLANICI --name 'Ad Soyad'"
