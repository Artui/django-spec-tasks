from __future__ import annotations

from django_service_specs import (
    AdditionalInputRequired,
    DispatchError,
    NotPermitted,
    PrincipalUnavailable,
    ServiceConflict,
    ServiceError,
    ServiceValidationError,
)

from django_spec_tasks.types.run_state import RunState

_STATES: tuple[tuple[type[Exception], RunState], ...] = (
    (NotPermitted, RunState.AUTHORIZATION_LAPSED),
    (PrincipalUnavailable, RunState.AUTHORIZATION_LAPSED),
    (DispatchError, RunState.INVALID),
    (ServiceValidationError, RunState.INVALID),
    (ServiceConflict, RunState.CONFLICT),
    (AdditionalInputRequired, RunState.INPUT_REQUIRED),
    (ServiceError, RunState.REFUSED),
)
"""Each refusal's state, read top to bottom, first match wins.

A subclass sits above its base, or the base's row would answer for it. Only rows that change
the answer are listed: ``PermissionCheckDenied``, ``InvalidArguments``, ``ServiceNotFound`` and
``ActionUnavailable`` take their base's state, so the base's row is theirs. Coverage sees one
expression, not seven rows, and a deleted row whose base answers still passes
``unanswered_refusals``, so every refusal is held by its own case of
``test_each_refusal_has_its_state``."""


def run_state_for(error: BaseException) -> RunState:
    """The [`RunState`][django_spec_tasks.types.run_state.RunState] ``error`` ends a run in."""
    return next((state for kind, state in _STATES if isinstance(error, kind)), RunState.FAILED)
