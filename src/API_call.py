import argparse
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NamedTuple

import psycopg
from dotenv import load_dotenv
import os


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_ENV_VARS = ["API_KEY", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME"]
DAILY_CALL_LIMIT = 25
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

logger = logging.getLogger("daily_api_collector")


class ValidationResult(NamedTuple):
    is_valid: bool
    result: dict | None
    error: dict | None


def get_config() -> dict[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        raise RuntimeError(
            f"Missing environment variables: {', '.join(missing)}. "
            "Create an .env file in the root of your project based on .env.example."
        )
    return {var: os.environ[var] for var in REQUIRED_ENV_VARS}


def get_connection(config: dict[str, str]) -> psycopg.Connection:
    return psycopg.connect(
        dbname=config["DB_NAME"],
        user=config["DB_USER"],
        password=config["DB_PASSWORD"],
        host=config["DB_HOST"],
        port=config["DB_PORT"],
    )


def calls_made_today(cur: psycopg.Cursor) -> int:
    """Successful calls so far today, UTC — see design doc 4.2/8 on why UTC."""
    cur.execute(
        "SELECT COUNT(*) FROM api_calls "
        "WHERE success AND called_at::date = (now() AT TIME ZONE 'UTC')::date"
    )
    return cur.fetchone()[0]


def fetch_daily_prices(ticker: str, api_key: str) -> dict:
    """Live call to Alpha Vantage. Network errors retry with backoff (design doc 4.4);
    a response that parses fine but signals quota/format trouble is NOT a network
    error and is handled by validate_call instead."""
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": ticker,
        "apikey": api_key,
        "outputsize": "compact",
    }
    url = f"{ALPHA_VANTAGE_URL}?{urllib.parse.urlencode(params)}"

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            wait = 2**attempt
            logger.warning(
                "Network error calling Alpha Vantage (attempt %d/3): %s. Retrying in %ds.",
                attempt + 1, exc, wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"Alpha Vantage request failed after retries: {last_error}")


def validate_call(call_result: dict) -> ValidationResult:
    """Success is judged by CONTENT (expected key present), not by the response
    merely being valid JSON — Alpha Vantage returns 200 + valid JSON even on a
    quota breach, just with a 'Note'/'Information' key instead."""
    if "Time Series (Daily)" in call_result:
        return ValidationResult(is_valid=True, result=call_result, error=None)
    return ValidationResult(is_valid=False, result=None, error=call_result)


def record_call(cur: psycopg.Cursor, ticker: str, is_valid: bool, error: dict | None) -> None:
    error_reason = None
    if error is not None:
        error_reason = error.get("Note") or error.get("Information") or json.dumps(error)[:500]
    cur.execute(
        "INSERT INTO api_calls (ticker, success, error_reason) VALUES (%s, %s, %s)",
        (ticker, is_valid, error_reason),
    )


def write_api_data(cur: psycopg.Cursor, ticker: str, price_data: dict) -> int:
    series = price_data["Time Series (Daily)"]
    rows = [
        (
            ticker,
            trade_date,
            values["1. open"],
            values["2. high"],
            values["3. low"],
            values["4. close"],
            values["5. volume"],
        )
        for trade_date, values in series.items()
    ]
    cur.executemany(
        """
        INSERT INTO daily_prices (ticker, trade_date, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker, trade_date) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            loaded_at = now()
        """,
        rows,
    )
    return len(rows)


def collect(ticker: str, config: dict[str, str], fixture_path: Path | None = None) -> None:
    """One parameterized entry point (design doc 4.5), so a future scheduler
    (cron / Airflow, project 6) can call this unchanged."""
    with get_connection(config) as conn:
        with conn.cursor() as cur:
            calls_today = calls_made_today(cur)
            if calls_today >= DAILY_CALL_LIMIT:
                logger.warning(
                    "Daily call limit (%d) already reached; skipping %s.",
                    DAILY_CALL_LIMIT, ticker,
                )
                return

            if fixture_path is not None:
                logger.info("Using fixture %s instead of a live API call.", fixture_path)
                with open(fixture_path) as f:
                    raw = json.load(f)
            else:
                raw = fetch_daily_prices(ticker, config["API_KEY"])

            validation = validate_call(raw)
            record_call(cur, ticker, validation.is_valid, validation.error)

            if not validation.is_valid:
                logger.error(
                    "API call for %s did not return price data (quota/format issue): %s",
                    ticker, validation.error,
                )
                conn.commit()
                return

            rows_written = write_api_data(cur, ticker, validation.result)
            conn.commit()
            logger.info("Upserted %d rows for %s.", rows_written, ticker)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily API-based data collector (Alpha Vantage).")
    parser.add_argument("--ticker", default="IBM", help="Stock ticker to fetch (default: IBM).")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="Path to a saved API response JSON, used instead of a live call "
        "(development/testing without burning API quota).",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()
    config = get_config()
    collect(args.ticker, config, fixture_path=args.fixture)


if __name__ == "__main__":
    main()
