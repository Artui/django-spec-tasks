from __future__ import annotations

import uuid

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState


def state_of(binding: TaskBinding, run_id: str) -> RunState | None:
    """The state of one of ``binding``'s runs, with no principal asked.

    For a caller that is trusted, such as a queue reporting on a run it enqueued. It
    answers ``None`` for an id that is not this binding's, or not an id at all. Anything
    a person may reach goes through ``read_run``.
    """
    try:
        key = uuid.UUID(str(run_id))
    except ValueError:
        return None
    state = SpecRun.objects.filter(pk=key, binding=binding.name).values_list("state", flat=True)
    found = state.first()
    return None if found is None else RunState(found)
