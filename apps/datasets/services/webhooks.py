"""Post-import webhook delivery: notify an owner-supplied URL once ingestion finishes.

The URL is owner-supplied, so this isn't the classic "attacker tricks a victim's server
into fetching an internal URL on the attacker's behalf" SSRF — but it's still a URL the
*server itself* will connect to, which could be used to probe or reach internal/cloud-
metadata endpoints the server has network access to but the public internet doesn't.
validate_webhook_url blocks private/loopback/link-local/reserved destinations for
exactly that reason.

That validation is DNS-rebinding-vulnerable in principle (resolve-then-connect is a
classic TOCTOU): a hostname could resolve to a public IP at validation time and a
private one at request time. Re-validating immediately before sending (below) shrinks
that window but doesn't close it — a production system would resolve once and pin that
IP for the actual connection instead of letting the HTTP client re-resolve. Out of scope
for this project's stated scale.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import requests
from rest_framework import serializers

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_WEBHOOK_TIMEOUT_SECONDS = 5


def validate_webhook_url(url: str) -> str:
    """Used both by the upload serializer (set-time) and send_webhook (send-time)."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise serializers.ValidationError("Webhook URL must use http or https.")
    if not parsed.hostname:
        raise serializers.ValidationError("Webhook URL must include a host.")

    try:
        addresses = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise serializers.ValidationError("Webhook URL host could not be resolved.") from exc

    for _family, _type, _proto, _canonname, sockaddr in addresses:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise serializers.ValidationError(
                "Webhook URL resolves to a private or internal address, which isn't allowed."
            )

    return url


def send_webhook(dataset) -> None:
    """Best-effort delivery: failures are logged, never raised — a broken webhook
    endpoint must not affect the dataset's own ingestion status.
    """
    try:
        validate_webhook_url(dataset.webhook_url)
    except serializers.ValidationError:
        logger.warning(
            "Skipping webhook for dataset %s: URL failed re-validation at send time",
            dataset.id,
        )
        return

    event = "dataset.ready" if dataset.status == dataset.Status.READY else "dataset.failed"
    payload = {
        "event": event,
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "status": dataset.status,
            "row_count": dataset.row_count,
            "column_count": dataset.column_count,
            "failure_reason": dataset.failure_reason,
        },
    }

    try:
        # stream=True + an immediate close, without ever touching .content/.text:
        # we don't care about the response, and a malicious/compromised endpoint
        # shouldn't be able to make this worker buffer an arbitrarily large body
        # into memory just because it returned one.
        response = requests.post(
            dataset.webhook_url,
            json=payload,
            timeout=_WEBHOOK_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
        response.close()
    except requests.RequestException as exc:
        # Not exc_info=True: requests' own exceptions embed the full request URL
        # (query string included) in their message, and a webhook_url could
        # reasonably carry an auth token as a query param — logging the exception
        # type only avoids leaking that into logs, consistent with how API keys are
        # only ever logged/stored as a hash, never in raw form (see services/api_keys.py).
        logger.warning("Webhook delivery failed for dataset %s: %s", dataset.id, type(exc).__name__)
