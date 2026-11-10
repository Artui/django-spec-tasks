from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import User
from django_service_specs import ServiceSpec, SpecRegistry, Unrestricted

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun

MARKER = "marker-that-must-not-leave-the-row"


class _NoQueue:
    def enqueue(self, run_id: str) -> None: ...


@pytest.fixture
def make_binding() -> Callable[..., TaskBinding]:
    counter = iter(range(1000))

    def make(**options: Any) -> TaskBinding:
        registry = SpecRegistry()
        registry.register("do_it", ServiceSpec(service=lambda: None, permissions=[Unrestricted()]))
        return TaskBinding(
            name=f"read-{next(counter)}", registry=registry, backend=_NoQueue(), **options
        )

    return make


@pytest.fixture
def owner(db: None) -> User:
    return User.objects.create_user("owner", password="x")


@pytest.fixture
def stranger(db: None) -> User:
    return User.objects.create_user("stranger", password="x")


@pytest.fixture
def make_run() -> Callable[..., SpecRun]:
    def make(binding: TaskBinding, actor: User | None, **fields: Any) -> SpecRun:
        fields.setdefault("params", {"secret": MARKER})
        fields.setdefault("carried", {"carried_secret": MARKER})
        fields.setdefault("traceback", f"Traceback ... {MARKER}")
        fields.setdefault("actor_pk", None if actor is None else str(actor.pk))
        return SpecRun.objects.create(
            binding=binding.name,
            spec_name="do_it",
            **fields,
        )

    return make
