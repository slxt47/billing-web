#!/bin/bash
# Legt die Log-Verzeichnisse an, in die die Container schreiben.
#
# Warum ueberhaupt ein eigenes Skript: die Container laufen unter ganz
# verschiedenen Benutzern – die App unter APP_UID (Vorgabe 10001), Postgres
# unter uid 70, MailHog unter uid 1000, nginx als root. Ein einzelner
# chown passt deshalb nicht fuer alle. Die Verzeichnisse bekommen 0777; darin
# liegen ausschliesslich Logdateien, und ohne Schreibrecht startet der
# jeweilige Dienst gar nicht erst oder verliert stillschweigend sein Log.
#
# Legt Docker die Verzeichnisse stattdessen selbst beim ersten Start an,
# gehoeren sie root und nur nginx koennte hineinschreiben.
#
# Aufruf: scripts/prepare-logs.sh [projektverzeichnis]

# Mit einer anderen Shell gestartet (`zsh scripts/prepare-logs.sh`)? Einmal mit
# bash neu starten – das Skript nutzt ein bash-Array.
if [ -z "${BASH_VERSION:-}" ]; then
  if ! command -v bash >/dev/null 2>&1; then
    echo "Dieses Skript braucht bash, die hier aber fehlt." >&2
    exit 1
  fi
  exec bash "$0" "$@"
fi

set -euo pipefail

# $0 statt BASH_SOURCE, wie in setup-test.sh und setup-prod.sh.
DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"

# Ein Verzeichnis je Container, benannt wie der Dienst in docker-compose.yml.
SERVICES=(app web proxy db mailhog backup)

for service in "${SERVICES[@]}"; do
  path="$DIR/logs/$service"
  mkdir -p "$path"
  chmod 0777 "$path" 2>/dev/null \
    || echo "! $path liess sich nicht auf 0777 setzen – bitte von Hand nachziehen."
done

echo "✓ Log-Verzeichnisse bereit: $DIR/logs/{$(IFS=,; echo "${SERVICES[*]}")}"
