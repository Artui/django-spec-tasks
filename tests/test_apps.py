from __future__ import annotations

import sys
from unittest import mock

import pytest
from django.apps import apps

from django_spec_tasks import apps as apps_module
from django_spec_tasks.binding.task_binding import TaskBinding
from tests.testapp.spec_tasks import discovered

_CELERY_BACKEND = "django_spec_tasks.backends.celery_backend"


def _ready() -> None:
    apps.get_app_config("django_spec_tasks").ready()


def test_ready_finds_a_binding_in_an_installed_app_s_spec_tasks_module() -> None:
    assert TaskBinding.named("discovered") is discovered


def test_ready_discovers_the_spec_tasks_module_by_that_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discover = mock.Mock()
    monkeypatch.setattr(apps_module, "autodiscover_modules", discover)
    _ready()
    discover.assert_called_once_with("spec_tasks")


def test_ready_imports_the_celery_backend_when_celery_is_installed() -> None:
    # The dev group installs celery, and setup has already run.
    assert _CELERY_BACKEND in sys.modules


def test_ready_asks_for_the_celery_backend_when_celery_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer = mock.Mock()
    monkeypatch.setattr(apps_module, "find_spec", lambda name: object())
    monkeypatch.setattr(apps_module, "import_module", importer)
    _ready()
    importer.assert_called_once_with(_CELERY_BACKEND)


def test_ready_leaves_the_celery_backend_alone_when_celery_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer = mock.Mock()
    monkeypatch.setattr(apps_module, "find_spec", lambda name: None)
    monkeypatch.setattr(apps_module, "import_module", importer)
    _ready()
    importer.assert_not_called()


def test_a_broken_celery_backend_is_not_read_as_an_absent_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(apps_module, "find_spec", lambda name: object())
    monkeypatch.setattr(apps_module, "import_module", mock.Mock(side_effect=ImportError("broken")))
    with pytest.raises(ImportError, match="broken"):
        _ready()
