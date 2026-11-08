from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from unittest import mock

import pytest
from celery import Celery, current_task
from celery.app.task import Task

from django_spec_tasks.backends import celery_backend
from django_spec_tasks.backends.celery_backend import CeleryBackend
from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.caller.enqueue import enqueue
from tests.caller.conftest import build_registry

NAME = "django_spec_tasks.run_spec"


@pytest.fixture
def app() -> Iterator[Celery]:
    """A real app, current for the test, running tasks where they are sent."""
    celery = Celery("tests", set_as_current=True)
    celery.conf.update(task_always_eager=True, task_eager_propagates=True)
    yield celery
    celery.close()


class TestTheTask:
    def test_the_name_is_spelled_out_and_not_derived_from_the_module(self, app: Celery) -> None:
        assert celery_backend._run_spec.name == NAME
        assert not NAME.endswith(celery_backend.__name__)

    def test_importing_the_module_alone_registers_the_name_in_an_apps_registry(
        self, app: Celery
    ) -> None:
        assert NAME in app.tasks

    def test_it_ignores_its_result(self, app: Celery) -> None:
        assert app.tasks[NAME].ignore_result is True

    def test_it_hands_the_id_to_the_worker_and_returns_none(
        self, app: Celery, run_spec: mock.Mock
    ) -> None:
        assert app.tasks[NAME]("run-1") is None
        run_spec.assert_called_once_with("run-1")


class TestTheBackend:
    def test_the_run_id_is_celery_s_task_id(self, app: Celery, run_spec: mock.Mock) -> None:
        seen: list[str] = []
        run_spec.side_effect = lambda run_id: seen.append(current_task.request.id)
        CeleryBackend().enqueue("0f6c3f0e-0000-4000-8000-000000000001")
        assert seen == ["0f6c3f0e-0000-4000-8000-000000000001"]
        run_spec.assert_called_once_with("0f6c3f0e-0000-4000-8000-000000000001")

    def test_no_queue_is_named_by_default(self, app: Celery) -> None:
        with mock.patch.object(Task, "apply_async") as send:
            CeleryBackend().enqueue("run-1")
        send.assert_called_once_with(args=["run-1"], task_id="run-1")

    def test_a_named_queue_is_sent_to(self, app: Celery) -> None:
        with mock.patch.object(Task, "apply_async") as send:
            CeleryBackend(queue="slow").enqueue("run-1")
        send.assert_called_once_with(args=["run-1"], task_id="run-1", queue="slow")

    @pytest.mark.django_db
    def test_a_run_reaches_the_worker_on_commit_under_a_real_app(
        self,
        app: Celery,
        run_spec: mock.Mock,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        bound = TaskBinding(name="celery-path", registry=build_registry(), backend=CeleryBackend())
        with django_capture_on_commit_callbacks(execute=True):
            run_id = enqueue(bound, "refund", {"order": "7"}, actor=None)
        run_spec.assert_called_once_with(run_id)
