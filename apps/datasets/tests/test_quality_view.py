import io

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
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
def dataset(owner):
    dataset = Dataset.objects.create(owner=owner, name="leads", original_filename="leads.csv")
    # 10/11 valid emails (>=90%) so the column is still detected as "email" — the one
    # typo shows up as invalid_count, not a downgrade to "string" (see type_detection).
    rows = "\n".join(f"person{i},a{i}@example.com,Lyon" for i in range(8))
    return _ingest(
        dataset,
        "name,email,campus\n"
        "Sarah,sarah@example.com,Paris\n"
        "Sarah,sarah@example.com,Paris\n"  # exact duplicate of the row above
        f"{rows}\n"
        "Lea,not-an-email,\n",  # invalid email + missing campus
    )


@pytest.fixture
def public_dataset(owner):
    dataset = Dataset.objects.create(
        owner=owner, name="public-leads", original_filename="leads.csv", is_public=True
    )
    return _ingest(dataset, "name,email\nMarc,marc@example.com\n")


class TestQualityEndpoint:
    def test_owner_gets_the_full_report(self, client_as, dataset):
        response = client_as.get(reverse("dataset-quality", args=[dataset.id]))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["row_count"] == 11
        assert response.data["duplicate_row_count"] == 1
        email_column = next(c for c in response.data["columns"] if c["name"] == "email")
        assert email_column["detected_type"] == "email"
        assert email_column["invalid_count"] == 1
        campus_column = next(c for c in response.data["columns"] if c["name"] == "campus")
        assert campus_column["missing_count"] == 1

    def test_non_owner_cannot_read_a_private_datasets_quality_report(self, dataset, other_user):
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.get(reverse("dataset-quality", args=[dataset.id]))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_anonymous_can_read_a_public_datasets_quality_report(self, public_dataset):
        client = APIClient()
        response = client.get(reverse("dataset-quality", args=[public_dataset.id]))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["row_count"] == 1
