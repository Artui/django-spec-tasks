from __future__ import annotations

import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

from django_spec_tasks.types.run_state import RunState


class SpecRun(models.Model):
    """One call of a spec: what was asked, whose claim it is under, and how it ended."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    binding = models.CharField(max_length=255)
    spec_name = models.CharField(max_length=255)
    params = models.JSONField()
    actor_pk = models.CharField(max_length=255, null=True, blank=True)
    carried = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=32, choices=RunState.choices, default=RunState.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    progress = models.FloatField(null=True, blank=True)
    progress_message = models.TextField(null=True, blank=True)
    kind = models.CharField(max_length=32, null=True, blank=True)
    output = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    instance_pk = models.CharField(max_length=255, null=True, blank=True)
    package_version = models.CharField(max_length=64, blank=True, default="")
    refusal_class = models.CharField(max_length=255, null=True, blank=True)
    refusal_code = models.CharField(max_length=255, null=True, blank=True)
    refusal_detail = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    traceback = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # When a backend was last asked for this row, so a sweep measures a binding's grace from
    # that ask and a backed-up queue is not sent a message on every sweep.
    asked_at = models.DateTimeField(default=timezone.now, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "spec run"
        verbose_name_plural = "spec runs"
        indexes = [
            models.Index(fields=["state", "lease_expires_at"], name="spec_tasks_state_lease_idx"),
            models.Index(fields=["state", "asked_at"], name="spec_tasks_state_asked_idx"),
            models.Index(fields=["state", "finished_at"], name="spec_tasks_state_finished_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.binding}.{self.spec_name} {self.pk} ({self.state})"
