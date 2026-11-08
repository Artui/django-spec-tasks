from __future__ import annotations

import pytest
from django.core.management import call_command


@pytest.mark.django_db(databases=["default", "progress"])
def test_the_models_and_the_migrations_agree() -> None:
    # Scoped to this app: the test app's models have no migrations on purpose.
    call_command("makemigrations", "django_spec_tasks", check=True, dry_run=True, verbosity=0)
