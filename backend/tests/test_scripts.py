"""Die Shell-Skripte in scripts/.

Getestet wird in einer Kopie unter tmp_path, nie im echten Projektverzeichnis:
uninstall.sh löscht im Ernstfall Backups und Datenbank, und ein Test, der das
aus Versehen im Arbeitsverzeichnis tut, wäre schlimmer als kein Test.

Was hier nicht geprüft werden kann: ob `docker compose down` und `userdel`
tatsächlich das Richtige tun – dafür bräuchte es einen laufenden Docker-Daemon
und root. Geprüft wird deshalb, dass genau diese Schritte im Trockenlauf
angekündigt und dabei keine Dateien angefasst werden.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


@pytest.fixture
def project(tmp_path):
    """Eine Attrappe des Projektverzeichnisses mit allem, was die Skripte anfassen."""
    shutil.copytree(SCRIPTS, tmp_path / "scripts")
    shutil.copy(REPO / "docker-compose.yml", tmp_path / "docker-compose.yml")
    (tmp_path / ".env").write_text("ADMIN_PASSWORD=geheim\n", encoding="utf-8")
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "rechnung_20260101.sql.gz").write_bytes(b"x")
    (tmp_path / "logs" / "app").mkdir(parents=True)
    (tmp_path / "logs" / "app" / "app.log").write_text("zeile\n", encoding="utf-8")
    (tmp_path / "nginx" / "certs").mkdir(parents=True)
    (tmp_path / "nginx" / "certs" / "localhost.crt").write_text("cert", encoding="utf-8")
    return tmp_path


def run_uninstall(project, *args):
    return subprocess.run(
        ["bash", str(project / "scripts" / "uninstall.sh"), *args],
        capture_output=True, text=True, cwd=project, stdin=subprocess.DEVNULL,
        timeout=60,
    )


# --------------------------- prepare-logs.sh -----------------------------
def test_prepare_logs_creates_one_directory_per_container(tmp_path):
    result = subprocess.run(["bash", str(SCRIPTS / "prepare-logs.sh"), str(tmp_path)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr

    for service in ("app", "web", "proxy", "db", "mailhog", "backup"):
        path = tmp_path / "logs" / service
        assert path.is_dir(), f"{service} fehlt"
        # Ohne Schreibrecht für alle käme Postgres (uid 70) oder MailHog
        # (uid 1000) nicht in sein Verzeichnis hinein.
        assert path.stat().st_mode & 0o777 == 0o777


def test_prepare_logs_runs_twice_without_complaining(tmp_path):
    for _ in range(2):
        assert subprocess.run(["bash", str(SCRIPTS / "prepare-logs.sh"), str(tmp_path)],
                              capture_output=True, timeout=60).returncode == 0


# --------------------------- uninstall.sh --------------------------------
def test_uninstall_dry_run_touches_nothing(project):
    result = run_uninstall(project, "--dry-run", "--all")
    assert result.returncode == 0, result.stderr

    assert (project / ".env").exists()
    assert (project / "backups" / "rechnung_20260101.sql.gz").exists()
    assert (project / "logs" / "app" / "app.log").exists()
    assert (project / "nginx" / "certs" / "localhost.crt").exists()
    assert "[dry-run]" in result.stdout


def test_dry_run_announces_every_step(project):
    out = run_uninstall(project, "--dry-run", "--all").stdout
    for step in ("Container stoppen", "Datenbank", "Backups", "Logs",
                 "Zertifikate", "Images", "Konfiguration", "Dienstbenutzer"):
        assert step in out, f"Schritt „{step}\" fehlt in der Ausgabe"


def test_without_a_terminal_nothing_beyond_the_containers_is_removed(project):
    """Ein unbeaufsichtigter Lauf ohne --all darf keine Daten wegräumen –
    niemand hat die Rückfragen beantwortet."""
    out = run_uninstall(project, "--dry-run").stdout
    assert "uebersprungen (kein Terminal)" in out
    # Der Schlusssatz nennt "rm -rf" als Hinweis – gemeint ist hier der
    # angekuendigte Schritt, und der traegt immer das [dry-run]-Praefix.
    assert "[dry-run] _sudo rm" not in out
    assert "[dry-run] docker image rm" not in out


def test_keep_data_skips_the_data_steps(project):
    out = run_uninstall(project, "--dry-run", "--keep-data").stdout
    assert "uebersprungen (--keep-data)" in out
    assert "[dry-run] _sudo rm" not in out
    # Schritt 1 laeuft trotzdem: Container weg, Daten bleiben.
    assert "[dry-run] compose down --remove-orphans" in out


def test_all_and_keep_data_contradict_each_other(project):
    result = run_uninstall(project, "--all", "--keep-data")
    assert result.returncode == 2
    assert "widersprechen" in result.stderr


def test_unknown_switch_is_refused(project):
    result = run_uninstall(project, "--loesch-alles-sofort")
    assert result.returncode == 2
    assert "Unbekannter Schalter" in result.stderr


def test_help_needs_no_project(project):
    result = run_uninstall(project, "--help")
    assert result.returncode == 0
    assert "--dry-run" in result.stdout


def test_help_survives_new_lines_at_the_top(project):
    """Die Hilfe schneidet den Dateikopf über awk aus, nicht über feste
    Zeilennummern – sonst verrutscht sie, sobald oben etwas dazukommt."""
    script = project / "scripts" / "uninstall.sh"
    lines = script.read_text(encoding="utf-8").split("\n")
    lines.insert(1, "# Eine zusaetzliche Kopfzeile.")
    script.write_text("\n".join(lines), encoding="utf-8")

    out = run_uninstall(project, "--help").stdout
    assert "Eine zusaetzliche Kopfzeile." in out
    assert "--keep-data" in out, "die Hilfe endet nicht vorzeitig"
    assert "set -euo pipefail" not in out, "und faengt nicht den Code mit ein"


# --------------------------- Aufruf mit fremder Shell --------------------
# `zsh scripts/uninstall.sh` erzwingt zsh statt der Shebang-Zeile. Dort gibt es
# BASH_SOURCE nicht, und das Skript brach mit „BASH_SOURCE[0]: parameter not
# set" ab, ohne lib-common.sh zu finden. Beide Skripte starten sich in dem Fall
# jetzt selbst mit bash neu.
#
# `sh` ist dabei die wichtigere Prüfung, deckt lokal aber unterschiedlich viel
# ab: auf einem Mac ist /bin/sh bash im POSIX-Modus, BASH_VERSION also gesetzt
# und die Weiche wird gar nicht erst betreten. Auf Ubuntu – und damit in der CI
# und auf dem Zielsystem – ist /bin/sh dash, dort läuft der echte Fall.
FOREIGN_SHELLS = ["sh", "zsh"]


@pytest.mark.parametrize("shell", FOREIGN_SHELLS)
def test_uninstall_runs_under_a_foreign_shell(project, shell):
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} ist hier nicht installiert")
    result = subprocess.run(
        [shell, "scripts/uninstall.sh", "--dry-run", "--all"],
        capture_output=True, text=True, cwd=project,
        stdin=subprocess.DEVNULL, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "BASH_SOURCE" not in result.stderr
    assert "lib-common.sh" not in result.stderr
    # Der Lauf muss auch inhaltlich durchkommen, nicht nur ohne Fehlercode enden.
    assert "Dienstbenutzer" in result.stdout


@pytest.mark.parametrize("shell", FOREIGN_SHELLS)
def test_prepare_logs_runs_under_a_foreign_shell(tmp_path, shell):
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} ist hier nicht installiert")
    result = subprocess.run(
        [shell, str(SCRIPTS / "prepare-logs.sh"), str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "logs" / "app").is_dir()


def test_scripts_do_not_rely_on_bash_source():
    """setup-test.sh und setup-prod.sh finden sich seit jeher über $0. Die
    neuen Skripte machen es genauso – BASH_SOURCE gäbe es unter zsh nicht."""
    for name in ("uninstall.sh", "prepare-logs.sh"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        # Nur Code, keine Kommentare: die Kommentare zitieren die alte
        # Fehlermeldung und sollen das auch weiterhin dürfen.
        code = [ln for ln in text.split("\n") if not ln.lstrip().startswith("#")]
        assert not any("BASH_SOURCE" in ln for ln in code), \
            f"{name} greift wieder auf BASH_SOURCE zu"
        assert 'exec bash "$0" "$@"' in text, f"{name} startet sich nicht mit bash neu"
