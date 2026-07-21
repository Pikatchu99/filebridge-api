"""
Django settings for the FileBridge API project.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env.bool("DEBUG", default=False)

# Only fall back to an insecure default while DEBUG is on (local dev). In any other
# environment, a missing SECRET_KEY must fail loudly (django-environ raises
# ImproperlyConfigured) instead of silently reusing a value that's public in this
# repo's history.
SECRET_KEY = (
    env("SECRET_KEY", default="django-insecure-dev-only-change-me") if DEBUG else env("SECRET_KEY")
)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Security headers — irrelevant in local dev (plain HTTP), enforced once DEBUG is off.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # Set when running behind a TLS-terminating proxy (Render, Fly, Railway, etc.).
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "django_filters",
    "apps.datasets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "static/"

# Uploaded source files (see Dataset.source_file), so the async ingestion worker can
# read them independently of the request that created them.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Django REST Framework
# https://www.django-rest-framework.org/api-guide/settings/

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "apps.datasets.authentication.DatasetApiKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.datasets.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_RATE_ANON", default="20/min"),
        "user": env("THROTTLE_RATE_USER", default="100/min"),
        # Used by DatasetApiKeyRateThrottle — see DatasetViewSet.get_throttles().
        "api_key": env("THROTTLE_RATE_API_KEY", default="60/min"),
        # Used by RetryRateThrottle — much tighter than "user" since a retry can
        # re-fire a dataset's webhook (see services/webhooks.py).
        "retry": env("THROTTLE_RATE_RETRY", default="10/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "FileBridge API",
    "DESCRIPTION": "Turn CSV files into searchable, filterable REST datasets.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Upload limits (V1 scope: reject anything larger than this)
FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES = env.int(
    "FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES", default=10 * 1024 * 1024
)

# Reject the request body itself (before our own size check even runs) once it's
# meaningfully over the upload cap, so an oversized upload can't be buffered in full
# just to be rejected by the serializer afterwards. A production deployment should
# still enforce a hard body-size limit at the reverse proxy / load balancer too.
DATA_UPLOAD_MAX_MEMORY_SIZE = FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES + (1 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE

# A .xlsx is a zip archive: its uncompressed size (and therefore row count) isn't
# bounded by FILEBRIDGE_MAX_UPLOAD_SIZE_BYTES, which only measures the compressed
# upload. This caps rows read from the first worksheet regardless of compression ratio.
FILEBRIDGE_MAX_XLSX_ROWS = env.int("FILEBRIDGE_MAX_XLSX_ROWS", default=200_000)


# Celery — ingestion runs in a worker process, not the request/response cycle, so a
# large file doesn't tie up a web worker or hit a request timeout. See config/celery.py
# and apps/datasets/tasks.py. CELERY_TASK_ALWAYS_EAGER is forced True under pytest (see
# conftest.py) so the test suite doesn't need a running broker/worker.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"


# Logging — surface security-relevant events (failed auth, permission denials,
# unhandled exceptions) instead of leaving them silent outside DEBUG.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
