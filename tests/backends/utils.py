"""Shared by the tests that start a process of their own."""

from __future__ import annotations

import os


def process_env() -> dict[str, str]:
    """The environment for a child that configures Django, and is not measured.

    pytest-cov at some versions measures every child through variables it sets here, and a
    child running from a copy of the package would put the copy's files in the report and
    the gate under 100%. What these children prove is behaviour, not coverage.
    """
    kept = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("COV_CORE", "COVERAGE"))
    }
    return {**kept, "DJANGO_SETTINGS_MODULE": "tests.conftest_settings"}
