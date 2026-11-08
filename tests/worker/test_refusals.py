"""What each refusal leaves on the row, from a real dispatch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django_service_specs import (
    AdditionalInputRequired,
    Affordance,
    DataclassValidator,
    PermissionCheck,
    ServiceConflict,
    ServiceSpec,
    ServiceValidationError,
    Unrestricted,
    unanswered_refusals,
)

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.worker.run_spec import run_spec
from django_spec_tasks.worker.run_state_for import run_state_for

pytestmark = pytest.mark.django_db

User = get_user_model()


class _IsStaff(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return getattr(principal, "is_staff", False)


def _built_refusals() -> list[Exception]:
    built: list[Exception] = []

    def collect(error: Exception) -> RunState:
        built.append(error)
        return RunState.REFUSED

    unanswered_refusals(collect)
    return built


@pytest.mark.parametrize("error", _built_refusals(), ids=lambda error: type(error).__name__)
def test_a_service_raising_each_refusal_ends_named_and_stated(
    make_run: Callable[..., SpecRun], error: Exception
) -> None:
    def service() -> None:
        raise error

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    # Which state is the table's own test's to say; this holds that the row gets it.
    assert run.state == run_state_for(error) != RunState.FAILED
    assert run.refusal_class == type(error).__name__
    assert run.refusal_detail["message"] == str(error)
    assert run.traceback is None
    assert run.finished_at is not None


def test_a_validator_refusal_keeps_the_field_errors(make_run: Callable[..., SpecRun]) -> None:
    @dataclass
    class Input:
        title: str

    spec = ServiceSpec(
        service=lambda: None,
        validator=DataclassValidator(Input),
        permissions=[Unrestricted()],
    )
    run = make_run(spec, params={"title": 7})

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.INVALID
    assert run.refusal_class == "InvalidArguments"
    assert list(run.refusal_detail["errors"]) == ["title"]
    assert run.refusal_detail["errors"]["title"]


def test_an_argument_outside_the_declared_set_is_invalid(make_run: Callable[..., SpecRun]) -> None:
    run = make_run(
        ServiceSpec(service=lambda: None, permissions=[Unrestricted()]), params={"nope": 1}
    )

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.INVALID
    assert "nope" in run.refusal_detail["errors"]


def test_a_service_validation_error_keeps_its_detail(make_run: Callable[..., SpecRun]) -> None:
    def service() -> None:
        raise ServiceValidationError({"total": ["Too much."]})

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.INVALID
    assert run.refusal_detail["errors"] == {"total": ["Too much."]}


def test_an_unmet_affordance_is_a_conflict_with_its_code(make_run: Callable[..., SpecRun]) -> None:
    spec = ServiceSpec(
        service=lambda: None,
        affordances=[Affordance(code="closed", reason="It is closed.", when=lambda: False)],
        permissions=[Unrestricted()],
    )
    run = make_run(spec)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.CONFLICT
    assert run.refusal_class == "ActionUnavailable"
    assert run.refusal_code == "closed"
    assert run.refusal_detail["message"] == "It is closed."


def test_a_conflict_with_no_code_has_none(make_run: Callable[..., SpecRun]) -> None:
    def service() -> None:
        raise ServiceConflict("Taken.")

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.CONFLICT
    assert run.refusal_code is None


def test_input_required_records_what_is_missing(make_run: Callable[..., SpecRun]) -> None:
    schema = {"confirmed": {"type": "boolean"}}

    def service() -> None:
        raise AdditionalInputRequired("Confirm to proceed.", schema=schema)

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.INPUT_REQUIRED
    assert run.refusal_detail["schema"] == schema
    assert run.refusal_detail["message"] == "Confirm to proceed."


def test_a_refusal_whose_detail_cannot_be_stored_keeps_its_state_and_message(
    make_run: Callable[..., SpecRun],
) -> None:
    def service() -> None:
        raise ServiceValidationError({"total": object()})

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.INVALID
    assert run.refusal_detail["errors"] is None
    assert run.refusal_detail["message"] == "Service validation error."


def test_an_exception_nobody_declared_is_a_failure_with_its_traceback(
    make_run: Callable[..., SpecRun],
) -> None:
    def service() -> None:
        raise ValueError("a bug, somewhere")

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert "ValueError: a bug, somewhere" in run.traceback
    # A reader is shown the detail and never the traceback, so a fault's words stay out.
    assert run.refusal_detail is None
    assert run.refusal_class is None


def test_a_deactivated_principal_lapses_at_the_worker_s_own_resolver(
    make_run: Callable[..., SpecRun],
) -> None:
    # An open spec, so no permission check can be what answers: the words name the resolver.
    user = User.objects.create(username="left")
    run = make_run(
        ServiceSpec(service=lambda: None, permissions=[Unrestricted()]), actor_pk=str(user.pk)
    )
    User.objects.filter(pk=user.pk).update(is_active=False)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.AUTHORIZATION_LAPSED
    assert run.refusal_class == "PrincipalUnavailable"
    assert "deactivated" in run.refusal_detail["message"]


def test_a_resolver_handing_back_a_deactivated_user_is_stopped_by_dispatch(
    make_run: Callable[..., SpecRun],
) -> None:
    called: list[bool] = []
    user = User.objects.create(username="left")
    run = make_run(
        ServiceSpec(service=lambda: called.append(True), permissions=[Unrestricted()]),
        actor_pk=str(user.pk),
        resolve_actor=lambda pk: User.objects.get(pk=pk),
    )
    User.objects.filter(pk=user.pk).update(is_active=False)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert called == []
    assert run.state == RunState.AUTHORIZATION_LAPSED
    assert run.refusal_class == "PrincipalUnavailable"
    # Not the resolver's words: dispatch is what answered this one.
    assert run.refusal_detail["message"] != f"The principal {str(user.pk)!r} is deactivated."


def test_a_deleted_principal_lapses(make_run: Callable[..., SpecRun]) -> None:
    run = make_run(ServiceSpec(service=lambda: None, permissions=[Unrestricted()]), actor_pk="999")

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.AUTHORIZATION_LAPSED
    assert run.refusal_class == "PrincipalUnavailable"


def test_a_resolver_with_a_bug_is_a_failure(make_run: Callable[..., SpecRun]) -> None:
    def resolve_actor(pk: str) -> Any:
        raise RuntimeError("the directory is down")

    run = make_run(
        ServiceSpec(service=lambda: None, permissions=[Unrestricted()]),
        actor_pk="1",
        resolve_actor=resolve_actor,
    )

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert "the directory is down" in run.traceback


def test_a_demoted_principal_lapses_through_its_permission_check(
    make_run: Callable[..., SpecRun],
) -> None:
    # Active, so the principal rule cannot answer: only the re-check can.
    user = User.objects.create(username="was-staff", is_staff=True)
    run = make_run(
        ServiceSpec(service=lambda: None, permissions=[_IsStaff()]), actor_pk=str(user.pk)
    )
    User.objects.filter(pk=user.pk).update(is_staff=False)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.AUTHORIZATION_LAPSED
    assert run.refusal_class == "PermissionCheckDenied"


def test_a_principal_still_allowed_runs_as_themselves(make_run: Callable[..., SpecRun]) -> None:
    seen: list[Any] = []
    user = User.objects.create(username="staff", is_staff=True)

    def service(user: Any) -> None:
        seen.append(user)

    run = make_run(ServiceSpec(service=service, permissions=[_IsStaff()]), actor_pk=str(user.pk))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert seen == [user]


def test_no_actor_runs_on_an_open_spec_with_no_principal_at_all(
    make_run: Callable[..., SpecRun],
) -> None:
    seen: list[Any] = []

    def service(user: Any) -> None:
        seen.append(user)

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert seen == [None]
    assert not isinstance(seen[0], AnonymousUser)


def test_no_actor_on_a_gated_spec_lapses(make_run: Callable[..., SpecRun]) -> None:
    run = make_run(ServiceSpec(service=lambda: None, permissions=[_IsStaff()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.AUTHORIZATION_LAPSED
    assert run.refusal_class == "PermissionCheckDenied"
