"""Source viewer API: read indexed files and simulate a code change.

The path-safe read/edit implementation lives in ``repo.source_service`` and is
shared with the command-line interface. Edits affect Evo Code's isolated
working copy only. Disable them with ``EVO_ALLOW_SOURCE_EDITS=false``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.deps import RepositoryId, ServicesDep, require_repository
from api.schemas import SourceEditRequest, SourceEditResponse, SourceFileResponse
from repo.source_service import SourceAccessError, edit_indexed_source, read_indexed_source

MAX_VIEW_BYTES = 2 * 1024 * 1024
router = APIRouter()


def _http_error(exc: SourceAccessError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/source/{repository_id}", response_model=SourceFileResponse)
def get_source(
    repository_id: RepositoryId,
    services: ServicesDep,
    path: str = Query(min_length=1, max_length=500),
) -> dict[str, Any]:
    repo = require_repository(services, repository_id)
    try:
        source = read_indexed_source(
            services.store,
            repository_id,
            Path(repo["path"]),
            path,
            max_bytes=MAX_VIEW_BYTES,
        )
    except SourceAccessError as exc:
        raise _http_error(exc) from exc
    return {
        "repository_id": repository_id,
        "path": source.record.path,
        "language": source.record.language,
        "content": source.content,
        "line_count": len(source.lines),
        "size": len(source.data),
        "sha256_current": source.sha256_current,
        "sha256_indexed": source.record.sha256,
        "changed": source.changed,
        "editable": services.settings.allow_source_edits,
    }


@router.post("/source/{repository_id}/edit", response_model=SourceEditResponse)
def edit_source(
    repository_id: RepositoryId,
    body: SourceEditRequest,
    services: ServicesDep,
) -> dict[str, Any]:
    if not services.settings.allow_source_edits:
        raise HTTPException(status_code=403, detail="Source edits are disabled (EVO_ALLOW_SOURCE_EDITS=false).")
    repo = require_repository(services, repository_id)
    if services.pipeline.is_running(repository_id):
        raise HTTPException(status_code=409, detail="Wait for the running analysis to finish.")
    try:
        result = edit_indexed_source(
            services.store,
            repository_id,
            Path(repo["path"]),
            body.path,
            body.line,
            body.content,
        )
    except SourceAccessError as exc:
        raise _http_error(exc) from exc
    return {
        "path": result.path,
        "line": result.line,
        "previous_content": result.previous_content,
        "sha256_before": result.sha256_before,
        "sha256_after": result.sha256_after,
    }
