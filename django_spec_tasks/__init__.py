"""Run a django-service-specs spec as a background task."""

import importlib

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.types.run_status import RunStatus
from django_spec_tasks.types.sweep_report import SweepReport
from django_spec_tasks.types.task_backend import TaskBackend
from django_spec_tasks.types.terminal_states import TERMINAL_STATES
from django_spec_tasks.version import __version__
from django_spec_tasks.worker.run_state_for import run_state_for

# Served on first attribute access rather than imported here. Each of these reaches
# the model layer when its module is imported, and Django imports this package while
# it is still loading apps, before a model may be imported, so a plain import of one
# here makes the package unimportable with AppRegistryNotReady. A consumer sees the
# same names, and their static types come from __init__.pyi.
_LAZY_EXPORTS: dict[str, str] = {
    "SpecRun": "django_spec_tasks.models.spec_run",
    "enqueue": "django_spec_tasks.caller.enqueue",
    "prune_runs": "django_spec_tasks.sweep.prune_runs",
    "read_run": "django_spec_tasks.read.read_run",
    "run_spec": "django_spec_tasks.worker.run_spec",
    "state_of": "django_spec_tasks.read.state_of",
    "sweep_runs": "django_spec_tasks.sweep.sweep_runs",
}

__all__ = [
    "TERMINAL_STATES",
    "RunState",
    "RunStatus",
    "SpecRun",
    "SweepReport",
    "TaskBackend",
    "TaskBinding",
    "__version__",
    "enqueue",
    "prune_runs",
    "read_run",
    "run_spec",
    "run_state_for",
    "state_of",
    "sweep_runs",
]


def __getattr__(name: str) -> object:
    """Resolve a lazily exported name from its leaf module, and cache the result."""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List every public name, lazy ones included, so tab completion sees them."""
    return sorted(set(globals()) | set(__all__))
