#!/bin/sh
# One-time set-up of a cloud server (AWS Lightsail Ubuntu, later the Kolkata server) for CTDMS.
# Run as root from the project folder copied to the server, e.g.:
#   sudo sh deploy/SETUP_CLOUD.sh ctdms.example.in
# It installs Docker and automatic security updates, writes .env with fresh secrets (only if .env does not exist yet),
# and starts PostgreSQL + the app + backups + Caddy (automatic HTTPS on the domain). It never creates a user account:
# afterwards create the first Admin with:  docker compose exec app python -m app.cli create-admin
set -eu
DOMAIN="${1:?usage: sudo sh deploy/SETUP_CLOUD.sh <domain name pointed at this server>}"
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
cd "$(dirname "$0")/.."

echo "1/4 Installing Docker and automatic security updates..."
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io docker-compose-v2 unattended-upgrades openssl
systemctl enable --now docker
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "2/4 Writing .env..."
if [ -f .env ]; then
  echo "    .env exists - kept as it is."
else
  cp .env.example .env
  sed -i \
    -e "s|^CTDMS_SECRET_KEY=.*|CTDMS_SECRET_KEY=$(openssl rand -hex 32)|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" \
    -e "s|^CTDMS_SESSION_COOKIE_SECURE=.*|CTDMS_SESSION_COOKIE_SECURE=true|" \
    -e "s|^CTDMS_SHOW_DEMO_LOGINS=.*|CTDMS_SHOW_DEMO_LOGINS=false|" \
    -e "s|^CTDMS_DOMAIN=.*|CTDMS_DOMAIN=$DOMAIN|" \
    -e "s|^FORWARDED_ALLOW_IPS=.*|FORWARDED_ALLOW_IPS=172.16.0.0/12|" \
    .env
  # The app port is reachable only from this machine; the internet comes in through Caddy (ports 80/443).
  echo "CTDMS_BIND=127.0.0.1" >> .env
  echo "CTDMS_ADMIN_2FA=true" >> .env   # Administrators sign in with a phone code
  chmod 600 .env
  echo "    .env written (secrets generated; keep a copy of this file somewhere safe)."
fi

echo "3/4 Starting CTDMS (database, app, backups every 4 hours, HTTPS)..."
mkdir -p backups
docker compose --profile https up -d --build

echo "4/4 Checking..."
sleep 20
docker compose ps
echo
echo "Done. Next:"
echo "  - In the Lightsail firewall allow only ports 22 (SSH), 80 and 443."
echo "  - Create the first Admin:  docker compose exec app python -m app.cli create-admin"
echo "  - Open https://$DOMAIN"
