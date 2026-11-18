import time
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import boto3
import sqlparse
from botocore.exceptions import ClientError
from flask import current_app, g

# Aurora Serverless v2 pauses after a few idle minutes. The first Data API call
# afterwards fails with DatabaseResumingException while the instance resumes,
# which usually takes 10 to 20 seconds. Wait for it, within the Lambda timeout.
RESUME_WAIT_SECONDS = 25
_RESUME_DELAYS = (0.5, 1, 2, 3)


def _while_resuming(call, **request):
    """Call the Data API, retrying while a paused Aurora instance resumes."""
    deadline = time.monotonic() + RESUME_WAIT_SECONDS
    attempt = 0
    while True:
        try:
            return call(**request)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "DatabaseResumingException":
                raise
            delay = _RESUME_DELAYS[min(attempt, len(_RESUME_DELAYS) - 1)]
            if time.monotonic() + delay > deadline:
                raise
            if attempt == 0:
                current_app.logger.info("Aurora is resuming from auto-pause; waiting")
            time.sleep(delay)
            attempt += 1


def _connection():
    if "rds_data" not in g:
        config = current_app.config
        keys = ("RDS_RESOURCE_ARN", "RDS_SECRET_ARN", "RDS_DATABASE")
        missing = [key for key in keys if not config[key]]
        if missing:
            raise RuntimeError("Missing database configuration: " + ", ".join(missing))
        g.rds_data = boto3.client("rds-data")
        g.rds_options = {
            "resourceArn": config["RDS_RESOURCE_ARN"],
            "secretArn": config["RDS_SECRET_ARN"],
            "database": config["RDS_DATABASE"],
        }
    return g.rds_data, g.rds_options


def _parameter(name, value):
    field = {"name": name}
    if value is None:
        field["value"] = {"isNull": True}
    elif isinstance(value, Decimal):
        field.update(value={"stringValue": str(value)}, typeHint="DECIMAL")
    elif isinstance(value, date):
        field.update(value={"stringValue": value.isoformat()}, typeHint="DATE")
    elif isinstance(value, bool):
        field["value"] = {"booleanValue": value}
    elif isinstance(value, int):
        field["value"] = {"longValue": value}
    elif isinstance(value, str):
        field["value"] = {"stringValue": value}
    else:
        raise TypeError(f"Unsupported SQL parameter type for {name}: {type(value).__name__}")
    return field


def execute(sql, params=None, *, transaction_id=None):
    """Execute one SQL statement and return the RDS Data API response."""
    client, options = _connection()
    request = dict(options, sql=sql)
    if params:
        request["parameters"] = [_parameter(name, value) for name, value in params.items()]
    if transaction_id is not None:
        # Inside a transaction the instance is already awake (BeginTransaction waited).
        request["transactionId"] = transaction_id
        return client.execute_statement(**request)
    return _while_resuming(client.execute_statement, **request)


def upsert_google_user(subject, email, display_name):
    result = execute(
        """
        INSERT INTO users (google_sub, email, display_name)
        VALUES (:subject, :email, :display_name)
        ON CONFLICT (google_sub) DO UPDATE
        SET email = EXCLUDED.email, display_name = EXCLUDED.display_name
        RETURNING id
        """,
        {"subject": subject, "email": email, "display_name": display_name},
    )
    return result["records"][0][0]["longValue"]


def rows(response):
    """Decode RDS Data API ``records`` into tuples of plain Python values.

    NULL becomes ``None``; every other field yields its single typed value.
    Select monetary columns as text and wrap them in ``Decimal``.
    """
    return [
        tuple(None if field.get("isNull") else next(iter(field.values())) for field in record)
        for record in response.get("records", [])
    ]


@contextmanager
def transaction():
    """Run several ``execute`` calls atomically.

    Yields a transaction ID to pass as ``execute(..., transaction_id=...)``.
    Commits on success and rolls back if the block raises.
    """
    client, options = _connection()
    transaction_id = _while_resuming(client.begin_transaction, **options)["transactionId"]
    transaction_options = {
        "resourceArn": options["resourceArn"],
        "secretArn": options["secretArn"],
        "transactionId": transaction_id,
    }
    try:
        yield transaction_id
        client.commit_transaction(**transaction_options)
    except Exception:
        try:
            client.rollback_transaction(**transaction_options)
        except Exception:
            current_app.logger.exception("Could not roll back the transaction")
        raise


def apply_initial_schema():
    directory = Path(__file__).resolve().parent.parent / "migrations"
    with transaction() as transaction_id:
        for path in sorted(directory.glob("*.sql")):
            for statement in sqlparse.split(path.read_text(encoding="utf-8")):
                execute(statement, transaction_id=transaction_id)
