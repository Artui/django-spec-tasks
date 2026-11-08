from __future__ import annotations

import copy
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import pytest
from django.utils.module_loading import import_string
from django_service_specs import SpecRegistry

from django_spec_tasks.backends import django_tasks_backend
from django_spec_tasks.backends.django_tasks_backend import DjangoTasksBackend
from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.enqueue.enqueue import enqueue
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from tests.enqueue.conftest import build_registry

_CORE = importlib.util.find_spec("django.tasks") is not None
_FRAMEWORK = "django.tasks" if _CORE else "django_tasks"
_DUMMY = f"{_FRAMEWORK}.backends.dummy.DummyBackend"
_IMMEDIATE = f"{_FRAMEWORK}.backends.immediate.ImmediateBackend"
_SOURCE = Path(django_tasks_backend.__file__)


def _load_copy(name: str) -> ModuleType:
    """Execute the module's source again under ``name``, so its import runs a second time."""
    spec = importlib.util.spec_from_file_location(name, _SOURCE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheTwoPathImport:
    def test_the_core_framework_is_preferred_when_it_imports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        core = ModuleType("django.tasks")
        core.task = lambda function: ("core", function)  # ty: ignore[unresolved-attribute]
        monkeypatch.setitem(sys.modules, "django.tasks", core)
        assert _load_copy("core_path")._run_spec[0] == "core"

    def test_the_backport_answers_where_core_has_no_tasks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backport = ModuleType("django_tasks")
        backport.task = lambda function: ("backport", function)  # ty: ignore[unresolved-attribute]
        monkeypatch.setitem(sys.modules, "django.tasks", None)
        monkeypatch.setitem(sys.modules, "django_tasks", backport)
        assert _load_copy("backport_path")._run_spec[0] == "backport"


class TestTheTaskBody:
    def test_it_resolves_by_its_dotted_path_to_the_task_itself(self) -> None:
        # A worker imports this path and refuses anything that is not a Task, which a plain
        # function is; a nested function has no path at all.
        task = django_tasks_backend._run_spec
        assert import_string(task.module_path) is task
        assert task.name == "_run_spec"

    def test_it_hands_the_id_to_the_worker_and_returns_none(self, run_spec: mock.Mock) -> None:
        assert django_tasks_backend._run_spec.call("run-1") is None
        run_spec.assert_called_once_with("run-1")


class TestTheDummyBackend:
    def test_a_run_goes_to_the_default_alias_and_queue(self, tasks: Callable[..., Any]) -> None:
        handler = tasks(default=_DUMMY)
        DjangoTasksBackend().enqueue("run-1")
        (result,) = handler["default"].results
        assert result.args == ["run-1"]
        assert result.task.queue_name == "default"
        assert result.task.func is django_tasks_backend._run_spec.func

    def test_a_stored_result_can_be_copied_and_found_again_by_its_path(
        self, tasks: Callable[..., Any]
    ) -> None:
        # What a database worker does with a row: rebuild the task from its path. Rebuilding
        # fails when the path names a function and not a Task.
        handler = tasks(default=_DUMMY)
        DjangoTasksBackend().enqueue("run-1")
        copied = copy.deepcopy(handler["default"].results[0])
        assert copied.task.func is django_tasks_backend._run_spec.func

    def test_a_named_queue_is_passed_on(self, tasks: Callable[..., Any]) -> None:
        handler = tasks(default={"BACKEND": _DUMMY, "QUEUES": ["default", "slow"]})
        DjangoTasksBackend(queue="slow").enqueue("run-1")
        assert handler["default"].results[0].task.queue_name == "slow"

    def test_a_named_alias_is_the_one_asked(self, tasks: Callable[..., Any]) -> None:
        handler = tasks(default=_DUMMY, elsewhere=_DUMMY)
        DjangoTasksBackend(alias="elsewhere").enqueue("run-1")
        assert handler["default"].results == []
        assert len(handler["elsewhere"].results) == 1

    def test_the_arguments_are_kept_as_given(self) -> None:
        backend = DjangoTasksBackend(alias="a", queue="q")
        assert (backend.alias, backend.queue) == ("a", "q")
        assert (DjangoTasksBackend().alias, DjangoTasksBackend().queue) == ("default", None)


class TestEndToEnd:
    @pytest.mark.django_db
    def test_a_run_reaches_the_worker_on_commit_under_the_immediate_backend(
        self,
        tasks: Callable[..., Any],
        run_spec: mock.Mock,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        tasks(default=_IMMEDIATE)
        bound = TaskBinding(
            name="immediate",
            registry=build_registry(),
            backend=DjangoTasksBackend(),
        )
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            run_id = enqueue(bound, "refund", {"order": "7"}, actor=None)
        run_spec.assert_not_called()
        callbacks[0]()
        run_spec.assert_called_once_with(run_id)

    @pytest.mark.django_db
    def test_the_whole_path_runs_the_service_and_records_the_outcome(
        self,
        tasks: Callable[..., Any],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        pytest.importorskip("django_spec_tasks.worker.run_spec")
        from tests.testapp.models import Note

        tasks(default=_IMMEDIATE)
        bound = TaskBinding(
            name="whole-path",
            registry=build_registry(),
            backend=DjangoTasksBackend(),
        )
        with django_capture_on_commit_callbacks(execute=True):
            run_id = enqueue(bound, "refund", {"order": "7"}, actor=None)
        assert SpecRun.objects.get(pk=run_id).state == RunState.SUCCEEDED
        assert list(Note.objects.values_list("text", flat=True)) == ["refunded 7"]


def test_a_binding_over_no_specs_is_still_a_binding() -> None:
    # The backend names no registry: it is handed an id and nothing else.
    assert TaskBinding(
        name="no-specs", registry=SpecRegistry(), backend=DjangoTasksBackend()
    ).backend
