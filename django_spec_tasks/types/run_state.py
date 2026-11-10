from __future__ import annotations

from django.db import models


class RunState(models.TextChoices):
    """Where a run is. ``PENDING`` and ``RUNNING`` are live; the rest are terminal."""

    PENDING = "PENDING", "Pending"
    RUNNING = "RUNNING", "Running"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    INVALID = "INVALID", "Invalid"
    AUTHORIZATION_LAPSED = "AUTHORIZATION_LAPSED", "Authorization lapsed"
    CONFLICT = "CONFLICT", "Conflict"
    INPUT_REQUIRED = "INPUT_REQUIRED", "Input required"
    REFUSED = "REFUSED", "Refused"
    SPEC_UNAVAILABLE = "SPEC_UNAVAILABLE", "Spec unavailable"
    OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE", "Output too large"
    ABANDONED = "ABANDONED", "Abandoned"
    FAILED = "FAILED", "Failed"
