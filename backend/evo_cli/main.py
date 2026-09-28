"""Argument parsing and command dispatch for ``./evo``."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from config import load_settings
from evo_cli import __version__
from evo_cli.console import Console, Style
from evo_cli.fix_flow import run_fix
from evo_cli.orchestration import orchestration_snapshot, print_orchestration
from evo_cli.repository import prepare_repository, repository_as_json, rerun_analysis, run_analysis, settings_for_cli
from evo_cli.orchestration import format_event
from evo_cli.session import InteractiveSession, parse_location
from evo_cli.solve_views import (
    print_breakdown,
    print_fix_plan,
    print_gate,
    print_plan,
    print_session_header,
    print_sessions,
    print_solve_summary,
    print_suspects,
    print_solve_report,
    print_test_report,
    read_text_argument,
    report_payload,
    session_payload,
)
from orchestrator.solving import SolveError, SolveSession
from repo import strix_adapter as strix
from repo.test_runner import TestReport
from evo_cli.views import (
    latest_agent_result,
    load_findings,
    print_agents,
    print_answer,
    print_architecture,
    print_finding,
    print_findings,
    print_history,
    print_mode,
    print_repositories,
    print_repository_summary,
    print_source,
    print_timeline,
    print_walkthrough,
    resolve_repository,
)
from logging_config import configure_logging
from repo.source_service import SourceAccessError, edit_indexed_source, read_indexed_source
from services import Services, build_services

#: The CLI closes analyses left "processing" by a dead process only after this idle time,
#: so a second terminal never interrupts an analysis that is still running.
STALE_ANALYSIS_SECONDS = 30 * 60
TREE_COMMANDS = {"tree", "orchestration"}
SOLVE_COMMANDS = {"solve", "plan", "breakdown", "report", "diff", "sessions", "test", "apply", "write", "check",
                  "scan"}
#: Exit codes the driving model can branch on.
EXIT_OK, EXIT_ERROR, EXIT_GATE_FAILED, EXIT_TESTS_FAILED = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evo",
        description="Evo Code — multi-level AI orchestration (Brain → lead agents → specialist sub-agents) "
                    "that turns a repository into verified, searchable intelligence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  ./evo                       interactive terminal wizard
  ./evo demo                  analyze the bundled ShopLite repo, then open a shell
  ./evo analyze ./project.zip --task "Only check for hardcoded secrets"
  ./evo analyze https://github.com/owner/repo
  ./evo tree REPO_ID          Brain → lead agents → specialist sub-agents
  ./evo ask REPO_ID "Where is authentication implemented?"
  ./evo findings REPO_ID --category security
  printf 'findings\\nshow 1\\n' | ./evo open REPO_ID
""",
    )
    parser.add_argument("--data-dir", help="SQLite and working-copy directory (default: .evo-data)")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI terminal colors")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output where supported")
    parser.add_argument("--verbose", action="store_true", help="print backend logs to stderr")
    parser.add_argument("--version", action="version", version=f"Evo Code CLI {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    demo = sub.add_parser("demo", help="analyze the bundled demo repository")
    demo.add_argument("--task", help="task for the Brain (a focused task narrows the agent teams)")
    demo.add_argument("--no-shell", action="store_true", help="exit after analysis")

    analyze = sub.add_parser("analyze", help="analyze a folder, ZIP or GitHub repository")
    analyze.add_argument("source", help="folder, .zip path or https://github.com/owner/repo")
    analyze.add_argument("--task", help="task for the Brain (a focused task narrows the agent teams)")
    analyze.add_argument("--no-shell", action="store_true", help="exit after analysis")

    sub.add_parser("repos", help="list local repository analyses")
    sub.add_parser("mode", help="show LLM/local mode, FTS5, data directory and agent roster")
    open_cmd = sub.add_parser("open", help="interactive shell for a repository (reads commands from a pipe too)")
    open_cmd.add_argument("repository_id", nargs="?", help="id or unambiguous prefix (default: latest)")

    for name, help_text, aliases in (
        ("status", "show repository status, orchestration and metrics", []),
        ("tree", "show the Brain → lead agents → specialist sub-agents tree", ["orchestration"]),
        ("agents", "show lead agents and their specialist sub-agents", []),
        ("architecture", "show architecture and request flows", []),
        ("walkthrough", "show the presentation walkthrough", []),
        ("verify", "re-check finding hashes against source", []),
    ):
        command = sub.add_parser(name, help=help_text, aliases=aliases)
        command.add_argument("repository_id", nargs="?", help="id or prefix (default: latest)")

    findings = sub.add_parser("findings", help="list verified findings")
    findings.add_argument("repository_id", nargs="?", help="id or prefix (default: latest)")
    findings.add_argument("--category", choices=("all", "security", "duplicate"), default="all")
    findings.add_argument("--status", choices=("all", "verified", "stale"), default="all")
    findings.add_argument("--limit", type=int, help="maximum number of rows")

    show = sub.add_parser("show", help="show a finding and its source")
    show.add_argument("repository_id", help="repository id or prefix")
    show.add_argument("number", type=int, help="finding number from `findings`")

    source = sub.add_parser("source", help="display indexed source code")
    source.add_argument("repository_id", help="repository id or prefix")
    source.add_argument("path", help="repository-relative file path (PATH:LINE works too)")
    source.add_argument("--line", help="line or inclusive range, e.g. 14-32")
    source.add_argument("--context", type=int, default=8, help="context lines (default: 8)")

    ask = sub.add_parser("ask", help="ask repository memory a question")
    ask.add_argument("repository_id", help="repository id or prefix")
    ask.add_argument("question", nargs="+", help="question")

    log = sub.add_parser("log", help="show Brain timeline events")
    log.add_argument("repository_id", nargs="?", help="id or prefix (default: latest)")
    log.add_argument("--limit", type=int, default=30)
    log.add_argument("--level", type=int, choices=(0, 1, 2), help="only events up to this orchestration level")

    edit = sub.add_parser("edit", help="replace one line in Evo Code's isolated working copy")
    edit.add_argument("repository_id", help="repository id or prefix")
    edit.add_argument("path", help="repository-relative file path")
    edit.add_argument("line", type=int)
    edit.add_argument("content", help="single replacement line")

    reanalyze = sub.add_parser("reanalyze", help="re-run the Brain on the current working copy")
    reanalyze.add_argument("repository_id", help="repository id or prefix")
    reanalyze.add_argument("--task", help="new task for the Brain")
    reanalyze.add_argument("--shell", action="store_true", help="enter interactive shell afterward")

    _add_solve_commands(sub)
    return parser


def _add_solve_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The harness commands an external model (for example DeepSeek) drives."""
    solve = sub.add_parser("solve", help="break an issue down, locate the code and capture a failing baseline")
    solve.add_argument("issue", nargs="*", help="the issue text (also accepted via --issue or --issue-file)")
    solve.add_argument("--repo", default=".", help="repository to work on (default: current directory)")
    solve.add_argument("--issue", dest="issue_text", metavar="TEXT", help="the issue text")
    solve.add_argument("--issue-file", help="file containing the issue text ('-' reads stdin)")
    solve.add_argument("--isolated", action="store_true",
                       help="work on a copy instead of the real repository (edits will not affect your files)")
    solve.add_argument("--reuse", help="reuse an existing repository index by id")
    solve.add_argument("--no-snippets", action="store_true", help="omit source snippets from --json output")

    for name, help_text in (("plan", "show the fix plan of a solve session"),
                            ("breakdown", "show the issue breakdown and suspect code"),
                            ("report", "what the issue was, how it was solved and the proof"),
                            ("diff", "show the changes made in a solve session")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--session", help="session id or prefix (default: the most recent)")
    sessions = sub.add_parser("sessions", help="list solve sessions")
    sessions.add_argument("--limit", type=int, default=20)

    test = sub.add_parser("test", help="run the repository's own tests (sandboxed)")
    test.add_argument("--session", help="session id or prefix (default: the most recent)")
    test.add_argument("--repo", help="run against this path instead of a session")
    test.add_argument("--tests", nargs="*", default=[], help="only these test files or ids")
    test.add_argument("--runner", help="force a runner (pytest, npm, unittest, go, cargo)")
    test.add_argument("--covering", action="store_true", help="run only the tests the Test Scout found")
    test.add_argument("--output", type=int, default=0, help="also print the last N lines of output")

    apply_cmd = sub.add_parser("apply", help="apply a unified diff to the session's repository")
    apply_cmd.add_argument("--session", help="session id or prefix (default: the most recent)")
    apply_cmd.add_argument("--patch", help="patch file ('-' reads stdin)")

    write = sub.add_parser("write", help="replace one file's contents in the session's repository")
    write.add_argument("path", help="repository-relative file path")
    write.add_argument("--session", help="session id or prefix (default: the most recent)")
    write.add_argument("--file", help="file holding the new contents ('-' reads stdin)")

    fix = sub.add_parser("fix", help="propose and apply verified fixes for findings of an analysis")
    fix.add_argument("repository_id", nargs="?",
                     help="id, prefix, URL or path (default: latest analysis). May be omitted before the goal.")
    fix.add_argument("goal", nargs="*", help='what to fix, e.g. "the hardcoded secrets" (default: all security)')
    fix.add_argument("--goal", dest="goal_text", metavar="TEXT", help="what to fix (unambiguous form)")
    fix.add_argument("--yes", "-y", action="store_true", help="apply every verified patch without asking")
    fix.add_argument("--dry-run", action="store_true", help="only show the patches")
    fix.add_argument("--no-reanalyze", action="store_true", help="skip the re-analysis after applying")

    scan = sub.add_parser("scan", help="optional deep security scan with Strix (external tool)")
    scan.add_argument("--repo", default=".", help="target repository (default: current directory)")
    scan.add_argument("--mode", choices=("quick", "standard", "deep"), default="quick")
    scan.add_argument("--instruction", help="extra guidance passed to Strix")
    scan.add_argument("--max-budget", type=float, help="cap Strix LLM spend in USD")
    scan.add_argument("--timeout", type=float, default=1800.0, help="seconds before Strix is stopped")
    scan.add_argument("--check", action="store_true", help="only report whether Strix can run")

    check = sub.add_parser("check", help="the gate: decide whether the issue is solved")
    check.add_argument("--session", help="session id or prefix (default: the most recent)")
    check.add_argument("--require-new-test", action="store_true", help="also require a test to be added or changed")
    check.add_argument("--save-patch", action="store_true", help="write the changes to a .patch file")
    check.add_argument("--allow-test-edits", action="store_true",
                       help="accept a fix even if it modified the failing test (off by default)")
    check.add_argument("--output", type=int, default=0, help="also print the last N lines of test output")


def _interactive() -> bool:
    return sys.stdin.isatty()


async def _analyze(
    services: Services,
    console: Console,
    source: str,
    task: str | None,
    *,
    json_output: bool,
    no_shell: bool,
) -> int:
    if not json_output:
        console.banner()
        console.heading("Preparing repository", source)
        console.info("Repository content is untrusted data. Evo Code never executes it.")
    prepared = prepare_repository(services, source, task)
    if not json_output:
        console.heading("Live orchestration",
                        "L0 Evo Brain → L1 lead agents → L2 specialist sub-agents → source verification")
    repo = await run_analysis(services, prepared, console, json_output=json_output)
    if json_output:
        console.write(repository_as_json(repo, services))
    else:
        if repo["status"] == "completed":
            print_orchestration(console, services, repo)
        print_repository_summary(console, services, repo)
    if repo["status"] != "completed":
        return 1
    if not no_shell and not json_output and _interactive():
        return await InteractiveSession(services, console, repo["id"]).run()
    if not json_output:
        console.write()
        console.write(console.paint(f"Next: ./evo open {repo['id'][:8]}  ·  ./evo tree {repo['id'][:8]}  ·  "
                                    f"./evo findings {repo['id'][:8]}", Style.DIM))
    return 0


async def _wizard(services: Services, console: Console) -> int:
    console.banner()
    info = services.llm.describe()
    console.write(f"  Mode: {console.paint(info['label'], Style.BRIGHT_CYAN)}")
    repos = services.store.list_repositories(limit=5)
    if repos:
        print_repositories(console, services)
    console.heading("Start")
    console.write("  [1] Analyze bundled demo repository")
    console.write("  [2] Analyze a local folder, ZIP or GitHub URL")
    if repos:
        console.write("  [3] Open the most recent analysis")
    console.write("  [q] Quit")
    choice = (console.prompt("choice › ") or "q").strip().lower()
    if choice in {"q", "quit", "exit", ""}:
        return 0
    if choice == "1":
        return await _analyze(services, console, "demo", None, json_output=False, no_shell=False)
    if choice == "2":
        source = (console.prompt("repository path / ZIP / GitHub URL › ") or "").strip()
        if not source:
            raise ValueError("No repository source entered.")
        task = (console.prompt("Brain task (Enter for full analysis) › ") or "").strip() or None
        return await _analyze(services, console, source, task, json_output=False, no_shell=False)
    if choice == "3" and repos:
        return await InteractiveSession(services, console, repos[0]["id"]).run()
    raise ValueError(f"Unknown choice: {choice}")


def _findings_summary(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {status: sum(f["status"] == status for f in findings) for status in ("verified", "stale", "missing")}


async def dispatch(args: argparse.Namespace, services: Services, console: Console) -> int:
    command = args.command
    if command is None:
        if not _interactive():
            build_parser().print_help()
            return 0
        return await _wizard(services, console)
    if command == "demo":
        return await _analyze(services, console, "demo", args.task, json_output=args.json, no_shell=args.no_shell)
    if command == "analyze":
        return await _analyze(services, console, args.source, args.task, json_output=args.json, no_shell=args.no_shell)
    if command == "repos":
        repos = services.store.list_repositories(limit=30)
        if args.json:
            console.write(json.dumps(
                [{key: repo[key] for key in ("id", "name", "source", "status", "analysis_count", "created_at")}
                 for repo in repos], indent=2))
        else:
            print_repositories(console, services)
        return 0
    if command == "mode":
        if args.json:
            console.write(json.dumps({**services.llm.describe(), "fts5": services.db.fts5_enabled,
                                      "data_dir": str(services.settings.data_dir)}, indent=2))
        else:
            print_mode(console, services)
        return 0

    if command in SOLVE_COMMANDS:
        return await dispatch_solve(args, services, console)
    if command == "fix":
        return await _fix(args, services, console)

    repo = resolve_repository(services.store, getattr(args, "repository_id", None))
    if command == "open":
        session = InteractiveSession(services, console, repo["id"])
        if not _interactive():
            code = await session.run_script(sys.stdin)
            if not session.commands_run:
                print_repository_summary(console, services, repo)
            return code
        console.banner()
        print_repository_summary(console, services, repo)
        return await session.run()
    if command == "status":
        if args.json:
            console.write(repository_as_json(repo, services))
        else:
            print_repository_summary(console, services, repo)
    elif command in TREE_COMMANDS:
        if args.json:
            console.write(json.dumps(orchestration_snapshot(services, repo), indent=2))
        else:
            print_orchestration(console, services, repo)
    elif command == "agents":
        if args.json:
            console.write(json.dumps(orchestration_snapshot(services, repo)["leads"], indent=2))
        else:
            print_agents(console, services, repo)
    elif command == "findings":
        findings = load_findings(services, repo)
        if args.json:
            console.write(json.dumps(findings, indent=2))
        else:
            print_findings(console, findings, category=args.category, only_status=args.status, limit=args.limit)
    elif command == "show":
        findings = load_findings(services, repo)
        index = args.number - 1
        if index < 0 or index >= len(findings):
            raise ValueError(f"Finding number must be between 1 and {len(findings)}.")
        finding = findings[index]
        if args.json:
            console.write(json.dumps(finding, indent=2))
            return 0
        print_finding(console, finding, args.number)
        source = read_indexed_source(services.store, repo["id"], Path(repo["path"]), finding["file"])
        print_source(console, source, line_start=finding["line"], line_end=finding["line_end"], context=5)
    elif command == "source":
        path, start, end = parse_location(args.path, args.line)
        source = read_indexed_source(services.store, repo["id"], Path(repo["path"]), path)
        print_source(console, source, line_start=start, line_end=end, context=max(0, args.context))
    elif command == "ask":
        result = await services.brain.answer(repo["id"], " ".join(args.question), 6)
        if args.json:
            console.write(json.dumps(result, indent=2))
        else:
            print_answer(console, result)
    elif command == "architecture":
        result = latest_agent_result(services, repo, "explainer")
        if not result:
            raise ValueError("Architecture is unavailable: the Explainer Agent did not complete.")
        if args.json:
            console.write(json.dumps(result.get("data", {}), indent=2))
        else:
            print_architecture(console, result.get("data", {}))
    elif command == "walkthrough":
        result = latest_agent_result(services, repo, "walkthrough")
        if not result:
            raise ValueError("Walkthrough is unavailable: the Walkthrough Agent did not complete.")
        if args.json:
            console.write(json.dumps(result.get("data", {}), indent=2))
        else:
            print_walkthrough(console, result.get("data", {}))
    elif command == "verify":
        findings = load_findings(services, repo)
        statuses = _findings_summary(findings)
        if args.json:
            console.write(json.dumps({"repository_id": repo["id"], "total": len(findings), **statuses}, indent=2))
        elif statuses["verified"] == len(findings):
            console.success(f"{len(findings)}/{len(findings)} findings verified against current source.")
        else:
            console.warning(f"{statuses['verified']}/{len(findings)} verified · {statuses['stale']} stale · "
                            f"{statuses['missing']} missing")
            print_findings(console, findings, only_status="stale")
    elif command == "log":
        print_timeline(console, repo, max(1, min(args.limit, 250)), level=args.level)
    elif command == "edit":
        if not services.settings.allow_source_edits:
            raise ValueError("Source edits are disabled (EVO_ALLOW_SOURCE_EDITS=false).")
        result = edit_indexed_source(services.store, repo["id"], Path(repo["path"]), args.path, args.line, args.content)
        if args.json:
            console.write(json.dumps(result.__dict__, indent=2))
        else:
            console.success(f"Working copy updated: {result.sha256_before[:10]}… → {result.sha256_after[:10]}…")
            console.info(f"The original repository was not changed. Run `./evo verify {repo['id'][:8]}` "
                         "to see which findings went stale.")
    elif command == "reanalyze":
        if not args.json:
            console.heading("Re-analyzing current working copy", "Brain → lead agents → specialist sub-agents")
        repo = await rerun_analysis(services, repo["id"], console, task=args.task, json_output=args.json)
        if args.json:
            console.write(repository_as_json(repo, services))
        else:
            print_repository_summary(console, services, repo)
            print_history(console, repo)
        if repo["status"] != "completed":
            return 1
        if args.shell and _interactive():
            return await InteractiveSession(services, console, repo["id"]).run()
    return 0


async def dispatch_solve(args: argparse.Namespace, services: Services, console: Console) -> int:
    """The harness loop: solve → (model edits) → test → check."""
    coordinator = services.solve
    command = args.command
    if command == "sessions":
        sessions = coordinator.sessions.list(max(1, args.limit))
        if args.json:
            console.write(json.dumps([session_payload(session, include_snippets=False) for session in sessions],
                                     indent=2))
        else:
            print_sessions(console, sessions)
        return EXIT_OK

    if command == "scan":
        return _strix_scan(args, console)

    if command == "solve":
        issue = (args.issue_text or " ".join(args.issue)).strip()
        if args.issue_file:
            issue = read_text_argument(args.issue_file, label="an issue file")
        if not issue and not _interactive():
            issue = sys.stdin.read()
        if not issue.strip():
            raise ValueError("Describe the issue: ./evo solve \"...\" or --issue-file bug.md")
        if not args.json:
            console.banner()
            console.heading("Solving an issue", "L0 Evo Brain → L1 Solver Agent → L2 specialist sub-agents")
            console.info("Evo Code reads the repository and runs its tests. It does not change your code here.")
        sink = None if args.json else (lambda event: console.write(format_event(console, event,
                                                                              truncate=console.is_tty)))
        session = await coordinator.solve(Path(args.repo), issue, isolated=args.isolated, sink=sink, reuse=args.reuse)
        if args.json:
            console.write(json.dumps(session_payload(session, include_snippets=not args.no_snippets), indent=2))
            return EXIT_OK
        print_session_header(console, session)
        print_breakdown(console, session)
        print_suspects(console, session)
        if session.baseline:
            reproduced = session.baseline.get("status") == "failed"
            print_test_report(console, TestReport(**_report_fields(session.baseline)),
                              title="Baseline · issue reproduced" if reproduced
                              else "Baseline · issue not reproduced yet")
        print_plan(console, session)
        print_solve_summary(console, session)
        return EXIT_OK

    if command == "test" and args.repo and not args.session:
        session = SolveSession(id="ad-hoc", repository_id="", root=str(Path(args.repo).expanduser().resolve()),
                              issue="ad-hoc test run", ephemeral=True)
    else:
        session = coordinator.sessions.resolve(args.session)

    if command in {"plan", "breakdown"}:
        if args.json:
            console.write(json.dumps(session_payload(session), indent=2))
            return EXIT_OK
        print_session_header(console, session)
        if command == "breakdown":
            print_breakdown(console, session)
            print_suspects(console, session)
        print_plan(console, session)
        return EXIT_OK

    if command == "report":
        diff, changed = coordinator.diff(session), coordinator.changed_files(session)
        if args.json:
            console.write(json.dumps(report_payload(session, diff, changed), indent=2))
        else:
            print_solve_report(console, session, diff, changed)
        return EXIT_OK if (session.gate or {}).get("status") == "pass" else EXIT_GATE_FAILED

    if command == "diff":
        patch = coordinator.diff(session)
        changed = coordinator.changed_files(session)
        if args.json:
            console.write(json.dumps({"session": session.id, "changed_files": changed, "patch": patch}, indent=2))
        elif patch.strip():
            console.write(patch, end="")
        else:
            console.info(f"No changes in {session.root} yet.")
        return EXIT_OK

    if command == "test":
        targets = tuple(args.tests) or (tuple(session.tests) if args.covering else ())
        report = coordinator.run_tests(session, targets=targets, runner_key=args.runner,
                                       scope="covering" if targets else "suite")
        if args.json:
            console.write(json.dumps({"session": session.id, **report.to_dict()}, indent=2))
        else:
            print_test_report(console, report, title=f"Tests · {session.root}",
                              show_output=bool(args.output) or not report.ok,
                              output_lines=args.output or 20)
        return EXIT_OK if report.ok else EXIT_TESTS_FAILED

    if command == "apply":
        patch = read_text_argument(args.patch, label="a patch file")
        entry = coordinator.apply_patch(session, patch)
        if args.json:
            console.write(json.dumps({"session": session.id, **entry}, indent=2))
        else:
            console.success(f"Applied the patch to {len(entry['files'])} file(s): {', '.join(entry['files'][:4])}")
            console.info(f"Now run `./evo check --session {session.id}`.")
        return EXIT_OK

    if command == "write":
        content = read_text_argument(args.file, label="the new file contents")
        entry = coordinator.write_file(session, args.path, content)
        if args.json:
            console.write(json.dumps({"session": session.id, **entry}, indent=2))
        else:
            console.success(f"{'Created' if entry['created'] else 'Updated'} {args.path} "
                            f"({entry['bytes']} bytes) in {session.root}")
            console.info(f"Now run `./evo check --session {session.id}`.")
        return EXIT_OK

    if command == "check":
        gate = coordinator.check(session, require_new_test=args.require_new_test,
                                 allow_test_edits=args.allow_test_edits)
        patch_path = coordinator.save_patch(session) if args.save_patch else None
        if args.json:
            console.write(json.dumps({"session": session.id, **gate.to_dict(),
                                      "patch_path": str(patch_path) if patch_path else None}, indent=2))
        else:
            print_gate(console, gate)
            if gate.report and (args.output or not gate.report.ok):
                print_test_report(console, gate.report, title="Test output", show_output=True,
                                  output_lines=args.output or 20)
            if patch_path:
                console.info(f"Patch written to {patch_path}")
        return EXIT_OK if gate.passed else EXIT_GATE_FAILED
    return EXIT_OK


_REPOSITORY_REFERENCE = re.compile(r"^[0-9a-f]{6,32}$")


def _looks_like_repository(value: str) -> bool:
    """True when a word was clearly meant as a repository, not as part of a fix goal."""
    if _REPOSITORY_REFERENCE.match(value) or "://" in value or value.endswith(".zip"):
        return True
    try:
        return Path(value).expanduser().exists()
    except OSError:
        return False


async def _fix(args: argparse.Namespace, services: Services, console: Console) -> int:
    """Fixer Agent: propose verified patches for an analysis, then apply the approved ones.

    The repository is optional and may be omitted entirely, so both
    ``./evo fix REPO "goal"`` and ``./evo fix "goal"`` do what they look like.
    """
    reference, words = args.repository_id, list(args.goal)
    repo = None
    if reference:
        try:
            repo = resolve_repository(services.store, reference)
        except ValueError:
            if _looks_like_repository(reference):
                raise
            words.insert(0, reference)  # it was the start of the goal
    if repo is None:
        repo = resolve_repository(services.store, None)
    goal = (args.goal_text or " ".join(words)).strip()
    outcome = await run_fix(
        services, console, repo, goal,
        assume_yes=args.yes, dry_run=args.dry_run, reanalyze=not args.no_reanalyze, json_output=args.json,
    )
    # Exit 2 only when there was something to fix and no patch could be verified.
    if outcome.in_scope and not outcome.verified and not args.dry_run:
        return EXIT_GATE_FAILED
    return EXIT_OK


def _strix_scan(args: argparse.Namespace, console: Console) -> int:
    """Optional deep security scan delegated to Strix; degrades cleanly when it is absent."""
    state = strix.availability()
    if args.check or not state.available:
        if args.json:
            console.write(json.dumps(state.to_dict(), indent=2))
        elif state.available:
            console.success(state.reason)
        else:
            console.warning(state.reason)
            console.write(console.paint("  Evo Code's own security agents work without Strix: "
                                        "./evo analyze .", Style.DIM))
        return EXIT_OK if state.available else EXIT_ERROR
    target = Path(args.repo).expanduser().resolve()
    if not args.json:
        console.heading("Strix security scan", f"{args.mode} scan of {target} (external tool, Docker sandbox)")
        console.info("Strix runs the target in its own sandbox. Evo Code never executes repository code itself.")
    scan = strix.run_scan(target, mode=args.mode, instruction=args.instruction, timeout=args.timeout,
                          max_budget=args.max_budget)
    if args.json:
        console.write(json.dumps(scan.to_dict(), indent=2))
    else:
        console.write()
        (console.error if scan.status == "error" else console.success)(scan.message)
        rows = [(finding["severity"].upper(), finding["title"],
                 f"{finding['file']}:{finding['line']}" if finding.get("file") else "—",
                 "yes" if finding["validated"] else "no")
                for finding in scan.findings]
        if rows:
            console.table(("SEVERITY", "FINDING", "LOCATION", "POC"), rows, max_widths=(9, 52, 30, 4))
        if scan.run_directory:
            console.info(f"Full Strix run: {scan.run_directory}")
    if scan.status == "error":
        return EXIT_ERROR
    return EXIT_GATE_FAILED if scan.findings else EXIT_OK


def _report_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a TestReport from its stored dict (dropping derived keys)."""
    fields = {key: value for key, value in payload.items() if key in TestReport.__dataclass_fields__}
    fields["command"] = tuple(fields.get("command") or ())
    fields["failing"] = tuple(fields.get("failing") or ())
    return fields


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(color=False if args.no_color or args.json else None)
    try:
        settings = settings_for_cli(load_settings(), args.data_dir)
        configure_logging(settings.log_level if args.verbose else "CRITICAL", settings.log_format == "json")
        services = build_services(settings, recover_after_seconds=STALE_ANALYSIS_SECONDS)
        return asyncio.run(dispatch(args, services, console))
    except (ValueError, KeyError, SourceAccessError, SolveError, RuntimeError, OSError) as exc:
        message = str(exc)
        if args.json:
            console.write(json.dumps({"error": message, "command": args.command}))
        else:
            console.error(message)
        return EXIT_ERROR
    except KeyboardInterrupt:
        console.write()
        console.warning("Cancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

