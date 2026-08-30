# Mel build log — SponsorLint Autopilot

Agentic Day Online Hackathon, powered by Mel. Track: **Agentic Marketing**.
Build session: **2026-08-30**. Mel **0.1.34**.

This log records only things that actually happened. Where an item has not happened yet, it says
so and stays open. No timestamps, screenshots, conversations, failures or Mel capabilities are
invented here.

---

## 0. Confirming Mel before touching a file

**Task** · Establish that the repository really was open inside Mel before making any change.
**Mel feature** · Mel terminal, as the host process of this session.
**Why Mel was useful** · The claim "built in Mel" had to be checkable rather than asserted, and the
Mel terminal is the thing that makes it checkable — the shell is a child of the Mel process.

**Command / action**

```bash
env | grep -iE 'mel|term|editor|ide|shell|session'
pid=$$; for i in $(seq 1 12); do read -r ppid comm < <(ps -o ppid=,comm= -p "$pid"); \
  echo "$pid  $comm"; pid=$ppid; done
ls -l /proc/505962/exe /proc/508206/cwd
mel --help
```

**Observed result**

```
509488  bash
508206  claude
505962  mel          ← ./mel, started Sun Aug 30 18:33:14 2026
1265    systemd

/proc/505962/exe -> /home/AeroChrome/Desktop/Telegraph/mel
/proc/508206/cwd -> /home/AeroChrome/Desktop/SponsorLint
mel 0.1.34 — agentic terminal
```

**Related file/change** · none (pre-flight).
**Evidence reference** · Manifest **M1**.
**Friction** · The environment carries no `MEL_*` variable at all, so the first check (env scan)
found nothing and looked like a negative. Mel is only detectable by walking the process tree.
Separately, `/proc/<mel>/cwd` is Mel's own install directory (`~/Desktop/Telegraph`), *not* the
workspace it has open — so the host process cannot tell you which repository the user is looking
at. The answer had to come from the child shell's cwd.

---

## 1. Exploring the project through the Mel terminal

**Task** · Understand SponsorLint before extending it.
**Mel feature** · Mel terminal command blocks.
**Why Mel was useful** · Long multi-command blocks (git state + tree + line counts in one block)
kept the whole baseline picture in a single scrollback region instead of a dozen fragments.

**Command / action**

```bash
git status --short && git branch --show-current && git log -8 --oneline
find . -not -path './.git/*' -not -path '*__pycache__*' -maxdepth 3 | sort
find sponsorlint tests -name '*.py' | xargs wc -l
```

**Observed result** · Branch `hackathon/sponsorlint-autopilot`, HEAD `8fecaf9`, exactly one
untracked path: `sponsorlint/autopilot/`. 6,692 lines of Python across `sponsorlint/` and `tests/`.
The untracked directory held 1,055 lines of prior, unintegrated Autopilot work — no web routes, no
frontend, no tests, no docs referenced it.

**Related file/change** · none (read-only).
**Evidence reference** · Manifest **M2**.
**Friction** · none.

---

## 2. Establishing a green baseline before editing

**Task** · Prove that nothing was broken on arrival, so any later failure is attributable.
**Mel feature** · Mel terminal command block.

**Command / action** · `python -m pytest -q`
**Observed result** · `361 passed, 3 skipped, 1 xfailed in 0.86s`
**Related file/change** · none.
**Evidence reference** · Manifest **M3**.
**Friction** · none.

---

## 3. Finding and fixing a real defect

**Task** · Determine whether the prior Autopilot code actually ran.
**Mel feature** · Mel terminal command block (grep + a one-line Python import check).
**Why Mel was useful** · The grep and the import check ran side by side in one block, which is what
made the mismatch obvious rather than something to be discovered later at runtime.

**Command / action**

```bash
python -c "import sponsorlint.autopilot as a; print('import ok')"
grep -rn "latest_transcript" sponsorlint/
```

**Observed result**

```
import ok
sponsorlint/autopilot/controller.py:312:    if run.latest_transcript is None:
sponsorlint/autopilot/controller.py:329:    report = _verify(run, run.latest_transcript)
```

`AutopilotRun` declares `model_config = ConfigDict(extra="forbid")` and has no `latest_transcript`
field, so `confirm_manual_item` would have raised `AttributeError` the first time a human confirmed
a visual item — the exact step the demo depends on. The import succeeding is what made this
dangerous: the package looked healthy.

**Related file/change** · `sponsorlint/autopilot/models.py` (added the field),
`sponsorlint/autopilot/controller.py` (`_verify` now remembers the take it judged).
**Evidence reference** · Manifest **M4**.
**Friction** · none beyond the defect itself.

---

## 4. Building the feature

**Task** · Integrate the controller into the product: API, UI, CLI, tests.
**Mel feature** · Mel terminal command blocks throughout; every edit, run and re-run happened in
this shell.

**Observed result** ·

- `sponsorlint/web/app.py` — five `/api/autopilot/*` routes, plus a `REPORT_SPEC_IDS` map so a run
  binds to the spec that produced the report rather than to one the browser hands back.
- `sponsorlint/web/templates/index.html`, `static/app.js`, `static/app.css` — the `05 / Autopilot`
  panel: agent state, iteration, spec fingerprint, evidence-linked checklist, live trace, retake
  picker, verification history.
- `sponsorlint/cli.py` — `python -m sponsorlint autopilot`, so the loop is demonstrable without a
  browser.

**Evidence reference** · Manifest **M6**, **M8**.
**Friction** · One layout defect found only by looking: `.check-dock` is `position: sticky;
bottom: 14px`, correct for a full-height step screen but wrong mid-report, where it floated over
its own heading. Fixed with `.agent-retake .check-dock { position: static; }`.

---

## 5. Running the suite and the commands

**Task** · Prove the feature and prove nothing regressed.
**Mel feature** · Mel terminal command blocks.

**Command / action**

```bash
python -m pytest -q
python -m sponsorlint demo
python -m sponsorlint demo --arc
python -m sponsorlint eval
python -m sponsorlint autopilot
python -m sponsorlint autopilot --confirm-manual
```

**Observed result**

```
425 passed, 3 skipped, 1 xfailed          (baseline was 361 passed, 3 skipped, 1 xfailed)
demo         V1  4/7  DO NOT SEND
demo --arc   V1  4/7  DO NOT SEND   ·   V3  7/7  REVIEW
eval         46 fixtures · 97.8% · 1 false FAIL · 0 false PASSes      (unchanged)
autopilot                    FINAL STATE  NEEDS_HUMAN_REVIEW   exit 1
autopilot --confirm-manual   FINAL STATE  COMPLETE             exit 0
```

**Related file/change** · `tests/test_autopilot.py` (44 tests), `tests/test_autopilot_web.py`
(13 tests), `tests/test_cli.py`, `tests/test_import_discipline.py`.
**Evidence reference** · Manifest **M6**.
**Friction** · One test I wrote asserted the wrong guard: after a human confirms the last manual
item the run reaches `COMPLETE`, so a second confirmation is refused by the terminal-state guard,
not the duplicate guard. The product was right and the test was wrong; the test now covers both
guards.

---

## 6. Launching and verifying the application

**Task** · Prove the judge flow works in a browser, not just in tests.
**Mel feature** · Mel terminal command block (server launch and health check).

**Command / action**

```bash
python -m sponsorlint serve --port 8017
curl -s http://127.0.0.1:8017/healthz
```

**Observed result** · `{"status":"ok"}`. The full flow was then driven against real Chrome over the
DevTools protocol and asserted at each step: sample loaded (7 rules) → approved → V1 verified
(`DO NOT SEND`, `04/07`) → **Create retake plan** (state `Waiting for a new take`, iteration `2/3`,
4 plan items, 4 trace events, `73%` vs `70%` on screen, 4/4 items citing the brief) → Take 3
selected → **Verify corrected take** (`REVIEW`, `07/07`, 9 trace events, visual item still open,
`SPONSOR READY` correctly absent) → human confirmation (`SPONSOR READY`, `07/07`, 13 trace events).
Zero horizontal overflow at 1024px.

**Related file/change** · `docs/images/autopilot-plan.png`, `docs/images/autopilot-complete.png`.
**Evidence reference** · Manifest **M7**.
**Friction** · The Claude browser extension was not connected, so browser verification had to be
done by driving Chrome directly over CDP rather than through the normal tooling. Not a Mel problem.

---

## 7. Mel's native agent — trust-boundary review

**Task** · Have Mel's own agent review the implementation for any path that bypasses the approved
spec, suppresses a failure, auto-confirms manual review, fabricates a trace, or returns
`SPONSOR_READY` outside the deterministic resolver.

**Status** · **OPEN — handed to the operator.**

**Why it is open** · `mel --help` on 0.1.34 lists exactly four surfaces:

```
  (none)      Launch the terminal
  --version   Print the version
  whatsnew    Show release notes
  --help      Show this help
```

There is no subcommand, flag, socket or documented protocol for submitting a prompt to Mel's agent,
so a coding agent running *inside* a Mel terminal pane cannot delegate to Mel's agent pane. The
prompt below was printed as a `MEL EVIDENCE CHECKPOINT` for the operator to submit with
`Ctrl+Enter`.

```text
Review the SponsorLint Autopilot implementation for any path that can bypass the approved
specification, suppress a verifier failure, confirm manual review automatically, fabricate an
agent trace, or return SPONSOR_READY without the existing deterministic readiness resolver.
```

**Findings** · _Not yet received. To be pasted verbatim here, with the resulting code changes
listed underneath. Claude's own review is recorded separately in §8 and is **not** a substitute._

**Evidence reference** · Manifest **M5**.

---

## 8. Claude's own trust-boundary checks (not Mel's)

Recorded separately so it is never mistaken for §7. These are automated tests, not a review:

- `test_the_agent_never_assigns_a_readiness_verdict` — walks the AST of every module in
  `sponsorlint/autopilot/` and fails on any assignment containing `SPONSOR_READY`.
- `test_only_the_transition_helper_can_append_a_trace_event` — asserts `run.trace.append` occurs
  exactly once in `controller.py` and only inside `_transition`.
- `test_the_agent_carries_no_campaign_specific_knowledge` — greps the package for `aegis`,
  `shield mode`, `73%`, `70%`, `aegisvpn`, `transcript.v1`, `transcript.v3`, `sponsor-cut`.
- `test_a_confirmation_cannot_rescue_a_failing_take`, `test_an_edited_specification_stops_the_run_instead_of_continuing`,
  `test_the_iteration_limit_stops_the_run_safely`.

---

## Submission answers, from observed evidence only

### Three ways Mel helped

1. **It made "built in Mel" verifiable rather than asserted.** Because the coding agent runs as a
   child of the Mel process, the claim was settled in one command block by walking the process tree
   (`mel → claude → bash`) before a single file was edited. That is a stronger provenance story than
   a screenshot.
2. **Long multi-command blocks kept the baseline legible.** Git state, repository tree and per-file
   line counts in one block made it immediately obvious that `sponsorlint/autopilot/` was untracked,
   1,055 lines, and referenced by nothing — which reframed the whole task from "write a feature" to
   "integrate and repair one".
3. **Fast, repeated command blocks made the red→green cycle cheap.** The suite runs in ~1–3s, so
   `pytest` after every edit was free; the wrong-guard test failure in §5 was caught and corrected
   in a single cycle without leaving the terminal.

### Three Mel friction points

1. **Mel's native agent has no programmatic entry point.** `mel --help` exposes only
   `(none) / --version / whatsnew / --help`. An agent working inside a Mel terminal cannot hand a
   task to Mel's own agent, which forced the hackathon's own required Mel-native task (§7) into a
   manual `Ctrl+Enter` hand-off and left it blocking at the end of the build.
2. **The Mel process cannot tell you which workspace it has open.** `/proc/<mel>/cwd` is Mel's
   install directory (`~/Desktop/Telegraph`), and no `MEL_*` environment variable is exported to
   child shells. Determining that SponsorLint was the open repository required reading the *child*
   process's cwd. Any tool trying to be workspace-aware inside Mel has to guess.
3. **Whether terminal blocks can be exported or bookmarked is undiscoverable from the CLI.** Neither
   `mel --help` nor `mel whatsnew` mentions such a capability, and no flag exposes one. Evidence
   capture therefore falls back to manual screenshots, which is the least reliable part of this
   submission. _(Recorded as UNDETERMINED in the manifest, M9 — not as a definite absence.)_

### One feature request

**A headless entry point to the Mel agent** — `mel agent --prompt "…"` (or `--prompt-file`, writing
the transcript to stdout or a path). Evidence: friction point 1. It would have turned the
hackathon's own required Mel-native review from a blocking manual hand-off into a step in the build,
and it would let Mel's agent be used for exactly what it is good at — a second, independent reviewer
of another agent's work — inside CI or inside a longer automated session. A `--json` transcript
would additionally make Mel usage self-evidencing, which would remove the need for friction point 3
entirely.
