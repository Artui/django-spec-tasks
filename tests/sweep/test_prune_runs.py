from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.sweep.prune_runs import prune_runs
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.types.terminal_states import TERMINAL_STATES
from tests.sweep.conftest import ago

pytestmark = pytest.mark.django_db

MakeRun = Callable[..., SpecRun]
LIVE = [RunState.PENDING, RunState.RUNNING]


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_a_finished_run_past_the_age_is_deleted_in_any_terminal_state(
    make_run: MakeRun, state: RunState
) -> None:
    make_run("b", state=state, finished_at=ago(days=31))

    assert prune_runs(older_than=timedelta(days=30)) == 1
    assert SpecRun.objects.count() == 0


def test_a_finished_run_inside_the_age_stays(make_run: MakeRun) -> None:
    make_run("b", state=RunState.SUCCEEDED, finished_at=ago(days=29))

    assert prune_runs(older_than=timedelta(days=30)) == 0
    assert SpecRun.objects.count() == 1


@pytest.mark.parametrize("state", LIVE)
def test_a_live_run_stays_however_old(make_run: MakeRun, state: RunState) -> None:
    # The live row has an old finished_at and created_at, so only its state can save it.
    make_run("b", state=state, created_at=ago(days=400), finished_at=ago(days=300))

    assert prune_runs(older_than=timedelta(days=30)) == 0
    assert SpecRun.objects.count() == 1


def test_a_live_run_with_no_finished_at_stays(make_run: MakeRun) -> None:
    make_run("b", state=RunState.RUNNING, created_at=ago(days=400))

    assert prune_runs(older_than=timedelta(days=30)) == 0


def test_a_terminal_run_never_stamped_finished_is_not_guessed_at(make_run: MakeRun) -> None:
    # Names the age conjunct from the null side: no ``finished_at`` is not an old one.
    make_run("b", state=RunState.FAILED, created_at=ago(days=400))

    assert prune_runs(older_than=timedelta(days=30)) == 0


def test_batches_cover_every_row_and_the_count_is_the_total(make_run: MakeRun) -> None:
    for _ in range(5):
        make_run("b", state=RunState.SUCCEEDED, finished_at=ago(days=31))
    kept = make_run("b", state=RunState.SUCCEEDED, finished_at=ago(days=1))

    assert prune_runs(older_than=timedelta(days=30), batch_size=2) == 5
    assert list(SpecRun.objects.all()) == [kept]
