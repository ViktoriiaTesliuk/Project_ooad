"""Function URL event translation, app secrets and the migration action."""

import base64
import json
from unittest.mock import Mock

import pytest

from pocket_ledger import lambda_handler


def event(path="/", *, method="GET", query="", headers=None, cookies=None, body=None, encoded=False):
    payload = {
        "version": "2.0", "rawPath": path, "rawQueryString": query,
        "headers": {"host": "abc.lambda-url.eu-central-1.on.aws", "x-forwarded-proto": "https", **(headers or {})},
        "requestContext": {"domainName": "abc.lambda-url.eu-central-1.on.aws",
                           "http": {"method": method, "path": path, "sourceIp": "192.0.2.1"}},
        "isBase64Encoded": encoded,
    }
    if cookies is not None:
        payload["cookies"] = cookies
    if body is not None:
        payload["body"] = body
    return payload


def echo_app(environ, start_response):
    body = environ["wsgi.input"].read(int(environ["CONTENT_LENGTH"] or 0))
    seen = {key: environ.get(key) for key in (
        "REQUEST_METHOD", "PATH_INFO", "QUERY_STRING", "HTTP_COOKIE", "CONTENT_TYPE", "HTTP_HOST",
        "SERVER_NAME", "SERVER_PORT", "wsgi.url_scheme", "REMOTE_ADDR", "HTTP_X_CUSTOM")}
    seen["body"] = base64.b64encode(body).decode()
    start_response("201 Created", [
        ("Content-Type", "application/json"), ("Set-Cookie", "a=1; Secure"), ("Set-Cookie", "b=2; HttpOnly"),
        ("Vary", "Cookie"), ("Vary", "Accept-Encoding"),
    ])
    return [json.dumps(seen).encode()]


def test_request_translation():
    raw = b"--x\r\nContent-Disposition: form-data; name=\"f\"; filename=\"a.csv\"\r\n\r\n\xff\x00data\r\n--x--\r\n"
    response = lambda_handler.call_wsgi(echo_app, event(
        "/caf%C3%A9/a%20b", method="post", query="month=2026-11&wallet=3",
        headers={"content-type": "multipart/form-data; boundary=x", "X-Custom": "yes"},
        cookies=["session=abc", "other=1"], body=base64.b64encode(raw).decode(), encoded=True,
    ))
    seen = json.loads(response["body"])
    assert response["statusCode"] == 201 and not response["isBase64Encoded"]
    assert seen["REQUEST_METHOD"] == "POST"
    assert seen["PATH_INFO"].encode("latin-1").decode("utf-8") == "/café/a b"
    assert seen["QUERY_STRING"] == "month=2026-11&wallet=3"
    assert seen["HTTP_COOKIE"] == "session=abc; other=1"
    assert seen["CONTENT_TYPE"] == "multipart/form-data; boundary=x"
    assert seen["wsgi.url_scheme"] == "https" and seen["SERVER_PORT"] == "443"
    assert seen["HTTP_HOST"] == seen["SERVER_NAME"] == "abc.lambda-url.eu-central-1.on.aws"
    assert seen["REMOTE_ADDR"] == "192.0.2.1" and seen["HTTP_X_CUSTOM"] == "yes"
    assert base64.b64decode(seen["body"]) == raw


def test_response_cookies_and_repeated_headers():
    response = lambda_handler.call_wsgi(echo_app, event(body="plain=text"))
    assert response["cookies"] == ["a=1; Secure", "b=2; HttpOnly"]
    assert "set-cookie" not in response["headers"]
    assert response["headers"]["vary"] == "Cookie, Accept-Encoding"
    assert base64.b64decode(json.loads(response["body"])["body"]) == b"plain=text"


def test_binary_and_compressed_responses_are_base64():
    def binary(environ, start_response):
        start_response("200 OK", [("Content-Type", "image/png")])
        return [b"\x89PNG\r\n"]

    def gzipped(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/html"), ("Content-Encoding", "gzip")])
        return [b"\x1f\x8b"]

    for app, raw in ((binary, b"\x89PNG\r\n"), (gzipped, b"\x1f\x8b")):
        response = lambda_handler.call_wsgi(app, event())
        assert response["isBase64Encoded"] and base64.b64decode(response["body"]) == raw


def test_flask_static_and_redirects_through_the_adapter(app):
    css = lambda_handler.call_wsgi(app, event("/static/app.css"))
    assert css["statusCode"] == 200 and css["headers"]["content-type"].startswith("text/css")
    assert not css["isBase64Encoded"] and ".rate-header" in css["body"]
    redirect = lambda_handler.call_wsgi(app, event("/dashboard"))
    assert redirect["statusCode"] == 302 and redirect["headers"]["location"] == "/"
    head = lambda_handler.call_wsgi(app, event("/health", method="HEAD"))
    assert head["statusCode"] == 200 and head["body"] == ""


def test_app_secrets_fill_unset_variables_only():
    environ = {"APP_SECRETS_ARN": "arn:aws:secretsmanager:eu-central-1:000000000000:secret:app", "SECRET_KEY": "kept"}
    client = Mock()
    client.get_secret_value.return_value = {"SecretString": json.dumps(
        {"SECRET_KEY": "ignored", "GOOGLE_CLIENT_SECRET": "from-secret", "GEMINI_API_KEY": ""})}
    factory = Mock(return_value=client)
    lambda_handler.load_app_secrets(environ, factory)
    factory.assert_called_once_with("secretsmanager")
    assert environ["SECRET_KEY"] == "kept"
    assert environ["GOOGLE_CLIENT_SECRET"] == "from-secret"
    assert "GEMINI_API_KEY" not in environ


def test_app_secrets_reject_unexpected_keys():
    client = Mock()
    client.get_secret_value.return_value = {"SecretString": json.dumps({"password": "db"})}
    with pytest.raises(RuntimeError, match="Unexpected keys"):
        lambda_handler.load_app_secrets({"APP_SECRETS_ARN": "arn"}, Mock(return_value=client))


def test_no_secret_arn_means_no_aws_call():
    factory = Mock()
    lambda_handler.load_app_secrets({}, factory)
    factory.assert_not_called()


def test_migrate_action_and_ignored_events(app, monkeypatch):
    applied = Mock()
    monkeypatch.setattr(lambda_handler, "_app", app)
    monkeypatch.setattr("pocket_ledger.db.apply_initial_schema", applied)
    assert lambda_handler.handler({"action": "migrate"}, None) == {"status": "migrated"}
    applied.assert_called_once()
    assert lambda_handler.handler({"source": "aws.events"}, None)["status"] == "ignored"
    assert lambda_handler.handler(event("/health"), None)["statusCode"] == 200


def test_public_legal_pages(app):
    for path, heading in (("/privacy", "Privacy Policy"), ("/terms", "Terms of Service")):
        page = lambda_handler.call_wsgi(app, event(path))
        assert page["statusCode"] == 200 and f"<h1>{heading}</h1>" in page["body"]
    home = lambda_handler.call_wsgi(app, event("/"))["body"]
    assert 'href="/privacy"' in home and 'href="/terms"' in home
    app.config["CONTACT_EMAIL"] = "owner@example.test"
    assert "mailto:owner@example.test" in lambda_handler.call_wsgi(app, event("/privacy"))["body"]
