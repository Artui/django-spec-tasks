from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class SweepReport:
    """What one sweep did, counted by outcome."""

    reasked: int = 0
    requeued: int = 0
    abandoned: int = 0
    unavailable: int = 0
    errors: int = 0

    @property
    def moved(self) -> int:
        """How many rows changed state."""
        return self.requeued + self.abandoned + self.unavailable
