"""Static asset URLs and friendly error pages shared by every view."""

import hashlib
from functools import cache
from pathlib import Path

from flask import Blueprint, current_app, render_template, request, url_for
from werkzeug.exceptions import HTTPException

STATIC_MAX_AGE = 365 * 24 * 60 * 60  # asset URLs carry a content hash

ERRORS = {
    400: ("Something in that request was not right",
          "Reload the page and try again."),
    403: ("You can’t open this page", "This page is not available to your account."),
    404: ("We couldn’t find that",
          "It may have been deleted, or it belongs to a different account."),
    405: ("That action isn’t available here", "Go back and use the buttons on the page."),
    413: ("That file is too large", "Choose a CSV file no larger than 128 KiB."),
    500: ("Something went wrong on our side",
          "Your change may not have been saved. Try again in a moment."),
    502: ("A connected service didn’t respond",
          "Google or the database is temporarily unavailable. Try again in a moment."),
    503: ("This feature isn’t set up yet",
          "The administrator still needs to finish configuring it."),
}


@cache
def _digest(static_folder, filename):
    path = Path(static_folder) / filename
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def static_url(filename):
    """URL for a file in ``pocket_ledger/static`` that changes when its content does."""
    return url_for("static", filename=filename, v=_digest(current_app.static_folder, filename))


pages = Blueprint("pages", __name__)


@pages.get("/privacy")
def privacy():
    return render_template("privacy.html")


@pages.get("/terms")
def terms():
    return render_template("terms.html")


def init_app(app):
    if app.config.get("SEND_FILE_MAX_AGE_DEFAULT") is None:  # Flask's default is None
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = STATIC_MAX_AGE
    app.jinja_env.globals["static_url"] = static_url
    app.register_blueprint(pages)

    # Unhandled exceptions reach this handler as InternalServerError (500) outside
    # testing, after Flask has logged the traceback.
    @app.errorhandler(HTTPException)
    def http_error(error):
        if error.code not in ERRORS or request.endpoint == "static":
            return error
        title, message = ERRORS[error.code]
        # Specific descriptions passed to abort() are clearer than the generic text.
        if error.description and error.description != type(error).description:
            message = error.description
        return render_template("error.html", code=error.code, title=title, message=message), error.code
