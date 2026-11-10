from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from django.db import router, transaction
from django_service_specs import (
    Parameters,
    build_call_context,
    check_call,
    kernel_spec,
)

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.models.storable_json import storable_json
from django_spec_tasks.types.run_state import RunState


def enqueue(
    binding: TaskBinding,
    spec_name: str,
    params: Mapping[str, Any],
    *,
    actor: Any,
) -> str:
    """Record a run of ``spec_name`` and ask the binding's backend to execute it on commit."""
    registered = binding.registry.get(spec_name)
    if registered is None:
        raise LookupError(f"Binding {binding.name!r} has no spec named {spec_name!r}.")
    spec = registered.spec

    for path in _file_parameters(kernel_spec(spec).parameters()):
        raise TypeError(
            f"Spec {spec_name!r} takes a file parameter, {path!r}, which cannot cross to a "
            "worker. Save the upload to storage and declare a parameter holding its key."
        )

    if actor is not None and getattr(actor, "pk", None) is None:
        raise TypeError(
            f"actor {actor!r} has no primary key, so the worker could not load it. Pass "
            "actor=None for a system run; an unauthenticated call is not one."
        )

    carried = dict(binding.carry(actor)) if binding.carry is not None else {}
    _require_json_round_trip(carried, "carried value")

    check_call(
        spec,
        principal=actor,
        arguments=params,
        pool_seeds=binding.pool_seeds,
        call_context=build_call_context("task", base={"carried": carried}),
    )

    _require_json_round_trip(params, "parameter")

    alias = router.db_for_write(SpecRun)
    run = SpecRun.objects.using(alias).create(
        binding=binding.name,
        spec_name=spec_name,
        params=dict(params),
        actor_pk=None if actor is None else str(actor.pk),
        carried=carried,
        state=RunState.PENDING,
    )
    run_id = str(run.pk)
    transaction.on_commit(lambda: binding.backend.enqueue(run_id), using=alias, robust=True)
    return run_id


def _file_parameters(parameters: Parameters, prefix: str = "") -> Iterator[str]:
    """The dotted path of every ``file`` parameter, however deeply it is nested."""
    for parameter in parameters:
        path = f"{prefix}{parameter.name}"
        if parameter.type == "file" or parameter.items == "file":
            yield path
        for nested in (parameter.items, parameter.fields):
            if isinstance(nested, Parameters):
                yield from _file_parameters(nested, f"{path}.")


def _require_json_round_trip(values: Mapping[str, Any], label: str) -> None:
    """Refuse a value that JSON would change or cannot carry, naming its key."""
    for key, value in values.items():
        if not isinstance(key, str):
            raise TypeError(f"The {label} key {key!r} is not a string, and JSON keys are.")
        try:
            storable_json(key)
            restored = json.loads(storable_json(value))
        except (TypeError, ValueError) as error:
            raise TypeError(f"The {label} {key!r} is not JSON: {error}") from error
        if restored != value:
            raise TypeError(
                f"The {label} {key!r} changes on its way through JSON ({value!r} comes back "
                f"as {restored!r}). Pass the form a JSON body would carry."
            )
