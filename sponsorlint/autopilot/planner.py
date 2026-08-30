"""Report -> retake plan. A pure function over real verifier output.

The planner reads a `Report` the deterministic verifier produced and the `Spec`
a human approved, and writes one checklist item per unresolved finding. It has
no campaign knowledge: every value it prints is copied out of a `Rule` or a
`Result`. Grep this module for a brand, a percentage or a filename and you will
not find one — the sample campaign produces its own findings from its own data.

Three things the planner will never do:

  * change an expected value so a take would pass
  * invent a stronger claim to replace a prohibited one
  * mark a human's item as something a retake can close
"""

from __future__ import annotations

import re

from ..models import ManualReviewItem, Report, Result, Rule, Spec
from .models import PlanItem, RetakePlan

#: Statuses that put a finding on the checklist. PASS never appears.
OPEN_STATUSES: frozenset[str] = frozenset({"FAIL", "WARN", "MANUAL_REVIEW"})

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def build_plan(spec: Spec, report: Report, iteration: int) -> RetakePlan:
    """Every unresolved finding in `report`, as an evidence-linked action."""
    rules = {rule.id: rule for rule in spec.rules}
    items: list[PlanItem] = []

    for result in report.results:
        if result.status not in OPEN_STATUSES:
            continue
        rule = rules.get(result.rule_id)
        if rule is None:
            # A result whose rule is not in the bound spec is a binding fault,
            # not something to plan around. Surface it as human work.
            items.append(_orphan_item(result))
            continue
        items.append(_rule_item(rule, result))

    for item in report.manual_review:
        if not item.confirmed:
            items.append(_manual_item(item))

    return RetakePlan(
        iteration=iteration,
        from_status=report.status,
        from_score=report.score.fraction,
        items=items,
    )


# --------------------------------------------------------------------------
# rule findings
# --------------------------------------------------------------------------


def _rule_item(rule: Rule, result: Result) -> PlanItem:
    action, note, escalation = _guidance(rule, result)

    # The engine says MANUAL_REVIEW when it could not decide. That is the one
    # signal that a retake cannot close the finding on its own.
    requires_human = result.status == "MANUAL_REVIEW"
    if requires_human and not escalation:
        escalation = result.advisory or "The verifier could not complete this check."

    return PlanItem(
        kind="rule",
        rule_id=rule.id,
        rule_type=rule.type,
        finding_status=result.status,
        label=rule.label,
        finding_title=result.title,
        source_quote=result.source_quote,
        expected=result.expected,
        detected=result.detected,
        evidence=result.evidence,
        timestamp=result.timestamp,
        recommended_action=action,
        recording_note=note,
        requires_human=requires_human,
        escalation_reason=escalation,
    )


def _guidance(rule: Rule, result: Result) -> tuple[str, str | None, str | None]:
    """(recommended action, recording note, escalation reason) for one finding.

    Dispatch is on the rule family and nothing else.
    """
    if result.status == "MANUAL_REVIEW":
        return (
            "A human has to resolve this check before the run can finish. "
            "Autopilot will not turn an unresolved check into a pass.",
            None,
            result.advisory,
        )

    handler = _FAMILY.get(rule.type)
    if handler is None:  # unreachable while the schema holds
        return (
            f"Resolve the outstanding {rule.type} requirement and record the segment again.",
            None,
            None,
        )
    return handler(rule, result)


def _exact_value(rule: Rule, result: Result) -> tuple[str, str | None, str | None]:
    approved = rule.expected
    action = f"Re-record the line so it states the approved value exactly: {approved}."
    if result.detected and result.detected != approved:
        action += (
            f" This take says {result.detected}. A different number is a different"
            f" claim, so the approved value is what has to be spoken."
        )
    return (
        action,
        "Say the figure in full and unhurried. The approved value comes from the "
        "brief; Autopilot cannot change it to match a recording.",
        None,
    )


def _must_say(rule: Rule, result: Result) -> tuple[str, str | None, str | None]:
    required = _phrase_list(rule)
    return (
        f"Record the required wording clearly at least once inside the segment: {required}.",
        "Say it as the brief has it. A paraphrase or a near-synonym does not "
        "satisfy a required mention.",
        None,
    )


def _must_not_say(rule: Rule, result: Result) -> tuple[str, str | None, str | None]:
    # Prefer what the verifier actually detected; fall back to the approved list.
    offending = result.detected or _phrase_list(rule)
    return (
        f"Re-record the sentence without the prohibited claim {offending}.",
        "Drop the claim rather than trading it for a stronger one. Any "
        "replacement wording is a draft and needs sponsor approval before it is "
        "recorded — Autopilot does not write new claims.",
        None,
    )


def _url_or_cta(rule: Rule, result: Result) -> tuple[str, str | None, str | None]:
    action = f"State the approved destination exactly as written: {rule.expected}."
    if rule.within_last_seconds is not None:
        action += (
            f" Placement matters here — it has to land inside the final "
            f"{rule.within_last_seconds:g}s of the segment."
        )
    return (
        action,
        "Read the address as approved. A shortener, an alternate path or a "
        "spoken variation is a different destination.",
        None,
    )


def _must_disclose(rule: Rule, result: Result) -> tuple[str, str | None, str | None]:
    if rule.within_first_seconds is None:
        return (
            "Record the sponsorship disclosure the brief approved.",
            "The approved specification carries no placement number for this "
            "disclosure, so the check is presence-only. Setting a threshold is a "
            "human decision made on the review screen.",
            result.advisory,
        )
    return (
        f"Record the sponsorship disclosure inside the first "
        f"{rule.within_first_seconds:g}s of the segment.",
        "Use the disclosure wording the brief approved, and place it before the "
        "pitch rather than after it.",
        result.advisory,
    )


def _duration(rule: Rule, result: Result) -> tuple[str, str | None, str | None]:
    approved = result.expected or _window(rule)
    actual = _first_number(result.detected)

    if actual is not None and rule.min_seconds is not None and actual < rule.min_seconds:
        action = (
            f"The segment runs {result.detected}, under the approved minimum of "
            f"{rule.min_seconds:g}s. Record a longer read — roughly "
            f"{rule.min_seconds - actual:.1f}s more — to reach {approved}."
        )
    elif actual is not None and rule.max_seconds is not None and actual > rule.max_seconds:
        action = (
            f"The segment runs {result.detected}, over the approved maximum of "
            f"{rule.max_seconds:g}s. Tighten the read by roughly "
            f"{actual - rule.max_seconds:.1f}s to reach {approved}."
        )
    else:
        action = f"Bring the segment length inside the approved window of {approved}."

    return (
        action,
        "Length is a property of the recording. Rewriting words in a transcript "
        "does not change it — the segment has to be recorded or re-cut to length.",
        None,
    )


_FAMILY = {
    "EXACT_VALUE": _exact_value,
    "MUST_SAY": _must_say,
    "MUST_NOT_SAY": _must_not_say,
    "URL_OR_CTA": _url_or_cta,
    "MUST_DISCLOSE": _must_disclose,
    "DURATION": _duration,
}


# --------------------------------------------------------------------------
# human work
# --------------------------------------------------------------------------


def _manual_item(item: ManualReviewItem) -> PlanItem:
    return PlanItem(
        kind="manual_review",
        finding_status="MANUAL_REVIEW",
        label="Human check",
        source_quote=item.source_quote,
        recommended_action=(
            "Watch the actual cut and confirm this requirement yourself. "
            "Autopilot cannot see the picture and will not confirm it for you."
        ),
        requires_human=True,
        escalation_reason=item.reason,
    )


def _orphan_item(result: Result) -> PlanItem:
    return PlanItem(
        kind="rule",
        rule_id=result.rule_id,
        rule_type=result.rule_type,
        finding_status=result.status,
        label=result.title,
        finding_title=result.title,
        source_quote=result.source_quote,
        expected=result.expected,
        detected=result.detected,
        evidence=result.evidence,
        timestamp=result.timestamp,
        recommended_action=(
            "This finding cites a rule that is not in the specification bound to "
            "this run. Re-approve the specification and start a new run."
        ),
        requires_human=True,
        escalation_reason="Finding does not match the bound specification.",
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _phrase_list(rule: Rule) -> str:
    phrases = rule.phrases or []
    return ", ".join(f'"{phrase}"' for phrase in phrases) or f'the wording in "{rule.label}"'


def _window(rule: Rule) -> str:
    lo, hi = rule.min_seconds, rule.max_seconds
    if lo is not None and hi is not None:
        return f"{lo:g}–{hi:g}s"
    if lo is not None:
        return f"at least {lo:g}s"
    return f"at most {hi:g}s"


def _first_number(text: str | None) -> float | None:
    if not text:
        return None
    match = _NUMBER.search(text)
    return float(match.group(0)) if match else None
