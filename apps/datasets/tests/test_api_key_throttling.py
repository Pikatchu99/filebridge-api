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
def client_as(owner):
    client = APIClient()
    client.force_authenticate(user=owner)
    return client


@pytest.fixture
def public_dataset(owner):
    dataset = Dataset.objects.create(
        owner=owner, name="public-leads", original_filename="leads.csv", is_public=True
    )
    return _ingest(dataset, "name,email\nMarc,marc@example.com\n")


def _create_key(client_as, dataset, name="integration"):
    response = client_as.post(reverse("dataset-api-keys", args=[dataset.id]), {"name": name})
    return response.data["key"]


def api_key_client(raw_key):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw_key}")
    return client


class TestApiKeyRateThrottle:
    def test_a_key_is_throttled_once_it_exceeds_its_own_rate(
        self, client_as, public_dataset, settings
    ):
        settings.REST_FRAMEWORK = {
            **settings.REST_FRAMEWORK,
            "DEFAULT_THROTTLE_RATES": {
                **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
                "api_key": "2/min",
            },
        }
        raw_key = _create_key(client_as, public_dataset)
        client = api_key_client(raw_key)
        url = reverse("dataset-schema", args=[public_dataset.id])

        first = client.get(url)
        second = client.get(url)
        third = client.get(url)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert third.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_two_different_keys_have_independent_buckets(self, client_as, public_dataset, settings):
        settings.REST_FRAMEWORK = {
            **settings.REST_FRAMEWORK,
            "DEFAULT_THROTTLE_RATES": {
                **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
                "api_key": "1/min",
            },
        }
        url = reverse("dataset-schema", args=[public_dataset.id])
        client_a = api_key_client(_create_key(client_as, public_dataset, "key-a"))
        client_b = api_key_client(_create_key(client_as, public_dataset, "key-b"))

        # Exhaust key A's one-request budget.
        assert client_a.get(url).status_code == status.HTTP_200_OK
        assert client_a.get(url).status_code == status.HTTP_429_TOO_MANY_REQUESTS

        # Key B is unaffected — it has its own bucket, not a shared IP-based one.
        assert client_b.get(url).status_code == status.HTTP_200_OK

    def test_api_key_throttling_does_not_consume_the_anonymous_bucket(
        self, client_as, public_dataset, settings
    ):
        settings.REST_FRAMEWORK = {
            **settings.REST_FRAMEWORK,
            "DEFAULT_THROTTLE_RATES": {
                **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
                "api_key": "1/min",
            },
        }
        url = reverse("dataset-schema", args=[public_dataset.id])
        keyed_client = api_key_client(_create_key(client_as, public_dataset))

        # Exhaust the API key's budget.
        assert keyed_client.get(url).status_code == status.HTTP_200_OK
        assert keyed_client.get(url).status_code == status.HTTP_429_TOO_MANY_REQUESTS

        # A plain anonymous reader of the same public dataset is unaffected.
        anonymous_client = APIClient()
        assert anonymous_client.get(url).status_code == status.HTTP_200_OK
