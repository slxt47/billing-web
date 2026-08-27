#!/bin/bash
# Gemeinsame Helfer für scripts/setup-test.sh und scripts/setup-prod.sh.
# Wird per `source` eingebunden, nicht direkt ausgeführt.
#
# Enthält u. a. das Anlegen des dedizierten Dienstbenutzers: weder die
# Anwendungsdateien auf dem Host noch der Prozess im Container sollen root
# gehören bzw. als root laufen.

APP_USER="${APP_USER:-rechnung}"

# --- .env schreiben -------------------------------------------------------
_set_env() {
  # _set_env KEY VALUE – ersetzt/ergänzt KEY=... in .env
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i.tmp "s#^${key}=.*#${key}=${value}#" .env && rm -f .env.tmp
  else
    echo "${key}=${value}" >> .env
  fi
}

rand() { openssl rand -hex "$1" 2>/dev/null || head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# --- Rechte ---------------------------------------------------------------
_sudo() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

_can_admin() {
  [ "$(id -u)" -eq 0 ] || command -v sudo >/dev/null 2>&1
}

# Legt den Dienstbenutzer an (idempotent). Rückgabe != 0, wenn das nicht
# möglich war – die Einrichtung läuft dann trotzdem weiter, nur eben ohne
# eigenen Benutzer.
ensure_app_user() {
  if id "$APP_USER" >/dev/null 2>&1; then
    echo "✓ Dienstbenutzer '$APP_USER' ist bereits vorhanden."
    return 0
  fi
  if ! _can_admin; then
    echo "! Ohne root/sudo kann '$APP_USER' nicht angelegt werden – übersprungen."
    return 1
  fi
  if command -v useradd >/dev/null 2>&1; then
    _sudo groupadd --system "$APP_USER" 2>/dev/null || true
    _sudo useradd --system --gid "$APP_USER" --no-create-home \
      --shell /usr/sbin/nologin --comment "Rechnungs-App" "$APP_USER" || return 1
  elif command -v adduser >/dev/null 2>&1; then   # BusyBox/Alpine
    _sudo addgroup -S "$APP_USER" 2>/dev/null || true
    _sudo adduser -S -G "$APP_USER" -H -s /sbin/nologin "$APP_USER" || return 1
  else
    echo "! Weder useradd noch adduser gefunden – Benutzer nicht angelegt."
    return 1
  fi
  echo "✓ Dienstbenutzer '$APP_USER' angelegt (Systembenutzer ohne Login-Shell)."
}

# Überträgt das Projektverzeichnis an den Dienstbenutzer und hinterlegt
# dessen UID/GID in der .env – der `web`-Container läuft damit unter
# derselben Identität (siehe docker-compose.yml: user: "${APP_UID}:${APP_GID}").
#
# Reihenfolge beachtet: erst .env schreiben, dann chown – danach gehört die
# .env dem Dienstbenutzer und wäre für den aufrufenden Benutzer schreibgeschützt.
own_app_files() {
  local dir="${1:-$PWD}" uid gid
  if ! id "$APP_USER" >/dev/null 2>&1; then
    echo "! Benutzer '$APP_USER' fehlt – Dateirechte bleiben unverändert."
    return 1
  fi
  if ! _can_admin; then
    echo "! Ohne root/sudo können die Dateirechte nicht gesetzt werden."
    return 1
  fi
  uid="$(id -u "$APP_USER")"
  gid="$(id -g "$APP_USER")"

  _set_env APP_UID "$uid"
  _set_env APP_GID "$gid"

  mkdir -p "$dir/backups"
  _sudo chown -R "$uid:$gid" "$dir"
  # Gruppenrechte, damit Administratoren über die Gruppe weiterarbeiten
  # können, ohne root zu werden. backups zusätzlich schreibbar.
  _sudo chmod -R g+rX "$dir"
  _sudo chmod -R g+rwX "$dir/backups"
  echo "✓ Anwendungsdateien gehören jetzt '$APP_USER' ($uid:$gid)."
  echo "  Der Container-Prozess läuft mit derselben UID (APP_UID/APP_GID in .env)."

  if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] \
     && command -v usermod >/dev/null 2>&1; then
    if _sudo usermod -aG "$APP_USER" "$SUDO_USER" 2>/dev/null; then
      echo "  '$SUDO_USER' wurde der Gruppe '$APP_USER' hinzugefügt"
      echo "  (wird erst nach einer neuen Anmeldung wirksam)."
    fi
  fi
}
