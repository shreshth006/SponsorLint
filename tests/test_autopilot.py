"""The bounded retake loop, and the things it must never be able to do.

The interesting assertions in this file are the negative ones. Autopilot is a
workflow controller wired between a human and a deterministic verifier, so most
of its value is in the routes it does *not* have: it cannot edit the approved
spec, cannot soften a finding, cannot confirm a human's visual check, and cannot
reach SPONSOR READY except by the existing resolver returning it.
"""

import ast
import json
from pathlib import Path

import pytest

from sponsorlint.autopilot import (
    AutopilotError,
    MAX_ITERATIONS,
    SpecBindingError,
    build_plan,
    confirm_manual_item,
    spec_fingerprint,
    start_run,
    stop_run,
    verify_retake,
)
from sponsorlint.autopilot.models import TERMINAL_STATES
from sponsorlint.lint.engine import run as run_verifier
from sponsorlint.models import Spec, Transcript

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
PACKAGE = ROOT / "sponsorlint" / "autopilot"


# --------------------------------------------------------------------------
# fixtures — the committed campaign, loaded the same way the app loads it
# --------------------------------------------------------------------------


def load_spec() -> Spec:
    return Spec.model_validate(json.loads((SAMPLES / "spec.approved.json").read_text(encoding="utf-8")))


def load_take(name: str) -> Transcript:
    return Transcript.model_validate(
        json.loads((SAMPLES / f"transcript.{name}.json").read_text(encoding="utf-8"))
    )


def opened_on_v1(**kwargs):
    return start_run(load_spec(), load_take("v1"), spec_id="s", source_report_id="report01", **kwargs)


def degrade(transcript: Transcript, *, drop: str) -> Transcript:
    """A take that still fails one requirement, built by removing a phrase.

    This is a *test fixture*, not a product capability: the tests need a second
    and third failing take, and the sample campaign only ships two.
    """
    data = transcript.model_dump()
    for segment in data["segments"]:
        segment["text"] = segment["text"].replace(drop, "").strip() or "and so on."
    data["source"] = f"degraded-{drop}"
    return Transcript.model_validate(data)


@pytest.fixture
def spec():
    return load_spec()


@pytest.fixture
def v1():
    return load_take("v1")


@pytest.fixture
def v3():
    return load_take("v3")


# --------------------------------------------------------------------------
# 1 · the plan comes from the report, and the report comes from the verifier
# --------------------------------------------------------------------------


def test_the_plan_is_built_from_v1s_actual_failures(spec, v1):
    report = run_verifier(spec, v1)
    assert report.status == "DO_NOT_SEND"

    plan = build_plan(spec, report, iteration=1)

    failed = {r.rule_id for r in report.results if r.status == "FAIL"}
    planned = {item.rule_id for item in plan.items if item.rule_id}
    assert planned == failed, "every failure, and nothing that passed, reaches the checklist"

    passed = {r.rule_id for r in report.results if r.status == "PASS"}
    assert planned.isdisjoint(passed)
    assert plan.from_status == report.status
    assert plan.from_score == report.score.fraction


def test_a_clean_report_produces_an_empty_retake_list(spec, v3):
    report = run_verifier(spec, v3)
    plan = build_plan(spec, report, iteration=1)
    assert [item.rule_id for item in plan.items if item.rule_id] == []
    assert plan.retake_items == [], "nothing to re-record when every rule passed"
    assert len(plan.human_items) == 1, "the unconfirmed visual item is still carried"


# --------------------------------------------------------------------------
# 2 · approved values and source quotes survive the trip unchanged
# --------------------------------------------------------------------------


def test_expected_values_and_source_quotes_are_carried_over_verbatim(spec, v1):
    report = run_verifier(spec, v1)
    plan = build_plan(spec, report, iteration=1)
    findings = {r.rule_id: r for r in report.results}
    rules = {r.id: r for r in spec.rules}

    for item in plan.items:
        if item.kind != "rule":
            continue
        finding = findings[item.rule_id]
        assert item.expected == finding.expected
        assert item.detected == finding.detected
        assert item.evidence == finding.evidence
        assert item.source_quote == finding.source_quote == rules[item.rule_id].source_quote


def test_the_approved_value_is_restated_not_replaced(spec, v1):
    """The failure mode this guards: quietly planning toward what was recorded."""
    plan = build_plan(spec, run_verifier(spec, v1), iteration=1)
    value = next(item for item in plan.items if item.rule_id == "r3")
    approved = next(rule for rule in spec.rules if rule.id == "r3").expected

    assert value.expected == approved
    assert approved in value.recommended_action
    assert value.detected is not None and value.detected != approved
    assert value.detected != value.expected


def test_a_prohibited_claim_is_removed_not_traded_for_a_stronger_one(spec, v1):
    plan = build_plan(spec, run_verifier(spec, v1), iteration=1)
    prohibited = next(item for item in plan.items if item.rule_id == "r6")
    guidance = f"{prohibited.recommended_action} {prohibited.recording_note}"
    assert "draft" in guidance.lower() and "approval" in guidance.lower()


def test_duration_guidance_never_claims_text_can_change_a_runtime(spec):
    """A DURATION miss is a property of the recording, and must read that way."""
    short = Transcript.model_validate(
        {"duration_seconds": 20.0, "segments": [{"start": 0, "end": 19, "text": "too short."}],
         "source": "short.mp4"}
    )
    report = run_verifier(spec, short)
    item = next(i for i in build_plan(spec, report, 1).items if i.rule_id == "r2")
    assert "60" in item.recommended_action and "90" in item.recommended_action
    assert "recorded" in (item.recording_note or "").lower()


# --------------------------------------------------------------------------
# 3 · the approved specification is out of reach
# --------------------------------------------------------------------------


def test_a_run_holds_its_own_copy_of_the_spec(spec, v1):
    run = start_run(spec, v1, spec_id="s", source_report_id="report01")
    before = spec_fingerprint(run.spec)

    spec.rules[2].expected = "70%"          # someone edits the session's spec
    spec.rules.pop()                        # ...and deletes a requirement

    assert spec_fingerprint(run.spec) == before, "the run's spec did not move"
    assert run.spec.rules[2].expected == "73%"
    assert len(run.spec.rules) == 7


def test_planning_and_verifying_leave_the_bound_spec_untouched(spec, v1, v3):
    run = start_run(spec, v1, spec_id="s", source_report_id="report01")
    frozen = run.spec.model_dump(mode="json")
    verify_retake(run, v3, take="v3")
    after = run.spec.model_dump(mode="json")

    # The one sanctioned change is a human confirming a visual item, and no step
    # above is a human confirming anything.
    assert after == frozen


def test_an_edited_specification_stops_the_run_instead_of_continuing(spec, v1, v3):
    run = start_run(spec, v1, spec_id="s", source_report_id="report01")
    tampered = load_spec()
    tampered.rules[2].expected = "70%"      # match the recording instead of the brief

    with pytest.raises(SpecBindingError) as caught:
        verify_retake(run, v3, take="v3", live_spec=tampered)
    assert "start a new run" in str(caught.value)
    assert run.state == "WAITING_FOR_RETAKE", "the run did not advance"


def test_confirming_a_visual_item_is_not_treated_as_tampering(spec, v1, v3):
    """The fingerprint must ignore `confirmed`, or the sanctioned path breaks."""
    run = start_run(spec, v1, spec_id="s", source_report_id="report01")
    verify_retake(run, v3, take="v3")

    live = load_spec()
    live.manual_review[0].confirmed = True
    assert spec_fingerprint(live) == run.spec_fingerprint

    live.rules[0].within_first_seconds = 999
    assert spec_fingerprint(live) != run.spec_fingerprint


# --------------------------------------------------------------------------
# 4 · no campaign knowledge in the core
# --------------------------------------------------------------------------

#: Values that belong to the sample campaign and nowhere in the agent.
CAMPAIGN_TOKENS = (
    "aegis", "shield mode", "73%", "70%", "aegisvpn",
    "transcript.v1", "transcript.v3", "sponsor-cut",
)


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_the_agent_carries_no_campaign_specific_knowledge(path):
    text = path.read_text(encoding="utf-8").lower()
    offenders = [token for token in CAMPAIGN_TOKENS if token in text]
    assert offenders == [], (
        f"{path.name} mentions {offenders}. The sample campaign has to produce "
        f"its findings from its own data, or the demo proves nothing."
    )


def test_the_agent_works_on_a_campaign_it_has_never_seen():
    """A different brief, different rule types, different failures."""
    other = Spec.model_validate({
        "campaign": "Kettle Coffee - Launch",
        "rules": [
            {"id": "k1", "type": "MUST_SAY", "label": "Roast name",
             "source_quote": "Say Midnight Roast at least once.",
             "phrases": ["Midnight Roast"]},
            {"id": "k2", "type": "EXACT_VALUE", "label": "Discount",
             "source_quote": "Tell viewers the discount is forty percent.",
             "expected": "40%"},
        ],
        "manual_review": [],
    })
    take = Transcript.model_validate({
        "duration_seconds": 30.0,
        "segments": [{"start": 0, "end": 8, "text": "Use my link for twenty percent off the beans."}],
        "source": "kettle-take-1.mp4",
    })

    run = start_run(other, take, spec_id="k", source_report_id="kettle001")
    assert run.state == "WAITING_FOR_RETAKE"
    planned = {item.rule_id for item in run.plan.items if item.rule_id}
    assert planned == {"k1", "k2"}
    assert "40%" in next(i for i in run.plan.items if i.rule_id == "k2").recommended_action
    assert "Midnight Roast" in next(i for i in run.plan.items if i.rule_id == "k1").recommended_action


# --------------------------------------------------------------------------
# 5 · the trace is a record of transitions, not a script
# --------------------------------------------------------------------------


def test_every_trace_event_records_the_state_the_run_moved_into(spec, v1, v3):
    run = start_run(spec, v1, spec_id="s", source_report_id="report01")
    verify_retake(run, v3, take="v3")
    confirm_manual_item(run, 0)

    assert [event.seq for event in run.trace] == list(range(1, len(run.trace) + 1))
    assert run.trace[-1].state == run.state
    for event in run.trace:
        assert event.message.strip()
        assert 1 <= event.iteration <= run.max_iterations

    # The states actually visited, in order, with no state appearing before the
    # controller could have reached it.
    visited = [event.state for event in run.trace]
    assert visited[0] == "INSPECTING"
    assert "WAITING_FOR_RETAKE" in visited
    assert visited.index("WAITING_FOR_RETAKE") < visited.index("VERIFYING_RETAKE")
    assert visited.index("NEEDS_HUMAN_REVIEW") < visited.index("COMPLETE")
    assert visited[-1] == "COMPLETE"


def test_a_run_that_never_ran_has_no_trace_to_show(spec):
    empty = Spec(campaign="Nothing approved", rules=[], manual_review=[])
    with pytest.raises(AutopilotError):
        start_run(empty, load_take("v1"), spec_id="s", source_report_id="report01")


def test_only_the_transition_helper_can_append_a_trace_event():
    """Structural, on purpose: a second writer is how fake traces get in."""
    source = (PACKAGE / "controller.py").read_text(encoding="utf-8")
    assert source.count("run.trace.append") == 1

    tree = ast.parse(source)
    writers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and "run.trace.append" in ast.get_source_segment(source, node)
    }
    assert writers == {"_transition"}


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_the_agent_never_assigns_a_readiness_verdict(path):
    """`SPONSOR_READY` may be compared against. It may never be assigned."""
    source = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            rendered = ast.get_source_segment(source, node) or ""
            assert "SPONSOR_READY" not in rendered, (
                f"{path.name} assigns a readiness value: {rendered!r}. Readiness "
                f"is the deterministic resolver's answer, copied and never made."
            )


# --------------------------------------------------------------------------
# 6 · the agent waits for a real take
# --------------------------------------------------------------------------


def test_a_failing_report_leaves_the_agent_waiting_for_a_retake(spec, v1):
    run = opened_on_v1()
    assert run.state == "WAITING_FOR_RETAKE"
    assert run.accepts_retake() is True
    assert run.is_terminal is False
    assert run.iteration == 2, "the next take is iteration two"
    assert run.trace[-1].action == "await_retake"
    assert "does not record or edit media" in run.trace[-1].message


def test_the_agent_cannot_advance_itself_without_a_take(spec, v1):
    run = opened_on_v1()
    trace_length = len(run.trace)
    history = list(run.history)
    # There is no `step()`, no `tick()`, no background task: the only way on is
    # a call that carries a transcript somebody supplied.
    assert not hasattr(run, "step")
    assert len(run.trace) == trace_length and run.history == history


# --------------------------------------------------------------------------
# 7 & 8 · the retake goes through the real verifier, and is not hardcoded
# --------------------------------------------------------------------------


def test_the_retake_is_judged_by_the_existing_verifier(spec, v1, v3):
    run = opened_on_v1()
    verify_retake(run, v3, take="v3")

    direct = run_verifier(load_spec(), v3)
    assert run.history[-1].status == direct.status
    assert run.history[-1].score == direct.score.fraction
    assert run.latest_report["status"] == direct.status
    assert [r["rule_id"] for r in run.latest_report["results"]] == [
        r.rule_id for r in direct.results
    ]
    assert [r["status"] for r in run.latest_report["results"]] == [
        r.status for r in direct.results
    ]


def test_the_verifier_is_invoked_once_per_take(spec, v1, v3, monkeypatch):
    calls = []
    import sponsorlint.autopilot.controller as controller

    real = controller.run_verifier
    monkeypatch.setattr(
        controller, "run_verifier",
        lambda s, t: (calls.append(t.source), real(s, t))[1],
    )
    run = opened_on_v1()
    assert calls == [v1.source]
    verify_retake(run, v3, take="v3")
    assert calls == [v1.source, v3.source]


def test_the_corrected_takes_result_is_computed_not_assumed(spec, v1, v3):
    """Feed a take that is *not* the corrected one and watch the answer change."""
    still_broken = degrade(v3, drop="Shield Mode")

    good = opened_on_v1()
    verify_retake(good, v3, take="corrected")
    assert good.latest_report["status"] == "REVIEW"
    assert good.state == "NEEDS_HUMAN_REVIEW"

    bad = opened_on_v1()
    verify_retake(bad, still_broken, take="also-broken")
    assert bad.latest_report["status"] == "DO_NOT_SEND"
    assert bad.state == "WAITING_FOR_RETAKE"
    assert "r5" in {item.rule_id for item in bad.plan.items}


def test_an_identical_retake_does_not_improve_the_verdict(spec, v1):
    run = opened_on_v1()
    verify_retake(run, load_take("v1"), take="same-take-again")
    assert run.history[-1].status == "DO_NOT_SEND"
    assert run.history[-1].score == run.history[0].score
    assert run.state == "WAITING_FOR_RETAKE"


# --------------------------------------------------------------------------
# 9 & 10 · the human check cannot be closed by the agent
# --------------------------------------------------------------------------


def test_an_unresolved_visual_item_stops_the_run_short_of_complete(spec, v1, v3):
    run = opened_on_v1()
    verify_retake(run, v3, take="v3")

    assert run.state == "NEEDS_HUMAN_REVIEW"
    assert run.state not in TERMINAL_STATES, "a pause, not an ending"
    assert run.latest_report["status"] == "REVIEW"
    assert run.spec.manual_review[0].confirmed is False
    assert run.finished_reason is None
    human = [item for item in run.plan.items if item.requires_human]
    assert len(human) == 1 and human[0].kind == "manual_review"
    assert "cannot see the picture" in human[0].recommended_action


def test_readiness_only_moves_after_a_human_confirms(spec, v1, v3):
    run = opened_on_v1()
    verify_retake(run, v3, take="v3")
    before = run.latest_report["status"]

    confirm_manual_item(run, 0)

    assert before == "REVIEW"
    assert run.latest_report["status"] == "SPONSOR_READY"
    assert run.state == "COMPLETE"
    assert run.spec.manual_review[0].confirmed is True
    # And the verdict is the resolver's, not the confirmation's.
    independent = load_spec()
    independent.manual_review[0].confirmed = True
    assert run_verifier(independent, v3).status == "SPONSOR_READY"


def test_confirming_the_same_item_twice_is_refused(spec, v1, v3):
    """Two guards cover this, and which one fires depends on where the run got to."""
    run = opened_on_v1()
    verify_retake(run, v3, take="v3")
    confirm_manual_item(run, 0)
    # That confirmation completed the run, so the terminal guard answers first.
    with pytest.raises(AutopilotError, match="COMPLETE"):
        confirm_manual_item(run, 0)

    # With a second item still open the run is not terminal, and the duplicate
    # guard is the one that has to hold.
    two_items = load_spec()
    two_items.manual_review.append(
        two_items.manual_review[0].model_copy(update={"source_quote": "A second visual check."})
    )
    live = start_run(two_items, load_take("v1"), spec_id="s", source_report_id="report02")
    confirm_manual_item(live, 0)
    assert live.is_terminal is False
    with pytest.raises(AutopilotError, match="already confirmed"):
        confirm_manual_item(live, 0)


def test_a_confirmation_re_runs_the_verifier_over_the_same_take(spec, v1, v3):
    run = opened_on_v1()
    verify_retake(run, v3, take="v3")
    confirm_manual_item(run, 0)
    takes = [record.take for record in run.history]
    assert takes[-1] == takes[-2], "the confirmation judged the take already supplied"


def test_a_confirmation_cannot_rescue_a_failing_take(spec, v1):
    """Confirming the visual item on a broken cut must not reach COMPLETE."""
    run = opened_on_v1()
    confirm_manual_item(run, 0)
    assert run.spec.manual_review[0].confirmed is True
    assert run.latest_report["status"] == "DO_NOT_SEND"
    assert run.state == "WAITING_FOR_RETAKE"
    assert run.state != "COMPLETE"


def test_an_out_of_range_manual_index_is_refused(spec, v1):
    run = opened_on_v1()
    for bad in (-1, 99, True, "0"):
        with pytest.raises(AutopilotError):
            confirm_manual_item(run, bad)


# --------------------------------------------------------------------------
# 11 & 12 · bounded iteration
# --------------------------------------------------------------------------


def test_remaining_failures_open_another_iteration(spec, v1, v3):
    run = opened_on_v1()
    assert run.iteration == 2

    verify_retake(run, degrade(v3, drop="Shield Mode"), take="take-2")
    assert run.state == "WAITING_FOR_RETAKE"
    assert run.iteration == 3
    assert run.plan.iteration == 2, "the plan is written for the take just judged"


def test_the_iteration_limit_stops_the_run_safely(spec, v1, v3):
    broken = degrade(v3, drop="Shield Mode")
    run = opened_on_v1()

    verify_retake(run, broken, take="take-2")
    verify_retake(run, broken, take="take-3")

    assert run.iteration == MAX_ITERATIONS == 3
    assert run.state == "ESCALATED"
    assert run.is_terminal is True
    assert run.accepts_retake() is False
    assert "iteration limit" in run.finished_reason.lower()
    assert run.latest_report["status"] != "SPONSOR_READY"

    with pytest.raises(AutopilotError, match="accepts no further takes"):
        verify_retake(run, load_take("v3"), take="one-too-many")


def test_the_bound_is_configurable_and_still_bounded(spec, v1, v3):
    broken = degrade(v3, drop="Shield Mode")
    run = opened_on_v1(max_iterations=2)
    verify_retake(run, broken, take="take-2")
    assert run.state == "ESCALATED"
    assert len(run.history) == 2


def test_a_stopped_run_is_never_a_passing_run(spec, v1, v3):
    run = opened_on_v1()
    stop_run(run, "Creator is re-shooting next week.")
    assert run.state == "STOPPED"
    assert run.is_terminal is True
    assert run.latest_report["status"] == "DO_NOT_SEND"
    with pytest.raises(AutopilotError):
        verify_retake(run, v3, take="v3")
    with pytest.raises(AutopilotError):
        confirm_manual_item(run, 0)
    assert stop_run(run).state == "STOPPED", "stopping twice is a no-op"


# --------------------------------------------------------------------------
# 13 · mismatched inputs fail loudly
# --------------------------------------------------------------------------


def test_a_report_verified_under_a_different_spec_is_refused(spec, v1):
    with pytest.raises(SpecBindingError) as caught:
        start_run(
            spec, v1, spec_id="s",
            source_report_id="report01",
            source_report_status="SPONSOR_READY",   # the stored report disagrees
        )
    message = str(caught.value)
    assert "DO_NOT_SEND" in message and "SPONSOR_READY" in message
    assert "start a new run" in message


def test_a_finding_whose_rule_left_the_spec_becomes_human_work(spec, v1):
    report = run_verifier(spec, v1)
    reduced = load_spec()
    reduced.rules = [rule for rule in reduced.rules if rule.id != "r3"]

    plan = build_plan(reduced, report, iteration=1)
    orphan = next(item for item in plan.items if item.rule_id == "r3")
    assert orphan.requires_human is True
    assert "not in the specification bound to this run" in orphan.recommended_action


def test_the_fingerprint_moves_for_every_weakening_a_spec_can_take(spec):
    baseline = spec_fingerprint(spec)
    for mutate in (
        lambda s: setattr(s.rules[2], "expected", "70%"),
        lambda s: s.rules.pop(),
        lambda s: setattr(s.rules[1], "min_seconds", 1.0),
        lambda s: setattr(s.rules[0], "severity", "warning"),
        lambda s: setattr(s.rules[4], "phrases", ["anything"]),
        lambda s: s.manual_review.clear(),
        lambda s: setattr(s, "campaign", "Something else"),
    ):
        edited = load_spec()
        mutate(edited)
        assert spec_fingerprint(edited) != baseline


# --------------------------------------------------------------------------
# 14 · the zero-key path stays zero-key
# --------------------------------------------------------------------------


def test_the_agent_imports_nothing_beyond_the_demo_requirements():
    demo_only = ("faster_whisper", "pypdf", "google", "openai", "torch", "httpx", "fastapi")
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in demo_only, f"{path.name} imports {name}"


def test_the_whole_loop_runs_without_a_key_a_model_or_ffmpeg(spec, v1, v3):
    run = start_run(spec, v1, spec_id="s", source_report_id="report01")
    verify_retake(run, v3, take="v3")
    confirm_manual_item(run, 0)
    assert run.state == "COMPLETE"
    assert run.latest_report["status"] == "SPONSOR_READY"


def test_the_client_view_never_carries_the_spec_or_the_transcript(spec, v1, v3):
    run = opened_on_v1()
    verify_retake(run, v3, take="v3")
    view = run.view()
    assert "spec" not in view and "latest_transcript" not in view
    assert view["spec_fingerprint"] == run.spec_fingerprint
    assert json.dumps(view), "the view is JSON-serialisable as the API returns it"
