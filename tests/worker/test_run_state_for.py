from __future__ import annotations

import pytest
from django_service_specs import (
    REFUSALS,
    ActionUnavailable,
    AdditionalInputRequired,
    DispatchError,
    InvalidArguments,
    NotPermitted,
    PermissionCheckDenied,
    PrincipalUnavailable,
    RefusalFinding,
    RefusalFindingKind,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceValidationError,
    unanswered_refusals,
)

from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.worker.run_state_for import _STATES, run_state_for
from tests.worker.conftest import built_refusals

_EXPECTED = {
    DispatchError: RunState.INVALID,
    InvalidArguments: RunState.INVALID,
    NotPermitted: RunState.AUTHORIZATION_LAPSED,
    PermissionCheckDenied: RunState.AUTHORIZATION_LAPSED,
    PrincipalUnavailable: RunState.AUTHORIZATION_LAPSED,
    ServiceError: RunState.REFUSED,
    ServiceNotFound: RunState.REFUSED,
    ServiceConflict: RunState.CONFLICT,
    ActionUnavailable: RunState.CONFLICT,
    AdditionalInputRequired: RunState.INPUT_REQUIRED,
    ServiceValidationError: RunState.INVALID,
}


def test_the_table_answers_every_refusal_and_shadows_no_row() -> None:
    assert unanswered_refusals(_STATES) == ()


def test_the_expectations_below_name_every_refusal_the_kernel_has() -> None:
    assert set(_EXPECTED) == set(REFUSALS)


@pytest.mark.parametrize("error", built_refusals(), ids=lambda error: type(error).__name__)
def test_each_refusal_has_its_state(error: DispatchError | ServiceError) -> None:
    # Coverage reads the table as one expression, so each row is held by its own case.
    assert run_state_for(error) == _EXPECTED[type(error)]


def test_the_check_reads_the_order_of_the_table() -> None:
    reordered = tuple(reversed(_STATES))
    assert RefusalFinding(ServiceConflict, RefusalFindingKind.SHADOWED_ROW, ServiceError) in (
        unanswered_refusals(reordered)
    )


def test_a_refusal_is_never_a_failure_or_a_success() -> None:
    assert {state for _, state in _STATES}.isdisjoint({RunState.FAILED, RunState.SUCCEEDED})


def test_a_refusal_of_the_call_added_later_is_invalid() -> None:
    class UnsupportedSomething(DispatchError):
        pass

    assert run_state_for(UnsupportedSomething()) == RunState.INVALID


def test_a_refusal_by_the_operation_added_later_is_refused() -> None:
    class OutOfStock(ServiceError):
        pass

    assert run_state_for(OutOfStock()) == RunState.REFUSED


def test_a_subclass_of_conflict_is_a_conflict() -> None:
    class SlotTaken(ServiceConflict):
        pass

    assert run_state_for(SlotTaken()) == RunState.CONFLICT


@pytest.mark.parametrize("error", [ValueError("boom"), KeyError("x"), RuntimeError()])
def test_anything_else_is_a_failure(error: Exception) -> None:
    assert run_state_for(error) == RunState.FAILED
