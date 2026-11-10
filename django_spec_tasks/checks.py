from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig, apps
from django.core.checks import CheckMessage, Error
from django.db import DEFAULT_DB_ALIAS, router


def check_spec_run_on_default_alias(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    """Refuse a database router sending ``SpecRun`` away from the default alias."""
    spec_run = apps.get_model("django_spec_tasks", "SpecRun")
    routed = {"writes": router.db_for_write(spec_run), "reads": router.db_for_read(spec_run)}
    return [
        Error(
            f"A database router sends SpecRun {operation} to {alias!r}.",
            hint=(
                "A run's terminal state commits in the transaction the service ran in, "
                f"which the kernel opens on {DEFAULT_DB_ALIAS!r}. Route the "
                "django_spec_tasks app there."
            ),
            id="django_spec_tasks.E001",
        )
        for operation, alias in routed.items()
        if alias != DEFAULT_DB_ALIAS
    ]
