import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.datasets.models import Dataset, DatasetColumn, DatasetRow

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def owner():
    return User.objects.create_user(username="owner", password="pass1234")


@pytest.fixture
def client_as(owner):
    client = APIClient()
    client.force_authenticate(user=owner)
    return client


class TestPreviewEndpoint:
    url = reverse("dataset-preview")

    def test_requires_authentication(self):
        client = APIClient()
        response = client.post(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_returns_schema_and_sample_without_creating_a_dataset(self, client_as):
        upload = SimpleUploadedFile(
            "leads.csv", b"name,email\nSarah,sarah@example.com\n", content_type="text/csv"
        )

        response = client_as.post(self.url, {"file": upload}, format="multipart")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["row_count"] == 1
        assert [c["name_normalized"] for c in response.data["columns"]] == ["name", "email"]
        assert response.data["sample_rows"][0]["name"] == "Sarah"
        assert Dataset.objects.count() == 0
        assert DatasetColumn.objects.count() == 0
        assert DatasetRow.objects.count() == 0

    def test_rejects_non_csv_xlsx_extension(self, client_as):
        upload = SimpleUploadedFile("leads.txt", b"name\nSarah\n", content_type="text/plain")
        response = client_as.post(self.url, {"file": upload}, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_file_over_size_limit(self, client_as, settings):
        settings.FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES = 10
        upload = SimpleUploadedFile(
            "leads.csv", b"name,email\nSarah,sarah@example.com\n", content_type="text/csv"
        )
        response = client_as.post(self.url, {"file": upload}, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_returns_400_on_unparseable_content(self, client_as):
        upload = SimpleUploadedFile("bad.csv", b"\n,\n", content_type="text/csv")
        response = client_as.post(self.url, {"file": upload}, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_works_for_xlsx_too(self, client_as):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["name", "email"])
        sheet.append(["Sarah", "sarah@example.com"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        upload = SimpleUploadedFile(
            "leads.xlsx",
            buffer.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = client_as.post(self.url, {"file": upload}, format="multipart")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["row_count"] == 1
