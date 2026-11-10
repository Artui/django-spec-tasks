from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from django_service_specs import Presenter, ServiceSpec, SpecRegistry, Unrestricted

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.caller.enqueue import enqueue
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.sweep.sweep_runs import sweep_runs
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.worker.run_spec import run_spec
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db(transaction=True)

SPEC = "do_it"

# The caller's ambient correlation, and the worker's scope: two variables because the two
# processes share neither, and the row is the only thing that crosses.
_caller_correlation: ContextVar[str | None] = ContextVar("caller_correlation", default=None)
_scope: ContextVar[dict[str, Any] | None] = ContextVar("scope", default=None)


# What happened in the worker, in order: the seam's entry and exit, and each event written.
_trace: list[str] = []


@pytest.fixture(autouse=True)
def _clear_the_trace() -> None:
    _trace.clear()


class _Killed(BaseException):
    """A worker dying outright: nothing above it may catch this."""


class _Dies(Presenter):
    """Kills the worker once the service has returned, before the terminal write."""

    def output(self) -> Any:
        return None

    def present(self, value: Any) -> Any:
        raise _Killed


class _Backend:
    """Remembers what it was asked; the test then runs the row itself."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def enqueue(self, run_id: str) -> None:
        self.sent.append(run_id)


def _fire(event: str, **fields: Any) -> None:
    """Write one event to the log, stamped with whatever scope this thread is in."""
    _trace.append(f"fire:{event}")
    Note.objects.create(text=json.dumps({"event": event, **(_scope.get() or {}), **fields}))


def _events() -> list[dict[str, Any]]:
    return [json.loads(text) for text in Note.objects.order_by("pk").values_list("text", flat=True)]


# The three seams a project fills, as the documentation shows them.


def carry_correlation(actor: Any) -> dict[str, Any]:
    correlation = _caller_correlation.get()
    return {"correlation_id": correlation} if correlation else {}


@contextmanager
def attribute(run: Any, principal: Any) -> Generator[None]:
    token = _scope.set(
        {
            "actor": None if principal is None else principal.pk,
            "correlation": run.carried.get("correlation_id"),
        }
    )
    _trace.append("enter")
    try:
        yield
        # Not in a finally: a generator the caller never exits is closed on collection too.
        _trace.append("leave")
    finally:
        _scope.reset(token)


def announce(run: Any) -> None:
    _fire("run.finished", run=str(run.pk), state=run.state)


def _service() -> str:
    _fire("service.done")
    return "done"


def _binding(spec: ServiceSpec, backend: _Backend | None = None, **seams: Any) -> TaskBinding:
    registry = SpecRegistry()
    registry.register(SPEC, spec)
    return TaskBinding(name="events", registry=registry, backend=backend or _Backend(), **seams)


def _spec(*, atomic: bool = True, presenter: Presenter | None = None) -> ServiceSpec:
    return ServiceSpec(
        service=_service, atomic=atomic, presenter=presenter, permissions=[Unrestricted()]
    )


def _pending_run(binding: TaskBinding) -> SpecRun:
    return SpecRun.objects.create(binding=binding.name, spec_name=SPEC, params={})


# --- carry and around_run: attribution crosses the row, and only the row ---------------


def test_the_callers_correlation_and_the_principal_reach_the_service_through_the_row(
    django_user_model: Any,
) -> None:
    user = django_user_model.objects.create_user("ada")
    backend = _Backend()
    binding = _binding(_spec(), backend, carry=carry_correlation, around_run=attribute)

    token = _caller_correlation.set("corr-1")
    try:
        run_id = enqueue(binding, SPEC, {}, actor=user)
    finally:
        _caller_correlation.reset(token)

    # The caller's scope is gone by the time the worker runs: all that is left is the row.
    assert backend.sent == [run_id]
    assert SpecRun.objects.get(pk=run_id).carried == {"correlation_id": "corr-1"}
    assert _scope.get() is None

    run_spec(run_id)

    assert _events()[0] == {"event": "service.done", "actor": user.pk, "correlation": "corr-1"}
    assert _trace == ["enter", "fire:service.done", "leave"]
    assert _scope.get() is None


# --- on_finish: the run's state and what it fired are one fact ------------------------


def test_what_the_service_fired_and_what_the_finish_fired_commit_with_the_state() -> None:
    run = _pending_run(_binding(_spec(), on_finish=announce))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    # Fails without the ``on_finish`` call: the second event is missing.
    assert [event["event"] for event in _events()] == ["service.done", "run.finished"]
    assert _events()[1]["state"] == "SUCCEEDED"


def test_a_worker_killed_before_the_commit_leaves_neither_event_and_a_running_run() -> None:
    run = _pending_run(_binding(_spec(presenter=_Dies()), on_finish=announce))

    with pytest.raises(_Killed):
        run_spec(str(run.pk))

    run.refresh_from_db()
    # Fails without the outer transaction: the event stays, committed by the kernel's own.
    assert run.state == RunState.RUNNING
    assert _events() == []


@pytest.mark.parametrize("atomic", [True, False], ids=["atomic", "not-atomic"])
@pytest.mark.parametrize(
    "columns",
    [{"attempts": 2}, {"state": RunState.ABANDONED}],
    ids=["reclaimed", "abandoned"],
)
def test_a_run_that_lost_its_claim_fires_no_finish_event_and_an_atomic_one_no_event_at_all(
    atomic: bool, columns: dict[str, Any]
) -> None:
    run_ids: list[Any] = []

    def service() -> None:
        _fire("service.done")
        SpecRun.objects.filter(pk=run_ids[0]).update(**columns)

    spec = ServiceSpec(service=service, atomic=atomic, permissions=[Unrestricted()])
    run = _pending_run(_binding(spec, on_finish=announce))
    run_ids.append(run.pk)

    run_spec(str(run.pk))

    # The finish event is the terminal write's, so it exists if and only if that write did.
    assert [event["event"] for event in _events()] == ([] if atomic else ["service.done"])


# --- the sweep writes the same row, and the seam holds for its endings too -------------


def _lapsed(binding: TaskBinding) -> SpecRun:
    return SpecRun.objects.create(
        binding=binding.name,
        spec_name=SPEC,
        params={},
        state=RunState.RUNNING,
        attempts=1,
        claimed_at=timezone.now() - timedelta(hours=2),
        lease_expires_at=timezone.now() - timedelta(minutes=1),
    )


def test_a_run_the_sweep_abandons_fires_its_finish_event_with_the_state() -> None:
    run = _lapsed(_binding(_spec(), on_finish=announce))

    report = sweep_runs()

    run.refresh_from_db()
    assert (run.state, report.abandoned) == (RunState.ABANDONED, 1)
    assert _events() == [{"event": "run.finished", "run": str(run.pk), "state": "ABANDONED"}]


def test_a_finish_event_the_sweep_wrote_goes_when_its_hook_raises_after_writing_it() -> None:
    def broken(run: Any) -> None:
        announce(run)
        raise RuntimeError("the project's hook is broken")

    run = _lapsed(_binding(_spec(), on_finish=broken))

    report = sweep_runs()

    run.refresh_from_db()
    # Fails when on_finish runs after the sweep's transaction closed: the event commits anyway.
    assert (run.state, report.errors) == (RunState.RUNNING, 1)
    assert _events() == []
