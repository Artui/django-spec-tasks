"""``RowProgress`` - a progress reporter that writes to the run's own row."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from django.db import transaction

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState

logger = logging.getLogger(__name__)


class RowProgress:
    """Writes each report to the row, for as long as this attempt still holds it.

    A kernel ``ProgressReporter``. With a ``total`` the column holds the fraction done, and
    without one the service's own unit, since the row has one number to hold it in. ``meta``
    has nowhere to go here and is dropped, which the protocol allows.

    The row is held by ``(pk, state, attempts)``, as the terminal write is, so a worker past
    its lease cannot write over the attempt after it. Each condition is one arc with its own
    test: ``test_a_report_touches_only_its_own_row`` fails without ``pk``,
    ``test_a_finished_run_takes_no_more_reports`` without ``state`` and
    ``test_a_stale_attempt_writes_nothing`` without ``attempts``.

    Reporting never ends a run: the protocol forbids raising into the service, so a write
    that fails is logged and dropped. Each write takes a savepoint, because a database error
    inside the kernel's transaction around the service would otherwise mark it broken, and
    the service's work would roll back as it returned, under a run recorded as a success
    (``test_a_failed_write_keeps_the_service_s_work``).

    Args:
        run_id: The row.
        attempts: The attempt this reporter belongs to.
        using: A second database alias, which makes reports visible while an ``atomic=True``
            run's outer transaction is still open. ``None`` writes where the row lives.
    """

    def __init__(self, run_id: Any, attempts: int, *, using: str | None = None) -> None:
        self._run_id = run_id
        self._attempts = attempts
        self._using = using

    def __call__(
        self,
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            with transaction.atomic(using=self._using):
                SpecRun.objects.using(self._using).filter(
                    pk=self._run_id, state=RunState.RUNNING, attempts=self._attempts
                ).update(
                    progress=progress / total if total else progress,
                    progress_message=message,
                )
        except Exception:
            logger.warning(
                "a progress report for run %s was not written and is dropped",
                self._run_id,
                exc_info=True,
            )
