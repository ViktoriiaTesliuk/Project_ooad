"""AWS Lambda entry point: ``pocket_ledger.lambda_handler.handler``.

Translates Lambda Function URL events (payload format 2.0) to WSGI and back,
so the Flask app runs unchanged behind its HTTPS URL. Direct invocations with
``{"action": "migrate"}`` apply the SQL migrations instead.

Function URL specifics handled here:

* request cookies arrive in ``event["cookies"]`` and response cookies must be
  returned in a ``cookies`` list (one ``Set-Cookie`` value each);
* request bodies (forms, multipart CSV uploads) may be base64-encoded;
* binary responses must be base64-encoded; text stays readable in logs;
* the URL is always HTTPS, which Flask needs to know for redirects and
  secure cookies.
"""

import base64
import io
import json
import os
import sys
from urllib.parse import unquote_to_bytes

SECRET_KEYS = ("SECRET_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GEMINI_API_KEY")
TEXT_TYPES = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml")

_app = None


def load_app_secrets(environ=os.environ, client_factory=None):
    """Copy app secrets from the Secrets Manager secret named by ``APP_SECRETS_ARN``.

    The secret is a JSON object with any of ``SECRET_KEYS``. Values already set
    as environment variables win, so a single key can be overridden for tests.
    """
    arn = environ.get("APP_SECRETS_ARN")
    if not arn:
        return
    if client_factory is None:
        import boto3

        client_factory = boto3.client
    payload = client_factory("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
    values = json.loads(payload)
    unknown = set(values) - set(SECRET_KEYS)
    if unknown:
        raise RuntimeError("Unexpected keys in the app secret: " + ", ".join(sorted(unknown)))
    for key, value in values.items():
        if value and not environ.get(key):
            environ[key] = str(value)


def get_app():
    """Create the Flask app once per Lambda execution environment."""
    global _app
    if _app is None:
        load_app_secrets()
        from . import create_app

        _app = create_app()
    return _app


def _environ(event):
    request_context = event.get("requestContext", {})
    http = request_context.get("http", {})
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}

    body = event.get("body") or ""
    body = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf-8")

    scheme = headers.get("x-forwarded-proto", "https").split(",")[0].strip()
    host = headers.get("host") or request_context.get("domainName", "localhost")
    raw_path = event.get("rawPath") or http.get("path") or "/"
    environ = {
        "REQUEST_METHOD": http.get("method", "GET").upper(),
        "SCRIPT_NAME": "",
        # PEP 3333: PATH_INFO is the percent-decoded path as latin-1 "bytes".
        "PATH_INFO": unquote_to_bytes(raw_path).decode("latin-1"),
        "QUERY_STRING": event.get("rawQueryString", ""),
        "SERVER_NAME": host.split(":")[0],
        "SERVER_PORT": headers.get("x-forwarded-port", "443" if scheme == "https" else "80"),
        "SERVER_PROTOCOL": http.get("protocol", "HTTP/1.1"),
        "REMOTE_ADDR": http.get("sourceIp", ""),
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": scheme,
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "lambda.event": event,
    }
    if "content-type" in headers:
        environ["CONTENT_TYPE"] = headers["content-type"]
    for key, value in headers.items():
        if key in ("content-type", "content-length", "cookie"):
            continue
        environ["HTTP_" + key.upper().replace("-", "_")] = value
    environ["HTTP_HOST"] = host
    cookies = list(event.get("cookies") or [])
    if headers.get("cookie"):
        cookies.append(headers["cookie"])
    if cookies:
        environ["HTTP_COOKIE"] = "; ".join(cookies)
    return environ


def _is_text(headers):
    if "content-encoding" in headers:
        return False
    content_type = headers.get("content-type", "").lower()
    return content_type.startswith(TEXT_TYPES) or "charset=" in content_type


def call_wsgi(app, event):
    """Run a WSGI app for one Function URL event and return the Lambda response."""
    started = {}

    def start_response(status, response_headers, exc_info=None):
        if exc_info and started:
            raise exc_info[1].with_traceback(exc_info[2])
        started["status"] = int(status.split(" ", 1)[0])
        started["headers"] = response_headers
        return lambda data: chunks.append(data)

    chunks = []
    result = app(_environ(event), start_response)
    try:
        chunks.extend(result)
    finally:
        if hasattr(result, "close"):
            result.close()
    body = b"".join(chunks)

    headers, cookies = {}, []
    for name, value in started["headers"]:
        key = name.lower()
        if key == "set-cookie":
            cookies.append(value)
        elif key in headers:
            headers[key] += ", " + value
        else:
            headers[key] = value

    response = {"statusCode": started["status"], "headers": headers, "cookies": cookies}
    if _is_text(headers):
        try:
            response.update(body=body.decode("utf-8"), isBase64Encoded=False)
            return response
        except UnicodeDecodeError:
            pass
    response.update(body=base64.b64encode(body).decode("ascii"), isBase64Encoded=True)
    return response


def handler(event, context):
    if isinstance(event, dict) and event.get("action") == "migrate":
        from . import db

        app = get_app()
        with app.app_context():
            db.apply_initial_schema()
        return {"status": "migrated"}
    if not isinstance(event, dict) or "requestContext" not in event:
        return {"status": "ignored", "reason": "Expected a Function URL request or {\"action\": \"migrate\"}."}
    return call_wsgi(get_app(), event)
