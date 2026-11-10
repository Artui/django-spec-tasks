from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from django.db import transaction

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState

logger = logging.getLogger(__name__)


class RowProgress:
    """Writes each report to the row, for as long as this attempt still holds it."""

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
