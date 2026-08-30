"""Autopilot data contracts.

Same discipline as `sponsorlint/models.py`: every boundary parses into one of
these, and nothing downstream re-derives a verdict. The verdict fields here are
copies of what the deterministic verifier already decided.

Import discipline: pydantic only. This module is on the zero-key demo path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import RuleType, Spec, Status, Transcript

# --------------------------------------------------------------------------
# States
# --------------------------------------------------------------------------

AgentState = Literal[
    "INSPECTING",
    "PLANNING",
    "WAITING_FOR_RETAKE",
    "VERIFYING_RETAKE",
    "NEEDS_HUMAN_REVIEW",
    "COMPLETE",
    "ESCALATED",
    "STOPPED",
]

#: States from which the run accepts no further work.
#:
#: `NEEDS_HUMAN_REVIEW` is deliberately absent: it is a *pause*, not an end. A
#: human can still confirm the outstanding visual item, and only the verifier's
#: rerun decides what happens next.
TERMINAL_STATES: frozenset[str] = frozenset({"COMPLETE", "ESCALATED", "STOPPED"})

#: States in which a new take may be submitted.
ACCEPTS_RETAKE: frozenset[str] = frozenset({"WAITING_FOR_RETAKE", "NEEDS_HUMAN_REVIEW"})

STATE_LABEL: dict[str, str] = {
    "INSPECTING": "Inspecting report",
    "PLANNING": "Planning retake",
    "WAITING_FOR_RETAKE": "Waiting for a new take",
    "VERIFYING_RETAKE": "Verifying the new take",
    "NEEDS_HUMAN_REVIEW": "Needs human review",
    "COMPLETE": "Complete",
    "ESCALATED": "Escalated",
    "STOPPED": "Stopped",
}

#: What a plan item is anchored to. A rule finding can be closed by re-recording;
#: a spec-level manual-review item never can.
ItemKind = Literal["rule", "manual_review"]


# --------------------------------------------------------------------------
# Trace
# --------------------------------------------------------------------------


class TraceEvent(BaseModel):
    """One executed transition.

    Every field is written by the controller at the moment the transition
    happens. Nothing here is scripted ahead of time, and the UI renders only
    what the backend appended.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int
    state: AgentState
    action: str
    message: str
    iteration: int
    rule_ids: list[str] = Field(default_factory=list)
    detail: str | None = None


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


class PlanItem(BaseModel):
    """One recommended action, anchored to a real finding.

    `source_quote`, `expected`, `detected` and `evidence` are carried over from
    the verifier's own `Result` and the approved `Rule`. Autopilot never edits
    an expected value — restating it is the entire point.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ItemKind
    finding_status: Status
    label: str
    source_quote: str
    recommended_action: str
    requires_human: bool

    rule_id: str | None = None
    rule_type: RuleType | None = None
    #: The verifier's own one-line finding title, carried over unchanged.
    finding_title: str | None = None
    expected: str | None = None
    detected: str | None = None
    evidence: str | None = None
    timestamp: float | None = None
    #: Conservative recording guidance. Never a stronger claim than the brief.
    recording_note: str | None = None
    #: Set when the item cannot be closed by re-recording alone.
    escalation_reason: str | None = None


class RetakePlan(BaseModel):
    """The checklist produced from one report."""

    model_config = ConfigDict(extra="forbid")

    iteration: int
    from_status: str
    from_score: str
    items: list[PlanItem] = Field(default_factory=list)

    @property
    def retake_items(self) -> list[PlanItem]:
        """Items a new recording can close."""
        return [item for item in self.items if not item.requires_human]

    @property
    def human_items(self) -> list[PlanItem]:
        return [item for item in self.items if item.requires_human]

    def summary(self) -> str:
        retake, human = len(self.retake_items), len(self.human_items)
        if not self.items:
            return "Nothing outstanding."
        parts = []
        if retake:
            parts.append(f"{retake} fixable by re-recording")
        if human:
            parts.append(f"{human} requiring a human")
        return " · ".join(parts)


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


class VerificationRecord(BaseModel):
    """What one verifier invocation produced. Copied, never recomputed."""

    model_config = ConfigDict(extra="forbid")

    iteration: int
    take: str
    status: str
    label: str
    score: str
    report_id: str


class AutopilotRun(BaseModel):
    """A bounded run, frozen to the specification the first report used.

    `spec` is the run's own deep copy. Editing the spec that lives in the web
    layer's session store cannot reach in here, and the fingerprint check on
    every reverify catches it if someone tries.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    spec_id: str
    campaign: str | None
    spec_fingerprint: str
    spec: Spec

    state: AgentState = "INSPECTING"
    iteration: int = 1
    max_iterations: int
    trace: list[TraceEvent] = Field(default_factory=list)
    plan: RetakePlan | None = None
    history: list[VerificationRecord] = Field(default_factory=list)

    latest_report_id: str | None = None
    latest_report: dict | None = None
    finished_reason: str | None = None

    #: The take the most recent verdict was reached on. Kept so a human
    #: confirming a manual item re-runs the verifier over *that* take rather
    #: than over anything the browser might supply. Never leaves the server.
    latest_transcript: Transcript | None = None

    @property
    def state_label(self) -> str:
        return STATE_LABEL[self.state]

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def accepts_retake(self) -> bool:
        return self.state in ACCEPTS_RETAKE

    def view(self) -> dict:
        """The client's picture of the run.

        The frozen spec stays server-side; the browser gets the fingerprint so
        it can show that the run is still bound to the specification the
        original report used, without being handed something it could edit and
        send back.
        """
        return {
            "run_id": self.run_id,
            "spec_id": self.spec_id,
            "campaign": self.campaign,
            "spec_fingerprint": self.spec_fingerprint,
            "state": self.state,
            "state_label": self.state_label,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "is_terminal": self.is_terminal,
            "accepts_retake": self.accepts_retake(),
            "finished_reason": self.finished_reason,
            "trace": [event.model_dump() for event in self.trace],
            "plan": (
                {
                    **self.plan.model_dump(),
                    "summary": self.plan.summary(),
                    "retake_count": len(self.plan.retake_items),
                    "human_count": len(self.plan.human_items),
                }
                if self.plan
                else None
            ),
            "history": [record.model_dump() for record in self.history],
            "latest_report_id": self.latest_report_id,
            "latest_report": self.latest_report,
        }
