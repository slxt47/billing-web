"""Log-Stufe: umschalten aus der Oberfläche, Ablage und Logdatei.

Die Stufe ist die einzige Einstellung, die sich zur Laufzeit ändern lässt und
einen Neustart überstehen soll – deshalb hier eigene Tests statt eines Anhängsels
in test_monitoring.py.
"""
import json
import logging

import pytest

from app import config, crud, logging_setup
from app.database import SessionLocal
from app.main import LOG_LEVEL_KEY
from conftest import ADMIN, USER, do_login


@pytest.fixture(autouse=True)
def restore_level():
    """Die Tests stellen die Stufe des laufenden Prozesses um – zurücksetzen,
    sonst hängt der Rest der Suite an einer fremden Stufe."""
    before = logging.getLogger().level
    yield
    logging.getLogger().setLevel(before)


# --------------------------- Zugriff -------------------------------------
def test_log_level_is_admin_only(user_client):
    assert user_client.get("/api/admin/log-level").status_code == 403
    assert user_client.post("/api/admin/log-level",
                            json={"level": "DEBUG"}).status_code == 403


def test_anonymous_cannot_read_the_level(client):
    assert client.get("/api/admin/log-level").status_code == 401


# --------------------------- Lesen und Setzen ----------------------------
def test_get_reports_the_running_level(admin_client):
    data = admin_client.get("/api/admin/log-level").json()
    assert data["level"] == logging.getLevelName(logging.getLogger().level)
    assert data["levels"] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert data["boot_level"] == config.LOG_LEVEL


def test_setting_a_level_takes_effect_immediately(admin_client):
    assert admin_client.post("/api/admin/log-level",
                             json={"level": "DEBUG"}).json() == {"level": "DEBUG"}
    assert logging.getLogger().level == logging.DEBUG

    admin_client.post("/api/admin/log-level", json={"level": "ERROR"})
    assert logging.getLogger().level == logging.ERROR


def test_lowercase_is_accepted(admin_client):
    assert admin_client.post("/api/admin/log-level",
                             json={"level": "warning"}).json()["level"] == "WARNING"


@pytest.mark.parametrize("bad", ["LAUT", "", "CRITICAL", "42"])
def test_unknown_levels_are_rejected(admin_client, bad):
    """CRITICAL ist bewusst dabei: als Startwert erlaubt (die Testsuite nutzt
    ihn), als Auswahl in der Oberfläche aber nicht."""
    before = logging.getLogger().level
    response = admin_client.post("/api/admin/log-level", json={"level": bad})
    assert response.status_code == 400
    assert logging.getLogger().level == before, "eine abgelehnte Stufe ändert nichts"


# --------------------------- Ablage --------------------------------------
def test_the_choice_is_stored_and_audited(admin_client):
    admin_client.post("/api/admin/log-level", json={"level": "WARNING"})

    db = SessionLocal()
    try:
        assert crud.get_app_setting(db, LOG_LEVEL_KEY) == "WARNING"
    finally:
        db.close()

    entries = admin_client.get("/api/audit-log").json()
    assert any(e["action"] == "log_level" and e["detail"] == "WARNING"
               for e in entries), "die Umstellung gehört ins Audit-Log"


def test_app_settings_are_separate_from_company_data(admin_client):
    """Die Log-Stufe darf nicht über /api/settings mitkommen – dort stehen die
    Firmendaten, die auf jedem PDF landen."""
    admin_client.post("/api/admin/log-level", json={"level": "ERROR"})
    assert "log_level" not in admin_client.get("/api/settings").json()


def test_stored_level_survives_a_restart(admin_client):
    """Beim Start liest on_startup() die abgelegte Stufe wieder ein."""
    admin_client.post("/api/admin/log-level", json={"level": "WARNING"})
    logging.getLogger().setLevel("INFO")          # so, als sei der Prozess neu

    from app.main import on_startup
    on_startup()
    assert logging.getLevelName(logging.getLogger().level) == "WARNING"


def test_a_broken_stored_level_does_not_stop_the_start(admin_client):
    db = SessionLocal()
    try:
        crud.set_app_setting(db, LOG_LEVEL_KEY, "VOELLIG-KAPUTT")
    finally:
        db.close()

    from app.main import on_startup
    on_startup()                                   # darf nicht werfen
    assert logging.getLevelName(logging.getLogger().level) in logging_setup.LEVELS + ("CRITICAL",)


# --------------------------- Logdatei ------------------------------------
def test_log_dir_gets_a_json_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path / "logs"))
    handler = logging_setup._file_handler()
    assert handler is not None
    try:
        record = logging.LogRecord("rechnung", logging.WARNING, __file__, 1,
                                   "probe", None, None)
        handler.emit(record)
        handler.flush()
        line = json.loads((tmp_path / "logs" / "app.log").read_text().splitlines()[0])
        assert line["message"] == "probe"
        assert line["level"] == "WARNING"
    finally:
        handler.close()


def test_without_log_dir_there_is_no_file_handler(monkeypatch):
    """So laufen die Tests und eine Installation ohne eingehängtes Verzeichnis."""
    monkeypatch.setattr(config, "LOG_DIR", "")
    assert logging_setup._file_handler() is None


def test_an_unwritable_log_dir_does_not_stop_the_app(tmp_path, monkeypatch, capsys):
    """Fehlende Rechte auf dem eingehängten Verzeichnis sind der wahrscheinlichste
    Fehlerfall – die App muss trotzdem starten."""
    blocked = tmp_path / "gesperrt"
    blocked.write_text("keine Datei-Ablage, sondern eine Datei")
    monkeypatch.setattr(config, "LOG_DIR", str(blocked / "unten"))

    assert logging_setup._file_handler() is None
    assert "log directory not writable" in capsys.readouterr().out
