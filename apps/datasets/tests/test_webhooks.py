import socket

import pytest
import requests
from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.datasets.models import Dataset
from apps.datasets.services.webhooks import send_webhook, validate_webhook_url

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def owner():
    return User.objects.create_user(username="owner", password="pass1234")


class TestValidateWebhookUrl:
    def test_accepts_a_url_resolving_to_a_public_ip(self):
        # 8.8.8.8 is an IP literal, so this doesn't perform a real DNS query.
        assert validate_webhook_url("https://8.8.8.8/hook") == "https://8.8.8.8/hook"

    def test_rejects_non_http_schemes(self):
        with pytest.raises(serializers.ValidationError):
            validate_webhook_url("ftp://8.8.8.8/hook")

    def test_rejects_loopback_address(self):
        with pytest.raises(serializers.ValidationError):
            validate_webhook_url("http://127.0.0.1/hook")

    def test_rejects_private_rfc1918_address(self):
        with pytest.raises(serializers.ValidationError):
            validate_webhook_url("http://10.0.0.5/hook")

    def test_rejects_link_local_including_cloud_metadata(self):
        with pytest.raises(serializers.ValidationError):
            validate_webhook_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_ipv6_loopback(self):
        with pytest.raises(serializers.ValidationError):
            validate_webhook_url("http://[::1]/hook")

    def test_rejects_unresolvable_host(self, mocker):
        mocker.patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host"))
        with pytest.raises(serializers.ValidationError):
            validate_webhook_url("https://this-should-not-resolve.example/hook")

    def test_rejects_url_with_no_host(self):
        with pytest.raises(serializers.ValidationError):
            validate_webhook_url("http:///hook")


class TestSendWebhook:
    def test_posts_the_expected_payload(self, owner, mocker):
        dataset = Dataset.objects.create(
            owner=owner,
            name="leads",
            original_filename="leads.csv",
            status=Dataset.Status.READY,
            row_count=3,
            column_count=2,
            webhook_url="https://8.8.8.8/hook",
        )
        mock_post = mocker.patch("apps.datasets.services.webhooks.requests.post")

        send_webhook(dataset)

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["event"] == "dataset.ready"
        assert kwargs["json"]["dataset"]["id"] == dataset.id
        assert kwargs["json"]["dataset"]["row_count"] == 3
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] > 0

    def test_sends_dataset_failed_event_for_a_failed_dataset(self, owner, mocker):
        dataset = Dataset.objects.create(
            owner=owner,
            name="bad",
            original_filename="bad.csv",
            status=Dataset.Status.FAILED,
            failure_reason="no header",
            webhook_url="https://8.8.8.8/hook",
        )
        mock_post = mocker.patch("apps.datasets.services.webhooks.requests.post")

        send_webhook(dataset)

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["event"] == "dataset.failed"
        assert kwargs["json"]["dataset"]["failure_reason"] == "no header"

    def test_does_not_raise_if_delivery_fails(self, owner, mocker):
        dataset = Dataset.objects.create(
            owner=owner,
            name="leads",
            original_filename="leads.csv",
            status=Dataset.Status.READY,
            webhook_url="https://8.8.8.8/hook",
        )
        mocker.patch(
            "apps.datasets.services.webhooks.requests.post",
            side_effect=requests.ConnectionError("boom"),
        )

        send_webhook(dataset)  # must not raise

    def test_skips_delivery_if_the_url_no_longer_passes_validation(self, owner, mocker):
        dataset = Dataset.objects.create(
            owner=owner,
            name="leads",
            original_filename="leads.csv",
            status=Dataset.Status.READY,
            webhook_url="http://127.0.0.1/hook",
        )
        mock_post = mocker.patch("apps.datasets.services.webhooks.requests.post")

        send_webhook(dataset)

        mock_post.assert_not_called()
