"""Run a spec through each queue one install shape holds, with no dev group in the way.

``--extra`` names the shape: nothing, ``celery`` or ``django-tasks``. Every other job installs
both queues, so a module reaching for the queue that is absent passes all of them, and a run
through a backend exercises what importing it cannot. A broker and a worker process are out of
reach here, so each backend runs where it is sent: Celery eagerly, Tasks under its immediate
backend. What is proved is the path from ``enqueue`` to the row, on this install.

``--at-floors`` also fails unless every declared dependency this shape installs sits on its
floor, read from the installed package's own metadata, or the run proves nothing about them.

The imports are inside ``main`` because the package holds models, so it is imported the way a
project imports it: after ``django.setup()`` against configured settings.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import platform
import re
import sys
from collections.abc import Iterator
from typing import Any

import django
from django.conf import settings
from django.core.management import call_command

_REQUIREMENT = re.compile(r"""([\w.-]+)\s*>=\s*([\w.]+)(?:\s*;\s*extra\s*==\s*['"]([\w-]+)['"])?""")
_DISTRIBUTION = "django-spec-tasks"


def _double(*, value: int, **_: Any) -> dict[str, int]:
    return {"doubled": value * 2}


def _declared(extra: str) -> Iterator[tuple[str, str]]:
    """Each dependency this shape installs and its floor. A requirement of another form fails."""
    for requirement in importlib.metadata.requires(_DISTRIBUTION) or []:
        match = _REQUIREMENT.fullmatch(requirement)
        assert match is not None, f"cannot read {requirement!r}"
        name, floor, only_with = match.groups()
        if only_with in (None, extra):
            yield name, floor


def _release(version: str) -> tuple[int, ...]:
    """``version``'s numbers without trailing zeros, since 5.6 and 5.6.0 are one floor."""
    parts = [int(part) for part in version.split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra", choices=["", "celery", "django-tasks"], default="")
    parser.add_argument("--at-floors", action="store_true")
    arguments = parser.parse_args()
    extra: str = arguments.extra

    # The install really is the shape it claims, or this proves nothing.
    assert (importlib.util.find_spec("celery") is not None) == (extra == "celery")
    assert (importlib.util.find_spec("django_tasks") is not None) == (extra == "django-tasks")

    versions = {name: importlib.metadata.version(name) for name, _ in _declared(extra)}
    if arguments.at_floors:
        for name, floor in _declared(extra):
            assert _release(versions[name]) == _release(floor), (name, versions[name], floor)

    core = importlib.util.find_spec("django.tasks") is not None
    framework = "django.tasks" if core else "django_tasks" if extra == "django-tasks" else None
    # This shape exists for a Django below 6, where core has no Tasks framework. Above that core
    # answers and the backport never loads, so a resolver moving past it has to fail here.
    assert extra != "django-tasks" or framework == "django_tasks", "the backport would not load"

    tasks = (
        {"TASKS": {"default": {"BACKEND": f"{framework}.backends.immediate.ImmediateBackend"}}}
        if framework
        else {}
    )
    settings.configure(
        SECRET_KEY="x",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django_spec_tasks",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        **tasks,
    )
    django.setup()
    call_command("migrate", verbosity=0)

    from django_service_specs import Parameter, Parameters, ServiceSpec, SpecRegistry, Unrestricted

    from django_spec_tasks import RunState, SpecRun, TaskBinding, __version__, enqueue, run_spec

    # A queue is one of the transports that needs no DRF, and nothing here installs it.
    assert "rest_framework" not in sys.modules, "the package imported DRF"

    class _ProjectQueue:
        """The protocol's one method, which is the whole of what a project's own queue owes."""

        def enqueue(self, run_id: str) -> None:
            run_spec(run_id)

    backends: dict[str, Any] = {"own": _ProjectQueue()}
    if extra == "celery":
        from celery import Celery

        from django_spec_tasks.backends.celery_backend import CeleryBackend

        app = Celery("queue_shape", set_as_current=True)
        app.conf.update(task_always_eager=True, task_eager_propagates=True)
        # A worker finds a message's task by name in its own registry, so startup alone has to
        # have registered it.
        assert "django_spec_tasks.run_spec" in app.tasks, "task not registered"
        backends["celery"] = CeleryBackend()
    loaded = "absent"
    if framework:
        from django_spec_tasks.backends import django_tasks_backend

        loaded = django_tasks_backend.task.__module__
        assert loaded.startswith(f"{framework}."), (loaded, framework)
        backends["tasks"] = django_tasks_backend.DjangoTasksBackend()

    registry = SpecRegistry()
    registry.register(
        "double",
        ServiceSpec(
            service=_double,
            permissions=[Unrestricted()],
            reads=Parameters.of(Parameter("value", "integer", required=True)),
        ),
    )
    for name, backend in backends.items():
        binding = TaskBinding(name=name, registry=registry, backend=backend)
        run = SpecRun.objects.get(pk=enqueue(binding, "double", {"value": 21}, actor=None))
        assert run.state == RunState.SUCCEEDED, (name, run.state, run.traceback)
        assert run.output == {"doubled": 42}, (name, run.output)

    print(
        f"SHAPE OK extra={extra!r} package={__version__} python={platform.python_version()} "
        f"versions={versions} task_framework={loaded} ran={sorted(backends)}"
    )


if __name__ == "__main__":
    main()
