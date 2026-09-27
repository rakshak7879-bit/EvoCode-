"""Progress reporting: Brain state, stage, timeline and agent status in SQLite.

The CLI (and the optional dashboard) read these values while an analysis runs.
Timeline entries carry the orchestration ``depth``: 0 for the Brain, 1 for lead
agents and 2 for specialist sub-agents (with their ``parent`` lead). The
reporter is also the level-2 ``TeamObserver``: every sub-agent gets its own
``agent_runs`` row named ``<lead>.<member>`` (for example ``security.secrets``).

``pacing_ms`` optionally keeps each stage visible for a minimum time so
orchestration is observable during demos; it never changes results (set
EVO_PACING_MS=0 to disable).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

from agents.base import SubAgentRun
from memory.store import MemoryStore, utc_now

logger = logging.getLogger("evo.brain")

# Brain states shown in the UI.
BRAIN_STATES = ("idle", "thinking", "retrieving", "routing", "running", "verifying", "complete", "error")


class ProgressReporter:
    def __init__(self, store: MemoryStore, repo_id: str, analysis_no: int, pacing_ms: int) -> None:
        self.store = store
        self.repo_id = repo_id
        self.analysis_no = analysis_no
        self.pacing_ms = pacing_ms
        self.stage_name = "queued"
        self.titles: dict[str, str] = {}
        self._started = time.monotonic()
        self._agent_runs: dict[str, int] = {}
        self._agent_started: dict[str, float] = {}
        self._members: dict[str, SubAgentRun] = {}

    async def pace(self, factor: float = 1.0) -> None:
        if self.pacing_ms > 0:
            await asyncio.sleep(self.pacing_ms * factor / 1000)

    def title(self, agent: str) -> str:
        return self.titles.get(agent) or f"{agent.capitalize()} Agent"

    def log(self, message: str, *, level: str = "info", stage: str | None = None, agent: str | None = None,
            depth: int = 0, parent: str | None = None) -> None:
        entry: dict[str, Any] = {
            "ts": utc_now(),
            "elapsed_ms": int((time.monotonic() - self._started) * 1000),
            "stage": stage or self.stage_name,
            "level": level,
            "message": message,
        }
        if agent:
            entry["agent"] = agent
        if depth:
            entry["depth"] = depth
        if parent:
            entry["parent"] = parent
        self.store.append_timeline(self.repo_id, entry)
        logger.info(message, extra={"repository_id": self.repo_id, "stage": entry["stage"], "agent": agent or "-"})

    async def stage(self, stage: str, brain_state: str, message: str, *, pace: bool = True) -> None:
        self.stage_name = stage
        self.store.update_repository(
            self.repo_id, stage=stage, brain_state=brain_state, brain_message=message, status="processing"
        )
        self.log(message, stage=stage)
        if pace:
            await self.pace()

    # ------------------------------------------------------------------ lead agents (level 1)
    def agent_queued(self, agent: str, reason: str) -> None:
        self._agent_runs[agent] = self.store.create_agent_run(self.repo_id, self.analysis_no, agent, "queued", reason)

    def agent_skipped(self, agent: str, reason: str) -> None:
        run_id = self.store.create_agent_run(self.repo_id, self.analysis_no, agent, "skipped", reason)
        self.store.update_agent_run(run_id, summary=reason)

    def agent_running(self, agent: str) -> None:
        self._agent_started[agent] = time.monotonic()
        run_id = self._agent_runs.get(agent)
        if run_id is None:
            run_id = self._agent_runs[agent] = self.store.create_agent_run(
                self.repo_id, self.analysis_no, agent, "running"
            )
        self.store.update_agent_run(run_id, status="running", started_at=utc_now())
        self.log(f"{self.title(agent)} started", agent=agent, depth=1)

    def agent_finished(self, agent: str, *, status: str, mode: str, summary: str, result: dict[str, Any],
                       error: str | None = None) -> int:
        started = self._agent_started.get(agent, time.monotonic())
        duration = int((time.monotonic() - started) * 1000)
        run_id = self._agent_runs.get(agent)
        if run_id is not None:
            self.store.update_agent_run(
                run_id, status=status, mode=mode, summary=summary, error=error, completed_at=utc_now(),
                duration_ms=duration, result=result,
            )
        level = "success" if status == "complete" else "error"
        detail = summary if status == "complete" else (error or summary)
        text = f"{self.title(agent)} {'completed' if status == 'complete' else 'failed'}: {detail}"
        self.log(text, level=level, agent=agent, depth=1)
        return duration

    def update_agent_result(self, agent: str, result: dict[str, Any]) -> None:
        """Persist Brain-side enrichment (e.g. citation verification) of an agent's output."""
        run_id = self._agent_runs.get(agent)
        if run_id is not None:
            self.store.update_agent_run(run_id, result=result)

    # ------------------------------------------------------------------ specialist sub-agents (level 2)
    def team_planned(self, parent: str, runs: Sequence[SubAgentRun]) -> None:
        active = [run for run in runs if run.status != "skipped"]
        waves = max((run.wave for run in active), default=0)
        for run in runs:
            self._members[run.name] = run
            self._agent_runs[run.name] = self.store.create_agent_run(
                self.repo_id, self.analysis_no, run.name, run.status, run.reason, result=run.model_dump(mode="json")
            )
        names = ", ".join(run.title for run in active) or "none"
        self.log(
            f"{self.title(parent)} delegates to {len(active)} specialist sub-agent{'s' if len(active) != 1 else ''} "
            f"in {waves} wave{'s' if waves != 1 else ''}: {names}",
            agent=parent, depth=1,
        )
        for run in runs:
            if run.status == "skipped":
                self.log(f"{run.title} skipped: {run.reason}", agent=run.name, parent=parent, depth=2)

    def member_started(self, run: SubAgentRun) -> None:
        run_id = self._agent_runs.get(run.name)
        if run_id is not None:
            self.store.update_agent_run(run_id, status="running", started_at=utc_now())

    def member_finished(self, run: SubAgentRun) -> None:
        self._persist_member(run)
        if run.status == "complete":
            self.log(f"{run.title}: {run.summary}", level="success", agent=run.name, parent=run.parent, depth=2)
        elif run.status == "failed":
            self.log(f"{run.title} failed: {run.error}", level="error", agent=run.name, parent=run.parent, depth=2)
        else:
            self.log(f"{run.title} skipped: {run.reason}", level="warning", agent=run.name, parent=run.parent,
                     depth=2)

    def abandon_team(self, parent: str, reason: str) -> None:
        """Close members left queued/running when their lead failed or timed out."""
        for run in self.members(parent):
            if run.status == "running":
                run.status, run.error = "failed", reason
            elif run.status == "queued":
                run.status, run.reason = "skipped", reason
            else:
                continue
            self._persist_member(run)

    def members(self, parent: str) -> list[SubAgentRun]:
        return [run for run in self._members.values() if run.parent == parent]

    def _persist_member(self, run: SubAgentRun) -> None:
        run_id = self._agent_runs.get(run.name)
        if run_id is None:
            return
        self.store.update_agent_run(
            run_id, status=run.status, mode=run.mode, summary=run.summary or run.reason, reason=run.reason,
            error=run.error, completed_at=utc_now(), duration_ms=run.duration_ms, result=run.model_dump(mode="json"),
        )
