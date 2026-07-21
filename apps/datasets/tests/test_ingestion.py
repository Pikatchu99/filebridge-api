import io

import pytest
from django.contrib.auth import get_user_model

from apps.datasets.exceptions import EmptyFileError, InvalidCsvError, NoHeaderError
from apps.datasets.models import Dataset, DatasetColumn, DatasetRow
from apps.datasets.services.ingestion import ingest_csv_file, normalize_column_name

pytestmark = pytest.mark.django_db

User = get_user_model()


def csv_file(content: str, name: str = "data.csv"):
    return io.BytesIO(content.encode("utf-8"))


@pytest.fixture
def user():
    return User.objects.create_user(username="modeste", password="pass1234")


@pytest.fixture
def dataset(user):
    return Dataset.objects.create(
        owner=user, name="inscriptions", original_filename="inscriptions.csv"
    )


class TestNormalizeColumnName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Email", "email"),
            ("First Name", "first_name"),
            (" Campus ", "campus"),
            ("E-mail Address", "e_mail_address"),
            ("2027 Promo", "2027_promo"),
            ("", "column"),
        ],
    )
    def test_normalizes_various_inputs(self, raw, expected):
        assert normalize_column_name(raw) == expected

    def test_disambiguates_duplicates(self):
        seen = {}
        names = ["Email", "email", "Email"]
        result = [normalize_column_name(n, seen=seen) for n in names]
        assert result == ["email", "email_2", "email_3"]


class TestIngestCsvFile:
    def test_creates_columns_and_rows(self, dataset):
        content = "name,email,campus\nSarah,sarah@example.com,Paris\nLea,lea@example.com,Lyon\n"
        ingest_csv_file(dataset, csv_file(content))

        dataset.refresh_from_db()
        assert dataset.status == Dataset.Status.READY
        assert dataset.row_count == 2
        assert dataset.column_count == 3

        columns = list(DatasetColumn.objects.filter(dataset=dataset).order_by("position"))
        assert [c.name_normalized for c in columns] == ["name", "email", "campus"]
        assert columns[1].detected_type == DatasetColumn.ColumnType.EMAIL

        rows = list(DatasetRow.objects.filter(dataset=dataset).order_by("row_index"))
        assert rows[0].data == {"name": "Sarah", "email": "sarah@example.com", "campus": "Paris"}
        assert rows[1].row_index == 1

    def test_normalizes_duplicate_and_messy_headers(self, dataset):
        content = "Full Name,Email,email\nSarah,a@b.com,c@d.com\n"
        ingest_csv_file(dataset, csv_file(content))

        columns = list(DatasetColumn.objects.filter(dataset=dataset).order_by("position"))
        assert [c.name_normalized for c in columns] == ["full_name", "email", "email_2"]

    def test_raises_on_empty_file(self, dataset):
        with pytest.raises(EmptyFileError):
            ingest_csv_file(dataset, csv_file(""))

    def test_raises_when_header_only_no_rows_is_allowed(self, dataset):
        ingest_csv_file(dataset, csv_file("name,email\n"))
        dataset.refresh_from_db()
        assert dataset.status == Dataset.Status.READY
        assert dataset.row_count == 0

    def test_marks_dataset_failed_and_reraises_on_bad_header(self, dataset):
        with pytest.raises(NoHeaderError):
            ingest_csv_file(dataset, csv_file("\ndata\n"))
        dataset.refresh_from_db()
        assert dataset.status == Dataset.Status.FAILED
        assert dataset.failure_reason

    def test_marks_dataset_failed_on_non_utf8_content(self, dataset):
        non_utf8 = io.BytesIO(b"name,email\n\xff\xfe,a@b.com\n")
        with pytest.raises(InvalidCsvError):
            ingest_csv_file(dataset, non_utf8)
        dataset.refresh_from_db()
        assert dataset.status == Dataset.Status.FAILED
        assert dataset.failure_reason

    def test_marks_dataset_failed_on_cell_over_csv_field_size_limit(self, dataset):
        huge_cell = "a" * 200_000
        content = f"name,email\n{huge_cell},a@b.com\n"
        with pytest.raises(InvalidCsvError):
            ingest_csv_file(dataset, csv_file(content))
        dataset.refresh_from_db()
        assert dataset.status == Dataset.Status.FAILED
        assert dataset.failure_reason
