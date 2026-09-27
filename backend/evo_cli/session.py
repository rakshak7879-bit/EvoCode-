"""Interactive Evo Code terminal session.

``run()`` reads commands from the terminal. ``run_script()`` reads them from a
file or pipe (``printf 'findings\\nshow 1\\n' | ./evo open ID``) and echoes each
command, so a session can be scripted and its transcript stays readable.
"""

from __future__ import annotations

import shlex
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from evo_cli.console import Console, Style, enable_line_editing
from evo_cli.fix_flow import run_fix
from evo_cli.orchestration import print_orchestration
from evo_cli.repository import rerun_analysis
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
from repo.source_service import SourceAccessError, edit_indexed_source, read_indexed_source
from services import Services

HELP = """
Repository
  status                      Current repository, orchestration, metrics and verification
  repos                       List analyses stored in local memory
  use ID                      Switch repository (an 8-char prefix is enough)

Orchestration (L0 Brain → L1 lead agents → L2 specialist sub-agents)
  tree                        Full multi-level orchestration tree of the latest analysis
  agents                      Lead agents and their sub-agents with status and timing
  log [N] [L0|L1|L2]          Last N Brain timeline events (default 30), optionally up to a level

Intelligence
  findings [all|security|duplicate] [all|verified|stale]
                              List findings (live source verification)
  show N                      Finding details and cited source
  source PATH [LINE[-END]]    Display source with highlighted lines
  architecture                Project summary, stack, flows and key files
  walkthrough                 Presentation-ready repository walkthrough
  ask QUESTION                Ask indexed memory; returns verified citations
  verify                      Re-check all finding hashes against current source

Working copy
  fix [GOAL]                  Propose verified patches for the findings, then apply the ones you approve
  edit PATH LINE [TEXT]       Replace one line in the isolated working copy, then detect STALE
  reanalyze [TASK]            Re-run the Brain on the working copy and update memory

Session
  mode                        LLM/local mode, FTS5, data directory and agent roster
  clear                       Clear terminal
  help                        Show this help
  exit                        Leave Evo Code (Ctrl-D works too)
""".strip()

LEVELS = {"l0": 0, "l1": 1, "l2": 2, "brain": 0, "leads": 1, "all": 2}
COMMANDS = (
    "status", "repos", "use", "tree", "orchestration", "agents", "log", "findings", "show", "source", "ask",
    "architecture", "walkthrough", "verify", "fix", "edit", "reanalyze", "mode", "clear", "help", "exit",
)
#: Commands that only exist as one-shot CLI commands, with the reason.
ONE_SHOT = {
    "solve": "solve works on a repository path and an issue: ./evo solve --repo . --issue-file issue.md",
    "check": "check judges a solve session: ./evo check --session SESSION",
    "test": "test runs a repository's own suite: ./evo test --repo .",
    "apply": "apply takes a patch file: ./evo apply --session SESSION --patch fix.patch",
    "write": "write replaces a file: ./evo write --session SESSION PATH --file NEW",
    "scan": "scan delegates to Strix: ./evo scan --repo .",
    "demo": "demo starts a new analysis: ./evo demo",
    "sessions": "sessions lists solve sessions: ./evo sessions",
}


def parse_location(path: str, line: str | None = None) -> tuple[str, int, int]:
    if line is None and ":" in path:
        candidate_path, candidate_line = path.rsplit(":", 1)
        if candidate_line.replace("-", "").isdigit():
            path, line = candidate_path, candidate_line
    if line is None:
        return path, 1, 1
    if "-" in line:
        start_text, end_text = line.split("-", 1)
    else:
        start_text = end_text = line
    try:
        start, end = int(start_text), int(end_text)
    except ValueError as exc:
        raise ValueError("Line must be a number or range such as 14-24.") from exc
    if start < 1 or end < start:
        raise ValueError("Line range must start at 1 or later, with END >= START.")
    return path, start, end


class InteractiveSession:
    def __init__(self, services: Services, console: Console, repository_id: str | None = None) -> None:
        self.services = services
        self.console = console
        self.repository_id = repository_id
        self.last_findings: list[dict[str, Any]] = []
        self.scripted = False
        self.commands_run = 0
        self._script: Iterable[str] | None = None

    def repo(self) -> dict[str, Any]:
        repo = resolve_repository(self.services.store, self.repository_id)
        self.repository_id = repo["id"]
        return repo

    @property
    def prompt_label(self) -> str:
        return f"evo[{self.repository_id[:8] if self.repository_id else 'none'}] › "

    async def run(self) -> int:
        repo = self.repo()
        save_history = enable_line_editing(self.services.settings.data_dir / "cli_history", COMMANDS)
        self.console.line_editing = save_history is not None
        self.console.write()
        self.console.success(f"Interactive session ready for {repo['name']}. Type `help` for commands "
                             "(Tab completes commands, arrow keys recall history).")
        try:
            while True:
                try:
                    line = self.console.prompt(self.prompt_label)
                except KeyboardInterrupt:
                    self.console.write()
                    self.console.write(self.console.paint("Use `exit` or Ctrl-D to leave Evo Code.", Style.DIM))
                    continue
                if line is None:  # Ctrl-D
                    self.console.write()
                    return 0
                if not await self.execute(line):
                    return 0
        finally:
            if save_history is not None:
                save_history()

    async def run_script(self, lines: Iterable[str]) -> int:
        """Execute commands from a non-interactive source; returns 1 if any command failed."""
        self.repo()
        self.scripted = True
        self._script = iter(lines)
        failed = False
        for raw in self._script:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            self.console.write(self.console.paint(self.prompt_label, Style.BOLD, Style.BRIGHT_CYAN) + line)
            self.commands_run += 1
            ok, keep_going = await self._execute(line)
            failed = failed or not ok
            if not keep_going:
                break
        return 1 if failed else 0

    async def execute(self, line: str) -> bool:
        """Run one command. Returns False when the session should end."""
        _, keep_going = await self._execute(line)
        return keep_going

    async def _execute(self, line: str) -> tuple[bool, bool]:
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            self.console.error(str(exc))
            return False, True
        # People naturally retype the launcher inside the shell: accept "./evo findings".
        if parts and parts[0] in {"./evo", "evo"}:
            parts = parts[1:]
            if parts:
                self.console.write(self.console.paint(
                    f"(inside the shell you can just type `{parts[0]}`)", Style.DIM))
        if not parts:
            return True, True
        command, args = parts[0].lower(), parts[1:]
        try:
            if command in {"exit", "quit", "q"}:
                self.console.write(self.console.paint("Goodbye.", Style.DIM))
                return True, False
            if command in {"help", "?"}:
                self.console.heading("Commands")
                self.console.write(HELP)
            elif command == "clear":
                self.console.clear()
                self.console.banner()
            elif command == "status":
                print_repository_summary(self.console, self.services, self.repo())
            elif command == "repos":
                print_repositories(self.console, self.services)
            elif command == "use":
                if not args:
                    raise ValueError("Usage: use REPOSITORY_ID")
                repo = resolve_repository(self.services.store, args[0])
                self.repository_id = repo["id"]
                self.last_findings = []
                self.console.success(f"Using {repo['name']} ({repo['id'][:8]}).")
            elif command in {"tree", "orchestration", "orch"}:
                print_orchestration(self.console, self.services, self.repo())
            elif command in {"agents", "team"}:
                print_agents(self.console, self.services, self.repo())
            elif command == "log":
                self._log(args)
            elif command in {"findings", "finding"}:
                self._findings(args)
            elif command == "show":
                self._show(args)
            elif command in {"source", "cat"}:
                self._source(args)
            elif command in {"ask", "search"}:
                await self._ask(args)
            elif command in {"architecture", "arch"}:
                self._architecture()
            elif command in {"walkthrough", "walk"}:
                self._walkthrough()
            elif command == "verify":
                self._verify()
            elif command == "edit":
                self._edit(args)
            elif command in {"reanalyze", "analyse", "analyze"}:
                await self._reanalyze(args)
            elif command == "fix":
                await self._fix(args)
            elif command == "mode":
                print_mode(self.console, self.services)
            elif command in ONE_SHOT:
                self.console.error(f"`{command}` is not a shell command. {ONE_SHOT[command]}")
                return False, True
            else:
                self.console.error(f"Unknown command: {command}. Type `help` for commands.")
                return False, True
        except (ValueError, KeyError, SourceAccessError, RuntimeError, OSError) as exc:
            self.console.error(str(exc))
            return False, True
        except KeyboardInterrupt:
            self.console.write()
            self.console.warning("Cancelled.")
            return False, True
        return True, True

    # ------------------------------------------------------------------ commands
    def _log(self, args: list[str]) -> None:
        limit, level = 30, None
        for arg in args:
            if arg.isdigit():
                limit = int(arg)
            elif arg.lower() in LEVELS:
                level = LEVELS[arg.lower()]
            else:
                raise ValueError("Usage: log [N] [L0|L1|L2]")
        print_timeline(self.console, self.repo(), max(1, min(limit, 250)), level=level)

    def _findings(self, args: list[str]) -> None:
        valid_categories = {"all", "security", "duplicate"}
        valid_statuses = {"all", "verified", "stale"}
        category = args[0].lower() if args and args[0].lower() in valid_categories else "all"
        status_arg = args[1].lower() if len(args) > 1 else (
            args[0].lower() if args and args[0].lower() in valid_statuses else "all"
        )
        if status_arg not in valid_statuses or (args and args[0].lower() not in valid_categories | valid_statuses):
            raise ValueError("Usage: findings [all|security|duplicate] [all|verified|stale]")
        self.last_findings = print_findings(
            self.console,
            load_findings(self.services, self.repo()),
            category=category,
            only_status=status_arg,
        )

    def _show(self, args: list[str]) -> None:
        if not args or not args[0].isdigit():
            raise ValueError("Usage: show FINDING_NUMBER")
        repo = self.repo()
        if not self.last_findings:
            self.last_findings = load_findings(self.services, repo)
        index = int(args[0]) - 1
        if index < 0 or index >= len(self.last_findings):
            raise ValueError(f"Finding number must be between 1 and {len(self.last_findings)}.")
        finding = self.last_findings[index]
        print_finding(self.console, finding, index + 1)
        source = read_indexed_source(self.services.store, repo["id"], Path(repo["path"]), finding["file"])
        print_source(self.console, source, line_start=finding["line"], line_end=finding["line_end"], context=5)

    def _source(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Usage: source PATH [LINE[-END]]")
        path, start, end = parse_location(args[0], args[1] if len(args) > 1 else None)
        repo = self.repo()
        source = read_indexed_source(self.services.store, repo["id"], Path(repo["path"]), path)
        print_source(self.console, source, line_start=start, line_end=end, context=8)

    async def _ask(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Usage: ask QUESTION")
        result = await self.services.brain.answer(self.repo()["id"], " ".join(args), 6)
        print_answer(self.console, result)

    def _architecture(self) -> None:
        repo = self.repo()
        result = latest_agent_result(self.services, repo, "explainer")
        if not result:
            raise ValueError("Architecture is unavailable: the Explainer Agent did not complete.")
        print_architecture(self.console, result.get("data", {}))

    def _walkthrough(self) -> None:
        repo = self.repo()
        result = latest_agent_result(self.services, repo, "walkthrough")
        if not result:
            raise ValueError("Walkthrough is unavailable: the Walkthrough Agent did not complete.")
        print_walkthrough(self.console, result.get("data", {}))

    def _verify(self) -> None:
        findings = load_findings(self.services, self.repo())
        statuses = Counter(finding["status"] for finding in findings)
        if findings and statuses["verified"] == len(findings):
            self.console.success(f"{len(findings)}/{len(findings)} findings verified against current source.")
        else:
            self.console.warning(
                f"{statuses['verified']}/{len(findings)} verified · {statuses['stale']} stale · "
                f"{statuses['missing']} missing."
            )
            for finding in findings:
                if finding["status"] != "verified":
                    self.console.write(
                        f"  {finding['status'].upper():10} {finding['title']} — {finding['file']}:{finding['line']}"
                    )
        self.last_findings = findings

    def _edit(self, args: list[str]) -> None:
        if not self.services.settings.allow_source_edits:
            raise ValueError("Source edits are disabled (EVO_ALLOW_SOURCE_EDITS=false).")
        if len(args) < 2 or not args[1].isdigit():
            raise ValueError("Usage: edit PATH LINE [REPLACEMENT] (prompts for the replacement when omitted)")
        repo = self.repo()
        path, line = args[0], int(args[1])
        current = read_indexed_source(self.services.store, repo["id"], Path(repo["path"]), path)
        if line < 1 or line > len(current.lines):
            raise ValueError(f"Line {line} is outside the file ({len(current.lines)} lines).")
        self.console.write(f"Current: {current.lines[line - 1]}")
        if len(args) > 2:
            replacement: str | None = " ".join(args[2:])
        elif self.scripted:
            replacement = next(iter(self._script or ()), None)
            if replacement is not None:
                replacement = replacement.rstrip("\n")
                self.console.write(self.console.paint("replacement › ", Style.BOLD, Style.BRIGHT_CYAN) + replacement)
        else:
            replacement = self.console.prompt("replacement › ")
        if replacement is None:
            raise ValueError("Edit cancelled: no replacement line.")
        result = edit_indexed_source(self.services.store, repo["id"], Path(repo["path"]), path, line, replacement)
        self.console.success(f"Working copy updated: {result.sha256_before[:10]}… → {result.sha256_after[:10]}…")
        self.console.write(self.console.paint("The original repository was not changed.", Style.DIM))
        self._verify()

    async def _fix(self, args: list[str]) -> None:
        goal = " ".join(args).strip()
        applied, _ = await run_fix(self.services, self.console, self.repo(), goal,
                                   assume_yes=self.scripted, reanalyze=True)
        if applied:
            self.last_findings = []

    async def _reanalyze(self, args: list[str]) -> None:
        task = " ".join(args).strip() or None
        self.console.heading("Re-analyzing current working copy", "Brain → lead agents → specialist sub-agents")
        repo = await rerun_analysis(self.services, self.repo()["id"], self.console, task=task)
        self.last_findings = []
        print_repository_summary(self.console, self.services, repo)
        print_history(self.console, repo)
