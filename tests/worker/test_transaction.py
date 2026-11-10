from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from typing import Any

import pytest
from django.db import connection
from django_service_specs import (
    Presenter,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    Unrestricted,
)

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.worker.run_spec import run_spec
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db(transaction=True)


class _Killed(BaseException):
    """A worker dying outright: nothing above it may catch this."""


class _Dies(Presenter):
    """Kills the worker once the service has returned, so the kernel's own transaction has
    closed and only an outer one can still take the service's row back."""

    def output(self) -> Any:
        return None

    def present(self, value: Any) -> Any:
        raise _Killed


def _writes_a_note() -> str:
    Note.objects.create(text="the service's work")
    return "done"


def _spec(*, atomic: bool, presenter: Presenter | None = None) -> ServiceSpec:
    return ServiceSpec(
        service=_writes_a_note, atomic=atomic, presenter=presenter, permissions=[Unrestricted()]
    )


def _spec_losing_the_fence(*, atomic: bool, **columns: Any) -> tuple[ServiceSpec, list[Any]]:
    """A spec whose service moves its own run on, as a sweep or another worker would."""
    run_ids: list[Any] = []

    def service() -> None:
        Note.objects.create(text="the service's work")
        SpecRun.objects.filter(pk=run_ids[0]).update(**columns)

    return ServiceSpec(service=service, atomic=atomic, permissions=[Unrestricted()]), run_ids


def test_an_atomic_run_commits_its_work_and_its_state_as_one(
    make_run: Callable[..., SpecRun],
) -> None:
    run = make_run(_spec(atomic=True))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.output == "done"
    assert Note.objects.count() == 1


def test_a_kill_before_commit_leaves_no_row_and_a_running_run(
    make_run: Callable[..., SpecRun],
) -> None:
    run = make_run(_spec(atomic=True, presenter=_Dies()))

    with pytest.raises(_Killed):
        run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.RUNNING
    assert run.attempts == 1
    assert Note.objects.count() == 0


def test_a_kill_under_atomic_false_leaves_the_work_and_a_running_run(
    make_run: Callable[..., SpecRun],
) -> None:
    run = make_run(_spec(atomic=False, presenter=_Dies()))

    with pytest.raises(_Killed):
        run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.RUNNING
    assert Note.objects.count() == 1


@pytest.mark.parametrize(
    "columns",
    [
        # Fails without ``attempts`` in the fence: the row is running, under a later claim.
        {"attempts": 2},
        {"state": RunState.ABANDONED},
    ],
    ids=["reclaimed", "abandoned"],
)
def test_a_lost_fence_rolls_an_atomic_run_back(
    make_run: Callable[..., SpecRun],
    caplog: pytest.LogCaptureFixture,
    columns: dict[str, Any],
) -> None:
    finished: list[Any] = []
    spec, run_ids = _spec_losing_the_fence(atomic=True, **columns)
    run = make_run(spec, on_finish=finished.append)
    run_ids.append(run.pk)

    run_spec(str(run.pk))

    assert Note.objects.count() == 0
    assert finished == []
    assert str(run.pk) in caplog.text
    run.refresh_from_db()
    assert (run.state, run.attempts) == (RunState.RUNNING, 1)
    assert run.finished_at is None
    assert run.output is None


@pytest.mark.parametrize(
    ("columns", "state", "attempts"),
    [
        ({"attempts": 2}, RunState.RUNNING, 2),
        ({"state": RunState.ABANDONED}, RunState.ABANDONED, 1),
    ],
    ids=["reclaimed", "abandoned"],
)
def test_a_lost_fence_under_atomic_false_keeps_the_work_and_the_mover_s_row(
    make_run: Callable[..., SpecRun],
    caplog: pytest.LogCaptureFixture,
    columns: dict[str, Any],
    state: RunState,
    attempts: int,
) -> None:
    finished: list[Any] = []
    spec, run_ids = _spec_losing_the_fence(atomic=False, **columns)
    run = make_run(spec, on_finish=finished.append)
    run_ids.append(run.pk)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert (run.state, run.attempts) == (state, attempts)
    assert run.finished_at is None
    assert Note.objects.count() == 1
    assert finished == []
    assert str(run.pk) in caplog.text


def test_a_refusal_never_overwrites_a_row_that_moved_on(make_run: Callable[..., SpecRun]) -> None:
    run_ids: list[Any] = []

    def service() -> None:
        SpecRun.objects.filter(pk=run_ids[0]).update(state=RunState.ABANDONED)
        raise ServiceError("no")

    run = make_run(ServiceSpec(service=service, atomic=False, permissions=[Unrestricted()]))
    run_ids.append(run.pk)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.ABANDONED
    assert run.refusal_class is None


def test_the_fence_writes_only_its_own_row(make_run: Callable[..., SpecRun]) -> None:
    # Fails without ``pk`` in the fence: this row is running on the same attempt count.
    other = make_run(_spec(atomic=True))
    SpecRun.objects.filter(pk=other.pk).update(state=RunState.RUNNING, attempts=1)
    run = make_run(_spec(atomic=True))

    run_spec(str(run.pk))

    other.refresh_from_db()
    assert other.state == RunState.RUNNING
    assert other.finished_at is None


@pytest.mark.parametrize(("atomic", "expected"), [(True, True), (False, False)])
def test_only_an_atomic_spec_runs_inside_a_transaction(
    make_run: Callable[..., SpecRun], atomic: bool, expected: bool
) -> None:
    seen: list[bool] = []
    spec = ServiceSpec(
        service=lambda: seen.append(connection.in_atomic_block),
        atomic=atomic,
        permissions=[Unrestricted()],
    )
    run = make_run(spec)

    run_spec(str(run.pk))

    assert seen == [expected]


@pytest.mark.parametrize("atomic", [True, False], ids=["atomic", "not_atomic"])
def test_a_spec_presents_in_a_transaction_only_when_atomic(
    make_run: Callable[..., SpecRun], atomic: bool
) -> None:
    # Kept apart from the service: the kernel never opens a transaction around a presenter.
    seen: list[bool] = []

    class Looks(Presenter):
        def output(self) -> Any:
            return None

        def present(self, value: Any) -> Any:
            seen.append(connection.in_atomic_block)
            return value

    run = make_run(_spec(atomic=atomic, presenter=Looks()))

    run_spec(str(run.pk))

    assert seen == [atomic]


def test_a_selector_runs_outside_a_transaction(make_run: Callable[..., SpecRun]) -> None:
    # Fails without the type check on the way to ``spec.atomic``: a selector spec has no such field.
    seen: list[bool] = []

    def selector() -> dict[str, str]:
        seen.append(connection.in_atomic_block)
        return {"read": "this"}

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector, permissions=[Unrestricted()])
    run = make_run(spec)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert seen == [False]
    assert run.state == RunState.SUCCEEDED
    assert run.output == {"read": "this"}


def test_a_refusal_rolls_the_work_back_and_commits_its_state_and_what_it_fired(
    make_run: Callable[..., SpecRun],
) -> None:
    def service() -> None:
        Note.objects.create(text="the service's work")
        raise ServiceError("no")

    # A presenter raising is what only the outer transaction takes back.
    run = make_run(
        ServiceSpec(service=service, permissions=[Unrestricted()]),
        on_finish=lambda row: Note.objects.create(text=f"fired {row.state}"),
    )

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.REFUSED
    assert list(Note.objects.values_list("text", flat=True)) == ["fired REFUSED"]


def test_a_presenter_raising_rolls_committed_service_work_back(
    make_run: Callable[..., SpecRun],
) -> None:
    class Raises(Presenter):
        def output(self) -> Any:
            return None

        def present(self, value: Any) -> Any:
            raise ServiceError("cannot show this")

    run = make_run(_spec(atomic=True, presenter=Raises()))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.REFUSED
    assert Note.objects.count() == 0


def test_a_refusal_under_atomic_false_keeps_the_work(make_run: Callable[..., SpecRun]) -> None:
    def service() -> None:
        Note.objects.create(text="the service's work")
        raise ServiceError("no")

    run = make_run(ServiceSpec(service=service, atomic=False, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.REFUSED
    assert Note.objects.count() == 1


@pytest.mark.parametrize("atomic", [True, False])
def test_on_finish_runs_inside_the_transaction_of_the_write(
    make_run: Callable[..., SpecRun], atomic: bool
) -> None:
    seen: list[tuple[bool, str]] = []
    run = make_run(
        _spec(atomic=atomic),
        on_finish=lambda row: seen.append((connection.in_atomic_block, row.state)),
    )

    run_spec(str(run.pk))

    assert seen == [(True, RunState.SUCCEEDED)]


def test_on_finish_reads_what_a_reader_would(make_run: Callable[..., SpecRun]) -> None:
    seen: list[tuple[Any, ...]] = []
    run = make_run(
        _spec(atomic=True),
        on_finish=lambda row: seen.append(
            (row.state, row.output, row.kind, row.attempts, row.finished_at is not None)
        ),
    )

    run_spec(str(run.pk))

    assert seen == [(RunState.SUCCEEDED, "done", "instance", 1, True)]


def test_on_finish_raising_takes_the_write_and_the_work_back(
    make_run: Callable[..., SpecRun],
) -> None:
    def on_finish(row: Any) -> None:
        raise RuntimeError("a bug in the project")

    run = make_run(_spec(atomic=True), on_finish=on_finish)

    with pytest.raises(RuntimeError, match="a bug in the project"):
        run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.RUNNING
    assert Note.objects.count() == 0


def test_on_finish_raising_under_atomic_false_takes_only_the_write_back(
    make_run: Callable[..., SpecRun],
) -> None:
    def on_finish(row: Any) -> None:
        Note.objects.create(text="fired")
        raise RuntimeError("a bug in the project")

    run = make_run(_spec(atomic=False), on_finish=on_finish)

    with pytest.raises(RuntimeError, match="a bug in the project"):
        run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.RUNNING
    assert run.output is None
    assert list(Note.objects.values_list("text", flat=True)) == ["the service's work"]


def test_on_finish_raising_after_a_refusal_leaves_the_run_for_the_sweep(
    make_run: Callable[..., SpecRun],
) -> None:
    def service() -> None:
        raise ServiceError("no")

    def on_finish(row: Any) -> None:
        raise RuntimeError("a bug in the project")

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]), on_finish=on_finish)

    with pytest.raises(RuntimeError, match="a bug in the project"):
        run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.RUNNING


def test_around_run_wraps_dispatch_and_present_with_the_row_and_the_principal(
    make_run: Callable[..., SpecRun],
) -> None:
    order: list[Any] = []

    class Looks(Presenter):
        def output(self) -> Any:
            return None

        def present(self, value: Any) -> Any:
            order.append("present")
            return value

    def service() -> str:
        order.append("service")
        return "done"

    @contextmanager
    def around_run(row: Any, principal: Any) -> Generator[None]:
        order.append(("enter", row.state, row.attempts, principal))
        yield
        order.append("leave")

    spec = ServiceSpec(service=service, presenter=Looks(), permissions=[Unrestricted()])
    run = make_run(spec, around_run=around_run)

    run_spec(str(run.pk))

    assert order == [("enter", RunState.RUNNING, 1, None), "service", "present", "leave"]


def test_around_run_is_handed_a_refusal_as_it_leaves(make_run: Callable[..., SpecRun]) -> None:
    seen: list[type[BaseException]] = []

    def service() -> None:
        raise ServiceError("no")

    @contextmanager
    def around_run(row: Any, principal: Any) -> Generator[None]:
        try:
            yield
        except ServiceError as error:
            seen.append(type(error))
            raise

    run = make_run(
        ServiceSpec(service=service, permissions=[Unrestricted()]), around_run=around_run
    )

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert seen == [ServiceError]
    assert run.state == RunState.REFUSED


def test_around_run_failing_to_open_is_a_failure(make_run: Callable[..., SpecRun]) -> None:
    def around_run(row: Any, principal: Any) -> Any:
        raise RuntimeError("no scope today")

    run = make_run(_spec(atomic=True), around_run=around_run)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert "no scope today" in run.traceback
    assert Note.objects.count() == 0


def test_around_run_swallowing_what_the_run_raised_is_a_failure_and_not_a_success(
    make_run: Callable[..., SpecRun],
) -> None:
    def service() -> None:
        raise ServiceError("no")

    @contextmanager
    def around_run(row: Any, principal: Any) -> Generator[None]:
        with suppress(ServiceError):
            yield

    run = make_run(
        ServiceSpec(service=service, permissions=[Unrestricted()]), around_run=around_run
    )

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert "around_run" in run.traceback
