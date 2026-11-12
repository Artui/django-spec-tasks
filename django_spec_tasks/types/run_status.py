from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django_spec_tasks.types.run_state import RunState


@dataclass(frozen=True, kw_only=True)
class RunStatus:
    """What a reader of a run is shown: how it went, and none of what it was asked.

    ``params``, ``carried``, ``traceback``, ``actor_pk`` and ``refusal_class`` are left
    out on purpose. The call's arguments are as sensitive as the call, a traceback names
    code, and the owner is who is asking.
    """

    id: str
    state: RunState
    kind: str | None
    output: Any
    progress: float | None
    progress_message: str | None
    refusal_code: str | None
    refusal_detail: Any
    created_at: datetime
    finished_at: datetime | None
