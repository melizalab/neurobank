"""Django settings for the registry server used by the integration tests.

The database is selected by environment: Postgres if NBANK_TEST_DB=postgres
(connection details from the POSTGRES_* variables), otherwise a sqlite file at
NBANK_TEST_SQLITE. The server runs in a separate process, so an in-memory
database won't work.

With Postgres the registry's migrations are run. With sqlite they are skipped
and the tables are created from the models, because the migrations import
django.contrib.postgres, which needs psycopg.
"""

import os

if os.environ.get("NBANK_TEST_DB") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "test_db"),
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ["NBANK_TEST_SQLITE"],
        }
    }
    MIGRATION_MODULES = {"nbank_registry": None}

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "django_filters",
    "nbank_registry",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

# a small page size so that tests can exercise pagination cheaply. The registry
# sets its pagination class on the views that need it.
REST_FRAMEWORK = {"PAGE_SIZE": 5}
SILENCED_SYSTEM_CHECKS = ["rest_framework.W001"]

SECRET_KEY = "not-a-secret-test-server-only"
ROOT_URLCONF = "test.integration.server.urls"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
USE_TZ = True
DEBUG = True
