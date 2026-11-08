from __future__ import annotations

from django_spec_tasks.types.run_state import RunState

TERMINAL_STATES: frozenset[RunState] = frozenset(RunState) - {RunState.PENDING, RunState.RUNNING}
"""Every state a run does not leave. A run in one is what pruning may delete."""
