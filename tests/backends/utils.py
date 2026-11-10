from __future__ import annotations

import os


def process_env() -> dict[str, str]:
    """The environment for a child that configures Django, and is not measured."""
    kept = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("COV_CORE", "COVERAGE"))
    }
    return {**kept, "DJANGO_SETTINGS_MODULE": "tests.conftest_settings"}
