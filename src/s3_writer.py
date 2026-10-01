import datetime
import logging
import json

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger(__name__)


def get_s3_client() -> BaseClient:
    s3 = boto3.client('s3')
    return s3


def upload_raw_to_s3(bucket_name: str,
                     ticker: str,
                     today: datetime.date,
                     raw_json_data: dict) -> bool:
    key = f"raw/year={today.year}/month={today.month:02d}/day={today.day:02d}/{ticker}.json"

    try:
        client = get_s3_client()
        json_text = json.dumps(raw_json_data)
        client.put_object(Bucket=bucket_name,
                          Key=key,
                          Body=json_text,
                          ContentType="application/json")
        logger.info("S3 upload successful: %s", key)
        return True

    except ClientError as ce:
        logger.error("S3 upload failed: %s, "
                     "error code: %s, "
                     "error message: %s",
                     key,
                     ce.response["Error"]["Code"],
                     ce.response["Error"]["Message"])
        return False

    except BotoCoreError as be:
        logger.error("S3 upload failed: %s, error: %s", key, be)
        return False
