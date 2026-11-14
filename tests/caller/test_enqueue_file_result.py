from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django_service_specs import FilePresenter, SelectorKind, SelectorSpec, Unrestricted

from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.caller.enqueue import enqueue
from django_spec_tasks.models.spec_run import SpecRun
from tests.caller.conftest import RecordingBackend

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_file_spec_registered_after_the_binding_is_refused_at_enqueue(
    binding: TaskBinding, backend: RecordingBackend, user: Any
) -> None:
    binding.registry.register(
        "report",
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: b"%PDF",
            presenter=FilePresenter("application/pdf", filename="report.pdf"),
            permissions=[Unrestricted()],
        ),
    )
    with transaction.atomic(), pytest.raises(ImproperlyConfigured, match="'report'"):
        enqueue(binding, "report", {}, actor=user)
    assert not SpecRun.objects.exists()
    assert backend.sent == []
