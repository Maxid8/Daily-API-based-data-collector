"""Unit tests for verify_s3.py.

The pure helpers (parse_json, has_time_series, symbol_matches) are tested with
small inline inputs. verify() is tested with a mocked S3 client: every exit
path is checked for both its return value and its log message, because the log
is the only place that says which check failed.
"""

import datetime
import io
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from verify_s3 import has_time_series, parse_json, symbol_matches, verify

FIXTURES_DIR = Path(__file__).parent / "fixtures"

TICKER = "IBM"
PARTITION_DATE = datetime.date(2026, 3, 5)
BUCKET = "test-bucket"
# Written out literally on purpose: the test must not rebuild the key with the
# same logic it is checking.
EXPECTED_KEY = "raw/year=2026/month=03/day=05/IBM.json"


def s3_body(raw_bytes: bytes) -> dict:
    """Shape of a get_object response; BytesIO is a real object with .read()."""
    return {"Body": io.BytesIO(raw_bytes)}


def s3_client_error(code: str, message: str) -> ClientError:
    """A ClientError as botocore raises it when AWS answers with an error."""
    return ClientError({"Error": {"Code": code, "Message": message}}, "GetObject")


@pytest.fixture
def mock_s3_client():
    """Replace get_s3_client in verify_s3 with a mock for the duration of a test.

    The test configures mock_s3_client.get_object (return_value or side_effect).
    After the test (the code after yield) the original function is restored.
    """
    client = MagicMock()
    with patch("verify_s3.get_s3_client", return_value=client):
        yield client


# --- parse_json -----------------------------------------------------------

@pytest.mark.parametrize(
    "raw_bytes",
    [b"Some text to check it returns None.", b"", b"[1, 2]"],
    ids=["plain_text", "empty", "json_array"],
)
def test_parse_json_returns_none_when_input_is_invalid(raw_bytes):
    result = parse_json(raw_bytes)

    assert result is None


def test_parse_json_returns_dict_for_json_object():
    raw_bytes = b'{"a": 1}'

    result = parse_json(raw_bytes)

    assert result == {"a": 1}


# --- has_time_series ------------------------------------------------------

def test_has_time_series_returns_false_when_key_missing():
    data = {"a": 1}

    result = has_time_series(data)

    assert result is False


def test_has_time_series_returns_true_when_key_present():
    data = {"Meta Data": {}, "Time Series (Daily)": {}}

    result = has_time_series(data)

    assert result is True


# --- symbol_matches -------------------------------------------------------

@pytest.mark.parametrize(
    "data",
    [{"a": 1}, {"Meta Data": {}}, {"Meta Data": {"2. Symbol": "AAPL"}}],
    ids=["no_metadata", "no_symbol", "other_symbol"],
)
def test_symbol_matches_returns_false_for_missing_or_wrong_symbol(data):
    result = symbol_matches(data, TICKER)

    assert result is False


def test_symbol_matches_returns_true_for_matching_symbol():
    data = {"Meta Data": {"2. Symbol": "IBM"}}

    result = symbol_matches(data, TICKER)

    assert result is True


# --- verify ---------------------------------------------------------------

def test_verify_returns_true_and_logs_success_for_valid_object(mock_s3_client, caplog):
    caplog.set_level(logging.INFO)
    raw_bytes = (FIXTURES_DIR / "daily_series_success.json").read_bytes()
    mock_s3_client.get_object.return_value = s3_body(raw_bytes)

    result = verify(TICKER, PARTITION_DATE, BUCKET)

    assert result is True
    assert "verified" in caplog.text
    mock_s3_client.get_object.assert_called_once_with(Bucket=BUCKET, Key=EXPECTED_KEY)


@pytest.mark.parametrize(
    "raw_bytes, expected_log",
    [
        (b"not json at all", "not a valid JSON object"),
        (json.dumps({"Meta Data": {"2. Symbol": "IBM"}}).encode(), "Time Series (Daily)"),
        (
            json.dumps({"Meta Data": {"2. Symbol": "MSFT"}, "Time Series (Daily)": {}}).encode(),
            "does not match ticker",
        ),
    ],
    ids=["not_json", "no_time_series", "wrong_symbol"],
)
def test_verify_returns_false_and_logs_reason_for_invalid_content(
    mock_s3_client, caplog, raw_bytes, expected_log
):
    mock_s3_client.get_object.return_value = s3_body(raw_bytes)

    result = verify(TICKER, PARTITION_DATE, BUCKET)

    assert result is False
    assert expected_log in caplog.text


@pytest.mark.parametrize(
    "error, expected_log",
    [
        (s3_client_error("NoSuchKey", "The specified key does not exist."), "not found"),
        (s3_client_error("AccessDenied", "Access Denied"), "AccessDenied"),
        (NoCredentialsError(), "Unable to locate credentials"),
    ],
    ids=["missing_object", "access_denied", "botocore_error"],
)
def test_verify_returns_false_and_logs_reason_when_fetch_fails(
    mock_s3_client, caplog, error, expected_log
):
    mock_s3_client.get_object.side_effect = error

    result = verify(TICKER, PARTITION_DATE, BUCKET)

    assert result is False
    assert expected_log in caplog.text
