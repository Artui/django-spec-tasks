"""The Celery backend. ``apps.py`` imports this module whenever ``celery`` is installed."""

from __future__ import annotations

from celery import shared_task

from django_spec_tasks.worker.run_spec import run_spec


@shared_task(name="django_spec_tasks.run_spec", ignore_result=True)
def _run_spec(run_id: str) -> None:
    """The task a worker runs: hand the id to the package's worker.

    Defined here at the top of the module, never inside ``enqueue``: a Celery worker finds a
    message's task by looking its name up in its own registry, so the task exists there only
    if importing this module registered it, and ``apps.py`` does that at startup. The name
    is spelled out because messages already on a broker carry it, and a name derived from
    this module's path would orphan them the day the module moved.

    Returns ``None`` and ignores its result: the outcome is on the run's row.
    """
    run_spec(run_id)


class CeleryBackend:
    """Hands a run to a Celery worker.

    ``queue`` names the queue to send to; ``None`` leaves routing to the project's
    ``task_routes``, keyed by the stable task name. The package sets no retry, no late
    acknowledgement, no result backend and no schedule, each because the row already
    answers it: a second copy of a message finds the claim taken, a lost one is asked
    for again by the sweep, and the result is on the row.
    """

    def __init__(self, queue: str | None = None) -> None:
        self.queue = queue

    def enqueue(self, run_id: str) -> None:
        """Send the run, with its id as Celery's ``task_id`` so a log line names the run."""
        options = {} if self.queue is None else {"queue": self.queue}
        _run_spec.apply_async(args=[run_id], task_id=run_id, **options)
