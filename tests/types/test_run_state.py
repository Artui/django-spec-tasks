from __future__ import annotations

from django_spec_tasks.types.run_state import RunState
from django_spec_tasks.types.terminal_states import TERMINAL_STATES


def test_only_pending_and_running_are_live() -> None:
    assert set(RunState) - TERMINAL_STATES == {RunState.PENDING, RunState.RUNNING}


def test_the_table_is_whole() -> None:
    assert {state.name for state in RunState} == {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "INVALID",
        "AUTHORIZATION_LAPSED",
        "CONFLICT",
        "INPUT_REQUIRED",
        "REFUSED",
        "SPEC_UNAVAILABLE",
        "OUTPUT_TOO_LARGE",
        "ABANDONED",
        "FAILED",
    }


def test_a_state_read_back_as_a_plain_string_is_in_the_terminal_set() -> None:
    # A row hands its state back as ``str``, and that is what callers test membership with.
    assert str(RunState.SUCCEEDED.value) in TERMINAL_STATES
    assert str(RunState.RUNNING.value) not in TERMINAL_STATES
