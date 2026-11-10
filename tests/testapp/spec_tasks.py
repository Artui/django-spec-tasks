from __future__ import annotations

from django_service_specs import SpecRegistry

from django_spec_tasks.binding.task_binding import TaskBinding


class _NoQueue:
    def enqueue(self, run_id: str) -> None: ...


discovered = TaskBinding(name="discovered", registry=SpecRegistry(), backend=_NoQueue())
