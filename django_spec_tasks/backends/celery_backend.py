from __future__ import annotations

from celery import shared_task

from django_spec_tasks.worker.run_spec import run_spec


@shared_task(name="django_spec_tasks.run_spec", ignore_result=True)
def _run_spec(run_id: str) -> None:
    """The task a worker runs: hand the id to the package's worker."""
    run_spec(run_id)


class CeleryBackend:
    """Hands a run to a Celery worker."""

    def __init__(self, queue: str | None = None) -> None:
        self.queue = queue

    def enqueue(self, run_id: str) -> None:
        """Send the run, with its id as Celery's ``task_id`` so a log line names the run."""
        options = {} if self.queue is None else {"queue": self.queue}
        _run_spec.apply_async(args=[run_id], task_id=run_id, **options)
