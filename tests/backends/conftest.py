"""Fixtures for the two backends: a stand-in for the worker's entry point, and a task framework."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any
from unittest import mock

import pytest
from asgiref.local import Local
from django.test.utils import override_settings

from django_spec_tasks.backends import celery_backend, django_tasks_backend


@pytest.fixture
def run_spec(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """The ``run_spec`` a task body calls, replaced where each body looks it up."""
    entry = mock.Mock(name="run_spec")
    monkeypatch.setattr(celery_backend, "run_spec", entry)
    monkeypatch.setattr(django_tasks_backend, "run_spec", entry)
    return entry


def reset_task_backends(handler: Any) -> None:
    """Make a handler re-read ``TASKS``: Django wires no ``setting_changed`` receiver for it."""
    handler.__dict__.pop("settings", None)
    handler._settings = handler.settings = handler.configure_settings(None)
    handler._connections = Local(handler.thread_critical)


@pytest.fixture
def tasks() -> Iterator[Callable[..., Any]]:
    """Point ``TASKS`` at backends of the test's choosing, on whichever framework imported."""
    try:
        from django.tasks import task_backends
    except ImportError:
        from django_tasks import task_backends

    with ExitStack() as stack:

        def configure(**aliases: str | dict[str, Any]) -> Any:
            override = override_settings(
                TASKS={
                    alias: {"BACKEND": entry} if isinstance(entry, str) else entry
                    for alias, entry in aliases.items()
                }
            )
            override.enable()
            stack.callback(reset_task_backends, task_backends)
            stack.callback(override.disable)
            reset_task_backends(task_backends)
            return task_backends

        yield configure
