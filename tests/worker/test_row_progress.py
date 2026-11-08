"""Progress: on the row, fenced, and never the reason a run ends."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.db import DatabaseError
from django.db.models.sql.compiler import SQLUpdateCompiler
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.worker.row_progress import RowProgress
from django_spec_tasks.worker.run_spec import run_spec
from tests.testapp.models import Note

_BOTH = ["default", "progress"]


def _running(make_run: Callable[..., SpecRun]) -> SpecRun:
    run = make_run(ServiceSpec(service=lambda: None, permissions=[Unrestricted()]))
    SpecRun.objects.filter(pk=run.pk).update(state=RunState.RUNNING, attempts=2)
    run.refresh_from_db()
    return run


@pytest.mark.django_db
def test_a_report_lands_on_the_row(make_run: Callable[..., SpecRun]) -> None:
    run = _running(make_run)

    RowProgress(run.pk, 2)(3, message="working")

    run.refresh_from_db()
    assert run.progress == 3
    assert run.progress_message == "working"


@pytest.mark.django_db
def test_a_total_makes_it_a_fraction(make_run: Callable[..., SpecRun]) -> None:
    run = _running(make_run)

    RowProgress(run.pk, 2)(1, total=4)

    run.refresh_from_db()
    assert run.progress == 0.25


@pytest.mark.django_db
def test_a_total_of_nothing_keeps_the_raw_value_and_raises_nothing(
    make_run: Callable[..., SpecRun],
) -> None:
    run = _running(make_run)

    RowProgress(run.pk, 2)(1, total=0)

    run.refresh_from_db()
    assert run.progress == 1


@pytest.mark.django_db
def test_a_report_without_a_message_clears_the_last(make_run: Callable[..., SpecRun]) -> None:
    run = _running(make_run)
    reporter = RowProgress(run.pk, 2)

    reporter(1, message="first")
    reporter(2)

    run.refresh_from_db()
    assert run.progress == 2
    assert run.progress_message is None


@pytest.mark.django_db
def test_meta_goes_nowhere_and_breaks_nothing(make_run: Callable[..., SpecRun]) -> None:
    run = _running(make_run)

    RowProgress(run.pk, 2)(1, meta={"com.example/stage": "one"})

    run.refresh_from_db()
    assert run.progress == 1


@pytest.mark.django_db
def test_a_stale_attempt_writes_nothing(make_run: Callable[..., SpecRun]) -> None:
    # Fails without ``attempts`` in the fence.
    run = _running(make_run)

    RowProgress(run.pk, 1)(1)

    run.refresh_from_db()
    assert run.progress is None


@pytest.mark.django_db
def test_a_finished_run_takes_no_more_reports(make_run: Callable[..., SpecRun]) -> None:
    # Fails without ``state`` in the fence.
    run = _running(make_run)
    SpecRun.objects.filter(pk=run.pk).update(state=RunState.ABANDONED)

    RowProgress(run.pk, 2)(1)

    run.refresh_from_db()
    assert run.progress is None


@pytest.mark.django_db
def test_a_report_touches_only_its_own_row(make_run: Callable[..., SpecRun]) -> None:
    # Fails without ``pk`` in the fence.
    run = _running(make_run)
    other = _running(make_run)

    RowProgress(run.pk, 2)(1)

    other.refresh_from_db()
    assert other.progress is None


@pytest.mark.django_db(transaction=True)
def test_a_failed_write_is_logged_and_dropped(
    make_run: Callable[..., SpecRun],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run = _running(make_run)

    def refuses(*args: Any, **kwargs: Any) -> int:
        raise DatabaseError("the disk is full")

    monkeypatch.setattr("django.db.models.query.QuerySet.update", refuses)

    RowProgress(run.pk, 2)(1)

    assert str(run.pk) in caplog.text


@pytest.mark.django_db(transaction=True)
def test_a_failed_write_inside_a_run_leaves_the_run_able_to_finish(
    make_run: Callable[..., SpecRun], caplog: pytest.LogCaptureFixture
) -> None:
    # A value the column refuses: a report never raises into the service, whatever it says.
    def service(progress: Any) -> str:
        Note.objects.create(text="kept")
        progress("not a number")
        return "done"

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.output == "done"
    assert run.progress is None
    assert Note.objects.count() == 1
    assert str(run.pk) in caplog.text


@pytest.mark.django_db(transaction=True)
def test_a_failed_write_keeps_the_service_s_work(
    make_run: Callable[..., SpecRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A database error inside ``update`` marks the open transaction broken unless a savepoint
    # took it. Fails without the savepoint: the kernel's own ``atomic`` rolls the note back as
    # the service returns, and raises nothing, so the run still ends ``SUCCEEDED``. Raised at
    # the compiler, under ``update``'s own guard, because that guard does the marking.
    refused: list[bool] = []
    real = SQLUpdateCompiler.execute_sql

    def execute_sql(compiler: Any, *args: Any, **kwargs: Any) -> Any:
        # A value, because the claim writes this column too, clearing it to null.
        if any(
            field.name == "progress" and value is not None
            for field, _, value in compiler.query.values
        ):
            refused.append(True)
            raise DatabaseError("the row is locked")
        return real(compiler, *args, **kwargs)

    monkeypatch.setattr(SQLUpdateCompiler, "execute_sql", execute_sql)

    def service(progress: Any) -> str:
        Note.objects.create(text="the service's work")
        progress(1)
        return "done"

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))

    run_spec(str(run.pk))

    assert refused == [True]
    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.output == "done"
    assert Note.objects.count() == 1


@pytest.mark.django_db(transaction=True, databases=_BOTH)
def test_progress_under_atomic_false_is_visible_from_another_connection(
    make_run: Callable[..., SpecRun],
) -> None:
    seen: list[Any] = []
    run_ids: list[Any] = []

    def service(progress: Any) -> None:
        progress(1)
        seen.append(SpecRun.objects.using("progress").get(pk=run_ids[0]).progress)

    run = make_run(ServiceSpec(service=service, atomic=False, permissions=[Unrestricted()]))
    run_ids.append(run.pk)

    run_spec(str(run.pk))

    assert seen == [1]


@pytest.mark.django_db(transaction=True, databases=_BOTH)
def test_progress_under_atomic_true_with_no_alias_is_invisible_until_commit(
    make_run: Callable[..., SpecRun],
) -> None:
    seen: list[Any] = []
    run_ids: list[Any] = []

    def service(progress: Any) -> None:
        progress(1)
        seen.append(SpecRun.objects.using("progress").get(pk=run_ids[0]).progress)

    run = make_run(ServiceSpec(service=service, permissions=[Unrestricted()]))
    run_ids.append(run.pk)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert seen == [None]
    assert run.progress == 1


@pytest.mark.django_db(transaction=True, databases=_BOTH)
def test_progress_under_atomic_true_through_an_alias_is_visible_during_the_run(
    make_run: Callable[..., SpecRun],
) -> None:
    # Nothing written on ``default`` yet, so SQLite has no write lock to refuse it for.
    seen: list[Any] = []
    run_ids: list[Any] = []

    def service(progress: Any) -> None:
        progress(1)
        seen.append(SpecRun.objects.using("progress").get(pk=run_ids[0]).progress)

    run = make_run(
        ServiceSpec(service=service, permissions=[Unrestricted()]), progress_using="progress"
    )
    run_ids.append(run.pk)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert seen == [1]
    assert run.state == RunState.SUCCEEDED


@pytest.mark.django_db(transaction=True, databases=_BOTH)
def test_a_progress_alias_that_cannot_write_drops_the_report_and_the_run_finishes(
    make_run: Callable[..., SpecRun], caplog: pytest.LogCaptureFixture
) -> None:
    # The service holds SQLite's write lock on ``default``, so the alias waits and fails.
    def service(progress: Any) -> str:
        Note.objects.create(text="kept")
        progress(1)
        return "done"

    run = make_run(
        ServiceSpec(service=service, permissions=[Unrestricted()]), progress_using="progress"
    )

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.progress is None
    assert Note.objects.count() == 1
    assert str(run.pk) in caplog.text
