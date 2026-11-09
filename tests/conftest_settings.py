"""Settings for the test suite.

SQLite unless Postgres is asked for, and no DRF anywhere: the kernel this package serves
exists for dispatch without it, and a suite that could import it could not notice either
package reaching for it.

On SQLite the test database is a **file**, not ``:memory:``. An in-memory SQLite database is per
connection, and Django's connections are per thread, so a second connection would land
on a database with no tables. The name is built at import so it holds no machine's path,
and carries the process id so two runs never share a file.

``progress`` is a second alias on that same file: another connection, which is what a
progress write through ``progress_using`` needs to be visible from outside a transaction.
A test using it says so with ``databases=``. SQLite serialises writers, so a write through
it while ``default`` holds the write lock fails, and the short timeout makes that fast.

``SPEC_TASKS_TEST_DATABASE=postgres`` selects Postgres, read from the ``PG*`` variables libpq
itself reads, which is the CI cell and how to run it by hand. SQLite stores a NUL and half a
surrogate pair in a JSON column and Postgres refuses them, so a value nothing guards fails its
write only there.
``progress`` is the same second connection, with the same settings as ``default``: Django gives
two aliases of one signature a single test database, as it does on the SQLite file. Any other
value of the variable is refused, because a misspelling must not read as a green Postgres run
that ran on SQLite.
"""

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
