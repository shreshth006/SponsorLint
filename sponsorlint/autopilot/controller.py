"""The bounded workflow controller. Decisions.md D25, README "SponsorLint Autopilot".

This module owns three things and nothing else:

  1. **Binding.** A run is frozen to the specification the original report used.
     Every later step re-checks the fingerprint, so an edited spec stops the run
     instead of quietly continuing under a different contract.
  2. **Transitions.** Each state change is performed here and appends exactly one
     trace event as it happens. There is no scripted trace and no place to write
     one — `_transition` is the only writer.
  3. **Bounds.** A run performs at most `max_iterations` verification cycles and
     then escalates. It cannot loop.

What this module deliberately cannot do: decide a verdict. Readiness arrives
from `lint.engine.run` already resolved and is copied, never recomputed. Search
this file for `SPONSOR_READY` and every occurrence is a comparison against what
the verifier returned.

Import discipline: standard library, pydantic and the demo-path modules only.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from ..lint.engine import run as run_verifier
from ..models import Report, Spec, Transcript
from ..report.render import report_context
from .models import AutopilotRun, TraceEvent, VerificationRecord
from .planner import build_plan

#: Verification cycles per run, counting the opening inspection. Two retakes.
MAX_ITERATIONS = 3

#: Statuses that keep a report below SPONSOR_READY and that a retake can act on.
BLOCKING_STATUSES: frozenset[str] = frozenset({"FAIL", "WARN"})


class AutopilotError(Exception):
    """A readable failure: what happened, and what to do about it."""


class SpecBindingError(AutopilotError):
    """The specification is not the one this run was bound to."""


# --------------------------------------------------------------------------
# binding
# --------------------------------------------------------------------------


def spec_fingerprint(spec: Spec) -> str:
    """A stable hash of everything a run is not allowed to change.

    Covers the campaign, every rule in full, and the text of every
    manual-review item. It deliberately excludes each item's `confirmed` flag:
    a human confirming a visual item is the one sanctioned change to a spec
    mid-run, and it must not read as tampering. Anything else — an edited
    expected value, a loosened threshold, a deleted rule — moves the hash.
    """
    payload = {
        "campaign": spec.campaign,
        "rules": [rule.model_dump(mode="json") for rule in spec.rules],
        "manual_review": [
            {"source_quote": item.source_quote, "reason": item.reason}
            for item in spec.manual_review
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_binding(run: AutopilotRun, spec: Spec | None) -> None:
    if spec is None:
        return
    if spec_fingerprint(spec) != run.spec_fingerprint:
        raise SpecBindingError(
            "The approved specification changed after this Autopilot run started. "
            "A run stays bound to the specification its first report used. "
            "Approve the edited specification and start a new run."
        )


# --------------------------------------------------------------------------
# transitions — the only writer of trace events
# --------------------------------------------------------------------------


def _transition(
    run: AutopilotRun,
    state: str,
    action: str,
    message: str,
    *,
    rule_ids: list[str] | None = None,
    detail: str | None = None,
) -> None:
    """Move the run and record that it moved. Called only after the work is done."""
    run.state = state
    run.trace.append(
        TraceEvent(
            seq=len(run.trace) + 1,
            state=state,
            action=action,
            message=message,
            iteration=run.iteration,
            rule_ids=list(rule_ids or []),
            detail=detail,
        )
    )


def _verdict_line(report: Report) -> str:
    s = report.summary
    return (
        f"{report.label} · {report.score.fraction} · "
        f"{s.fail} FAIL · {s.warn} WARN · {s.passed} PASS · "
        f"{s.manual_review} manual unresolved"
    )


def _open_rule_ids(report: Report) -> list[str]:
    return [r.rule_id for r in report.results if r.status in BLOCKING_STATUSES]


# --------------------------------------------------------------------------
# start
# --------------------------------------------------------------------------


def start_run(
    spec: Spec,
    transcript: Transcript,
    *,
    spec_id: str,
    source_report_id: str,
    source_report_status: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
    run_id: str | None = None,
) -> AutopilotRun:
    """Open a run against an existing report and write the first plan.

    Autopilot's opening move is to run the deterministic verifier itself, over
    the bound specification and the transcript the original report was built
    from. It inspects first-hand output rather than trusting a stored summary,
    which is also what catches a report produced under a different spec.
    """
    if not spec.rules:
        raise AutopilotError("No requirements to check. Approve a specification first.")

    run = AutopilotRun(
        run_id=run_id or uuid.uuid4().hex,
        spec_id=spec_id,
        campaign=spec.campaign,
        spec_fingerprint=spec_fingerprint(spec),
        spec=spec.model_copy(deep=True),
        max_iterations=max_iterations,
    )

    _transition(
        run,
        "INSPECTING",
        "bind_specification",
        f"Bound this run to the approved specification: {len(spec.rules)} rules, "
        f"{len(spec.manual_review)} manual-review item"
        f"{'s' if len(spec.manual_review) != 1 else ''}. The specification is frozen "
        f"for the life of the run.",
        rule_ids=[rule.id for rule in spec.rules],
        detail=f"fingerprint {run.spec_fingerprint[:12]}",
    )

    report = _verify(run, transcript)

    if source_report_status and report.status != source_report_status:
        raise SpecBindingError(
            f"Re-running the verifier on the bound specification produced "
            f"{report.status}, but the report Autopilot was started from says "
            f"{source_report_status}. The specification or the take changed "
            f"underneath the report. Verify the cut again and start a new run."
        )

    _transition(
        run,
        "INSPECTING",
        "run_verifier",
        f"Ran the deterministic verifier against the bound specification and the "
        f"transcript saved with report {source_report_id[:8]}.",
        rule_ids=[r.rule_id for r in report.results],
        detail=_verdict_line(report),
    )

    _record(run, report, take=transcript.source or "original take")
    _plan_and_resolve(run, report, opened_from=source_report_id)
    return run


# --------------------------------------------------------------------------
# retake
# --------------------------------------------------------------------------


def verify_retake(
    run: AutopilotRun,
    transcript: Transcript,
    *,
    take: str,
    live_spec: Spec | None = None,
) -> AutopilotRun:
    """Verify a genuinely new take and decide what happens next.

    `transcript` is whatever the creator actually supplied — a committed take or
    a fresh transcription of uploaded media. Autopilot did not produce it, does
    not edit it, and cannot distinguish a good one from a bad one except by
    handing it to the verifier.
    """
    if run.is_terminal:
        raise AutopilotError(
            f"This run finished in state {run.state} and accepts no further takes. "
            f"Start a new run from the current report."
        )
    if not run.accepts_retake():
        raise AutopilotError(
            f"This run is in state {run.state}, which does not accept a take. "
            f"A take is accepted only while the run is waiting for one."
        )
    _require_binding(run, live_spec)

    _transition(
        run,
        "VERIFYING_RETAKE",
        "receive_retake",
        f"Received a new take from the creator: {take}. "
        f"{len(transcript.segments)} segments, {transcript.duration_seconds:g}s. "
        f"Autopilot did not record, edit or transcribe this media.",
        detail=f"source {transcript.source or take}",
    )

    previous = run.history[-1] if run.history else None
    report = _verify(run, transcript)

    _transition(
        run,
        "VERIFYING_RETAKE",
        "run_verifier",
        "Ran the same deterministic verifier over the new take, against the same "
        "frozen specification.",
        rule_ids=[r.rule_id for r in report.results],
        detail=_verdict_line(report),
    )

    _record(run, report, take=take)

    if previous is not None:
        _transition(
            run,
            "VERIFYING_RETAKE",
            "compare_results",
            _comparison(previous, report, run),
            rule_ids=_open_rule_ids(report),
            detail=f"{previous.score} → {report.score.fraction}",
        )

    _plan_and_resolve(run, report)
    return run


def _comparison(previous: VerificationRecord, report: Report, run: AutopilotRun) -> str:
    still_open = len(_open_rule_ids(report))
    moved = "unchanged" if previous.status == report.status else (
        f"{previous.label} → {report.label}"
    )
    return (
        f"Compared iteration {previous.iteration} with iteration {run.iteration}: "
        f"score {previous.score} → {report.score.fraction}, readiness {moved}, "
        f"{still_open} blocking finding{'s' if still_open != 1 else ''} still open."
    )


# --------------------------------------------------------------------------
# human confirmation
# --------------------------------------------------------------------------


def confirm_manual_item(
    run: AutopilotRun,
    index: int,
    *,
    live_spec: Spec | None = None,
) -> AutopilotRun:
    """Record a human's confirmation of one manual-review item and re-verify.

    The confirmation is the human's, and this is the only way one enters a run.
    Autopilot never calls this on its own behalf: it is reachable exclusively
    from an operator action, and it re-runs the real verifier afterwards so the
    readiness that follows is the resolver's, not a substitution.
    """
    if run.is_terminal:
        raise AutopilotError(
            f"This run finished in state {run.state}. Nothing further can be confirmed on it."
        )
    _require_binding(run, live_spec)

    items = run.spec.manual_review
    if isinstance(index, bool) or not isinstance(index, int):
        raise AutopilotError("Choose a valid manual-review item.")
    if index < 0 or index >= len(items):
        raise AutopilotError("That manual-review item does not exist.")
    if items[index].confirmed:
        raise AutopilotError("That manual-review item is already confirmed.")
    if run.latest_transcript is None:
        raise AutopilotError(
            "This run has no verified take to re-check. Verify a take before "
            "confirming a manual item."
        )

    items[index].confirmed = True

    _transition(
        run,
        "VERIFYING_RETAKE",
        "confirm_manual_item",
        f"A human confirmed manual-review item {index + 1} of {len(items)} after "
        f"inspecting the cut. Autopilot cannot make this call and did not make it.",
        detail=items[index].source_quote,
    )

    report = _verify(run, run.latest_transcript)

    _transition(
        run,
        "VERIFYING_RETAKE",
        "run_verifier",
        "Re-ran the deterministic verifier over the same take with the "
        "confirmation recorded. Readiness is the resolver's answer, not a "
        "consequence of the confirmation itself.",
        detail=_verdict_line(report),
    )

    _record(run, report, take=run.history[-1].take if run.history else "current take")
    _plan_and_resolve(run, report)
    return run


# --------------------------------------------------------------------------
# stop
# --------------------------------------------------------------------------


def stop_run(run: AutopilotRun, reason: str = "Stopped by the operator.") -> AutopilotRun:
    """End a run on request. A stopped run is never a passing run."""
    if run.is_terminal:
        return run
    run.finished_reason = reason
    _transition(run, "STOPPED", "stop", reason)
    return run


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------


def _verify(run: AutopilotRun, transcript: Transcript) -> Report:
    """The one call that produces a verdict. Nothing else in Autopilot may.

    The judged take is remembered so a later human confirmation re-runs the
    verifier over the same media rather than over anything supplied afterwards.
    """
    run.latest_transcript = transcript
    return run_verifier(run.spec, transcript)


def _record(run: AutopilotRun, report: Report, *, take: str) -> None:
    """Copy the verifier's answer onto the run. No field is recomputed here."""
    report_id = uuid.uuid4().hex
    run.latest_report_id = report_id
    run.latest_report = report_context(report)
    run.history.append(
        VerificationRecord(
            iteration=run.iteration,
            take=take,
            status=report.status,
            label=report.label,
            score=report.score.fraction,
            report_id=report_id,
        )
    )


def _plan_and_resolve(
    run: AutopilotRun, report: Report, *, opened_from: str | None = None
) -> None:
    """Write the plan for this report, then choose the next state.

    The branch order is the product's whole safety argument:

        verifier says ready  -> COMPLETE
        blocking findings    -> another bounded iteration, or ESCALATED
        only human work left -> NEEDS_HUMAN_REVIEW
    """
    plan = build_plan(run.spec, report, run.iteration)
    run.plan = plan

    _transition(
        run,
        "PLANNING",
        "build_retake_plan",
        f"Wrote {len(plan.items)} plan item"
        f"{'s' if len(plan.items) != 1 else ''} from the verifier's findings"
        + (f", opened from report {opened_from[:8]}" if opened_from else "")
        + ". Every item cites its rule and the sentence of the brief it came from.",
        rule_ids=[item.rule_id for item in plan.items if item.rule_id],
        detail=plan.summary(),
    )

    # SPONSOR_READY is never assembled here. It is read off the report the
    # deterministic resolver returned, and it is the only route to COMPLETE.
    if report.status == "SPONSOR_READY":
        run.finished_reason = (
            f"The deterministic verifier resolved {report.label} on iteration "
            f"{run.iteration} ({report.score.fraction})."
        )
        _transition(
            run,
            "COMPLETE",
            "finish",
            f"Finished: the verifier resolved {report.label} on its own. "
            f"{report.score.fraction} automated requirements passed and every "
            f"manual item was confirmed by a human.",
            detail=_verdict_line(report),
        )
        return

    blocking = _open_rule_ids(report)

    if blocking:
        if run.iteration >= run.max_iterations:
            run.finished_reason = (
                f"Iteration limit reached: {run.max_iterations} verification "
                f"cycles with {len(blocking)} finding"
                f"{'s' if len(blocking) != 1 else ''} still open."
            )
            _transition(
                run,
                "ESCALATED",
                "escalate",
                f"Stopping at the iteration limit of {run.max_iterations}. "
                f"{len(blocking)} finding{'s' if len(blocking) != 1 else ''} did not "
                f"close across the run — this needs a person, not another loop.",
                rule_ids=blocking,
                detail=_verdict_line(report),
            )
            return

        run.iteration += 1
        _transition(
            run,
            "WAITING_FOR_RETAKE",
            "await_retake",
            f"Waiting for take {run.iteration} of at most {run.max_iterations}. "
            f"Autopilot does not record or edit media — the next step is the "
            f"creator's, and nothing advances until a real take arrives.",
            rule_ids=blocking,
            detail=plan.summary(),
        )
        return

    # No blocking finding, but the verifier did not resolve SPONSOR_READY.
    outstanding = plan.human_items
    run.finished_reason = None
    _transition(
        run,
        "NEEDS_HUMAN_REVIEW",
        "escalate_to_human",
        f"Every automated requirement the verifier can decide has passed "
        f"({report.score.fraction}), and readiness is {report.label}. "
        f"{len(outstanding)} item{'s' if len(outstanding) != 1 else ''} can only be "
        f"closed by a person inspecting the cut. Autopilot stops here.",
        rule_ids=[item.rule_id for item in outstanding if item.rule_id],
        detail=_verdict_line(report),
    )
