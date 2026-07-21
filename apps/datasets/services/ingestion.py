"""CSV ingestion: parse an uploaded file into Dataset/DatasetColumn/DatasetRow rows.

V1 scope is CSV only — Excel support is a V2 concern (see README roadmap).
"""

import csv
import io
import re

from django.db import transaction

from apps.datasets.exceptions import EmptyFileError, InvalidCsvError, NoHeaderError
from apps.datasets.models import Dataset, DatasetColumn, DatasetRow

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOOLEAN_VALUES = {"true", "false"}


def normalize_column_name(raw: str, seen: dict | None = None) -> str:
    """Turn a raw CSV header into a slug-safe, snake_case column name.

    When `seen` (a dict shared across the whole header) is provided, repeated
    names are disambiguated as name, name_2, name_3, ...
    """
    slug = _NON_ALNUM_RE.sub("_", raw.strip().lower()).strip("_") or "column"

    if seen is None:
        return slug

    if slug not in seen:
        seen[slug] = 1
        return slug

    seen[slug] += 1
    candidate = f"{slug}_{seen[slug]}"
    while candidate in seen:
        seen[slug] += 1
        candidate = f"{slug}_{seen[slug]}"
    seen[candidate] = 1
    return candidate


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def detect_column_type(values: list[str]) -> str:
    """Infer a DatasetColumn.ColumnType from a sample of raw string values."""
    non_empty = [v.strip() for v in values if v and v.strip()]
    if not non_empty:
        return DatasetColumn.ColumnType.UNKNOWN
    if all(_EMAIL_RE.match(v) for v in non_empty):
        return DatasetColumn.ColumnType.EMAIL
    if all(v.lower() in _BOOLEAN_VALUES for v in non_empty):
        return DatasetColumn.ColumnType.BOOLEAN
    if all(_is_number(v) for v in non_empty):
        return DatasetColumn.ColumnType.NUMBER
    if all(_DATE_RE.match(v) for v in non_empty):
        return DatasetColumn.ColumnType.DATE
    return DatasetColumn.ColumnType.STRING


def ingest_csv_file(dataset: Dataset, file_obj) -> None:
    """Parse `file_obj` as CSV and populate columns/rows for `dataset`.

    Marks the dataset FAILED (with a reason) and re-raises on unusable input,
    so callers can surface a clean 4xx instead of a 500.
    """
    raw_bytes = file_obj.read()
    if not raw_bytes or not raw_bytes.strip():
        _fail(dataset, "The uploaded file is empty.")
        raise EmptyFileError("The uploaded file is empty.")

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        _fail(dataset, "The uploaded file isn't valid UTF-8 text.")
        raise InvalidCsvError("The uploaded file isn't valid UTF-8 text.") from exc

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader, [])
    except csv.Error as exc:
        _fail(dataset, "The uploaded file isn't valid CSV.")
        raise InvalidCsvError("The uploaded file isn't valid CSV.") from exc

    if not header or all(not cell.strip() for cell in header):
        _fail(dataset, "The uploaded file has no header row.")
        raise NoHeaderError("The uploaded file has no header row.")

    seen: dict[str, int] = {}
    normalized_names = [normalize_column_name(cell, seen=seen) for cell in header]

    try:
        data_rows = list(reader)
    except csv.Error as exc:
        _fail(dataset, "The uploaded file isn't valid CSV.")
        raise InvalidCsvError("The uploaded file isn't valid CSV.") from exc

    columns_values: list[list[str]] = [[] for _ in normalized_names]
    for row in data_rows:
        for i in range(len(normalized_names)):
            columns_values[i].append(row[i] if i < len(row) else "")
    detected_types = [detect_column_type(values) for values in columns_values]

    with transaction.atomic():
        DatasetColumn.objects.filter(dataset=dataset).delete()
        DatasetRow.objects.filter(dataset=dataset).delete()

        columns = DatasetColumn.objects.bulk_create(
            DatasetColumn(
                dataset=dataset,
                name_original=original,
                name_normalized=normalized,
                detected_type=col_type,
                position=position,
            )
            for position, (original, normalized, col_type) in enumerate(
                zip(header, normalized_names, detected_types)
            )
        )

        rows = DatasetRow.objects.bulk_create(
            DatasetRow(
                dataset=dataset,
                row_index=row_index,
                data={
                    normalized_names[i]: (row[i] if i < len(row) else "")
                    for i in range(len(normalized_names))
                },
            )
            for row_index, row in enumerate(data_rows)
        )

        dataset.status = Dataset.Status.READY
        dataset.row_count = len(rows)
        dataset.column_count = len(columns)
        dataset.failure_reason = ""
        dataset.save(update_fields=["status", "row_count", "column_count", "failure_reason"])


def _fail(dataset: Dataset, reason: str) -> None:
    dataset.status = Dataset.Status.FAILED
    dataset.failure_reason = reason
    dataset.save(update_fields=["status", "failure_reason"])
