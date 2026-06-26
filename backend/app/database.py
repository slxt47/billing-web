"""Datenbank-Verbindung (SQLAlchemy) für PostgreSQL."""
import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://rechnung:rechnung_pw@db:5432/rechnung",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI-Dependency: liefert eine DB-Session und schließt sie wieder."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries: int = 10, delay: float = 2.0):
    """Wartet auf die DB und legt die Tabellen an."""
    from . import models  # noqa: F401 — registriert die Modelle bei Base

    last_err = None
    for _ in range(retries):
        try:
            Base.metadata.create_all(bind=engine)
            _migrate()
            return
        except OperationalError as err:  # DB evtl. noch nicht bereit
            last_err = err
            time.sleep(delay)
    raise last_err


# Idempotente Mini-Migration: ergänzt nachträglich neue Spalten an bestehenden
# Tabellen, ohne vorhandene Daten zu verlieren (PostgreSQL: ADD COLUMN IF NOT EXISTS).
_MIGRATIONS = [
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS skonto_percent NUMERIC(5,2) NOT NULL DEFAULT 0",
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS skonto_days INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS discount_percent NUMERIC(5,2) NOT NULL DEFAULT 0",
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS small_business BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(12,2) NOT NULL DEFAULT 0",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS payment_term_days INTEGER NOT NULL DEFAULT 14",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS skonto_percent NUMERIC(5,2) NOT NULL DEFAULT 0",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS skonto_days INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS email VARCHAR(200) DEFAULT ''",
]


def _migrate():
    with engine.begin() as conn:
        for stmt in _MIGRATIONS:
            conn.execute(text(stmt))
