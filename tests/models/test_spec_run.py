from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from django.db import connection
from django.utils.translation import gettext_lazy

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState


def _run(**overrides: object) -> SpecRun:
    values = {"binding": "orders", "spec_name": "refund", "params": {"order": "7"}}
    return SpecRun.objects.create(**{**values, **overrides})


def test_the_columns_are_the_call_and_the_queue_s_bookkeeping() -> None:
    # Named, because a column added later is empty for every earlier run.
    assert {field.name for field in SpecRun._meta.get_fields()} == {
        "id",
        "binding",
        "spec_name",
        "params",
        "actor_pk",
        "carried",
        "state",
        "attempts",
        "claimed_at",
        "lease_expires_at",
        "progress",
        "progress_message",
        "kind",
        "output",
        "instance_pk",
        "package_version",
        "refusal_class",
        "refusal_code",
        "refusal_detail",
        "traceback",
        "created_at",
        "asked_at",
        "finished_at",
    }


def test_no_column_copies_a_declaration() -> None:
    names = {field.name for field in SpecRun._meta.get_fields()}
    assert not names & {"idempotent", "atomic", "permissions"}


@pytest.mark.django_db
def test_a_new_run_is_pending_with_nothing_recorded_about_its_end() -> None:
    run = _run()
    run.refresh_from_db()
    assert isinstance(run.pk, uuid.UUID)
    assert run.state == RunState.PENDING
    assert run.attempts == 0
    assert run.carried == {}
    assert run.created_at is not None
    assert run.package_version == ""
    assert (run.actor_pk, run.finished_at, run.output, run.kind, run.traceback) == (
        None,
        None,
        None,
        None,
        None,
    )


@pytest.mark.django_db
def test_every_state_fits_its_column() -> None:
    longest = max(RunState, key=lambda state: len(state.value))
    assert len(longest.value) <= SpecRun._meta.get_field("state").max_length


@pytest.mark.django_db
def test_output_and_refusal_detail_take_what_a_presenter_and_a_refusal_produce() -> None:
    # A spec with no presenter hands its value back as is, and refusal messages may be lazy.
    run = _run(
        output={"total": Decimal("10.50")},
        refusal_detail={"order": [gettext_lazy("This field is required.")]},
    )
    run.refresh_from_db()
    assert run.output == {"total": "10.50"}
    assert run.refusal_detail == {"order": ["This field is required."]}


@pytest.mark.django_db
def test_a_refusal_detail_keyed_by_row_index_reads_back_with_string_keys() -> None:
    # The kernel keys failing rows by their ``int`` index, and JSON has no such key.
    run = _run(refusal_detail={"books": {1: {"title": ["Required."]}}})
    run.refresh_from_db()
    assert run.refusal_detail == {"books": {"1": {"title": ["Required."]}}}
    assert json.dumps(run.refusal_detail)


@pytest.mark.django_db
def test_the_sweep_and_prune_indexes_exist_leading_on_state() -> None:
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, SpecRun._meta.db_table)
    indexed = {tuple(c["columns"]) for c in constraints.values() if c["index"]}
    assert {
        ("state", "lease_expires_at"),
        ("state", "asked_at"),
        ("state", "finished_at"),
    } <= indexed


def test_str_names_the_binding_the_spec_and_the_state() -> None:
    run = SpecRun(binding="orders", spec_name="refund")
    assert str(run) == f"orders.refund {run.pk} (PENDING)"
