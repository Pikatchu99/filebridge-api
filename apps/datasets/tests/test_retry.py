import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
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
def other_user():
    return User.objects.create_user(username="other", password="pass1234")


@pytest.fixture
def client_as(owner):
    client = APIClient()
    client.force_authenticate(user=owner)
    return client


@pytest.fixture
def failed_dataset(owner):
    dataset = Dataset.objects.create(
        owner=owner,
        name="broken",
        original_filename="broken.csv",
        status=Dataset.Status.FAILED,
        failure_reason="The uploaded file has no header row.",
    )
    dataset.source_file.save("broken.csv", ContentFile(b"\ndata\n"), save=True)
    return dataset


@pytest.fixture
def ready_dataset(owner):
    dataset = Dataset.objects.create(owner=owner, name="fine", original_filename="fine.csv")
    dataset.source_file.save(
        "fine.csv", ContentFile(b"name,email\nSarah,sarah@example.com\n"), save=True
    )
    from apps.datasets.tasks import ingest_dataset_file

    ingest_dataset_file(dataset.id)
    dataset.refresh_from_db()
    return dataset


class TestRetry:
    url_name = "dataset-retry"

    def test_owner_can_retry_a_failed_dataset(self, client_as, failed_dataset):
        # Fix the underlying source file so the retry actually succeeds this time —
        # simulates "the user re-saved the same file correctly and clicks retry".
        failed_dataset.source_file.save(
            "broken.csv",
            ContentFile(b"name,email\nSarah,sarah@example.com\n"),
            save=True,
        )

        response = client_as.post(reverse(self.url_name, args=[failed_dataset.id]))

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == Dataset.Status.READY
        assert response.data["row_count"] == 1
        assert response.data["failure_reason"] == ""

    def test_retry_clears_stale_columns_and_rows_before_reingesting(
        self, client_as, failed_dataset
    ):
        DatasetColumn.objects.create(
            dataset=failed_dataset, name_original="stale", name_normalized="stale", position=0
        )
        DatasetRow.objects.create(dataset=failed_dataset, row_index=0, data={"stale": "x"})
        failed_dataset.source_file.save("broken.csv", ContentFile(b"name\nSarah\n"), save=True)

        client_as.post(reverse(self.url_name, args=[failed_dataset.id]))

        assert not DatasetColumn.objects.filter(
            dataset=failed_dataset, name_normalized="stale"
        ).exists()
        assert DatasetColumn.objects.filter(dataset=failed_dataset).count() == 1

    def test_cannot_retry_a_ready_dataset(self, client_as, ready_dataset):
        response = client_as.post(reverse(self.url_name, args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        ready_dataset.refresh_from_db()
        assert ready_dataset.status == Dataset.Status.READY

    def test_cannot_retry_a_pending_dataset(self, client_as, owner):
        dataset = Dataset.objects.create(
            owner=owner, name="in-flight", original_filename="in-flight.csv"
        )
        dataset.source_file.save("in-flight.csv", ContentFile(b"name\nSarah\n"), save=True)
        # left at the default PENDING status, simulating "task hasn't run yet"

        response = client_as.post(reverse(self.url_name, args=[dataset.id]))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_owner_cannot_retry(self, failed_dataset, other_user):
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.post(reverse(self.url_name, args=[failed_dataset.id]))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_still_failed_if_the_underlying_problem_was_never_fixed(
        self, client_as, failed_dataset
    ):
        response = client_as.post(reverse(self.url_name, args=[failed_dataset.id]))
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == Dataset.Status.FAILED
        assert response.data["failure_reason"]
