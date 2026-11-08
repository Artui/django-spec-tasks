from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from datetime import timedelta
from typing import cast

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django_service_specs import ServiceSpec, kernel_spec

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.types.sweep_report import SweepReport

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

    | Row | Outcome |
    | --- | --- |
    | ``PENDING`` past its binding's ``grace`` | the backend is asked again; the claim stops a second run |
    | ``RUNNING`` past its lease, spec declares ``idempotent=True``, ``attempts`` to spare | back to ``PENDING`` and the backend is asked |
    | the same, ``attempts >= max_attempts`` | ``ABANDONED``: a poison run must not loop |
    | the same, ``idempotent`` ``False`` or undeclared | ``ABANDONED`` |
    | either, binding or spec gone | ``SPEC_UNAVAILABLE`` |

    ``idempotent`` is read off the registered spec now, never off the row, so a spec whose
    declaration changed answers with what it declares today. ``None`` is undeclared and does
    not read as ``True``.

    Every transition is a compare-and-swap on ``(state, attempts)``, so two sweeps agree and
    a worker that claimed the row meanwhile is not overwritten. ``on_finish`` runs for
    ``ABANDONED`` and ``SPEC_UNAVAILABLE`` in the transaction with the write. A backend or
    an ``on_finish`` that raises leaves that row as it was and the sweep carries on; the
    row is answered by the next sweep.

    Rows are taken oldest first by ``created_at``, ``batch_size`` at a time, and ``limit``
    bounds one sweep.

    Args:
        limit: The most rows one sweep looks at.
        batch_size: How many rows are read from the database at once.

    Returns:
        What was done, counted by outcome.
    """
    counts: Counter[str] = Counter()
    for run in _candidates(limit, batch_size):
        try:
            _answer(run, counts)
        except Exception:
            logger.exception("The sweep could not answer run %s", run.pk)
            counts["errors"] += 1
    return SweepReport(**counts)


def _answer(run: SpecRun, counts: Counter[str]) -> None:
    """Decide one row and apply it, counting each outcome as it lands.

    The count of a transition is taken before the backend is asked, so a backend that
    raises afterwards is an error beside a requeue that did happen.
    """
    binding = TaskBinding.named(run.binding)
    entry = None if binding is None else binding.registry.get(run.spec_name)
    if binding is None or entry is None:
        counts["unavailable"] += _end(run, RunState.SPEC_UNAVAILABLE, binding)
        return
    if run.state == RunState.PENDING:
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


def _requeue(run: SpecRun) -> bool:
    return bool(_fenced(run).update(state=RunState.PENDING, claimed_at=None, lease_expires_at=None))


def _end(run: SpecRun, state: RunState, binding: TaskBinding | None) -> bool:
    with transaction.atomic():
        if not _fenced(run).update(state=state, finished_at=timezone.now()):
            return False
        if binding is not None and binding.on_finish is not None:
            run.refresh_from_db()
            binding.on_finish(run)
    return True


def _candidates(limit: int, batch_size: int) -> Iterator[SpecRun]:
    """Rows to look at, oldest first, paged by ``(created_at, pk)`` so none is seen twice.

    A requeued row keeps its old ``created_at`` and so reads as a lost ``PENDING`` run to
    any query that starts over. The cursor is what stops it being asked a second time.
    """
    wanted = _wanted()
    cursor: Q = Q()
    remaining = limit
    while remaining > 0:
        batch = list(
            SpecRun.objects.filter(wanted)
            .filter(cursor)
            .order_by("created_at", "pk")[: min(batch_size, remaining)]
        )
        yield from batch
        if not batch:
            return
        last = batch[-1]
        cursor = Q(created_at__gt=last.created_at) | Q(created_at=last.created_at, pk__gt=last.pk)
        remaining -= len(batch)


def _wanted() -> Q:
    """Lapsed ``RUNNING`` rows, and ``PENDING`` rows past their own binding's grace.

    A grace belongs to a binding, so the filter is built per binding name found on a live
    row, asking the registry for each. One whose binding is not known takes the default.
    """
    now = timezone.now()
    names = SpecRun.objects.filter(state=RunState.PENDING).values_list("binding", flat=True)
    lost = Q(pk__in=[])
    for name in set(names.distinct()):
        binding = TaskBinding.named(name)
        grace = _DEFAULT_GRACE if binding is None else binding.grace
        lost |= Q(binding=name, created_at__lt=now - grace)
    return Q(state=RunState.PENDING) & lost | Q(state=RunState.RUNNING, lease_expires_at__lt=now)
