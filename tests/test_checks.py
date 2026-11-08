from __future__ import annotations

import pytest
from django.core.checks import Error
from django.core.checks.registry import registry
from django.test import override_settings

from django_spec_tasks.checks import check_spec_run_on_default_alias


class _SendsWritesAway:
    def db_for_write(self, model: type, **hints: object) -> str | None:
        return "progress" if model._meta.app_label == "django_spec_tasks" else None


class _SendsReadsAway:
    def db_for_read(self, model: type, **hints: object) -> str | None:
        return "progress" if model._meta.app_label == "django_spec_tasks" else None


class _SendsOtherAppsAway:
    def db_for_write(self, model: type, **hints: object) -> str | None:
        return "progress" if model._meta.app_label != "django_spec_tasks" else None


class _NamesTheDefault:
    def db_for_write(self, model: type, **hints: object) -> str | None:
        return "default"

    def db_for_read(self, model: type, **hints: object) -> str | None:
        return "default"


def _here(name: str) -> str:
    return f"{__name__}.{name}"


@pytest.mark.parametrize(
    "routers",
    [
        pytest.param([], id="no_router"),
        pytest.param([_here("_NamesTheDefault")], id="a_router_naming_the_default_alias"),
        pytest.param([_here("_SendsOtherAppsAway")], id="a_router_moving_only_other_apps"),
    ],
)
def test_a_table_left_on_the_default_alias_passes(routers: list[str]) -> None:
    with override_settings(DATABASE_ROUTERS=routers):
        assert check_spec_run_on_default_alias(None) == []


@pytest.mark.parametrize(
    ("router", "direction"), [("_SendsWritesAway", "writes"), ("_SendsReadsAway", "reads")]
)
def test_a_router_sending_the_table_away_is_refused_naming_the_alias(
    router: str, direction: str
) -> None:
    with override_settings(DATABASE_ROUTERS=[_here(router)]):
        errors = check_spec_run_on_default_alias(None)

    assert [(e.id, e.msg) for e in errors] == [
        ("django_spec_tasks.E001", f"A database router sends SpecRun {direction} to 'progress'.")
    ]
    assert isinstance(errors[0], Error)


def test_it_is_registered_with_no_tag() -> None:
    # Tagged ``database``, it would stay silent under ``manage.py check``.
    assert check_spec_run_on_default_alias in registry.get_checks()
    assert list(check_spec_run_on_default_alias.tags) == []
