"""Service container: builds and wires every backend component once."""

from __future__ import annotations

import logging
from dataclasses import dataclass

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
from orchestrator.pipeline import AnalysisPipeline
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


def build_services(settings: Settings, llm: LLMProvider | None = None) -> Services:
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
    pipeline = AnalysisPipeline(settings, store, scanner, MemoryIndexer(store), brain)
    _recover_interrupted(store)
    logger.info(
        "Evo Code services ready",
        extra={"llm": provider.describe()["label"], "fts5": db.fts5_enabled, "data_dir": str(settings.data_dir)},
    )
    return Services(settings, db, store, search, provider, brain, pipeline)


def _recover_interrupted(store: MemoryStore) -> None:
    """Analyses cannot survive a restart; mark them failed so the UI can offer a re-run."""
    for repo in store.list_repositories(limit=500):
        if repo["status"] in {"queued", "processing"}:
            message = "Analysis was interrupted by a server restart. Re-run the analysis."
            store.update_repository(repo["id"], status="failed", stage="failed", brain_state="error",
                                    brain_message=message, error=message)
