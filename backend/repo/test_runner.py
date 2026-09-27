"""Run a repository's own test suite, sandboxed, to prove a fix works.

This is the only place where Evo Code executes code from a repository, and it
does so under tight limits:

- the command comes from Evo Code's own detection table (``DETECTORS``), never
  from repository content, and is executed as an argument list without a shell
- it runs in a child process group with a wall-clock timeout, killed as a group
- the environment is rebuilt from scratch: no inherited secrets, proxies
  disabled, network-using package managers never invoked
- installs, builds, watch modes and coverage uploads are never run
- stdout/stderr are captured with a byte cap

Detection is evidence-based: a runner is only offered when its config file and
its executable are both present. ``EVO_ALLOW_TEST_EXECUTION=false`` disables
execution entirely (detection still works).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo.filters import IGNORED_DIRS

logger = logging.getLogger("evo.tests")

MAX_OUTPUT_BYTES = 256 * 1024
KEEP_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "SHELL", "USER", "TERM")
SAFE_ENV = {
    "CI": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "NO_COLOR": "1",
    "FORCE_COLOR": "0",
    # Keep test runs offline and non-interactive.
    "no_proxy": "*",
    "NO_PROXY": "*",
    "npm_config_offline": "true",
    "npm_config_audit": "false",
    "npm_config_fund": "false",
    "PIP_NO_INPUT": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


@dataclass(frozen=True)
class TestRunner:
    """A detected way to run a repository's tests.

    ``directory`` is where the suite actually lives, relative to the repository
    root (``""`` for the root itself). Most real repositories keep their tests
    one level down — ``backend/``, ``src/``, ``packages/api/`` — so the runner
    carries its own working directory instead of assuming the root.
    """

    key: str
    title: str
    command: tuple[str, ...]
    evidence: str
    #: How to restrict the run to selected tests (``{targets}`` is replaced).
    select: tuple[str, ...] = ()
    directory: str = ""

    def working_directory(self, root: Path) -> Path:
        return root / self.directory if self.directory else root

    def select_targets(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        """Repository-relative test paths rewritten relative to this runner's directory."""
        if not self.directory:
            return paths
        prefix = self.directory.rstrip("/") + "/"
        return tuple(path[len(prefix):] for path in paths if path.startswith(prefix))

    def command_for(self, targets: tuple[str, ...] = ()) -> tuple[str, ...]:
        if not targets or not self.select:
            return self.command
        extra: list[str] = []
        for part in self.select:
            extra.extend(targets if part == "{targets}" else [part])
        return (*self.command, *extra)

    @property
    def display(self) -> str:
        return f"{self.directory}: {' '.join(self.command)}" if self.directory else " ".join(self.command)


@dataclass
class TestReport:
    """Outcome of one test run."""

    runner: str
    command: tuple[str, ...]
    status: str  # passed | failed | error | timeout | skipped
    exit_code: int | None = None
    duration_ms: int = 0
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    errors: int | None = None
    failing: tuple[str, ...] = ()
    output: str = ""
    message: str = ""
    truncated: bool = False
    #: Where the suite ran, relative to the repository root ("" for the root).
    directory: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    @property
    def total(self) -> int | None:
        counted = [n for n in (self.passed, self.failed, self.errors) if n is not None]
        return sum(counted) if counted else None

    def summary(self) -> str:
        if self.status == "passed":
            counts = f"{self.passed} passed" if self.passed is not None else "no failures reported"
            return f"{counts}{f' · {self.skipped} skipped' if self.skipped else ''} ({self.duration_ms}ms)"
        if self.status == "failed":
            failed = self.failed if self.failed is not None else "some"
            first = f" · first: {self.failing[0]}" if self.failing else ""
            return f"{failed} failing{f' of {self.total}' if self.total else ''}{first} ({self.duration_ms}ms)"
        return self.message or self.status

    def to_dict(self, *, include_output: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runner": self.runner,
            "directory": self.directory,
            "command": list(self.command),
            "status": self.status,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
            "failing": list(self.failing),
            "summary": self.summary(),
            "message": self.message,
        }
        if include_output:
            payload["output"] = self.output
            payload["output_truncated"] = self.truncated
        return payload


# --------------------------------------------------------------------------- detection
@dataclass(frozen=True)
class Detector:
    key: str
    title: str
    #: Repository files that indicate this runner (any of them).
    markers: tuple[str, ...]
    executable: str
    arguments: tuple[str, ...]
    select: tuple[str, ...] = ()
    #: Extra check on the repository (for example a "test" script in package.json).
    requires_script: str | None = None


DETECTORS: tuple[Detector, ...] = (
    # No -q: a repository's own addopts may already pass it, and -qq hides the counts line.
    Detector("pytest", "pytest", ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "conftest.py",
                                  "tests/__init__.py", "tests/conftest.py"),
             "pytest", ("-p", "no:cacheprovider", "-rf"), ("{targets}",)),
    Detector("unittest", "python -m unittest", ("tests/__init__.py",), sys.executable,
             ("-m", "unittest", "discover", "-q"), ()),
    Detector("npm", "npm test", ("package.json",), "npm", ("test", "--silent", "--", "--watch=false"),
             ("{targets}",), requires_script="test"),
    Detector("go", "go test", ("go.mod",), "go", ("test", "./..."), ("-run", "{targets}")),
    Detector("cargo", "cargo test", ("Cargo.toml",), "cargo", ("test", "--quiet"), ("{targets}",)),
)
_PYTEST_MARKERS = ("pytest", "[tool.pytest", "pytest.ini")


def _has_test_script(root: Path) -> bool:
    try:
        import json

        package = json.loads((root / "package.json").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    script = (package.get("scripts") or {}).get("test") or ""
    return bool(script) and "no test specified" not in script


def _python_executable(directory: Path, root: Path, name: str) -> str | None:
    """Prefer a virtualenv beside the suite, then one at the repository root, then this interpreter."""
    candidates = [directory / ".venv" / "bin" / name, directory / "venv" / "bin" / name]
    if directory != root:
        candidates += [root / ".venv" / "bin" / name, root / "venv" / "bin" / name]
    candidates.append(Path(sys.prefix) / "bin" / name)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


#: Directories that commonly hold a sub-project's test suite, tried before other siblings.
PREFERRED_DIRS = ("backend", "server", "api", "src", "app", "lib", "core", "packages", "services", "python", "web")
MAX_SEARCH_DIRECTORIES = 60


def _searchable(directory: Path) -> bool:
    name = directory.name
    return directory.is_dir() and not name.startswith(".") and name not in IGNORED_DIRS


def _candidate_directories(root: Path) -> list[Path]:
    """The root, then its subdirectories up to two levels down (monorepo layouts)."""
    candidates = [root]
    try:
        level_one = sorted((path for path in root.iterdir() if _searchable(path)), key=_directory_rank)
    except OSError:
        return candidates
    candidates.extend(level_one)
    for directory in level_one:
        if len(candidates) >= MAX_SEARCH_DIRECTORIES:
            break
        try:
            candidates.extend(sorted((path for path in directory.iterdir() if _searchable(path)),
                                     key=_directory_rank))
        except OSError:
            continue
    return candidates[:MAX_SEARCH_DIRECTORIES]


def _directory_rank(directory: Path) -> tuple[int, str]:
    name = directory.name.lower()
    return (PREFERRED_DIRS.index(name) if name in PREFERRED_DIRS else len(PREFERRED_DIRS), name)


def _detect_in(directory: Path, root: Path) -> list[TestRunner]:
    relative = "" if directory == root else directory.relative_to(root).as_posix()
    found: list[TestRunner] = []
    for detector in DETECTORS:
        markers = [marker for marker in detector.markers if (directory / marker).exists()]
        if not markers:
            continue
        if detector.key == "pytest":
            text = ""
            for marker in markers:
                try:
                    text += (directory / marker).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
            has_tests = (directory / "tests").is_dir() or any(directory.glob("test_*.py"))
            if not (any(hint in text for hint in _PYTEST_MARKERS) or has_tests):
                continue
        if detector.key == "unittest" and not (directory / "tests").is_dir():
            continue
        if detector.requires_script == "test" and not _has_test_script(directory):
            continue
        if detector.key in {"pytest", "unittest"}:
            executable = _python_executable(directory, root, detector.executable) \
                if detector.key == "pytest" else detector.executable
        else:
            executable = shutil.which(detector.executable)
        if not executable:
            continue
        evidence = f"found {relative + '/' if relative else ''}{markers[0]}"
        found.append(TestRunner(detector.key, detector.title, (executable, *detector.arguments), evidence,
                                detector.select, relative))
    return found


def detect_runners(root: Path) -> list[TestRunner]:
    """Every runner this repository supports, best first, including sub-project suites.

    The repository root is tried first; if it has no suite of its own, subdirectories
    up to two levels down are searched, with conventional names (``backend``, ``src``,
    ``packages`` …) first, so a monorepo's tests are still found and still run in the
    right working directory.
    """
    found: list[TestRunner] = []
    seen: set[tuple[str, str]] = set()
    claimed: list[str] = []
    for directory in _candidate_directories(root):
        relative = "" if directory == root else directory.relative_to(root).as_posix()
        if any(relative.startswith(parent + "/") for parent in claimed):
            continue  # a parent directory already owns this suite
        runners = _detect_in(directory, root)
        for runner in runners:
            key = (runner.key, runner.directory)
            if key not in seen:
                seen.add(key)
                found.append(runner)
        if runners:
            if directory == root:
                break  # a suite at the root wins outright
            claimed.append(relative)
    return found


def detect_runner(root: Path, key: str | None = None) -> TestRunner | None:
    runners = detect_runners(root)
    if key:
        return next((runner for runner in runners if runner.key == key), None)
    return runners[0] if runners else None


# --------------------------------------------------------------------------- output parsing
_PYTEST_COUNTS = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
_PYTEST_FAIL = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_JEST_COUNTS = re.compile(r"Tests:\s+(.+)")
_JEST_ITEM = re.compile(r"(\d+)\s+(passed|failed|skipped|todo)")
_MOCHA_PASS = re.compile(r"^\s*(\d+)\s+passing", re.MULTILINE)
_MOCHA_FAIL = re.compile(r"^\s*(\d+)\s+failing", re.MULTILINE)
_NODE_FAIL = re.compile(r"^\s*(?:✕|✗|×|not ok \d+ -)\s*(.+)$", re.MULTILINE)
_GO_FAIL = re.compile(r"^--- FAIL: (\S+)", re.MULTILINE)
_CARGO_COUNTS = re.compile(r"test result:\s+\w+\.\s+(\d+) passed;\s+(\d+) failed;\s+(\d+) ignored")
_CARGO_FAIL = re.compile(r"^\s{4}(\S+)$", re.MULTILINE)
_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)
_UNITTEST_COUNTS = re.compile(r"(failures|errors|skipped)=(\d+)")
_UNITTEST_FAIL = re.compile(r"^(?:FAIL|ERROR):\s+(\S+)", re.MULTILINE)


def parse_output(runner: str, text: str) -> dict[str, Any]:
    """Counts and failing test ids parsed from a runner's output (best effort)."""
    stats: dict[str, Any] = {"passed": None, "failed": None, "skipped": None, "errors": None, "failing": ()}
    if runner == "pytest":
        counts = {kind: int(number) for number, kind in _PYTEST_COUNTS.findall(text)}
        if counts:
            stats.update(passed=counts.get("passed", 0) + counts.get("xpassed", 0), failed=counts.get("failed", 0),
                         skipped=counts.get("skipped", 0) + counts.get("xfailed", 0),
                         errors=counts.get("error", 0) + counts.get("errors", 0))
        stats["failing"] = tuple(dict.fromkeys(_PYTEST_FAIL.findall(text)))[:20]
    elif runner == "unittest":
        ran = _UNITTEST_RAN.search(text)
        counts = {kind: int(number) for kind, number in _UNITTEST_COUNTS.findall(text)}
        if ran:
            total = int(ran.group(1))
            failed, errors, skipped = counts.get("failures", 0), counts.get("errors", 0), counts.get("skipped", 0)
            stats.update(passed=max(0, total - failed - errors - skipped), failed=failed, errors=errors,
                         skipped=skipped)
        stats["failing"] = tuple(dict.fromkeys(_UNITTEST_FAIL.findall(text)))[:20]
    elif runner == "npm":
        jest = _JEST_COUNTS.search(text)
        if jest:
            counts = {kind: int(number) for number, kind in _JEST_ITEM.findall(jest.group(1))}
            stats.update(passed=counts.get("passed", 0), failed=counts.get("failed", 0),
                         skipped=counts.get("skipped", 0) + counts.get("todo", 0))
        else:
            passing, failing = _MOCHA_PASS.search(text), _MOCHA_FAIL.search(text)
            if passing or failing:
                stats.update(passed=int(passing.group(1)) if passing else 0,
                             failed=int(failing.group(1)) if failing else 0)
        stats["failing"] = tuple(dict.fromkeys(name.strip() for name in _NODE_FAIL.findall(text)))[:20]
    elif runner == "go":
        stats["failing"] = tuple(dict.fromkeys(_GO_FAIL.findall(text)))[:20]
        stats["failed"] = len(stats["failing"]) or None
    elif runner == "cargo":
        totals = _CARGO_COUNTS.findall(text)
        if totals:
            stats.update(passed=sum(int(p) for p, _, _ in totals), failed=sum(int(f) for _, f, _ in totals),
                         skipped=sum(int(i) for _, _, i in totals))
        section = text.split("failures:", 1)[-1] if "failures:" in text else ""
        stats["failing"] = tuple(dict.fromkeys(_CARGO_FAIL.findall(section)))[:20]
    return stats


def _environment() -> dict[str, str]:
    env = {key: os.environ[key] for key in KEEP_ENV if key in os.environ}
    env.update(SAFE_ENV)
    return env


def run_tests(
    root: Path,
    runner: TestRunner,
    *,
    targets: tuple[str, ...] = (),
    timeout: float = 300.0,
    allow_execution: bool = True,
) -> TestReport:
    """Execute ``runner`` in its own directory under ``root``. Never raises for test failures.

    ``targets`` are repository-relative paths; they are rewritten relative to the
    runner's directory, and any that fall outside it are dropped.
    """
    command = runner.command_for(runner.select_targets(targets))
    if not allow_execution:
        return TestReport(runner.key, command, "skipped", directory=runner.directory,
                          message="Test execution is disabled (EVO_ALLOW_TEST_EXECUTION=false).")
    working = runner.working_directory(root)
    if not working.is_dir():
        return TestReport(runner.key, command, "error", directory=runner.directory,
                          message=f"Test directory does not exist: {working}")
    started = time.monotonic()
    try:
        process = subprocess.Popen(  # noqa: S603 - command comes from DETECTORS, never from repository content
            command,
            cwd=str(working),
            env=_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own process group, so children die with it
            text=True,
            errors="replace",
        )
    except (OSError, ValueError) as exc:
        return TestReport(runner.key, command, "error", directory=runner.directory,
                          message=f"Could not start the test command: {exc}")
    timed_out = False
    try:
        output = process.communicate(timeout=timeout)[0] or ""
    except subprocess.TimeoutExpired:
        timed_out = True
        output = _terminate(process)
    duration = int((time.monotonic() - started) * 1000)
    truncated = len(output.encode("utf-8", "ignore")) > MAX_OUTPUT_BYTES
    if truncated:
        output = output.encode("utf-8", "ignore")[-MAX_OUTPUT_BYTES:].decode("utf-8", "replace")
    exit_code = process.returncode
    stats = parse_output(runner.key, output)
    if timed_out:
        status, message = "timeout", f"The test command was killed after {timeout:.0f}s."
    elif exit_code == 0:
        status, message = "passed", ""
    elif stats["failed"] or stats["errors"] or stats["failing"]:
        status, message = "failed", "The test suite reported failures."
    else:
        status = "error"
        message = (f"The test command exited with code {exit_code} without reporting test results "
                   "(missing dependencies or a collection error).")
    logger.info("Test run finished", extra={"runner": runner.key, "status": status, "duration_ms": duration})
    return TestReport(runner.key, command, status, exit_code, duration, stats["passed"], stats["failed"],
                      stats["skipped"], stats["errors"], tuple(stats["failing"]), output, message, truncated,
                      runner.directory)


def _terminate(process: subprocess.Popen[str]) -> str:
    """Kill the whole process group and return whatever output was produced."""
    for send, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(os.getpgid(process.pid), send)
        except (ProcessLookupError, PermissionError, OSError):
            break
        try:
            return process.communicate(timeout=wait)[0] or ""
        except subprocess.TimeoutExpired:
            continue
    try:
        process.kill()
        return process.communicate(timeout=5)[0] or ""
    except (subprocess.TimeoutExpired, OSError):
        return ""
