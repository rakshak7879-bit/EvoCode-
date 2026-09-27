"""The ``./evo`` command-line interface, driven in-process through ``evo_cli.main.main``."""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from config import Settings
from evo_cli.console import Console
from evo_cli.main import main
from evo_cli.repository import prepare_repository, run_analysis
from evo_cli.session import parse_location
from services import build_services

Invoke = Callable[..., tuple[int, str, str]]
EVENT_LINE = re.compile(r"^ \+\d\d\.\d\ds\s+\S+\s+L(\d) ", re.MULTILINE)


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


def demo_id(cli: Invoke) -> str:
    code, out, err = cli("--json", "demo", "--no-shell")
    assert code == 0, err
    return str(json.loads(out)["repository_id"])


def test_demo_streams_and_prints_the_multi_level_orchestration(cli: Invoke) -> None:
    code, out, err = cli("demo", "--no-shell")
    assert code == 0, err
    assert "Live orchestration" in out and "Multi-level orchestration" in out
    assert {int(level) for level in EVENT_LINE.findall(out)} == {0, 1, 2}
    assert "L2       ✓ security › Secrets Scanner: 2 candidate(s)" in out
    assert "Duplicate Code Agent delegates to 4 specialist sub-agents in 3 waves" in out
    assert "├─ ✓ Walkthrough Agent  L1  wave 2 ← explainer, security, duplicate" in out
    assert "│  └─ ○ LLM Narrator" in out and "skipped · No LLM configured (local analysis)" in out
    assert "4/4 lead agents · 17/21 specialist sub-agents ran · 4 skipped · 0 failed" in out
    assert "16/16 findings · 56/56 architecture & walkthrough citations verified" in out
    assert "Findings: 16 total · 12 security · 4 duplicate clusters" in out


def test_one_shot_commands(cli: Invoke) -> None:
    repo_id = demo_id(cli)
    short = repo_id[:8]

    code, out, _ = cli("tree", short)
    assert code == 0 and "◉ Evo Brain  L0" in out and "Flow Tracer" in out and "Reuse Advisor" in out

    code, out, _ = cli("agents")  # latest repository by default
    assert code == 0 and "Clone Detector" in out and "Stack Detector" in out and " L2 " in out

    code, out, _ = cli("findings", short, "--category", "security", "--limit", "3")
    assert code == 0 and "Hardcoded API key" in out and "backend/payments.js:7" in out

    code, out, _ = cli("show", short, "3")
    assert code == 0 and "Finding 3: Hardcoded API key" in out and "SHA-256 (cited lines)" in out

    code, out, _ = cli("source", short, "backend/payments.js:7")
    assert code == 0 and '>  7 │ const API_KEY = "sk-demo-secret";' in out

    code, out, _ = cli("ask", short, "Where", "is", "authentication", "implemented?")
    assert code == 0 and "Authentication is implemented primarily in:" in out
    assert "Brain → Intent Classifier → Memory Retriever ∥ Insight Recall → Citation Verifier" in out
    assert "6/6 cited sources verified" in out

    code, out, _ = cli("log", short, "--level", "1", "--limit", "200")
    levels = {int(level) for level in EVENT_LINE.findall(out)}
    assert code == 0 and levels == {0, 1}

    code, out, _ = cli("walkthrough", short)
    assert code == 0 and "8 steps · 120 seconds" in out

    code, out, _ = cli("architecture", short)
    assert code == 0 and "ShopLite" in out and "Request flows" in out

    code, out, _ = cli("mode")
    assert code == 0 and "DEMO / LOCAL ANALYSIS" in out and "4 lead agents → 21 specialist sub-agents" in out

    code, out, _ = cli("repos")
    assert code == 0 and short in out


def test_json_outputs_are_machine_readable(cli: Invoke) -> None:
    repo_id = demo_id(cli)
    tree = json.loads(cli("--json", "tree", repo_id)[1])
    assert tree["levels"] == 3 and [lead["name"] for lead in tree["leads"]] == [
        "security", "duplicate", "explainer", "walkthrough"]
    assert tree["totals"]["subagents_complete"] == 17
    status = json.loads(cli("--json", "status", repo_id)[1])
    assert status["report"]["orchestration"]["totals"]["subagents"] == 21
    ask = json.loads(cli("--json", "ask", repo_id, "API_KEY")[1])
    assert [step["agent"] for step in ask["trace"]] == ["intent", "retriever", "recall", "verifier", "composer"]
    assert ask["citations"][0]["file"] == "backend/payments.js" and "sk-demo-secret" not in json.dumps(ask)
    assert json.loads(cli("--json", "verify", repo_id)[1]) == {
        "repository_id": repo_id, "total": 16, "verified": 16, "stale": 0, "missing": 0}
    code, out, _ = cli("--json", "status", "ffffffff")
    assert code == 1 and json.loads(out)["error"] == "Repository not found: ffffffff"


def test_stale_source_loop_edit_verify_reanalyze(cli: Invoke) -> None:
    repo_id = demo_id(cli)
    code, out, _ = cli("edit", repo_id, "backend/payments.js", "7", "const API_KEY = process.env.PAYMENT_API_KEY;")
    assert code == 0 and "Working copy updated" in out

    code, out, _ = cli("verify", repo_id)
    assert code == 0 and "15/16 verified · 1 stale" in out and "Hardcoded API key" in out

    code, out, _ = cli("reanalyze", repo_id)
    assert code == 0 and "Re-analysis #2 requested" in out
    assert "Copied the bundled demo repository" not in out, "the previous run's timeline must not be replayed"
    assert "Resolved since the previous analysis: Hardcoded API key (backend/payments.js:7)" in out

    code, out, _ = cli("verify", repo_id)
    assert code == 0 and "15/15 findings verified against current source." in out


def test_open_runs_piped_commands_as_a_script(cli: Invoke, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_id = demo_id(cli)
    script = "tree\nfindings security\nshow 3\n# comments and blank lines are ignored\n\nexit\nstatus\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    code, out, _ = cli("open", repo_id[:8])
    assert code == 0
    assert f"evo[{repo_id[:8]}] › tree" in out and "Multi-level orchestration" in out
    assert "Finding 3: Hardcoded API key" in out and "Goodbye." in out
    assert "Status: COMPLETED" not in out, "commands after `exit` are not executed"

    monkeypatch.setattr("sys.stdin", io.StringIO("edit backend/payments.js 7\nconst API_KEY = process.env.KEY;\n"
                                                 "log 5 L2\nnot-a-command\n"))
    code, out, err = cli("open", repo_id)
    assert code == 1, "a failed command makes the script exit non-zero"
    assert "replacement › const API_KEY = process.env.KEY;" in out and "1 stale" in out
    assert "Unknown command: not-a-command" in err


def test_invalid_input_exits_with_an_error(cli: Invoke, tmp_path: Path) -> None:
    code, _, err = cli("status")
    assert code == 1 and "No repositories have been analyzed yet" in err
    code, _, err = cli("analyze", str(tmp_path / "missing"))
    assert code == 1 and "Repository source does not exist" in err
    repo_id = demo_id(cli)
    code, _, err = cli("show", repo_id, "99")
    assert code == 1 and "between 1 and 16" in err
    code, _, err = cli("source", repo_id, "../../etc/passwd")
    assert code == 1 and "Invalid path" in err


def test_parse_location() -> None:
    assert parse_location("a.js:7") == ("a.js", 7, 7)
    assert parse_location("a.js:3-9") == ("a.js", 3, 9)
    assert parse_location("a.js", "5") == ("a.js", 5, 5)
    assert parse_location("dir/a.js") == ("dir/a.js", 1, 1)
    with pytest.raises(ValueError):
        parse_location("a.js", "9-3")
    with pytest.raises(ValueError):
        parse_location("a.js", "x")


def test_second_cli_process_does_not_interrupt_a_running_analysis(settings: Settings) -> None:
    services = build_services(settings)
    services.store.create_repository(repo_id="a" * 16, name="busy", path="/tmp", source="demo", source_ref=None,
                                     task="t")
    services.store.update_repository("a" * 16, status="processing")
    build_services(settings, recover_after_seconds=1800)  # e.g. `./evo repos` in another terminal
    assert services.store.get_repository("a" * 16)["status"] == "processing"
    build_services(settings)  # the API server starting up closes abandoned analyses
    assert services.store.get_repository("a" * 16)["status"] == "failed"


def test_cancelled_analysis_is_marked_failed(settings: Settings) -> None:
    services = build_services(settings.with_overrides(pacing_ms=150))
    prepared = prepare_repository(services, "demo")

    async def scenario() -> None:
        task = asyncio.create_task(run_analysis(services, prepared, Console(stream=io.StringIO())))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    repo = services.store.get_repository(prepared.repository_id)
    assert repo is not None and repo["status"] == "failed" and "cancelled" in repo["error"]
