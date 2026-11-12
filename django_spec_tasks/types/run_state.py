from __future__ import annotations

from django.db import models


class RunState(models.TextChoices):
    """Where a run is. ``PENDING`` and ``RUNNING`` are live; the rest are terminal.

    A state stays coarse: the row's ``refusal_class`` and ``refusal_code`` say which
    refusal ended a run. The whole table lands in the first release, because a state
    added later is missing from every run before it.

    Each value spells its name, so a value read back off a row as a plain string
    compares equal and hashes equal, and ``run.state in TERMINAL_STATES`` holds.
    """

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
