"""The solve harness: test runner, Solver Agent, gate, and the CLI a model drives."""

from __future__ import annotations

import io
import json
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

from agents.solver import analyze_issue, issue_terms, weighted_terms
from config import Settings
from evo_cli.main import EXIT_GATE_FAILED, EXIT_OK, EXIT_TESTS_FAILED, main
from llm.provider import DeepSeekProvider, MockProvider, OpenAIProvider, create_provider
from orchestrator.solving import SolveError
from repo import strix_adapter as strix
from repo.test_runner import detect_runner, detect_runners, parse_output, run_tests
from services import build_services

Invoke = Callable[..., tuple[int, str, str]]

BUGGY = '''\
"""Text helpers."""


def split_lines(text):
    """Split text into lines (LF, CRLF or CR)."""
    if not text:
        return []
    lines = text.replace("\\r\\n", "\\n").split("\\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def join_lines(lines):
    return "\\n".join(lines)
'''
TESTS = '''\
from textlib import join_lines, split_lines


def test_split_lines_handles_all_endings():
    assert split_lines("a\\r\\nb\\rc\\n") == ["a", "b", "c"]
    assert split_lines("") == []


def test_join_lines():
    assert join_lines(["a", "b"]) == "a\\nb"
'''
ISSUE = """split_lines() mishandles bare carriage returns

A file whose lines end with a bare `\\r` collapses into a single line, so every
line number after it is wrong.

Expected: `split_lines` normalizes `\\r` to a newline and returns three lines.
Actual: only CRLF is normalized, so `split_lines("a\\rb\\rc")` returns one item.
"""
FIX = '    lines = text.replace("\\r\\n", "\\n").replace("\\r", "\\n").split("\\n")'


def build_patch(target: Path) -> str:
    """A real unified diff of the fix, generated from the file on disk."""
    before = (target / "textlib.py").read_text().splitlines()
    after = [FIX if line.strip().startswith("lines = text.replace") else line for line in before]
    index = next(number for number, (a, b) in enumerate(zip(before, after)) if a != b)
    start = max(0, index - 3)
    end = min(len(before), index + 4)
    body = [f" {line}" for line in before[start:index]]
    body += [f"-{before[index]}", f"+{after[index]}"]
    body += [f" {line}" for line in before[index + 1 : end]]
    span = end - start
    return "\n".join([
        "diff --git a/textlib.py b/textlib.py",
        "--- a/textlib.py",
        "+++ b/textlib.py",
        f"@@ -{start + 1},{span} +{start + 1},{span} @@",
        *body,
    ]) + "\n"


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A tiny repository with a real pytest suite and one failing test."""
    root = tmp_path / "target"
    (root / "tests").mkdir(parents=True)
    (root / "textlib.py").write_text(BUGGY)
    (root / "tests" / "test_textlib.py").write_text(TESTS)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\npythonpath = .\n")
    return root


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Invoke:
    monkeypatch.setenv("EVO_PACING_MS", "0")
    monkeypatch.setenv("EVO_LLM_PROVIDER", "mock")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    data_dir = str(tmp_path / "cli-data")

    def invoke(*args: str) -> tuple[int, str, str]:
        code = main(["--data-dir", data_dir, "--no-color", *args])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


# --------------------------------------------------------------------------- test runner
def test_detects_and_runs_the_repository_suite(target: Path) -> None:
    runners = detect_runners(target)
    assert [runner.key for runner in runners] == ["pytest"]
    report = run_tests(target, runners[0], timeout=120)
    assert report.status == "failed" and report.ok is False
    assert report.passed == 1 and report.failed == 1
    assert report.failing == ("tests/test_textlib.py::test_split_lines_handles_all_endings",)

    (target / "textlib.py").write_text(BUGGY.replace(
        '    lines = text.replace("\\r\\n", "\\n").split("\\n")', FIX))
    fixed = run_tests(target, runners[0], timeout=120)
    assert fixed.status == "passed" and fixed.ok and fixed.passed == 2

    subset = run_tests(target, runners[0], targets=("tests/test_textlib.py::test_join_lines",), timeout=120)
    assert subset.ok and subset.passed == 1


def test_runner_guards(target: Path, tmp_path: Path) -> None:
    runner = detect_runner(target)
    assert runner is not None
    assert run_tests(target, runner, timeout=120, allow_execution=False).status == "skipped"
    (target / "tests" / "test_slow.py").write_text("import time\ndef test_hang(): time.sleep(30)\n")
    timed_out = run_tests(target, runner, targets=("tests/test_slow.py",), timeout=2)
    assert timed_out.status == "timeout" and "killed after" in timed_out.message
    assert detect_runner(tmp_path / "empty") is None
    broken = tmp_path / "broken"
    (broken / "tests").mkdir(parents=True)
    (broken / "pytest.ini").write_text("[pytest]\n")
    (broken / "tests" / "test_x.py").write_text("import missing_dependency\n")
    assert run_tests(broken, detect_runner(broken), timeout=60).status in {"failed", "error"}


def test_parses_other_runners() -> None:
    jest = parse_output("npm", "Tests:       2 failed, 1 skipped, 7 passed, 10 total\n  ✕ renders badly\n")
    assert (jest["passed"], jest["failed"], jest["skipped"]) == (7, 2, 1)
    assert jest["failing"] == ("renders badly",)
    mocha = parse_output("npm", "  12 passing (30ms)\n  2 failing\n")
    assert (mocha["passed"], mocha["failed"]) == (12, 2)
    go = parse_output("go", "--- FAIL: TestSplit (0.00s)\n--- FAIL: TestJoin (0.00s)\nFAIL\n")
    assert go["failing"] == ("TestSplit", "TestJoin") and go["failed"] == 2
    cargo = parse_output("cargo", "test result: FAILED. 3 passed; 1 failed; 2 ignored\nfailures:\n    my::case\n")
    assert (cargo["passed"], cargo["failed"], cargo["skipped"]) == (3, 1, 2)
    assert parse_output("unittest", "Ran 5 tests in 0.1s\n\nFAILED (failures=1, skipped=1)\nFAIL: test_a\n") == {
        "passed": 3, "failed": 1, "skipped": 1, "errors": 0, "failing": ("test_a",)}


# --------------------------------------------------------------------------- issue analysis
def test_issue_analysis_extracts_signals_and_ranks_the_subject() -> None:
    analysis = analyze_issue(ISSUE)
    assert analysis["kinds"] == ["incorrect-behavior"]
    assert analysis["expected"] and "normalizes" in analysis["expected"][0]
    assert analysis["actual"] and "CRLF" in analysis["actual"][0]
    assert any("existing tests still pass" in item for item in analysis["acceptance_criteria"])
    weights = weighted_terms(ISSUE)
    assert weights["split_lines"] > weights.get("Expected", 0)
    assert issue_terms(ISSUE)[0] == "split_lines", "the subject of the issue must rank first"

    crash = analyze_issue('Crash on empty input\n\nTraceback (most recent call last):\n'
                          '  File "app/parse.py", line 42, in parse\n    raise ValueError("empty")\n'
                          'ValueError: empty payload\n')
    assert crash["kinds"] == ["crash"]
    assert crash["exceptions"][0]["type"] == "ValueError"
    assert crash["frames"] == [{"file": "app/parse.py", "line": 42}]
    assert any("ValueError" in item for item in crash["acceptance_criteria"])


# --------------------------------------------------------------------------- the harness loop
def test_solve_locates_the_bug_and_captures_a_failing_baseline(settings: Settings, target: Path) -> None:
    services = build_services(settings)
    session = __import__("asyncio").run(services.solve.solve(target, ISSUE))
    assert session.mode == "live" and session.runner == "pytest"
    assert session.suspects[0]["file"] == "textlib.py" and session.suspects[0]["symbol"] == "split_lines"
    assert session.baseline["status"] == "failed"
    assert session.baseline["failing"] == ["tests/test_textlib.py::test_split_lines_handles_all_endings"]
    assert session.plan["strategy"] == "failing-test-first"
    assert "tests/test_textlib.py" in session.tests
    assert (settings.data_dir / "sessions" / f"{session.id}.json").is_file()
    # The repository is untouched by planning.
    assert '.replace("\\r", "\\n")' not in (target / "textlib.py").read_text()

    reloaded = services.solve.sessions.resolve(session.id[:6])
    assert reloaded.id == session.id and reloaded.issue == ISSUE.strip()


def test_gate_rejects_then_accepts_the_fix(settings: Settings, target: Path) -> None:
    services = build_services(settings)
    session = __import__("asyncio").run(services.solve.solve(target, ISSUE))

    nothing_done = services.solve.check(session)
    assert not nothing_done.passed and nothing_done.status == "fail"
    assert any("No changes" in reason for reason in nothing_done.reasons)

    services.solve.write_file(session, "textlib.py", BUGGY.replace("if not text:", "if not text:  # noqa"))
    still_broken = services.solve.check(session)
    assert not still_broken.passed
    assert {check["name"] for check in still_broken.checks} == {"changes", "tests", "baseline"}
    assert next(c for c in still_broken.checks if c["name"] == "changes")["ok"] is True
    assert next(c for c in still_broken.checks if c["name"] == "tests")["ok"] is False

    services.solve.write_file(session, "textlib.py", BUGGY.replace(
        '    lines = text.replace("\\r\\n", "\\n").split("\\n")', FIX))
    solved = services.solve.check(session, require_new_test=False)
    assert solved.passed and solved.status == "pass"
    assert all(check["ok"] for check in solved.checks)
    assert "textlib.py" in solved.changed_files
    assert services.solve.check(session, require_new_test=True).passed is False, "no test was added"


def test_apply_patch_requires_a_real_change(settings: Settings, target: Path) -> None:
    services = build_services(settings)
    session = __import__("asyncio").run(services.solve.solve(target, ISSUE))
    patch = build_patch(target)
    entry = services.solve.apply_patch(session, patch)
    assert entry["files"] == ["textlib.py"]
    assert services.solve.check(session).passed

    with pytest.raises(SolveError, match="does not apply"):
        services.solve.apply_patch(session, patch)  # already applied: context no longer matches
    with pytest.raises(SolveError, match="empty"):
        services.solve.apply_patch(session, "   ")
    with pytest.raises(SolveError, match="diff --git"):
        services.solve.apply_patch(session, "just some text")


def test_solve_cli_end_to_end(cli: Invoke, target: Path, tmp_path: Path) -> None:
    issue_file = tmp_path / "issue.md"
    issue_file.write_text(ISSUE)
    code, out, err = cli("--json", "solve", "--repo", str(target), "--issue-file", str(issue_file))
    assert code == EXIT_OK, err
    payload = json.loads(out)
    session = payload["session"]
    assert payload["suspects"][0]["symbol"] == "split_lines"
    assert payload["baseline"]["status"] == "failed"
    assert set(payload["commands"]) == {"test", "apply", "write", "check", "diff"}

    code, out, _ = cli("--json", "test", "--session", session, "--covering")
    assert code == EXIT_TESTS_FAILED and json.loads(out)["failed"] == 1

    code, out, err = cli("--json", "check", "--session", session)
    assert code == EXIT_GATE_FAILED and json.loads(out)["status"] == "fail"

    fixed = tmp_path / "fixed.py"
    fixed.write_text(BUGGY.replace('    lines = text.replace("\\r\\n", "\\n").split("\\n")', FIX))
    code, out, err = cli("--json", "write", "--session", session, "textlib.py", "--file", str(fixed))
    assert code == EXIT_OK, err

    code, out, _ = cli("--json", "test", "--session", session)
    assert code == EXIT_OK and json.loads(out)["passed"] == 2

    code, out, _ = cli("--json", "check", "--session", session, "--save-patch")
    gate = json.loads(out)
    assert code == EXIT_OK and gate["status"] == "pass" and gate["changed_files"] == ["textlib.py"]
    assert Path(gate["patch_path"]).is_file()

    code, out, _ = cli("--json", "sessions")
    assert code == EXIT_OK and json.loads(out)[0]["session"] == session
    code, out, _ = cli("check", "--session", session)  # human-readable
    assert "PASS" in out and "previously failing test" in out

    code, out, _ = cli("--json", "solve", "--repo", str(target))
    assert code == 1 and "Describe the issue" in json.loads(out)["error"]


def test_solve_cli_rejects_bad_input(cli: Invoke, tmp_path: Path) -> None:
    code, out, _ = cli("--json", "solve", "--repo", str(tmp_path / "nope"), "--issue", "anything")
    assert code == 1 and "does not exist" in json.loads(out)["error"]
    code, out, _ = cli("--json", "check", "--session", "zzzzzz")
    assert code == 1 and "not found" in json.loads(out)["error"]
    code, out, _ = cli("--json", "test", "--repo", str(tmp_path))
    assert code == 1 and "No test runner" in json.loads(out)["error"]


def test_test_execution_can_be_disabled(settings: Settings, target: Path) -> None:
    services = build_services(settings.with_overrides(allow_test_execution=False))
    session = __import__("asyncio").run(services.solve.solve(target, ISSUE))
    assert session.baseline["status"] == "skipped"
    gate = services.solve.check(session)
    assert gate.status == "unknown" and not gate.passed


# --------------------------------------------------------------------------- providers and Strix
def test_provider_selection_prefers_deepseek() -> None:
    base = Settings(data_dir=Path("/tmp"), demo_repo_path=Path("/tmp"))
    assert isinstance(create_provider(base), MockProvider)
    deepseek = create_provider(base.with_overrides(deepseek_api_key="k"))
    assert isinstance(deepseek, DeepSeekProvider) and deepseek.available
    assert deepseek.describe()["provider"] == "deepseek"
    assert deepseek.token_parameter == "max_tokens" and deepseek.no_json_mode == ("reasoner",)
    both = create_provider(base.with_overrides(deepseek_api_key="k", openai_api_key="o"))
    assert isinstance(both, DeepSeekProvider), "DeepSeek wins when both keys are set"
    forced = create_provider(base.with_overrides(deepseek_api_key="k", openai_api_key="o", llm_provider="openai"))
    assert isinstance(forced, OpenAIProvider) and not isinstance(forced, DeepSeekProvider)
    assert isinstance(create_provider(base.with_overrides(deepseek_api_key="k", llm_provider="mock")), MockProvider)
    assert isinstance(create_provider(base.with_overrides(llm_provider="deepseek")), MockProvider)


def test_strix_adapter_degrades_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strix.shutil, "which", lambda name: None)
    state = strix.availability({})
    assert not state.available and "strix CLI is not installed" in state.reason
    assert strix.run_scan(Path("/tmp")).status == "unavailable"

    command = strix.build_command("/usr/bin/strix", Path("/repo"), mode="quick", instruction="focus on auth",
                                  max_budget=5)
    assert command[:5] == ["/usr/bin/strix", "--non-interactive", "--target", "/repo", "--scan-mode"]
    assert "--max-budget" in command and "focus on auth" in command
    with pytest.raises(ValueError, match="Unknown Strix scan mode"):
        strix.build_command("strix", Path("/repo"), mode="nonsense")


def test_strix_findings_are_mapped(tmp_path: Path) -> None:
    run = tmp_path / "strix_runs" / "run-1"
    run.mkdir(parents=True)
    (run / "findings.json").write_text(json.dumps({"findings": [
        {"title": "SQL injection in login", "severity": "Critical", "file": "api/auth.js", "line": 42,
         "description": "User input is concatenated", "remediation": "Parameterize", "cwe": "CWE-89",
         "proof_of_concept": "' OR 1=1"},
        {"name": "Reflected XSS", "risk": "moderate", "location": "web/app.js:10"},
        {"description": "no title, must be dropped"},
    ]}))
    findings = strix.parse_run(run)
    assert len(findings) == 2
    assert findings[0]["severity"] == "critical" and findings[0]["file"] == "api/auth.js"
    assert findings[0]["validated"] is True and findings[0]["source"] == "strix"
    assert findings[1]["title"] == "Reflected XSS" and findings[1]["severity"] == "medium"
    assert strix.latest_run(tmp_path) == run


def test_strix_scan_cli_reports_availability(cli: Invoke, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strix.shutil, "which", lambda name: None)
    code, out, _ = cli("--json", "scan", "--check")
    payload = json.loads(out)
    assert code == 1 and payload["available"] is False and payload["missing"]
    code, out, _ = cli("scan", "--check")
    assert code == 1 and "Strix is not available" in out
