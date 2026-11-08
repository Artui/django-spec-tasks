from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.terminal_states import TERMINAL_STATES


def prune_runs(*, older_than: timedelta, batch_size: int = 1000) -> int:
    """Delete runs that finished more than ``older_than`` ago, and no others.

    A run that is still ``PENDING`` or ``RUNNING`` is never deleted, however old: it is
    the sweep's to answer, and deleting it would lose work. A finished run without a
    ``finished_at`` is left too, since there is no age to measure. Retention is the
    project's call, so there is no default age.

    Args:
        older_than: How long ago a run must have finished.
        batch_size: How many rows one delete takes, so no statement is unbounded.

    Returns:
        How many rows were deleted.
    """
    cutoff = timezone.now() - older_than
    expired = SpecRun.objects.filter(state__in=TERMINAL_STATES, finished_at__lt=cutoff)
    deleted = 0
    while batch := list(expired.order_by("finished_at").values_list("pk", flat=True)[:batch_size]):
        deleted += SpecRun.objects.filter(pk__in=batch).delete()[0]
    return deleted
