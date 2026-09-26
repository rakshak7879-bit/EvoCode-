"""Shared API dependencies and helpers."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Path, Request

from api.schemas import REPOSITORY_ID_PATTERN
from services import Services


def get_services(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


ServicesDep = Annotated[Services, Depends(get_services)]
RepositoryId = Annotated[str, Path(pattern=REPOSITORY_ID_PATTERN, description="Repository id")]


def require_repository(services: Services, repository_id: str) -> dict[str, Any]:
    repo = services.store.get_repository(repository_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    return repo


def current_agent_result(services: Services, repo: dict[str, Any], agent: str) -> dict[str, Any] | None:
    """Result of ``agent`` from the repository's most recent analysis (only if it completed)."""
    runs = services.store.list_agent_runs(repo["id"], repo["analysis_count"])
    run = next((r for r in reversed(runs) if r["agent"] == agent), None)
    if run is None or run["status"] != "complete":
        return None
    return run["result"]  # type: ignore[no-any-return]
