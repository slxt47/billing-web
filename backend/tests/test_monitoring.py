"""Monitoring: Kennzahlen, Prometheus-Ausgabe und Alarme."""
import pytest

from app import config, monitoring
from conftest import ADMIN, USER, do_login, make_invoice


@pytest.fixture(autouse=True)
def fresh_counters():
    monitoring.reset()
    yield
    monitoring.reset()


def test_metrics_need_an_admin(user_client):
    assert user_client.get("/api/admin/metrics").status_code == 403
    assert user_client.get("/api/admin/metrics.prom").status_code == 403


def test_metrics_count_requests_and_documents(admin_client):
    make_invoice(admin_client)
    data = admin_client.get("/api/admin/metrics").json()

    assert data["requests_total"] > 0
    assert data["responses"]["2xx"] > 0
    assert data["documents"]["invoices"] == 1
    assert data["documents"]["credit_notes"] == 0
    assert data["uptime_seconds"] >= 0
    assert data["avg_response_seconds"] >= 0


def test_metrics_count_client_errors(admin_client):
    admin_client.get("/api/invoices/999")          # 404
    data = admin_client.get("/api/admin/metrics").json()
    assert data["responses"]["4xx"] >= 1
    assert data["server_errors_total"] == 0


def test_failed_logins_are_visible(client):
    client.get("/login")
    for _ in range(3):
        do_login(client, "tester", "falsch")
    do_login(client, *ADMIN)

    data = client.get("/api/admin/metrics").json()
    assert data["failed_logins_in_window"] >= 3


def test_prometheus_format(admin_client):
    body = admin_client.get("/api/admin/metrics.prom").text
    assert body.startswith("# HELP rechnung_uptime_seconds")
    assert "rechnung_requests_total " in body
    assert 'rechnung_responses_total{class="2xx"}' in body
    assert 'rechnung_documents{kind="invoices"}' in body


def test_alerts_trigger_on_many_server_errors(monkeypatch):
    monkeypatch.setattr(config, "ALERT_ERROR_THRESHOLD", 3)
    monkeypatch.setattr(config, "ALERT_BACKUP_MAX_AGE_HOURS", 0)
    monkeypatch.setattr(config, "ALERT_LOGIN_THRESHOLD", 0)

    assert monitoring.pending_alerts() == []
    for _ in range(3):
        monitoring.record_request("GET", "/api/kaputt", 500, 0.01)
    kinds = [kind for kind, _ in monitoring.pending_alerts()]
    assert kinds == ["errors"]


def test_alerts_trigger_on_failed_logins(monkeypatch):
    monkeypatch.setattr(config, "ALERT_LOGIN_THRESHOLD", 2)
    monkeypatch.setattr(config, "ALERT_ERROR_THRESHOLD", 0)
    monkeypatch.setattr(config, "ALERT_BACKUP_MAX_AGE_HOURS", 0)

    monitoring.record_login_failure("tester")
    assert monitoring.pending_alerts() == []
    monitoring.record_login_failure("tester")
    assert [k for k, _ in monitoring.pending_alerts()] == ["logins"]


def test_missing_backup_is_an_alert(monkeypatch):
    monkeypatch.setattr(config, "ALERT_ERROR_THRESHOLD", 0)
    monkeypatch.setattr(config, "ALERT_LOGIN_THRESHOLD", 0)
    monkeypatch.setattr(config, "ALERT_BACKUP_MAX_AGE_HOURS", 36)
    assert [k for k, _ in monitoring.pending_alerts()] == ["backup"]


def test_alert_mail_is_sent_once_per_cooldown(admin_client, monkeypatch):
    sent = []
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "ALERT_ERROR_THRESHOLD", 1)
    monkeypatch.setattr(config, "ALERT_LOGIN_THRESHOLD", 0)
    monkeypatch.setattr(config, "ALERT_BACKUP_MAX_AGE_HOURS", 0)
    monkeypatch.setattr("app.email_service.send_alert_email",
                        lambda to, kind, text, settings=None: sent.append((to, kind)))
    admin_client.put("/api/settings", json={"company_name": "Muster GmbH",
                                            "email": "buero@muster.example"})

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        monitoring.record_request("GET", "/api/kaputt", 500, 0.01)
        assert monitoring.check_and_notify(db) == ["errors"]
        # Innerhalb der Sperrfrist kommt keine zweite Mail.
        monitoring.record_request("GET", "/api/kaputt", 500, 0.01)
        assert monitoring.check_and_notify(db) == []
    finally:
        db.close()
    assert sent == [("buero@muster.example", "errors")]


def test_alerts_stay_quiet_when_disabled(admin_client, monkeypatch):
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "ALERT_ERROR_THRESHOLD", 1)
    monitoring.record_request("GET", "/api/kaputt", 500, 0.01)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        assert monitoring.check_and_notify(db) == []
    finally:
        db.close()


def test_test_alert_needs_a_company_email(admin_client, monkeypatch):
    admin_client.put("/api/settings", json={"company_name": "Muster GmbH", "email": ""})
    assert admin_client.post("/api/admin/metrics/test-alert").status_code == 400

    sent = []
    monkeypatch.setattr("app.email_service.send_alert_email",
                        lambda to, kind, text, settings=None: sent.append(to))
    admin_client.put("/api/settings", json={"company_name": "Muster GmbH",
                                            "email": "buero@muster.example"})
    assert admin_client.post("/api/admin/metrics/test-alert").json()["sent"] is True
    assert sent == ["buero@muster.example"]
