"""Repository preparation and live pipeline watching for the CLI."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import Settings
from evo_cli.console import Console
from evo_cli.orchestration import format_event
from orchestrator.router import DEFAULT_TASK
from repo.archive import copy_working_copy, extract_zip
from repo.github import GitHubRepoRef, parse_github_url
from services import Services

CANCELLED = "Analysis cancelled from the terminal. Re-run it with `reanalyze`."


@dataclass(frozen=True)
class PreparedRepository:
    repository_id: str
    github: GitHubRepoRef | None
    intro: tuple[str, ...]


def _safe_name(value: str, fallback: str = "repository") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ /-]", "", value).strip()[:80]
    return cleaned or fallback


def prepare_repository(services: Services, source: str, task: str | None = None) -> PreparedRepository:
    """Prepare an isolated working copy and create its repository record.

    ``source`` is ``demo``, a local directory, a ZIP path or a GitHub URL.
    GitHub download/extraction is deferred to the analysis pipeline.
    """
    settings = services.settings
    repo_id = uuid.uuid4().hex[:16]
    work_dir = settings.repos_dir / repo_id
    source_value = source.strip()
    github: GitHubRepoRef | None = None
    intro: list[str] = []
    try:
        if source_value.lower() == "demo":
            if not settings.demo_repo_path.is_dir():
                raise ValueError(f"Demo repository not found: {settings.demo_repo_path}")
            root = copy_working_copy(settings.demo_repo_path, work_dir)
            name, kind, source_ref = "demo-repo (ShopLite)", "demo", "demo-repo"
            intro.append("Copied the bundled demo repository into an isolated working copy")
        elif source_value.startswith(("https://github.com/", "http://github.com/", "https://www.github.com/")):
            github = parse_github_url(source_value)
            work_dir.mkdir(parents=True, exist_ok=True)
            root, name, kind, source_ref = work_dir, github.full_name, "github", source_value
        else:
            path = Path(source_value).expanduser().resolve()
            if not path.exists():
                raise ValueError(f"Repository source does not exist: {path}")
            if path.is_dir():
                root = copy_working_copy(path, work_dir)
                name, kind, source_ref = _safe_name(path.name), "folder", str(path)
                intro.append(f"Copied {path} into an isolated working copy")
            elif path.is_file() and path.suffix.lower() == ".zip":
                if path.stat().st_size > settings.max_upload_bytes:
                    raise ValueError(f"ZIP exceeds the {settings.max_upload_mb} MB upload limit.")
                report = extract_zip(path, work_dir, max_total_bytes=settings.max_extracted_bytes)
                root, name, kind, source_ref = report.root, _safe_name(path.stem), "zip", str(path)
                intro.append(
                    f"Read {path.name} ({path.stat().st_size / 1024:.1f} KB); extracted {report.files} files safely"
                )
                if report.skipped_symlinks:
                    intro.append(f"Ignored {report.skipped_symlinks} symlink(s) from the archive")
            else:
                raise ValueError("Source must be a repository folder, a .zip archive, a GitHub URL, or 'demo'.")
        services.store.create_repository(
            repo_id=repo_id,
            name=name,
            path=str(root),
            source=kind,
            source_ref=source_ref,
            task=(task or DEFAULT_TASK).strip()[:500],
        )
        return PreparedRepository(repo_id, github, tuple(intro))
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


async def run_analysis(
    services: Services,
    prepared: PreparedRepository,
    console: Console,
    *,
    json_output: bool = False,
) -> dict[str, Any]:
    """Run the pipeline in-process and stream new timeline events (all three levels) as they happen."""
    existing = services.store.get_repository(prepared.repository_id)
    baseline = existing["analysis_count"] if existing else 0
    task = asyncio.create_task(
        services.pipeline.run(
            prepared.repository_id,
            github=prepared.github,
            intro=list(prepared.intro),
        )
    )
    seen = 0
    try:
        while not task.done():
            repo = services.store.get_repository(prepared.repository_id)
            # Only stream once this run has started (a re-analysis still shows the previous timeline).
            if repo and not json_output and repo["analysis_count"] > baseline:
                seen = _print_new_events(console, repo.get("timeline", []), seen)
            await asyncio.sleep(0.08)
        await task
    except (asyncio.CancelledError, KeyboardInterrupt):
        task.cancel()
        services.store.update_repository(prepared.repository_id, status="failed", stage="failed",
                                         brain_state="error", brain_message=CANCELLED, error=CANCELLED)
        raise
    repo = services.store.get_repository(prepared.repository_id)
    if repo is None:
        raise RuntimeError("Repository record disappeared during analysis.")
    if not json_output and repo["analysis_count"] > baseline:
        _print_new_events(console, repo.get("timeline", []), seen)
    return repo


async def rerun_analysis(services: Services, repo_id: str, console: Console, *, task: str | None = None,
                         json_output: bool = False) -> dict[str, Any]:
    repo = services.store.get_repository(repo_id)
    if repo is None:
        raise KeyError(repo_id)
    if services.pipeline.is_running(repo_id):
        raise RuntimeError("An analysis is already running for this repository.")
    if task:
        services.store.update_repository(repo_id, task=task.strip()[:500])
    prepared = PreparedRepository(
        repo_id,
        None,
        (f"Re-analysis #{repo['analysis_count'] + 1} requested on the current working copy",),
    )
    return await run_analysis(services, prepared, console, json_output=json_output)


def _print_new_events(console: Console, events: list[dict[str, Any]], seen: int) -> int:
    for event in events[seen:]:
        console.write(format_event(console, event, truncate=console.is_tty))
    return len(events)


def settings_for_cli(settings: Settings, data_dir: str | None) -> Settings:
    if not data_dir:
        return settings
    return settings.with_overrides(data_dir=Path(data_dir).expanduser().resolve())


def repository_as_json(repo: dict[str, Any], services: Services) -> str:
    files = services.store.list_files(repo["id"])
    findings = services.store.list_findings(repo["id"])
    payload = {
        "repository_id": repo["id"],
        "name": repo["name"],
        "source": repo["source"],
        "status": repo["status"],
        "stage": repo["stage"],
        "error": repo.get("error"),
        "analysis_count": repo["analysis_count"],
        "files": len(files),
        "findings": len(findings),
        "stats": repo["stats"],
        "report": repo["report"],
    }
    return json.dumps(payload, indent=2)
