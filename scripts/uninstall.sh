#!/bin/bash
# Deinstallation der Rechnungs-App.
#
# Der erste Schritt – Container und Netz weg – laeuft immer. Alles Weitere
# loescht Daten und wird deshalb einzeln abgefragt: Datenbank-Volume, Backups,
# Logs, Zertifikate, gebaute Images, Host-Benutzer.
#
#   scripts/uninstall.sh              gestuft, mit Rueckfrage je Schritt
#   scripts/uninstall.sh --all        alles, ohne Rueckfrage
#   scripts/uninstall.sh --dry-run    nur anzeigen, was passieren wuerde
#   scripts/uninstall.sh --keep-data  Container/Images weg, Daten bleiben
#
# --dry-run laesst sich mit den anderen Schaltern kombinieren.

# Mit einer anderen Shell gestartet – etwa `zsh scripts/uninstall.sh`? Dann
# einmal mit bash neu starten. Das Skript nutzt bash-Syntax (read -p, [[ =~ ]]),
# und unter zsh brach es vorher mit einer kryptischen Meldung ab
# ("BASH_SOURCE[0]: parameter not set"), noch dazu bei einem Skript, das loescht.
if [ -z "${BASH_VERSION:-}" ]; then
  if ! command -v bash >/dev/null 2>&1; then
    echo "Dieses Skript braucht bash, die hier aber fehlt." >&2
    exit 1
  fi
  exec bash "$0" "$@"
fi

set -euo pipefail

# $0 statt BASH_SOURCE, wie in setup-test.sh und setup-prod.sh: das gibt es in
# jeder Shell und ueberlebt damit auch einen Aufruf ueber `sh`.
cd "$(dirname "$0")/.."
PROJECT_DIR="$PWD"

# shellcheck source=lib-common.sh
source scripts/lib-common.sh

ASSUME_YES=0
DRY_RUN=0
KEEP_DATA=0

for arg in "$@"; do
  case "$arg" in
    --all)       ASSUME_YES=1 ;;
    --dry-run)   DRY_RUN=1 ;;
    --keep-data) KEEP_DATA=1 ;;
    -h|--help)
      # Der Kopf der Datei bis zur ersten Leerzeile – so verrutscht die Hilfe
      # nicht, sobald oben eine Zeile dazukommt.
      awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
      exit 0 ;;
    *)
      echo "Unbekannter Schalter: $arg (--help zeigt die Moeglichkeiten)" >&2
      exit 2 ;;
  esac
done

if [ "$ASSUME_YES" -eq 1 ] && [ "$KEEP_DATA" -eq 1 ]; then
  echo "--all und --keep-data widersprechen sich." >&2
  exit 2
fi

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@";
  else docker-compose "$@"; fi
}

run() {
  # Fuehrt einen Schritt aus – oder zeigt ihn bei --dry-run nur an. Ein
  # fehlgeschlagener Schritt beendet die Deinstallation nicht: sonst bliebe
  # bei einem einzigen Stolperstein (Docker laeuft nicht, sudo fragt nach
  # einem Passwort) alles Nachfolgende ungetan, ohne dass es jemand merkt.
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "   [dry-run] $*"
    return 0
  fi
  if ! "$@"; then
    echo "   ! Schritt fehlgeschlagen: $*"
    return 1
  fi
}

remove_path() {
  # Loeschen ohne sudo versuchen und erst danach mit. Nach scripts/setup-*.sh
  # gehoeren die Dateien dem Dienstbenutzer und brauchen sudo – vorher nicht,
  # und dann soll auch keine Passwortabfrage kommen.
  local path="$1"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "   [dry-run] rm -rf $path"
    return 0
  fi
  rm -rf "$path" 2>/dev/null && return 0
  if _can_admin && _sudo rm -rf "$path" 2>/dev/null; then return 0; fi
  echo "   ! $path liess sich nicht loeschen – fehlende Rechte. Von Hand:"
  echo "     sudo rm -rf $path"
  return 1
}

# ask FRAGE – 0 (ja) oder 1 (nein). --all sagt immer ja, --keep-data immer
# nein; ohne Terminal (Skript in einer Pipeline) gilt ebenfalls nein, damit
# ein unbeaufsichtigter Lauf nichts loescht, was niemand bestaetigt hat.
ask() {
  if [ "$KEEP_DATA" -eq 1 ]; then echo "   uebersprungen (--keep-data): $1"; return 1; fi
  if [ "$ASSUME_YES" -eq 1 ]; then echo "   ja (--all): $1"; return 0; fi
  if [ ! -t 0 ]; then echo "   uebersprungen (kein Terminal): $1"; return 1; fi
  local answer
  read -r -p "   $1 [j/N] " answer
  [[ "$answer" =~ ^([jJ]|[yY])$ ]]
}

echo "Deinstallation der Rechnungs-App in $PROJECT_DIR"
[ "$DRY_RUN" -eq 1 ] && echo "(--dry-run: es wird nichts veraendert)"
echo

# --- 1. Container und Netz (laeuft immer) --------------------------------
echo "1. Container stoppen und entfernen"
if [ -f docker-compose.yml ]; then
  run compose down --remove-orphans || true
else
  echo "   ! docker-compose.yml nicht gefunden – uebersprungen."
fi
echo

# --- 2. Datenbank-Volume --------------------------------------------------
echo "2. Datenbank"
if ask "Datenbank-Volume db_data loeschen? ALLE Rechnungen sind dann weg."; then
  run compose down -v --remove-orphans && echo "   ✓ Volume entfernt." || true
fi
echo

# --- 3. Backups -----------------------------------------------------------
echo "3. Backups ($PROJECT_DIR/backups)"
if [ -d backups ] && [ -n "$(ls -A backups 2>/dev/null)" ]; then
  echo "   $(find backups -maxdepth 1 -type f | wc -l | tr -d ' ') Dateien, $(du -sh backups 2>/dev/null | cut -f1)"
  if ask "Backups loeschen?"; then
    remove_path "$PROJECT_DIR/backups" && echo "   ✓ Backups entfernt." || true
  fi
else
  echo "   keine Backups vorhanden."
fi
echo

# --- 4. Logs --------------------------------------------------------------
echo "4. Logs ($PROJECT_DIR/logs)"
if [ -d logs ]; then
  if ask "Log-Verzeichnisse loeschen?"; then
    remove_path "$PROJECT_DIR/logs" && echo "   ✓ Logs entfernt." || true
  fi
else
  echo "   kein logs-Verzeichnis vorhanden."
fi
echo

# --- 5. Zertifikate -------------------------------------------------------
echo "5. TLS-Zertifikate ($PROJECT_DIR/nginx/certs)"
if [ -d nginx/certs ]; then
  if ask "Zertifikate loeschen?"; then
    remove_path "$PROJECT_DIR/nginx/certs" && echo "   ✓ Zertifikate entfernt." || true
  fi
else
  echo "   keine Zertifikate vorhanden."
fi
echo

# --- 6. Images ------------------------------------------------------------
echo "6. Gebaute Images"
if ask "Das selbst gebaute App-Image entfernen (nginx/postgres/mailhog bleiben)?"; then
  # Der Name entsteht aus dem Verzeichnisnamen: <projekt>-app bzw. <projekt>_app.
  project="$(basename "$PROJECT_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
  for image in "${project}-app" "${project}_app" "${project}-web" "${project}_web"; do
    if docker image inspect "$image" >/dev/null 2>&1; then
      run docker image rm "$image" && echo "   ✓ $image entfernt." || true
    fi
  done
fi
echo

# --- 7. .env --------------------------------------------------------------
echo "7. Konfiguration (.env)"
if [ -f .env ]; then
  if ask ".env loeschen? Enthaelt Passwoerter und das Sitzungsgeheimnis."; then
    remove_path "$PROJECT_DIR/.env" && echo "   ✓ .env entfernt." || true
  fi
else
  echo "   keine .env vorhanden."
fi
echo

# --- 8. Host-Benutzer -----------------------------------------------------
echo "8. Dienstbenutzer '$APP_USER'"
if id "$APP_USER" >/dev/null 2>&1; then
  if ask "Benutzer '$APP_USER' vom System entfernen?"; then
    if command -v userdel >/dev/null 2>&1; then
      run _sudo userdel "$APP_USER" && echo "   ✓ Benutzer entfernt." || true
    elif command -v deluser >/dev/null 2>&1; then
      run _sudo deluser "$APP_USER" && echo "   ✓ Benutzer entfernt." || true
    else
      echo "   ! Weder userdel noch deluser gefunden."
    fi
  fi
else
  echo "   Benutzer '$APP_USER' existiert nicht."
fi
echo

echo "Fertig. Die Projektdateien selbst (Quellcode, docker-compose.yml) sind"
echo "unangetastet geblieben – sie loescht man mit 'rm -rf $PROJECT_DIR'."
