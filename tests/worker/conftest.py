from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

import pytest
from django_service_specs import DispatchError, ServiceError, SpecRegistry, unanswered_refusals

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState

SPEC_NAME = "operation"
_names = itertools.count()


def built_refusals() -> list[DispatchError | ServiceError]:
    """One of each refusal, built as the kernel raises it, handed over by its own check."""
    built: list[DispatchError | ServiceError] = []

    def collect(error: DispatchError | ServiceError) -> RunState:
        built.append(error)
        return RunState.REFUSED

    unanswered_refusals(collect)
    return built


class NoQueue:
    """The worker never asks a backend for anything, so nothing here can answer."""

    def enqueue(self, run_id: str) -> None:
        raise AssertionError("the worker asked a backend to run something")


@pytest.fixture
def make_run() -> Callable[..., SpecRun]:
    """A pending row for ``spec`` on a binding of its own, as enqueue would leave one."""

    def build(
        spec: Any,
        *,
        params: dict[str, Any] | None = None,
        actor_pk: str | None = None,
        carried: dict[str, Any] | None = None,
        **binding_fields: Any,
    ) -> SpecRun:
        registry = SpecRegistry()
        registry.register(SPEC_NAME, spec)
        binding = TaskBinding(
            name=f"worker-{next(_names)}",
            registry=registry,
            backend=NoQueue(),
            **binding_fields,
        )
        return SpecRun.objects.create(
            binding=binding.name,
            spec_name=SPEC_NAME,
            params=params or {},
            actor_pk=actor_pk,
            carried=carried or {},
        )

    return build
