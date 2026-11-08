from __future__ import annotations

import uuid
from typing import Any

from django_service_specs import NotPermitted, is_deactivated

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.types.run_status import RunStatus


def read_run(binding: TaskBinding, run_id: str, principal: Any) -> RunStatus:
    """What ``principal`` may see of a run: its status, never what it was asked.

    The call's ``params`` and its presented output are as sensitive as the call itself, so
    the default reader is the run's owner. ``binding.can_read(principal, run)`` widens
    that. A principal that is deactivated is refused either way: it never acts.

    A run that is not this principal's, an id nobody wrote, a malformed id and another
    binding's run all raise the same ``NotPermitted`` with the same message, so an id
    cannot be probed for whether it exists. A system run has no owner, so only
    ``can_read`` reads it.

    Raises:
        NotPermitted: For every refusal above.
    """
    run = _find(binding, run_id)
    if run is None or not _may_read(binding, run, principal):
        raise NotPermitted()
    return RunStatus(
        id=str(run.pk),
        state=RunState(run.state),
        kind=run.kind,
        output=run.output,
        progress=run.progress,
        progress_message=run.progress_message,
        refusal_code=run.refusal_code,
        refusal_detail=run.refusal_detail,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _find(binding: TaskBinding, run_id: str) -> SpecRun | None:
    try:
        key = uuid.UUID(str(run_id))
    except ValueError:
        return None
    return SpecRun.objects.filter(pk=key, binding=binding.name).first()


def _may_read(binding: TaskBinding, run: SpecRun, principal: Any) -> bool:
    if is_deactivated(principal):
        return False
    pk = getattr(principal, "pk", None)
    # An anonymous principal has no pk, and ``str(None)`` is a string a row could hold: the
    # guard is what keeps it from reading as the owner of anything.
    if pk is not None and run.actor_pk == str(pk):
        return True
    return binding.can_read is not None and bool(binding.can_read(principal, run))
