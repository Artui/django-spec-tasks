"""Builders the sweep tests share: a binding over a one-spec registry, a recording backend."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone
from django_service_specs import ServiceSpec, SpecRegistry, Unrestricted

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun

SPEC = "do_it"


class RecordingBackend:
    """Remembers every run id it was asked for, and refuses the ones it is told to."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.refuse: set[str] = set()

    def enqueue(self, run_id: str) -> None:
        if run_id in self.refuse:
            raise RuntimeError("the broker is down")
        self.asked.append(run_id)


def ago(**delta: float) -> datetime:
    return timezone.now() - timedelta(**delta)


def ahead(**delta: float) -> datetime:
    return timezone.now() + timedelta(**delta)


def spec_declaring(idempotent: bool | None) -> ServiceSpec:
    return ServiceSpec(service=lambda: None, permissions=[Unrestricted()], idempotent=idempotent)


_names = itertools.count()


@pytest.fixture
def backend() -> RecordingBackend:
    return RecordingBackend()


@pytest.fixture
def make_binding(backend: RecordingBackend) -> Callable[..., TaskBinding]:
    def make(*, idempotent: bool | None = None, **options: Any) -> TaskBinding:
        registry = SpecRegistry()
        registry.register(SPEC, spec_declaring(idempotent))
        options.setdefault("backend", backend)
        return TaskBinding(name=f"sweep-{next(_names)}", registry=registry, **options)

    return make


@pytest.fixture
def make_run() -> Callable[..., SpecRun]:
    def make(binding: TaskBinding | str, **fields: Any) -> SpecRun:
        name = binding if isinstance(binding, str) else binding.name
        fields.setdefault("spec_name", SPEC)
        return SpecRun.objects.create(binding=name, params={}, **fields)

    return make


@pytest.fixture
def lapsed(make_run: Callable[..., SpecRun]) -> Callable[..., SpecRun]:
    """A ``RUNNING`` run whose lease ran out a minute ago."""

    def make(binding: TaskBinding | str, **fields: Any) -> SpecRun:
        fields.setdefault("state", "RUNNING")
        fields.setdefault("attempts", 1)
        fields.setdefault("claimed_at", ago(hours=2))
        fields.setdefault("lease_expires_at", ago(minutes=1))
        return make_run(binding, **fields)

    return make


def reload(run: SpecRun) -> SpecRun:
    return SpecRun.objects.get(pk=run.pk)
