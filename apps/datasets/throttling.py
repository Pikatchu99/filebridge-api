from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

from apps.datasets.models import DatasetApiKey


class RetryRateThrottle(UserRateThrottle):
    """Retrying re-fires ingestion — and, if the dataset has one configured, its
    webhook — so a much tighter cap than the general user rate keeps a retry loop
    from being used to flood an arbitrary webhook_url target via this server.
    """

    scope = "retry"

    def __init__(self):
        # See DatasetApiKeyRateThrottle below for why this refresh is needed.
        self.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        super().__init__()


class DatasetApiKeyRateThrottle(SimpleRateThrottle):
    """One independent bucket per DatasetApiKey, instead of every key sharing the
    global anon-by-IP bucket (which is also shared with unauthenticated browsers of
    public datasets, and doesn't distinguish one key from another at all).
    """

    scope = "api_key"

    def __init__(self):
        # SimpleRateThrottle.THROTTLE_RATES is a class attribute snapshotted from
        # api_settings.DEFAULT_THROTTLE_RATES once, at import time — overriding
        # settings.REST_FRAMEWORK later (e.g. via override_settings in tests, or any
        # runtime config reload) would silently have no effect without this refresh.
        self.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        super().__init__()

    def get_cache_key(self, request, view):
        api_key = getattr(request, "auth", None)
        if not isinstance(api_key, DatasetApiKey):
            return None
        return self.cache_format % {"scope": self.scope, "ident": api_key.id}
