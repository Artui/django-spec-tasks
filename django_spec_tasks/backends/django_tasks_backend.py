from __future__ import annotations

try:
    from django.tasks import task
except ImportError:
    from django_tasks import task

from django_spec_tasks.worker.run_spec import run_spec


@task
def _run_spec(run_id: str) -> None:
    """Decorated at module level, since Django Tasks finds it again by its dotted path."""
    run_spec(run_id)


class DjangoTasksBackend:
    """Hands a run to Django's Tasks framework."""

    def __init__(self, alias: str = "default", queue: str | None = None) -> None:
        self.alias = alias
        self.queue = queue

    def enqueue(self, run_id: str) -> None:
        """Send the run to the aliased backend."""
        _run_spec.using(backend=self.alias, queue_name=self.queue).enqueue(run_id)
