import io
import os

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook
from rest_framework import status
from rest_framework.test import APIClient

from apps.datasets.models import Dataset
from apps.datasets.services.ingestion import ingest_csv_file

pytestmark = pytest.mark.django_db

User = get_user_model()


def _ingest(dataset, content: str):
    ingest_csv_file(dataset, io.BytesIO(content.encode("utf-8")))
    dataset.refresh_from_db()
    return dataset


@pytest.fixture
def owner():
    return User.objects.create_user(username="owner", password="pass1234")


@pytest.fixture
def other_user():
    return User.objects.create_user(username="other", password="pass1234")


@pytest.fixture
def client_as(owner):
    client = APIClient()
    client.force_authenticate(user=owner)
    return client


@pytest.fixture
def ready_dataset(owner):
    dataset = Dataset.objects.create(
        owner=owner, name="inscriptions", original_filename="inscriptions.csv"
    )
    return _ingest(
        dataset,
        "name,email,campus\n"
        "Sarah,sarah@example.com,Paris\n"
        "Lea,lea@example.com,Lyon\n"
        "Marc,marc@example.com,Paris\n",
    )


class TestUpload:
    url = reverse("dataset-upload")

    def test_requires_authentication(self):
        client = APIClient()
        response = client.post(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_upload_is_pending_until_the_worker_actually_runs_the_task(self, client_as, mocker):
        # Simulates a real deployment: .delay() hands off to a worker and returns
        # immediately, without running the task inline the way eager mode does for
        # every other test in this file.
        mocker.patch("apps.datasets.views.ingest_dataset_file.delay")
        upload = SimpleUploadedFile(
            "students.csv", b"name,email\nSarah,sarah@example.com\n", content_type="text/csv"
        )

        response = client_as.post(
            self.url, {"name": "students", "file": upload}, format="multipart"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == Dataset.Status.PENDING
        dataset = Dataset.objects.get(name="students")
        assert dataset.status == Dataset.Status.PENDING
        assert dataset.row_count == 0

    def test_uploads_csv_and_creates_ready_dataset(self, client_as):
        csv_content = b"name,email\nSarah,sarah@example.com\n"
        upload = SimpleUploadedFile("students.csv", csv_content, content_type="text/csv")

        response = client_as.post(
            self.url, {"name": "students", "file": upload}, format="multipart"
        )

        # 202: ingestion runs in a Celery task, not this request. Under CELERY_TASK_ALWAYS_EAGER
        # (see conftest.py) the task has already run by the time this response is built, so
        # status/row_count/column_count already reflect the outcome — that wouldn't hold with
        # a real worker, where the client would poll GET .../ instead.
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == Dataset.Status.READY
        assert response.data["row_count"] == 1
        assert response.data["column_count"] == 2
        assert Dataset.objects.filter(name="students").exists()

    def test_rejects_non_csv_extension(self, client_as):
        upload = SimpleUploadedFile("students.txt", b"name\nSarah\n", content_type="text/plain")
        response = client_as.post(
            self.url, {"name": "students", "file": upload}, format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_file_over_size_limit(self, client_as, settings):
        settings.FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES = 10
        upload = SimpleUploadedFile(
            "students.csv", b"name,email\nSarah,sarah@example.com\n", content_type="text/csv"
        )
        response = client_as.post(
            self.url, {"name": "students", "file": upload}, format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_truly_empty_file_at_validation(self, client_as):
        upload = SimpleUploadedFile("empty.csv", b"", content_type="text/csv")
        response = client_as.post(self.url, {"name": "empty", "file": upload}, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Dataset.objects.filter(name="empty").exists()

    def test_marks_dataset_failed_on_unparseable_content(self, client_as):
        upload = SimpleUploadedFile("bad.csv", b"\n,\n", content_type="text/csv")
        response = client_as.post(self.url, {"name": "bad", "file": upload}, format="multipart")
        # Content-level failures (bad header, encoding, etc.) surface asynchronously — the
        # upload itself is accepted, and the failure shows up on the dataset's status.
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == Dataset.Status.FAILED
        assert Dataset.objects.get(name="bad").status == Dataset.Status.FAILED

    def test_uploads_xlsx_and_creates_ready_dataset(self, client_as):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["name", "email"])
        sheet.append(["Sarah", "sarah@example.com"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        upload = SimpleUploadedFile(
            "students.xlsx",
            buffer.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = client_as.post(
            self.url, {"name": "students-xlsx", "file": upload}, format="multipart"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == Dataset.Status.READY
        assert response.data["row_count"] == 1
        assert response.data["column_count"] == 2


class TestListAndRetrieve:
    def test_list_only_returns_own_datasets(self, client_as, ready_dataset, other_user):
        Dataset.objects.create(owner=other_user, name="not-mine", original_filename="x.csv")

        response = client_as.get(reverse("dataset-list"))

        assert response.status_code == status.HTTP_200_OK
        names = [d["name"] for d in response.data["results"]]
        assert names == ["inscriptions"]

    def test_retrieve_own_dataset(self, client_as, ready_dataset):
        response = client_as.get(reverse("dataset-detail", args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "inscriptions"

    def test_retrieve_other_users_dataset_is_not_found(self, ready_dataset, other_user):
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.get(reverse("dataset-detail", args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestSchema:
    def test_returns_columns_in_position_order(self, client_as, ready_dataset):
        response = client_as.get(reverse("dataset-schema", args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_200_OK
        names = [c["name_normalized"] for c in response.data]
        assert names == ["name", "email", "campus"]
        assert response.data[1]["detected_type"] == "email"


class TestRows:
    def test_lists_rows_paginated(self, client_as, ready_dataset):
        response = client_as.get(reverse("dataset-rows", args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 3

    def test_filters_by_known_column(self, client_as, ready_dataset):
        response = client_as.get(
            reverse("dataset-rows", args=[ready_dataset.id]), {"campus": "Paris"}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        assert all(r["data"]["campus"] == "Paris" for r in response.data["results"])

    def test_unknown_column_filter_returns_400(self, client_as, ready_dataset):
        response = client_as.get(
            reverse("dataset-rows", args=[ready_dataset.id]), {"not_a_column": "x"}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_global_search_matches_any_column(self, client_as, ready_dataset):
        response = client_as.get(
            reverse("dataset-rows", args=[ready_dataset.id]), {"search": "sarah"}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["data"]["name"] == "Sarah"

    def test_retrieve_single_row(self, client_as, ready_dataset):
        row = ready_dataset.rows.get(row_index=0)
        response = client_as.get(reverse("dataset-row-detail", args=[ready_dataset.id, row.id]))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["data"]["name"] == "Sarah"

    def test_retrieve_missing_row_is_404(self, client_as, ready_dataset):
        response = client_as.get(reverse("dataset-row-detail", args=[ready_dataset.id, 9999]))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestExport:
    def test_export_returns_csv_with_header_and_rows(self, client_as, ready_dataset):
        response = client_as.get(reverse("dataset-export", args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/csv"
        body = response.content.decode("utf-8")
        lines = body.strip().splitlines()
        assert lines[0] == "name,email,campus"
        assert len(lines) == 4

    def test_export_neutralizes_formula_injection(self, client_as, owner):
        dataset = Dataset.objects.create(
            owner=owner, name="malicious", original_filename="malicious.csv"
        )
        _ingest(dataset, "name,note\nMarc,=cmd|'/c calc'!A1\n")

        response = client_as.get(reverse("dataset-export", args=[dataset.id]))

        body = response.content.decode("utf-8")
        assert "'=cmd|'/c calc'!A1" in body


class TestDestroy:
    def test_owner_can_delete(self, client_as, ready_dataset):
        response = client_as.delete(reverse("dataset-detail", args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Dataset.objects.filter(id=ready_dataset.id).exists()

    def test_non_owner_cannot_delete(self, ready_dataset, other_user):
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.delete(reverse("dataset-detail", args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Dataset.objects.filter(id=ready_dataset.id).exists()

    def test_deleting_a_dataset_removes_its_source_file_from_disk(self, client_as):
        upload = SimpleUploadedFile(
            "students.csv", b"name,email\nSarah,sarah@example.com\n", content_type="text/csv"
        )
        upload_response = client_as.post(
            reverse("dataset-upload"), {"name": "students", "file": upload}, format="multipart"
        )
        dataset = Dataset.objects.get(id=upload_response.data["id"])
        file_path = dataset.source_file.path
        assert os.path.exists(file_path)

        response = client_as.delete(reverse("dataset-detail", args=[dataset.id]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not os.path.exists(file_path)
