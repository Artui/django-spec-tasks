from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    Presenter,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
    call_transport,
)

from django_spec_tasks.models.spec_run import SpecRun
from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.version import __version__
from django_spec_tasks.worker.run_spec import run_spec
from tests.testapp.models import Document, Note, Order

pytestmark = pytest.mark.django_db


class _RendersTotal(Presenter):
    def output(self) -> Any:
        return None

    def present(self, value: Any) -> Any:
        return {"total": str(value), "currency": "EUR"}


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, cls=DjangoJSONEncoder).encode())


def _service_returning(value: Any, *, atomic: bool = True) -> ServiceSpec:
    def service() -> Any:
        Note.objects.create(text="the service's work")
        return value

    return ServiceSpec(service=service, atomic=atomic, permissions=[Unrestricted()])


def test_a_decimal_through_a_presenter_lands_as_the_presenter_s_json(
    make_run: Callable[..., SpecRun],
) -> None:
    spec = ServiceSpec(
        service=lambda: Decimal("12.50"), presenter=_RendersTotal(), permissions=[Unrestricted()]
    )
    run = make_run(spec)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.kind == "instance"
    assert run.output == {"total": "12.50", "currency": "EUR"}


def test_a_decimal_with_no_presenter_encodes_as_the_column_does(
    make_run: Callable[..., SpecRun],
) -> None:
    run = make_run(_service_returning(Decimal("12.50")))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.output == "12.50"


@pytest.mark.parametrize("atomic", [True, False])
def test_output_exactly_at_the_bound_is_stored(
    make_run: Callable[..., SpecRun], atomic: bool
) -> None:
    value = {"text": "x" * 50}
    run = make_run(_service_returning(value, atomic=atomic), max_output_bytes=_encoded_size(value))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.output == value


@pytest.mark.parametrize(("atomic", "notes"), [(True, 0), (False, 1)])
def test_output_one_byte_over_the_bound_is_refused_and_never_truncated(
    make_run: Callable[..., SpecRun], atomic: bool, notes: int
) -> None:
    value = {"text": "x" * 50}
    size = _encoded_size(value)
    run = make_run(_service_returning(value, atomic=atomic), max_output_bytes=size - 1)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.OUTPUT_TOO_LARGE
    assert run.output is None
    assert run.refusal_detail == {"size": size, "limit": size - 1}
    assert run.refusal_class is None
    assert Note.objects.count() == notes


def test_the_bound_measures_what_the_column_writes(make_run: Callable[..., SpecRun]) -> None:
    # Six bytes each as JSON writes them, two each as UTF-8: measuring the text passes this.
    value = "é" * 10
    run = make_run(_service_returning(value), max_output_bytes=_encoded_size(value) - 1)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.OUTPUT_TOO_LARGE


def test_the_bound_measures_with_the_column_s_encoder(make_run: Callable[..., SpecRun]) -> None:
    # A Decimal is a seven-byte string to this encoder, and an error to the plain one.
    value = Decimal("12.50")
    run = make_run(_service_returning(value), max_output_bytes=_encoded_size(value))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED


def test_on_finish_runs_for_output_too_large(make_run: Callable[..., SpecRun]) -> None:
    finished: list[str] = []
    run = make_run(
        _service_returning("too long"),
        max_output_bytes=1,
        on_finish=lambda row: finished.append(row.state),
    )

    run_spec(str(run.pk))

    assert finished == [RunState.OUTPUT_TOO_LARGE]


def test_output_no_json_can_hold_is_a_failure_and_rolls_back(
    make_run: Callable[..., SpecRun],
) -> None:
    run = make_run(_service_returning(object()))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert "TypeError" in run.traceback
    assert Note.objects.count() == 0


def test_a_float_json_cannot_write_is_a_failure_and_not_a_stuck_run(
    make_run: Callable[..., SpecRun],
) -> None:
    # The default encoder writes a bare word no JSON column stores; the write would raise.
    run = make_run(_service_returning({"ratio": float("nan")}))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert "ValueError" in run.traceback


@pytest.mark.parametrize("atomic", [True, False])
@pytest.mark.parametrize(
    "output",
    [
        pytest.param({"text": "a\x00b"}, id="in-a-value"),
        pytest.param({"a\x00b": 1}, id="in-a-key"),
        pytest.param({"text": "\\\x00"}, id="after-a-backslash"),
        pytest.param({"text": "a\ud800b"}, id="half-a-surrogate-pair"),
        pytest.param({"text": "a\udc00b"}, id="half-a-low-surrogate"),
    ],
)
def test_output_a_json_column_cannot_hold_is_a_failure_and_not_a_stuck_run(
    make_run: Callable[..., SpecRun], output: Any, atomic: bool
) -> None:
    # SQLite stores it and Postgres raises at the terminal write, leaving the row RUNNING.
    run = make_run(_service_returning(output, atomic=atomic))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert "ValueError" in run.traceback
    assert run.output is None


@pytest.mark.parametrize(
    "text", ["\\u0000", "\\\\u0000", "\\\\\\u0000x", "\\ud800", "\\udc00", "a\U0001f600b"]
)
def test_text_that_only_reads_like_something_unstorable_is_stored(
    make_run: Callable[..., SpecRun], text: str
) -> None:
    # Backslash-letters and surrogate pairs are stored as JSON holds them, not refused.
    run = make_run(_service_returning({"text": text}))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.output == {"text": text}


def test_a_surrogate_pair_beside_a_value_only_the_encoder_can_write_is_stored(
    make_run: Callable[..., SpecRun],
) -> None:
    # Telling a pair from half of one encodes the value a second time, with the same encoder.
    run = make_run(_service_returning({"total": Decimal("1.50"), "text": "\U0001f600"}))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.output == {"total": "1.50", "text": "\U0001f600"}


def test_a_selector_finding_nothing_is_a_result(make_run: Callable[..., SpecRun]) -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=lambda: None, permissions=[Unrestricted()]
    )
    run = make_run(spec)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.kind == "not_found"
    assert run.output is None


@pytest.mark.parametrize("atomic", [True, False])
def test_a_service_whose_target_is_gone_is_refused_and_never_reads_as_succeeded(
    make_run: Callable[..., SpecRun], atomic: bool
) -> None:
    # A missing target answers with a result; a refund of a deleted order must not read as done.
    called: list[bool] = []
    finished: list[str] = []
    target = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None)
    spec = ServiceSpec(
        service=lambda: called.append(True),
        instance_selector_spec=target,
        permissions=[Unrestricted()],
        atomic=atomic,
    )
    run = make_run(spec, on_finish=lambda row: finished.append(row.state))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert called == []
    assert run.state == RunState.REFUSED
    assert run.kind == "not_found"
    assert run.output is None
    assert run.refusal_class == "ServiceNotFound"
    assert run.refusal_detail["message"]
    assert run.finished_at is not None
    assert finished == [RunState.REFUSED]


def test_a_list_is_recorded_as_one(make_run: Callable[..., SpecRun]) -> None:
    spec = SelectorSpec(
        kind=SelectorKind.LIST, selector=lambda: [1, 2], permissions=[Unrestricted()]
    )
    run = make_run(spec)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.kind == "list"
    assert run.output == [1, 2]
    assert run.instance_pk is None


def test_the_target_s_identity_is_kept(make_run: Callable[..., SpecRun]) -> None:
    order = Order.objects.create(reference="A-1")
    target = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=lambda: Order.objects.filter(pk=order.pk)
    )
    spec = ServiceSpec(
        service=lambda instance: instance.reference,
        instance_selector_spec=target,
        permissions=[Unrestricted()],
    )
    run = make_run(spec)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.instance_pk == str(order.pk)
    assert run.output == "A-1"


@pytest.mark.parametrize("atomic", [True, False])
@pytest.mark.parametrize("length", [255, 256, 400])
def test_a_target_whose_key_is_past_255_characters_ends_succeeded_with_the_key_whole(
    make_run: Callable[..., SpecRun], length: int, atomic: bool
) -> None:
    key = "k" * length
    Document.objects.create(key=key, title="long")
    target = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=lambda: Document.objects.filter(pk=key)
    )
    spec = ServiceSpec(
        service=lambda instance: instance.title,
        instance_selector_spec=target,
        atomic=atomic,
        permissions=[Unrestricted()],
    )
    run = make_run(spec)

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.instance_pk == key
    assert run.output == "long"


def test_the_output_is_stamped_with_the_version_that_wrote_it(
    make_run: Callable[..., SpecRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("django_spec_tasks.worker.run_spec.__version__", "9.9.9")
    run = make_run(_service_returning("done"))

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.package_version == "9.9.9"
    assert run.finished_at is not None


def test_a_refusal_is_stamped_too(
    make_run: Callable[..., SpecRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("django_spec_tasks.worker.run_spec.__version__", "9.9.9")
    run = make_run(ServiceSpec(service=lambda: None, permissions=[Unrestricted()]), params={"a": 1})

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.INVALID
    assert run.package_version == "9.9.9"


def test_the_real_version_is_the_default_stamp(make_run: Callable[..., SpecRun]) -> None:
    run = make_run(_service_returning("done"))
    SpecRun.objects.filter(pk=run.pk).update(package_version="stale")

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.package_version == __version__


def test_the_call_is_a_task_call_with_what_was_carried(make_run: Callable[..., SpecRun]) -> None:
    seen: list[Any] = []

    def seed(call_context: Any) -> Any:
        return call_context["carried"]["correlation"]

    def service(call_context: Any, correlation: Any) -> None:
        seen.append((call_transport(call_context), correlation))

    run = make_run(
        ServiceSpec(service=service, permissions=[Unrestricted()]),
        carried={"correlation": "abc"},
        pool_seeds=DEFAULT_POOL_SEEDS.extend(correlation=seed),
    )

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert seen == [("task", "abc")]


def test_the_arguments_are_the_row_s_params(make_run: Callable[..., SpecRun]) -> None:
    run = make_run(ServiceSpec(service=lambda: None, permissions=[Unrestricted()]), params={"a": 1})

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert "a" in run.refusal_detail["errors"]


def test_a_spec_source_is_compiled_before_its_declaration_is_read(
    make_run: Callable[..., SpecRun],
) -> None:
    # Fails reading ``atomic`` off what was registered: a source has no such field, and the
    # outer transaction would silently not open, leaving the note behind the failure.
    class Raises(Presenter):
        def output(self) -> Any:
            return None

        def present(self, value: Any) -> Any:
            raise ValueError("after the service")

    def service() -> str:
        Note.objects.create(text="the service's work")
        return "done"

    compiled = ServiceSpec(service=service, presenter=Raises(), permissions=[Unrestricted()])

    class Source:
        def as_kernel_spec(self) -> ServiceSpec:
            return compiled

    run = make_run(Source())

    run_spec(str(run.pk))

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert Note.objects.count() == 0
