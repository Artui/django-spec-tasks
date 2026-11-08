from __future__ import annotations

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


def test_no_router_passes() -> None:
    assert check_spec_run_on_default_alias(None) == []


@override_settings(DATABASE_ROUTERS=[_here("_NamesTheDefault")])
def test_a_router_naming_the_default_alias_passes() -> None:
    assert check_spec_run_on_default_alias(None) == []


@override_settings(DATABASE_ROUTERS=[_here("_SendsOtherAppsAway")])
def test_a_router_moving_only_other_apps_passes() -> None:
    assert check_spec_run_on_default_alias(None) == []


@override_settings(DATABASE_ROUTERS=[_here("_SendsWritesAway")])
def test_a_router_sending_writes_away_is_refused_naming_the_alias() -> None:
    errors = check_spec_run_on_default_alias(None)
    assert [(e.id, e.msg) for e in errors] == [
        ("django_spec_tasks.E001", "A database router sends SpecRun writes to 'progress'.")
    ]
    assert isinstance(errors[0], Error)


@override_settings(DATABASE_ROUTERS=[_here("_SendsReadsAway")])
def test_a_router_sending_reads_away_is_refused_naming_the_alias() -> None:
    assert [(e.id, e.msg) for e in check_spec_run_on_default_alias(None)] == [
        ("django_spec_tasks.E001", "A database router sends SpecRun reads to 'progress'.")
    ]


def test_it_is_registered_with_no_tag() -> None:
    # Tagged ``database``, it would stay silent under ``manage.py check``.
    assert check_spec_run_on_default_alias in registry.get_checks()
    assert list(check_spec_run_on_default_alias.tags) == []
