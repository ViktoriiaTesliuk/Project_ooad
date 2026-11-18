import os

import click
from dotenv import load_dotenv
from flask import Flask, request

from . import auth, csrf, csv_import, db, views


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY"),
        PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "http://localhost:5000"),
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        GEMINI_API_KEY=os.getenv("GEMINI_API_KEY"),
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

    @app.before_request
    def limit_csv_upload():
        if request.endpoint == "csv_import.preview" and request.method == "POST":
            request.max_content_length = csv_import.MAX_BYTES + 16 * 1024

    csrf.init_app(app)
    auth.init_app(app)
    views.init_app(app)
    app.register_blueprint(csv_import.bp)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.cli.command("init-db")
    def init_db_command():
        """Apply the PostgreSQL schema migrations."""
        db.apply_initial_schema()
        click.echo("Schema migrations applied.")

    return app
