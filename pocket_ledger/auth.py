from functools import wraps
from secrets import compare_digest, token_urlsafe

from authlib.integrations.base_client import OAuthError
from authlib.integrations.flask_client import OAuth
from joserfc.errors import JoseError
from flask import (
    Blueprint, abort, current_app, g, redirect, render_template, request,
    session, url_for,
)
from requests import RequestException

from . import db

bp = Blueprint("auth", __name__)


def init_app(app):
    oauth = OAuth(app)
    oauth.register(
        "google",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    app.extensions["pocket_ledger.oauth"] = oauth
    app.register_blueprint(bp)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user_id is None:
            return redirect(url_for("auth.index"))
        return view(*args, **kwargs)

    return wrapped


@bp.before_app_request
def load_current_user():
    g.user_id = session.get("user_id")


@bp.get("/")
def index():
    if g.user_id is not None:
        return redirect(url_for("auth.dashboard"))
    return render_template("sign_in.html")


@bp.get("/auth/google")
def sign_in():
    if not all(current_app.config[key] for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")):
        abort(503, description="Google Sign-In is not configured.")
    oauth = current_app.extensions["pocket_ledger.oauth"]
    callback = current_app.config["PUBLIC_BASE_URL"].rstrip("/") + url_for("auth.callback")
    try:
        return oauth.google.authorize_redirect(callback, nonce=token_urlsafe(32))
    except (OAuthError, RequestException):
        abort(502, description="Google Sign-In is temporarily unavailable.")


@bp.get("/auth/google/callback")
def callback():
    oauth = current_app.extensions["pocket_ledger.oauth"]
    try:
        token = oauth.google.authorize_access_token()
    except (OAuthError, JoseError, ValueError):
        abort(400, description="Google sign-in could not be verified. Start again from the sign-in page.")
    except RequestException:
        abort(502, description="Google Sign-In is temporarily unavailable.")

    claims = token.get("userinfo")
    if not token.get("id_token") or not claims or not claims.get("sub"):
        abort(400, description="Google did not return a verified identity.")

    user_id = db.upsert_google_user(
        claims["sub"], claims.get("email"), claims.get("name")
    )
    session.clear()
    session["user_id"] = user_id
    session["logout_token"] = token_urlsafe(32)
    return redirect(url_for("auth.dashboard"))


@bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@bp.post("/auth/logout")
@login_required
def sign_out():
    token = request.form.get("csrf_token", "")
    expected = session.get("logout_token")
    if not expected or not compare_digest(token.encode(), expected.encode()):
        abort(400, description="Invalid sign-out request.")
    session.clear()
    return redirect(url_for("auth.index"))
