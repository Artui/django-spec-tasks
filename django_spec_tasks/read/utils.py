from __future__ import annotations

import uuid

from django.db.models import QuerySet

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun


def runs_named(binding: TaskBinding, run_id: str) -> QuerySet[SpecRun]:
    """``binding``'s row under ``run_id``. A malformed id names none, and costs no query.

    ``test_no_refusal_tells_a_stranger_from_an_id_that_is_nothing`` fails without each part:
    its ``unknown`` case without ``pk``, its ``other_binding`` case without ``binding``, and its
    ``malformed`` case without the empty answer. ``test_a_malformed_id_costs_no_query`` holds
    the cost.
    """
    try:
        key = uuid.UUID(str(run_id))
    except ValueError:
        return SpecRun.objects.none()
    return SpecRun.objects.filter(pk=key, binding=binding.name)
