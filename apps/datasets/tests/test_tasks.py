import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile

from apps.datasets.models import Dataset, DatasetColumn, DatasetRow
from apps.datasets.tasks import ingest_dataset_file

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def owner():
    return User.objects.create_user(username="owner", password="pass1234")


class TestIngestDatasetFileTask:
    def test_ingests_a_csv_source_file(self, owner):
        dataset = Dataset.objects.create(
            owner=owner, name="inscriptions", original_filename="inscriptions.csv"
        )
        dataset.source_file.save(
            "inscriptions.csv",
            ContentFile(b"name,email\nSarah,sarah@example.com\n"),
            save=True,
        )

        ingest_dataset_file(dataset.id)

        dataset.refresh_from_db()
        assert dataset.status == Dataset.Status.READY
        assert dataset.row_count == 1
        assert DatasetColumn.objects.filter(dataset=dataset).count() == 2

    def test_ingests_an_xlsx_source_file(self, owner):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["name", "email"])
        sheet.append(["Sarah", "sarah@example.com"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        dataset = Dataset.objects.create(
            owner=owner, name="inscriptions", original_filename="inscriptions.xlsx"
        )
        dataset.source_file.save("inscriptions.xlsx", ContentFile(buffer.read()), save=True)

        ingest_dataset_file(dataset.id)

        dataset.refresh_from_db()
        assert dataset.status == Dataset.Status.READY
        assert dataset.row_count == 1

    def test_swallows_ingestion_errors_the_dataset_already_recorded(self, owner):
        dataset = Dataset.objects.create(owner=owner, name="bad", original_filename="bad.csv")
        dataset.source_file.save("bad.csv", ContentFile(b""), save=True)

        ingest_dataset_file(dataset.id)  # must not raise

        dataset.refresh_from_db()
        assert dataset.status == Dataset.Status.FAILED
        assert dataset.failure_reason

    def test_does_not_raise_when_the_dataset_no_longer_exists(self):
        deleted_id = 999_999
        assert not Dataset.objects.filter(id=deleted_id).exists()

        ingest_dataset_file(deleted_id)  # must not raise

    def test_does_not_leave_stale_rows_on_reingestion(self, owner):
        dataset = Dataset.objects.create(
            owner=owner, name="inscriptions", original_filename="inscriptions.csv"
        )
        dataset.source_file.save("inscriptions.csv", ContentFile(b"name\nSarah\nLea\n"), save=True)
        ingest_dataset_file(dataset.id)
        assert DatasetRow.objects.filter(dataset=dataset).count() == 2

        dataset.source_file.save("inscriptions.csv", ContentFile(b"name\nMarc\n"), save=True)
        ingest_dataset_file(dataset.id)

        assert DatasetRow.objects.filter(dataset=dataset).count() == 1
