import os

import click
from dotenv import load_dotenv
from flask import Flask, request

from . import ai_expenses, auth, csrf, csv_import, db, demo, ui, views


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
        CONTACT_EMAIL=os.getenv("CONTACT_EMAIL"),
        AI_DAILY_LIMIT=int(os.getenv("AI_DAILY_LIMIT", "30")),
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
        elif request.endpoint == "ai.recognise_view":
            request.max_content_length = ai_expenses.MAX_IMAGE_BYTES + 64 * 1024

    ui.init_app(app)
    csrf.init_app(app)
    auth.init_app(app)
    views.init_app(app)
    ai_expenses.init_app(app)
    app.register_blueprint(csv_import.bp)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.cli.command("init-db")
    def init_db_command():
        """Apply the PostgreSQL schema migrations."""
        db.apply_initial_schema()
        click.echo("Schema migrations applied.")

    @app.cli.command("seed-demo")
    @click.argument("email")
    def seed_demo_command(email):
        """Add synthetic sample data to the empty account that signed in with EMAIL."""
        found = db.rows(db.execute(
            "SELECT id FROM users WHERE lower(email) = lower(:email)", {"email": email}
        ))
        if len(found) != 1:
            raise click.ClickException("Sign in once with that Google account first.")
        try:
            summary = demo.seed(found[0][0])
        except demo.AccountNotEmpty as error:
            raise click.ClickException(str(error)) from error
        click.echo(f"Added {summary.wallets} wallets and {summary.transactions} transactions.")

    return app
