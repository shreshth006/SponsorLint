# Hackathon submission — SponsorLint Autopilot

## Project name

**SponsorLint Autopilot** — *from a rejected sponsor cut to a verified retake.*

## Track

**Agentic Marketing.** (Agentic Day Online Hackathon, powered by Mel.)

## Problem

A sponsor brief is a contract with enumerated deliverables: an exact discount figure, a tracked URL,
a required feature mention, a disclosure, a duration window, prohibited claims. A creator finishes
the integration, sends it, and the brand sends it back. The expensive unit is not the mistake — it
is the **revision cycle**: re-read the brief, work out which of seven requirements actually moved,
re-record, re-check, discover you fixed two of three, repeat. Every cycle costs a day and some
goodwill, and the cost repeats.

SponsorLint already answered *"did I follow the brief?"* deterministically, with timestamped
evidence. It did not answer *"so what do I record instead, and did that actually fix it?"* — and it
had no memory that you were mid-revision at all.

## Solution

Autopilot turns the revision cycle into a bounded agentic workflow with a human in it:

```
Inspect the report  →  Plan the smallest safe retake  →  Wait for a real take
        ↑                                                        │
        │                                                        ▼
   Another bounded iteration  ←  Observe & decide  ←  Run the deterministic verifier
        │
        └─→  COMPLETE (the verifier said so) · NEEDS_HUMAN_REVIEW · ESCALATED (bound reached)
```

It reads the report the deterministic verifier produced, writes an evidence-linked checklist (each
item citing its rule, the sentence of the brief it came from, the approved value and what was
actually detected), then **stops** and waits for a take a person genuinely records. When one
arrives it hands it to the same verifier, compares the results, and either finishes, opens another
bounded iteration, or escalates to a human. Maximum three verification cycles.

## Agentic behavior

Goal → Understand → Plan → Act → Observe → Decide → Act again → Verify, with eight explicit states:
`INSPECTING`, `PLANNING`, `WAITING_FOR_RETAKE`, `VERIFYING_RETAKE`, `NEEDS_HUMAN_REVIEW`,
`COMPLETE`, `ESCALATED`, `STOPPED`.

The agent's real tools are: invoke the existing verifier · inspect structured `Report` / `Result` /
`Rule` / `Spec` data · write a structured retake plan · compare two verification runs · decide
continue / escalate / stop. That is the whole surface. There is no chat loop, no free-text
generation in the decision path, and no second model.

Every trace line on screen is appended by the backend at the moment the transition executes.
`_transition` is the only function that can write one, and a test enforces that. There are no fake
delays, no scripted progress and no decorative terminal output.

## What existed before

Baseline commit **`8fecaf9e23e71af8f5580ea80b5f2c99d7b1d062`**.

An AI-assisted brief compiler (the only model call), a human approval screen, six deterministic
validators, readiness resolution to `DO_NOT_SEND` / `REVIEW` / `SPONSOR_READY`, a zero-key V1→V3
sample campaign, a CLI, a FastAPI + vanilla-JS web app, deployment config, and 361 passing tests.
Roughly 6,700 lines of Python.

## What was added

| Area | Added |
|---|---|
| `sponsorlint/autopilot/` | `models.py` · `planner.py` · `controller.py` — states, trace, plan, spec binding, bounded iteration |
| `sponsorlint/web/app.py` | five `/api/autopilot/*` routes; `REPORT_SPEC_IDS` so a run binds to the spec that produced the report |
| Web UI | the `05 / Autopilot` panel — agent state, iteration, spec fingerprint, evidence-linked checklist, live trace, retake picker, verification history |
| `sponsorlint/cli.py` | `python -m sponsorlint autopilot` |
| Tests | `tests/test_autopilot.py` (44) · `tests/test_autopilot_web.py` (13) · CLI and import-discipline additions. **361 → 425 passing** |
| Docs | this file · `AUTOPILOT_DEMO.md` · `MEL_BUILD_LOG.md` · `MEL_EVIDENCE_MANIFEST.md` · a README section |

A pre-existing but **unintegrated and non-functional** draft of the controller was present in the
working tree at the start of this session (untracked, 1,055 lines, referenced by nothing). It
contained a latent `AttributeError` on the manual-confirmation path — see `MEL_BUILD_LOG.md` §3.
It was repaired and integrated rather than discarded.

## Trust boundary — unchanged

> **AI proposes. Humans approve. Deterministic code verifies.**

Autopilot cannot: edit the approved specification (a SHA-256 fingerprint stops the run with a 409);
change an expected value to match the recording; remove or weaken a requirement; downgrade or
suppress a failure; auto-confirm a manual-review item; present an edited transcript as a recording;
claim to have edited a video; show a hardcoded trace; or return `SPONSOR_READY` by any route other
than the existing resolver returning it.

## Zero-key demo path

No API key, no model download, no ffmpeg, no network, no new dependency, no second AI service.
`pip install -r requirements-demo.txt && python -m sponsorlint serve`.

The bundled campaign produces the whole arc from its own data — `V1 4/7 DO NOT SEND` → plan →
`V3 7/7 REVIEW` → human confirmation → `SPONSOR READY`. None of those values exist in the agent: a
test greps the package for the brand, the percentages, the feature name and the sample filenames.

## Repository / working URLs

- Repository: `https://github.com/shreshth006/SponsorLint` — branch `hackathon/sponsorlint-autopilot`
- Baseline commit: `8fecaf9e23e71af8f5580ea80b5f2c99d7b1d062`
- Live deployment: **TODO — paste the Render URL here before submitting.**
- Demo video (75–90s): **TODO — record from `docs/AUTOPILOT_DEMO.md` and paste the link here.**

## Attribution

Two-person project. [@Harshyadav442277](https://github.com/Harshyadav442277) opened the repository
and wrote the first commits; [@shreshth006](https://github.com/shreshth006) wrote the majority of
the current tree. The CI badge points at `Harshyadav442277/SponsorLint`, where the workflow was
first configured; this tree's `origin` is `shreshth006/SponsorLint`. The brand, campaign, URL and
promo code in the sample are fictional.

## Limitations

- **It plans; it does not touch media.** No recording, editing, re-cutting or "video repair".
  `WAITING_FOR_RETAKE` is a genuine stop.
- **Suggested wording is a draft.** For a prohibited claim it recommends removal and states that any
  replacement needs sponsor approval. It will not invent a stronger claim.
- **Three iterations, then a person.** Not tunable from the browser.
- **One specification per run.** Editing the spec mid-run stops the run rather than migrating it.
- **In-memory, process-local state.** Restarting the server ends every run. No database, no
  accounts, no queue — deliberately.
- **Audio and duration only**, inherited from SponsorLint. Visual requirements are surfaced for
  explicit human confirmation and never scored.
- **A `DURATION` miss is reported, not solved.** Length is a property of a recording.

## Mel usage

### Three evidence-backed ways Mel helped

1. **Mel made "built in Mel" verifiable instead of asserted.** The coding agent runs as a child of
   the Mel process, so the claim was settled before any file was edited by walking the process tree:
   `mel (PID 505962, ./mel) → claude (PID 508206) → bash`, with the `claude` cwd at
   `~/Desktop/SponsorLint`. Evidence: `MEL_EVIDENCE_MANIFEST.md` **M1**, `MEL_BUILD_LOG.md` §0.
2. **Long multi-command blocks kept the baseline legible and changed the plan.** Git state, tree and
   per-file line counts in one block made it immediately clear that `sponsorlint/autopilot/` was
   untracked, 1,055 lines and referenced by nothing — reframing the task from "write a feature" to
   "integrate and repair one". Evidence: **M2**, §1.
3. **Cheap, repeated command blocks made the red→green loop free.** The suite runs in 1–3s, so
   `pytest` ran after every edit; the one genuinely wrong assertion I wrote was caught and fixed in
   a single cycle without leaving the terminal. Evidence: **M3**, **M6**, §5.

### Three real Mel friction points

1. **Mel's native agent has no programmatic entry point.** `mel --help` (0.1.34) exposes only
   `(none) / --version / whatsnew / --help`. An agent working inside a Mel terminal pane cannot hand
   work to Mel's agent pane, which forced the hackathon's own required Mel-native review into a
   manual `Ctrl+Enter` hand-off. Evidence: **M5**, §7.
2. **The Mel process cannot report which workspace it has open.** `/proc/<mel>/cwd` is Mel's install
   directory and no `MEL_*` variable reaches child shells, so workspace context has to be inferred
   from the child process. Evidence: §0, "Friction".
3. **Block export/bookmarking is undiscoverable from the CLI**, so evidence capture falls back to
   manual screenshots — the least reliable part of this submission. Recorded as **UNDETERMINED**
   (**M9**), not as a definite absence.

### One evidence-backed feature request

**A headless entry point to the Mel agent**: `mel agent --prompt "…"` (or `--prompt-file`), writing
the transcript to stdout, with `--json`. Evidence: friction point 1. It would have turned this
hackathon's own required Mel-native review from a blocking manual hand-off into a step in the build,
would let Mel's agent act as an independent second reviewer of another agent's work inside CI, and
would make Mel usage self-evidencing — which would dissolve friction point 3 as well.

## Outstanding before submission

- [ ] Mel-native agent trust-boundary review (**M5**) — run it and paste the findings into
      `MEL_BUILD_LOG.md` §7.
- [ ] Mel code-review panel screenshot showing the Autopilot diff (**M8**).
- [ ] Screenshots for M1, M2, M3, M4, M6, M7.
- [ ] Record the 75–90s demo video and paste the link above.
- [ ] Deploy and paste the live URL above.
