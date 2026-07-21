import io

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.datasets.models import Dataset, DatasetApiKey
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
        dataset, "name,email,campus\nSarah,sarah@example.com,Paris\nLea,lea@example.com,Lyon\n"
    )


@pytest.fixture
def other_dataset(other_user):
    dataset = Dataset.objects.create(owner=other_user, name="leads", original_filename="leads.csv")
    return _ingest(dataset, "name,email\nMarc,marc@example.com\n")


def api_key_client(raw_key):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw_key}")
    return client


class TestCreateApiKey:
    url_name = "dataset-api-keys"

    def test_owner_can_create_a_key(self, client_as, ready_dataset):
        response = client_as.post(
            reverse(self.url_name, args=[ready_dataset.id]), {"name": "n8n integration"}
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "n8n integration"
        assert "key" in response.data
        assert len(response.data["key"]) > 20

        stored = DatasetApiKey.objects.get(dataset=ready_dataset)
        assert stored.key_hash != response.data["key"]

    def test_non_owner_cannot_create_a_key(self, ready_dataset, other_user):
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.post(reverse(self.url_name, args=[ready_dataset.id]), {"name": "steal"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_requires_a_name(self, client_as, ready_dataset):
        response = client_as.post(reverse(self.url_name, args=[ready_dataset.id]), {})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestListApiKeys:
    url_name = "dataset-api-keys"

    def test_lists_keys_without_exposing_hash_or_raw_key(self, client_as, ready_dataset):
        client_as.post(reverse(self.url_name, args=[ready_dataset.id]), {"name": "zapier"})

        response = client_as.get(reverse(self.url_name, args=[ready_dataset.id]))

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["name"] == "zapier"
        assert "key" not in response.data[0]
        assert "key_hash" not in response.data[0]

    def test_key_list_is_scoped_per_dataset(self, client_as, ready_dataset, owner):
        second_dataset = _ingest(
            Dataset.objects.create(owner=owner, name="leads", original_filename="leads.csv"),
            "name,email\nMarc,marc@example.com\n",
        )
        client_as.post(reverse(self.url_name, args=[ready_dataset.id]), {"name": "for-first"})
        client_as.post(reverse(self.url_name, args=[second_dataset.id]), {"name": "for-second"})

        first_keys = client_as.get(reverse(self.url_name, args=[ready_dataset.id])).data
        second_keys = client_as.get(reverse(self.url_name, args=[second_dataset.id])).data

        assert [k["name"] for k in first_keys] == ["for-first"]
        assert [k["name"] for k in second_keys] == ["for-second"]


class TestRevokeApiKey:
    url_name = "dataset-api-key-detail"

    def test_owner_can_revoke_a_key(self, client_as, ready_dataset):
        create_response = client_as.post(
            reverse("dataset-api-keys", args=[ready_dataset.id]), {"name": "zapier"}
        )
        key_id = DatasetApiKey.objects.get(dataset=ready_dataset).id
        raw_key = create_response.data["key"]

        response = client_as.delete(reverse(self.url_name, args=[ready_dataset.id, key_id]))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not DatasetApiKey.objects.filter(id=key_id).exists()

        revoked_client = api_key_client(raw_key)
        schema_response = revoked_client.get(reverse("dataset-schema", args=[ready_dataset.id]))
        assert schema_response.status_code == status.HTTP_403_FORBIDDEN


class TestApiKeyAuthentication:
    def _create_key(self, client_as, dataset):
        response = client_as.post(
            reverse("dataset-api-keys", args=[dataset.id]), {"name": "integration"}
        )
        return response.data["key"]

    def test_valid_key_can_read_schema_rows_and_export(self, client_as, ready_dataset):
        raw_key = self._create_key(client_as, ready_dataset)
        client = api_key_client(raw_key)

        schema = client.get(reverse("dataset-schema", args=[ready_dataset.id]))
        rows = client.get(reverse("dataset-rows", args=[ready_dataset.id]))
        export = client.get(reverse("dataset-export", args=[ready_dataset.id]))

        assert schema.status_code == status.HTTP_200_OK
        assert rows.status_code == status.HTTP_200_OK
        assert export.status_code == status.HTTP_200_OK

    def test_valid_key_cannot_access_a_different_dataset(
        self, client_as, ready_dataset, other_dataset
    ):
        raw_key = self._create_key(client_as, ready_dataset)
        client = api_key_client(raw_key)

        assert (
            client.get(reverse("dataset-schema", args=[other_dataset.id])).status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert (
            client.get(reverse("dataset-rows", args=[other_dataset.id])).status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert (
            client.get(reverse("dataset-export", args=[other_dataset.id])).status_code
            == status.HTTP_404_NOT_FOUND
        )

        other_row = other_dataset.rows.first()
        response = client.get(reverse("dataset-row-detail", args=[other_dataset.id, other_row.id]))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_valid_key_cannot_list_datasets_upload_or_delete(self, client_as, ready_dataset):
        raw_key = self._create_key(client_as, ready_dataset)
        client = api_key_client(raw_key)

        assert client.get(reverse("dataset-list")).status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert client.post(reverse("dataset-upload")).status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert client.delete(reverse("dataset-detail", args=[ready_dataset.id])).status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_valid_key_cannot_manage_api_keys(self, client_as, ready_dataset):
        raw_key = self._create_key(client_as, ready_dataset)
        client = api_key_client(raw_key)
        key_id = DatasetApiKey.objects.get(dataset=ready_dataset).id

        assert client.get(reverse("dataset-api-keys", args=[ready_dataset.id])).status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert client.post(
            reverse("dataset-api-keys", args=[ready_dataset.id]), {"name": "escalate"}
        ).status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        assert client.delete(
            reverse("dataset-api-key-detail", args=[ready_dataset.id, key_id])
        ).status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_malformed_authorization_header_is_ignored_not_crashed(self, ready_dataset):
        for header in ["api-key sometoken", "Api-Keysometoken", "Bearer sometoken", "Api-Key "]:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=header)
            response = client.get(reverse("dataset-schema", args=[ready_dataset.id]))
            assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_garbage_key_is_rejected(self, ready_dataset):
        client = api_key_client("not-a-real-key")
        response = client.get(reverse("dataset-schema", args=[ready_dataset.id]))
        # SessionAuthentication is first in DEFAULT_AUTHENTICATION_CLASSES and doesn't
        # support a WWW-Authenticate challenge, so DRF forces every auth failure to 403
        # here (same reasoning as test_requires_authentication in test_views.py).
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_inactive_key_is_rejected(self, client_as, ready_dataset):
        raw_key = self._create_key(client_as, ready_dataset)
        DatasetApiKey.objects.filter(dataset=ready_dataset).update(is_active=False)

        client = api_key_client(raw_key)
        response = client.get(reverse("dataset-schema", args=[ready_dataset.id]))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_successful_use_updates_last_used_at(self, client_as, ready_dataset):
        raw_key = self._create_key(client_as, ready_dataset)
        client = api_key_client(raw_key)
        before = timezone.now()

        client.get(reverse("dataset-schema", args=[ready_dataset.id]))

        api_key = DatasetApiKey.objects.get(dataset=ready_dataset)
        assert api_key.last_used_at is not None
        assert api_key.last_used_at >= before
