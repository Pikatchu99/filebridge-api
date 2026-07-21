"""Data-quality report: per-column missing/invalid value counts, plus exact-duplicate rows.

Computed on demand from the already-stored rows/columns — O(rows × columns), which is fine
at this project's scale. A dataset big enough for that to matter belongs to the async
ingestion path (see README roadmap), where these counts would be precomputed at import time
instead.
"""

import json

from apps.datasets.services.type_detection import is_valid_for_type


def build_quality_report(dataset) -> dict:
    columns = list(dataset.columns.all())
    rows = list(dataset.rows.all())

    column_reports = [_column_report(column, rows) for column in columns]

    return {
        "row_count": len(rows),
        "duplicate_row_count": _duplicate_row_count(rows),
        "columns": column_reports,
    }


def _column_report(column, rows) -> dict:
    values = [str(row.data.get(column.name_normalized, "")) for row in rows]
    non_empty = [v.strip() for v in values if v.strip()]

    missing_count = len(values) - len(non_empty)
    invalid_count = sum(1 for v in non_empty if not is_valid_for_type(v, column.detected_type))

    return {
        "name": column.name_normalized,
        "detected_type": column.detected_type,
        "missing_count": missing_count,
        "invalid_count": invalid_count,
    }


def _duplicate_row_count(rows) -> int:
    seen = set()
    duplicates = 0
    for row in rows:
        key = json.dumps(row.data, sort_keys=True)
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates
