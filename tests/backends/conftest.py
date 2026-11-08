"""Fixtures for the two backends: a stand-in for the worker's entry point, and a task framework."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from types import ModuleType
from typing import Any
from unittest import mock

import pytest
from asgiref.local import Local
from django.test.utils import override_settings

_WORKER = "django_spec_tasks.worker"
_ENTRY = "django_spec_tasks.worker.run_spec"


@pytest.fixture
def run_spec(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """The ``run_spec`` a task body calls, replaced where the body looks it up.

    Installed as modules rather than patched by dotted path, so these tests do not need the
    worker's package to exist to name it, and read the same once it does.
    """
    entry = mock.Mock(name="run_spec")
    for name, attributes in ((_WORKER, {}), (_ENTRY, {"run_spec": entry})):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
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
