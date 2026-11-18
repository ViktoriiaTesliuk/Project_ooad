"""Aurora auto-pause: Data API calls wait for the instance to resume."""

from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from pocket_ledger import db


def resuming():
    return ClientError({"Error": {"Code": "DatabaseResumingException", "Message": "resuming"}}, "ExecuteStatement")


@pytest.fixture
def client(app, monkeypatch):
    fake = Mock()
    monkeypatch.setattr(db.time, "sleep", Mock())
    with app.app_context():
        from flask import g

        g.rds_data, g.rds_options = fake, {"resourceArn": "arn", "secretArn": "arn", "database": "db"}
        yield fake


def test_execute_waits_while_aurora_resumes(client):
    client.execute_statement.side_effect = [resuming(), resuming(), {"records": [[{"longValue": 1}]]}]
    assert db.rows(db.execute("SELECT 1")) == [(1,)]
    assert client.execute_statement.call_count == 3
    assert db.time.sleep.call_count == 2


def test_begin_transaction_waits_while_aurora_resumes(client):
    client.begin_transaction.side_effect = [resuming(), {"transactionId": "t1"}]
    with db.transaction() as transaction_id:
        assert transaction_id == "t1"
    client.commit_transaction.assert_called_once()


def test_gives_up_after_the_wait_budget(client, monkeypatch):
    monkeypatch.setattr(db, "RESUME_WAIT_SECONDS", 0)
    client.execute_statement.side_effect = resuming()
    with pytest.raises(ClientError):
        db.execute("SELECT 1")
    assert client.execute_statement.call_count == 1


def test_other_errors_are_not_retried(client):
    client.execute_statement.side_effect = ClientError({"Error": {"Code": "BadRequestException"}}, "ExecuteStatement")
    with pytest.raises(ClientError):
        db.execute("SELECT 1")
    assert client.execute_statement.call_count == 1
