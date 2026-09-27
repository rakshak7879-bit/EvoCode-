"""Finding records, fingerprints and live re-verification.

Analysis time: accepted claims become persisted finding records carrying the
SHA-256 of the cited lines and of the file.
Any later time: ``reverify_findings`` / ``reverify_citations`` re-read the
working copy and report VERIFIED or STALE for each claim.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from memory.store import MemoryStore, utc_now
from orchestrator.crossval import AcceptedFinding
from verification.citations import SourceVerifier, VerificationStatus, worst_status


def fingerprint(category: str, rule_or_title: str, file: str, anchor_text: str) -> str:
    normalized = re.sub(r"\d+", "N", rule_or_title.lower())
    anchor = re.sub(r"\s+", " ", anchor_text).strip().lower()
    return hashlib.sha256(f"{category}|{normalized}|{file}|{anchor}".encode()).hexdigest()[:16]


def _record_fingerprint(item: AcceptedFinding) -> str:
    draft = item.draft
    if draft.category == "duplicate":
        members = ",".join(sorted(f"{loc.file}#{loc.symbol}" for loc, _ in item.location_anchors))
        return fingerprint("duplicate", draft.rule_id or draft.title, "", members)
    return fingerprint(draft.category, draft.rule_id or draft.title, draft.file, draft.evidence or "")


def build_records(repo_id: str, accepted: list[AcceptedFinding], indexed: dict[str, str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    now = utc_now()
    for position, item in enumerate(accepted):
        draft, anchor = item.draft, item.anchor
        locations = []
        for location, location_anchor in item.location_anchors:
            locations.append({
                **location.model_dump(),
                "line": location_anchor.line_start,
                "line_end": location_anchor.line_end,
                "evidence_sha256": location_anchor.evidence_sha256,
                "file_sha256": location_anchor.file_sha256,
                "verification": {"status": str(location_anchor.status), "message": location_anchor.message},
            })
        status = worst_status([anchor.status, *(VerificationStatus(l["verification"]["status"]) for l in locations)])
        fp = _record_fingerprint(item)
        finding_id = hashlib.sha256(f"{repo_id}|{fp}|{draft.file}|{anchor.line_start}|{position}".encode()).hexdigest()[:16]
        records.append({
            "id": finding_id,
            "agent": draft.agent,
            "category": draft.category,
            "severity": draft.severity,
            "title": draft.title,
            "description": draft.description,
            "file": draft.file,
            "line": anchor.line_start,
            "line_end": anchor.line_end,
            "evidence": draft.evidence,
            "recommendation": draft.recommendation,
            "confidence": draft.confidence,
            "source": draft.source,
            "rule_id": draft.rule_id,
            "fingerprint": fp,
            "status": str(status),
            "sha256": anchor.evidence_sha256,
            "file_sha256": anchor.file_sha256,
            "verification": {
                "status": str(anchor.status),
                "message": anchor.message,
                "line_start": anchor.line_start,
                "line_end": anchor.line_end,
                "file_sha256_indexed": indexed.get(draft.file),
                "file_sha256_current": anchor.file_sha256,
                "evidence_sha256_expected": anchor.evidence_sha256,
                "evidence_sha256_current": anchor.evidence_sha256,
                "file_changed": bool(indexed.get(draft.file)) and indexed.get(draft.file) != anchor.file_sha256,
                "lines_changed": False,
                "relocated_line": None,
                "relocated_from": anchor.relocated_from,
                "checked_at": now,
            },
            "extra": {
                "family": draft.family,
                "cwe": draft.cwe,
                "locations": locations,
                "similarity": draft.similarity,
                "annotations": draft.annotations,
                "detectors": draft.detectors,
                "merged_from": item.merged_from,
            },
            "verified_at": now,
        })
    return records


SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The one finding order used everywhere a finding is referred to by number (``show 3``, ``fix 3``)."""
    findings.sort(key=lambda f: (SEVERITY_RANK.get(f["severity"], 9), f["category"], f["file"], f["line"]))
    return findings


def reverify_findings(store: MemoryStore, repo_id: str, root: Path) -> list[dict[str, Any]]:
    """Re-check every stored finding against the current working copy and persist the result."""
    findings = store.list_findings(repo_id)
    if not findings:
        return findings
    verifier = SourceVerifier(root)
    updates = []
    for finding in findings:
        result = verifier.verify(finding["file"], finding["line"], finding["line_end"], finding["sha256"],
                                 finding["file_sha256"])
        statuses = [result.status]
        extra = finding["extra"]
        for location in extra.get("locations", []):
            location_result = verifier.verify(location["file"], location["line"], location.get("line_end"),
                                              location.get("evidence_sha256"), location.get("file_sha256"))
            location["verification"] = {"status": str(location_result.status), "message": location_result.message}
            statuses.append(location_result.status)
        status = worst_status(statuses)
        verification = result.to_dict()
        if status != result.status:
            verification["message"] = "One or more duplicate locations changed. Re-run analysis."
        finding["status"] = str(status)
        finding["verification"] = verification
        updates.append((finding["id"], str(status), verification, extra))
    store.update_finding_verifications(updates)
    return findings


def anchor_citation(citation: dict[str, Any], verifier: SourceVerifier, indexed: dict[str, str]) -> bool:
    """Attach analysis-time verification to a citation dict ({file, line_start, line_end})."""
    anchor = verifier.anchor(citation["file"], citation["line_start"], citation.get("line_end"), None,
                             indexed.get(citation["file"]))
    citation["verification"] = {
        "status": str(anchor.status),
        "message": anchor.message,
        "evidence_sha256": anchor.evidence_sha256,
        "file_sha256": anchor.file_sha256,
    }
    return anchor.status == VerificationStatus.VERIFIED


def reverify_citation(citation: dict[str, Any], verifier: SourceVerifier) -> bool:
    previous = citation.get("verification") or {}
    result = verifier.verify(citation["file"], citation["line_start"], citation.get("line_end"),
                             previous.get("evidence_sha256"), previous.get("file_sha256"))
    citation["verification"] = {
        **previous,
        "status": str(result.status),
        "message": result.message,
        "file_sha256_current": result.file_sha256_current,
        "checked_at": result.checked_at,
    }
    return result.status == VerificationStatus.VERIFIED


def explainer_citations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every citation dict inside Explainer output (mutable references)."""
    citations: list[dict[str, Any]] = []
    for layer in (data.get("architecture") or {}).values():
        citations.extend(layer.get("evidence", []))
    for flow in data.get("request_flows") or []:
        for step in flow.get("steps", []):
            step.setdefault("line_start", step.get("line", 1))
            step.setdefault("line_end", step.get("line", 1))
            citations.append(step)
    for note in data.get("history") or []:
        citations.append(note["source"])
    if data.get("summary_citation"):
        citations.append(data["summary_citation"])
    return citations


def walkthrough_citations(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for step in data.get("steps") or [] for c in step.get("citations", [])]
