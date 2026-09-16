# Daily API-Based Data Collector

![Python](https://img.shields.io/badge/Python-3.14-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18.4-blue)

A small, idempotent pipeline that pulls daily stock prices from the
Alpha Vantage API and loads them into PostgreSQL. Built as the second
project in a data engineering portfolio series, focused on practicing
live API integration, upsert logic, and fault-tolerant, observable
batch jobs.

The first project in the series worked with a static CSV. This one
practices the step where data arrives repeatedly from an external,
live source and where three properties matter more than the API
call itself:

- **Idempotency**: running the collector multiple times for the same
  day never creates duplicates or an inconsistent state.
- **Fault tolerance**: a network hiccup, an invalid API key, or a
  quota breach is caught, logged, and handled explicitly it never
  fails silently or crashes the process unexpectedly.
- **Observability**: every run, successful or not, is logged clearly
  enough to reconstruct after the fact what happened.

A full design doc: architecture rationale, the alternatives that
were considered and rejected, and the key design decisions in detail
lives in [`docs/design-doc.md`](docs/design-doc.md). This README
covers what the project does and how to run it and the design doc covers
why it's built this way.

## Architecture

```
Alpha Vantage API  →  Python client (call + validation)  →  Upsert logic  →  PostgreSQL (daily_prices table)
                                                                                        │
                                                              structured logging (at every step)
```

A single run loads data for one ticker, then stops. There's no
long-running process and no in-memory state carried between runs —
everything the collector needs to decide what to do next (how many
calls were made today, whether a ticker's data already exists) lives
in the database.

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Upsert (`ON CONFLICT ... DO UPDATE`), not delete-then-insert | Atomic, race-condition-free, a single round trip — see design doc §5 |
| Success judged by response *content*, not HTTP status or JSON validity | Alpha Vantage returns `200 OK` with valid JSON even on a quota breach — the difference is which key the response body contains |
| Call log (`api_calls`) as its own table, not a JSON file | Single source of truth; lets the call log and the price upsert commit in one transaction |
| Rate-limit check happens *before* each call | The client checks today's successful call count against the daily quota itself, rather than waiting for the API to reject it |
| PostgreSQL, not SQLite | Closer to real-world conditions; exercises concurrent access and a proper type system |

See [`docs/design-doc.md`](docs/design-doc.md) for the full list, including the alternatives considered for each.

## Data Source

[Alpha Vantage](https://www.alphavantage.co/) — free tier, daily
(`TIME_SERIES_DAILY`) stock price endpoint. The free tier is limited
to 25 requests per day; the collector tracks and enforces this limit
itself before making a call.

## Prerequisites

- Python 3.14
- PostgreSQL 18.4
- An [Alpha Vantage API key](https://www.alphavantage.co/support/#api-key) (free)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Maxid8/Daily-API-based-data-collector.git
   cd Daily-API-based-data-collector
   ```

2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1   # Windows
   ```

3. Install the pinned dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create your `.env` file from the template and fill in your Alpha
   Vantage API key and PostgreSQL credentials:
   ```bash
   cp .env.example .env
   ```

5. Create the database schema:
   ```bash
   psql -U your_user -d your_database -f sql/table.sql
   ```

## Usage

Run the collector for a given ticker (defaults to `IBM`):

```bash
python src/API_call.py --ticker IBM
```

For development or testing without spending API quota, replay a saved
response instead of making a live call:

```bash
python src/API_call.py --ticker IBM --fixture tests/fixtures/daily_series_success.json
```

Re-running the same command for the same day is safe — the upsert
logic means the row count for that ticker won't change, only the
`loaded_at` timestamp.

## Testing

Two API response fixtures live under `tests/fixtures/`, captured from
real Alpha Vantage responses:

- `daily_series_success.json` — a successful `TIME_SERIES_DAILY`
  response.
- `invalid_api_key.json` — the error response Alpha Vantage returns
  for an invalid key, used to exercise the failure path in
  `validate_call` without needing a live, broken request.

An automated `pytest` suite built on these fixtures is planned as a
next step; until then, the behaviors below have been verified
manually against a real PostgreSQL database:

- A successful run upserts the expected row count and logs it.
- Re-running the same fixture a second time leaves the row count
  unchanged (idempotency).
- Once the daily call limit is reached, further calls are skipped
  before touching the API or writing any rows.
- Both successful and failed calls are recorded in `api_calls`.

## Project Structure

```
Daily API-based data collector/
├── docs/
│   └── design-doc.md              # detailed design rationale (ADR-style)
├── sql/
│   └── table.sql                  # daily_prices / api_calls schema
├── src/
│   └── API_call.py                # client, validation, upsert, orchestration
├── tests/
│   └── fixtures/
│       ├── daily_series_success.json
│       └── invalid_api_key.json
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Lessons Learned

The API integration and database insertion logic came together
faster than expected.

The bigger challenge was designing a clean function structure.
Working out how functions should depend on and call each other, so
each one has a single, clear responsibility, is still something I'm
actively learning.

If I restarted this project, I'd spend more time in the design phase
up front. I'd lay out every piece the pipeline needs and how they
connect before writing any code, instead of discovering the structure
while coding.

This project also made clear how much I still have to learn. Upsert
semantics and transactions in particular were new to me. I understand
the reasoning now, but I want to keep practicing them.

## Note

This project was built for learning purposes, as part of a personal
data engineering portfolio series. Feedback is welcome, but it isn't
maintained as an actively developed, production-ready project.
