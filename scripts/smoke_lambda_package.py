#!/usr/bin/env python3
"""Run the built Lambda zip in the AWS Lambda Python image and send it Function URL events.

    python scripts/build_lambda.py
    python scripts/smoke_lambda_package.py dist/pocket-ledger-lambda-arm64.zip

Needs Docker. It uses placeholder credentials only, never contacts AWS, and
checks request handling that does not need a database: the health check,
HTML pages, static assets, the Google sign-in redirect and its session
cookie, CSRF rejection of a multipart upload, and error pages. The Google
step fetches Google's public OpenID configuration; pass --offline to skip it.
"""

import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

IMAGE = "public.ecr.aws/lambda/python:3.13"
PUBLIC_URL = "https://smoke-test.lambda-url.eu-central-1.on.aws"
PORT = 9011


def event(method, path, *, query="", headers=None, cookies=None, body=None, base64_body=False):
    payload = {
        "version": "2.0",
        "rawPath": path,
        "rawQueryString": query,
        "headers": {"host": urlsplit(PUBLIC_URL).hostname, "x-forwarded-proto": "https",
                    "x-forwarded-port": "443", "user-agent": "smoke-test", **(headers or {})},
        "requestContext": {
            "domainName": urlsplit(PUBLIC_URL).hostname,
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.10"},
        },
        "isBase64Encoded": base64_body,
    }
    if cookies:
        payload["cookies"] = cookies
    if body is not None:
        payload["body"] = base64.b64encode(body).decode() if base64_body else body
    return payload


def invoke(payload):
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/2015-03-31/functions/function/invocations",
        data=json.dumps(payload).encode(), method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"ok   {message}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--arch", choices=("arm64", "x86_64"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    arch = args.arch or ("x86_64" if "x86_64" in args.package.name else "arm64")
    platform = "linux/arm64" if arch == "arm64" else "linux/amd64"

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(args.package) as archive:
            archive.extractall(tmp)
        container = subprocess.run([
            "docker", "run", "-d", "--rm", "--platform", platform, "-p", f"127.0.0.1:{PORT}:8080",
            "-v", f"{tmp}:/var/task:ro",
            "-e", "SECRET_KEY=smoke-test-secret-key-not-for-production",
            "-e", f"PUBLIC_BASE_URL={PUBLIC_URL}",
            "-e", "SESSION_COOKIE_SECURE=true",
            "-e", "GOOGLE_CLIENT_ID=smoke-test.apps.googleusercontent.com",
            "-e", "GOOGLE_CLIENT_SECRET=placeholder",
            "-e", "RDS_RESOURCE_ARN=arn:aws:rds:eu-central-1:000000000000:cluster:placeholder",
            "-e", "RDS_SECRET_ARN=arn:aws:secretsmanager:eu-central-1:000000000000:secret:placeholder",
            "-e", "AWS_DEFAULT_REGION=eu-central-1",
            IMAGE, "pocket_ledger.lambda_handler.handler",
        ], check=True, capture_output=True, text=True).stdout.strip()
        try:
            for _ in range(50):
                try:
                    started = time.monotonic()
                    health = invoke(event("GET", "/health"))
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                raise SystemExit("FAIL: the Lambda emulator did not start")
            print(f"     first invocation (includes init) took {time.monotonic() - started:.2f}s")
            run_checks(health, args.offline)
        finally:
            logs = subprocess.run(["docker", "logs", container], check=False, capture_output=True, text=True)
            subprocess.run(["docker", "stop", container], check=False, capture_output=True)
            if "Traceback" in logs.stdout + logs.stderr:
                print(logs.stdout[-4000:], logs.stderr[-4000:], sep="\n")
                raise SystemExit("FAIL: the function logged a traceback")
    print("All package smoke checks passed.")


def run_checks(health, offline):
    check(health["statusCode"] == 200 and json.loads(health["body"]) == {"status": "ok"}, "GET /health returns JSON")

    page = invoke(event("GET", "/"))
    check(page["statusCode"] == 200 and "Sign in with Google" in page["body"], "sign-in page renders from templates")
    check(not page["isBase64Encoded"], "HTML is returned as text")
    css_url = re.search(r'href="(/static/app\.css\?v=[0-9a-f]+)"', page["body"])
    check(css_url is not None, "page links a content-hashed stylesheet")

    path, _, query = css_url[1].partition("?")
    css = invoke(event("GET", path, query=query))
    check(css["statusCode"] == 200 and css["headers"]["content-type"].startswith("text/css")
          and ".rate-header" in css["body"], "static CSS is served from the package")
    check("max-age=31536000" in css["headers"].get("cache-control", ""), "static assets are cacheable")
    icon = invoke(event("GET", "/static/icon.svg"))
    check(icon["statusCode"] == 200 and "<svg" in icon["body"], "SVG icon is served")
    sample = invoke(event("GET", "/static/sample-transactions.csv"))
    sample_body = base64.b64decode(sample["body"]) if sample["isBase64Encoded"] else sample["body"].encode()
    check(sample["statusCode"] == 200 and sample_body.startswith(b"date,kind,amount,currency,category"),
          "sample CSV can be downloaded")

    private = invoke(event("GET", "/dashboard"))
    check(private["statusCode"] == 302 and private["headers"]["location"] == "/",
          "private pages redirect signed-out visitors")

    boundary = "smokeboundary"
    multipart = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"wallet_id\"\r\n\r\n1\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"csv_file\"; filename=\"t.csv\"\r\n"
        f"Content-Type: text/csv\r\n\r\ndate,kind,amount,currency,category\r\n--{boundary}--\r\n"
    ).encode()
    upload = invoke(event("POST", "/transactions/import", body=multipart, base64_body=True,
                          headers={"content-type": f"multipart/form-data; boundary={boundary}"}))
    check(upload["statusCode"] == 400 and "Reload the page" in upload["body"],
          "base64 multipart upload is parsed and rejected without a CSRF token")

    missing = invoke(event("GET", "/no-such-page"))
    check(missing["statusCode"] == 404 and "We couldn’t find that" in missing["body"], "404 page renders")
    ignored = invoke({"action": "unknown"})
    check(ignored.get("status") == "ignored", "unknown direct invocations are ignored")

    if offline:
        print("skip Google redirect (offline)")
        return
    redirect = invoke(event("GET", "/auth/google"))
    check(redirect["statusCode"] == 302, "GET /auth/google redirects")
    location = urlsplit(redirect["headers"]["location"])
    params = parse_qs(location.query)
    check(location.netloc == "accounts.google.com", "redirect goes to Google")
    check(params["redirect_uri"] == [f"{PUBLIC_URL}/auth/google/callback"], "callback URL uses the HTTPS public origin")
    check(params.get("code_challenge_method") == ["S256"] and "nonce" in params and "state" in params,
          "PKCE, nonce and state are sent")
    cookie = next((c for c in redirect["cookies"] if c.startswith("session=")), "")
    check("Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie,
          "session cookie is returned in the Function URL cookies list as Secure, HttpOnly, SameSite=Lax")

    session_value = cookie.split(";", 1)[0]
    callback = invoke(event("GET", "/auth/google/callback", query="state=wrong&code=x", cookies=[session_value]))
    check(callback["statusCode"] == 400 and "could not be verified" in callback["body"],
          "callback reads the session cookie and rejects a mismatched state")


if __name__ == "__main__":
    sys.exit(main())
