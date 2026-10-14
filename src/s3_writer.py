"""Raw-layer S3 helpers: client creation, the partition-key contract and the
best-effort upload used by the collector (API_call.py)."""

import datetime
import json
import logging

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


def get_s3_client() -> BaseClient:
    """Create an S3 client.

    Credentials, region and the optional AWS_CA_BUNDLE come from boto3's
    standard lookup (environment variables, ~/.aws, IAM role), so nothing is
    passed in explicitly.
    """
    return boto3.client("s3")


def build_raw_key(ticker: str, partition_date: datetime.date) -> str:
    """Return the raw-layer object key for one ticker and one day.

    This is the partition contract of the raw layer, shared by the uploader and
    verify_s3.py. Changing the format is a breaking change for every reader of
    the raw layer.
    """
    return (
        f"raw/year={partition_date.year}"
        f"/month={partition_date.month:02d}"
        f"/day={partition_date.day:02d}"
        f"/{ticker}.json"
    )


def upload_raw_to_s3(bucket_name: str,
                     ticker: str,
                     partition_date: datetime.date,
                     raw_json_data: dict) -> bool:
    """Upload the raw API response to the raw layer of the data lake.

    partition_date is the run date (UTC), not a trading date: each run stores
    the full response it received that day.

    Best-effort by design: S3 errors (ClientError, BotoCoreError) are logged
    and reported as False, because the Postgres load is already committed and
    must not depend on S3. Any other exception is a programming error and is
    deliberately not caught. Transient network errors are retried by botocore
    before an exception reaches this function.
    """
    key = build_raw_key(ticker, partition_date)

    try:
        client = get_s3_client()
        json_text = json.dumps(raw_json_data)
        client.put_object(Bucket=bucket_name,
                          Key=key,
                          Body=json_text,
                          ContentType="application/json")
        logger.info("Uploaded raw object to S3: %s", key)
        return True

    except ClientError as ce:
        logger.error("S3 upload failed for %s: %s (%s)",
                     key,
                     ce.response["Error"]["Code"],
                     ce.response["Error"]["Message"])
        return False

    except BotoCoreError as be:
        logger.error("S3 upload failed for %s: %s", key, be)
        return False
