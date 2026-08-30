# Mel evidence manifest

Every row is a claim we are prepared to defend. `Result` records what actually happened, including
where evidence is still outstanding. Nothing in this file is aspirational: a row that has not
happened says so.

**Date of the build session:** 2026-08-30. **Mel version:** 0.1.34 (`mel --help`).

| ID | Claim | Mel feature | Real action | Evidence to capture | Result |
|----|-------|-------------|-------------|---------------------|--------|
| M1 | SponsorLint was built inside the Mel terminal | Mel terminal (host process) | Verified programmatically before editing any file: process ancestry `mel (PID 505962, ./mel) → claude (PID 508206) → bash`; the `claude` process cwd is `/home/AeroChrome/Desktop/SponsorLint` | Screenshot: Mel window, terminal pane, repo path visible in the prompt | **Done.** Ancestry confirmed via `ps -o ppid=,comm=` walk and `/proc/505962/exe → ~/Desktop/Telegraph/mel`. Screenshot pending manual capture. |
| M2 | Project exploration was performed through Mel | Mel terminal command blocks | `git status --short`, `git branch --show-current`, `git log -5 --oneline`, `find`-based tree, `wc -l` over `sponsorlint/` and `tests/`, then reads of `models.py`, `lint/engine.py`, `web/app.py`, `spec.approved.json`, `app.js`, `app.css` | Screenshot: the Mel block containing the git + tree output | **Done.** Baseline recorded: branch `hackathon/sponsorlint-autopilot`, HEAD `8fecaf9`, one untracked path `sponsorlint/autopilot/`. Screenshot pending manual capture. |
| M3 | The pre-Autopilot baseline test suite was green before any edit | Mel terminal command block | `python -m pytest -q` run before the first file change | Screenshot: the block showing `361 passed, 3 skipped, 1 xfailed` | **Done.** Screenshot pending manual capture. |
| M4 | A real defect was found and investigated in Mel, not invented | Mel terminal command block | `grep -rn "latest_transcript" sponsorlint/` returned two `controller.py` hits with no matching field on `AutopilotRun` — an `AttributeError` on the manual-confirmation path | Screenshot: the grep block and the two hits | **Done.** Defect was real, reproduced by reading the model, and fixed in `autopilot/models.py` + `autopilot/controller.py`. Screenshot pending manual capture. |
| M5 | Mel's native agent performed the trust-boundary review | Mel native agent pane | Prompt supplied to the operator verbatim; `Ctrl+Enter` submission is the operator's action | Screenshot: the Mel agent pane with the prompt and the agent's full findings | **NOT DONE — blocked.** `mel --help` on 0.1.34 lists only `(none) / --version / whatsnew / --help`. There is no programmatic entry point to the agent from inside Mel's own terminal, so this hand-off is manual. Findings will be recorded in `MEL_BUILD_LOG.md` §7 when supplied. |
| M6 | The complete test suite passes with Autopilot added | Mel terminal command blocks | `python -m pytest -q`, `python -m sponsorlint demo`, `python -m sponsorlint demo --arc`, `python -m sponsorlint eval`, `python -m sponsorlint autopilot` | Screenshot: one Mel block with all five commands and the final `425 passed, 3 skipped, 1 xfailed` | **Done.** Observed: 425 passed / 3 skipped / 1 xfailed; eval 46 fixtures, 97.8%, 0 false PASSes — unchanged from baseline. Screenshot pending manual capture. |
| M7 | The application was launched and verified from Mel | Mel terminal command block + a browser | `python -m sponsorlint serve --port 8017` started from the Mel terminal; the full judge flow then driven against real Chrome over CDP and asserted step by step | Screenshot: the Mel block showing the server start and `{"status":"ok"}` from `/healthz`; plus the browser at `SPONSOR READY` | **Done.** 12 browser assertions passed, including `SPONSOR READY` withheld until a human confirmed. Artefacts: `docs/images/autopilot-plan.png`, `docs/images/autopilot-complete.png`. Screenshot of the Mel block pending manual capture. |
| M8 | Mel's code-review panel showed the real Autopilot diff | Mel git diff / code-review panel | Operator opens the panel against the working tree | Screenshot: the panel showing `sponsorlint/autopilot/*`, `sponsorlint/web/app.py`, `tests/test_autopilot*.py` | **NOT DONE — pending operator.** The panel is a GUI surface; it cannot be opened from a shell. |
| M9 | Mel supports exporting or bookmarking terminal blocks | — | Checked `mel --help` and `mel whatsnew` | — | **UNDETERMINED.** Neither surface documents a block export or bookmark capability, and no CLI flag exposes one. We are not claiming it. Capture screenshots manually. |

## Screenshot hygiene

Before capturing any Mel window: no `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `.env` contents on
screen; no `~/Desktop/Telegraph/.env.local`; no unrelated personal paths or window titles. Every
command in this build ran without any key set — that is the point of the zero-key path, and it makes
the screenshots safe by construction.
