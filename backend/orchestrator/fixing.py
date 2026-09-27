"""Brain-orchestrated fixing: goal → Fixer Agent (L1) → fix sub-agents (L2) → approval → apply.

``FixCoordinator.plan`` re-verifies the findings, builds a read-only context
from the working copy and delegates the goal to the Fixer Agent, streaming
every orchestration event to a sink (the CLI prints them live). Nothing is
written while planning. ``FixCoordinator.apply`` writes only the patches the
user approved, only into Evo Code's isolated working copy, and saves a unified
diff that can be applied to the real repository with ``git apply``. The caller
then re-runs the analysis so memory records the findings as resolved.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.base import AgentResult, SubAgentRun
from agents.fixer import FixerAgent, FixProposal
from agents.team import DelegationRuntime
from config import Settings
from memory.store import MemoryStore, utc_now
from orchestrator.brain import Brain
from orchestrator.evidence import reverify_findings, sort_findings
from repo.paths import resolve_in_root
from repo.source_service import SourceAccessError, replace_indexed_lines
from repo.text import decode_source

logger = logging.getLogger("evo.fixer")
Sink = Callable[[dict[str, Any]], None]


class FixEvents:
    """Timeline-shaped events (same format as analysis events) sent to a sink; also the L2 observer."""

    def __init__(self, sink: Sink | None, *, titles: dict[str, str] | None = None, stage: str = "fixing") -> None:
        self.sink = sink
        self.titles = titles or {}
        self.default_stage = stage
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []

    def title(self, agent: str) -> str:
        return self.titles.get(agent, f"{agent.capitalize()} Agent")

    def log(self, message: str, *, level: str = "info", stage: str | None = None, depth: int = 0,
            agent: str | None = None, parent: str | None = None) -> None:
        event: dict[str, Any] = {"ts": utc_now(), "elapsed_ms": int((time.monotonic() - self.started) * 1000),
                                 "stage": stage or self.default_stage, "level": level, "message": message,
                                 "depth": depth}
        if agent:
            event["agent"] = agent
        if parent:
            event["parent"] = parent
        self.events.append(event)
        if self.sink is not None:
            self.sink(event)

    def team_planned(self, parent: str, runs: Sequence[SubAgentRun]) -> None:
        active = [run for run in runs if run.status != "skipped"]
        waves = max((run.wave for run in active), default=0)
        self.log(f"{self.title(parent)} delegates to {len(active)} specialist sub-agent"
                 f"{'s' if len(active) != 1 else ''} in {waves} wave{'s' if waves != 1 else ''}: "
                 + ", ".join(run.title for run in active), agent=parent, depth=1)
        for run in runs:
            if run.status == "skipped":
                self.log(f"{run.title} skipped: {run.reason}", agent=run.name, parent=parent, depth=2)

    def member_started(self, run: SubAgentRun) -> None:
        return None

    def member_finished(self, run: SubAgentRun) -> None:
        if run.status == "complete":
            self.log(f"{run.title}: {run.summary}", level="success", agent=run.name, parent=run.parent, depth=2)
        elif run.status == "failed":
            self.log(f"{run.title} failed: {run.error}", level="error", agent=run.name, parent=run.parent, depth=2)
        else:
            self.log(f"{run.title} skipped: {run.reason}", level="warning", agent=run.name, parent=run.parent,
                     depth=2)


@dataclass
class FixPlan:
    repository_id: str
    goal: str
    scope: str = ""
    in_scope: int = 0
    proposals: list[FixProposal] = field(default_factory=list)
    manual: list[dict[str, Any]] = field(default_factory=list)
    runs: list[SubAgentRun] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    mode: str = "local"
    error: str | None = None

    @property
    def verified(self) -> list[FixProposal]:
        return [proposal for proposal in self.proposals if proposal.status == "verified"]

    @property
    def rejected(self) -> list[FixProposal]:
        return [proposal for proposal in self.proposals if proposal.status == "rejected"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id, "goal": self.goal, "scope": self.scope, "in_scope": self.in_scope,
            "mode": self.mode, "error": self.error, "notes": self.notes, "manual": self.manual,
            "proposals": [proposal.model_dump(mode="json") for proposal in self.proposals],
            "subagents": [run.model_dump(mode="json") for run in self.runs],
        }


@dataclass
class FixApplication:
    applied: list[FixProposal] = field(default_factory=list)
    conflicts: list[tuple[FixProposal, str]] = field(default_factory=list)
    patch: str = ""
    patch_path: Path | None = None

    @property
    def files(self) -> list[str]:
        return sorted({proposal.file for proposal in self.applied})

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": [proposal.id for proposal in self.applied],
            "conflicts": [{"id": proposal.id, "reason": reason} for proposal, reason in self.conflicts],
            "files": self.files,
            "patch_path": str(self.patch_path) if self.patch_path else None,
        }


def unified_diff(path: str, before: str, after: str) -> str:
    """A ``git apply``-compatible diff of one file."""
    lines = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                 fromfile=f"a/{path}", tofile=f"b/{path}")
    text = ""
    for line in lines:
        text += line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
    return (f"diff --git a/{path} b/{path}\n" + text) if text else ""


class FixCoordinator:
    def __init__(self, store: MemoryStore, brain: Brain, settings: Settings, fixer: FixerAgent | None = None) -> None:
        self.store = store
        self.brain = brain
        self.settings = settings
        self.fixer = fixer or FixerAgent()

    async def plan(self, repo_id: str, goal: str, sink: Sink | None = None) -> FixPlan:
        repo = self.store.get_repository(repo_id)
        if repo is None:
            raise KeyError(repo_id)
        if repo["status"] != "completed":
            raise ValueError("The repository has no completed analysis to fix. Run or re-run the analysis first.")
        goal = goal.strip() or "fix all security issues"
        root = Path(repo["path"])
        events = FixEvents(sink, titles={self.fixer.name: self.fixer.title})
        events.log(f'Understanding fix goal: "{goal}"', stage="understanding")
        findings = sort_findings(await asyncio.to_thread(reverify_findings, self.store, repo_id, root))
        stale = sum(1 for finding in findings if finding["status"] != "verified")
        events.log(f"Re-verified {len(findings)} finding(s) against the working copy: "
                   f"{len(findings) - stale} verified · {stale} stale", stage="retrieving",
                   level="success" if not stale else "warning")
        context, summary = await asyncio.to_thread(self.brain.context_builder.build, repo_id, repo["name"], root, goal)
        events.log(f"Loaded {summary.files} file(s) from the isolated working copy (read-only while planning)",
                   stage="retrieving")
        events.log(f"Route → {self.fixer.title}: the goal asks Evo Code to change code", stage="routing",
                   agent=self.fixer.name)
        context = context.with_upstream({
            "findings": AgentResult(agent="findings", data={"goal": goal, "findings": findings}),
        }).with_delegation(DelegationRuntime(
            observer=events,
            min_visible_ms=int(self.settings.pacing_ms * 0.5),
            simulate_failures=frozenset(self.settings.simulate_agent_failure),
            timeout_seconds=self.settings.agent_timeout_seconds,
        ))
        events.log(f"{self.fixer.title} started", stage="running", depth=1, agent=self.fixer.name)
        plan = FixPlan(repo_id, goal)
        try:
            if self.fixer.name in self.settings.simulate_agent_failure:
                raise RuntimeError("Simulated failure (EVO_SIMULATE_AGENT_FAILURE)")
            result = await asyncio.wait_for(self.fixer.run(context), timeout=self.settings.agent_timeout_seconds)
        except Exception as exc:  # the fixer failing must never touch the working copy
            if not isinstance(exc, RuntimeError) or "Simulated" not in str(exc):
                logger.exception("Fixer failed", extra={"repository_id": repo_id})
            plan.error = f"{exc.__class__.__name__}: {exc}"
            events.log(f"{self.fixer.title} failed: {plan.error}", level="error", stage="running", depth=1,
                       agent=self.fixer.name)
            return plan
        data = result.data
        plan.scope, plan.in_scope, plan.mode, plan.notes = data["scope"], data["in_scope"], result.mode, result.notes
        plan.proposals = [FixProposal.model_validate(item) for item in data["proposals"]]
        plan.manual = data["manual"]
        plan.runs = result.subagents
        events.log(f"{self.fixer.title} completed: {result.summary}", level="success", stage="running", depth=1,
                   agent=self.fixer.name)
        events.log(f"{len(plan.verified)} verified patch(es) ready for review · nothing has been changed yet",
                   stage="reviewing", level="success" if plan.verified else "info")
        return plan

    def apply(self, repo_id: str, proposals: Sequence[FixProposal]) -> FixApplication:
        """Write approved, verified patches into the working copy and save a unified diff."""
        repo = self.store.get_repository(repo_id)
        if repo is None:
            raise KeyError(repo_id)
        if not self.settings.allow_source_edits:
            raise ValueError("Source edits are disabled (EVO_ALLOW_SOURCE_EDITS=false).")
        root = Path(repo["path"])
        outcome = FixApplication()
        originals: dict[str, str] = {}
        for proposal in sorted(proposals, key=lambda p: (p.file, p.line_start)):
            if proposal.status != "verified":
                outcome.conflicts.append((proposal, "Only verified patches can be applied."))
                continue
            try:
                if proposal.file not in originals:
                    originals[proposal.file] = decode_source(resolve_in_root(root, proposal.file).read_bytes())
                replace_indexed_lines(self.store, repo_id, root, proposal.file, proposal.line_start,
                                      proposal.before, proposal.after)
            except (SourceAccessError, OSError, ValueError) as exc:
                outcome.conflicts.append((proposal, str(exc)))
                continue
            outcome.applied.append(proposal)
        diffs = []
        for path in outcome.files:
            after = decode_source(resolve_in_root(root, path).read_bytes())
            diffs.append(unified_diff(path, originals[path], after))
        outcome.patch = "".join(diffs)
        if outcome.patch:
            patches = self.settings.data_dir / "patches"
            patches.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            outcome.patch_path = patches / f"{repo_id[:8]}-analysis{repo['analysis_count']}-{stamp}.patch"
            outcome.patch_path.write_text(outcome.patch, encoding="utf-8")
        return outcome
