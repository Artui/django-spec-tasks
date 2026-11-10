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
    """A registry served as background tasks, with everything a queue needs beside it."""

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
        """The binding registered under ``name``, or ``None``."""
        return TaskBinding._registered.get(name)

    @classmethod
    def clear_registry(cls) -> None:
        """Forget every binding: the one way a test suite empties the registry."""
        TaskBinding._registered.clear()
