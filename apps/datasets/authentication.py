from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from apps.datasets.models import DatasetApiKey
from apps.datasets.services.api_keys import hash_api_key

KEYWORD = "Api-Key"


class DatasetApiKeyAuthentication(BaseAuthentication):
    """Authenticate a single request scoped to one Dataset via `Authorization: Api-Key <key>`.

    On success, request.user is anonymous (a key isn't a user account) and request.auth is
    the DatasetApiKey instance — permission classes decide dataset access from that.
    """

    def authenticate(self, request):
        auth_header = get_authorization_header(request).decode("utf-8")
        if not auth_header or not auth_header.startswith(f"{KEYWORD} "):
            return None

        raw_key = auth_header[len(KEYWORD) + 1 :].strip()
        if not raw_key:
            raise AuthenticationFailed("No API key provided.")

        try:
            api_key = DatasetApiKey.objects.select_related("dataset").get(
                key_hash=hash_api_key(raw_key), is_active=True
            )
        except DatasetApiKey.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid or revoked API key.") from exc

        api_key.last_used_at = timezone.now()
        api_key.save(update_fields=["last_used_at"])
        return (AnonymousUser(), api_key)

    def authenticate_header(self, request):
        return KEYWORD


class DatasetApiKeyScheme(OpenApiAuthenticationExtension):
    """Lets drf-spectacular document the Api-Key header instead of ignoring it."""

    target_class = DatasetApiKeyAuthentication
    name = "DatasetApiKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": f"Format: `{KEYWORD} <key>`. Grants read-only access to the one "
            "dataset the key was issued for.",
        }
