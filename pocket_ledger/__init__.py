import os

import click
from dotenv import load_dotenv
from flask import Flask

from . import auth, csrf, db, views


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY"),
        PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "http://localhost:5000"),
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        GOOGLE_CLIENT_ID=os.getenv("GOOGLE_CLIENT_ID"),
        GOOGLE_CLIENT_SECRET=os.getenv("GOOGLE_CLIENT_SECRET"),
        RDS_RESOURCE_ARN=os.getenv("RDS_RESOURCE_ARN"),
        RDS_SECRET_ARN=os.getenv("RDS_SECRET_ARN"),
        RDS_DATABASE=os.getenv("RDS_DATABASE", "pocket_ledger"),
    )
    if test_config is not None:
        app.config.update(test_config)
    if not app.secret_key:
        raise RuntimeError("Set SECRET_KEY before starting Pocket Ledger.")

    csrf.init_app(app)
    auth.init_app(app)
    views.init_app(app)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.cli.command("init-db")
    def init_db_command():
        """Apply the initial PostgreSQL schema."""
        db.apply_initial_schema()
        click.echo("Initial schema applied.")

    return app
