"""End-to-end analysis pipeline: (download) -> scan -> index -> Brain.

Runs in the background after the API responds. Any failure is recorded on the
repository (status "failed" + message) instead of crashing the server.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from config import Settings
from memory.indexer import MemoryIndexer
from memory.store import MemoryStore
from orchestrator.brain import Brain
from orchestrator.progress import ProgressReporter
from repo.archive import ArchiveError, extract_zip
from repo.github import GitHubError, GitHubRepoRef, download_repository_zip
from repo.scanner import RepositoryScanner

logger = logging.getLogger("evo.pipeline")


class AnalysisError(RuntimeError):
    """Expected, user-facing analysis failure."""


class AnalysisPipeline:
    def __init__(self, settings: Settings, store: MemoryStore, scanner: RepositoryScanner, indexer: MemoryIndexer,
                 brain: Brain) -> None:
        self.settings = settings
        self.store = store
        self.scanner = scanner
        self.indexer = indexer
        self.brain = brain
        self._active: set[str] = set()

    def is_running(self, repo_id: str) -> bool:
        return repo_id in self._active

    async def run(self, repo_id: str, *, github: GitHubRepoRef | None = None, intro: list[str] | None = None) -> None:
        if repo_id in self._active:
            return
        self._active.add(repo_id)
        analysis_no = self.store.begin_analysis(repo_id)
        progress = ProgressReporter(self.store, repo_id, analysis_no, self.settings.pacing_ms)
        try:
            for message in intro or []:
                progress.log(message, stage="queued")
            if github is not None:
                await self._download(repo_id, github, progress)
            repo = self.store.get_repository(repo_id)
            if repo is None:
                raise AnalysisError("Repository record disappeared.")
            root = Path(repo["path"])
            if not root.is_dir():
                raise AnalysisError("The repository working copy is missing. Upload it again.")

            await progress.stage("scanning", "idle", "Repository scanner discovering files")
            scan = await asyncio.to_thread(self.scanner.scan, root)
            if not scan.files:
                raise AnalysisError("No supported source files were found in this repository.")
            stats = scan.to_stats()
            languages = ", ".join(f"{lang} {count}" for lang, count in list(scan.languages.items())[:5])
            skipped = sum(scan.skipped.values())
            progress.log(f"Discovered {len(scan.files)} files ({languages}); skipped {skipped} ignored/unsupported item(s)")
            if scan.sensitive_files:
                progress.log(
                    f"{len(scan.sensitive_files)} sensitive file(s) skipped without reading: "
                    f"{', '.join(scan.sensitive_files[:3])}",
                    level="warning",
                )
            special = stats["special"]
            detected = [f"{key}: {', '.join(v[:2])}" for key, v in special.items() if v and key != "configs"]  # type: ignore[union-attr]
            if detected:
                progress.log("Identified " + " · ".join(detected))
            progress.log(f"SHA-256 computed for {len(scan.files)} files", level="success")
            self.store.update_repository(repo_id, stats=stats)

            await progress.stage("indexing", "idle", "Memory engine indexing passages into SQLite FTS5")
            index_stats, _ = await asyncio.to_thread(self.indexer.index, repo_id, root, scan)
            stats["memory"] = index_stats.to_dict()
            stats["fts5"] = self.store.db.fts5_enabled
            self.store.update_repository(repo_id, stats=stats)
            by_type = ", ".join(f"{count} {kind}" for kind, count in index_stats.by_type.most_common())
            progress.log(
                f"Indexed {index_stats.memories} memory passages ({by_type}) · {index_stats.symbols} symbols",
                level="success",
            )

            report = await self.brain.analyze(repo_id, progress)
            self.store.update_repository(repo_id, status="completed", stage="completed", brain_state="complete",
                                         brain_message="Verified intelligence ready", report=report, error=None)
            progress.log("Analysis complete: verified intelligence ready", level="success", stage="completed")
        except Exception as exc:
            expected = isinstance(exc, (AnalysisError, ArchiveError, GitHubError))
            message = str(exc) if expected else f"Unexpected error during analysis ({exc.__class__.__name__})."
            if expected:
                logger.warning("Analysis failed", extra={"repository_id": repo_id, "error": message})
            else:
                logger.exception("Analysis crashed", extra={"repository_id": repo_id})
            self.store.update_repository(repo_id, status="failed", stage="failed", brain_state="error",
                                         brain_message=message, error=message)
            progress.log(message, level="error", stage="failed")
        finally:
            self._active.discard(repo_id)

    async def _download(self, repo_id: str, ref: GitHubRepoRef, progress: ProgressReporter) -> None:
        await progress.stage("downloading", "idle", f"Downloading {ref.full_name} from GitHub (ZIP, no code execution)")
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        archive = self.settings.uploads_dir / f"{repo_id}.zip"
        try:
            size = await download_repository_zip(ref, archive, max_bytes=self.settings.max_upload_bytes,
                                                 token=self.settings.github_token)
            progress.log(f"Downloaded {size / 1024:.1f} KB")
            await progress.stage("extracting", "idle", "Extracting archive with zip-slip and size protections")
            report = await asyncio.to_thread(extract_zip, archive, self.settings.repos_dir / repo_id,
                                             max_total_bytes=self.settings.max_extracted_bytes)
        finally:
            archive.unlink(missing_ok=True)
        self.store.update_repository(repo_id, path=str(report.root))
        progress.log(f"Extracted {report.files} files into an isolated working copy")
