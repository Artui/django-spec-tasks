from __future__ import annotations

import os
import tempfile

SECRET_KEY = "not-a-secret-this-is-the-test-suite"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.admin",
    "django_spec_tasks",
    "tests.testapp",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

ROOT_URLCONF = "tests.urls"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

_TEST_DATABASE = os.path.join(tempfile.gettempdir(), f"django_spec_tasks_{os.getpid()}.sqlite3")

_SELECTED_DATABASE = os.environ.get("SPEC_TASKS_TEST_DATABASE", "sqlite")

if _SELECTED_DATABASE == "postgres":
    _POSTGRES = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PGDATABASE", "django_spec_tasks"),
        "USER": os.environ.get("PGUSER", "postgres"),
        "PASSWORD": os.environ.get("PGPASSWORD", "postgres"),
        "HOST": os.environ.get("PGHOST", "localhost"),
        "PORT": os.environ.get("PGPORT", "5432"),
    }
    DATABASES = {"default": dict(_POSTGRES), "progress": dict(_POSTGRES)}
elif _SELECTED_DATABASE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
            "TEST": {"NAME": _TEST_DATABASE},
        },
        "progress": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
            "OPTIONS": {"timeout": 1},
            "TEST": {"NAME": _TEST_DATABASE},
        },
    }
else:
    raise ValueError(f"SPEC_TASKS_TEST_DATABASE={_SELECTED_DATABASE!r}: sqlite or postgres.")

USE_TZ = True
