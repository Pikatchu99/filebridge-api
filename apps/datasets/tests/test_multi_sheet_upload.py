import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook
from rest_framework import status
from rest_framework.test import APIClient

from apps.datasets.models import Dataset

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


def multi_sheet_xlsx(sheets: dict[str, list[list]]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name, rows in sheets.items():
        sheet = workbook.create_sheet(sheet_name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestMultiSheetUpload:
    url = reverse("dataset-upload")

    def _upload(self, client_as, content, name="school", extra=None):
        upload = SimpleUploadedFile(
            "school.xlsx",
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        data = {"name": name, "file": upload, **(extra or {})}
        return client_as.post(self.url, data, format="multipart")

    def test_uploads_every_sheet_as_its_own_dataset_by_default(self, client_as):
        content = multi_sheet_xlsx(
            {
                "Students": [["name"], ["Sarah"], ["Lea"]],
                "Staff": [["name", "role"], ["Marc", "Teacher"]],
            }
        )

        response = self._upload(client_as, content)

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert len(response.data) == 2
        by_sheet = {d["sheet_name"]: d for d in response.data}
        assert by_sheet["Students"]["row_count"] == 2
        assert by_sheet["Students"]["name"] == "school-students"
        assert by_sheet["Staff"]["row_count"] == 1
        assert by_sheet["Staff"]["name"] == "school-staff"
        assert Dataset.objects.filter(owner__isnull=False).count() == 2

    def test_uploads_only_the_requested_sheets(self, client_as):
        content = multi_sheet_xlsx(
            {
                "Students": [["name"], ["Sarah"]],
                "Staff": [["name", "role"], ["Marc", "Teacher"]],
                "Alumni": [["name"], ["Lea"]],
            }
        )

        response = self._upload(client_as, content, extra={"sheet_names": ["Staff", "Alumni"]})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert len(response.data) == 2
        names = {d["sheet_name"] for d in response.data}
        assert names == {"Staff", "Alumni"}

    def test_a_single_requested_sheet_keeps_the_plain_dataset_name(self, client_as):
        content = multi_sheet_xlsx(
            {"Students": [["name"], ["Sarah"]], "Staff": [["name"], ["Marc"]]}
        )

        response = self._upload(client_as, content, extra={"sheet_names": ["Staff"]})

        assert len(response.data) == 1
        assert response.data[0]["name"] == "school"
        assert response.data[0]["sheet_name"] == "Staff"

    def test_a_single_sheet_workbook_also_keeps_the_plain_dataset_name(self, client_as):
        content = multi_sheet_xlsx({"Sheet1": [["name"], ["Sarah"]]})

        response = self._upload(client_as, content)

        assert len(response.data) == 1
        assert response.data[0]["name"] == "school"
        assert response.data[0]["sheet_name"] == "Sheet1"

    def test_rejects_an_unknown_requested_sheet(self, client_as):
        content = multi_sheet_xlsx({"Students": [["name"], ["Sarah"]]})

        response = self._upload(client_as, content, extra={"sheet_names": ["Nonexistent"]})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Dataset.objects.exists()

    def test_rejects_sheet_names_on_a_csv_upload(self, client_as):
        upload = SimpleUploadedFile("leads.csv", b"name\nSarah\n", content_type="text/csv")
        response = client_as.post(
            self.url,
            {"name": "leads", "file": upload, "sheet_names": ["Sheet1"]},
            format="multipart",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Dataset.objects.exists()

    def test_a_name_collision_rejects_the_whole_upload_atomically(self, client_as, owner):
        content = multi_sheet_xlsx(
            {
                "Students": [["name"], ["Sarah"]],
                "Staff": [["name"], ["Marc"]],
            }
        )
        # Pre-create a dataset whose name will collide with the "Staff" sheet's
        # suffixed name, so the second iteration of the creation loop fails.
        Dataset.objects.create(owner=owner, name="school-staff", original_filename="existing.csv")

        response = self._upload(client_as, content)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Atomic: the "Students" sheet must NOT have been created either, even though
        # it would have succeeded on its own.
        assert not Dataset.objects.filter(name="school-students").exists()

    def test_rejects_too_many_requested_sheets(self, client_as, settings):
        settings.FILEBRIDGE_MAX_SHEETS_PER_UPLOAD = 2
        content = multi_sheet_xlsx({"A": [["name"]], "B": [["name"]], "C": [["name"]]})

        response = self._upload(client_as, content, extra={"sheet_names": ["A", "B", "C"]})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Dataset.objects.exists()

    def test_rejects_a_workbook_with_too_many_sheets_by_default(self, client_as, settings):
        settings.FILEBRIDGE_MAX_SHEETS_PER_UPLOAD = 2
        content = multi_sheet_xlsx({"A": [["name"]], "B": [["name"]], "C": [["name"]]})

        response = self._upload(client_as, content)  # no sheet_names => defaults to all

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Dataset.objects.exists()

    def test_one_sheets_ingestion_failure_does_not_block_the_others(self, client_as):
        content = multi_sheet_xlsx(
            {
                "Good": [["name"], ["Sarah"]],
                "Empty": [],
            }
        )

        response = self._upload(client_as, content)

        assert response.status_code == status.HTTP_202_ACCEPTED
        by_sheet = {d["sheet_name"]: d for d in response.data}
        assert by_sheet["Good"]["status"] == Dataset.Status.READY
        assert by_sheet["Empty"]["status"] == Dataset.Status.FAILED
