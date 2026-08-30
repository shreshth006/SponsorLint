"""The Autopilot API, exercised the way the browser exercises it.

The judge path is a sequence of HTTP calls, so it is worth proving as one: load
the sample, approve it, verify V1, start a run, hand it V3, confirm the visual
item, and only then see SPONSOR READY. Everything after that is the ways the
sequence is allowed to fail.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from sponsorlint.web.app import (
    REPORTS,
    REPORT_SPEC_IDS,
    REPORT_TRANSCRIPTS,
    RUNS,
    SPECS,
    app,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def clear_state() -> None:
    for store in (SPECS, REPORTS, REPORT_TRANSCRIPTS, REPORT_SPEC_IDS, RUNS):
        store.clear()


def drive(scenario):
    """Run one async scenario against the ASGI app, from a sync test."""

    async def main():
        clear_state()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await scenario(client)

    return asyncio.run(main())


async def approve_and_verify(client, take: str = "v1"):
    """Get to a saved report the way the UI does, and return its ids."""
    campaign = (await client.get("/api/sample")).json()
    approval = await client.post("/api/spec/approve", json={"spec": campaign["spec"]})
    spec_id = approval.json()["spec_id"]
    verified = await client.post("/api/verify", data={"spec_id": spec_id, "take": take})
    body = verified.json()
    return campaign, spec_id, body["report_id"], body["report"]


# --------------------------------------------------------------------------
# the judge path
# --------------------------------------------------------------------------


def test_the_whole_judge_flow_reaches_sponsor_ready_only_after_a_human_confirms():
    async def scenario(client):
        campaign, spec_id, report_id, report = await approve_and_verify(client, "v1")
        assert report["status"] == "DO_NOT_SEND"
        assert report["score"] == "4/7"

        started = await client.post("/api/autopilot/start", json={"report_id": report_id})
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        run = started.json()["run"]

        # inspect -> plan -> wait, and the plan cites real failures
        assert run["state"] == "WAITING_FOR_RETAKE"
        assert run["accepts_retake"] is True
        assert run["iteration"] == 2 and run["max_iterations"] == 3
        assert [event["action"] for event in run["trace"]] == [
            "bind_specification", "run_verifier", "build_retake_plan", "await_retake",
        ]
        planned = {item["rule_id"] for item in run["plan"]["items"] if item["rule_id"]}
        assert planned == {"r3", "r5", "r6"}
        assert run["plan"]["retake_count"] == 3
        assert run["plan"]["human_count"] == 1

        # the run is readable on its own
        fetched = await client.get(f"/api/autopilot/{run_id}")
        assert fetched.status_code == 200
        assert fetched.json()["run"] == run

        # the creator supplies the corrected take
        retaken = await client.post(
            f"/api/autopilot/{run_id}/verify-retake", data={"take": "v3"}
        )
        assert retaken.status_code == 200
        run = retaken.json()["run"]
        assert run["state"] == "NEEDS_HUMAN_REVIEW"
        assert run["latest_report"]["status"] == "REVIEW"
        assert run["latest_report"]["score"] == "7/7"
        assert run["is_terminal"] is False
        assert run["plan"]["human_count"] == 1
        assert run["plan"]["retake_count"] == 0
        assert [record["status"] for record in run["history"]] == ["DO_NOT_SEND", "REVIEW"]

        # and only a human closes the visual item
        confirmed = await client.post(
            f"/api/autopilot/{run_id}/confirm-manual", json={"index": 0}
        )
        assert confirmed.status_code == 200
        run = confirmed.json()["run"]
        assert run["state"] == "COMPLETE"
        assert run["is_terminal"] is True
        assert run["latest_report"]["status"] == "SPONSOR_READY"
        assert run["latest_report"]["score"] == "7/7"
        assert "resolved SPONSOR READY" in run["finished_reason"]

        # the trace is one continuous, gap-free record of the run
        assert [event["seq"] for event in run["trace"]] == list(
            range(1, len(run["trace"]) + 1)
        )
        assert [event["state"] for event in run["trace"]][-1] == "COMPLETE"

    drive(scenario)


def test_the_run_view_never_ships_the_specification_to_the_browser():
    async def scenario(client):
        _, _, report_id, _ = await approve_and_verify(client)
        started = await client.post("/api/autopilot/start", json={"report_id": report_id})
        run = started.json()["run"]
        assert "spec" not in run
        assert "latest_transcript" not in run
        assert len(run["spec_fingerprint"]) == 64
        # and nothing anywhere in the serialised view leaks the rule payloads
        assert "min_seconds" not in json.dumps(run)

    drive(scenario)


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------


def test_an_unknown_run_id_is_a_clear_404():
    async def scenario(client):
        for method, url, kwargs in (
            ("get", "/api/autopilot/nope", {}),
            ("post", "/api/autopilot/nope/verify-retake", {"data": {"take": "v3"}}),
            ("post", "/api/autopilot/nope/confirm-manual", {"json": {"index": 0}}),
            ("post", "/api/autopilot/nope/stop", {"json": {}}),
        ):
            response = await getattr(client, method)(url, **kwargs)
            assert response.status_code == 404, url
            assert "no longer in memory" in response.json()["detail"]

    drive(scenario)


def test_an_unknown_report_id_cannot_open_a_run():
    async def scenario(client):
        response = await client.post("/api/autopilot/start", json={"report_id": "missing"})
        assert response.status_code == 404
        assert "Check the cut again" in response.json()["detail"]

        for payload in ({}, {"report_id": ""}, {"report_id": 7}):
            bad = await client.post("/api/autopilot/start", json=payload)
            assert bad.status_code == 400

    drive(scenario)


def test_a_run_cannot_be_opened_against_a_forgotten_specification():
    async def scenario(client):
        _, spec_id, report_id, _ = await approve_and_verify(client)
        SPECS.pop(spec_id)                      # the bounded store evicted it
        response = await client.post("/api/autopilot/start", json={"report_id": report_id})
        assert response.status_code == 404
        assert "no longer in memory" in response.json()["detail"]

    drive(scenario)


def test_editing_the_approved_spec_mid_run_stops_the_run():
    async def scenario(client):
        campaign, spec_id, report_id, _ = await approve_and_verify(client)
        started = await client.post("/api/autopilot/start", json={"report_id": report_id})
        run_id = started.json()["run_id"]

        # An expected value edited to match what was actually said.
        SPECS[spec_id].rules[2].expected = "70%"

        blocked = await client.post(
            f"/api/autopilot/{run_id}/verify-retake", data={"take": "v3"}
        )
        assert blocked.status_code == 409
        assert "changed after this Autopilot run started" in blocked.json()["detail"]

        held = (await client.get(f"/api/autopilot/{run_id}")).json()["run"]
        assert held["state"] == "WAITING_FOR_RETAKE"
        assert len(held["history"]) == 1

    drive(scenario)


def test_a_retake_needs_media_somebody_actually_supplied():
    async def scenario(client):
        _, _, report_id, _ = await approve_and_verify(client)
        run_id = (await client.post("/api/autopilot/start", json={"report_id": report_id})).json()["run_id"]

        empty = await client.post(f"/api/autopilot/{run_id}/verify-retake", data={"take": ""})
        assert empty.status_code == 400
        assert "Choose a recorded take" in empty.json()["detail"]

        unknown = await client.post(
            f"/api/autopilot/{run_id}/verify-retake", data={"take": "v9"}
        )
        assert unknown.status_code == 404

    drive(scenario)


def test_a_stopped_run_refuses_further_takes():
    async def scenario(client):
        _, _, report_id, _ = await approve_and_verify(client)
        run_id = (await client.post("/api/autopilot/start", json={"report_id": report_id})).json()["run_id"]

        stopped = await client.post(
            f"/api/autopilot/{run_id}/stop", json={"reason": "Re-shooting on Monday."}
        )
        assert stopped.status_code == 200
        run = stopped.json()["run"]
        assert run["state"] == "STOPPED"
        assert run["finished_reason"] == "Re-shooting on Monday."
        assert run["latest_report"]["status"] == "DO_NOT_SEND"

        refused = await client.post(
            f"/api/autopilot/{run_id}/verify-retake", data={"take": "v3"}
        )
        assert refused.status_code == 409
        assert "accepts no further takes" in refused.json()["detail"]

    drive(scenario)


def test_a_manual_index_the_spec_does_not_have_is_refused():
    async def scenario(client):
        _, _, report_id, _ = await approve_and_verify(client)
        run_id = (await client.post("/api/autopilot/start", json={"report_id": report_id})).json()["run_id"]
        for index in (-1, 5, "0", True, None):
            response = await client.post(
                f"/api/autopilot/{run_id}/confirm-manual", json={"index": index}
            )
            assert response.status_code == 400, index

    drive(scenario)


def test_autopilot_writes_are_covered_by_the_cross_origin_guard():
    async def scenario(client):
        _, _, report_id, _ = await approve_and_verify(client)
        response = await client.post(
            "/api/autopilot/start",
            json={"report_id": report_id},
            headers={"Origin": "https://elsewhere.example"},
        )
        assert response.status_code == 403

    drive(scenario)


def test_the_run_store_is_bounded_like_every_other_store(monkeypatch):
    async def scenario(client):
        monkeypatch.setattr("sponsorlint.web.app.MAX_STORED_ITEMS", 3)
        _, _, report_id, _ = await approve_and_verify(client)
        for _ in range(6):
            started = await client.post("/api/autopilot/start", json={"report_id": report_id})
            assert started.status_code == 200
        assert len(RUNS) == 3

    drive(scenario)


# --------------------------------------------------------------------------
# the pre-Autopilot routes still behave exactly as they did
# --------------------------------------------------------------------------


def test_the_original_report_confirmation_path_is_untouched():
    async def scenario(client):
        campaign, spec_id, report_id, _ = await approve_and_verify(client, "v3")
        confirmed = await client.post(
            f"/api/report/{report_id}/confirm-manual",
            json={"spec": campaign["spec"], "index": 0},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["report"]["status"] == "SPONSOR_READY"

    drive(scenario)


def test_verify_records_which_spec_produced_each_report():
    async def scenario(client):
        _, spec_id, report_id, _ = await approve_and_verify(client)
        assert REPORT_SPEC_IDS[report_id] == spec_id

        # and the confirm-manual route records its new pairing too
        campaign = (await client.get("/api/sample")).json()
        confirmed = await client.post(
            f"/api/report/{report_id}/confirm-manual",
            json={"spec": campaign["spec"], "index": 0},
        )
        body = confirmed.json()
        assert REPORT_SPEC_IDS[body["report_id"]] == body["spec_id"]

    drive(scenario)
