#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAMPUS_USER="${CAMPUS_USER:-campus}"
CAMPUS_ROOT="${CAMPUS_ROOT:-/opt/campus}"
MONITOR_ROOT="${MONITOR_ROOT:-$CAMPUS_ROOT/monitor}"
SCHEDULE_ROOT="${SCHEDULE_ROOT:-$CAMPUS_ROOT/schedule}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run as root: sudo bash deploy/bootstrap.sh"
  exit 1
fi

echo "[1/7] Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  apache2-utils \
  ca-certificates \
  certbot \
  curl \
  docker.io \
  git \
  nginx \
  openssl \
  python3 \
  python3-certbot-nginx \
  python3-pip \
  python3-venv \
  rsync \
  sqlite3

if apt-cache show docker-compose-plugin >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin
elif apt-cache show docker-compose >/dev/null 2>&1; then
  apt-get install -y docker-compose
else
  echo "TODO: install Docker Compose plugin from Docker's official apt repository if needed."
fi

if command -v chromium >/dev/null 2>&1; then
  echo "chromium already installed: $(command -v chromium)"
elif apt-cache show chromium-browser >/dev/null 2>&1; then
  apt-get install -y chromium-browser
elif apt-cache show chromium >/dev/null 2>&1; then
  apt-get install -y chromium
else
  echo "TODO: install Chromium manually, then make sure 'chromium' is on PATH."
fi

if ! command -v chromium >/dev/null 2>&1; then
  if command -v chromium-browser >/dev/null 2>&1; then
    ln -sfn "$(command -v chromium-browser)" /usr/local/bin/chromium
  elif [[ -x /snap/bin/chromium ]]; then
    ln -sfn /snap/bin/chromium /usr/local/bin/chromium
  fi
fi

echo "[2/7] Creating user and directories"
if ! id "$CAMPUS_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$CAMPUS_USER"
fi
usermod -aG docker "$CAMPUS_USER" || true
install -d -o "$CAMPUS_USER" -g "$CAMPUS_USER" "$CAMPUS_ROOT" "$MONITOR_ROOT" "$SCHEDULE_ROOT" "$CAMPUS_ROOT/backup"
install -d -o "$CAMPUS_USER" -g "$CAMPUS_USER" "$MONITOR_ROOT/data" "$SCHEDULE_ROOT/data"

echo "[3/7] Creating Python virtualenvs"
python3 -m venv "$MONITOR_ROOT/.venv"
python3 -m venv "$SCHEDULE_ROOT/.venv"
chown -R "$CAMPUS_USER:$CAMPUS_USER" "$MONITOR_ROOT/.venv" "$SCHEDULE_ROOT/.venv"

if [[ -f "$MONITOR_ROOT/requirements.txt" ]]; then
  sudo -u "$CAMPUS_USER" "$MONITOR_ROOT/.venv/bin/python" -m pip install --upgrade pip
  sudo -u "$CAMPUS_USER" "$MONITOR_ROOT/.venv/bin/python" -m pip install -r "$MONITOR_ROOT/requirements.txt"
else
  echo "TODO: copy monitor repo to $MONITOR_ROOT, then run:"
  echo "  sudo -u $CAMPUS_USER $MONITOR_ROOT/.venv/bin/python -m pip install -r $MONITOR_ROOT/requirements.txt"
fi

if [[ -f "$SCHEDULE_ROOT/backend/requirements.txt" ]]; then
  sudo -u "$CAMPUS_USER" "$SCHEDULE_ROOT/.venv/bin/python" -m pip install --upgrade pip
  sudo -u "$CAMPUS_USER" "$SCHEDULE_ROOT/.venv/bin/python" -m pip install -r "$SCHEDULE_ROOT/backend/requirements.txt"
else
  echo "TODO: copy schedule repo to $SCHEDULE_ROOT, then run:"
  echo "  sudo -u $CAMPUS_USER $SCHEDULE_ROOT/.venv/bin/python -m pip install -r $SCHEDULE_ROOT/backend/requirements.txt"
fi

echo "[4/7] Installing systemd unit files"
install -m 0644 "$SCRIPT_DIR/systemd/wg-monitor.service" /etc/systemd/system/wg-monitor.service
install -m 0644 "$SCRIPT_DIR/systemd/schedule-backend.service" /etc/systemd/system/schedule-backend.service
install -m 0644 "$SCRIPT_DIR/systemd/chrome-xuexitong.service" /etc/systemd/system/chrome-xuexitong.service
systemctl daemon-reload
systemctl enable docker
systemctl restart docker

echo "[5/7] Installing nginx template"
if [[ ! -f /etc/ssl/certs/campus-selfsigned.crt || ! -f /etc/ssl/private/campus-selfsigned.key ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
    -subj "/CN=campus.local" \
    -keyout /etc/ssl/private/campus-selfsigned.key \
    -out /etc/ssl/certs/campus-selfsigned.crt
  chmod 600 /etc/ssl/private/campus-selfsigned.key
fi
install -m 0644 "$SCRIPT_DIR/nginx/campus.conf" /etc/nginx/sites-available/campus.conf
ln -sfn /etc/nginx/sites-available/campus.conf /etc/nginx/sites-enabled/campus.conf

echo "[6/7] Filesystem ownership"
chown -R "$CAMPUS_USER:$CAMPUS_USER" "$CAMPUS_ROOT"

echo "[7/7] Next manual steps"
cat <<EOF
TODO:
1. Copy both repos:
   - monitor  -> $MONITOR_ROOT
   - schedule -> $SCHEDULE_ROOT
2. Copy and edit env files:
   - $MONITOR_ROOT/.env
   - $SCHEDULE_ROOT/.env
3. Copy SQLite/profile data only while Windows services are stopped.
4. Create nginx basic auth:
   htpasswd -c /etc/nginx/.htpasswd-campus campus
5. Replace campus.example.com in /etc/nginx/sites-available/campus.conf.
6. Issue certificate after DNS points to this server:
   certbot --nginx -d your.domain.example
7. Validate nginx:
   nginx -t && systemctl reload nginx
8. Start services:
   systemctl enable --now wg-monitor.service
   systemctl enable --now schedule-backend.service
   systemctl enable --now chrome-xuexitong.service
9. Check logs:
   journalctl -u wg-monitor -f
   journalctl -u schedule-backend -f
   journalctl -u chrome-xuexitong -f
EOF
