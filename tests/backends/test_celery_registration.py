"""The trap this package's Celery task is built around, held in a process that knows nothing.

A Celery worker runs a message by looking its name up in its own registry. A task defined
inside ``enqueue`` is registered only in a process that has already called ``enqueue``, so it
passes every test that sends and runs in one process and is missing in every real worker.
Only a process that starts cold, as a worker does, can tell.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import django_spec_tasks
from tests.backends.utils import process_env

_PACKAGE = Path(django_spec_tasks.__file__).parent
_REPOSITORY = _PACKAGE.parent
_NAME = "django_spec_tasks.run_spec"

# What a worker does at startup: configure Django, and nothing else of this package's.
_COLD_START = f"""
import django
django.setup()
from celery import current_app
print({_NAME!r} in current_app.tasks)
"""


def _registered_in_a_cold_process(root: Path) -> bool:
    """Whether a process that only runs ``django.setup()`` finds the task, importing ``root``."""
    completed = subprocess.run(
        [sys.executable, "-c", _COLD_START],
        cwd=root,
        env=process_env(),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip() == "True"


def test_a_cold_process_that_only_calls_django_setup_finds_the_task_registered() -> None:
    assert _registered_in_a_cold_process(_REPOSITORY)


def test_the_probe_sees_a_task_built_inside_a_function_as_missing(tmp_path: Path) -> None:
    # The same copy of the package with the task moved inside a function, which is where a
    # task built "when it is needed" ends up: the module imports, a test that sends and runs
    # in one process passes, and a cold process finds nothing. Without this, the probe above
    # would pass whatever it was looking at.
    copy = tmp_path / "copy"
    shutil.copytree(
        _REPOSITORY,
        copy,
        ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", "docs", "*.egg-info"),
    )
    module = copy / "django_spec_tasks" / "backends" / "celery_backend.py"
    module.write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            from celery import shared_task


            def build() -> object:
                @shared_task(name="django_spec_tasks.run_spec", ignore_result=True)
                def _run_spec(run_id: str) -> None: ...

                return _run_spec
            """
        )
    )
    assert not _registered_in_a_cold_process(copy)
