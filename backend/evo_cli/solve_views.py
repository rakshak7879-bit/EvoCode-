"""Terminal rendering for solve sessions: breakdown, suspects, plan, test runs and the gate."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from evo_cli.console import Console, Style
from evo_cli.orchestration import status_symbol
from orchestrator.solving import GATE_PASS, GATE_UNKNOWN, GateResult, SolveSession
from repo.test_runner import TestReport

STATUS_STYLE = {
    "passed": Style.BRIGHT_GREEN,
    "failed": Style.BRIGHT_RED,
    "error": Style.BRIGHT_YELLOW,
    "timeout": Style.BRIGHT_YELLOW,
    "skipped": Style.DIM,
}


def print_session_header(console: Console, session: SolveSession) -> None:
    console.heading(f"Solve session {session.id}",
                    f"{session.mode} workspace · {session.root} · repository {session.repository_id}")


def print_breakdown(console: Console, session: SolveSession) -> None:
    analysis = session.analysis
    console.heading("Issue breakdown", f"{'/'.join(analysis.get('kinds') or ['unspecified'])} · "
                                       f"{len(analysis.get('terms') or [])} search terms")
    if analysis.get("summary"):
        console.wrapped(analysis["summary"], indent=2)
    if analysis.get("root_cause"):
        console.write()
        console.write(console.paint("  Likely root cause", Style.BOLD))
        console.wrapped(analysis["root_cause"], indent=4)
    for label, key in (("Expected", "expected"), ("Reported", "actual")):
        values = analysis.get(key) or []
        if values:
            console.write()
            console.write(console.paint(f"  {label}", Style.BOLD))
            for value in values:
                console.wrapped(f"• {value}", indent=4, subsequent=6)
    signals = []
    for exception in analysis.get("exceptions") or []:
        signals.append(exception["type"] + (f": {exception['message']}" if exception["message"] else ""))
    for frame in analysis.get("frames") or []:
        signals.append(f"{frame['file']}:{frame['line']}")
    if signals:
        console.write()
        console.write(console.paint("  Signals from the issue", Style.BOLD))
        for signal in signals[:6]:
            console.wrapped(f"• {signal}", indent=4, subsequent=6, style=Style.BRIGHT_CYAN)
    criteria = analysis.get("acceptance_criteria") or []
    if criteria:
        console.write()
        console.write(console.paint("  Acceptance criteria", Style.BOLD, Style.BRIGHT_GREEN))
        for index, item in enumerate(criteria, start=1):
            console.wrapped(f"{index}. {item}", indent=4, subsequent=7)


def print_suspects(console: Console, session: SolveSession) -> None:
    suspects = session.suspects
    console.heading("Suspect code", f"{len(suspects)} location(s) ranked by the Code Locator")
    if not suspects:
        console.write(console.paint("  No candidate located. Add file names or a stack trace to the issue.",
                                    Style.DIM))
        return
    rows = [
        (index, f"{suspect['file']}:{suspect['line_start']}-{suspect['line_end']}", suspect.get("symbol") or "—",
         f"{suspect['score']:.1f}", "; ".join(suspect.get("reasons") or []))
        for index, suspect in enumerate(suspects, start=1)
    ]
    console.table(("#", "LOCATION", "SYMBOL", "SCORE", "WHY"), rows, max_widths=(3, 42, 22, 6, 46))


def print_plan(console: Console, session: SolveSession) -> None:
    steps = (session.plan or {}).get("steps") or []
    console.heading("Fix plan", f"{len(steps)} step(s) · strategy {(session.plan or {}).get('strategy', 'n/a')}")
    for step in steps:
        where = ""
        if step.get("file"):
            where = f" ({step['file']}" + (f":{step['line_start']}" if step.get("line_start") else "") + ")"
        console.write()
        console.write(f"  {console.paint(str(step['order']).rjust(2), Style.BOLD, Style.BRIGHT_MAGENTA)}  "
                      f"{console.paint(step['action'], Style.BOLD)}{console.paint(where, Style.DIM)}")
        if step.get("detail"):
            console.wrapped(step["detail"], indent=6)


def print_test_report(console: Console, report: TestReport, *, title: str = "Test run", show_output: bool = False,
                      output_lines: int = 20) -> None:
    style = STATUS_STYLE.get(report.status, Style.DIM)
    where = f" in {report.directory}/" if report.directory else ""
    console.heading(title, f"{report.runner}{where} · {' '.join(report.command[-3:])}")
    console.write(f"  {status_symbol(console, 'complete' if report.ok else 'failed')} "
                  f"{console.paint(report.status.upper(), Style.BOLD, style)}  {report.summary()}")
    if report.failing:
        console.write()
        console.write(console.paint("  Failing", Style.BOLD))
        for name in report.failing[:10]:
            console.write(f"    {console.paint(name, Style.BRIGHT_RED)}")
        if len(report.failing) > 10:
            console.write(console.paint(f"    …and {len(report.failing) - 10} more", Style.DIM))
    if report.message:
        console.write()
        console.wrapped(report.message, indent=2, style=Style.DIM)
    if show_output and report.output:
        console.write()
        console.write(console.paint(f"  Output (last {output_lines} lines)", Style.BOLD))
        for line in report.output.strip().splitlines()[-output_lines:]:
            console.write(f"    {console.paint(line, Style.DIM)}")


def print_gate(console: Console, gate: GateResult) -> None:
    passed = gate.status == GATE_PASS
    style = Style.BRIGHT_GREEN if passed else (Style.BRIGHT_YELLOW if gate.status == GATE_UNKNOWN else Style.BRIGHT_RED)
    console.heading("Solve gate", "every check must pass for the issue to count as solved")
    for check in gate.checks:
        console.write(f"  {status_symbol(console, 'complete' if check['ok'] else 'failed')} "
                      f"{console.paint(check['name'].ljust(10), Style.BOLD)} {check['detail']}")
    console.write()
    console.write(f"  {console.paint(gate.status.upper(), Style.BOLD, style)}"
                  + (f" · {len(gate.changed_files)} file(s) changed" if gate.changed_files else ""))
    for reason in gate.reasons:
        console.write(f"    {console.paint('-', Style.DIM)} {reason}")


def print_sessions(console: Console, sessions: list[SolveSession]) -> None:
    console.heading("Solve sessions", f"{len(sessions)} stored locally")
    rows = []
    for session in sessions:
        gate = (session.gate or {}).get("status", "—")
        rows.append((session.id, (session.analysis.get("title") or session.issue.splitlines()[0])[:46],
                     session.mode, session.runner or "—", str(len(session.edits)),
                     console.paint(gate, Style.BRIGHT_GREEN if gate == GATE_PASS else Style.DIM)))
    console.table(("SESSION", "ISSUE", "MODE", "RUNNER", "EDITS", "GATE"), rows,
                  max_widths=(12, 46, 8, 8, 5, 8))


def print_solve_summary(console: Console, session: SolveSession) -> None:
    baseline = session.baseline or {}
    console.heading("Next steps", f"session {session.id}")
    lines = [
        f"1. Read the plan above (or `./evo plan --session {session.id}`).",
        "2. Edit the repository, or send a patch: "
        f"`./evo apply --session {session.id} --patch fix.patch`.",
        f"3. Run the tests: `./evo test --session {session.id}`.",
        f"4. Ask for the verdict: `./evo check --session {session.id}`.",
    ]
    for line in lines:
        console.wrapped(line, indent=2, subsequent=5)
    console.write()
    if baseline.get("status") == "failed":
        console.success(f"Failing baseline captured: {baseline['summary']} — this is the proof to fix.")
    elif baseline.get("status") == "passed":
        console.warning("The current tests all pass, so nothing reproduces the issue yet. "
                        "Add a failing test first (step 1 of the plan).")
    elif baseline:
        console.warning(f"No baseline: {baseline.get('summary') or baseline.get('status')}")


def session_payload(session: SolveSession, *, include_snippets: bool = True) -> dict[str, Any]:
    """What `--json` returns for a solve: everything a model needs to start working."""
    suspects = []
    for suspect in session.suspects:
        entry = dict(suspect)
        if not include_snippets:
            entry.pop("snippet", None)
        suspects.append(entry)
    return {
        "session": session.id,
        "repository_id": session.repository_id,
        "root": session.root,
        "mode": session.mode,
        "issue": session.issue,
        "analysis": session.analysis,
        "suspects": suspects,
        "tests": {"runner": session.runner, "directory": session.runner_directory, "files": session.tests},
        "baseline": session.baseline,
        "plan": session.plan,
        "notes": session.notes,
        "llm": session.llm,
        "subagents": session.subagents,
        "commands": {
            "test": f"./evo --json test --session {session.id}",
            "apply": f"./evo --json apply --session {session.id} --patch PATCH_FILE",
            "write": f"./evo --json write --session {session.id} PATH --file NEW_CONTENT",
            "check": f"./evo --json check --session {session.id}",
            "diff": f"./evo --json diff --session {session.id}",
        },
    }


def read_text_argument(value: str | None, *, label: str) -> str:
    """Read text from a file path, or from stdin when the path is ``-``."""
    if not value:
        raise ValueError(f"Provide {label} (use '-' to read standard input).")
    if value == "-":
        return sys.stdin.read()
    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def print_fix_plan(console: Console, plan: Any) -> None:
    """Every proposed patch with its diff, plus what needs a human."""
    console.heading("Proposed fixes", f"goal: {plan.goal} · scope: {plan.scope} · "
                                     f"{plan.in_scope} finding(s) in scope")
    for proposal in plan.proposals:
        verified = proposal.status == "verified"
        mark = status_symbol(console, "complete" if verified else "failed")
        console.write()
        console.write(f"  {mark} {console.paint(proposal.id, Style.BOLD)} "
                      f"{console.paint(proposal.severity.upper(), Style.DIM)} {proposal.title} "
                      f"{console.paint(f'({proposal.file}:{proposal.line_start})', Style.DIM)}")
        if verified:
            console.wrapped(proposal.explanation, indent=6)
            for line in proposal.before:
                console.write(f"      {console.paint('- ' + line, Style.BRIGHT_RED)}")
            for line in proposal.after:
                console.write(f"      {console.paint('+ ' + line, Style.BRIGHT_GREEN)}")
            console.write(f"      {console.paint(proposal.verification or '', Style.DIM)}")
            for note in proposal.notes:
                console.write(f"      {console.paint('! ' + note, Style.BRIGHT_YELLOW)}")
        else:
            console.wrapped(f"rejected: {proposal.rejection}", indent=6, style=Style.DIM)
    if plan.manual:
        console.write()
        console.write(console.paint("  Needs a human", Style.BOLD))
        for item in plan.manual:
            number = console.paint(f"#{item['number']}", Style.DIM)
            where = console.paint(f"({item['file']}:{item['line']})", Style.DIM)
            console.write(f"    {number} {item['title']} {where}")
            console.wrapped(item["reason"], indent=8, style=Style.DIM)
    for note in plan.notes:
        console.write()
        console.info(note)
