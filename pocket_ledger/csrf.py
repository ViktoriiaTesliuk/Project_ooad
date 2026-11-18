"""Session-bound CSRF protection for every state-changing request.

Forms include ``<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">``.
Any POST, PUT, PATCH or DELETE without the matching token is rejected with 400.
"""

from secrets import compare_digest, token_urlsafe

from flask import abort, request, session

SESSION_KEY = "csrf_token"
FIELD_NAME = "csrf_token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_token():
    token = session.get(SESSION_KEY)
    if not token:
        token = session[SESSION_KEY] = token_urlsafe(32)
    return token


def rotate_token():
    """Issue a fresh token, e.g. right after sign-in."""
    session[SESSION_KEY] = token_urlsafe(32)
    return session[SESSION_KEY]


def init_app(app):
    app.jinja_env.globals["csrf_token"] = get_token

    @app.before_request
    def verify_csrf_token():
        if request.method not in UNSAFE_METHODS:
            return
        expected = session.get(SESSION_KEY)
        supplied = request.form.get(FIELD_NAME) or request.headers.get("X-CSRF-Token", "")
        if not expected or not compare_digest(supplied.encode(), expected.encode()):
            abort(400, description="The form expired or was not sent from Pocket Ledger. Reload the page and try again.")
