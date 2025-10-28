#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DOMAIN="${DOMAIN:-eus.lat}"
PORT="${PORT:-8000}"

if [ ! -f "$APP_DIR/.env" ]; then
  echo ".env not found in $APP_DIR" >&2
  exit 1
fi

set -a
source "$APP_DIR/.env"
set +a

if [ -z "${TG_BOT_TOKEN:-}" ]; then
  echo "TG_BOT_TOKEN is missing in .env" >&2
  exit 1
fi

sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip \
  nginx curl \
  certbot python3-certbot-nginx

VENV_DIR="$APP_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

APP_USER="$(stat -c %U "$APP_DIR")"
APP_GROUP="$(stat -c %G "$APP_DIR")"

SERVICE_FILE="/etc/systemd/system/leads-bot.service"
sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Leads Telegram Bot (aiohttp)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/python $APP_DIR/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now leads-bot.service

NGINX_SITE="/etc/nginx/sites-available/leads-bot"
sudo tee "$NGINX_SITE" >/dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 25m;

    location /tg/ {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }
}
EOF

if [ -f /etc/nginx/sites-enabled/default ]; then
  sudo rm -f /etc/nginx/sites-enabled/default
fi
sudo ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/leads-bot
sudo nginx -t
sudo systemctl restart nginx

if [ -n "${EMAIL:-}" ]; then
  sudo certbot --nginx --agree-tos -m "$EMAIL" -d "$DOMAIN" --non-interactive --redirect
else
  sudo certbot --nginx --agree-tos --register-unsafely-without-email -d "$DOMAIN" --non-interactive --redirect
fi

WEBHOOK_URL="https://$DOMAIN/tg/$TG_BOT_TOKEN"
curl -sS -X POST "https://api.telegram.org/bot$TG_BOT_TOKEN/setWebhook" \
  -d "url=$WEBHOOK_URL" \
  -d "drop_pending_updates=true" \
  -d "allowed_updates[]=message" \
  -d "allowed_updates[]=edited_message" \
  | sed 's/.*/Webhook response: &/'

echo "Done. Service: leads-bot. Webhook -> $WEBHOOK_URL"


