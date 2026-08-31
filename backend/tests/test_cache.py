"""Response-Cache (cache.py): Dashboard und Auswertungen kommen innerhalb der
TTL aus dem Prozessspeicher, ein Schreibzugriff auf Rechnungen/Gutschriften
leert ihn, alles andere bleibt ungecacht."""
import time

from app import cache, config
from conftest import make_invoice


def test_stats_is_served_from_cache_on_the_second_call(user_client):
    first = user_client.get("/api/stats")
    assert first.headers["x-cache"] == "MISS"

    second = user_client.get("/api/stats")
    assert second.headers["x-cache"] == "HIT"
    assert second.json() == first.json()


def test_creating_an_invoice_invalidates_the_cache(user_client):
    before = user_client.get("/api/stats")
    assert before.json()["invoice_count"] == 0
    assert user_client.get("/api/stats").headers["x-cache"] == "HIT"

    make_invoice(user_client)

    after = user_client.get("/api/stats")
    assert after.headers["x-cache"] == "MISS"
    assert after.json()["invoice_count"] == 1


def test_a_credit_note_also_invalidates_the_cache(user_client):
    invoice = make_invoice(user_client)
    user_client.get("/api/stats")  # cachen

    user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={"reason": "Kulanz"})

    assert user_client.get("/api/stats").headers["x-cache"] == "MISS"


def test_locking_an_invoice_does_not_invalidate_the_cache(user_client):
    """Die Bearbeitungssperre läuft unter /api/invoices/{id}/lock – sie
    ändert nichts an Umsatz oder Steuerlast, ein Heartbeat alle 90 s soll den
    Cache also nicht andauernd leeren, auch wenn der Pfad mit /api/invoices
    beginnt."""
    invoice = make_invoice(user_client)
    user_client.get("/api/stats")
    assert user_client.get("/api/stats").headers["x-cache"] == "HIT"

    user_client.post(f"/api/invoices/{invoice['id']}/lock")

    assert user_client.get("/api/stats").headers["x-cache"] == "HIT"


def test_lists_are_not_cached(user_client):
    """Beleg- und Stammdatenlisten bleiben live: dort arbeiten mehrere
    Benutzer über Anwesenheitsanzeige/Bearbeitungssperre zusammen."""
    response = user_client.get("/api/customers")
    assert "x-cache" not in {k.lower() for k in response.headers}


def test_reports_are_cached_per_period(user_client):
    period_a = {"from": "2026-01-01", "to": "2026-12-31"}
    period_b = {"from": "2025-01-01", "to": "2025-12-31"}

    assert user_client.get("/api/reports/vat", params=period_a).headers["x-cache"] == "MISS"
    assert user_client.get("/api/reports/vat", params=period_a).headers["x-cache"] == "HIT"
    # Ein anderer Zeitraum ist ein eigener Cache-Eintrag, kein Treffer.
    assert user_client.get("/api/reports/vat", params=period_b).headers["x-cache"] == "MISS"


def test_csv_download_keeps_its_filename_on_a_cache_hit(user_client):
    period = {"from": "2026-01-01", "to": "2026-12-31"}
    first = user_client.get("/api/reports/vat.csv", params=period)
    second = user_client.get("/api/reports/vat.csv", params=period)
    assert second.headers["x-cache"] == "HIT"
    assert second.headers["content-disposition"] == first.headers["content-disposition"]
    assert second.content == first.content


def test_expired_entries_are_recomputed(user_client):
    user_client.get("/api/stats")
    # Statt einer echten Wartezeit: den einzigen Eintrag künstlich altern lassen.
    key = next(iter(cache._store))
    expires, status, body, media_type, headers = cache._store[key]
    cache._store[key] = (time.monotonic() - 1, status, body, media_type, headers)

    assert user_client.get("/api/stats").headers["x-cache"] == "MISS"


def test_cache_can_be_switched_off(user_client, monkeypatch):
    monkeypatch.setattr(config, "CACHE_ENABLED", False)
    user_client.get("/api/stats")
    response = user_client.get("/api/stats")
    assert "x-cache" not in {k.lower() for k in response.headers}
