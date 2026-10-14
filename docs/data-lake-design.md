# Project 3 — Data Lake on AWS S3
## Design Doc

**Status:** implemented, to be tagged as `v0.3-data-lake`
**Builds on:** project 2, the daily collector ([`design-doc.md`](design-doc.md))
**Last updated:** 2026-10-14

---

## 1. Problem and Goal

Project 2 loads daily prices into PostgreSQL. The database keeps the *processed* result: one row per ticker and trading day. The original API response is gone after the load.

This project adds the first layer of a data lake: every live API response is stored unchanged in S3. Three goals:

- **Keep the raw source.** If parsing or the schema changes later, the history can be reprocessed from the original responses (project 4 builds on this).
- **Move to the cloud safely.** Least-privilege access, encryption, no public access, cost alerts from day one.
- **Prove what was written.** A separate verification script checks the stored object independently of the code that wrote it.

## 2. Scope

**In scope:**
- An S3 bucket with a `raw/` layer, partitioned by date
- Uploading the raw API response from the existing collector
- A dedicated IAM user and least-privilege policy
- A verification script with exit codes for a future scheduler
- Unit tests for the upload and the verification

**Out of scope (later projects):**
- The `processed/` layer and Parquet conversion — project 4 (Glue / Athena)
- Scheduling and alerting — project 6 (Airflow)
- Infrastructure as code — project 8 (Terraform); the AWS resources here are created in the console

## 3. Architecture

```
Alpha Vantage API
      │
      ▼
src/API_call.py ──► PostgreSQL (daily_prices, api_calls)   1. commit
      │
      └──────────► S3 raw layer                             2. best-effort upload
                   raw/year=YYYY/month=MM/day=DD/TICKER.json
                            │
                            ▼
                   src/verify_s3.py  ──►  exit code 0 / 1 + log
```

The upload code lives in the same repository as the collector, because it extends the same run. The partition key is built in one place (`build_raw_key` in `src/s3_writer.py`), and both the uploader and the verifier use it.

## 4. Components in Detail

### 4.1 S3 Bucket

| Setting | Value | Why |
|---|---|---|
| Region | `eu-north-1` | the account's home region |
| Block Public Access | on | raw data is never public |
| Object ownership | ACLs disabled | access is controlled by IAM only |
| Encryption | SSE-S3 | encryption at rest, no key management needed |
| Versioning | off | see decision table, section 5 |
| Cost allocation tag | `Project = daily-prices-datalake` | costs are traceable per project |

### 4.2 Raw Layer Layout

```
raw/year=2026/month=10/day=12/IBM.json
```

- **The date is the run date in UTC**, not a trading date. Each run stores the full response it received that day. UTC matches the daily quota counting in project 2, and it does not change when the code moves to a cloud machine.
- **Month and day are zero-padded** (`month=03`), so the keys sort correctly and the format stays stable.
- **The `key=value` folder names** are the Hive partition convention. Glue and Athena (project 4) recognise them as partition columns without extra configuration.
- **The content is the unchanged API response** (`TIME_SERIES_DAILY`, `outputsize=compact`). One response covers about 100 trading days, so consecutive daily files overlap. That is intended in a raw layer; de-duplication belongs to the `processed/` layer in project 4.

### 4.3 Access: IAM

A dedicated IAM user, `daily-prices-collector`, with one customer-managed policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListRawPrefixOnly",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME",
      "Condition": { "StringLike": { "s3:prefix": ["raw/*"] } }
    },
    {
      "Sid": "ReadWriteRawObjects",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME/raw/*"
    }
  ]
}
```

- **`ListBucket` is a bucket-level action**, so it needs the bucket ARN, not the object ARN. The `Condition` limits it to the `raw/` prefix.
- **Why listing is needed at all:** without `s3:ListBucket`, S3 answers a request for a missing object with `AccessDenied` (403) instead of `NoSuchKey` (404), to avoid revealing which objects exist. The verifier needs the difference between "missing" and "no permission". This was confirmed with a test request after the policy change.
- **The `processed/` prefix is not accessible** to this user. Project 4 gets its own permissions.

### 4.4 Upload — `src/s3_writer.py`

- Runs **after** the PostgreSQL commit. The database load is the primary result, the upload is secondary.
- **Best-effort:** S3 errors (`ClientError`, `BotoCoreError`) are logged with their error code and reported as `False`. They never undo the database load. Transient network errors are retried by botocore before an exception is raised.
- **Programming errors are not caught.** A `TypeError` from `json.dumps`, for example, stops the run with a full traceback. A catch-all `except Exception` was tried and removed: it hid a real bug behind an "S3 upload failed" log line.
- **Skipped in fixture mode.** A saved fixture is not what the API returned on that day, so it must not appear in the raw layer as if it were.

### 4.5 Verification — `src/verify_s3.py`

```
python src/verify_s3.py --ticker IBM [--date YYYY-MM-DD]
```

Checks, in this order, and stops at the first failure:

1. the object exists,
2. it is a JSON object,
3. it contains `Time Series (Daily)`,
4. `Meta Data` → `2. Symbol` equals the requested ticker.

- **Exit code 0 / 1** is what a scheduler acts on. The log line says which check failed and why.
- **`--ticker` is required.** A default would let a misconfigured job verify the wrong ticker and report success.
- **`--date` defaults to today in UTC**, computed at run time, matching the upload.
- **Only the orchestrating function logs.** The helpers return values (`None`, `True`/`False`), so every event appears in the log exactly once.
- **Only `GetObject` is used**, no listing of the bucket: the exact key is known.

### 4.6 Corporate Network (TLS-Inspecting Proxy)

The development machine sits behind a proxy that re-signs HTTPS traffic with an internal CA. Two clients needed two different fixes:

- **Alpha Vantage (urllib):** a `truststore.SSLContext` is passed to that one `urlopen` call, so it trusts the Windows certificate store. A global `truststore.inject_into_ssl()` was used before, but it replaced `ssl.SSLContext` for the whole process and broke boto3 with a `RecursionError`.
- **AWS (boto3):** boto3 uses its own CA list. The optional `AWS_CA_BUNDLE` environment variable points to a PEM file that contains botocore's default CA list plus the corporate root and intermediate certificates. The file lives outside the repository. On a network without the proxy the variable is simply not set.

Certificate verification is never disabled.

## 5. Key Design Decisions

| Decision | Alternatives | Rationale |
|---|---|---|
| Upload code in the collector's repository | separate repository | the upload extends the same run; projects that build on each other share one codebase, with a git tag per project |
| Upload after the DB commit, best-effort | upload first; roll back the DB on S3 failure | the database load must not depend on S3 availability |
| Partition by run date (UTC) | by trading date | a raw layer stores what was received, when it was received; UTC matches the quota counting |
| Partition key built in one function | each script formats the key itself | the key is a contract between components; one source of truth prevents silent drift (e.g. `month=3` vs `month=03`) |
| Versioning off | versioning on | a same-day re-run overwrites that day's snapshot, which carries no extra information; history across days is kept by the date partitions |
| Narrow exception handling | `except Exception` | expected S3 failures are handled, programming errors fail loudly |
| Scoped `truststore` context | global `inject_into_ssl()` | the global patch broke boto3; the scoped context affects one call only |
| `AWS_CA_BUNDLE` for boto3 | `verify=False` | verification stays on; the credentials travel over this connection |
| `ListBucket` on `raw/*` | GetObject only | needed to tell `NoSuchKey` from `AccessDenied` |
| Verifier checks content, not only existence | existence check only | an existing but wrong or broken file must also fail |

## 6. The Raw Layer as a Contract

The key format and the content of the raw layer are a promise to every reader, now and in later projects. Rules:

- **The format is guarded by a unit test** that writes the expected key out literally. Changing the format makes that test fail, which forces a deliberate decision.
- **A format change is a breaking change.** It is introduced under a new prefix (for example `raw/v2/...`) next to the old one, readers are notified, and the old layout is retired only after an announced date.
- **Consumers should not read the raw layer directly.** Reports and dashboards belong on the processed or warehouse layers (projects 4–5), which are designed to stay stable.

## 7. Cost and Security

- AWS Budgets zero-spend alert and a 20 USD monthly limit with alerts at 50 / 75 / 90 %.
- One IAM user with programmatic access only; its keys are in a local `.env` that is git-ignored.
- In the cloud (project 6) the access key is replaced by an IAM role. boto3 picks the role up automatically, so the code does not change.

## 8. Testing

- **Unit tests** (`tests/test_s3_writer.py`, `tests/test_verify_s3.py`): the S3 client is a mock, no AWS access needed. Every exit path of the verifier is checked for its return value **and** its log message, because the log is the only place that says which check failed.
- **Mutation check:** six deliberate bugs were introduced in `verify_s3.py` (removed `raise`, missing `BotoCoreError`, removed type check, wrong log text, disabled ticker check, unpadded month). Each was caught by exactly one test.
- **End-to-end run** against real AWS: upload, verification, and a same-day re-run (the object is overwritten, the database row count does not change).

## 9. Risks and Open Questions

- *Accepted:* the `AWS_CA_BUNDLE` file is a copy of botocore's CA list. After a boto3 upgrade it should be rebuilt.
- *Accepted:* one ticker per run. Multiple tickers would need a loop or one scheduled task per ticker (project 6).
- *Open:* the policy is named `DailyPricesRawWritePolicy`, but it also allows reading and listing. A more accurate name would be clearer.
- *Open:* the AWS resources are created by hand. Project 8 moves them to Terraform.

## 10. Lessons Learned

- Unit tests with mocks prove the logic, not the integration. Two real problems (the `RecursionError` and the proxy certificate) only appeared in the end-to-end run.
- A green test is not necessarily a good test. One test passed for the wrong reason until a catch-all `except` was removed.
- Logs are part of the interface. Splitting one validation into two checks made the failure reason visible, and the tests now protect it.
- Small AWS details matter: `ListBucket` is a bucket-level action, and its absence changes `NoSuchKey` into `AccessDenied`.
