import json
from pathlib import Path
from unittest.mock import MagicMock

from src.API_call import validate_call, record_call, write_api_data

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def test_validate_call_accepts_a_successful_response():
    response = load_fixture("daily_series_success.json")

    result = validate_call(response)

    assert result.is_valid is True
    assert result.result == response
    assert result.error is None


def test_validate_call_rejects_an_invalid_api_key_response():
    response = load_fixture("invalid_api_key.json")

    result = validate_call(response)

    assert result.is_valid is False
    assert result.result is None
    assert result.error == response


def test_record_call_inserts_a_successful_call():
    mock_cursor = MagicMock()

    record_call(mock_cursor, "IBM", True, None)

    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO api_calls (ticker, success, error_reason) VALUES (%s, %s, %s)",
        ("IBM", True, None),
    )


def test_record_call_inserts_a_failed_call():
    mock_cursor = MagicMock()

    response = load_fixture("invalid_api_key.json")

    record_call(mock_cursor, "IBM", False, response)

    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO api_calls (ticker, success, error_reason) VALUES (%s, %s, %s)",
        ("IBM", False, response["Information"]),
    )  