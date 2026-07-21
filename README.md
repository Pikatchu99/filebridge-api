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

- CSV upload with automatic schema detection (column names, inferred type: string, number,
  date, email, boolean, unknown)
- Flexible row storage via `JSONField` — no fixed table per dataset
- Dynamic per-column filtering (`?campus=Paris&status=active`) and global `search`
- Pagination on every list endpoint
- Owner-only permissions (a dataset is only visible to the user who uploaded it)
- CSV export
- OpenAPI schema + Swagger UI via drf-spectacular
- Full test suite (models, ingestion service, API views) written test-first

## Tech stack

- Python 3.12
- Django 5.2 LTS
- Django REST Framework 3.16
- drf-spectacular (OpenAPI 3 / Swagger UI)
- django-filter
- django-environ (12-factor config)
- SQLite locally, PostgreSQL in production (via `DATABASE_URL`)
- pytest + pytest-django + factory-boy
- ruff (lint + format)
- GitHub Actions CI

## API examples

Upload a CSV (creates the dataset and ingests it synchronously):

```bash
curl -u alice:password -X POST http://localhost:8000/api/datasets/upload/ \
  -F "name=inscriptions" \
  -F "file=@inscriptions.csv"
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

Delete a dataset:

```bash
curl -u alice:password -X DELETE http://localhost:8000/api/datasets/1/
```

## Data model

- **Dataset** — one uploaded file: owner, name, status (`pending`/`ready`/`failed`), row/column
  counts, visibility.
- **DatasetColumn** — one detected column per dataset: original header, normalized name,
  detected type, position.
- **DatasetRow** — one row per dataset, stored as a `JSONField` keyed by normalized column
  names.
- **DatasetApiKey** — reserved for future machine-to-machine access to a single dataset
  (hashed key, not implemented in the API yet — see Roadmap).

## Installation

Requirements: Python 3.12+.

```bash
git clone git@github.com:Pikatchu99/filebridge-api.git
cd filebridge-api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Swagger UI: http://localhost:8000/api/docs/
Raw OpenAPI schema: http://localhost:8000/api/schema/

## Environment variables

See [.env.example](.env.example):

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Django secret key | dev-only insecure value |
| `DEBUG` | Debug mode | `False` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `DATABASE_URL` | DB connection string (sqlite:// or postgres://) | local SQLite file |
| `FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES` | Max upload size in bytes | `10485760` (10 MB) |

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

## Roadmap (V2)

- `.xlsx` (Excel) support
- Richer type detection + a data-quality report (invalid emails, duplicates, missing values)
- API keys for machine-to-machine, per-dataset access
- Public / read-only dataset sharing
- Async ingestion for large files (Celery/RQ)
- Upload size limits enforced with retry/import status
- Preview before import, post-import webhooks, rate limiting
- Docker + deployment to Render/Fly/Railway

## Why this project

This project was built to go deep on a specific set of Django/DRF topics rather than to ship a
generic CRUD app: file upload handling, flexible modeling with `JSONField`, dynamic
per-request filtering on JSON data, permissions, CSV parsing/export, and OpenAPI documentation.
