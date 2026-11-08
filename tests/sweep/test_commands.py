"""The two commands: what they print, what they require, what a limit bounds."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from tests.sweep.conftest import RecordingBackend, ago

pytestmark = pytest.mark.django_db

MakeBinding = Callable[..., TaskBinding]
MakeRun = Callable[..., SpecRun]


def _run(command: str, *args: str) -> str:
    out = StringIO()
    call_command(command, *args, stdout=out)
    return out.getvalue()


def test_the_sweep_command_says_how_many_rows_moved_to_where(
    make_binding: MakeBinding, lapsed: MakeRun, make_run: MakeRun, backend: RecordingBackend
) -> None:
    binding = make_binding(idempotent=True)
    lapsed(binding, attempts=1)
    lapsed(binding, attempts=3)
    lapsed(binding, attempts=3)
    lapsed(binding, spec_name="removed_since")
    make_run(binding, created_at=ago(hours=1))

    output = _run("sweep_spec_runs")

    assert "1 requeued" in output
    assert "2 abandoned" in output
    assert "1 spec unavailable" in output
    assert "1 asked again" in output
    assert "0 errors" in output


def test_the_sweep_command_limit_bounds_the_sweep(
    make_binding: MakeBinding, make_run: MakeRun, backend: RecordingBackend
) -> None:
    binding = make_binding()
    for hours in range(1, 5):
        make_run(binding, created_at=ago(hours=hours))

    output = _run("sweep_spec_runs", "--limit", "2")

    assert "2 asked again" in output
    assert len(backend.asked) == 2


def test_the_prune_command_requires_an_age() -> None:
    with pytest.raises(CommandError, match="older-than"):
        call_command("prune_spec_runs")


def test_the_prune_command_deletes_finished_rows_past_the_age_in_days(
    make_run: MakeRun,
) -> None:
    make_run("b", state=RunState.SUCCEEDED, finished_at=ago(days=31))
    make_run("b", state=RunState.SUCCEEDED, finished_at=ago(days=29))
    live = make_run("b", state=RunState.RUNNING, finished_at=ago(days=90))

    output = _run("prune_spec_runs", "--older-than", "30")

    assert "1 run" in output
    assert SpecRun.objects.count() == 2
    assert SpecRun.objects.filter(pk=live.pk).exists()


def test_the_prune_command_refuses_a_negative_age() -> None:
    with pytest.raises(CommandError, match="negative"):
        call_command("prune_spec_runs", "--older-than", "-1")
