from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import timedelta

import pytest
from django_service_specs import DEFAULT_POOL_SEEDS, SpecRegistry, resolve_principal

from django_spec_tasks.binding.task_binding import TaskBinding


class _NoQueue:
    def enqueue(self, run_id: str) -> None: ...


def _binding(name: str = "orders") -> TaskBinding:
    return TaskBinding(name=name, registry=SpecRegistry(), backend=_NoQueue())


def test_a_name_a_registry_and_a_backend_are_enough() -> None:
    binding = _binding()
    assert {field.name: getattr(binding, field.name) for field in fields(binding)} == {
        "name": "orders",
        "registry": binding.registry,
        "backend": binding.backend,
        "resolve_actor": resolve_principal,
        "pool_seeds": DEFAULT_POOL_SEEDS,
        "lease": timedelta(hours=1),
        "grace": timedelta(minutes=10),
        "max_attempts": 3,
        "max_output_bytes": 1_048_576,
        "can_read": None,
        "progress_using": None,
        "carry": None,
        "around_run": None,
        "on_finish": None,
    }


def test_resolve_actor_defaults_to_the_kernel_s_function_unbound() -> None:
    # A plain function as a class-level default would bind as a method if the field were
    # ever read from the class's attribute rather than the instance's.
    assert _binding().resolve_actor is resolve_principal


def test_it_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        _binding().name = "other"


def test_every_field_after_the_three_required_is_keyword_only_and_optional() -> None:
    with pytest.raises(TypeError):
        TaskBinding("orders", SpecRegistry(), _NoQueue())


def test_construction_registers_it_under_its_name() -> None:
    binding = _binding()
    assert TaskBinding.named("orders") is binding


def test_a_duplicate_name_raises_naming_the_name() -> None:
    _binding("twice")
    with pytest.raises(ValueError, match="'twice'"):
        _binding("twice")


def test_a_refused_duplicate_leaves_the_first_registered() -> None:
    first = _binding("twice")
    with pytest.raises(ValueError):
        _binding("twice")
    assert TaskBinding.named("twice") is first


def test_an_unknown_name_answers_none() -> None:
    assert TaskBinding.named("no-such-binding") is None


def test_clear_registry_forgets_every_binding() -> None:
    _binding()
    TaskBinding.clear_registry()
    assert TaskBinding.named("orders") is None
    assert TaskBinding.named("discovered") is None


def test_the_registry_is_back_after_a_test_clears_it() -> None:
    # The fixture in conftest.py: the test above cleared it, and this one runs after.
    assert TaskBinding.named("discovered") is not None
