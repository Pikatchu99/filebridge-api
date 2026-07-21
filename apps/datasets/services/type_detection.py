"""Infer a column's type from its values, and validate individual values against it.

Shared by ingestion (detect_column_type, at upload time) and the data-quality report
(is_valid_for_type, on demand) so both agree on exactly what "an email" or "a number" means.
"""

import re

from apps.datasets.models import DatasetColumn

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOOLEAN_VALUES = {"true", "false"}

# A type is assigned if at least this fraction of a column's non-empty values match it.
# Real CSV exports routinely have a handful of typos/blanks; requiring a perfect match
# would push almost every column to "string". The quality report then surfaces exactly
# which values are in the non-matching minority.
_MAJORITY_THRESHOLD = 0.9


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _is_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def _is_date(value: str) -> bool:
    return bool(_DATE_RE.match(value))


def _is_boolean(value: str) -> bool:
    return value.lower() in _BOOLEAN_VALUES


# Checked in this order because it's most-specific-first: e.g. "true"/"false" would also
# satisfy a looser "is this a plausible date" test if checked out of order.
_TYPE_CHECKS = [
    (DatasetColumn.ColumnType.EMAIL, _is_email),
    (DatasetColumn.ColumnType.BOOLEAN, _is_boolean),
    (DatasetColumn.ColumnType.NUMBER, _is_number),
    (DatasetColumn.ColumnType.DATE, _is_date),
]


def detect_column_type(values: list[str]) -> str:
    """Infer a DatasetColumn.ColumnType from a sample of raw string values."""
    non_empty = [v.strip() for v in values if v and v.strip()]
    if not non_empty:
        return DatasetColumn.ColumnType.UNKNOWN

    for column_type, check in _TYPE_CHECKS:
        match_ratio = sum(1 for v in non_empty if check(v)) / len(non_empty)
        if match_ratio >= _MAJORITY_THRESHOLD:
            return column_type

    return DatasetColumn.ColumnType.STRING


def is_valid_for_type(value: str, column_type: str) -> bool:
    """Does `value` match the format expected for `column_type`?

    STRING and UNKNOWN have no format to validate against, so anything counts as valid.
    """
    for candidate_type, check in _TYPE_CHECKS:
        if candidate_type == column_type:
            return check(value)
    return True
