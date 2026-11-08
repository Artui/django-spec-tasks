"""What the caller's half is tested against: real specs, a recording backend, real users."""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    Parameter,
    Parameters,
    PermissionCheck,
    PoolSeeds,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_tasks.binding.task_binding import TaskBinding
from tests.testapp.models import Note


class RecordingBackend:
    """A queue that remembers what it was asked. Its shape is the protocol's, one method."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def enqueue(self, run_id: str) -> None:
        self.sent.append(run_id)


class StaffOnly(PermissionCheck):
    """Admits staff, and records the context it was asked with."""

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def has_permission(self, principal: Any, spec: Any, call_context: dict[str, Any]) -> bool:
        self.contexts.append(call_context)
        return bool(getattr(principal, "is_staff", False))


def _service(**_: Any) -> None:
    return None


def _write_note(*, order: str, **_: Any) -> None:
    Note.objects.create(text=f"refunded {order}")


# One parameter of each kind a refusal needs, and ``note`` declared ``any`` so the shape check
# lets a tuple or bytes through and the round trip is the check that answers.
_OPEN_PARAMETERS = Parameters.of(
    Parameter("order", "string", required=True),
    Parameter("count", "integer"),
    Parameter("mode", "string", choices=("fast", "slow")),
    Parameter("note", "any"),
)

STAFF_CHECK = StaffOnly()


def build_registry() -> SpecRegistry:
    registry = SpecRegistry()
    registry.register(
        "open",
        ServiceSpec(service=_service, permissions=[Unrestricted()], reads=_OPEN_PARAMETERS),
    )
    registry.register(
        "gated", ServiceSpec(service=_service, permissions=[STAFF_CHECK], reads=_OPEN_PARAMETERS)
    )
    registry.register(
        "upload",
        ServiceSpec(
            service=_service,
            permissions=[Unrestricted()],
            reads=Parameters.of(Parameter("attachment", "file")),
        ),
    )
    registry.register(
        "uploads",
        ServiceSpec(
            service=_service,
            permissions=[Unrestricted()],
            reads=Parameters.of(Parameter("attachments", "array", items="file")),
        ),
    )
    registry.register(
        "nested_upload",
        ServiceSpec(
            service=_service,
            permissions=[Unrestricted()],
            reads=Parameters.of(
                Parameter(
                    "rows", "array", items=Parameters.of(Parameter("doc", "file")), required=False
                ),
            ),
        ),
    )
    registry.register(
        "object_upload",
        ServiceSpec(
            service=_service,
            permissions=[Unrestricted()],
            reads=Parameters.of(
                Parameter("meta", "object", fields=Parameters.of(Parameter("doc", "file"))),
            ),
        ),
    )
    registry.register(
        "tenant_scoped",
        ServiceSpec(
            service=_service,
            permissions=[Unrestricted()],
            reads=Parameters.of(Parameter("tenant", "string")),
        ),
    )
    registry.register(
        "refund",
        ServiceSpec(
            service=_write_note,
            permissions=[Unrestricted()],
            reads=Parameters.of(Parameter("order", "string", required=True)),
        ),
    )
    return registry


@pytest.fixture
def backend() -> RecordingBackend:
    return RecordingBackend()


@pytest.fixture
def binding(backend: RecordingBackend) -> TaskBinding:
    return TaskBinding(name="enqueue-tests", registry=build_registry(), backend=backend)


@pytest.fixture
def tenant_seeds() -> PoolSeeds:
    return DEFAULT_POOL_SEEDS.extend(tenant=lambda **_: "acme")


@pytest.fixture
def user(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(username="ada")


@pytest.fixture
def staff(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(username="grace", is_staff=True)


@pytest.fixture
def deactivated(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(username="gone", is_active=False)
