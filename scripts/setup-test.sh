#!/bin/bash
# Test-Umgebung ohne Let's-Encrypt-Zertifikat.
#
# Anders als install.sh (volle lokale Dev-Einrichtung inkl. "sudo apt install
# docker.io docker-compose") und scripts/setup-prod.sh (interaktive Produktions-
# Einrichtung, optional mit echtem Let's-Encrypt-Zertifikat) ist dieses Skript
# für eine schnelle, nicht-interaktive Test-Instanz gedacht:
#   - setzt IMMER ein selbstsigniertes Zertifikat ein, fragt nie nach einer
#     Domain und ruft nie certbot/Let's Encrypt auf
#   - erwartet, dass Docker bereits installiert ist (kein "apt install")
#   - erzeugt eine frische .env mit zufälligen Test-Zugangsdaten, damit die
#     Test-Instanz nicht die Standard-Zugangsdaten admin/admin verwendet
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Rechnungs-App: Test-Umgebung (ohne Let's Encrypt) ==="
echo

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker wurde nicht gefunden. Bitte zuerst Docker installieren"
  echo "(dieses Skript installiert es bewusst nicht, siehe install.sh dafür)."
  exit 1
fi

echo "Erzeuge selbstsigniertes Zertifikat..."
mkdir -p nginx/certs
openssl req -x509 -nodes -days 30 -newkey rsa:2048 \
  -keyout nginx/certs/localhost.key -out nginx/certs/localhost.crt \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost"
echo "✓ Selbstsigniertes Zertifikat erzeugt (30 Tage gültig, nur für Tests)."

echo
echo "Richte .env für die Test-Umgebung ein..."
cp .env.example .env

rand() { openssl rand -hex "$1" 2>/dev/null || head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
_set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i.tmp "s#^${key}=.*#${key}=${value}#" .env && rm -f .env.tmp
  else
    echo "${key}=${value}" >> .env
  fi
}

_set_env SESSION_SECRET "$(rand 32)"
_set_env POSTGRES_PASSWORD "test_$(rand 8)"
_set_env ADMIN_USER "admin"
ADMIN_PASSWORD="test_$(rand 6)"
_set_env ADMIN_PASSWORD "$ADMIN_PASSWORD"
_set_env APP_USERS "tester:tester123"
echo "✓ .env mit zufälligen Test-Zugangsdaten erzeugt."
echo "  Admin-Login: admin / $ADMIN_PASSWORD"
echo "  (E-Mail-Versand bleibt über MailHog, kein echter SMTP-Versand.)"

echo
echo "Baue und starte die Docker-Container..."
sudo docker-compose up -d --build
echo "✓ Test-Umgebung läuft."

echo
echo "Fertig. Aufrufbar unter https://localhost (Browser-Warnung wegen"
echo "selbstsigniertem Zertifikat ist erwartet). MailHog-UI: http://localhost:8025"
echo "Diese Instanz ist NICHT für Produktivbetrieb gedacht (kein echtes"
echo "TLS-Zertifikat, kein echter E-Mail-Versand) – dafür scripts/setup-prod.sh nutzen."
