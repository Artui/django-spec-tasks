from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar

from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds, SpecRegistry, resolve_principal

from django_spec_tasks.types.task_backend import TaskBackend


@dataclass(frozen=True, kw_only=True, eq=False)
class TaskBinding:
    """A registry served as background tasks, with everything a queue needs beside it.

    Collaborators are objects passed here, never dotted paths in settings. A seam
    that takes a run is handed the ``SpecRun`` row, typed ``Any`` because nothing here
    imports a model: that is what keeps this class importable before the app registry
    is ready.

    Constructing one registers it under its name, since a worker has only the name a
    row recorded. Autodiscovery of each installed app's ``spec_tasks`` module is what
    makes every binding known in a worker that imported Django.

    Attributes:
        name: Recorded on every row, so it must not change while runs are pending.
        registry: Where enqueue and the worker find a spec by name.
        backend: Where a run's code executes.
        resolve_actor: Turns a row's ``actor_pk`` into the principal. Raises
            ``PrincipalUnavailable`` for anyone who may no longer act.
        pool_seeds: The same set at enqueue and in the worker, so the prelude's seed
            check agrees between them.
        lease: How long a claimed run may take. Sized to the binding's longest run: a
            worker past it is refused at its terminal write.
        grace: How long a ``PENDING`` row waits before the sweep asks the backend again.
        max_attempts: How many claims an idempotent run gets before it is abandoned.
        max_output_bytes: Past this, a run ends ``OUTPUT_TOO_LARGE``. Output is never
            truncated.
        can_read: ``(principal, run) -> bool``, consulted beside the run's owner.
        progress_using: A database alias progress is written through, which is what
            makes it visible during an ``atomic=True`` run.
        carry: ``(actor) -> JSON mapping``, recorded on the row at enqueue and handed
            to the call context in both processes.
        around_run: ``(run, principal) -> context manager`` around ``dispatch`` and
            ``present``.
        on_finish: ``(run) -> None``, inside the transaction of the terminal write.
    """

    name: str
    registry: SpecRegistry
    backend: TaskBackend
    resolve_actor: Callable[[str], Any] = resolve_principal
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    lease: timedelta = timedelta(hours=1)
    grace: timedelta = timedelta(minutes=10)
    max_attempts: int = 3
    max_output_bytes: int = 1_048_576
    can_read: Callable[[Any, Any], bool] | None = None
    progress_using: str | None = None
    carry: Callable[[Any], Mapping[str, Any]] | None = None
    around_run: Callable[[Any, Any], AbstractContextManager[Any]] | None = None
    on_finish: Callable[[Any], None] | None = None

    # On the class rather than in a module of its own: a module holding bindings
    # would have to import this class, and this class has to reach it to register.
    _registered: ClassVar[dict[str, TaskBinding]] = {}

    def __post_init__(self) -> None:
        if self.name in TaskBinding._registered:
            raise ValueError(
                f"A TaskBinding named {self.name!r} is already registered. A row records "
                "a binding by name, so two of one name could not be told apart."
            )
        TaskBinding._registered[self.name] = self

    @classmethod
    def named(cls, name: str) -> TaskBinding | None:
        """The binding registered under ``name``, or ``None``.

        A row naming no binding is a rolling deploy rather than a bug, so an unknown
        name is an answer here and not an exception.
        """
        return TaskBinding._registered.get(name)

    @classmethod
    def clear_registry(cls) -> None:
        """Forget every binding: the one way a test suite empties the registry."""
        TaskBinding._registered.clear()
