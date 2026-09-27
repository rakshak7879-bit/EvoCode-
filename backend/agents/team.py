"""Level-2 orchestration: lead agents delegate to specialist sub-agents.

The Brain (level 0) routes work to lead agents (level 1). A lead plans a team
of specialist sub-agents (level 2) with ``plan_team`` and ``run_team`` executes
it as a dependency graph: members whose dependencies are satisfied run
concurrently in waves, every failure is isolated, LLM-only members are skipped
(and labeled) in local mode, and dependents of a failed member are skipped with
a reason. The lead then consolidates the members' outputs into one
``AgentResult`` for the Brain.

Sub-agents are functions of the read-only ``AgentContext`` plus the outputs of
the members they declare as dependencies. They never touch the database, the
filesystem or each other; progress flows through the narrow ``TeamObserver``
that the Brain supplies via ``context.delegation``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from agents.base import FindingDraft, SubAgentRun, SubAgentSpec
from llm.provider import LLMError

if TYPE_CHECKING:
    from agents.context import AgentContext

logger = logging.getLogger("evo.agents.team")

NOT_REQUIRED = "Not required for this focused task"
NO_LLM = "No LLM configured (local analysis)"
SIMULATED_FAILURE = "Simulated failure (EVO_SIMULATE_AGENT_FAILURE)"


@dataclass
class SubAgentOutput:
    """What a sub-agent hands back to its lead (in-process; ``value`` is never persisted)."""

    summary: str
    value: Any = None
    findings: list[FindingDraft] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    mode: str = "local"


Upstream = Mapping[str, SubAgentOutput]
Handler = Callable[["AgentContext", Upstream], "SubAgentOutput | Awaitable[SubAgentOutput]"]


class TeamObserver(Protocol):
    """Receives level-2 lifecycle events (the Brain's progress reporter implements this)."""

    def team_planned(self, parent: str, runs: Sequence[SubAgentRun]) -> None: ...

    def member_started(self, run: SubAgentRun) -> None: ...

    def member_finished(self, run: SubAgentRun) -> None: ...


class NullObserver:
    """Observer used when a lead runs outside the Brain (for example in unit tests)."""

    def team_planned(self, parent: str, runs: Sequence[SubAgentRun]) -> None:
        return None

    def member_started(self, run: SubAgentRun) -> None:
        return None

    def member_finished(self, run: SubAgentRun) -> None:
        return None


@dataclass(frozen=True)
class DelegationRuntime:
    """How the Brain wants lead agents to run their teams."""

    observer: TeamObserver = field(default_factory=NullObserver)
    #: The Brain routed a focused task (not a full analysis): leads may narrow their teams.
    allow_narrowing: bool = False
    #: Minimum visible time per member in milliseconds (demo pacing; never changes results).
    min_visible_ms: int = 0
    #: Full member names such as ``security.secrets`` that must fail (graceful-degradation demos).
    simulate_failures: frozenset[str] = frozenset()
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class TeamMember:
    """A planned member: its spec, the handler that implements it and why it runs (or not)."""

    spec: SubAgentSpec
    handler: Handler
    reason: str
    skip_reason: str | None = None


@dataclass
class TeamResult:
    parent: str
    runs: list[SubAgentRun]
    outputs: dict[str, SubAgentOutput]
    specs: dict[str, SubAgentSpec]

    def run(self, key: str) -> SubAgentRun | None:
        return next((run for run in self.runs if run.key == key), None)

    def output(self, key: str) -> SubAgentOutput | None:
        return self.outputs.get(key)

    def value(self, key: str, default: Any = None) -> Any:
        output = self.outputs.get(key)
        return output.value if output is not None else default

    def completed(self, key: str) -> bool:
        return key in self.outputs

    @property
    def any_completed(self) -> bool:
        return bool(self.outputs)

    @property
    def active(self) -> list[SubAgentRun]:
        return [run for run in self.runs if run.wave > 0]

    @property
    def waves(self) -> int:
        return max((run.wave for run in self.runs), default=0)

    def failure_notes(self) -> list[str]:
        """Notes for failed members (and members skipped because an upstream member failed)."""
        notes: list[str] = []
        for run in self.runs:
            if run.status == "failed":
                fallback = self.specs[run.key].fallback
                notes.append(f"{run.title} unavailable ({run.error})" + (f"; {fallback}." if fallback else "."))
            elif run.status == "skipped" and run.wave > 0:
                notes.append(f"{run.title} skipped: {run.reason}.")
        return notes

    def focus_note(self) -> str | None:
        """Note describing task-driven narrowing, if the lead narrowed its team."""
        excluded = [run for run in self.runs if run.reason == NOT_REQUIRED]
        if not excluded:
            return None
        chosen = [run.title for run in self.runs if run.reason != NOT_REQUIRED and run.wave > 0]
        return (f"Task-focused delegation: {len(chosen)}/{len(self.runs)} specialists ran "
                f"({', '.join(chosen) or 'none'}).")


def plan_team(
    specs: Sequence[SubAgentSpec],
    handlers: Mapping[str, Handler],
    context: "AgentContext",
    *,
    route_by_task: bool = False,
) -> list[TeamMember]:
    """Decide which members run and why.

    With ``route_by_task`` and a focused task from the Brain, only members whose
    keywords appear in the task (plus their hard dependencies) are selected. LLM
    members are skipped in local mode, and skips propagate along hard
    dependencies so nothing runs without its required inputs.
    """
    by_key = {spec.key: spec for spec in specs}
    missing = [spec.key for spec in specs if spec.key not in handlers]
    if missing:
        raise ValueError(f"No handler for team member(s): {', '.join(missing)}")
    runtime = context.delegation
    lowered = f" {context.task.lower()} "
    focused = route_by_task and runtime.allow_narrowing
    selected: dict[str, str] = {}
    if focused:
        for spec in specs:
            hit = next((word for word in spec.keywords if word in lowered), None)
            if hit:
                selected[spec.key] = f"Task mentions '{hit.strip()}'"
        pending = list(selected)
        while pending:
            spec = by_key[pending.pop()]
            for dependency in spec.depends_on:
                if dependency in by_key and dependency not in selected:
                    selected[dependency] = f"Required by the {spec.title}"
                    pending.append(dependency)
    narrowed = bool(selected)

    members: list[TeamMember] = []
    for spec in specs:
        handler = handlers[spec.key]
        if narrowed and spec.key not in selected:
            members.append(TeamMember(spec, handler, NOT_REQUIRED, NOT_REQUIRED))
        elif spec.requires_llm and not context.llm.available:
            members.append(TeamMember(spec, handler, NO_LLM, NO_LLM))
        else:
            members.append(TeamMember(spec, handler, selected.get(spec.key) or _default_reason(spec, by_key, focused)))

    skipped = {member.spec.key for member in members if member.skip_reason}
    changed = True
    while changed:
        changed = False
        for index, member in enumerate(members):
            if member.skip_reason:
                continue
            blocked = [dep for dep in member.spec.depends_on if dep in skipped or dep not in by_key]
            if blocked:
                names = ", ".join(by_key[dep].title if dep in by_key else dep for dep in blocked)
                reason = f"Requires {names}, which will not run"
                members[index] = replace(member, reason=reason, skip_reason=reason)
                skipped.add(member.spec.key)
                changed = True
    return members


def _default_reason(spec: SubAgentSpec, by_key: Mapping[str, SubAgentSpec], focused: bool) -> str:
    upstream = [by_key[key].title for key in (*spec.depends_on, *spec.uses) if key in by_key]
    if upstream:
        return f"Builds on {', '.join(upstream)}"
    if focused:
        return "No narrower focus in the task: full team"
    return "Full analysis: full team"


def assign_waves(members: Sequence[TeamMember]) -> dict[str, int]:
    """Wave number (1-based) of every active member: 1 + the deepest planned dependency."""
    active = {member.spec.key: member.spec for member in members if not member.skip_reason}
    waves: dict[str, int] = {}
    visiting: set[str] = set()

    def wave(key: str) -> int:
        if key in waves:
            return waves[key]
        if key in visiting:
            raise ValueError(f"Dependency cycle in agent team at {key!r}")
        visiting.add(key)
        spec = active[key]
        upstream = [dep for dep in (*spec.depends_on, *spec.uses) if dep in active]
        waves[key] = 1 + max((wave(dep) for dep in upstream), default=0)
        visiting.discard(key)
        return waves[key]

    for key in active:
        wave(key)
    return waves


async def run_team(parent: str, members: Sequence[TeamMember], context: "AgentContext") -> TeamResult:
    """Execute a planned team wave by wave and return every member's record and output."""
    runtime = context.delegation
    specs = {member.spec.key: member.spec for member in members}
    waves = assign_waves(members)
    runs: dict[str, SubAgentRun] = {}
    for member in members:
        spec = member.spec
        runs[spec.key] = SubAgentRun(
            name=f"{parent}.{spec.key}",
            key=spec.key,
            parent=parent,
            title=spec.title,
            description=spec.description,
            status="skipped" if member.skip_reason else "queued",
            reason=member.skip_reason or member.reason,
            wave=waves.get(spec.key, 0),
            depends_on=[key for key in (*spec.depends_on, *spec.uses) if key in specs],
        )
    runtime.observer.team_planned(parent, [runs[member.spec.key] for member in members])

    outputs: dict[str, SubAgentOutput] = {}
    for wave in sorted(set(waves.values())):
        ready: list[TeamMember] = []
        for member in members:
            key = member.spec.key
            if waves.get(key) != wave:
                continue
            blocked = [dep for dep in member.spec.depends_on if dep not in outputs]
            if blocked:
                run = runs[key]
                run.status = "skipped"
                run.reason = f"Upstream {', '.join(runs[dep].title for dep in blocked if dep in runs)} did not complete"
                runtime.observer.member_finished(run)
                continue
            ready.append(member)
        results = await asyncio.gather(*(
            _execute(member, runs[member.spec.key], context,
                     {dep: outputs[dep] for dep in (*member.spec.depends_on, *member.spec.uses) if dep in outputs})
            for member in ready
        ))
        for member, output in zip(ready, results):
            if output is not None:
                outputs[member.spec.key] = output
    return TeamResult(parent, [runs[member.spec.key] for member in members], outputs, specs)


async def _execute(member: TeamMember, run: SubAgentRun, context: "AgentContext",
                   upstream: Upstream) -> SubAgentOutput | None:
    runtime = context.delegation
    run.status = "running"
    runtime.observer.member_started(run)
    started = time.monotonic()
    output: SubAgentOutput | None = None
    try:
        if run.name in runtime.simulate_failures:
            run.status, run.error = "failed", SIMULATED_FAILURE
        else:
            if inspect.iscoroutinefunction(member.handler):
                call: Awaitable[Any] = member.handler(context, upstream)  # type: ignore[assignment]
            else:
                call = asyncio.to_thread(member.handler, context, upstream)
            result = await (asyncio.wait_for(call, runtime.timeout_seconds) if runtime.timeout_seconds else call)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, SubAgentOutput):
                raise TypeError(f"{run.title} returned {type(result).__name__}, expected SubAgentOutput")
            output = result
            run.status = "complete"
            run.summary = output.summary
            run.mode = "llm" if output.mode == "llm" else "local"
            run.findings = len(output.findings)
            run.metrics = dict(output.metrics)
            run.notes = list(output.notes)
    except TimeoutError:
        run.status, run.error = "failed", f"Timed out after {runtime.timeout_seconds:.0f}s"
    except LLMError as exc:
        run.status, run.error = "failed", str(exc) or "LLM request failed"
    except Exception as exc:  # one sub-agent failing must never break its lead
        logger.exception("Sub-agent failed", extra={"agent": run.name, "repository_id": context.repository_id})
        run.status, run.error = "failed", f"{exc.__class__.__name__}: {exc}"
    run.duration_ms = int((time.monotonic() - started) * 1000)
    remaining = runtime.min_visible_ms / 1000 - (time.monotonic() - started)
    if remaining > 0:
        await asyncio.sleep(remaining)
    runtime.observer.member_finished(run)
    return output
