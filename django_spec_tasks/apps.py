from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec

from django.apps import AppConfig
from django.core.checks import register
from django.utils.module_loading import autodiscover_modules

from django_spec_tasks.checks import check_spec_run_on_default_alias


class DjangoSpecTasksConfig(AppConfig):
    """Registers the check, the Celery task and every project's bindings."""

    name = "django_spec_tasks"
    verbose_name = "Spec tasks"

    def ready(self) -> None:
        register(check_spec_run_on_default_alias)
        _register_celery_task()
        autodiscover_modules("spec_tasks")


def _register_celery_task() -> None:
    """Import the Celery backend whenever ``celery`` is installed."""
    if find_spec("celery") is not None:
        import_module("django_spec_tasks.backends.celery_backend")
