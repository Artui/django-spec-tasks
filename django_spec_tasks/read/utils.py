from __future__ import annotations

import uuid

from django.db.models import QuerySet

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun


def runs_named(binding: TaskBinding, run_id: str) -> QuerySet[SpecRun]:
    """``binding``'s row under ``run_id``, or none for a malformed id."""
    try:
        key = uuid.UUID(str(run_id))
    except ValueError:
        return SpecRun.objects.none()
    return SpecRun.objects.filter(pk=key, binding=binding.name)
