import io

import pytest
from django.contrib.auth import get_user_model

from apps.datasets.models import Dataset
from apps.datasets.services.ingestion import ingest_csv_file
from apps.datasets.services.quality import build_quality_report

pytestmark = pytest.mark.django_db

User = get_user_model()


def _ingest(dataset, content: str):
    ingest_csv_file(dataset, io.BytesIO(content.encode("utf-8")))
    dataset.refresh_from_db()
    return dataset


@pytest.fixture
def owner():
    return User.objects.create_user(username="owner", password="pass1234")


class TestBuildQualityReport:
    def test_reports_row_and_column_counts(self, owner):
        dataset = Dataset.objects.create(
            owner=owner, name="inscriptions", original_filename="inscriptions.csv"
        )
        _ingest(dataset, "name,email\nSarah,sarah@example.com\nLea,lea@example.com\n")

        report = build_quality_report(dataset)

        assert report["row_count"] == 2
        assert len(report["columns"]) == 2
        assert report["duplicate_row_count"] == 0

    def test_flags_invalid_values_against_the_detected_type(self, owner):
        dataset = Dataset.objects.create(owner=owner, name="leads", original_filename="leads.csv")
        # 9 valid emails + 1 typo: still detected as an "email" column (majority rule),
        # and the report should surface that one typo as invalid.
        rows = "\n".join(f"row{i},valid{i}@example.com" for i in range(9))
        _ingest(dataset, f"name,email\n{rows}\nrow9,not-an-email\n")

        report = build_quality_report(dataset)
        email_column = next(c for c in report["columns"] if c["name"] == "email")

        assert email_column["detected_type"] == "email"
        assert email_column["invalid_count"] == 1
        assert email_column["missing_count"] == 0

    def test_counts_missing_values(self, owner):
        dataset = Dataset.objects.create(owner=owner, name="leads", original_filename="leads.csv")
        _ingest(dataset, "name,campus\nSarah,Paris\nLea,\nMarc,\n")

        report = build_quality_report(dataset)
        campus_column = next(c for c in report["columns"] if c["name"] == "campus")

        assert campus_column["missing_count"] == 2

    def test_counts_exact_duplicate_rows(self, owner):
        dataset = Dataset.objects.create(owner=owner, name="leads", original_filename="leads.csv")
        _ingest(
            dataset,
            "name,email\nSarah,sarah@example.com\nSarah,sarah@example.com\nLea,lea@example.com\n",
        )

        report = build_quality_report(dataset)

        assert report["duplicate_row_count"] == 1

    def test_string_and_unknown_columns_report_zero_invalid(self, owner):
        dataset = Dataset.objects.create(owner=owner, name="notes", original_filename="notes.csv")
        _ingest(dataset, "note\nanything goes here\nso does this !@#\n")

        report = build_quality_report(dataset)
        note_column = report["columns"][0]

        assert note_column["invalid_count"] == 0

    def test_empty_dataset_reports_zero_everything(self, owner):
        dataset = Dataset.objects.create(owner=owner, name="empty", original_filename="empty.csv")
        _ingest(dataset, "name,email\n")

        report = build_quality_report(dataset)

        assert report["row_count"] == 0
        assert report["duplicate_row_count"] == 0
        assert all(c["missing_count"] == 0 and c["invalid_count"] == 0 for c in report["columns"])
