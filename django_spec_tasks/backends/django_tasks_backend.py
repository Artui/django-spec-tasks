"""The Django Tasks backend: ``django.tasks`` from 6.0, the ``django-tasks`` backport below."""

from __future__ import annotations

try:
    from django.tasks import task
except ImportError:
    from django_tasks import task

from django_spec_tasks.worker.run_spec import run_spec


@task
def _run_spec(run_id: str) -> None:
    """The task body.

    Decorated at the top of the module because Django Tasks finds a task again by its dotted
    path, which has to resolve to the ``Task`` itself: a worker reading a stored result, or
    a ``db_worker`` picking up a row, imports ``module._run_spec`` and refuses a plain
    function there. Nested in a method it would not be importable at all. Returns ``None``,
    since a returned frozen dataclass fails the task after the work has committed, and the
    outcome is on the run's row.
    """
    run_spec(run_id)


class DjangoTasksBackend:
    """Hands a run to Django's Tasks framework.

    Name the backend in the binding; it is never chosen by what imports. ``django.tasks``
    imports on any Django 6, and its default backend runs a task inline on the caller's
    thread, so choosing by availability would pick that in a project running Celery.

    ``alias`` is the ``TASKS`` entry to send to, ``queue`` the queue on it; ``None`` leaves
    the framework's default. The module's task is built against the ``default`` entry, as
    any ``@task`` is, so ``TASKS`` needs one.
    """

    def __init__(self, alias: str = "default", queue: str | None = None) -> None:
        self.alias = alias
        self.queue = queue

    def enqueue(self, run_id: str) -> None:
        """Send the run to the aliased backend."""
        _run_spec.using(backend=self.alias, queue_name=self.queue).enqueue(run_id)
