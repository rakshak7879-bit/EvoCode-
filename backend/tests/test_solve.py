"""The solve harness: test runner, Solver Agent, gate, and the CLI a model drives."""

from __future__ import annotations

import io
import json
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

from agents.solver import (
    Suspect,
    analyze_issue,
    failure_signals,
    issue_terms,
    rerank_by_failure,
    weighted_terms,
)
from config import Settings
from evo_cli.main import EXIT_GATE_FAILED, EXIT_OK, EXIT_TESTS_FAILED, main
from llm.provider import DeepSeekProvider, MockProvider, OpenAIProvider, create_provider
from orchestrator.solving import SolveCoordinator, SolveError
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
    assert {check["name"] for check in still_broken.checks} == {"changes", "tests", "baseline", "tests-intact"}
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
    with pytest.raises(SolveError, match="file headers"):
        services.solve.apply_patch(session, "just some text")


def test_gate_sees_direct_edits_and_rejects_a_rewritten_test(settings: Settings, target: Path) -> None:
    """A model edits files with its own tools, so the gate cannot rely on `apply`/`write`.

    It also must not accept "make the assertion match the bug": if the failing test
    itself was rewritten, a passing suite proves nothing.
    """
    services = build_services(settings)
    session = __import__("asyncio").run(services.solve.solve(target, ISSUE))
    assert session.baseline and session.baseline["status"] == "failed"

    # Nothing has changed yet, and the gate should say so.
    assert services.solve.changed_files(session) == []

    # The model edits the implementation directly — no harness command involved.
    source = target / "textlib.py"
    source.write_text(source.read_text().replace(
        '    lines = text.replace("\\r\\n", "\\n").split("\\n")', FIX))
    assert services.solve.changed_files(session) == ["textlib.py"], "a direct edit must be visible"

    gate = services.solve.check(session)
    assert gate.passed and [c["name"] for c in gate.checks] == ["changes", "tests", "baseline", "tests-intact"]

    # Now the cheat: revert the fix and weaken the test instead.
    source.write_text(BUGGY)
    suite = target / "tests" / "test_textlib.py"
    suite.write_text(suite.read_text().replace(
        'assert split_lines("a\\r\\nb\\rc\\n") == ["a", "b", "c"]',
        'assert split_lines("a\\r\\nb\\rc\\n") == ["a", "b\\rc"]'))

    cheated = services.solve.check(session)
    assert cheated.passed is False, "rewriting the failing test must not pass the gate"
    intact = next(check for check in cheated.checks if check["name"] == "tests-intact")
    assert intact["ok"] is False and "test_textlib.py" in intact["detail"]
    assert any("proves nothing" in reason for reason in cheated.reasons)

    # An explicit override exists for the case where the test really was wrong.
    assert services.solve.check(session, allow_test_edits=True).passed


def test_failure_signals_blame_the_asserted_call() -> None:
    """pytest reports which call produced the wrong value; that must outweigh a name match."""
    output = textwrap.dedent("""\
        _____________________ test_discount_rounds_half_up _____________________

            def test_discount_rounds_half_up():
                five = Discount(code="FIVE", percent=5)
        >       assert discount_amount(1050, five) == 53
        E       AssertionError: assert 52 == 53
        E        +  where 52 = discount_amount(1050, Discount(code='FIVE', percent=5))
        tests/test_discounts.py:28: AssertionError
    """)
    weights = failure_signals(output)
    assert weights["discount_amount"] > weights.get("Discount", 0.0)
    assert "assert" not in weights, "keywords are noise, not suspects"

    # Ranking: the blamed function starts far behind on name matching alone and still wins.
    suspects = [
        Suspect(file="d.py", line_start=10, line_end=23, symbol="Discount", score=6.7, reasons=["name match"]),
        Suspect(file="d.py", line_start=36, line_end=46, symbol="discount_amount", score=2.6),
        Suspect(file="m.py", line_start=8, line_end=11, symbol="to_cents", score=2.5),
    ]
    ranked = rerank_by_failure(suspects, output)
    assert [s.symbol for s in ranked[:2]] == ["discount_amount", "Discount"]
    assert "the failing test blames this call" in ranked[0].reasons
    assert suspects[0].score == 6.7, "the original ranking is left untouched"
    # Unrelated symbols keep their score, and no output means no change.
    assert ranked[-1].symbol == "to_cents" and ranked[-1].score == 2.5
    assert [s.symbol for s in rerank_by_failure(suspects, "")] == ["Discount", "discount_amount", "to_cents"]


def test_patch_targets_accepts_the_shapes_models_emit() -> None:
    """A model rarely writes a `git diff` header; plain unified diffs must work."""
    targets = SolveCoordinator._patch_targets

    git_style = "\n".join(["diff --git a/src/app.py b/src/app.py",
                           "--- a/src/app.py", "+++ b/src/app.py", "@@ -1 +1 @@", "-a", "+b"])
    assert targets(git_style) == (["src/app.py"], 1)

    # `difflib.unified_diff` output: a/ and b/ prefixes, no git header.
    assert targets("--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b") == (["src/app.py"], 1)

    # No prefixes at all: the path must survive intact, so no component is stripped.
    assert targets("--- src/app.py\n+++ src/app.py\n@@ -1 +1 @@\n-a\n+b") == (["src/app.py"], 0)

    # `diff -u` adds a tab and a timestamp to each header.
    tabbed = "--- src/app.py\t2026-01-01 10:00:00\n+++ src/app.py\t2026-01-01 10:01:00\n@@ -1 +1 @@\n-a\n+b"
    assert targets(tabbed) == (["src/app.py"], 0)

    # A new file: /dev/null is not a target, the other side is.
    assert targets("--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1 @@\n+x") == (["tests/test_new.py"], 1)
    # A deletion: fall back to the path being removed.
    assert targets("--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x") == (["old.py"], 1)

    assert targets("no headers here") == ([], 0)


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


def test_detects_a_suite_in_a_subdirectory(tmp_path: Path, target: Path) -> None:
    """Most repositories keep tests under backend/, src/ or packages/*, not at the root."""
    root = tmp_path / "monorepo"
    (root / "frontend").mkdir(parents=True)
    (root / "frontend" / "index.js").write_text("export const x = 1;\n")
    (root / "backend").mkdir()
    for name in ("textlib.py", "pytest.ini"):
        (root / "backend" / name).write_text((target / name).read_text())
    (root / "backend" / "tests").mkdir()
    (root / "backend" / "tests" / "test_textlib.py").write_text((target / "tests" / "test_textlib.py").read_text())

    runners = detect_runners(root)
    assert runners and runners[0].key == "pytest"
    assert runners[0].directory == "backend" and "backend/pytest.ini" in runners[0].evidence
    assert not any(runner.directory.startswith("backend/") for runner in runners), "nested suites are not duplicated"

    # Repository-relative targets are rewritten for the runner's directory; outsiders are dropped.
    assert runners[0].select_targets(("backend/tests/test_textlib.py", "frontend/a.test.js")) == \
        ("tests/test_textlib.py",)
    report = run_tests(root, runners[0], targets=("backend/tests/test_textlib.py",), timeout=120)
    assert report.status == "failed" and report.directory == "backend"
    assert report.failing == ("tests/test_textlib.py::test_split_lines_handles_all_endings",)
    assert "backend" in report.to_dict()["directory"]


def test_solve_uses_the_subdirectory_suite(settings: Settings, tmp_path: Path, target: Path) -> None:
    root = tmp_path / "project"
    (root / "backend").mkdir(parents=True)
    for name in ("textlib.py", "pytest.ini"):
        (root / "backend" / name).write_text((target / name).read_text())
    (root / "backend" / "tests").mkdir()
    (root / "backend" / "tests" / "test_textlib.py").write_text((target / "tests" / "test_textlib.py").read_text())

    services = build_services(settings)
    session = __import__("asyncio").run(services.solve.solve(root, ISSUE))
    assert session.runner == "pytest" and session.runner_directory == "backend"
    assert session.baseline["status"] == "failed", "the baseline must be captured from the subdirectory suite"
    assert session.suspects[0]["file"] == "backend/textlib.py"

    services.solve.write_file(session, "backend/textlib.py", BUGGY.replace(
        '    lines = text.replace("\\r\\n", "\\n").split("\\n")', FIX))
    gate = services.solve.check(session)
    assert gate.passed and gate.report.directory == "backend"


def test_gate_requires_a_test_when_nothing_failed_at_the_start(settings: Settings, target: Path) -> None:
    """A passing suite proves nothing on its own: the change must bring coverage."""
    (target / "textlib.py").write_text(BUGGY.replace(
        '    lines = text.replace("\\r\\n", "\\n").split("\\n")', FIX))
    services = build_services(settings)
    session = __import__("asyncio").run(services.solve.solve(target, "make join_lines accept a generator"))
    assert session.baseline["status"] == "passed"

    services.solve.write_file(session, "textlib.py", (target / "textlib.py").read_text().replace(
        'return "\\n".join(lines)', 'return "\\n".join(list(lines))'))
    without_test = services.solve.check(session)
    assert not without_test.passed
    assert any("add a test that fails without your fix" in reason for reason in without_test.reasons)

    services.solve.write_file(session, "tests/test_join.py",
                              "from textlib import join_lines\n\n\n"
                              "def test_join_accepts_a_generator():\n"
                              "    assert join_lines(str(n) for n in range(3)) == '0\\n1\\n2'\n")
    with_test = services.solve.check(session)
    assert with_test.passed and any("coverage added in" in check["detail"] for check in with_test.checks)


def test_report_explains_the_issue_and_the_proof(cli: Invoke, target: Path, tmp_path: Path) -> None:
    """`report` answers: what was the issue, how was it solved, and what proves it."""
    issue_file = tmp_path / "issue.md"
    issue_file.write_text(ISSUE)
    code, out, err = cli("--json", "solve", "--repo", str(target), "--issue-file", str(issue_file))
    assert code == EXIT_OK, err
    session = json.loads(out)["session"]

    code, out, _ = cli("report", "--session", session)
    assert code == EXIT_GATE_FAILED, "nothing has been fixed yet"
    assert "not judged yet" in out and "reproduced the failure" in out

    fixed = tmp_path / "fixed.py"
    fixed.write_text(BUGGY.replace('    lines = text.replace("\\r\\n", "\\n").split("\\n")', FIX))
    assert cli("--json", "write", "--session", session, "textlib.py", "--file", str(fixed))[0] == EXIT_OK
    assert cli("--json", "check", "--session", session)[0] == EXIT_OK

    code, out, _ = cli("report", "--session", session)
    assert code == EXIT_OK
    assert "split_lines" in out and "PASS" in out
    assert "previously failing test(s) now pass" in out
    assert "textlib.py" in out

    code, out, _ = cli("--json", "report", "--session", session)
    payload = json.loads(out)
    assert payload["solved"] is True and payload["gate"]["status"] == "pass"
    assert payload["located"][0]["symbol"] == "split_lines"
    assert payload["baseline"]["status"] == "failed"
    assert payload["edits"] and payload["edits"][0]["files"] == ["textlib.py"]
    assert "-" in payload["diff"] and "+" in payload["diff"]
    assert any("existing tests still pass" in item for item in payload["acceptance_criteria"])
