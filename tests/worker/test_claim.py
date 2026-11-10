from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.db.models import F
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.worker.run_spec import run_spec

pytestmark = pytest.mark.django_db


class _Counts:
    """A service that counts its calls, and the spec around it."""

    def __init__(self) -> None:
        self.calls = 0
        self.spec = ServiceSpec(service=self._service, permissions=[Unrestricted()])

    def _service(self) -> None:
        self.calls += 1


def _during_the_read(monkeypatch: pytest.MonkeyPatch, run: SpecRun, **columns: Any) -> None:
    """Change the row between the worker's read of it and its claim."""
    real = TaskBinding.named

    def named(name: str) -> TaskBinding | None:
        SpecRun.objects.filter(pk=run.pk).update(**columns)
        return real(name)

    monkeypatch.setattr(TaskBinding, "named", named)


def test_a_claim_runs_the_spec_and_records_the_attempt(make_run: Callable[..., SpecRun]) -> None:
    counts = _Counts()
    run = make_run(counts.spec)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert counts.calls == 1
    assert run.state == RunState.SUCCEEDED
    assert run.attempts == 1


def test_a_claim_stamps_when_and_for_how_long(make_run: Callable[..., SpecRun]) -> None:
    run = make_run(_Counts().spec, lease=timedelta(minutes=7))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.claimed_at is not None
    assert run.lease_expires_at == run.claimed_at + timedelta(minutes=7)


def test_a_second_copy_of_a_claimed_run_runs_nothing_for_one_query(
    make_run: Callable[..., SpecRun], django_assert_num_queries: Any
) -> None:
    counts = _Counts()
    run = make_run(counts.spec)
    SpecRun.objects.filter(pk=run.pk).update(state=RunState.RUNNING, attempts=1)

    with django_assert_num_queries(1):
        run_spec(str(run.pk))

    run.refresh_from_db()
    assert counts.calls == 0
    assert (run.state, run.attempts) == (RunState.RUNNING, 1)


def test_a_copy_of_a_finished_run_runs_nothing(make_run: Callable[..., SpecRun]) -> None:
    counts = _Counts()
    run = make_run(counts.spec)

    run_spec(str(run.pk))
    run_spec(str(run.pk))

    run.refresh_from_db()
    assert counts.calls == 1
    assert run.attempts == 1


def test_a_message_naming_no_row_runs_nothing(make_run: Callable[..., SpecRun]) -> None:
    counts = _Counts()
    run = make_run(counts.spec)
    run_id = str(run.pk)
    run.delete()

    run_spec(run_id)

    assert counts.calls == 0


def test_a_copy_getting_there_between_the_read_and_the_claim_wins(
    make_run: Callable[..., SpecRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The read saw it pending, so only the claim can tell two copies apart.
    counts = _Counts()
    run = make_run(counts.spec)
    _during_the_read(monkeypatch, run, state=RunState.RUNNING, attempts=F("attempts") + 1)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert counts.calls == 0
    assert (run.state, run.attempts) == (RunState.RUNNING, 1)


def test_a_row_ended_since_the_read_is_not_claimed(
    make_run: Callable[..., SpecRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fails without state in the claim: an ended row keeps its attempt count and matches.
    counts = _Counts()
    run = make_run(counts.spec)
    _during_the_read(monkeypatch, run, state=RunState.SPEC_UNAVAILABLE)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert counts.calls == 0
    assert (run.state, run.attempts) == (RunState.SPEC_UNAVAILABLE, 0)


def test_a_row_claimed_and_handed_back_since_the_read_is_not_claimed_on_a_stale_count(
    make_run: Callable[..., SpecRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fails without attempts in the claim: the row is pending again, so state matches alone.
    counts = _Counts()
    run = make_run(counts.spec)
    _during_the_read(monkeypatch, run, attempts=F("attempts") + 1)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert counts.calls == 0
    assert (run.state, run.attempts) == (RunState.PENDING, 1)


def test_a_claim_touches_only_its_own_row(make_run: Callable[..., SpecRun]) -> None:
    # Fails without ``pk`` in the claim.
    run = make_run(_Counts().spec)
    other = make_run(_Counts().spec)

    run_spec(str(run.pk))

    other.refresh_from_db()
    assert (other.state, other.attempts) == (RunState.PENDING, 0)


def test_a_new_attempt_starts_with_no_progress(make_run: Callable[..., SpecRun]) -> None:
    # A handed-back row keeps the dead attempt's progress; a new attempt must start at none.
    run = make_run(_Counts().spec)
    SpecRun.objects.filter(pk=run.pk).update(attempts=1, progress=0.5, progress_message="old")

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.attempts == 2
    assert run.progress is None
    assert run.progress_message is None


def test_a_row_naming_no_binding_ends_spec_unavailable(make_run: Callable[..., SpecRun]) -> None:
    run = make_run(_Counts().spec)
    SpecRun.objects.filter(pk=run.pk).update(binding="gone")

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SPEC_UNAVAILABLE
    assert run.attempts == 0
    assert "gone" in run.refusal_detail["message"]
    assert run.finished_at is not None


def test_a_row_naming_no_spec_ends_spec_unavailable_and_fires(
    make_run: Callable[..., SpecRun],
) -> None:
    finished: list[str] = []
    run = make_run(_Counts().spec, on_finish=lambda row: finished.append(row.state))
    SpecRun.objects.filter(pk=run.pk).update(spec_name="gone")

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SPEC_UNAVAILABLE
    assert "gone" in run.refusal_detail["message"]
    assert finished == [RunState.SPEC_UNAVAILABLE]


def test_a_spec_unavailable_write_is_fenced_on_the_pending_row(
    make_run: Callable[..., SpecRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = make_run(_Counts().spec)
    SpecRun.objects.filter(pk=run.pk).update(binding="gone")
    _during_the_read(monkeypatch, run, state=RunState.ABANDONED)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.ABANDONED
