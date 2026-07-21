# FileBridge API

Turn CSV files into searchable, filterable REST datasets.

> Upload a CSV, get a clean searchable API.

FileBridge API is a Django REST API that turns an uploaded CSV file into a structured,
searchable, filterable dataset: it detects the schema, stores each row as JSON, and exposes
the whole thing as a paginated, filterable REST API — with permissions, CSV export, and
OpenAPI/Swagger documentation.

## Problem solved

Teams often have their data sitting in flat files (event sign-ups, leads exports, inventories,
Notion/Airtable/Shopify exports...). That's fine until the data needs to be consumed elsewhere:
a dashboard, a script, a Make/Zapier/n8n automation, an internal app. FileBridge is a small
bridge between "I have a CSV" and "I have a real API".

## Features

- CSV or Excel (`.xlsx`) upload with automatic schema detection (column names, inferred type:
  string, number, date, email, boolean, unknown — a type is assigned when at least 90% of a
  column's values match it, so a handful of typos doesn't downgrade the whole column to
  "string")
- Preview a file's detected schema and a row sample before committing to it, with nothing
  written to the database
- Retry ingestion for a failed dataset from its already-stored file, no re-upload needed
- Optional webhook fired once ingestion finishes (success or failure), with SSRF-aware
  URL validation (blocks private/loopback/link-local/cloud-metadata destinations)
- A data-quality report per dataset: missing values and type mismatches per column, plus
  exact-duplicate row detection
- Flexible row storage via `JSONField` — no fixed table per dataset
- Dynamic per-column filtering (`?campus=Paris&status=active`) and global `search`
- Pagination on every list endpoint
- Owner-only permissions (a dataset is only visible to the user who uploaded it)
- Per-dataset API keys for read-only, machine-to-machine access (schema/rows/export only —
  never upload, list, delete, or key management), each with its own independent rate limit
  bucket — not shared with other keys or with anonymous traffic
- Public / read-only dataset sharing: an owner can flip a dataset public so anyone can read
  its schema/rows/export with no authentication at all, without exposing it via `list` or
  granting any write/management access
- CSV export
- OpenAPI schema + Swagger UI via drf-spectacular
- Full test suite (models, ingestion service, API views) written test-first

## Tech stack

- Python 3.12
- Django 5.2 LTS
- Django REST Framework 3.16
- drf-spectacular (OpenAPI 3 / Swagger UI)
- openpyxl (`.xlsx` ingestion)
- Celery + Redis (async ingestion)
- requests (webhook delivery)
- django-filter
- django-environ (12-factor config)
- SQLite or PostgreSQL (via `DATABASE_URL`) — the Docker Compose stack uses Postgres
- Docker + Docker Compose for local, one-command setup
- pytest + pytest-django + pytest-mock + factory-boy
- ruff (lint + format)
- GitHub Actions CI

## API examples

Preview a file before committing to it — parses it and returns the detected schema plus a
10-row sample, without creating a dataset or writing anything:

```bash
curl -u alice:password -X POST http://localhost:8000/api/datasets/preview/ \
  -F "file=@inscriptions.csv"
```

Upload a CSV or Excel file (creates the dataset; the format is picked from the file
extension — see [Async ingestion](#async-ingestion) for what happens next):

```bash
curl -u alice:password -X POST http://localhost:8000/api/datasets/upload/ \
  -F "name=inscriptions" \
  -F "file=@inscriptions.csv"

curl -u alice:password -X POST http://localhost:8000/api/datasets/upload/ \
  -F "name=inscriptions" \
  -F "file=@inscriptions.xlsx"
```

Optionally, get notified when ingestion finishes by passing a `webhook_url` at upload time
(see [Webhooks](#webhooks) below):

```bash
curl -u alice:password -X POST http://localhost:8000/api/datasets/upload/ \
  -F "name=inscriptions" \
  -F "file=@inscriptions.csv" \
  -F "webhook_url=https://example.com/hooks/filebridge"
```

List your datasets:

```bash
curl -u alice:password http://localhost:8000/api/datasets/
```

Get the detected schema:

```bash
curl -u alice:password http://localhost:8000/api/datasets/1/schema/
```

List rows, paginated:

```bash
curl -u alice:password http://localhost:8000/api/datasets/1/rows/
```

Filter rows by column, and combine filters:

```bash
curl -u alice:password "http://localhost:8000/api/datasets/1/rows/?campus=Paris"
curl -u alice:password "http://localhost:8000/api/datasets/1/rows/?promo=2027&status=active"
```

Global search across all columns:

```bash
curl -u alice:password "http://localhost:8000/api/datasets/1/rows/?search=sarah"
```

Get a single row:

```bash
curl -u alice:password http://localhost:8000/api/datasets/1/rows/42/
```

Export a dataset back to CSV:

```bash
curl -u alice:password http://localhost:8000/api/datasets/1/export/ -o export.csv
```

Retry a failed dataset (re-ingests the same uploaded file, no re-upload needed):

```bash
curl -u alice:password -X POST http://localhost:8000/api/datasets/1/retry/
```

Delete a dataset:

```bash
curl -u alice:password -X DELETE http://localhost:8000/api/datasets/1/
```

### API keys (read-only, machine-to-machine access)

Create a key for a dataset (the raw key is only ever shown in this response — store it now):

```bash
curl -u alice:password -X POST http://localhost:8000/api/datasets/1/api-keys/ \
  -d "name=n8n integration"
```

List a dataset's keys (never returns the raw key or its hash):

```bash
curl -u alice:password http://localhost:8000/api/datasets/1/api-keys/
```

Use a key — grants read-only access to *that one dataset's* schema/rows/export, nothing else:

```bash
curl -H "Authorization: Api-Key fbk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  http://localhost:8000/api/datasets/1/rows/
```

Revoke a key:

```bash
curl -u alice:password -X DELETE http://localhost:8000/api/datasets/1/api-keys/3/
```

### Public sharing

Make a dataset public (owner-only; only `is_public` is writable through this endpoint):

```bash
curl -u alice:password -X PATCH http://localhost:8000/api/datasets/1/visibility/ \
  -d "is_public=true"
```

Anyone can now read it — no credentials needed:

```bash
curl http://localhost:8000/api/datasets/1/rows/
```

`list`/`retrieve`/upload/delete/key-management stay owner-only regardless of visibility —
sharing only exposes schema/rows/row-detail/export for the exact dataset ID you hand out.

### Data quality report

Same read-access tier as schema/rows/export (owner, API key, or public dataset):

```bash
curl -u alice:password http://localhost:8000/api/datasets/1/quality/
```

```json
{
  "row_count": 42,
  "duplicate_row_count": 2,
  "columns": [
    {"name": "email", "detected_type": "email", "missing_count": 0, "invalid_count": 1},
    {"name": "campus", "detected_type": "string", "missing_count": 3, "invalid_count": 0}
  ]
}
```

### Webhooks

Pass `webhook_url` when uploading (or leave it out — it's optional) to get a POST once
ingestion finishes, whether it succeeds or fails:

```json
{
  "event": "dataset.ready",
  "dataset": {
    "id": 1,
    "name": "inscriptions",
    "status": "ready",
    "row_count": 42,
    "column_count": 5,
    "failure_reason": ""
  }
}
```

`event` is `"dataset.ready"` or `"dataset.failed"`. Delivery is best-effort: a failed or
slow (>5s) webhook request is logged and otherwise ignored — it never affects the
dataset's own status. Retrying a failed dataset (`POST .../retry/`) fires the webhook
again for that new attempt; retry has its own tighter rate limit specifically because of
that (see [Environment variables](#environment-variables), `THROTTLE_RATE_RETRY`).

The URL is validated (scheme must be http/https, and it can't resolve to a
private/loopback/link-local/reserved address — this includes cloud metadata endpoints
like `169.254.169.254`) both when you set it and again immediately before every send.
This is a security boundary, not just an input check: without it, a webhook URL would
let this server be used to probe or reach internal network destinations it has access to
but the public internet doesn't. See the docstring in
[`services/webhooks.py`](apps/datasets/services/webhooks.py) for the one limitation this
doesn't close (DNS rebinding between validation and the actual request).

## Data model

- **Dataset** — one uploaded file: owner, name, status (`pending`/`ready`/`failed`), row/column
  counts, visibility.
- **DatasetColumn** — one detected column per dataset: original header, normalized name,
  detected type, position.
- **DatasetRow** — one row per dataset, stored as a `JSONField` keyed by normalized column
  names.
- **DatasetApiKey** — a hashed API key scoped to one dataset, for read-only
  machine-to-machine access (see [API keys](#api-keys-read-only-machine-to-machine-access)
  above).

## Installation

This project isn't deployed anywhere — it's meant to be run on your own machine. Uploads are
ingested by a Celery task (see [Async ingestion](#async-ingestion) below), which needs a
Redis broker and a running worker; Docker Compose gives you the whole stack in one command.

### With Docker (recommended)

Requirements: Docker + Docker Compose.

```bash
git clone git@github.com:Pikatchu99/filebridge-api.git
cd filebridge-api
docker compose up --build
```

This starts Postgres, Redis, the Django app (migrating automatically on boot), and a Celery
worker. Create a superuser in a separate terminal once it's up:

```bash
docker compose exec web python manage.py createsuperuser
```

Swagger UI: http://localhost:8000/api/docs/
Raw OpenAPI schema: http://localhost:8000/api/schema/

### Without Docker

Requirements: Python 3.12+, a running Redis instance (`brew install redis && redis-server`,
or any Redis you already have).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

In a second terminal, run the Celery worker — without it, uploads stay `pending` forever:

```bash
source .venv/bin/activate
celery -A config worker --loglevel=info
```

## Environment variables

See [.env.example](.env.example):

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Django secret key | dev-only insecure value |
| `DEBUG` | Debug mode | `False` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `DATABASE_URL` | DB connection string (sqlite:// or postgres://) | local SQLite file |
| `FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES` | Max upload size in bytes | `10485760` (10 MB) |
| `FILEBRIDGE_MAX_XLSX_ROWS` | Max rows read from an `.xlsx` upload's first sheet | `200000` |
| `CELERY_BROKER_URL` | Redis URL used as the Celery broker/result backend | `redis://localhost:6379/0` |
| `THROTTLE_RATE_ANON` | Rate limit for unauthenticated requests, per IP | `20/min` |
| `THROTTLE_RATE_USER` | Rate limit for session/basic-auth requests, per user | `100/min` |
| `THROTTLE_RATE_API_KEY` | Rate limit per `DatasetApiKey` — each key has its own bucket, independent of other keys and of anonymous traffic | `60/min` |
| `THROTTLE_RATE_RETRY` | Rate limit for `POST .../retry/` specifically — tighter than the general user rate since a retry can re-fire a dataset's webhook | `10/min` |

## Async ingestion

`POST /api/datasets/upload/` returns `202 Accepted` immediately — parsing runs in a Celery
task, not the request. The response body reflects whatever the dataset's status already is
at that moment (`pending` if the worker hasn't gotten to it yet, or `ready`/`failed` if it
was fast enough that it already has); poll `GET /api/datasets/{id}/` to watch it resolve.
If ingestion fails (bad header, encoding, corrupt file), `POST /api/datasets/{id}/retry/`
re-queues the same `source_file` without re-uploading — useful once you've confirmed the
underlying file was fixed. It's rejected with `400` for any dataset that isn't currently
`failed` (a `ready` dataset has nothing to retry; a `pending` one is already queued).

## Running tests

```bash
pytest
```

Lint:

```bash
ruff check .
```

Both are enforced by CI on every push and pull request — see [CONTRIBUTING.md](CONTRIBUTING.md)
for the branching and commit conventions used in this repo.

## Known scope limits

- The data-quality report still loads a dataset's rows into memory on every request; fine
  at this scale, would move to counts precomputed at ingestion time if that changes
- Webhook URL validation is DNS-rebinding-vulnerable in principle — see
  [Webhooks](#webhooks) above

## Why this project

This project was built to go deep on a specific set of Django/DRF topics rather than to ship a
generic CRUD app: file upload handling, flexible modeling with `JSONField`, dynamic
per-request filtering on JSON data, permissions, CSV parsing/export, and OpenAPI documentation.
