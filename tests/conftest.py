"""Test fixtures.

Database tests run the application's real SQL against a local PostgreSQL through
a stand-in for ``pocket_ledger.db.execute`` that mimics RDS Data API responses.
Set ``TEST_DATABASE_URL`` (for example
``postgresql://postgres:test@localhost:55432/pocket_ledger_test``) to run them;
the schema of that database is dropped and recreated. Never point it at real data.
"""

import os
import re
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlparse

from pocket_ledger import create_app, csrf, db

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "001_initial.sql"
_PLACEHOLDER = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def _field(value):
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, (Decimal, date)):
        return {"stringValue": str(value)}
    return {"stringValue": str(value)}


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "SESSION_COOKIE_SECURE": False,
        "RDS_RESOURCE_ARN": "arn:test",
        "RDS_SECRET_ARN": "arn:test",
    })
    return app


@pytest.fixture
def pg(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run database tests.")
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(url, autocommit=True)
    connection.execute("DROP SCHEMA public CASCADE")
    connection.execute("CREATE SCHEMA public")
    for statement in sqlparse.split(MIGRATION.read_text(encoding="utf-8")):
        connection.execute(statement)

    def execute(sql, params=None, *, transaction_id=None):
        with connection.cursor() as cursor:
            cursor.execute(_PLACEHOLDER.sub(r"%(\1)s", sql), params or {})
            records = []
            if cursor.description is not None:
                records = [[_field(value) for value in row] for row in cursor.fetchall()]
            return {"records": records, "numberOfRecordsUpdated": max(cursor.rowcount, 0)}

    @contextmanager
    def transaction():
        with connection.transaction():
            yield "test-transaction"

    monkeypatch.setattr(db, "execute", execute)
    monkeypatch.setattr(db, "transaction", transaction)
    yield connection
    connection.close()


@pytest.fixture
def make_user(pg, app):
    counter = iter(range(1, 1000))

    def make():
        with app.app_context():
            return db.upsert_google_user(f"sub-{next(counter)}", None, None)

    return make


@pytest.fixture
def login(app):
    """Return a test client signed in as ``user_id`` with a known CSRF token."""

    def sign_in(user_id):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session[csrf.SESSION_KEY] = "token"
        client.csrf = "token"
        return client

    return sign_in
