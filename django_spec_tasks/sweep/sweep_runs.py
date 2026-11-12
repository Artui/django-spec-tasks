from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import cast

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django_service_specs import ServiceSpec, kernel_spec

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.types.sweep_report import SweepReport
from django_spec_tasks.version import __version__

logger = logging.getLogger(__name__)

# A row whose binding this process does not know has no grace of its own, so the default
# stands in. Otherwise a sweep running on code from before a binding was added would end
# every run of that binding the moment it was written.
_DEFAULT_GRACE = cast("timedelta", TaskBinding.__dataclass_fields__["grace"].default)


def sweep_runs(*, limit: int = 1000, batch_size: int = 100) -> SweepReport:
    """Answer the runs the queue lost and end the ones whose worker did not come back.

    Run it on a schedule the project owns: a beat entry, cron, the platform's scheduler.
    This package ships none, because whether a queue is one's own is not for a library
    to assume.

    Each row's outcome:

    - ``PENDING``, ``grace`` past its last ask: the backend is asked again; the claim
      stops a second run.
    - ``RUNNING`` past its lease, spec declares ``idempotent=True``, ``attempts`` to spare:
      back to ``PENDING`` and the backend is asked.
    - The same, with ``attempts >= max_attempts``: ``ABANDONED``, because a poison run must
      not loop.
    - The same, with ``idempotent`` ``False`` or undeclared: ``ABANDONED``.
    - Either, with the binding or the spec gone: ``SPEC_UNAVAILABLE``.

    ``idempotent`` is read off the registered spec now, never off the row, so a spec whose
    declaration changed answers with what it declares today. ``None`` is undeclared and does
    not read as ``True``.

    Every transition is a compare-and-swap on ``(state, attempts)``, so two sweeps agree and
    a worker that claimed the row meanwhile is not overwritten. An ask is stamped on
    ``asked_at`` before the backend hears it, conditioned on the value read as well, so two
    sweeps send one message, and a row waits out its grace again whether or not the backend
    took it. A backend that raises is counted in ``errors``, and the sweep carries on.

    ``on_finish`` runs for ``ABANDONED`` and ``SPEC_UNAVAILABLE`` in the transaction with the
    write. One that raises takes the write back: it is counted in ``errors``, and the row
    stays as it was, on every sweep, until the hook stops raising.

    Each binding is walked apart, its lapsed rows first and then its pending ones, longest
    waiting first, ``batch_size`` at a time, and ``limit`` is its own, so a binding whose
    queue is dead or whose hook is broken holds back nothing but itself.

    Args:
        limit: The most rows one sweep looks at for one binding.
        batch_size: How many rows are read from the database at once.

    Returns:
        What was done, counted by outcome.
    """
    counts: Counter[str] = Counter()
    for run in _candidates(timezone.now(), limit, batch_size):
        try:
            _answer(run, counts)
        except Exception:
            logger.exception("The sweep could not answer run %s", run.pk)
            counts["errors"] += 1
    return SweepReport(**counts)


def _answer(run: SpecRun, counts: Counter[str]) -> None:
    """Decide one row and apply it, counting each outcome as it lands."""
    binding = TaskBinding.named(run.binding)
    if binding is None:
        why = f"No binding named {run.binding!r} is registered."
        counts["unavailable"] += _end(run, RunState.SPEC_UNAVAILABLE, None, why)
        return
    entry = binding.registry.get(run.spec_name)
    if entry is None:
        why = f"No spec named {run.spec_name!r} on this binding."
        counts["unavailable"] += _end(run, RunState.SPEC_UNAVAILABLE, binding, why)
        return
    if run.state == RunState.PENDING:
        if _stamp_ask(run):
            binding.backend.enqueue(str(run.pk))
            counts["reasked"] += 1
        return
    spec = kernel_spec(entry.spec)
    declared = spec.idempotent if isinstance(spec, ServiceSpec) else None
    if declared is True and run.attempts < binding.max_attempts:
        if _requeue(run):
            counts["requeued"] += 1
            binding.backend.enqueue(str(run.pk))
        return
    counts["abandoned"] += _end(run, RunState.ABANDONED, binding)


def _fenced(run: SpecRun) -> QuerySet[SpecRun]:
    """The rows still in the state and attempt count this sweep read."""
    return SpecRun.objects.filter(pk=run.pk, state=run.state, attempts=run.attempts)


def _stamp_ask(run: SpecRun) -> bool:
    """Take this row's next ask: a second sweep holding the same read finds nothing to take."""
    return bool(_fenced(run).filter(asked_at=run.asked_at).update(asked_at=timezone.now()))


def _requeue(run: SpecRun) -> bool:
    return bool(
        _fenced(run).update(
            state=RunState.PENDING,
            claimed_at=None,
            lease_expires_at=None,
            asked_at=timezone.now(),
        )
    )


def _end(
    run: SpecRun, state: RunState, binding: TaskBinding | None, why: str | None = None
) -> bool:
    """Write a terminal state as the worker writes one, so a reader cannot tell who did."""
    with transaction.atomic():
        written = _fenced(run).update(
            state=state,
            finished_at=timezone.now(),
            package_version=__version__,
            refusal_detail=None if why is None else {"message": why},
        )
        if not written:
            return False
        if binding is not None and binding.on_finish is not None:
            run.refresh_from_db()
            binding.on_finish(run)
    return True


def _candidates(now: datetime, limit: int, batch_size: int) -> Iterator[SpecRun]:
    """Rows to look at, one binding at a time, each with ``limit`` of its own."""
    live = SpecRun.objects.filter(state__in=(RunState.PENDING, RunState.RUNNING))
    for name in sorted(live.values_list("binding", flat=True).distinct()):
        binding = TaskBinding.named(name)
        grace = _DEFAULT_GRACE if binding is None else binding.grace
        lapsed = SpecRun.objects.filter(
            binding=name, state=RunState.RUNNING, lease_expires_at__lt=now
        )
        lost = SpecRun.objects.filter(
            binding=name, state=RunState.PENDING, asked_at__lt=now - grace
        )
        remaining = limit
        for rows, column in ((lapsed, "lease_expires_at"), (lost, "asked_at")):
            for run in _walk(rows, column, remaining, batch_size):
                remaining -= 1
                yield run


def _walk(rows: QuerySet[SpecRun], column: str, limit: int, batch_size: int) -> Iterator[SpecRun]:
    """``rows`` longest waiting first, paged by ``(column, pk)`` so none is seen twice."""
    cursor: Q = Q()
    remaining = limit
    while remaining > 0:
        batch = list(rows.filter(cursor).order_by(column, "pk")[: min(batch_size, remaining)])
        yield from batch
        if not batch:
            return
        last = batch[-1]
        moment = getattr(last, column)
        cursor = Q(**{f"{column}__gt": moment}) | Q(**{column: moment, "pk__gt": last.pk})
        remaining -= len(batch)
