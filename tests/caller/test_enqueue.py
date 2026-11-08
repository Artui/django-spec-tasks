from __future__ import annotations

import decimal
import json
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.db import connections, router, transaction
from django.test.utils import override_settings
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    InvalidArguments,
    NotPermitted,
    PrincipalUnavailable,
    SpecRegistry,
    build_call_context,
    check_call,
)

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.caller.enqueue import enqueue
from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from tests.caller.conftest import STAFF_CHECK, RecordingBackend, build_registry

try:
    from django.utils.json import normalize_json
except ImportError:  # Django below 6.0, where the framework is the backport
    from django_tasks.utils import normalize_json

OPEN = {"order": "7"}

# A list holding itself: ``json.dumps`` raises ``ValueError`` where the others raise ``TypeError``.
_LOOP: list[Any] = []
_LOOP.append(_LOOP)


# Each case a caller can get wrong: the spec, the params, who is calling, the exception that
# names it, and a fragment its message (or the kernel's per-field detail) must carry.
REFUSALS = [
    pytest.param(
        "open", {"order": "7", "orders": "8"}, "user", InvalidArguments, "orders", id="typo"
    ),
    pytest.param("open", {}, "user", InvalidArguments, "order", id="missing-field"),
    pytest.param(
        "open", {"order": "7", "count": [1]}, "user", InvalidArguments, "count", id="wrong-type"
    ),
    pytest.param(
        "open", {"order": "7", "mode": "turbo"}, "user", InvalidArguments, "mode", id="choice"
    ),
    pytest.param("open", OPEN, "deactivated", PrincipalUnavailable, "", id="deactivated"),
    pytest.param("gated", OPEN, "nobody", NotPermitted, "", id="gated-system-run"),
    pytest.param("open", OPEN, "anonymous", TypeError, "actor=None", id="anonymous"),
    pytest.param("upload", {}, "user", TypeError, "attachment", id="file"),
    pytest.param("uploads", {}, "user", TypeError, "attachments", id="file-array"),
    pytest.param("nested_upload", {}, "user", TypeError, "rows.doc", id="file-in-rows"),
    pytest.param("object_upload", {}, "user", TypeError, "meta.doc", id="file-in-fields"),
    pytest.param("open", {"order": "7", "note": (1, 2)}, "user", TypeError, "'note'", id="tuple"),
    pytest.param("open", {"order": "7", "note": b"raw"}, "user", TypeError, "'note'", id="bytes"),
    pytest.param(
        "open", {"order": "7", "note": {1: "a"}}, "user", TypeError, "'note'", id="int-key"
    ),
    pytest.param(
        "open", {"order": "7", "note": _LOOP}, "user", TypeError, "is not JSON", id="loop"
    ),
    pytest.param(
        "open",
        {"order": "7", "note": decimal.Decimal("1.5")},
        "user",
        TypeError,
        "'note'",
        id="decimal",
    ),
]


@pytest.fixture
def actors(user: Any, deactivated: Any) -> dict[str, Any]:
    return {"user": user, "deactivated": deactivated, "nobody": None, "anonymous": AnonymousUser()}


class TestARefusalWritesNothing:
    @pytest.mark.parametrize(("spec_name", "params", "who", "expected", "fragment"), REFUSALS)
    @pytest.mark.django_db
    def test_each_refusal_runs_no_query_writes_no_row_and_sends_nothing(
        self,
        binding: TaskBinding,
        backend: RecordingBackend,
        actors: dict[str, Any],
        django_assert_num_queries: Callable[..., Any],
        django_capture_on_commit_callbacks: Callable[..., Any],
        spec_name: str,
        params: dict[str, Any],
        who: str,
        expected: type[Exception],
        fragment: str,
    ) -> None:
        with (
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            django_assert_num_queries(0),
            pytest.raises(expected) as raised,
        ):
            enqueue(binding, spec_name, params, actor=actors[who])
        detail = getattr(raised.value, "detail", "")
        assert fragment in str(raised.value) or fragment in repr(detail)
        assert callbacks == []
        assert SpecRun.objects.count() == 0
        assert backend.sent == []

    @pytest.mark.django_db
    def test_a_missing_field_is_answered_per_field(self, binding: TaskBinding, user: Any) -> None:
        with pytest.raises(InvalidArguments) as raised:
            enqueue(binding, "open", {}, actor=user)
        assert raised.value.detail == {"order": ["This field is required."]}

    @pytest.mark.django_db
    def test_a_numeric_string_is_accepted_for_an_integer_and_kept_as_sent(
        self, binding: TaskBinding, user: Any
    ) -> None:
        run_id = enqueue(binding, "open", {"order": "7", "count": "3"}, actor=user)
        assert SpecRun.objects.get(pk=run_id).params == {"order": "7", "count": "3"}

    @pytest.mark.django_db
    def test_an_unknown_spec_is_a_programmer_s_mistake_naming_both(
        self, binding: TaskBinding
    ) -> None:
        with pytest.raises(LookupError, match=r"'enqueue-tests'.*'nope'"):
            enqueue(binding, "nope", OPEN, actor=None)
        assert SpecRun.objects.count() == 0

    @pytest.mark.django_db
    def test_leaving_the_actor_out_is_a_type_error_and_not_a_system_run(
        self, binding: TaskBinding
    ) -> None:
        with pytest.raises(TypeError, match="actor"):
            enqueue(binding, "open", OPEN)  # ty: ignore[missing-argument]
        assert SpecRun.objects.count() == 0

    @pytest.mark.django_db
    def test_a_file_is_refused_before_the_principal_is_looked_at(
        self, binding: TaskBinding
    ) -> None:
        # Both answers are TypeErrors; the message says which one answered.
        with pytest.raises(TypeError, match="file parameter"):
            enqueue(binding, "upload", {}, actor=AnonymousUser())

    @pytest.mark.django_db
    def test_a_param_that_json_would_change_is_refused_after_the_kernel_has_looked(
        self, binding: TaskBinding, user: Any
    ) -> None:
        # The tuple is also a bad value, but the unknown key is the kernel's, and it answers first.
        with pytest.raises(InvalidArguments):
            enqueue(binding, "open", {"order": "7", "extra": (1, 2)}, actor=user)

    @pytest.mark.parametrize(
        ("carried", "message"),
        [
            pytest.param(
                {"when": decimal.Decimal("1")}, "carried value 'when' is not JSON", id="decimal"
            ),
            pytest.param({"pair": (1, 2)}, "carried value 'pair' changes", id="tuple"),
            pytest.param({"ratio": float("nan")}, "carried value 'ratio' is not JSON", id="nan"),
            pytest.param({"ratio": float("inf")}, "carried value 'ratio' is not JSON", id="inf"),
            pytest.param({"note": "a\x00b"}, "carried value 'note' is not JSON", id="nul"),
            pytest.param(
                {"note": "a\ud800b"}, "carried value 'note' is not JSON", id="half-a-pair"
            ),
            pytest.param({"k\x00": "a"}, "holding a NUL", id="nul-in-a-key"),
            pytest.param({1: "a"}, "carried value key 1 is not a string", id="int-key"),
        ],
    )
    @pytest.mark.django_db
    def test_a_carried_value_that_json_would_change_is_refused(
        self,
        backend: RecordingBackend,
        user: Any,
        django_assert_num_queries: Callable[..., Any],
        carried: dict[Any, Any],
        message: str,
    ) -> None:
        bound = TaskBinding(
            name="carries-badly",
            registry=build_registry(),
            backend=backend,
            carry=lambda actor: carried,
        )
        with django_assert_num_queries(0), pytest.raises(TypeError, match=message):
            enqueue(bound, "open", OPEN, actor=user)
        assert SpecRun.objects.count() == 0


class TestTheSeedsAreTheBindings:
    @pytest.mark.django_db
    def test_a_parameter_a_seed_occupies_is_refused_at_enqueue_as_the_worker_would(
        self, backend: RecordingBackend, tenant_seeds: Any, user: Any
    ) -> None:
        seeded = TaskBinding(
            name="seeded", registry=build_registry(), backend=backend, pool_seeds=tenant_seeds
        )
        with pytest.raises(ImproperlyConfigured, match="tenant"):
            enqueue(seeded, "tenant_scoped", {"tenant": "x"}, actor=user)
        assert SpecRun.objects.count() == 0

    @pytest.mark.django_db
    def test_the_same_spec_enqueues_under_the_default_seeds(
        self, binding: TaskBinding, user: Any
    ) -> None:
        assert enqueue(binding, "tenant_scoped", {"tenant": "x"}, actor=user)


class TestTheRow:
    @pytest.mark.django_db
    def test_the_row_records_the_call_and_the_id_returned_is_its_key(
        self, binding: TaskBinding, user: Any
    ) -> None:
        run_id = enqueue(binding, "open", OPEN, actor=user)
        run = SpecRun.objects.get()
        assert isinstance(run_id, str)
        assert run_id == str(run.pk)
        assert (run.binding, run.spec_name, run.params) == ("enqueue-tests", "open", OPEN)
        assert run.actor_pk == str(user.pk)
        assert run.carried == {}
        assert run.state == RunState.PENDING
        assert run.attempts == 0

    @pytest.mark.django_db
    def test_a_system_run_records_no_actor(self, binding: TaskBinding) -> None:
        enqueue(binding, "open", OPEN, actor=None)
        assert SpecRun.objects.get().actor_pk is None

    @pytest.mark.django_db
    def test_a_write_costs_one_query(
        self, binding: TaskBinding, user: Any, django_assert_num_queries: Callable[..., Any]
    ) -> None:
        with django_assert_num_queries(1):
            enqueue(binding, "open", OPEN, actor=user)

    @pytest.mark.django_db
    def test_the_binding_s_carry_is_recorded_and_offered_to_the_checks_as_they_run(
        self, backend: RecordingBackend, staff: Any
    ) -> None:
        offered = []

        def carry(actor: Any) -> dict[str, Any]:
            offered.append(actor)
            return {"tenant": "acme", "locale": ["en", "nl"]}

        bound = TaskBinding(name="carries", registry=build_registry(), backend=backend, carry=carry)
        STAFF_CHECK.contexts.clear()
        enqueue(bound, "gated", OPEN, actor=staff)
        assert offered == [staff]
        assert SpecRun.objects.get().carried == {"tenant": "acme", "locale": ["en", "nl"]}
        (context,) = STAFF_CHECK.contexts
        assert context["carried"] == {"tenant": "acme", "locale": ["en", "nl"]}
        assert context == build_call_context("task", base={"carried": context["carried"]})

    @pytest.mark.django_db
    def test_the_kernel_s_own_prelude_spends_no_query(
        self, binding: TaskBinding, user: Any, django_assert_num_queries: Callable[..., Any]
    ) -> None:
        spec = binding.registry.get("open").spec  # ty: ignore[unresolved-attribute]
        with django_assert_num_queries(0):
            check_call(
                spec,
                principal=user,
                arguments=OPEN,
                pool_seeds=DEFAULT_POOL_SEEDS,
                call_context=build_call_context("task", base={"carried": {}}),
            )


class TestTheMessage:
    @pytest.mark.django_db
    def test_the_backend_is_asked_for_the_id_once_the_transaction_commits(
        self,
        binding: TaskBinding,
        backend: RecordingBackend,
        user: Any,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            run_id = enqueue(binding, "open", OPEN, actor=user)
        assert backend.sent == []
        assert len(callbacks) == 1
        callbacks[0]()
        assert backend.sent == [run_id]

    @pytest.mark.django_db(transaction=True)
    def test_nothing_is_sent_before_commit(
        self, binding: TaskBinding, backend: RecordingBackend, user: Any
    ) -> None:
        with transaction.atomic():
            run_id = enqueue(binding, "open", OPEN, actor=user)
            assert backend.sent == []
        assert backend.sent == [run_id]

    @pytest.mark.django_db(transaction=True)
    def test_nothing_is_sent_on_rollback_and_no_row_remains(
        self, binding: TaskBinding, backend: RecordingBackend, user: Any
    ) -> None:
        with pytest.raises(RuntimeError, match="caller failed"), transaction.atomic():
            enqueue(binding, "open", OPEN, actor=user)
            raise RuntimeError("caller failed")
        assert backend.sent == []
        assert SpecRun.objects.count() == 0

    @pytest.mark.django_db(transaction=True)
    def test_a_backend_raising_at_commit_cancels_no_later_callback_and_leaves_the_row_pending(
        self, binding: TaskBinding, user: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Down:
            def enqueue(self, run_id: str) -> None:
                raise ConnectionError("broker is down")

        broken = TaskBinding(name="broken", registry=build_registry(), backend=Down())
        later: list[str] = []
        with transaction.atomic():
            run_id = enqueue(broken, "open", OPEN, actor=user)
            transaction.on_commit(lambda: later.append("ran"))
        assert later == ["ran"]
        assert SpecRun.objects.get(pk=run_id).state == RunState.PENDING
        assert "broker is down" in caplog.text

    @pytest.mark.django_db(transaction=True, databases=["default", "progress"])
    def test_the_callback_waits_on_the_alias_the_row_was_written_to(
        self, binding: TaskBinding, backend: RecordingBackend, user: Any
    ) -> None:
        # Not a supported setup (the system check refuses a run table off the default alias);
        # it isolates the mechanism. A router sends the run table to the second connection. A
        # callback registered on the default alias would find autocommit there and fire at
        # once, before this transaction committed.
        class ToProgress:
            def db_for_write(self, model: Any, **hints: Any) -> str | None:
                return "progress" if model is SpecRun else None

        with override_settings(DATABASE_ROUTERS=[ToProgress()]):
            assert router.db_for_write(SpecRun) == "progress"
            with transaction.atomic(using="progress"):
                run_id = enqueue(binding, "open", OPEN, actor=None)
                assert backend.sent == []
            assert backend.sent == [run_id]
            assert SpecRun.objects.using("progress").filter(pk=run_id).exists()
        connections["progress"].close()


class TestTheRoundTripAgainstTheFramework:
    """Django Tasks normalizes its arguments, and some changes are silent: the gate is ours."""

    ACCEPTED = [
        "text",
        3,
        2.5,
        True,
        ["a", 1, None],
        {"nested": {"deep": ["x", {"y": 1}]}},
    ]
    REFUSED = [
        (1, 2),
        b"raw",
        {1: "a"},
        {"inner": (1, 2)},
        [b"raw"],
        decimal.Decimal("1.5"),
        {"a", "b"},
    ]

    @pytest.mark.parametrize("value", ACCEPTED, ids=repr)
    @pytest.mark.django_db
    def test_what_enqueue_accepts_the_framework_leaves_unchanged(
        self, binding: TaskBinding, value: Any
    ) -> None:
        enqueue(binding, "open", {"order": "7", "note": value}, actor=None)
        assert normalize_json({"order": "7", "note": value}) == {"order": "7", "note": value}

    @pytest.mark.parametrize("value", REFUSED, ids=repr)
    @pytest.mark.django_db
    def test_what_enqueue_refuses_the_framework_changes_or_cannot_carry(
        self, binding: TaskBinding, value: Any
    ) -> None:
        with pytest.raises(TypeError, match="'note'"):
            enqueue(binding, "open", {"order": "7", "note": value}, actor=None)
        try:
            normalized = normalize_json(value)
        except (TypeError, ValueError):
            return
        # Accepted, then: what a real queue hands the worker is what a JSON store returns.
        assert json.loads(json.dumps(normalized)) != value


class TestAnEmptyRegistry:
    @pytest.mark.django_db
    def test_a_binding_over_no_specs_refuses_by_name(self, backend: RecordingBackend) -> None:
        empty = TaskBinding(name="empty", registry=SpecRegistry(), backend=backend)
        with pytest.raises(LookupError, match="'empty'"):
            enqueue(empty, "open", OPEN, actor=None)
