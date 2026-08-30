"""Bounded retake workflow. Autopilot plans; humans record; the verifier judges.

Autopilot is a *workflow controller*, not a second verifier and not an
autonomous agent. It has exactly four powers:

    inspect a Report · write a retake plan · invoke `lint.engine.run` ·
    decide whether to continue, escalate or stop

It cannot edit the approved spec, cannot decide a verdict, cannot confirm a
manual-review item on a human's behalf, and cannot reach `SPONSOR_READY` by any
route other than the existing deterministic resolver returning it.

Import discipline: this package is on the zero-key demo path. Standard library
and pydantic only.
"""

from .models import (
    AgentState,
    AutopilotRun,
    PlanItem,
    RetakePlan,
    TraceEvent,
)
from .controller import (
    AutopilotError,
    MAX_ITERATIONS,
    SpecBindingError,
    confirm_manual_item,
    spec_fingerprint,
    start_run,
    stop_run,
    verify_retake,
)
from .planner import build_plan

__all__ = [
    "AgentState",
    "AutopilotError",
    "AutopilotRun",
    "MAX_ITERATIONS",
    "PlanItem",
    "RetakePlan",
    "SpecBindingError",
    "TraceEvent",
    "build_plan",
    "confirm_manual_item",
    "spec_fingerprint",
    "start_run",
    "stop_run",
    "verify_retake",
]
