"""Source viewer API: read indexed files and (optionally) simulate a code change.

Only files that were indexed can be read, always through safe path resolution
inside the repository's working copy. Edits apply to Evo Code's isolated
working copy (never the user's original files) and exist to demonstrate stale
citation detection. Disable with EVO_ALLOW_SOURCE_EDITS=false.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.deps import RepositoryId, ServicesDep, require_repository
from api.schemas import SourceEditRequest, SourceEditResponse, SourceFileResponse
from repo.paths import UnsafePathError, normalize_relpath, resolve_in_root
from repo.text import decode_source, sha256_bytes, split_lines

MAX_VIEW_BYTES = 2 * 1024 * 1024
router = APIRouter()


def _resolve_indexed(services: Any, repo: dict[str, Any], path: str) -> tuple[Any, Path]:
    try:
        relpath = normalize_relpath(path)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    record = services.store.get_file(repo["id"], relpath)
    if record is None:
        raise HTTPException(status_code=404, detail="File is not part of the indexed repository.")
    try:
        resolved = resolve_in_root(Path(repo["path"]), record.path)
    except (UnsafePathError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Unsafe path.") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists in the working copy.")
    return record, resolved


@router.get("/source/{repository_id}", response_model=SourceFileResponse)
def get_source(repository_id: RepositoryId, services: ServicesDep,
               path: str = Query(min_length=1, max_length=500)) -> dict[str, Any]:
    repo = require_repository(services, repository_id)
    record, resolved = _resolve_indexed(services, repo, path)
    data = resolved.read_bytes()
    if len(data) > MAX_VIEW_BYTES:
        raise HTTPException(status_code=413, detail="File is too large to display.")
    lines = split_lines(decode_source(data))
    current = sha256_bytes(data)
    return {
        "repository_id": repository_id,
        "path": record.path,
        "language": record.language,
        "content": "\n".join(lines),
        "line_count": len(lines),
        "size": len(data),
        "sha256_current": current,
        "sha256_indexed": record.sha256,
        "changed": current != record.sha256,
        "editable": services.settings.allow_source_edits,
    }


@router.post("/source/{repository_id}/edit", response_model=SourceEditResponse)
def edit_source(repository_id: RepositoryId, body: SourceEditRequest, services: ServicesDep) -> dict[str, Any]:
    if not services.settings.allow_source_edits:
        raise HTTPException(status_code=403, detail="Source edits are disabled (EVO_ALLOW_SOURCE_EDITS=false).")
    repo = require_repository(services, repository_id)
    if services.pipeline.is_running(repository_id):
        raise HTTPException(status_code=409, detail="Wait for the running analysis to finish.")
    if "\n" in body.content or "\r" in body.content:
        raise HTTPException(status_code=400, detail="Replacement must be a single line.")
    record, resolved = _resolve_indexed(services, repo, body.path)
    data = resolved.read_bytes()
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = (data[3:] if bom else data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Only UTF-8 files can be edited.") from exc
    lines = split_lines(text)
    if body.line > len(lines):
        raise HTTPException(status_code=400, detail=f"Line {body.line} is outside the file ({len(lines)} lines).")
    newline = "\r\n" if "\r\n" in text else "\n"
    previous = lines[body.line - 1]
    lines[body.line - 1] = body.content
    updated = newline.join(lines) + (newline if text.endswith(("\n", "\r")) else "")
    payload = (b"\xef\xbb\xbf" if bom else b"") + updated.encode("utf-8")
    resolved.write_bytes(payload)
    return {
        "path": record.path,
        "line": body.line,
        "previous_content": previous,
        "sha256_before": sha256_bytes(data),
        "sha256_after": sha256_bytes(payload),
    }
