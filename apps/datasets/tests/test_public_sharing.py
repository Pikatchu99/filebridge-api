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
def private_dataset(owner):
    dataset = Dataset.objects.create(
        owner=owner, name="inscriptions", original_filename="inscriptions.csv"
    )
    return _ingest(
        dataset, "name,email,campus\nSarah,sarah@example.com,Paris\nLea,lea@example.com,Lyon\n"
    )


@pytest.fixture
def public_dataset(owner):
    dataset = Dataset.objects.create(
        owner=owner, name="public-leads", original_filename="leads.csv", is_public=True
    )
    return _ingest(dataset, "name,email\nMarc,marc@example.com\n")


class TestToggleVisibility:
    url_name = "dataset-visibility"

    def test_owner_can_make_a_dataset_public(self, client_as, private_dataset):
        response = client_as.patch(
            reverse(self.url_name, args=[private_dataset.id]), {"is_public": True}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_public"] is True
        private_dataset.refresh_from_db()
        assert private_dataset.is_public is True

    def test_owner_can_make_a_dataset_private_again(self, client_as, public_dataset):
        response = client_as.patch(
            reverse(self.url_name, args=[public_dataset.id]), {"is_public": False}
        )
        assert response.status_code == status.HTTP_200_OK
        public_dataset.refresh_from_db()
        assert public_dataset.is_public is False

    def test_non_owner_cannot_toggle_visibility(self, private_dataset, other_user):
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.patch(
            reverse(self.url_name, args=[private_dataset.id]), {"is_public": True}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        private_dataset.refresh_from_db()
        assert private_dataset.is_public is False

    def test_anonymous_cannot_toggle_visibility(self, private_dataset):
        client = APIClient()
        response = client.patch(
            reverse(self.url_name, args=[private_dataset.id]), {"is_public": True}
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_only_is_public_is_writable_through_this_endpoint(self, client_as, private_dataset):
        response = client_as.patch(
            reverse(self.url_name, args=[private_dataset.id]),
            {"is_public": True, "name": "renamed", "row_count": 999},
        )
        assert response.status_code == status.HTTP_200_OK
        private_dataset.refresh_from_db()
        assert private_dataset.name == "inscriptions"
        assert private_dataset.row_count == 2


class TestPublicReadAccess:
    def test_anonymous_can_read_schema_rows_export_of_a_public_dataset(self, public_dataset):
        client = APIClient()

        schema = client.get(reverse("dataset-schema", args=[public_dataset.id]))
        rows = client.get(reverse("dataset-rows", args=[public_dataset.id]))
        export = client.get(reverse("dataset-export", args=[public_dataset.id]))
        row = public_dataset.rows.first()
        row_detail = client.get(reverse("dataset-row-detail", args=[public_dataset.id, row.id]))

        assert schema.status_code == status.HTTP_200_OK
        assert rows.status_code == status.HTTP_200_OK
        assert export.status_code == status.HTTP_200_OK
        assert row_detail.status_code == status.HTTP_200_OK

    def test_a_different_authenticated_user_can_also_read_a_public_dataset(
        self, public_dataset, other_user
    ):
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.get(reverse("dataset-rows", args=[public_dataset.id]))
        assert response.status_code == status.HTTP_200_OK

    def test_anonymous_cannot_read_a_private_dataset(self, private_dataset):
        client = APIClient()
        response = client.get(reverse("dataset-schema", args=[private_dataset.id]))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_making_a_dataset_private_again_revokes_anonymous_access(
        self, client_as, public_dataset
    ):
        client_as.patch(
            reverse("dataset-visibility", args=[public_dataset.id]), {"is_public": False}
        )

        client = APIClient()
        response = client.get(reverse("dataset-schema", args=[public_dataset.id]))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_public_dataset_still_hidden_from_list_and_retrieve_for_others(
        self, public_dataset, other_user
    ):
        client = APIClient()
        client.force_authenticate(user=other_user)

        list_response = client.get(reverse("dataset-list"))
        assert all(d["id"] != public_dataset.id for d in list_response.data["results"])

        retrieve_response = client.get(reverse("dataset-detail", args=[public_dataset.id]))
        assert retrieve_response.status_code == status.HTTP_404_NOT_FOUND

    def test_anonymous_cannot_upload_delete_or_manage_keys_even_for_a_public_dataset(
        self, public_dataset
    ):
        client = APIClient()

        assert client.delete(reverse("dataset-detail", args=[public_dataset.id])).status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert client.get(reverse("dataset-api-keys", args=[public_dataset.id])).status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_search_and_column_filters_still_work_on_public_dataset(self, public_dataset):
        client = APIClient()
        response = client.get(reverse("dataset-rows", args=[public_dataset.id]), {"search": "marc"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_authenticated_non_owner_cannot_delete_or_manage_a_public_dataset(
        self, public_dataset, other_user
    ):
        client = APIClient()
        client.force_authenticate(user=other_user)

        assert client.delete(reverse("dataset-detail", args=[public_dataset.id])).status_code == (
            status.HTTP_404_NOT_FOUND
        )
        assert client.get(reverse("dataset-api-keys", args=[public_dataset.id])).status_code == (
            status.HTTP_404_NOT_FOUND
        )
        assert (
            client.patch(
                reverse("dataset-visibility", args=[public_dataset.id]), {"is_public": False}
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )
        public_dataset.refresh_from_db()
        assert public_dataset.is_public is True

    def test_row_detail_cannot_be_used_to_reach_a_row_from_a_different_dataset(
        self, public_dataset, private_dataset
    ):
        client = APIClient()
        private_row = private_dataset.rows.first()

        response = client.get(
            reverse("dataset-row-detail", args=[public_dataset.id, private_row.id])
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
