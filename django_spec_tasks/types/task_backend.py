from __future__ import annotations

from typing import Protocol


class TaskBackend(Protocol):
    """Where a run's code executes."""

    def enqueue(self, run_id: str) -> None:
        """Arrange for ``run_spec(run_id)`` to be called somewhere."""
        ...
