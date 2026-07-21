import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """DRF throttling stores request counts in Django's cache, which pytest-django
    doesn't reset between tests (unlike the database). Without this, throttle state
    leaks across tests and can make an unrelated test flakily 429 once enough requests
    have accumulated across the whole suite run.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _celery_eager_mode(settings):
    """Run Celery tasks inline, synchronously, with no broker — so the test suite
    doesn't need Redis or a running worker, and can assert on task results directly.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture(autouse=True)
def _media_root_tmp(settings, tmp_path):
    """Datasets now write an uploaded file to disk (Dataset.source_file); redirect
    MEDIA_ROOT to a per-test tmp dir so the suite doesn't accumulate files in the
    project's real media/ directory.
    """
    settings.MEDIA_ROOT = tmp_path
