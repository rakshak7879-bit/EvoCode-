"""Solver Agent: turns a plain-language issue into a verified, actionable fix plan.

This is the lead agent (orchestration level 1) that an external model — for
example DeepSeek — drives through ``./evo solve``. Evo Code supplies the
intelligence and the proof; the model supplies the patch. Level-2 team:

1. Issue Analyst      symptoms, expected behavior, signals and acceptance criteria
2. Code Locator       ranked suspect files and symbols, each with a verified citation
3. Test Scout         detects the repository's test runner and the tests that cover them
4. Reproducer         runs those tests to capture the failing baseline (the proof)
5. Plan Writer        ordered steps with the exact files and lines to change
6. LLM Reviewer       (optional) refines the analysis and plan from verified facts only

Nothing here writes to the repository: the Solver only reads, runs tests and
plans. Applying a patch and re-running the gate is the coordinator's job.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agents.base import AgentResult, BaseAgent, SubAgentSpec
from agents.context import AgentContext, SourceFile
from agents.team import NO_LLM, Handler, SubAgentOutput, Upstream, plan_team, run_team
from llm.prompts import number_lines, redact_secrets, wrap_repository_content
from memory.tokens import split_identifier
from repo.test_runner import TestReport, TestRunner, detect_runners, run_tests

MAX_SUSPECTS = 8
MAX_SNIPPET_LINES = 40

# Words that carry no signal when locating code for an issue.
STOPWORDS = frozenset("""a an the is are was were be been being do does did doing have has had when while then than
that this these those there here it its it's if else for from with without into onto about above below over under
and or not no none null nil true false but so such very only just also too then now new old some any all each both
i we you they he she my our your their me us them as at by in on of to up out off again once more most other same
should would could can cannot will shall may might must need needs get gets got getting make makes made making
happens happen happened issue bug problem error fails failing failed fail broken break breaks expected actual
instead currently please thanks report reported steps reproduce reproduction repro description summary title
version behavior behaviour result results returns return returned returning value values code line lines file files
function functions method methods class classes test tests case cases add added adds adding fix fixed fixes fixing
support supports use used uses using work works working""".split())
# Signals worth extracting verbatim from an issue.
QUOTED = re.compile(r"`([^`\n]{2,80})`|\"([^\"\n]{3,80})\"|'([^'\n]{3,80})'")
PATH_LIKE = re.compile(r"\b[\w./-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs|go|rb|rs|java|php|cs|kt|swift|vue|svelte)\b")
IDENTIFIER = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\(\)?|\b([a-z]+(?:[A-Z]\w+)+)\b"
                        r"|\b([A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*)\b|\b([a-z_]+_[a-z_0-9]+)\b")
EXCEPTION = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning))\b(?::\s*([^\n]{0,120}))?")
TRACEBACK_FRAME = re.compile(r'File "([^"]+)", line (\d+)')
JS_FRAME = re.compile(r"at [^\s(]+ \(([\w./-]+):(\d+):\d+\)")
NUMBERED = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.+)$", re.MULTILINE)
EXPECTED_LINE = re.compile(r"(?im)^\W*(?:expected|should|must|want(?:ed)?|desired)\b[:\s]*(.{4,200})$")
ACTUAL_LINE = re.compile(r"(?im)^\W*(?:actual|but|instead|currently|observed|got|returns?)\b[:\s]*(.{4,200})$")
TEST_NAME = re.compile(r"\b((?:test_[\w]+|[\w]+_test|test[A-Z]\w+))\b")
TEST_PATH = re.compile(r"\b((?:tests?|spec|__tests__)/[\w./-]+|[\w./-]*(?:test_[\w-]+|[\w-]+[._]test|"
                       r"[\w-]+\.spec)\.[a-z]{2,4})\b")
KIND_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("crash", ("crash", "traceback", "stack trace", "exception", "segfault", "panic", "unhandled")),
    ("regression", ("regression", "used to work", "worked before", "since version", "after upgrading", "broke")),
    ("incorrect-behavior", ("wrong", "incorrect", "unexpected", "should return", "expected", "mismatch", "off by")),
    ("performance", ("slow", "timeout", "hangs", "memory leak", "performance", "cpu", "latency")),
    ("feature", ("add support", "feature request", "would be nice", "implement", "please add", "enhancement")),
    ("security", ("vulnerab", "injection", "xss", "csrf", "secret", "credential", "auth bypass")),
)
TEST_DIR_PARTS = frozenset({"test", "tests", "spec", "specs", "__tests__", "testing"})


class Suspect(BaseModel):
    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    symbol: str | None = None
    score: float
    reasons: list[str] = Field(default_factory=list)
    snippet: str = ""
    verification: dict[str, Any] = Field(default_factory=dict)
    sha256: str | None = None


class Step(BaseModel):
    order: int
    action: str
    file: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    detail: str = ""


class LLMRefinement(BaseModel):
    summary: str = ""
    root_cause: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    ranked_files: list[str] = Field(default_factory=list)


REFINE_SYSTEM = """You are helping a coding agent fix one issue in a repository.
Use ONLY the issue text and the provided source excerpts. Never invent files, symbols or line numbers.
Explain the most likely root cause in one or two sentences, list concrete acceptance criteria, and give ordered
fix steps that name real files. Order ranked_files from most to least likely to need the change.
Return JSON: {"summary": str, "root_cause": str, "acceptance_criteria": [str], "steps": [str], "ranked_files": [str]}"""


# --------------------------------------------------------------------------- issue understanding
def weighted_terms(text: str) -> dict[str, float]:
    """Search terms from an issue mapped to how strongly the issue points at them.

    Weight combines where a term appears (backticks and paths beat prose), how
    often it is repeated, and whether it shows up in the title or in the
    expected/actual sentences, which name the real subject of the issue.
    """
    scored: dict[str, float] = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = lines[0] if lines else ""
    emphasis = " ".join([title, *EXPECTED_LINE.findall(text), *ACTUAL_LINE.findall(text)])

    def add(term: str, weight: float) -> None:
        cleaned = term.strip().strip("`\"'.,:;()[]{}")
        if len(cleaned) < 3 or cleaned.lower() in STOPWORDS:
            return
        scored[cleaned] = max(scored.get(cleaned, 0.0), weight)

    for match in QUOTED.finditer(text):
        add(next(group for group in match.groups() if group), 3.0)
    for path in PATH_LIKE.findall(text):
        add(path, 3.0)
    for name, _ in EXCEPTION.findall(text):
        add(name, 2.5)
    for groups in IDENTIFIER.findall(text):
        for candidate in groups:
            if candidate:
                add(candidate, 2.0)
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text):
        add(word, 1.0)
    for term in list(scored):
        occurrences = len(re.findall(rf"(?<![\w]){re.escape(term)}(?![\w])", text))
        scored[term] += min(1.5, 0.25 * (occurrences - 1))
        if re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", emphasis):
            scored[term] += 1.5
    return dict(sorted(scored.items(), key=lambda item: (-item[1], item[0].lower()))[:40])


def issue_terms(text: str) -> list[str]:
    """Meaningful search terms from an issue, most specific first."""
    return list(weighted_terms(text))


def analyze_issue(issue: str) -> dict[str, Any]:
    """Break an issue into the parts that drive the rest of the solve."""
    text = issue.strip()
    lowered = f" {text.lower()} "
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = lines[0][:200] if lines else ""
    kinds = [kind for kind, words in KIND_WORDS if any(word in lowered for word in words)]
    exceptions = [{"type": name, "message": (message or "").strip()[:200]} for name, message in EXCEPTION.findall(text)]
    frames = [{"file": path, "line": int(number)} for path, number in
              (*TRACEBACK_FRAME.findall(text), *JS_FRAME.findall(text))]
    expected = [match.strip() for match in EXPECTED_LINE.findall(text)][:4]
    actual = [match.strip() for match in ACTUAL_LINE.findall(text)][:4]
    steps = [step.strip() for step in NUMBERED.findall(text)][:8]
    criteria = [f"Expected behavior holds: {item}" for item in expected]
    for exception in exceptions[:2]:
        label = exception["type"] + (f": {exception['message']}" if exception["message"] else "")
        criteria.append(f"{label} is no longer raised")
    for item in actual[:2]:
        criteria.append(f"The reported behavior is gone: {item}")
    if not criteria:
        criteria.append(f"The behavior described in the issue is fixed: {title or 'see the issue text'}")
    criteria.append("The repository's existing tests still pass (no regressions)")
    return {
        "title": title,
        "kinds": kinds or ["unspecified"],
        "terms": issue_terms(text),
        "paths": sorted({path for path in PATH_LIKE.findall(text)}),
        "exceptions": exceptions[:4],
        "frames": frames[:8],
        "expected": expected,
        "actual": actual,
        "reproduction_steps": steps,
        "test_names": sorted({name for group in TEST_NAME.findall(text) for name in [group] if name})[:8],
        "test_paths": sorted({path for path in TEST_PATH.findall(text)})[:8],
        "acceptance_criteria": criteria[:6],
        "length": len(text),
    }


# --------------------------------------------------------------------------- locating code
def _is_test_file(path: str) -> bool:
    parts = PurePosixPath(path.lower()).parts
    name = parts[-1] if parts else ""
    return any(part in TEST_DIR_PARTS for part in parts[:-1]) or bool(
        re.match(r"^(test_.*|.*_test|.*\.(?:test|spec))\.[a-z]+$", name))


def _symbol_window(file: SourceFile, line: int) -> tuple[int, int, str | None]:
    if file.parsed:
        symbol = file.parsed.symbol_at(line)
        if symbol:
            return symbol.line_start, min(symbol.line_end, symbol.line_start + MAX_SNIPPET_LINES), symbol.name
    return max(1, line - 5), min(len(file.lines), line + 15), None


def locate_suspects(context: AgentContext, analysis: dict[str, Any], limit: int = MAX_SUSPECTS) -> list[Suspect]:
    """Rank code that most likely needs to change, using memory search plus issue signals."""
    scores: dict[tuple[str, int, int], dict[str, Any]] = {}

    def record(file: SourceFile, line: int, weight: float, reason: str) -> None:
        start, end, symbol = _symbol_window(file, line)
        key = (file.path, start, end)
        entry = scores.setdefault(key, {"score": 0.0, "reasons": [], "symbol": symbol, "file": file})
        entry["score"] += weight * (0.35 if _is_test_file(file.path) else 1.0)
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)

    # 1. Stack-trace frames are the strongest signal.
    for frame in analysis["frames"]:
        for file in context.files:
            if file.path == frame["file"] or file.path.endswith("/" + frame["file"].lstrip("./")):
                record(file, min(frame["line"], len(file.lines) or 1), 6.0,
                       f"named in the stack trace at line {frame['line']}")
    weights: dict[str, float] = analysis.get("term_weights") or {term: 2.0 for term in analysis["terms"]}
    # 2. Paths named in the issue: the file matters, but the exact function matters more.
    for path in analysis["paths"]:
        for file in context.files:
            if file.path == path or file.path.endswith("/" + path.lstrip("./")):
                record(file, 1, 2.0, "named in the issue")
    # 3. Symbols named in the issue, weighted by how strongly the issue points at them.
    for term, weight in list(weights.items())[:14]:
        for symbol in context.symbol_index.get(term, []):
            file = context.file(symbol.file)
            if file:
                record(file, symbol.line_start, 1.4 * weight, f"defines `{term}`")
    # 4. Indexed-memory retrieval over the whole issue.
    query = " ".join(list(weights)[:12]) or analysis["title"]
    for rank, hit in enumerate(context.memory.search(query, limit=14)):
        file = context.file(hit.path or "")
        if file and hit.line_start:
            record(file, hit.line_start, max(0.8, 2.0 - rank * 0.15), "matched indexed memory")
    # 5. Literal occurrences of distinctive terms (a few per file, not just the first).
    for term, weight in list(weights.items())[:10]:
        if len(term) < 4:
            continue
        pattern = re.compile(rf"(?<![\w]){re.escape(term)}(?![\w])")
        for file in context.source_files:
            hits = [number for number, line in enumerate(file.lines, start=1) if pattern.search(line)][:3]
            for number in hits:
                record(file, number, 0.5 * weight / max(1, len(hits)), f"mentions `{term}`")

    suspects: list[Suspect] = []
    for (path, start, end), entry in sorted(scores.items(), key=lambda item: -item[1]["score"])[:limit]:
        file: SourceFile = entry["file"]
        snippet = "\n".join(file.lines[start - 1 : end])
        suspects.append(Suspect(file=path, line_start=start, line_end=end, symbol=entry["symbol"],
                                score=round(entry["score"], 2), reasons=entry["reasons"][:4],
                                snippet=redact_secrets(snippet)))
    return suspects


def related_tests(context: AgentContext, analysis: dict[str, Any], suspects: list[Suspect],
                  consider: int = 3) -> list[str]:
    """Test files that most likely cover the issue.

    Only the strongest suspects are considered, so a weakly ranked file cannot
    drag the whole suite into the "covering tests" set.
    """
    named = {path for path in analysis["test_paths"]}
    for name in analysis["test_names"]:
        for symbol in context.symbol_index.get(name, []):
            named.add(symbol.file)
    leading = suspects[:consider]
    stems = {PurePosixPath(suspect.file).stem for suspect in leading}
    symbols = {suspect.symbol for suspect in leading if suspect.symbol}
    for file in context.files:
        if not _is_test_file(file.path):
            continue
        core = re.sub(r"^test[_.]|[_.]test$|\.spec$", "", PurePosixPath(file.path).stem)
        if core in stems or any(part and part in stems for part in split_identifier(core)):
            named.add(file.path)
        elif any(re.search(rf"(?<![\w]){re.escape(symbol)}(?![\w])", file.text) for symbol in symbols):
            named.add(file.path)
    existing = {file.path for file in context.files}
    return sorted(path for path in named if path in existing)[:6]


SOLVER_TEAM: tuple[SubAgentSpec, ...] = (
    SubAgentSpec("analyst", "Issue Analyst",
                 "Breaks the issue into symptoms, signals, reproduction steps and acceptance criteria"),
    SubAgentSpec("locator", "Code Locator",
                 "Ranks the files and symbols that most likely need the change, with verified citations",
                 depends_on=("analyst",)),
    SubAgentSpec("scout", "Test Scout",
                 "Detects the repository's test runner and the tests covering the suspect code",
                 depends_on=("analyst", "locator")),
    SubAgentSpec("reproducer", "Reproducer",
                 "Runs the covering tests to capture the failing baseline that proves the issue",
                 depends_on=("scout",)),
    SubAgentSpec("planner", "Plan Writer", "Writes ordered fix steps naming the exact files and lines",
                 depends_on=("analyst", "locator"), uses=("scout", "reproducer")),
    SubAgentSpec("llm_reviewer", "LLM Solution Reviewer",
                 "Refines the root cause, criteria and steps from the verified excerpts (secrets redacted)",
                 depends_on=("planner",), uses=("analyst", "locator"), requires_llm=True,
                 fallback="the deterministic analysis and plan are used as they are"),
)


class SolverAgent(BaseAgent):
    name = "solver"
    title = "Solver Agent"
    description = "Breaks an issue down, locates the code, proves it fails and writes a verified fix plan."
    team = SOLVER_TEAM

    def __init__(self, *, run_tests_enabled: bool = True, test_timeout: float = 300.0) -> None:
        self.run_tests_enabled = run_tests_enabled
        self.test_timeout = test_timeout

    async def run(self, context: AgentContext) -> AgentResult:
        handlers: dict[str, Handler] = {
            "analyst": self._analyze,
            "locator": self._locate,
            "scout": self._scout,
            "reproducer": self._reproduce,
            "planner": self._plan,
            "llm_reviewer": self._refine,
        }
        team = await run_team(self.name, plan_team(self.team, handlers, context), context)
        analysis = team.value("analyst")
        if analysis is None:
            raise RuntimeError("The Issue Analyst did not complete: " + "; ".join(team.failure_notes()))
        suspects: list[Suspect] = team.value("locator", [])
        scout = team.value("scout", {"runners": [], "tests": [], "runner": None})
        baseline: TestReport | None = team.value("reproducer")
        plan = team.value("planner", {"steps": [], "strategy": ""})
        refinement: LLMRefinement | None = team.value("llm_reviewer")

        notes = team.failure_notes()
        mode = "local"
        if refinement is not None:
            mode = "llm"
            if refinement.root_cause:
                analysis["root_cause"] = refinement.root_cause
            if refinement.summary:
                analysis["summary"] = refinement.summary
            if refinement.acceptance_criteria:
                analysis["acceptance_criteria"] = refinement.acceptance_criteria[:8]
            if refinement.steps:
                plan["steps"] = [Step(order=index, action=text[:300]).model_dump()
                                 for index, text in enumerate(refinement.steps[:10], start=1)]
                plan["strategy"] = "llm"
            if refinement.ranked_files:
                order = {path: index for index, path in enumerate(refinement.ranked_files)}
                suspects.sort(key=lambda s: (order.get(s.file, len(order)), -s.score))
        elif (run := team.run("llm_reviewer")) is not None and run.reason == NO_LLM:
            notes.append("No LLM configured: deterministic issue analysis and fix plan (local mode).")

        proof = "no baseline"
        if baseline is not None:
            proof = {"failed": "reproduced", "passed": "not reproduced", "timeout": "timed out",
                     "error": "could not run", "skipped": "execution disabled"}.get(baseline.status, baseline.status)
        return AgentResult(
            agent=self.name,
            mode=mode,  # type: ignore[arg-type]
            summary=(f"{len(suspects)} suspect location(s) · {len(plan['steps'])} planned step(s) · "
                     f"baseline {proof}"),
            data={
                "issue": analysis["issue"],
                "analysis": analysis,
                "suspects": [suspect.model_dump(mode="json") for suspect in suspects],
                "tests": scout,
                "baseline": baseline.to_dict(include_output=False) if baseline else None,
                "baseline_output": baseline.output[-8000:] if baseline else "",
                "plan": plan,
            },
            notes=notes,
            metrics={"suspects": len(suspects), "steps": len(plan["steps"]),
                     "reproduced": 1.0 if baseline and baseline.status == "failed" else 0.0},
            subagents=team.runs,
        )

    # ------------------------------------------------------------------ team members
    @staticmethod
    def _issue(context: AgentContext) -> str:
        return context.upstream["issue"].data["text"]

    def _analyze(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        issue = self._issue(context)
        analysis = analyze_issue(issue)
        analysis["issue"] = issue
        analysis["summary"] = analysis["title"]
        analysis["term_weights"] = weighted_terms(issue)
        analysis["root_cause"] = ""
        signals = []
        if analysis["exceptions"]:
            signals.append(f"{len(analysis['exceptions'])} exception(s)")
        if analysis["frames"]:
            signals.append(f"{len(analysis['frames'])} stack frame(s)")
        if analysis["paths"]:
            signals.append(f"{len(analysis['paths'])} path(s)")
        if analysis["reproduction_steps"]:
            signals.append(f"{len(analysis['reproduction_steps'])} reproduction step(s)")
        return SubAgentOutput(
            summary=f"{'/'.join(analysis['kinds'])} · {len(analysis['terms'])} search term(s)"
                    + (f" · {', '.join(signals)}" if signals else "")
                    + f" · {len(analysis['acceptance_criteria'])} acceptance criteria",
            value=analysis,
            metrics={"terms": len(analysis["terms"]), "criteria": len(analysis["acceptance_criteria"])},
        )

    @staticmethod
    def _locate(context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        suspects = locate_suspects(context, upstream["analyst"].value)
        top = ", ".join(f"{s.file}:{s.line_start}" for s in suspects[:3])
        return SubAgentOutput(summary=f"{len(suspects)} suspect location(s)" + (f": {top}" if top else ""),
                              value=suspects, metrics={"suspects": len(suspects)})

    def _scout(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        root = Path(context.upstream["issue"].data["root"])
        suspects: list[Suspect] = upstream["locator"].value
        runners = detect_runners(root)
        tests = related_tests(context, upstream["analyst"].value, suspects)
        return SubAgentOutput(summary=f"{len(runners)} runner(s), {len(tests)} covering test file(s)",
                              value={"runners": [r.key for r in runners], "runner": runners[0].key if runners else None,
                                     "command": runners[0].display if runners else None, "tests": tests},
                              metrics={"runners": len(runners), "tests": len(tests)})

    def _reproduce(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        root = Path(context.upstream["issue"].data["root"])
        scout = upstream["scout"].value
        runners = detect_runners(root)
        runner: TestRunner | None = next((r for r in runners if r.key == scout["runner"]), None)
        if runner is None:
            return SubAgentOutput(summary="No test runner detected: no baseline to capture", value=None)
        targets = tuple(scout["tests"])
        report = run_tests(root, runner, targets=targets, timeout=self.test_timeout,
                           allow_execution=self.run_tests_enabled)
        scope = f"{len(targets)} covering test file(s)" if targets else "the whole suite"
        return SubAgentOutput(summary=f"{runner.title} on {scope}: {report.summary()}", value=report,
                             metrics={"duration_ms": float(report.duration_ms)})

    @staticmethod
    def _plan(context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        analysis = upstream["analyst"].value
        suspects: list[Suspect] = upstream["locator"].value
        scout = upstream["scout"].value if "scout" in upstream else {"runner": None, "tests": []}
        baseline: TestReport | None = upstream["reproducer"].value if "reproducer" in upstream else None
        steps: list[Step] = []

        def add(action: str, detail: str = "", suspect: Suspect | None = None) -> None:
            steps.append(Step(order=len(steps) + 1, action=action, detail=detail,
                              file=suspect.file if suspect else None,
                              line_start=suspect.line_start if suspect else None,
                              line_end=suspect.line_end if suspect else None))

        if baseline is not None and baseline.status == "failed" and baseline.failing:
            add("Start from the failing test", f"{baseline.failing[0]} fails now; it must pass when you are done.")
        elif scout["runner"]:
            add("Write a test that reproduces the issue",
                f"Add it to {scout['tests'][0] if scout['tests'] else 'the test suite'} and confirm it fails first.")
        for suspect in suspects[:3]:
            where = f"{suspect.file}:{suspect.line_start}-{suspect.line_end}"
            target = f"`{suspect.symbol}`" if suspect.symbol else "this range"
            add(f"Inspect {target} in {suspect.file}", f"{where} — {'; '.join(suspect.reasons) or 'ranked by memory'}",
                suspect)
        # Prefer a named symbol the issue actually mentions over a whole-file window.
        named = {term.lower() for term in (analysis.get("term_weights") or analysis["terms"])}
        primary = next((s for s in suspects if s.symbol and s.symbol.lower() in named), None) or \
            next((s for s in suspects if s.symbol), None) or (suspects[0] if suspects else None)
        if primary:
            add(f"Change {f'`{primary.symbol}`' if primary.symbol else primary.file} to satisfy the expected behavior",
                (analysis["expected"][0] if analysis["expected"] else analysis["title"])[:200], primary)
        add("Run the covering tests first (fast feedback)",
            "./evo test --covering" + (f" — {', '.join(scout['tests'][:3])}" if scout["tests"] else ""))
        add("Run the gate", "./evo check — the issue counts as solved only when the whole suite passes "
                            "and a previously failing test now passes.")
        strategy = "failing-test-first" if baseline and baseline.status == "failed" else "locate-then-verify"
        return SubAgentOutput(summary=f"{len(steps)} step(s) · strategy {strategy}",
                              value={"steps": [step.model_dump() for step in steps], "strategy": strategy},
                              metrics={"steps": len(steps)})

    async def _refine(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        analysis = upstream["analyst"].value
        suspects: list[Suspect] = upstream["locator"].value
        blocks = []
        for suspect in suspects[:5]:
            file = context.file(suspect.file)
            if file is None:
                continue
            body = number_lines([redact_secrets(line) for line in
                                 file.lines[suspect.line_start - 1 : suspect.line_end]], suspect.line_start)
            blocks.append((f"{suspect.file}:{suspect.line_start}-{suspect.line_end}"
                           f"{f' ({suspect.symbol})' if suspect.symbol else ''} — {'; '.join(suspect.reasons)}", body))
        prompt = (f"ISSUE:\n{redact_secrets(analysis['issue'])[:4000]}\n\n"
                  f"Deterministic acceptance criteria: {analysis['acceptance_criteria']}\n"
                  f"Candidate files: {[s.file for s in suspects]}\n\n{wrap_repository_content(blocks)}")
        payload = await context.llm.complete_json(system=REFINE_SYSTEM, prompt=prompt, max_tokens=1200)
        try:
            refinement = LLMRefinement.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"The model returned an unusable refinement: {exc.error_count()} invalid field(s)")
        known = {suspect.file for suspect in suspects} | {file.path for file in context.files}
        refinement.ranked_files = [path for path in refinement.ranked_files if path in known]
        counted = Counter({"criteria": len(refinement.acceptance_criteria), "steps": len(refinement.steps)})
        return SubAgentOutput(
            summary=("root cause refined · " if refinement.root_cause else "")
                    + f"{counted['criteria']} criteria · {counted['steps']} step(s)"
                    + (f" · reordered {len(refinement.ranked_files)} file(s)" if refinement.ranked_files else ""),
            value=refinement, mode="llm", metrics=dict(counted),
        )
