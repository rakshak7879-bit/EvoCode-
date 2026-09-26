"""Core API: health, repository analysis/status, findings, memory search, walkthrough."""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from api.deps import RepositoryId, ServicesDep, current_agent_result, require_repository
from api.schemas import (
    AnalyzeResponse,
    FindingOut,
    FindingsResponse,
    HealthResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    ReanalyzeRequest,
    RepositoryStatus,
    RepositorySummary,
    WalkthroughResponse,
)
from memory.store import utc_now
from orchestrator.evidence import explainer_citations, reverify_citation, reverify_findings, walkthrough_citations
from orchestrator.router import DEFAULT_TASK
from repo.archive import ArchiveError, copy_working_copy, extract_zip
from repo.github import GitHubError, parse_github_url
from services import Services
from verification.citations import SourceVerifier

VERSION = "0.1.0"
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
router = APIRouter()


def _safe_name(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "", value or "").strip()[:80]
    return cleaned or fallback


async def _save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> int:
    total = 0
    with destination.open("wb") as sink:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"Upload exceeds the {max_bytes // (1024 * 1024)} MB limit.")
            sink.write(chunk)
    return total


@router.get("/health", response_model=HealthResponse)
def health(services: ServicesDep) -> dict[str, Any]:
    settings = services.settings
    return {
        "status": "ok",
        "version": VERSION,
        "llm": services.llm.describe(),
        "fts5": services.db.fts5_enabled,
        "database_recovered": services.db.recovered_from is not None,
        "allow_source_edits": settings.allow_source_edits,
        "demo_repo_available": settings.demo_repo_path.is_dir(),
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_file_kb": settings.max_file_kb,
            "max_files": settings.max_files,
        },
    }


@router.get("/repositories", response_model=list[RepositorySummary])
def list_repositories(services: ServicesDep) -> list[dict[str, Any]]:
    return [
        {"repository_id": r["id"], "name": r["name"], "source": r["source"], "status": r["status"],
         "created_at": r["created_at"], "updated_at": r["updated_at"]}
        for r in services.store.list_repositories(limit=12)
    ]


@router.post("/repository/analyze", response_model=AnalyzeResponse, status_code=202)
async def analyze_repository(
    services: ServicesDep,
    background: BackgroundTasks,
    file: UploadFile | None = File(default=None, description="ZIP archive of the repository"),
    github_url: str | None = Form(default=None, max_length=300),
    use_demo: bool = Form(default=False),
    task: str | None = Form(default=None, max_length=500),
) -> dict[str, str]:
    settings = services.settings
    sources = [bool(file and file.filename), bool(github_url and github_url.strip()), use_demo]
    if sum(sources) != 1:
        raise HTTPException(status_code=400, detail="Provide exactly one of: a ZIP file, a GitHub URL, or use_demo=true.")
    repo_id = uuid.uuid4().hex[:16]
    work_dir = settings.repos_dir / repo_id
    github_ref = None
    intro: list[str] = []

    if file and file.filename:
        if not file.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="Only .zip archives are supported.")
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        archive = settings.uploads_dir / f"{repo_id}.zip"
        try:
            size = await _save_upload(file, archive, settings.max_upload_bytes)
            report = await asyncio.to_thread(extract_zip, archive, work_dir, max_total_bytes=settings.max_extracted_bytes)
        except ArchiveError as exc:
            await asyncio.to_thread(_remove_tree, work_dir)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            archive.unlink(missing_ok=True)
        name = _safe_name(Path(file.filename).stem, "uploaded-repository")
        root, source, source_ref = report.root, "zip", file.filename[:200]
        intro.append(f"Received {file.filename} ({size / 1024:.0f} KB); extracted {report.files} files safely")
        if report.skipped_symlinks:
            intro.append(f"Ignored {report.skipped_symlinks} symlink(s) from the archive")
    elif github_url and github_url.strip():
        try:
            github_ref = parse_github_url(github_url)
        except GitHubError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        work_dir.mkdir(parents=True, exist_ok=True)
        name, root, source, source_ref = github_ref.full_name, work_dir, "github", github_url.strip()[:300]
    else:
        if not settings.demo_repo_path.is_dir():
            raise HTTPException(status_code=404, detail="The bundled demo repository is not available.")
        root = await asyncio.to_thread(copy_working_copy, settings.demo_repo_path, work_dir)
        name, source, source_ref = "demo-repo (ShopLite)", "demo", "demo-repo"
        intro.append("Copied the bundled demo repository into an isolated working copy")

    services.store.create_repository(repo_id=repo_id, name=name, path=str(root), source=source,
                                     source_ref=source_ref, task=(task or DEFAULT_TASK).strip()[:500])
    background.add_task(services.pipeline.run, repo_id, github=github_ref, intro=intro)
    return {"repository_id": repo_id, "status": "processing"}


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


@router.post("/repository/{repository_id}/reanalyze", response_model=AnalyzeResponse, status_code=202)
def reanalyze_repository(repository_id: RepositoryId, services: ServicesDep, background: BackgroundTasks,
                         body: ReanalyzeRequest | None = None) -> dict[str, str]:
    repo = require_repository(services, repository_id)
    if services.pipeline.is_running(repository_id):
        raise HTTPException(status_code=409, detail="An analysis is already running for this repository.")
    if body and body.task:
        services.store.update_repository(repository_id, task=body.task.strip())
    services.store.update_repository(repository_id, status="queued", stage="queued")
    intro = [f"Re-analysis #{repo['analysis_count'] + 1} requested on the current working copy"]
    background.add_task(services.pipeline.run, repository_id, intro=intro)
    return {"repository_id": repository_id, "status": "processing"}


def _status_value(status: str) -> str:
    return status if status in {"queued", "completed", "failed"} else "processing"


@router.get("/repository/{repository_id}", response_model=RepositoryStatus)
def repository_status(repository_id: RepositoryId, services: ServicesDep) -> dict[str, Any]:
    repo = require_repository(services, repository_id)
    runs = services.store.list_agent_runs(repository_id, repo["analysis_count"])
    latest = {run["agent"]: run for run in runs}
    agents = []
    for name, agent in services.brain.agents.items():
        run = latest.get(name)
        agents.append({
            "name": name, "title": agent.title, "description": agent.description,
            "status": run["status"] if run else "pending",
            "mode": run["mode"] if run else None, "summary": run["summary"] if run else None,
            "reason": run["reason"] if run else None, "error": run["error"] if run else None,
            "duration_ms": run["duration_ms"] if run else None,
            "started_at": run["started_at"] if run else None, "completed_at": run["completed_at"] if run else None,
        })
    findings = services.store.list_findings(repository_id)
    memory_entries = sum(services.store.memory_counts(repository_id).values())
    files = len(services.store.list_files(repository_id))
    completed = repo["status"] == "completed"
    statuses = Counter(f["status"] for f in findings)
    metrics = {
        "files_analyzed": files,
        "memory_entries": memory_entries,
        "security_findings": sum(1 for f in findings if f["category"] == "security") if completed else None,
        "duplicate_clusters": sum(1 for f in findings if f["category"] == "duplicate") if completed else None,
        "verified_findings": statuses.get("verified", 0) if completed else None,
        "stale_findings": statuses.get("stale", 0) if completed else None,
        "total_findings": len(findings) if completed else None,
    }
    return {
        "repository_id": repo["id"], "name": repo["name"], "source": repo["source"], "source_ref": repo["source_ref"],
        "status": _status_value(repo["status"]), "stage": repo["stage"],
        "brain": {"state": repo["brain_state"], "stage": repo["stage"], "message": repo["brain_message"]},
        "files": files,
        "agents_completed": sum(1 for a in agents if a["status"] == "complete"),
        "agents_total": sum(1 for a in agents if a["status"] != "skipped"),
        "findings": len(findings) if completed else 0,
        "error": repo["error"], "analysis_count": repo["analysis_count"],
        "created_at": repo["created_at"], "updated_at": repo["updated_at"],
        "metrics": metrics, "agents": agents, "timeline": repo["timeline"], "stats": repo["stats"],
        "report": repo["report"], "llm": services.llm.describe(),
    }


def _finding_out(finding: dict[str, Any]) -> dict[str, Any]:
    extra = finding.get("extra", {})
    return {
        **{k: finding[k] for k in ("id", "agent", "category", "severity", "title", "description", "file", "line",
                                   "line_end", "evidence", "recommendation", "confidence", "source", "rule_id",
                                   "status", "sha256", "file_sha256", "verification", "verified_at")},
        "locations": extra.get("locations", []),
        "similarity": extra.get("similarity"),
        "annotations": extra.get("annotations", []),
        "detectors": extra.get("detectors", []),
        "cwe": extra.get("cwe"),
        "family": extra.get("family"),
    }


def _reverify_explainer(services: Services, repo: dict[str, Any]) -> dict[str, Any]:
    result = current_agent_result(services, repo, "explainer")
    if not result:
        return {}
    data = result.get("data", {})
    verifier = SourceVerifier(Path(repo["path"]))
    for citation in explainer_citations(data):
        if citation.get("verification"):
            reverify_citation(citation, verifier)
    data["mode"] = result.get("mode", "local")
    return data


@router.get("/findings/{repository_id}", response_model=FindingsResponse)
def get_findings(repository_id: RepositoryId, services: ServicesDep, verify: bool = True) -> dict[str, Any]:
    repo = require_repository(services, repository_id)
    root = Path(repo["path"])
    findings = reverify_findings(services.store, repository_id, root) if verify else services.store.list_findings(repository_id)
    out = [_finding_out(f) for f in findings]
    out.sort(key=lambda f: (SEVERITY_RANK.get(f["severity"], 9), f["file"], f["line"]))
    statuses = Counter(f["status"] for f in out)
    report = repo.get("report") or {}
    return {
        "repository_id": repository_id,
        "mode": (report.get("llm") or services.llm.describe()).get("mode", "local"),
        "llm": services.llm.describe(),
        "security": [f for f in out if f["category"] == "security"],
        "duplicates": [f for f in out if f["category"] == "duplicate"],
        "architecture": _reverify_explainer(services, repo),
        "verification": {
            "total": len(out),
            "verified": statuses.get("verified", 0),
            "stale": statuses.get("stale", 0),
            "missing": statuses.get("missing", 0),
            "other": len(out) - statuses.get("verified", 0) - statuses.get("stale", 0) - statuses.get("missing", 0),
            "checked_at": utc_now(),
            "live": verify,
        },
        "cross_validation": report.get("cross_validation", {}),
        "history": report.get("history", {}),
    }


@router.post("/memory/search", response_model=MemorySearchResponse)
async def memory_search(body: MemorySearchRequest, services: ServicesDep) -> dict[str, Any]:
    require_repository(services, body.repository_id)
    return await services.brain.answer(body.repository_id, body.query.strip(), body.limit)


@router.get("/walkthrough/{repository_id}", response_model=WalkthroughResponse)
def get_walkthrough(repository_id: RepositoryId, services: ServicesDep) -> dict[str, Any]:
    repo = require_repository(services, repository_id)
    result = current_agent_result(services, repo, "walkthrough")
    if not result:
        raise HTTPException(status_code=404, detail="Walkthrough not available: the Walkthrough Agent did not complete.")
    data = result.get("data", {})
    verifier = SourceVerifier(Path(repo["path"]))
    citations = walkthrough_citations(data)
    verified = sum(1 for c in citations if reverify_citation(c, verifier))
    return {
        "repository_id": repository_id,
        "mode": result.get("mode", "local"),
        "question": data.get("question", ""),
        "total_duration": data.get("total_duration", 0),
        "target_seconds": data.get("target_seconds", 120),
        "steps": data.get("steps", []),
        "verification": {"verified": verified, "total": len(citations)},
    }
