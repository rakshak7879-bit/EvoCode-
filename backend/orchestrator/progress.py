"""Progress reporting: Brain state, stage, timeline and agent status in SQLite.

The dashboard polls these values. ``pacing_ms`` optionally keeps each stage
visible for a minimum time so orchestration is observable during demos; it
never changes results (set EVO_PACING_MS=0 to disable).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

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
        self._started = time.monotonic()
        self._agent_runs: dict[str, int] = {}
        self._agent_started: dict[str, float] = {}

    async def pace(self, factor: float = 1.0) -> None:
        if self.pacing_ms > 0:
            await asyncio.sleep(self.pacing_ms * factor / 1000)

    def log(self, message: str, *, level: str = "info", stage: str | None = None, agent: str | None = None) -> None:
        entry: dict[str, Any] = {
            "ts": utc_now(),
            "elapsed_ms": int((time.monotonic() - self._started) * 1000),
            "stage": stage or self.stage_name,
            "level": level,
            "message": message,
        }
        if agent:
            entry["agent"] = agent
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

    # ------------------------------------------------------------------ agents
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
        self.log(f"{agent.capitalize()} Agent started", agent=agent)

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
        text = f"{agent.capitalize()} Agent {'completed' if status == 'complete' else 'failed'}: {detail}"
        self.log(text, level=level, agent=agent)
        return duration

    def update_agent_result(self, agent: str, result: dict[str, Any]) -> None:
        """Persist Brain-side enrichment (e.g. citation verification) of an agent's output."""
        run_id = self._agent_runs.get(agent)
        if run_id is not None:
            self.store.update_agent_run(run_id, result=result)
