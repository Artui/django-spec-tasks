"""Who may read a run, what they are shown, and that no refusal tells ids apart."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import fields

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django_service_specs import NotPermitted

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.read.read_run import read_run
from django_spec_tasks.read.state_of import state_of
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.types.run_status import RunStatus
from tests.read.conftest import MARKER

pytestmark = pytest.mark.django_db

MakeBinding = Callable[..., TaskBinding]
MakeRun = Callable[..., SpecRun]


def test_the_owner_reads_the_run(make_binding: MakeBinding, make_run: MakeRun, owner: User) -> None:
    binding = make_binding()
    run = make_run(
        binding,
        owner,
        state=RunState.REFUSED,
        kind="detail",
        output={"total": "1.00"},
        progress=0.5,
        progress_message="halfway",
        refusal_code="gone",
        refusal_detail={"why": "gone"},
    )

    status = read_run(binding, str(run.pk), owner)

    assert status == RunStatus(
        id=str(run.pk),
        state=RunState.REFUSED,
        kind="detail",
        output={"total": "1.00"},
        progress=0.5,
        progress_message="halfway",
        refusal_code="gone",
        refusal_detail={"why": "gone"},
        created_at=run.created_at,
        finished_at=None,
    )
    assert isinstance(status.state, RunState)


def test_the_status_holds_none_of_what_the_run_was_asked(
    make_binding: MakeBinding, make_run: MakeRun, owner: User
) -> None:
    binding = make_binding()
    run = make_run(binding, owner, refusal_class="ServiceError")

    status = read_run(binding, str(run.pk), owner)

    names = {field.name for field in fields(RunStatus)}
    assert names.isdisjoint({"params", "carried", "traceback", "actor_pk", "refusal_class"})
    assert names == {
        "id",
        "state",
        "kind",
        "output",
        "progress",
        "progress_message",
        "refusal_code",
        "refusal_detail",
        "created_at",
        "finished_at",
    }
    assert MARKER not in repr(status)


def test_a_stranger_is_refused(
    make_binding: MakeBinding, make_run: MakeRun, owner: User, stranger: User
) -> None:
    binding = make_binding()
    run = make_run(binding, owner)

    with pytest.raises(NotPermitted):
        read_run(binding, str(run.pk), stranger)


def test_a_deactivated_owner_is_refused(
    make_binding: MakeBinding, make_run: MakeRun, owner: User
) -> None:
    binding = make_binding()
    run = make_run(binding, owner)
    owner.is_active = False
    owner.save()

    with pytest.raises(NotPermitted):
        read_run(binding, str(run.pk), owner)


def test_a_deactivated_principal_is_refused_even_where_can_read_would_say_yes(
    make_binding: MakeBinding, make_run: MakeRun, owner: User, stranger: User
) -> None:
    # A deactivated principal never acts, as the kernel reads it, whichever way it arrived.
    binding = make_binding(can_read=lambda principal, run: True)
    run = make_run(binding, owner)
    stranger.is_active = False
    stranger.save()

    with pytest.raises(NotPermitted):
        read_run(binding, str(run.pk), stranger)


def test_can_read_lets_a_stranger_in_and_is_handed_the_principal_and_the_row(
    make_binding: MakeBinding, make_run: MakeRun, owner: User, stranger: User
) -> None:
    seen: list[tuple[object, object]] = []

    def can_read(principal: object, run: object) -> bool:
        seen.append((principal, run))
        return True

    binding = make_binding(can_read=can_read)
    run = make_run(binding, owner)

    assert read_run(binding, str(run.pk), stranger).id == str(run.pk)
    assert seen == [(stranger, run)]


def test_can_read_saying_no_is_a_refusal(
    make_binding: MakeBinding, make_run: MakeRun, owner: User, stranger: User
) -> None:
    binding = make_binding(can_read=lambda principal, run: False)
    run = make_run(binding, owner)

    with pytest.raises(NotPermitted):
        read_run(binding, str(run.pk), stranger)


def test_a_system_run_is_read_only_through_can_read(
    make_binding: MakeBinding, make_run: MakeRun, owner: User
) -> None:
    closed = make_binding()
    open_ = make_binding(can_read=lambda principal, run: True)
    system_closed = make_run(closed, None)
    system_open = make_run(open_, None)

    with pytest.raises(NotPermitted):
        read_run(closed, str(system_closed.pk), owner)
    assert read_run(open_, str(system_open.pk), owner).id == str(system_open.pk)
    assert system_open.actor_pk is None


@pytest.mark.parametrize(
    "actor_pk",
    [
        pytest.param(None, id="a_system_run"),
        pytest.param("1", id="someone_s_run"),
        # Names the ``pk is not None`` guard: ``str(None)`` is "None", which a row could hold.
        pytest.param("None", id="a_row_naming_none"),
    ],
)
def test_an_anonymous_principal_owns_nothing(
    make_binding: MakeBinding, make_run: MakeRun, actor_pk: str | None
) -> None:
    binding = make_binding()
    run = make_run(binding, None, actor_pk=actor_pk)

    with pytest.raises(NotPermitted):
        read_run(binding, str(run.pk), AnonymousUser())


@pytest.mark.parametrize("which", ["unknown", "malformed", "other_binding"])
def test_no_refusal_tells_a_stranger_from_an_id_that_is_nothing(
    make_binding: MakeBinding, make_run: MakeRun, owner: User, stranger: User, which: str
) -> None:
    binding = make_binding(can_read=lambda principal, run: True)
    other = make_binding()
    elsewhere = make_run(other, owner)
    refused_stranger = make_binding()
    stranger_run = make_run(refused_stranger, owner)
    ids = {
        "unknown": str(uuid.uuid4()),
        "malformed": "not-a-uuid",
        # The id exists and the owner is reading it: only the binding is wrong, so one
        # binding's ``can_read`` cannot be used to read another's runs.
        "other_binding": str(elsewhere.pk),
    }

    with pytest.raises(NotPermitted) as stranger_refusal:
        read_run(refused_stranger, str(stranger_run.pk), stranger)
    with pytest.raises(NotPermitted) as refusal:
        read_run(binding, ids[which], owner if which == "other_binding" else stranger)

    assert type(refusal.value) is NotPermitted
    assert type(stranger_refusal.value) is NotPermitted
    assert str(refusal.value) == str(stranger_refusal.value)


# --- state_of: for trusted callers, with no principal ---------------------------------


def test_state_of_answers_the_state_alone(
    make_binding: MakeBinding, make_run: MakeRun, owner: User
) -> None:
    binding = make_binding()
    run = make_run(binding, owner, state=RunState.RUNNING)

    assert state_of(binding, str(run.pk)) == RunState.RUNNING


@pytest.mark.parametrize("which", ["unknown", "malformed", "other_binding"])
def test_state_of_answers_none_for_an_id_that_is_not_this_bindings(
    make_binding: MakeBinding, make_run: MakeRun, owner: User, which: str
) -> None:
    binding = make_binding()
    elsewhere = make_run(make_binding(), owner)
    ids = {
        "unknown": str(uuid.uuid4()),
        "malformed": "not-a-uuid",
        "other_binding": str(elsewhere.pk),
    }

    assert state_of(binding, ids[which]) is None
