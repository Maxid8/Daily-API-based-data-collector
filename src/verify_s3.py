"""Verify the raw-layer object of one ticker and one day in S3.

Checks, in this order:
  1. the object exists,
  2. its content is a JSON object,
  3. it contains the daily time series,
  4. its symbol matches the requested ticker.

Exit code 0 means every check passed, 1 means a check failed. The reason of a
failure is written to the log. The exit code is what a scheduler (Airflow in
project 6) acts on.

Usage:
    python src/verify_s3.py --ticker IBM [--date YYYY-MM-DD]
"""

import argparse
import datetime
import json
import logging
import os
import sys
from pathlib import Path

from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from s3_writer import build_raw_key, get_s3_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_ENV_VAR = "S3_BUCKET_NAME"

# Explicit name instead of __name__: when this file is run as a script,
# __name__ is "__main__", which would say nothing in the log.
logger = logging.getLogger("verify_s3")


def fetch_raw_object(client: BaseClient, bucket: str, key: str) -> bytes | None:
    """Download one object. Return None if it does not exist.

    A missing object is an expected outcome of a verification, so it is a
    return value. Any other S3 error (e.g. AccessDenied) propagates to the
    caller. NoSuchKey is only distinguishable from AccessDenied because the
    IAM policy allows s3:ListBucket on the raw/ prefix.
    """
    try:
        client_response = client.get_object(Bucket=bucket, Key=key)
        return client_response["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise


def parse_json(raw_bytes: bytes) -> dict | None:
    """Return the content as a dict, or None if it is not a JSON object."""
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError:
        return None

    # Valid JSON can also be a list or a number; the raw layer must hold objects.
    if not isinstance(data, dict):
        return None

    return data


def has_time_series(data: dict) -> bool:
    """True if the response contains the daily price series."""
    return "Time Series (Daily)" in data


def symbol_matches(data: dict, ticker: str) -> bool:
    """True if the symbol in the response metadata equals the ticker."""
    meta_data = data.get("Meta Data")
    if meta_data is not None:
        return meta_data.get("2. Symbol") == ticker
    return False


def verify(ticker: str, partition_date: datetime.date, bucket: str) -> bool:
    """Run every check in order and log the outcome. Stop at the first failure."""
    client = get_s3_client()
    key = build_raw_key(ticker, partition_date)

    try:
        raw = fetch_raw_object(client, bucket, key)
    except (ClientError, BotoCoreError) as e:
        logger.error("Could not fetch %s from bucket %s: %s", key, bucket, e)
        return False

    if raw is None:
        logger.error("Raw object not found: %s", key)
        return False

    json_data = parse_json(raw)
    if json_data is None:
        logger.error("Raw object is not a valid JSON object: %s", key)
        return False

    if not has_time_series(json_data):
        logger.error("Raw object has no 'Time Series (Daily)' section: %s", key)
        return False

    if not symbol_matches(json_data, ticker):
        logger.error("Raw object symbol does not match ticker %s: %s", ticker, key)
        return False

    logger.info("Raw object verified: %s", key)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a raw-layer object in the S3 data lake.")
    parser.add_argument("--ticker", required=True, help="Stock ticker to verify (e.g. IBM).")
    parser.add_argument(
        "--date",
        default=None,
        type=datetime.date.fromisoformat,
        help="Partition date to verify, YYYY-MM-DD. Default: today (UTC).",
    )
    return parser.parse_args()


def get_bucket_name() -> str:
    """Read the bucket name from the environment. Fail fast if it is missing or empty."""
    name = os.environ.get(REQUIRED_ENV_VAR)
    if not name:
        raise RuntimeError(
            f"Missing environment variable: {REQUIRED_ENV_VAR}. "
            "Create an .env file in the root of your project based on .env.example."
        )
    return name


def main() -> None:
    # Load .env first: it provides the bucket name and also the AWS credentials
    # and the optional AWS_CA_BUNDLE that boto3 reads from the environment.
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()

    # Computed at run time, in UTC, to match the partition date of the upload.
    if args.date is None:
        partition_date = datetime.datetime.now(datetime.timezone.utc).date()
    else:
        partition_date = args.date

    bucket_name = get_bucket_name()
    if verify(args.ticker, partition_date, bucket_name):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
