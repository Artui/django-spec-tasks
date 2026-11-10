from __future__ import annotations

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.read.utils import runs_named
from django_spec_tasks.types.run_state import RunState


def state_of(binding: TaskBinding, run_id: str) -> RunState | None:
    """The state of one of ``binding``'s runs, with no principal asked."""
    found = runs_named(binding, run_id).values_list("state", flat=True).first()
    return None if found is None else RunState(found)
