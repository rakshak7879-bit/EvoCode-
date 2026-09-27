"""Service container: builds and wires every backend component once."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from agents.duplicate import DuplicateAgent
from agents.explainer import ExplainerAgent
from agents.security import SecurityAgent
from agents.walkthrough import WalkthroughAgent
from config import Settings
from llm.provider import LLMProvider, create_provider
from memory.database import Database
from memory.indexer import MemoryIndexer
from memory.search import MemorySearch
from memory.store import MemoryStore
from orchestrator.brain import Brain
from orchestrator.fixing import FixCoordinator
from orchestrator.pipeline import AnalysisPipeline
from orchestrator.solving import SolveCoordinator
from repo.scanner import RepositoryScanner

logger = logging.getLogger("evo.services")


@dataclass
class Services:
    settings: Settings
    db: Database
    store: MemoryStore
    search: MemorySearch
    llm: LLMProvider
    brain: Brain
    pipeline: AnalysisPipeline
    fixes: FixCoordinator
    solve: SolveCoordinator


def build_services(settings: Settings, llm: LLMProvider | None = None, *,
                   recover_after_seconds: float | None = 0) -> Services:
    """Wire every component once.

    ``recover_after_seconds`` controls how queued/processing analyses left by a
    previous process are closed: ``0`` (the API server) marks all of them failed,
    a positive value (the CLI) only those idle for longer than that, so running
    ``./evo`` in a second terminal never interrupts an analysis in progress.
    ``None`` skips recovery.
    """
    for directory in (settings.data_dir, settings.repos_dir, settings.uploads_dir):
        directory.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_path)
    db.initialize()
    store = MemoryStore(db)
    search = MemorySearch(db)
    provider = llm or create_provider(settings)
    agents = [SecurityAgent(), DuplicateAgent(), ExplainerAgent(), WalkthroughAgent()]
    brain = Brain(store, search, provider, agents, settings)
    scanner = RepositoryScanner(settings.max_file_bytes, settings.max_files)
    indexer = MemoryIndexer(store)
    pipeline = AnalysisPipeline(settings, store, scanner, indexer, brain)
    if recover_after_seconds is not None:
        recover_interrupted(store, idle_seconds=recover_after_seconds)
    logger.info(
        "Evo Code services ready",
        extra={"llm": provider.describe()["label"], "fts5": db.fts5_enabled, "data_dir": str(settings.data_dir)},
    )
    return Services(settings, db, store, search, provider, brain, pipeline,
                    FixCoordinator(store, brain, settings),
                    SolveCoordinator(settings, store, brain, scanner, indexer))


def recover_interrupted(store: MemoryStore, *, idle_seconds: float = 0) -> int:
    """Analyses cannot survive their process; mark abandoned ones failed so they can be re-run.

    With ``idle_seconds > 0`` only analyses whose record has not been updated for
    that long are closed (progress updates the record several times a second).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=idle_seconds)
    recovered = 0
    for repo in store.list_repositories(limit=500):
        if repo["status"] not in {"queued", "processing"}:
            continue
        if idle_seconds > 0:
            try:
                updated = datetime.fromisoformat(repo["updated_at"])
            except (TypeError, ValueError):
                updated = None
            if updated is not None and updated > cutoff:
                continue  # still active in another process
        message = "Analysis was interrupted before it finished (process stopped). Re-run the analysis."
        store.update_repository(repo["id"], status="failed", stage="failed", brain_state="error",
                                brain_message=message, error=message)
        recovered += 1
    return recovered
