"""Solve sessions: the harness an external model drives to fix a repository.

A model (for example DeepSeek) calls Evo Code between its own reasoning turns:

    ./evo solve --repo . --issue-file bug.md --json   ->  breakdown, suspects, plan, failing baseline
    ...the model edits the repository, or sends a patch to `./evo apply`...
    ./evo test --session S --json                     ->  runs the repository's tests
    ./evo check --session S --json                    ->  the gate: pass or fail, with reasons

A session is stored as JSON under ``<data-dir>/sessions`` so each call is a
separate process, and ``check`` can compare against the baseline captured by
``solve``. In live mode the session points at the real repository, so the
model's own edits are what gets tested; isolated mode analyzes a copy instead.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.base import AgentResult, SubAgentRun
from agents.context import AgentContext
from agents.solver import SolverAgent
from agents.team import DelegationRuntime
from config import Settings
from memory.indexer import MemoryIndexer
from memory.store import MemoryStore, utc_now
from orchestrator.brain import Brain
from orchestrator.fixing import FixEvents, Sink, unified_diff
from repo.paths import UnsafePathError, resolve_in_root
from repo.scanner import RepositoryScanner
from repo.source_service import SourceAccessError
from repo.test_runner import TestReport, TestRunner, detect_runner, detect_runners, run_tests
from repo.text import decode_source, sha256_bytes

#: Generated files that must never count as a fix.
ARTIFACTS = ("__pycache__/", ".pytest_cache/", "node_modules/", ".evo-data/", "dist/", "build/", ".tox/",
             ".mypy_cache/", ".ruff_cache/", "coverage/", ".coverage", "target/debug/", "target/release/")


def _is_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.endswith((".pyc", ".pyo", ".log", ".orig", ".rej")) or any(
        part in normalized for part in ARTIFACTS)


#: The "this side does not exist" placeholder in a unified diff header.
_NO_FILE = "/dev/null"
#: ``diff -u`` appends a tab and a timestamp to each file header.
_HEADER_TAIL = re.compile(r"\t.*$")


def _header_path(value: str) -> str:
    """Read the path out of a ``---``/``+++`` diff header line."""
    text = _HEADER_TAIL.sub("", value.strip())
    if len(text) > 1 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text


TEST_DIRECTORIES = frozenset({"test", "tests", "spec", "specs", "__tests__", "testing"})
#: Names that only ever belong to tests, whatever directory they sit in.
_UNAMBIGUOUS_TEST = re.compile(r"^.+(?:_test|_spec)\.[a-z]+$|^.+\.(?:test|spec)\.[a-z]+$")
#: ``test_x.py`` is a test in a suite, but also a plausible module name (``test_runner.py``).
_PREFIXED_TEST = re.compile(r"^test_.+\.[a-z]+$")


def _is_test_path(path: str, root: Path | None = None) -> bool:
    """Whether a changed file is a test, so the gate can tell coverage from implementation.

    Directory evidence wins. A ``test_*`` name outside a test directory only counts
    when its neighbours look like a suite too, so a module such as
    ``repo/test_runner.py`` is not mistaken for coverage.
    """
    normalized = path.replace("\\", "/")
    parts = normalized.lower().split("/")
    name, directories = parts[-1], parts[:-1]
    if any(part in TEST_DIRECTORIES for part in directories):
        return True
    if _UNAMBIGUOUS_TEST.match(name):
        return True
    if not _PREFIXED_TEST.match(name):
        return False
    if not directories:
        return True  # test_x.py at the repository root is a suite of its own
    if root is None:
        return True
    try:
        siblings = [entry.name.lower() for entry in (root / "/".join(normalized.split("/")[:-1])).iterdir()
                    if entry.is_file()]
    except OSError:
        return True
    prefixed = sum(1 for sibling in siblings if _PREFIXED_TEST.match(sibling))
    return prefixed >= 2 or prefixed == len([s for s in siblings if s.endswith(name.rsplit(".", 1)[-1])])

logger = logging.getLogger("evo.solve")

SESSION_VERSION = 1
GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_UNKNOWN = "unknown"


class SolveError(RuntimeError):
    """A user-facing problem with a solve session."""


@dataclass
class SolveSession:
    """Everything a later process needs to continue or judge a solve."""

    id: str
    repository_id: str
    root: str
    issue: str
    mode: str = "live"  # live (real repository) | isolated (working copy)
    #: Ad-hoc sessions (``./evo test --repo``) are never written to disk.
    ephemeral: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    runner: str | None = None
    #: Where the suite lives, relative to the repository root ("" for the root).
    runner_directory: str = ""
    tests: list[str] = field(default_factory=list)
    baseline: dict[str, Any] | None = None
    analysis: dict[str, Any] = field(default_factory=dict)
    suspects: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    subagents: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)
    edits: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    gate: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return Path(self.root)

    def to_dict(self) -> dict[str, Any]:
        return {"version": SESSION_VERSION, **{key: value for key, value in self.__dict__.items()}}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SolveSession":
        known = {key for key in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in payload.items() if key in known})


class SessionStore:
    """JSON files under ``<data-dir>/sessions`` (one per solve)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _file(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.json"

    def save(self, session: SolveSession) -> Path | None:
        if session.ephemeral:
            return None
        session.updated_at = utc_now()
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._file(session.id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(target)
        return target

    def load(self, session_id: str) -> SolveSession:
        matches = [session_id] if self._file(session_id).is_file() else sorted(
            path.stem for path in self.directory.glob(f"{session_id}*.json"))
        if not matches:
            raise SolveError(f"Solve session not found: {session_id}. List them with `./evo sessions`.")
        if len(matches) > 1:
            raise SolveError(f"Session prefix {session_id!r} is ambiguous ({len(matches)} matches).")
        try:
            payload = json.loads(self._file(matches[0]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SolveError(f"Could not read session {matches[0]}: {exc}") from exc
        return SolveSession.from_dict(payload)

    def latest(self) -> SolveSession:
        files = sorted(self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not files:
            raise SolveError("No solve sessions yet. Start one with `./evo solve --issue \"...\"`.")
        return self.load(files[0].stem)

    def resolve(self, session_id: str | None) -> SolveSession:
        return self.load(session_id) if session_id else self.latest()

    def list(self, limit: int = 20) -> list[SolveSession]:
        files = sorted(self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        sessions = []
        for path in files[:limit]:
            try:
                sessions.append(self.load(path.stem))
            except SolveError:
                continue
        return sessions


@dataclass
class GateResult:
    """The verdict a harness returns to the model (and to whoever scores the run)."""

    status: str
    reasons: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    report: TestReport | None = None
    changed_files: list[str] = field(default_factory=list)
    patch: str = ""

    @property
    def passed(self) -> bool:
        return self.status == GATE_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "reasons": self.reasons,
            "checks": self.checks,
            "changed_files": self.changed_files,
            "tests": self.report.to_dict(include_output=False) if self.report else None,
        }


class SolveCoordinator:
    """Brain-level orchestration of a solve: analyze, run tests, apply patches, judge."""

    def __init__(self, settings: Settings, store: MemoryStore, brain: Brain, scanner: RepositoryScanner,
                 indexer: MemoryIndexer) -> None:
        self.settings = settings
        self.store = store
        self.brain = brain
        self.scanner = scanner
        self.indexer = indexer
        self.sessions = SessionStore(settings.data_dir / "sessions")

    # ------------------------------------------------------------------ indexing
    def _index(self, root: Path, name: str, issue: str, events: FixEvents) -> str:
        """Index a repository in place (no copy) so the model's own edits are what we analyze."""
        repo_id = uuid.uuid4().hex[:16]
        self.store.create_repository(repo_id=repo_id, name=name, path=str(root), source="workspace",
                                     source_ref=str(root), task=issue.strip()[:500] or "solve an issue")
        events.log(f"Indexing {name} in place: {root}", stage="scanning")
        scan = self.scanner.scan(root)
        if not scan.files:
            raise SolveError(f"No supported source files were found in {root}.")
        stats = scan.to_stats()
        self.store.update_repository(repo_id, stats=stats)
        index_stats, _ = self.indexer.index(repo_id, root, scan)
        stats["memory"] = index_stats.to_dict()
        stats["fts5"] = self.store.db.fts5_enabled
        self.store.update_repository(repo_id, stats=stats, status="completed", stage="indexed",
                                    brain_state="complete", brain_message="Workspace indexed for solving")
        events.log(f"Indexed {len(scan.files)} file(s) · {index_stats.memories} memory passages · "
                   f"{index_stats.symbols} symbols", stage="indexing", level="success")
        return repo_id

    # ------------------------------------------------------------------ solve
    async def solve(self, repo: Path, issue: str, *, isolated: bool = False, sink: Sink | None = None,
                    reuse: str | None = None) -> SolveSession:
        """Break the issue down, locate the code, capture the failing baseline and write the plan."""
        issue = issue.strip()
        if not issue:
            raise SolveError("The issue text is empty. Pass --issue or --issue-file.")
        root = repo.expanduser().resolve()
        if not root.is_dir():
            raise SolveError(f"Repository path does not exist: {root}")
        events = FixEvents(sink, titles={"solver": SolverAgent.title}, stage="solving")
        events.log(f'Solve goal: "{issue.splitlines()[0][:160]}"', stage="understanding")
        if isolated:
            from repo.archive import copy_working_copy

            work = self.settings.repos_dir / f"solve-{uuid.uuid4().hex[:12]}"
            root = copy_working_copy(root, work)
            events.log(f"Copied the repository into an isolated working copy: {root}", stage="queued")
        if reuse:
            existing = self.store.get_repository(reuse)
            if existing is None:
                raise SolveError(f"Repository not found: {reuse}")
            repo_id, root = existing["id"], Path(existing["path"])
            events.log(f"Reusing the existing index of {existing['name']}", stage="indexing")
        else:
            repo_id = self._index(root, root.name, issue, events)

        context, summary = self.brain.context_builder.build(repo_id, root.name, root, issue)
        events.log(f"Context loaded: {summary.files} file(s), {summary.memory_entries} memory entries",
                   stage="retrieving")
        solver = SolverAgent(run_tests_enabled=self.settings.allow_test_execution,
                            test_timeout=self.settings.test_timeout_seconds)
        events.log(f"Route → {solver.title}: the goal describes an issue to solve", stage="routing",
                   agent=solver.name)
        context = context.with_upstream({
            "issue": AgentResult(agent="issue", data={"text": issue, "root": str(root)}),
        }).with_delegation(DelegationRuntime(
            observer=events,
            min_visible_ms=int(self.settings.pacing_ms * 0.5),
            simulate_failures=frozenset(self.settings.simulate_agent_failure),
            timeout_seconds=max(self.settings.agent_timeout_seconds, self.settings.test_timeout_seconds + 30),
        ))
        events.log(f"{solver.title} started", stage="running", depth=1, agent=solver.name)
        result = await solver.run(context)
        events.log(f"{solver.title} completed: {result.summary}", level="success", stage="running", depth=1,
                   agent=solver.name)

        data = result.data
        session = SolveSession(
            id=f"{uuid.uuid4().hex[:12]}",
            repository_id=repo_id,
            root=str(root),
            issue=issue,
            mode="isolated" if isolated else "live",
            runner=data["tests"]["runner"],
            runner_directory=data["tests"].get("directory", ""),
            tests=list(data["tests"]["tests"]),
            baseline=data["baseline"],
            analysis=data["analysis"],
            suspects=data["suspects"],
            plan=data["plan"],
            subagents=[run.model_dump(mode="json") for run in result.subagents],
            notes=result.notes,
            llm=self.brain.llm.describe(),
        )
        self.sessions.save(session)
        events.log(f"Session {session.id} saved · the repository has not been modified", stage="planning",
                   level="success")
        return session

    # ------------------------------------------------------------------ tests
    def runner_for(self, session: SolveSession, key: str | None = None) -> TestRunner:
        """The runner this session used, or the best one available now."""
        runners = detect_runners(session.path)
        wanted = key or session.runner
        if wanted:
            exact = next((runner for runner in runners
                          if runner.key == wanted and runner.directory == session.runner_directory), None)
            if exact:
                return exact
            same_key = next((runner for runner in runners if runner.key == wanted), None)
            if same_key:
                return same_key
            if key:
                available = ", ".join(sorted({runner.key for runner in runners})) or "none"
                raise SolveError(f"No {key} runner in {session.path} (detected: {available}).")
        if runners:
            return runners[0]
        raise SolveError(
            f"No test runner detected in {session.path}. Evo Code looks for pytest, unittest, npm test, "
            "go test and cargo test in the repository root and up to two levels down. Without one it cannot "
            "prove a fix works."
        )

    def run_tests(self, session: SolveSession, *, targets: tuple[str, ...] = (), runner_key: str | None = None,
                  scope: str = "suite") -> TestReport:
        runner = self.runner_for(session, runner_key)
        report = run_tests(session.path, runner, targets=targets, timeout=self.settings.test_timeout_seconds,
                           allow_execution=self.settings.allow_test_execution)
        session.runs.append({"at": utc_now(), "scope": scope, "targets": list(targets),
                             **report.to_dict(include_output=False)})
        self.sessions.save(session)
        return report

    # ------------------------------------------------------------------ patches
    def apply_patch(self, session: SolveSession, patch: str) -> dict[str, Any]:
        """Apply a unified diff with ``git apply``, then prove the files really changed.

        Patch paths are treated as relative to the session's repository. When that
        repository sits inside a larger git work tree (for example ``backend/`` of a
        monorepo), git would otherwise ignore the patch as "outside the directory",
        so the run is anchored at the work-tree root with ``--directory``.
        """
        if not self.settings.allow_source_edits:
            raise SolveError("Source edits are disabled (EVO_ALLOW_SOURCE_EDITS=false).")
        if not patch.strip():
            raise SolveError("The patch is empty.")
        git = shutil.which("git")
        if git is None:
            raise SolveError("git is required to apply a patch. Use `./evo write` for single-file changes.")
        files, strip = self._patch_targets(patch)
        if not files:
            raise SolveError("The patch has no '--- '/'+++ ' file headers, so the target files are "
                             "unknown. Emit a unified diff (`diff -u`) or a `git diff`.")
        before = {path: self._digest(session.path / path) for path in files}
        # Keep the original text so `diff` and `--save-patch` still work when the
        # repository is not under git and cannot be diffed after the fact.
        source = {path: self._read(session.path / path) for path in files}

        options = ["--whitespace=nowarn", f"-p{strip}"]
        cwd = session.path
        toplevel = self._toplevel(session)
        if toplevel is not None and toplevel != session.path:
            try:
                options.append(f"--directory={session.path.relative_to(toplevel).as_posix()}")
                cwd = toplevel
            except ValueError:
                pass
        check = subprocess.run([git, "apply", "--check", *options], cwd=str(cwd), input=patch,  # noqa: S603
                               capture_output=True, text=True, timeout=120)
        if check.returncode != 0:
            detail = (check.stderr or check.stdout).strip().splitlines()
            raise SolveError("The patch does not apply: " + ("; ".join(detail[:4]) or "unknown error")
                             + ". Re-read the file with `./evo source` and rebuild the diff.")
        result = subprocess.run([git, "apply", *options], cwd=str(cwd), input=patch,  # noqa: S603
                                capture_output=True, text=True, timeout=120)
        output = (result.stderr or "") + (result.stdout or "")
        if result.returncode != 0:
            raise SolveError("git apply failed: " + ("; ".join(output.strip().splitlines()[:4]) or "unknown error"))
        changed = [path for path in files if self._digest(session.path / path) != before[path]]
        if not changed:
            hint = " git reported: " + output.strip().splitlines()[0] if output.strip() else ""
            raise SolveError(f"The patch applied cleanly but changed nothing.{hint} "
                             "Check that its paths are relative to the repository root you passed to `solve`.")
        recorded = "".join(unified_diff(path, source.get(path) or "", self._read(session.path / path) or "")
                           for path in changed)
        entry = {"at": utc_now(), "kind": "patch", "files": changed, "bytes": len(patch), "diff": recorded}
        session.edits.append(entry)
        self.sessions.save(session)
        logger.info("Patch applied", extra={"session": session.id, "files": len(changed)})
        return entry

    @staticmethod
    def _patch_targets(patch: str) -> tuple[list[str], int]:
        """Work out which files a diff touches, plus the ``-p`` level git needs.

        Both shapes are accepted: a ``git diff`` (paths prefixed ``a/`` and ``b/``)
        and the plain unified diff that ``diff -u``, ``difflib`` and most language
        models emit, with or without those prefixes. The strip level is derived from
        the headers rather than assumed, so an unprefixed ``+++ src/app.py`` is not
        mistakenly shortened to ``app.py``.
        """
        pairs: list[tuple[str, str]] = []
        old: str | None = None
        for raw in patch.splitlines():
            if raw.startswith("--- "):
                old = _header_path(raw[4:])
            elif raw.startswith("+++ ") and old is not None:
                pairs.append((old, _header_path(raw[4:])))
                old = None
        named = [path for pair in pairs for path in pair if path and path != _NO_FILE]
        prefixed = bool(named) and all(path.startswith(("a/", "b/")) for path in named)
        strip = 1 if prefixed else 0
        files: list[str] = []
        for before, after in pairs:
            # A deletion has no "after" side, so fall back to the original path.
            chosen = after if after and after != _NO_FILE else before
            if not chosen or chosen == _NO_FILE:
                continue
            if strip:
                head, _, tail = chosen.partition("/")
                chosen = tail or head
            if chosen not in files:
                files.append(chosen)
        return sorted(files), strip

    @staticmethod
    def _digest(path: Path) -> str | None:
        try:
            return sha256_bytes(path.read_bytes())
        except OSError:
            return None

    @staticmethod
    def _read(path: Path) -> str | None:
        """Decode a file for diffing, or ``None`` when it cannot be read."""
        try:
            return decode_source(path.read_bytes())
        except OSError:
            return None

    def write_file(self, session: SolveSession, path: str, content: str) -> dict[str, Any]:
        """Replace one file's contents inside the session's repository (creates it if new)."""
        if not self.settings.allow_source_edits:
            raise SolveError("Source edits are disabled (EVO_ALLOW_SOURCE_EDITS=false).")
        root = session.path
        try:
            target = root / Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=True)
            resolved = resolve_in_root(root, path)
        except (UnsafePathError, OSError, SourceAccessError) as exc:
            raise SolveError(f"Refusing to write {path}: {exc}") from exc
        before = decode_source(resolved.read_bytes()) if resolved.stat().st_size else ""
        if not content.endswith("\n"):
            content += "\n"
        resolved.write_text(content, encoding="utf-8")
        entry = {"at": utc_now(), "kind": "write", "files": [path], "bytes": len(content),
                 "created": not before, "diff": unified_diff(path, before, content)}
        session.edits.append(entry)
        self.sessions.save(session)
        return entry

    # ------------------------------------------------------------------ the gate
    def _failing_test_files(self, session: SolveSession) -> list[str]:
        """The files that held the baseline failures, relative to the session root.

        Test ids look like ``tests/test_x.py::test_y`` (pytest) or a bare path, and
        are relative to the directory the runner used, which is not always the
        repository root.
        """
        prefix = (session.runner_directory or "").strip("/")
        files: list[str] = []
        for node in session.baseline.get("failing") or [] if session.baseline else []:
            path = str(node).split("::", 1)[0].strip()
            if not path or "/" not in path and "." not in path:
                continue
            relative = f"{prefix}/{path}" if prefix else path
            if relative not in files:
                files.append(relative)
        return files

    def check(self, session: SolveSession, *, require_new_test: bool = False,
              allow_test_edits: bool = False) -> GateResult:
        """Decide whether the issue is solved: tests pass, the baseline improved, something changed."""
        checks: list[dict[str, Any]] = []
        reasons: list[str] = []

        changed = self.changed_files(session)
        edited = bool(changed or session.edits)
        checks.append({"name": "changes", "ok": edited,
                       "detail": f"{len(changed)} file(s) changed" if changed else
                                 (f"{len(session.edits)} recorded edit(s)" if session.edits else "nothing changed")})
        if not edited:
            reasons.append("No changes were made to the repository yet.")

        report = self.run_tests(session, scope="gate")
        suite_ok = report.status == "passed"
        checks.append({"name": "tests", "ok": suite_ok, "detail": f"{report.runner}: {report.summary()}"})
        if not suite_ok:
            if report.status == "failed":
                failing = ", ".join(report.failing[:3]) or "see the output"
                reasons.append(f"The test suite still fails ({failing}).")
            else:
                reasons.append(f"The test suite could not confirm the fix: {report.summary()}.")

        baseline = session.baseline or {}
        baseline_status = baseline.get("status")
        tests_changed = [path for path in changed if _is_test_path(path, session.path)]
        if baseline_status == "failed":
            fixed = sorted(set(baseline.get("failing") or []) - set(report.failing))
            improved = suite_ok or bool(fixed)
            checks.append({"name": "baseline", "ok": improved,
                           "detail": (f"{len(fixed)} previously failing test(s) now pass: {', '.join(fixed[:3])}"
                                      if fixed else "no previously failing test passes yet")})
            if not improved:
                reasons.append("None of the tests that failed at the start pass now.")

            # A test that was rewritten cannot also be the proof. Without this the gate
            # would accept "make the assertion match the bug".
            rewritten = sorted(set(self._failing_test_files(session)) & set(changed))
            if not allow_test_edits:
                checks.append({"name": "tests-intact", "ok": not rewritten,
                               "detail": ("the failing test(s) were not modified" if not rewritten else
                                          f"the proof itself was edited: {', '.join(rewritten[:3])}")})
                if rewritten:
                    reasons.append(f"{', '.join(rewritten[:3])} held the failing test(s), and it was modified, so "
                                   "a pass proves nothing. Fix the implementation instead, or re-run with "
                                   "--allow-test-edits if the test really was wrong.")
            elif rewritten:
                checks.append({"name": "tests-intact", "ok": True,
                               "detail": f"allowed: {', '.join(rewritten[:3])} was modified"})
        else:
            # Nothing failed at the start, so a passing suite proves nothing on its own:
            # the change has to bring a test that covers it.
            checks.append({
                "name": "baseline",
                "ok": bool(tests_changed),
                "detail": (f"nothing failed at the start; coverage added in {', '.join(tests_changed[:3])}"
                           if tests_changed else
                           f"nothing failed at the start ({baseline_status or 'no baseline'}), and no test "
                           "was added to cover the change"),
            })
            if not tests_changed:
                reasons.append("No test failed at the start, so add a test that fails without your fix and "
                               "passes with it. Otherwise nothing proves the issue is solved.")

        if require_new_test and baseline_status == "failed":
            checks.append({"name": "new-test", "ok": bool(tests_changed),
                           "detail": ", ".join(tests_changed[:3]) or "no test file was added or changed"})
            if not tests_changed:
                reasons.append("No test was added or changed to cover the fix.")

        if report.status == "skipped" or (report.status in {"error", "timeout"} and not reasons):
            # Without a usable test result the gate cannot honestly pass or fail.
            status = GATE_UNKNOWN
            if report.status == "skipped":
                reasons.append("Tests were not run, so the fix cannot be verified "
                               "(EVO_ALLOW_TEST_EXECUTION=false).")
        else:
            status = GATE_PASS if all(check["ok"] for check in checks) else GATE_FAIL
        gate = GateResult(status, reasons, checks, report, changed, self.diff(session))
        session.gate = {"at": utc_now(), **gate.to_dict()}
        self.sessions.save(session)
        return gate

    # ------------------------------------------------------------------ change tracking
    def _git(self, session: SolveSession, *arguments: str) -> str | None:
        """Run git inside the session's repository; ``None`` when git or a work tree is missing."""
        git = shutil.which("git")
        if git is None:
            return None
        environment = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
                       "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
        try:
            process = subprocess.run([git, *arguments], cwd=str(session.path), capture_output=True,  # noqa: S603
                                     text=True, timeout=60, env=environment)
        except (OSError, subprocess.SubprocessError):
            return None
        return process.stdout if process.returncode == 0 else None

    def _toplevel(self, session: SolveSession) -> Path | None:
        output = self._git(session, "rev-parse", "--show-toplevel")
        if not output or not output.strip():
            return None
        try:
            return Path(output.strip()).resolve()
        except OSError:
            return None

    def changed_files(self, session: SolveSession) -> list[str]:
        """Files changed inside the session's repository (git when available, else recorded edits).

        Paths are returned relative to the session root, and generated artifacts
        such as ``__pycache__`` are ignored so the gate judges real source changes.
        """
        status = self._git(session, "status", "--porcelain", "--untracked-files=all")
        if status is None:
            return self._snapshot_changes(session)
        toplevel = self._toplevel(session) or session.path
        files: set[str] = set()
        for line in status.splitlines():
            name = line[3:].strip()
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            name = name.strip('"')
            if not name or _is_artifact(name):
                continue
            absolute = (toplevel / name).resolve()
            try:
                files.add(absolute.relative_to(session.path).as_posix())
            except ValueError:
                continue  # changed outside the repository the model was given
        return sorted(files)

    def _snapshot_changes(self, session: SolveSession) -> list[str]:
        """Files differing from the index that was captured when the session started.

        This is the fallback when the repository is not under git, and it matters:
        a model usually edits files with its own tools rather than through ``apply``
        or ``write``, and the gate has to see those edits too. Every indexed file
        already carries a SHA-256 from solve time, so the comparison is free.
        """
        indexed = {record.path: record.sha256 for record in self.store.list_files(session.repository_id)}
        changed = {path for edit in session.edits for path in edit["files"]}
        try:
            current = self.scanner.scan(session.path).files
        except OSError as exc:  # unreadable tree: fall back to what we recorded ourselves
            logger.warning("Could not re-scan the workspace", extra={"session": session.id, "error": str(exc)})
            return sorted(path for path in changed if not _is_artifact(path))
        seen = set()
        for file in current:
            seen.add(file.path)
            if indexed.get(file.path) != file.sha256:
                changed.add(file.path)  # edited by the model, or newly added
        changed |= set(indexed) - seen  # deleted since the session started
        return sorted(path for path in changed if not _is_artifact(path))

    def diff(self, session: SolveSession) -> str:
        """A unified diff of the work so far (git when available, else the recorded writes)."""
        changed = [path for path in self.changed_files(session)]
        if not changed:
            return ""
        tracked = self._git(session, "diff", "--", *changed)
        if tracked is None:
            return "".join(edit.get("diff", "") for edit in session.edits)
        diff = tracked
        untracked = self._git(session, "ls-files", "--others", "--exclude-standard", "--", *changed) or ""
        for path in (line.strip() for line in untracked.splitlines() if line.strip()):
            if _is_artifact(path):
                continue
            added = self._git(session, "diff", "--no-index", "--", "/dev/null", path)
            if added:
                diff += added
        return diff

    def save_patch(self, session: SolveSession, patch: str | None = None) -> Path | None:
        text = patch if patch is not None else self.diff(session)
        if not text.strip():
            return None
        directory = self.settings.data_dir / "patches"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"solve-{session.id}-{time.strftime('%Y%m%d-%H%M%S')}.patch"
        target.write_text(text, encoding="utf-8")
        return target
