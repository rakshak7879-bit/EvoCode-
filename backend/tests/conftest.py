from __future__ import annotations

import shutil
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agents.context import AgentContext
from app_factory import create_app
from config import PROJECT_ROOT, Settings
from llm.provider import LLMProvider, MockProvider
from memory.database import Database
from memory.indexer import MemoryIndexer
from memory.search import MemorySearch
from memory.store import MemoryStore
from orchestrator.context_builder import ContextBuilder
from repo.scanner import RepositoryScanner
from services import build_services

DEMO_REPO = PROJECT_ROOT / "demo-repo"


class FakeLLM(LLMProvider):
    """Deterministic stand-in for an LLM, used to test Brain-side validation."""

    name = "fake"
    model = "fake-model"

    def __init__(self, responder: Callable[[str, str], dict[str, Any]]) -> None:
        self.responder = responder
        self.calls: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        return True

    async def complete_json(self, *, system: str, prompt: str, max_tokens: int = 1600) -> dict[str, Any]:
        self.calls.append((system, prompt))
        return self.responder(system, prompt)


def line_of(path: Path, needle: str) -> int:
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not found in {path}")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", demo_repo_path=DEMO_REPO, llm_provider="mock", pacing_ms=0)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def client_factory(settings: Settings) -> Callable[..., TestClient]:
    def make(llm: LLMProvider | None = None, **overrides: Any) -> TestClient:
        custom = settings.with_overrides(**overrides) if overrides else settings
        services = build_services(custom, llm=llm)
        return TestClient(create_app(custom, services_override=services))

    return make


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    shutil.copytree(DEMO_REPO, target)
    return target


@pytest.fixture
def demo_zip(tmp_path: Path) -> Path:
    archive = tmp_path / "shoplite.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in DEMO_REPO.rglob("*"):
            if path.is_file():
                zf.write(path, f"shoplite-main/{path.relative_to(DEMO_REPO).as_posix()}")
    return archive


@pytest.fixture
def indexed(demo_copy: Path, tmp_path: Path) -> tuple[MemoryStore, MemorySearch, str, Path]:
    db = Database(tmp_path / "memory.db")
    db.initialize()
    store = MemoryStore(db)
    store.create_repository(repo_id="abcdef123456", name="demo", path=str(demo_copy), source="demo",
                            source_ref=None, task="test")
    scan = RepositoryScanner().scan(demo_copy)
    MemoryIndexer(store).index("abcdef123456", demo_copy, scan)
    return store, MemorySearch(db), "abcdef123456", demo_copy


@pytest.fixture
def agent_context(indexed: tuple[MemoryStore, MemorySearch, str, Path]) -> AgentContext:
    store, search, repo_id, root = indexed
    context, _ = ContextBuilder(store, search, MockProvider()).build(repo_id, "demo", root, "full analysis")
    return context


def analyze_demo(client: TestClient, **data: str) -> str:
    response = client.post("/api/repository/analyze", data={"use_demo": "true", **data})
    assert response.status_code == 202, response.text
    return response.json()["repository_id"]
