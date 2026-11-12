from __future__ import annotations

from typing import Protocol


class TaskBackend(Protocol):
    """Where a run's code executes.

    The row is the record, so a backend may lose a message without consequence: the
    sweep asks again. It may also deliver one twice or late, and the claim stops the
    second copy. That is what keeps this a single method, and what lets one protocol
    hold Django Tasks and Celery.
    """

    def enqueue(self, run_id: str) -> None:
        """Arrange for ``run_spec(run_id)`` to be called somewhere."""
        ...
