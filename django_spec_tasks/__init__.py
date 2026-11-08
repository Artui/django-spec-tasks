"""Run a django-service-specs spec as a background task."""

import importlib

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.types.task_backend import TaskBackend
from django_spec_tasks.types.terminal_states import TERMINAL_STATES
from django_spec_tasks.version import __version__

# Served on first attribute access rather than imported here. Each of these reaches
# the model layer when its module is imported, and Django imports this package while
# it is still loading apps, before a model may be imported, so a plain import of one
# here makes the package unimportable with AppRegistryNotReady. A consumer sees the
# same names, and their static types come from __init__.pyi.
_LAZY_EXPORTS: dict[str, str] = {
    "SpecRun": "django_spec_tasks.models.spec_run",
}

__all__ = [
    "TERMINAL_STATES",
    "RunState",
    "SpecRun",
    "TaskBackend",
    "TaskBinding",
    "__version__",
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
