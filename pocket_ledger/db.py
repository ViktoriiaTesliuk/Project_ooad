from datetime import date
from decimal import Decimal
from pathlib import Path

import boto3
import sqlparse
from flask import current_app, g


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
        request["transactionId"] = transaction_id
    return client.execute_statement(**request)


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


def apply_initial_schema():
    path = Path(__file__).resolve().parent.parent / "migrations" / "001_initial.sql"
    statements = sqlparse.split(path.read_text(encoding="utf-8"))
    client, options = _connection()
    transaction_id = client.begin_transaction(**options)["transactionId"]
    transaction_options = {
        "resourceArn": options["resourceArn"],
        "secretArn": options["secretArn"],
        "transactionId": transaction_id,
    }
    try:
        for statement in statements:
            execute(statement, transaction_id=transaction_id)
        client.commit_transaction(**transaction_options)
    except Exception:
        try:
            client.rollback_transaction(**transaction_options)
        except Exception:
            current_app.logger.exception("Could not roll back the schema transaction")
        raise
