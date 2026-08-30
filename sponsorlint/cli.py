"""Command dispatch. Architecture.md §6.

Invocation is always `python -m sponsorlint <command>`, run from the repo root.
There is no pyproject.toml, no setup.py and no console script — the bare
`sponsorlint` form does not exist.

IMPORT DISCIPLINE — this is what makes the zero-key path work.

Module scope here may import only from `models`, `lint/`, `report/`,
`normalize/` and `eval/` — modules whose entire dependency set is in
`requirements-demo.txt`. `faster_whisper`, `pypdf` and the LLM client are
imported *inside the command branch that needs them*. A module-scope import of
any of them kills `demo` on a judge's machine before dispatch runs, and is
invisible on a dev machine that has them installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import Spec, SpecError, Transcript

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "samples"

USAGE = """SponsorLint — pre-flight QA for sponsored YouTube integrations.

  python -m sponsorlint demo                    zero-key demo on the committed campaign
  python -m sponsorlint demo --arc              compare the V1 and V3 verdicts
  python -m sponsorlint verify --spec S --transcript T
  python -m sponsorlint autopilot               bounded retake loop over the committed takes
  python -m sponsorlint eval                    validator accuracy over labeled fixtures
  python -m sponsorlint compile BRIEF           brief -> proposed spec (needs an API key)
  python -m sponsorlint transcribe VIDEO        video -> transcript (needs ffmpeg)
  python -m sponsorlint serve                   the web UI

Run from the repo root. `demo`, `autopilot` and `eval` need no API key, no model
download and no ffmpeg."""


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


class SponsorLintError(Exception):
    """A readable failure. Says what went wrong and how to fix it."""


def main(argv: list[str] | None = None) -> int:
    _configure_windows_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    command, rest = argv[0], argv[1:]
    handlers = {
        "demo": _demo,
        "verify": _verify,
        "autopilot": _autopilot,
        "eval": _eval,
        "compile": _compile,
        "transcribe": _transcribe,
        "serve": _serve,
    }

    handler = handlers.get(command)
    if handler is None:
        print(f"Unknown command: {command}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    try:
        return handler(rest)
    except SpecError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except SponsorLintError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _configure_windows_stdio() -> None:
    """Keep the Unicode report readable on Windows' legacy code pages.

    Python still inherits CP-1252 in some Windows terminals and subprocess
    captures.  The report deliberately uses a box-drawing divider, which that
    codec cannot represent.  Reconfigure only the process-owned standard
    streams; test doubles such as ``StringIO`` do not expose ``reconfigure``
    and are left alone.
    """
    if sys.platform != "win32":
        return

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            # Encoding is presentation-only; it must never prevent a check.
            pass


# --------------------------------------------------------------------------
# demo — the zero-key path
# --------------------------------------------------------------------------


def _demo(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sponsorlint demo")
    parser.add_argument("--v3", action="store_true", help="run the corrected take")
    parser.add_argument("--arc", action="store_true", help="run V1 then V3")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    from .lint.engine import run
    from .report.terminal import render

    spec = _load_spec(SAMPLES / "spec.approved.json")

    takes = ["v1", "v3"] if args.arc else (["v3"] if args.v3 else ["v1"])
    reports = []
    for take in takes:
        transcript = _load_transcript(SAMPLES / f"transcript.{take}.json")
        report = run(spec, transcript)
        reports.append((take, report))
        if args.json:
            print(json.dumps(report.model_dump(by_alias=True), indent=2))
        else:
            render(report)

    if args.arc and not args.json:
        print("  " + "─" * 62)
        for take, report in reports:
            print(f"  {take.upper():<4} {report.score.fraction} requirements passed"
                  f"      {report.label}")
        print()

    return 0


# --------------------------------------------------------------------------
# verify — deterministic checks only
# --------------------------------------------------------------------------


def _verify(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sponsorlint verify")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from .lint.engine import run
    from .report.terminal import render

    report = run(_load_spec(Path(args.spec)), _load_transcript(Path(args.transcript)))

    if args.json:
        print(json.dumps(report.model_dump(by_alias=True), indent=2))
    else:
        render(report)

    # Linter semantics: a blocking failure is a non-zero exit.
    return 1 if report.status == "DO_NOT_SEND" else 0


# --------------------------------------------------------------------------
# autopilot — the bounded retake loop, in the terminal
# --------------------------------------------------------------------------


def _autopilot(argv: list[str]) -> int:
    """Drive one bounded run over committed takes and print the real trace.

    Every line this prints is read back off the run the controller built. The
    command supplies takes in the order given and stops when the controller
    stops — it does not decide anything itself.
    """
    parser = argparse.ArgumentParser(prog="python -m sponsorlint autopilot")
    parser.add_argument("--spec", help="approved specification (default: the sample campaign)")
    parser.add_argument(
        "--take",
        action="append",
        default=None,
        metavar="TRANSCRIPT",
        help="a take to verify, in order; repeatable (default: the sample V1 then V3)",
    )
    parser.add_argument(
        "--confirm-manual",
        action="store_true",
        help="confirm outstanding manual-review items, as a human operator would",
    )
    parser.add_argument("--json", action="store_true", help="emit the run as JSON")
    args = parser.parse_args(argv)

    from .autopilot import confirm_manual_item, start_run, verify_retake
    from .autopilot.models import STATE_LABEL

    spec = _load_spec(Path(args.spec) if args.spec else SAMPLES / "spec.approved.json")
    take_paths = (
        [Path(t) for t in args.take]
        if args.take
        else [SAMPLES / "transcript.v1.json", SAMPLES / "transcript.v3.json"]
    )

    first, rest = take_paths[0], take_paths[1:]
    run = start_run(
        spec,
        _load_transcript(first),
        spec_id="cli",
        source_report_id="cli-run",
    )

    for path in rest:
        if not run.accepts_retake():
            break
        verify_retake(run, _load_transcript(path), take=path.name)

    if args.confirm_manual and not run.is_terminal:
        for index, item in enumerate(run.spec.manual_review):
            if not item.confirmed:
                confirm_manual_item(run, index)

    if args.json:
        print(json.dumps(run.view(), indent=2))
        return 0

    print()
    print("  SPONSORLINT AUTOPILOT")
    print(f"  {run.campaign}")
    print(f"  spec binding {run.spec_fingerprint[:12]} · at most {run.max_iterations} iterations")
    print("  " + "─" * 68)
    for event in run.trace:
        print(f"  {event.seq:>2}  [{event.state}] {event.action}")
        print(f"      {event.message}")
        if event.detail:
            print(f"      · {event.detail}")
        print()

    print("  " + "─" * 68)
    for record in run.history:
        print(f"  iteration {record.iteration}  {record.take:<24} "
              f"{record.score:>5}  {record.label}")
    print("  " + "─" * 68)
    print(f"  FINAL STATE   {run.state}  ({STATE_LABEL[run.state]})")
    if run.finished_reason:
        print(f"  REASON        {run.finished_reason}")
    if run.plan and run.plan.items:
        print(f"  OPEN ITEMS    {run.plan.summary()}")
    print()

    # Linter semantics, as `verify` has: an unfinished run is a non-zero exit.
    return 0 if run.state == "COMPLETE" else 1


# --------------------------------------------------------------------------
# eval — validator metrics
# --------------------------------------------------------------------------


def _eval(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sponsorlint eval")
    parser.add_argument("--verbose", action="store_true", help="list every case")
    args = parser.parse_args(argv)

    from .eval.runner import run_eval

    run_eval(verbose=args.verbose)
    return 0  # metrics are reported, never enforced as a gate


# --------------------------------------------------------------------------
# compile — the only LLM call
# --------------------------------------------------------------------------


def _compile(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sponsorlint compile")
    parser.add_argument("brief")
    parser.add_argument("-o", "--out", help="write the proposed spec here")
    args = parser.parse_args(argv)

    from .brief.compile import CompileError, compile_brief  # LLM client imported here
    from .brief.extract import ExtractError, extract_text  # pypdf imported here

    try:
        text = extract_text(Path(args.brief))
        spec = compile_brief(text)
    except (CompileError, ExtractError) as exc:
        raise SponsorLintError(str(exc)) from exc
    payload = json.dumps(spec.model_dump(exclude_none=True), indent=2)

    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"Wrote {args.out} — {len(spec.rules)} rules, "
              f"{len(spec.manual_review)} for manual review.")
        print("Review it before verifying: the spec is yours, not the model's.")
    else:
        print(payload)
    return 0


# --------------------------------------------------------------------------
# transcribe — faster-whisper + ffprobe
# --------------------------------------------------------------------------


def _transcribe(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sponsorlint transcribe")
    parser.add_argument("video")
    parser.add_argument("-o", "--out", help="write the transcript here")
    parser.add_argument("--model", default="base.en")
    args = parser.parse_args(argv)

    from .transcript.transcribe import TranscribeError, transcribe  # faster-whisper imported here

    try:
        transcript = transcribe(Path(args.video), model_size=args.model)
    except TranscribeError as exc:
        raise SponsorLintError(str(exc)) from exc
    payload = json.dumps(transcript.model_dump(), indent=2)

    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"Wrote {args.out} — {len(transcript.segments)} segments, "
              f"{transcript.duration_seconds:.1f}s.")
    else:
        print(payload)
    return 0


# --------------------------------------------------------------------------
# serve — the web UI
# --------------------------------------------------------------------------


def _serve(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sponsorlint serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    import uvicorn

    print(f"SponsorLint UI on http://{args.host}:{args.port}")
    uvicorn.run("sponsorlint.web.app:app", host=args.host, port=args.port, log_level="warning")
    return 0


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def _read_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise SponsorLintError(
            f"Could not read the {what}: {path} does not exist. "
            f"Run documented commands from the repo root."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SponsorLintError(f"{path} is not valid JSON — {exc}") from exc


def _load_spec(path: Path) -> Spec:
    from pydantic import ValidationError

    try:
        return Spec.model_validate(_read_json(path, "spec"))
    except ValidationError as exc:
        raise SponsorLintError(f"{path} is not a valid specification:\n{exc}") from exc


def _load_transcript(path: Path) -> Transcript:
    from pydantic import ValidationError

    try:
        return Transcript.model_validate(_read_json(path, "transcript"))
    except ValidationError as exc:
        raise SponsorLintError(f"{path} is not a valid transcript:\n{exc}") from exc
