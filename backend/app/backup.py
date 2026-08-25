"""Zugriff auf die automatischen DB-Backups (siehe docker-compose.yml,
Service `backup`: täglicher pg_dump nach /backups, 14 Tage Aufbewahrung).

Diese App liest diesen Ordner nur, um Admins eine Liste vorhandener Backups
und eine Wiederherstellungs-Funktion anzubieten.
"""
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import config
from .database import DATABASE_URL

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.sql\.gz$")


def list_backups() -> list[dict]:
    d = Path(config.BACKUP_DIR)
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime),
        }
        for f in files
    ]


def _resolve(filename: str) -> Path:
    """Verhindert Path-Traversal: nur exakte, bekannte Dateinamen im
    Backup-Ordner werden akzeptiert."""
    if not _NAME_RE.match(filename):
        raise ValueError("Ungültiger Dateiname")
    path = Path(config.BACKUP_DIR) / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path


def read_backup(filename: str) -> bytes:
    return _resolve(filename).read_bytes()


def restore_backup(filename: str) -> None:
    """Spielt ein Backup über `psql` in die aktuelle Datenbank ein
    (überschreibt bestehende Daten in den betroffenen Tabellen)."""
    path = _resolve(filename)
    cmd = f"gunzip -c {path} | psql \"{DATABASE_URL}\""
    result = subprocess.run(
        ["sh", "-c", cmd],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] or "Wiederherstellung fehlgeschlagen")
