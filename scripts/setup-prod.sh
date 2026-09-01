#!/bin/bash
# Geführte Produktions-Einrichtung.
#
# Ergänzt install.sh (das nur eine lokale Dev-Umgebung mit selbstsigniertem
# Zertifikat und Test-Zugangsdaten aufsetzt) um die Schritte, die für einen
# echten Produktivbetrieb nötig sind:
#   - starke, zufällige Geheimnisse statt der Beispielwerte aus .env.example
#   - echter SMTP-Versand statt MailHog
#   - echtes Let's-Encrypt-Zertifikat statt selbstsigniert (falls certbot
#     verfügbar ist und eine öffentlich erreichbare Domain angegeben wird)
#   - einen dedizierten Dienstbenutzer `rechnung`, dem die Anwendungsdateien
#     gehören und unter dem der Container-Prozess läuft (statt root)
#
# Das Skript ist interaktiv, ändert nichts ohne Rückfrage und legt vor dem
# Überschreiben immer eine Sicherung der bestehenden .env an.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/lib-common.sh
. "$(dirname "$0")/lib-common.sh"

echo "=== Rechnungs-App: Produktions-Einrichtung ==="
echo

if [ -f .env ]; then
  cp .env ".env.bak.$(date +%Y%m%d_%H%M%S)"
  echo "Bestehende .env gesichert."
else
  cp .env.example .env
  echo ".env aus .env.example angelegt."
fi

# --- 1) Starke Geheimnisse ------------------------------------------------
echo
echo "-- Geheimnisse --"
SESSION_SECRET="$(rand 32)"
_set_env SESSION_SECRET "$SESSION_SECRET"
echo "✓ Neues SESSION_SECRET generiert."

POSTGRES_PASSWORD="$(rand 16)"
_set_env POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
echo "✓ Neues Datenbank-Passwort generiert."
echo "  (Bei einer bereits laufenden DB muss das Passwort dort ebenfalls geändert werden!)"

read -rp "Admin-Benutzername [admin]: " ADMIN_USER
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="$(openssl rand -base64 18 2>/dev/null || rand 16)"
_set_env ADMIN_USER "$ADMIN_USER"
_set_env ADMIN_PASSWORD "$ADMIN_PASSWORD"
echo "✓ Admin-Account: $ADMIN_USER / $ADMIN_PASSWORD"
echo "  Bitte sicher notieren – wird nur beim allerersten Start in die DB übernommen."

# --- 1b) Sicherheitseinstellungen für den Produktivbetrieb ---------------
# Die App ist in Produktion nur über den HTTPS-Proxy erreichbar, daher darf
# das Secure-Flag auf Sitzungs- und CSRF-Cookie gesetzt werden.
_set_env SESSION_HTTPS_ONLY "true"
_set_env CSRF_ENABLED "true"
_set_env HSTS_ENABLED "true"
echo "✓ Cookies auf 'secure', CSRF-Schutz und HSTS aktiviert."

# Alarm-Mails: im Produktivbetrieb sinnvoll, sobald in den Firmendaten eine
# E-Mail-Adresse hinterlegt ist (dorthin gehen sie).
_set_env ALERTS_ENABLED "true"
echo "✓ Betriebsalarme aktiviert (gehen an die Firmen-E-Mail aus den Firmendaten)."

# --- 2) Echter SMTP-Versand ------------------------------------------------
echo
echo "-- E-Mail-Versand --"
read -rp "SMTP-Host für echten Versand (leer = MailHog/Test beibehalten): " SMTP_HOST
if [ -n "$SMTP_HOST" ]; then
  read -rp "SMTP-Port [587]: " SMTP_PORT
  SMTP_PORT="${SMTP_PORT:-587}"
  read -rp "SMTP-Benutzername: " SMTP_USER
  read -rsp "SMTP-Passwort: " SMTP_PASSWORD; echo
  read -rp "Absenderadresse (MAIL_FROM): " MAIL_FROM
  _set_env SMTP_HOST "$SMTP_HOST"
  _set_env SMTP_PORT "$SMTP_PORT"
  _set_env SMTP_USER "$SMTP_USER"
  _set_env SMTP_PASSWORD "$SMTP_PASSWORD"
  _set_env SMTP_USE_TLS "true"
  [ -n "$MAIL_FROM" ] && _set_env MAIL_FROM "$MAIL_FROM"
  echo "✓ Echter SMTP-Versand konfiguriert."
else
  echo "– Übersprungen, MailHog bleibt aktiv (kein echter Versand)."
fi

# --- 3) Echtes TLS-Zertifikat (Let's Encrypt) -----------------------------
echo
echo "-- TLS-Zertifikat --"
read -rp "Öffentliche Domain für Let's Encrypt (leer = selbstsigniertes Zertifikat behalten): " DOMAIN
if [ -n "$DOMAIN" ]; then
  if command -v certbot >/dev/null 2>&1; then
    echo "Fordere Zertifikat für $DOMAIN via certbot an (Port 80 muss erreichbar sein)..."
    sudo certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos \
      -m "admin@${DOMAIN}" --register-unsafely-without-email || {
        echo "certbot fehlgeschlagen – es bleibt beim selbstsignierten Zertifikat."
      }
    if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
      sudo cp "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" nginx/certs/localhost.crt
      sudo cp "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" nginx/certs/localhost.key
      sudo chmod 644 nginx/certs/localhost.crt nginx/certs/localhost.key
      echo "✓ Let's-Encrypt-Zertifikat eingerichtet."
      echo "  Hinweis: Zertifikat läuft nach 90 Tagen ab – 'certbot renew' regelmäßig ausführen"
      echo "  und die Kopie nach nginx/certs/ danach erneuern."
    fi
  else
    echo "certbot ist nicht installiert. Manuell ein Zertifikat besorgen und als"
    echo "nginx/certs/localhost.crt + nginx/certs/localhost.key ablegen."
  fi
else
  echo "– Übersprungen, selbstsigniertes Zertifikat bleibt aktiv."
fi

# --- 4) Dienstbenutzer statt root -----------------------------------------
echo
echo "-- Dienstbenutzer --"
ensure_app_user || true
own_app_files "$PWD" || true

echo
echo "-- Log-Verzeichnisse --"
scripts/prepare-logs.sh "$PWD"

echo
echo "=== Fertig ==="
echo "Änderungen stehen in .env. Zum Anwenden:"
echo "  docker-compose up -d --build"
