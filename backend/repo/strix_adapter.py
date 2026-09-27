"""Optional adapter for Strix, an external AI penetration-testing tool.

Strix (https://github.com/usestrix/strix, Apache-2.0) runs a target application
in a Docker sandbox and validates vulnerabilities with real exploits. It covers
ground Evo Code deliberately does not: Evo Code never executes repository code,
so it reasons statically about source, while Strix attacks a running target.

The integration is strictly optional and additive:

- Strix is never required. When the binary, Docker or an LLM key is missing,
  ``availability()`` explains what is absent and Evo Code carries on unchanged.
- Evo Code builds the command itself and always runs Strix non-interactively, so
  no repository content can influence the arguments.
- Strix results are read from its own run directory and mapped into Evo Code's
  finding shape, then verified against the source like any other claim.

Strix exit codes: ``0`` clean, ``1`` fatal error, ``2`` vulnerabilities found.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("evo.strix")

SCAN_MODES = ("quick", "standard", "deep")
EXIT_CLEAN, EXIT_ERROR, EXIT_VULNERABILITIES = 0, 1, 2
SEVERITY_MAP = {"critical": "critical", "high": "high", "medium": "medium", "moderate": "medium", "low": "low",
                "info": "info", "informational": "info"}
RUN_FILES = ("findings.json", "results.json", "report.json", "vulnerabilities.json")


@dataclass(frozen=True)
class Availability:
    """Whether a Strix scan can run right now, and what is missing if not."""

    available: bool
    binary: str | None = None
    docker: bool = False
    api_key: bool = False
    missing: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        if self.available:
            return f"Strix is ready ({self.binary})."
        return "Strix is not available: " + ", ".join(self.missing) + "."

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "binary": self.binary, "docker": self.docker,
                "api_key": self.api_key, "missing": list(self.missing), "reason": self.reason}


def availability(environment: dict[str, str] | None = None) -> Availability:
    """Check the three things a Strix scan needs: the CLI, a running Docker, an LLM key."""
    env = environment if environment is not None else dict(os.environ)
    binary = shutil.which("strix")
    docker = shutil.which("docker")
    running = False
    if docker:
        try:
            probe = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"],  # noqa: S603
                                   capture_output=True, text=True, timeout=20)
            running = probe.returncode == 0
        except (OSError, subprocess.SubprocessError):
            running = False
    api_key = bool(env.get("LLM_API_KEY") or env.get("STRIX_LLM_API_KEY"))
    missing: list[str] = []
    if binary is None:
        missing.append("the strix CLI is not installed (curl -sSL https://strix.ai/install | bash)")
    if not docker:
        missing.append("docker is not installed")
    elif not running:
        missing.append("the docker daemon is not running")
    if not api_key:
        missing.append("LLM_API_KEY is not set")
    return Availability(not missing, binary, running, api_key, tuple(missing))


@dataclass
class StrixScan:
    """Outcome of one Strix run, mapped into Evo Code's vocabulary."""

    status: str  # completed | vulnerabilities | error | unavailable
    exit_code: int | None = None
    duration_ms: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)
    run_directory: str | None = None
    message: str = ""
    output: str = ""

    def to_dict(self, *, include_output: bool = False) -> dict[str, Any]:
        payload = {"status": self.status, "exit_code": self.exit_code, "duration_ms": self.duration_ms,
                   "findings": self.findings, "run_directory": self.run_directory, "message": self.message}
        if include_output:
            payload["output"] = self.output
        return payload


def build_command(binary: str, target: Path, *, mode: str = "quick", instruction: str | None = None,
                  max_budget: float | None = None) -> list[str]:
    """The exact Strix invocation Evo Code uses: always headless, always Evo-built."""
    if mode not in SCAN_MODES:
        raise ValueError(f"Unknown Strix scan mode: {mode}. Choose one of {', '.join(SCAN_MODES)}.")
    command = [binary, "--non-interactive", "--target", str(target), "--scan-mode", mode]
    if instruction:
        command += ["--instruction", instruction[:2000]]
    if max_budget and max_budget > 0:
        command += ["--max-budget", f"{max_budget:g}"]
    return command


def _coerce_finding(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Strix finding onto Evo Code's finding shape (file + line + evidence)."""
    title = str(item.get("title") or item.get("name") or item.get("vulnerability") or "").strip()
    if not title:
        return None
    location = item.get("location") or item.get("file_path") or item.get("file") or ""
    line = item.get("line") or item.get("line_number") or (item.get("location") or {}).get("line") \
        if isinstance(item.get("location"), dict) else item.get("line") or item.get("line_number")
    if isinstance(location, dict):
        location = location.get("file") or location.get("path") or ""
    path = str(location).split(":")[0].lstrip("./")
    if ":" in str(location) and not line:
        tail = str(location).rsplit(":", 1)[-1]
        line = int(tail) if tail.isdigit() else None
    severity = SEVERITY_MAP.get(str(item.get("severity") or item.get("risk") or "").lower(), "medium")
    return {
        "title": title[:160],
        "severity": severity,
        "description": str(item.get("description") or item.get("summary") or "")[:1200],
        "recommendation": str(item.get("remediation") or item.get("fix") or item.get("recommendation") or "")[:800],
        "file": path or None,
        "line": int(line) if isinstance(line, int) or (isinstance(line, str) and str(line).isdigit()) else None,
        "evidence": str(item.get("evidence") or item.get("proof_of_concept") or item.get("poc") or "")[:600],
        "cwe": item.get("cwe") or item.get("cwe_id"),
        "cvss": item.get("cvss") or item.get("cvss_score"),
        "source": "strix",
        "validated": bool(item.get("validated") or item.get("verified") or item.get("proof_of_concept")),
    }


def parse_run(directory: Path) -> list[dict[str, Any]]:
    """Read findings from a Strix run directory, tolerating differences between versions."""
    findings: list[dict[str, Any]] = []
    candidates = [directory / name for name in RUN_FILES]
    candidates += sorted(directory.rglob("*.json"))[:50]
    seen: set[str] = set()
    for path in candidates:
        if not path.is_file() or str(path) in seen:
            continue
        seen.add(str(path))
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        items = payload if isinstance(payload, list) else None
        if items is None and isinstance(payload, dict):
            for key in ("findings", "vulnerabilities", "results", "issues"):
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break
        for item in items or []:
            if isinstance(item, dict):
                mapped = _coerce_finding(item)
                if mapped and not any(existing["title"] == mapped["title"] and existing["file"] == mapped["file"]
                                      for existing in findings):
                    findings.append(mapped)
        if findings:
            break
    return findings


def latest_run(working_directory: Path) -> Path | None:
    runs = working_directory / "strix_runs"
    if not runs.is_dir():
        return None
    directories = [path for path in runs.iterdir() if path.is_dir()]
    return max(directories, key=lambda path: path.stat().st_mtime) if directories else None


def run_scan(target: Path, *, mode: str = "quick", instruction: str | None = None, timeout: float = 1800.0,
             max_budget: float | None = None, working_directory: Path | None = None) -> StrixScan:
    """Run Strix against ``target`` and return its findings. Never raises for scan failures."""
    import time

    state = availability()
    if not state.available or state.binary is None:
        return StrixScan("unavailable", message=state.reason)
    command = build_command(state.binary, target, mode=mode, instruction=instruction, max_budget=max_budget)
    cwd = working_directory or target
    cwd.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        process = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,  # noqa: S603
                                 timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return StrixScan("error", duration_ms=int((time.monotonic() - started) * 1000),
                         message=f"Strix did not finish within {timeout:.0f}s.")
    except (OSError, subprocess.SubprocessError) as exc:
        return StrixScan("error", message=f"Could not run Strix: {exc}")
    duration = int((time.monotonic() - started) * 1000)
    output = (process.stdout or "") + (process.stderr or "")
    run_directory = latest_run(cwd)
    findings = parse_run(run_directory) if run_directory else []
    if process.returncode == EXIT_ERROR:
        first = next((line for line in output.strip().splitlines()[::-1] if line.strip()), "")
        return StrixScan("error", process.returncode, duration, findings,
                         str(run_directory) if run_directory else None,
                         f"Strix reported a fatal error: {first[:300]}", output[-8000:])
    status = "vulnerabilities" if process.returncode == EXIT_VULNERABILITIES or findings else "completed"
    message = (f"{len(findings)} validated finding(s) from Strix" if findings
               else "Strix found no vulnerabilities")
    logger.info("Strix scan finished", extra={"status": status, "findings": len(findings)})
    return StrixScan(status, process.returncode, duration, findings,
                     str(run_directory) if run_directory else None, message, output[-8000:])
