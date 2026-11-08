from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class SweepReport:
    """What one sweep did, counted by outcome.

    ``reasked`` is a backend call and not a transition: a ``PENDING`` row the queue
    seems to have lost stays ``PENDING``. ``requeued``, ``abandoned`` and ``unavailable``
    are transitions. ``errors`` counts rows left as they were because a backend or an
    ``on_finish`` raised.
    """

    reasked: int = 0
    requeued: int = 0
    abandoned: int = 0
    unavailable: int = 0
    errors: int = 0

    @property
    def moved(self) -> int:
        """How many rows changed state."""
        return self.requeued + self.abandoned + self.unavailable
