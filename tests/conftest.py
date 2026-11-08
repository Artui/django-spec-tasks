"""Fixtures the whole suite shares. A package's own go in ``tests/<package>/conftest.py``."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from django_spec_tasks.binding.task_binding import TaskBinding


@pytest.fixture(autouse=True)
def restore_the_binding_registry() -> Iterator[None]:
    """Hand the registry back as it was, so a test may build a binding freely.

    A binding registers globally at construction and a duplicate name raises, so without
    this every test would need a name of its own. A snapshot rather than a clear, so the
    one autodiscovery registered and any at a test module's top level stay known.
    """
    registered = dict(TaskBinding._registered)
    yield
    TaskBinding._registered.clear()
    TaskBinding._registered.update(registered)
