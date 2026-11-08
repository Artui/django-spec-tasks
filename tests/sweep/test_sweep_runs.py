"""Each row of the sweep table, and the guards each conjunct of a decision hangs on."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.utils import timezone
from django_service_specs import SelectorSpec, ServiceSpec, SpecRegistry, Unrestricted

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.sweep.sweep_runs import sweep_runs
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.version import __version__
from django_spec_tasks.worker.run_spec import run_spec
from tests.sweep.conftest import SPEC, RecordingBackend, ago, ahead, reload, spec_declaring

pytestmark = pytest.mark.django_db

MakeBinding = Callable[..., TaskBinding]
MakeRun = Callable[..., SpecRun]

IDEMPOTENT = [True, False, None]


def _broken(run: SpecRun) -> None:
    raise RuntimeError("the project's hook is broken")


class Finished:
    """An ``on_finish`` that remembers the state it saw each run in."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, bool, bool]] = []

    def __call__(self, run: SpecRun) -> None:
        self.seen.append(
            (str(run.pk), run.state, run.finished_at is not None, connection.in_atomic_block)
        )


# --- PENDING past grace: the backend is asked again, for any declaration ---------------


@pytest.mark.parametrize("idempotent", IDEMPOTENT)
def test_a_pending_run_past_its_grace_is_asked_for_again(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend, idempotent: bool | None
) -> None:
    binding = make_binding(idempotent=idempotent)
    run = make_run(binding, created_at=ago(minutes=11))

    report = sweep_runs()

    assert backend.asked == [str(run.pk)]
    assert reload(run).state == RunState.PENDING
    assert (report.reasked, report.moved) == (1, 0)


def test_a_pending_run_inside_its_grace_is_left_alone(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    # Names the asked_at conjunct: without it every pending run is asked for at once.
    binding = make_binding()
    make_run(binding, created_at=ago(minutes=9))

    assert sweep_runs().reasked == 0
    assert backend.asked == []


def test_a_bindings_own_grace_decides(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    slow = make_binding(grace=timedelta(hours=1))
    quick = make_binding(grace=timedelta(minutes=1))
    make_run(slow, created_at=ago(minutes=5))
    asked = make_run(quick, created_at=ago(minutes=5))

    sweep_runs()

    assert backend.asked == [str(asked.pk)]


@pytest.mark.parametrize("state", [RunState.SUCCEEDED, RunState.FAILED, RunState.ABANDONED])
def test_a_finished_run_is_never_asked_for(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend, state: RunState
) -> None:
    # Names the state conjunct of the pending branch: an old, finished row has the age.
    binding = make_binding()
    run = make_run(binding, state=state, created_at=ago(days=3), finished_at=ago(days=2))
    # The binding has a pending row of its own, which is what puts its grace in the query.
    make_run(binding, created_at=ago(minutes=1))

    report = sweep_runs()

    assert backend.asked == []
    assert (reload(run).state, report.moved) == (state, 0)


# --- RUNNING past its lease -------------------------------------------------------------


def test_an_idempotent_lapse_with_attempts_to_spare_goes_back_to_pending(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    finished = Finished()
    binding = make_binding(idempotent=True, on_finish=finished)
    run = lapsed(binding, attempts=2)

    report = sweep_runs()

    row = reload(run)
    assert (row.state, row.attempts) == (RunState.PENDING, 2)
    assert row.claimed_at is None
    assert row.lease_expires_at is None
    assert row.finished_at is None
    assert backend.asked == [str(run.pk)]
    assert finished.seen == []
    assert (report.requeued, report.reasked, report.abandoned) == (1, 0, 0)


@pytest.mark.parametrize("attempts", [3, 4])
def test_an_idempotent_lapse_out_of_attempts_is_abandoned(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend, attempts: int
) -> None:
    # Names ``attempts < max_attempts``: tested at exactly equal, where ``<=`` would loop.
    finished = Finished()
    binding = make_binding(idempotent=True, on_finish=finished, max_attempts=3)
    run = lapsed(binding, attempts=attempts)

    report = sweep_runs()

    row = reload(run)
    assert row.state == RunState.ABANDONED
    assert row.finished_at is not None
    assert backend.asked == []
    assert finished.seen == [(str(run.pk), "ABANDONED", True, True)]
    assert (report.abandoned, report.requeued) == (1, 0)


@pytest.mark.parametrize("idempotent", [False, None])
def test_a_lapse_not_declared_idempotent_is_abandoned(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend, idempotent: bool | None
) -> None:
    # Names ``idempotent is True``: ``None`` is undeclared and must not read as ``True``,
    # and attempts are at zero so only the declaration can be what decides.
    binding = make_binding(idempotent=idempotent)
    run = lapsed(binding, attempts=0)

    sweep_runs()

    assert reload(run).state == RunState.ABANDONED
    assert backend.asked == []


def test_a_lapse_declared_idempotent_is_requeued_with_the_same_attempts_to_spare(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    # The twin of the one above: same row, same attempts, only the declaration differs.
    binding = make_binding(idempotent=True)
    run = lapsed(binding, attempts=0)

    sweep_runs()

    assert reload(run).state == RunState.PENDING


def test_a_run_inside_its_lease_is_left_alone(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    # Names ``lease_expires_at < now``: a live worker's run is not the sweep's.
    binding = make_binding(idempotent=True)
    run = lapsed(binding, lease_expires_at=ahead(minutes=5))

    report = sweep_runs()

    assert reload(run).state == RunState.RUNNING
    assert (report.moved, report.reasked) == (0, 0)
    assert backend.asked == []


def test_a_running_run_with_no_lease_is_left_alone(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    # Names the not-null half of the lease conjunct: no lease is not an expired one.
    binding = make_binding(idempotent=True)
    run = lapsed(binding, lease_expires_at=None)

    assert sweep_runs().moved == 0
    assert reload(run).state == RunState.RUNNING


@pytest.mark.parametrize("state", [RunState.SUCCEEDED, RunState.FAILED])
def test_a_finished_run_with_an_old_lease_is_left_alone(
    make_binding: MakeBinding, make_run: MakeRun, lapsed: MakeRun, state: RunState
) -> None:
    # Names ``state == RUNNING``: a finished run keeps the lease it held. The young pending row
    # is what makes the sweep walk this binding at all.
    binding = make_binding(idempotent=True)
    make_run(binding)
    run = lapsed(binding, state=state, finished_at=ago(hours=1))

    assert sweep_runs().moved == 0
    assert reload(run).state == state


def test_a_selector_spec_lapsing_is_abandoned_because_it_declares_nothing(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    registry = SpecRegistry()
    registry.register(
        "lookup",
        SelectorSpec(kind="list", selector=lambda: [], permissions=[Unrestricted()]),
    )
    binding = TaskBinding(name="selector-binding", registry=registry, backend=RecordingBackend())
    run = lapsed(binding, spec_name="lookup", attempts=0)

    sweep_runs()

    assert reload(run).state == RunState.ABANDONED


# --- The declaration is read from the spec, never from the row --------------------------


def test_idempotent_is_read_from_the_spec_as_it_is_now(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    binding = make_binding(idempotent=True)
    entry = binding.registry.get(SPEC)
    assert entry is not None
    run = lapsed(binding, attempts=0)
    # The registry the binding serves is replaced after the run was written, as a deploy
    # that changed the declaration would. A copy of ``idempotent`` on the row would still
    # say True here.
    object.__setattr__(
        binding, "registry", binding.registry.replace(SPEC, entry.spec.derive(idempotent=False))
    )

    sweep_runs()

    assert reload(run).state == RunState.ABANDONED
    assert backend.asked == []


class _Source:
    """A declaration of another library's: it is not a spec, it compiles to one."""

    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec

    def as_kernel_spec(self) -> ServiceSpec:
        return self.spec


def test_idempotent_is_read_through_the_compiled_spec_of_a_source(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    registry = SpecRegistry()
    registry.register(SPEC, _Source(spec_declaring(True)))
    binding = TaskBinding(name="source-binding", registry=registry, backend=backend)
    run = lapsed(binding, attempts=0)

    sweep_runs()

    assert reload(run).state == RunState.PENDING
    assert backend.asked == [str(run.pk)]


# --- The binding or the spec is gone ----------------------------------------------------


@pytest.mark.parametrize("age", [{"minutes": 11}, {"days": 2}])
def test_a_pending_run_whose_spec_is_gone_is_unavailable(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend, age: dict[str, int]
) -> None:
    finished = Finished()
    binding = make_binding(on_finish=finished)
    run = make_run(binding, spec_name="removed_since", created_at=ago(**age))

    report = sweep_runs()

    row = reload(run)
    assert row.state == RunState.SPEC_UNAVAILABLE
    assert row.finished_at is not None
    assert finished.seen == [(str(run.pk), "SPEC_UNAVAILABLE", True, True)]
    assert backend.asked == []
    assert report.unavailable == 1


@pytest.mark.parametrize("idempotent", IDEMPOTENT)
def test_a_lapsed_run_whose_spec_is_gone_is_unavailable_whatever_it_declared(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend, idempotent: bool | None
) -> None:
    # The gone-spec check comes before the declaration is read.
    finished = Finished()
    binding = make_binding(idempotent=idempotent, on_finish=finished)
    run = lapsed(binding, spec_name="removed_since", attempts=0)

    sweep_runs()

    assert reload(run).state == RunState.SPEC_UNAVAILABLE
    assert len(finished.seen) == 1
    assert backend.asked == []


def test_a_lapsed_run_whose_binding_is_gone_is_unavailable(lapsed: MakeRun) -> None:
    run = lapsed("never-registered")

    report = sweep_runs()

    assert reload(run).state == RunState.SPEC_UNAVAILABLE
    assert reload(run).finished_at is not None
    assert report.unavailable == 1


@pytest.mark.parametrize(
    "where", [{"binding": "never-registered"}, {"spec_name": "removed_since"}], ids=str
)
def test_the_sweep_and_the_worker_end_an_unavailable_run_alike(
    make_binding: MakeBinding, make_run: MakeRun, lapsed: MakeRun, where: dict[str, str]
) -> None:
    # Two writers of one state, and a reader cannot tell which wrote it.
    binding = where.get("binding", make_binding())
    fields = {"spec_name": where.get("spec_name", SPEC)}
    by_worker = make_run(binding, **fields)
    by_sweep = lapsed(binding, **fields)

    run_spec(str(by_worker.pk))
    sweep_runs()

    swept, worked = reload(by_sweep), reload(by_worker)
    assert swept.state == worked.state == RunState.SPEC_UNAVAILABLE
    assert swept.refusal_detail == worked.refusal_detail
    assert list(where.values())[0] in swept.refusal_detail["message"]


@pytest.mark.parametrize(
    ("idempotent", "fields", "state"),
    [
        (False, {}, RunState.ABANDONED),
        (True, {"spec_name": "removed_since"}, RunState.SPEC_UNAVAILABLE),
    ],
)
def test_a_row_the_sweep_ends_carries_the_package_version(
    make_binding: MakeBinding,
    lapsed: MakeRun,
    idempotent: bool,
    fields: dict[str, str],
    state: RunState,
) -> None:
    run = lapsed(make_binding(idempotent=idempotent), **fields)

    sweep_runs()

    row = reload(run)
    assert row.state == state
    assert row.package_version == __version__


def test_a_pending_run_whose_binding_is_gone_waits_out_the_default_grace(
    make_run: MakeRun,
) -> None:
    # A binding that is not known has no grace of its own, so the default stands in: a
    # sweep on stale code must not end a binding's runs the moment they are written.
    young = make_run("never-registered", created_at=ago(minutes=9))
    old = make_run("never-registered", created_at=ago(minutes=11))

    sweep_runs()

    assert reload(young).state == RunState.PENDING
    assert reload(old).state == RunState.SPEC_UNAVAILABLE


# --- Two sweeps, one transition: the fence ----------------------------------------------


def test_two_sweeps_make_one_transition(make_binding: MakeBinding, lapsed: MakeRun) -> None:
    finished = Finished()
    binding = make_binding(on_finish=finished)
    run = lapsed(binding)

    first = sweep_runs()
    second = sweep_runs()

    assert (first.abandoned, second.moved) == (1, 0)
    assert len(finished.seen) == 1
    assert reload(run).state == RunState.ABANDONED


class Mover:
    """An ``on_finish`` that changes another row behind the sweep, as a worker would."""

    def __init__(self, **changes: Any) -> None:
        self.changes = changes
        self.target: SpecRun | None = None
        self.seen: list[Any] = []

    def __call__(self, run: SpecRun) -> None:
        assert self.target is not None
        self.seen.append(run.pk)
        SpecRun.objects.filter(pk=self.target.pk).update(**self.changes)


def test_a_row_that_moved_since_the_batch_was_read_is_not_overwritten_state(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    # The first row's ``on_finish`` runs while the batch holds the second as it was. The
    # second finishes meanwhile; the fence's ``state`` is what refuses the sweep.
    mover = Mover(state=RunState.SUCCEEDED)
    binding = make_binding(idempotent=True, on_finish=mover)
    first = lapsed(binding, attempts=3, lease_expires_at=ago(hours=5))
    mover.target = lapsed(binding, attempts=3)

    report = sweep_runs()

    assert reload(first).state == RunState.ABANDONED
    assert reload(mover.target).state == RunState.SUCCEEDED
    assert mover.seen == [first.pk]
    assert report.abandoned == 1


def test_a_row_reclaimed_since_the_batch_was_read_is_not_overwritten_attempts(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    # Still ``RUNNING``, but claimed again: only ``attempts`` tells it from the snapshot.
    mover = Mover(attempts=4)
    binding = make_binding(idempotent=True, on_finish=mover)
    first = lapsed(binding, attempts=3, lease_expires_at=ago(hours=5))
    mover.target = lapsed(binding, attempts=3)

    sweep_runs()

    assert reload(first).state == RunState.ABANDONED
    row = reload(mover.target)
    assert (row.state, row.attempts) == (RunState.RUNNING, 4)
    assert mover.seen == [first.pk]


def test_a_row_that_finished_since_the_batch_was_read_is_not_requeued(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    # The requeue is fenced as an ending is: a worker that finished the run first wins,
    # and the backend is not asked for a run that is done.
    mover = Mover(state=RunState.SUCCEEDED)
    binding = make_binding(idempotent=True, on_finish=mover)
    lapsed(binding, attempts=3, lease_expires_at=ago(hours=5))
    mover.target = lapsed(binding, attempts=1)

    report = sweep_runs()

    assert reload(mover.target).state == RunState.SUCCEEDED
    assert backend.asked == []
    assert report.requeued == 0


def test_a_pending_row_claimed_since_the_batch_was_read_is_not_ended(
    make_binding: MakeBinding, make_run: MakeRun
) -> None:
    mover = Mover(state=RunState.RUNNING)
    binding = make_binding(on_finish=mover)
    first = make_run(binding, spec_name="removed_since", created_at=ago(hours=5))
    mover.target = make_run(binding, spec_name="removed_since", created_at=ago(hours=1))

    sweep_runs()

    assert reload(mover.target).state == RunState.RUNNING
    assert mover.seen == [first.pk]


class MovingBackend(RecordingBackend):
    """A backend that changes the other row while being asked, as a second sweep would."""

    def __init__(self, **changes: Any) -> None:
        super().__init__()
        self.changes = changes
        self.target: SpecRun | None = None

    def enqueue(self, run_id: str) -> None:
        super().enqueue(run_id)
        assert self.target is not None
        SpecRun.objects.filter(pk=self.target.pk).update(**self.changes)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"asked_at": timezone.now()}, id="asked-by-another-sweep"),
        pytest.param({"state": RunState.RUNNING}, id="claimed"),
        pytest.param({"attempts": 1}, id="claimed-and-handed-back"),
    ],
)
def test_a_row_that_moved_since_the_batch_was_read_is_not_asked_for(
    make_binding: MakeBinding, make_run: MakeRun, changes: dict[str, Any]
) -> None:
    # One message from two sweeps racing, and none for a run a worker already has.
    backend = MovingBackend(**changes)
    binding = make_binding(backend=backend)
    first = make_run(binding, created_at=ago(hours=5))
    backend.target = make_run(binding, created_at=ago(hours=1))

    report = sweep_runs()

    assert backend.asked == [str(first.pk)]
    assert report.reasked == 1


@pytest.mark.parametrize(
    ("idempotent", "state", "ends", "asked"),
    [(False, RunState.ABANDONED, 1, 0), (True, RunState.PENDING, 0, 1)],
)
def test_a_workers_late_terminal_write_after_the_sweep_moved_the_row_is_refused(
    make_run: MakeRun,
    backend: RecordingBackend,
    idempotent: bool,
    state: RunState,
    ends: int,
    asked: int,
) -> None:
    # The real worker and the real sweep. The lease runs out while the service works and the
    # sweep answers the row, so what the worker writes as it returns is the late write.
    def service() -> str:
        SpecRun.objects.filter(state=RunState.RUNNING).update(lease_expires_at=ago(minutes=1))
        sweep_runs()
        return "too late"

    registry = SpecRegistry()
    registry.register(
        SPEC,
        ServiceSpec(
            service=service, permissions=[Unrestricted()], idempotent=idempotent, atomic=False
        ),
    )
    finished = Finished()
    binding = TaskBinding(name="late", registry=registry, backend=backend, on_finish=finished)
    run = make_run(binding)

    run_spec(str(run.pk))

    row = reload(run)
    assert row.state == state
    assert row.output is None
    assert row.kind is None
    assert len(finished.seen) == ends
    assert len(backend.asked) == asked


# --- Errors in a row do not stop the sweep ----------------------------------------------


def test_a_backend_that_raises_leaves_the_row_pending_and_the_sweep_goes_on(
    make_binding: MakeBinding, lapsed: MakeRun, make_run: MakeRun, backend: RecordingBackend
) -> None:
    binding = make_binding(idempotent=True)
    refused = lapsed(binding, created_at=ago(hours=5))
    asked = make_run(binding, created_at=ago(hours=1))
    backend.refuse.add(str(refused.pk))

    report = sweep_runs()

    assert reload(refused).state == RunState.PENDING
    assert backend.asked == [str(asked.pk)]
    assert (report.requeued, report.reasked, report.errors) == (1, 1, 1)


def test_a_backend_that_raises_for_a_pending_row_is_counted_not_reasked(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    binding = make_binding()
    refused = make_run(binding, created_at=ago(hours=1))
    backend.refuse.add(str(refused.pk))

    report = sweep_runs()

    assert (report.reasked, report.errors) == (0, 1)
    assert reload(refused).state == RunState.PENDING


@pytest.mark.django_db(transaction=True)
def test_an_on_finish_that_raises_rolls_its_row_back_and_the_sweep_goes_on(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    bad = make_binding(on_finish=_broken)
    good = make_binding()
    stuck = lapsed(bad, created_at=ago(hours=5))
    ended = lapsed(good, created_at=ago(hours=1))

    report = sweep_runs()

    # The state and what it fires commit as one, so the row waits for the next sweep.
    assert reload(stuck).state == RunState.RUNNING
    assert reload(stuck).finished_at is None
    assert reload(ended).state == RunState.ABANDONED
    assert (report.abandoned, report.errors) == (1, 1)


@pytest.mark.django_db(transaction=True)
def test_on_finish_runs_inside_the_transaction_with_the_write(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    finished = Finished()
    binding = make_binding(on_finish=finished)
    run = lapsed(binding)

    sweep_runs()

    # Under ``transaction=True`` nothing else has opened an atomic block, so seeing one
    # here is the sweep's own.
    assert finished.seen == [(str(run.pk), "ABANDONED", True, True)]


# --- Order and bound --------------------------------------------------------------------


def test_a_limit_bounds_one_sweep_and_takes_the_oldest_first(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    binding = make_binding()
    runs = [make_run(binding, created_at=ago(hours=hours)) for hours in (1, 5, 3, 4, 2)]

    report = sweep_runs(limit=2)

    assert report.reasked == 2
    assert backend.asked == [str(runs[1].pk), str(runs[3].pk)]


def test_paging_asks_each_row_once_and_does_not_revisit_a_requeued_row(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    binding = make_binding(idempotent=True)
    runs = [lapsed(binding, created_at=ago(hours=hours)) for hours in (5, 4, 3)]

    report = sweep_runs(batch_size=1)

    assert sorted(backend.asked) == sorted(str(run.pk) for run in runs)
    assert (report.requeued, report.reasked) == (3, 0)


def test_a_requeued_row_is_not_asked_for_again_in_the_same_sweep_with_no_grace(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    # Lapsed rows are read first, so a row they send back must not read as a lost one when the
    # pending rows are read next. Its ask is later than the moment the sweep measures from.
    run = lapsed(make_binding(idempotent=True, grace=timedelta(0)))

    report = sweep_runs()

    assert backend.asked == [str(run.pk)]
    assert (report.requeued, report.reasked) == (1, 0)


def test_pending_rows_asked_at_one_moment_are_each_handled_once_across_pages(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    binding = make_binding()
    moment = ago(hours=1)
    runs = [make_run(binding, created_at=moment) for _ in range(4)]

    report = sweep_runs(batch_size=1)

    assert sorted(backend.asked) == sorted(str(run.pk) for run in runs)
    assert report.reasked == 4


def test_lapsed_rows_sharing_a_lease_are_each_handled_once_across_pages(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    # A row whose ending fails stays lapsed, so only the cursor keeps it from a second answer.
    binding = make_binding(on_finish=_broken)
    moment = ago(hours=1)
    for _ in range(4):
        lapsed(binding, lease_expires_at=moment)

    report = sweep_runs(batch_size=1)

    assert report.errors == 4


def test_a_limit_is_each_binding_s_own(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    for binding in (make_binding(), make_binding()):
        for hours in (1, 2, 3):
            make_run(binding, created_at=ago(hours=hours))

    report = sweep_runs(limit=2)

    assert report.reasked == 4


def test_lapsed_rows_are_answered_before_pending_rows_within_a_binding(
    make_binding: MakeBinding, make_run: MakeRun, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    # However old the pending row, a re-ask can be repeated and a dead worker's row cannot wait.
    binding = make_binding()
    make_run(binding, created_at=ago(days=3))
    run = lapsed(binding, created_at=ago(hours=1))

    report = sweep_runs(limit=1)

    assert reload(run).state == RunState.ABANDONED
    assert backend.asked == []
    assert report.abandoned == 1


def test_the_row_longest_unasked_is_asked_for_first(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    # Taking the oldest row every time would hold the ones behind it back for good.
    binding = make_binding(grace=timedelta(0))
    runs = [make_run(binding, created_at=ago(hours=hours)) for hours in (3, 2, 1)]

    for _ in range(4):
        sweep_runs(limit=1)

    assert backend.asked == [str(run.pk) for run in (*runs, runs[0])]


def test_a_lapsed_row_longest_past_its_lease_is_answered_first(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    binding = make_binding()
    later = lapsed(binding, lease_expires_at=ago(minutes=1), created_at=ago(hours=5))
    earlier = lapsed(binding, lease_expires_at=ago(minutes=5), created_at=ago(hours=1))

    sweep_runs(limit=1)

    assert reload(earlier).state == RunState.ABANDONED
    assert reload(later).state == RunState.RUNNING


# --- A grace is measured from the last ask ----------------------------------------------


def test_a_row_just_asked_for_waits_out_its_grace_again(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    # A backed-up queue would otherwise get one more message per row on every sweep.
    binding = make_binding(grace=timedelta(minutes=10))
    run = make_run(binding, created_at=ago(hours=1))

    assert sweep_runs().reasked == 1
    assert sweep_runs().reasked == 0
    assert backend.asked == [str(run.pk)]

    SpecRun.objects.filter(pk=run.pk).update(asked_at=ago(minutes=11))

    assert sweep_runs().reasked == 1
    assert backend.asked == [str(run.pk), str(run.pk)]


def test_a_requeued_row_waits_out_its_grace_before_it_is_asked_for_again(
    make_binding: MakeBinding, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    run = lapsed(make_binding(idempotent=True), created_at=ago(hours=5))

    assert sweep_runs().requeued == 1
    assert sweep_runs().reasked == 0
    assert backend.asked == [str(run.pk)]


def test_an_old_row_asked_for_recently_is_left_alone(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    make_run(make_binding(), created_at=ago(hours=5), asked_at=ago(minutes=1))

    assert sweep_runs().reasked == 0
    assert backend.asked == []


def test_a_row_a_backend_refused_waits_out_its_grace_too(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    # Stamped before the ask: a queue that is down is not hammered, and the row goes to the
    # back of the line rather than holding the budget of every sweep until it recovers.
    run = make_run(make_binding(), created_at=ago(hours=1))
    backend.refuse.add(str(run.pk))

    assert sweep_runs().errors == 1
    assert sweep_runs().errors == 0


# --- One binding cannot starve another --------------------------------------------------


def test_a_binding_with_a_broken_hook_cannot_use_another_binding_s_budget(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    bad = make_binding(on_finish=_broken)
    for hours in (5, 4, 3):
        lapsed(bad, created_at=ago(hours=hours))
    run = lapsed(make_binding(), created_at=ago(hours=1))

    report = sweep_runs(limit=3)

    assert reload(run).state == RunState.ABANDONED
    assert (report.abandoned, report.errors) == (1, 3)


def test_a_binding_with_a_dead_queue_cannot_use_another_binding_s_budget(
    make_binding: MakeBinding, make_run: MakeRun, lapsed: MakeRun, backend: RecordingBackend
) -> None:
    bad = make_binding()
    for hours in (5, 4, 3):
        backend.refuse.add(str(make_run(bad, created_at=ago(hours=hours)).pk))
    run = lapsed(make_binding(), created_at=ago(hours=1))

    report = sweep_runs(limit=3)

    assert reload(run).state == RunState.ABANDONED
    assert (report.abandoned, report.errors) == (1, 3)


def test_a_hook_that_always_raises_keeps_its_row_running_and_counted(
    make_binding: MakeBinding, lapsed: MakeRun
) -> None:
    # The sweep calls the same hook the worker did, so no later sweep can end this row until
    # the hook stops raising. The operating page says so.
    run = lapsed(make_binding(on_finish=_broken))

    for _ in range(3):
        assert sweep_runs().errors == 1

    assert reload(run).state == RunState.RUNNING


def test_a_sweep_with_nothing_to_do_reports_nothing(make_binding: MakeBinding) -> None:
    make_binding()

    report = sweep_runs()

    assert (report.reasked, report.moved, report.errors) == (0, 0, 0)
