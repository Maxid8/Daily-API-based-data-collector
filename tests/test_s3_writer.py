import datetime
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, BotoCoreError

from s3_writer import upload_raw_to_s3


def test_upload_raw_to_s3_puts_object_with_partitioned_key_and_returns_true():
    mock_client = MagicMock()
    price_data = {"Time Series (Daily)": {}}
    test_date = datetime.date(2026, 3, 5)

    with patch("s3_writer.get_s3_client", return_value=mock_client):
        result = upload_raw_to_s3("s3_bucket", "IBM", test_date, price_data)

    assert result is True
    mock_client.put_object.assert_called_once_with(
        Bucket='s3_bucket',
        Key='raw/year=2026/month=03/day=05/IBM.json',
        Body=json.dumps(price_data),
        ContentType="application/json"
    )


def test_upload_raw_to_s3_returns_false_on_client_error():
    mock_client = MagicMock()
    price_data = {"Time Series (Daily)": {}}
    test_date = datetime.date(2026, 3, 5)
    mock_client.put_object.side_effect = ClientError(
        error_response={"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
        operation_name="PutObject",
    )

    with patch("s3_writer.get_s3_client", return_value=mock_client):
        result = upload_raw_to_s3("s3_bucket", "IBM", test_date, price_data)

    assert result is False


def test_upload_raw_to_s3_returns_false_on_botocore_error():
    mock_client = MagicMock()
    price_data = {"Time Series (Daily)": {}}
    test_date = datetime.date(2026, 3, 5)
    mock_client.put_object.side_effect = BotoCoreError()

    with patch("s3_writer.get_s3_client", return_value=mock_client):
        result = upload_raw_to_s3("s3_bucket", "IBM", test_date, price_data)

    assert result is False


def test_upload_raw_to_s3_raises_on_unexpected_exception():
    mock_client = MagicMock()
    price_data = {"Time Series (Daily)": {1,2}}
    test_date = datetime.date(2026, 3, 5)

    with patch("s3_writer.get_s3_client", return_value=mock_client):
        with pytest.raises(TypeError, match="not JSON serializable"):
            upload_raw_to_s3("s3_bucket", "IBM", test_date, price_data)