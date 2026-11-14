from __future__ import annotations

import logging
import traceback
from contextlib import nullcontext
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from django_service_specs import (
    DispatchResult,
    PrincipalUnavailable,
    ServiceNotFound,
    ServiceSpec,
    build_call_context,
    dispatch,
    kernel_spec,
    present,
)

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.models.storable_json import storable_json
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.version import __version__
from django_spec_tasks.worker.row_progress import RowProgress
from django_spec_tasks.worker.run_state_for import run_state_for

logger = logging.getLogger(__name__)


def run_spec(run_id: str) -> None:
    """Run the row ``run_id`` names, once, whatever number of messages name it.

    Both backends call this. It returns nothing, and no retry policy of a queue's applies:
    whether a run may run again is read from the row, by the sweep.

    A message for a row that is not ``PENDING`` costs one query and runs nothing. Otherwise
    the row is claimed, the principal resolved, and the spec called through the kernel's
    ``dispatch`` and ``present``, so the worker re-checks everything enqueue checked and the
    Validator runs for the first time. The row ends in the state
    [`run_state_for`][django_spec_tasks.worker.run_state_for.run_state_for] gives what was
    raised, or in ``SUCCEEDED`` with what was presented, or in ``OUTPUT_TOO_LARGE``. A service
    whose target is gone never ran, and ends ``REFUSED``; a selector finding nothing has
    answered, and ends ``SUCCEEDED``, each with ``kind="not_found"``.

    **Under** ``atomic=True`` one transaction holds ``around_run``, ``dispatch``, ``present``,
    the terminal write and ``on_finish``, so the service's work and the row's state commit as
    one fact, and a worker that dies before the commit has left nothing but a ``RUNNING``
    row. A refusal rolls that transaction back first, then writes its state and runs
    ``on_finish`` in a transaction of its own. **Under** ``atomic=False``, and for a selector,
    no transaction surrounds the call, and only the write and ``on_finish`` share one.

    A write finding the row is no longer this attempt's writes nothing and rolls its own
    transaction back: a worker that lost its claim cannot commit.

    Only an ``Exception`` is an ending. A ``BaseException`` is the worker dying, and passes
    through untouched, as does anything ``on_finish`` raises: both leave the row ``RUNNING``.
    The sweep answers a dead worker's. It calls the same hook, so a hook that always raises
    keeps its row ``RUNNING`` until it stops raising.
    """
    run = SpecRun.objects.filter(pk=run_id, state=RunState.PENDING).first()
    if run is None:
        return
    binding = TaskBinding.named(run.binding)
    if binding is None:
        _end(run, None, _unavailable(f"No binding named {run.binding!r} is registered."))
        return
    entry = binding.registry.get(run.spec_name)
    if entry is None:
        _end(run, binding, _unavailable(f"No spec named {run.spec_name!r} on this binding."))
        return
    if _claim(run, binding):
        _run_claimed(run, binding, kernel_spec(entry.spec))


def _claim(run: SpecRun, binding: TaskBinding) -> bool:
    """Take the row for this attempt with one conditional ``UPDATE``, never a read then a save."""
    now = timezone.now()
    claim = {
        "state": RunState.RUNNING,
        "attempts": run.attempts + 1,
        "claimed_at": now,
        "lease_expires_at": now + binding.lease,
        "progress": None,
        "progress_message": None,
    }
    taken = SpecRun.objects.filter(pk=run.pk, state=RunState.PENDING, attempts=run.attempts).update(
        **claim
    )
    if not taken:
        return False
    for column, value in claim.items():
        setattr(run, column, value)
    return True


def _run_claimed(run: SpecRun, binding: TaskBinding, spec: Any) -> None:
    """The claimed row's call and its ending, under the spec's transaction rules."""
    try:
        principal = None if run.actor_pk is None else binding.resolve_actor(run.actor_pk)
    except Exception as error:
        _end(run, binding, _ending_for(error))
        return
    if isinstance(spec, ServiceSpec) and spec.atomic:
        with transaction.atomic():
            ending = _call(run, binding, spec, principal)
            if ending["state"] == RunState.SUCCEEDED:
                _record(run, binding, ending)
                return
            transaction.set_rollback(True)
    else:
        ending = _call(run, binding, spec, principal)
    _end(run, binding, ending)


def _call(run: SpecRun, binding: TaskBinding, spec: Any, principal: Any) -> dict[str, Any]:
    """Catches every ``Exception`` from the call, and nothing past it."""
    finished: tuple[DispatchResult, Any] | None = None
    try:
        seam = binding.around_run(run, principal) if binding.around_run else nullcontext()
        with seam:
            result = dispatch(
                spec,
                principal=principal,
                arguments=run.params,
                pool_seeds=binding.pool_seeds,
                progress=RowProgress(run.pk, run.attempts, using=binding.progress_using),
                call_context=build_call_context("task", base={"carried": run.carried}),
            )
            finished = (result, None if result.kind == "not_found" else present(spec, result))
        if finished is None:
            raise RuntimeError(
                "around_run swallowed what the run raised, so there is no result to record."
            )
        return _ending_of(binding, spec, *finished)
    except Exception as error:
        return _ending_for(error)


def _ending_of(
    binding: TaskBinding, spec: Any, result: DispatchResult, output: Any
) -> dict[str, Any]:
    """The columns of a call that returned: its output, or the refusal to store it."""
    if result.kind == "not_found" and isinstance(spec, ServiceSpec):
        return {**_ending_for(ServiceNotFound()), "kind": result.kind}
    size = len(_encoded(output).encode())
    if size > binding.max_output_bytes:
        return {
            "state": RunState.OUTPUT_TOO_LARGE,
            "refusal_detail": {"size": size, "limit": binding.max_output_bytes},
        }
    pk = getattr(result.instance, "pk", None)
    return {
        "state": RunState.SUCCEEDED,
        "kind": result.kind,
        "output": output,
        "instance_pk": None if pk is None else str(pk),
    }


def _ending_for(error: Exception) -> dict[str, Any]:
    """A fault's words stay in ``traceback``, never in ``refusal_detail``."""
    state = run_state_for(error)
    if state == RunState.FAILED:
        return {"state": state, "traceback": _storable("".join(traceback.format_exception(error)))}
    code = getattr(error, "code", None)
    return {
        "state": state,
        "refusal_class": type(error).__name__,
        "refusal_code": None if code is None else _storable(str(code)),
        "refusal_detail": _refusal_detail(error),
    }


def _refusal_detail(error: Exception) -> dict[str, Any]:
    """A detail no column could store must not turn a refusal into a write that raises."""
    if isinstance(error, PrincipalUnavailable):
        message = str(error.default_message)
    else:
        message = _storable(str(error))
    detail = {
        "message": message,
        "errors": getattr(error, "detail", None),
        "schema": getattr(error, "schema", None),
    }
    try:
        _encoded(detail)
    except (TypeError, ValueError):
        return {"message": message, "errors": None, "schema": None}
    return detail


def _encoded(value: Any) -> str:
    """``value`` as the JSON columns write it, refusing what they could not store."""
    return storable_json(value, encoder=DjangoJSONEncoder)


def _storable(text: str) -> str:
    """``text`` as every text column holds it, without a NUL or half a surrogate pair."""
    return text.replace("\x00", "").encode(errors="replace").decode()


def _unavailable(why: str) -> dict[str, Any]:
    """The columns of a row nothing here can run."""
    return {"state": RunState.SPEC_UNAVAILABLE, "refusal_detail": {"message": why}}


def _end(run: SpecRun, binding: TaskBinding | None, ending: dict[str, Any]) -> None:
    """Record ``ending`` and fire what it fires, in a transaction of their own."""
    with transaction.atomic():
        _record(run, binding, ending)


def _record(run: SpecRun, binding: TaskBinding | None, ending: dict[str, Any]) -> None:
    """The terminal write and ``on_finish``, inside a transaction the caller holds."""
    written = SpecRun.objects.filter(pk=run.pk, state=run.state, attempts=run.attempts).update(
        **ending, package_version=__version__, finished_at=timezone.now()
    )
    if not written:
        logger.warning(
            "run %s is no longer attempt %s's to finish, so its ending was not recorded and "
            "its transaction rolled back. Raise lease= on binding %r if it runs this long",
            run.pk,
            run.attempts,
            run.binding,
        )
        transaction.set_rollback(True)
        return
    if binding is not None and binding.on_finish is not None:
        run.refresh_from_db()
        binding.on_finish(run)
