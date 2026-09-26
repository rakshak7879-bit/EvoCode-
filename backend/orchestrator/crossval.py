"""Cross-validation of agent claims (run by the Brain, never by agents).

1. Anchor every claim: the cited file/line must exist and contain the claimed
   evidence. Off-by-N line numbers are corrected; unsupported claims are
   rejected and reported.
2. Merge duplicate detections of the same issue (e.g. two rules, or a rule and
   the LLM, flagging the same line) and boost confidence.
3. Resolve severity conflicts (keep the highest, record the disagreement).
4. Add cross-agent annotations: legacy code documented in history notes, and
   security issues that live inside duplicated code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agents.base import SEVERITY_ORDER, FindingDraft, Location
from agents.history import legacy_notes, legacy_symbols
from repo.parser import Symbol
from verification.citations import Anchor, SourceVerifier, VerificationStatus

REJECTED = {
    VerificationStatus.UNSUPPORTED,
    VerificationStatus.INVALID_LINE,
    VerificationStatus.MISSING,
    VerificationStatus.UNSAFE,
}
SymbolLookup = Callable[[str, int], Symbol | None]


@dataclass
class AcceptedFinding:
    draft: FindingDraft
    anchor: Anchor
    location_anchors: list[tuple[Location, Anchor]] = field(default_factory=list)
    merged_from: int = 1


def _families_compatible(a: str | None, b: str | None) -> bool:
    return a == b or a is None or b is None


def _anchor_drafts(drafts: list[FindingDraft], verifier: SourceVerifier, indexed: dict[str, str],
                   report: dict[str, Any]) -> list[AcceptedFinding]:
    anchored: list[AcceptedFinding] = []
    for draft in drafts:
        anchor = verifier.anchor(draft.file, draft.line, draft.line_end, draft.evidence, indexed.get(draft.file))
        if anchor.status in REJECTED:
            report["unsupported"].append({
                "agent": draft.agent, "source": draft.source, "title": draft.title, "file": draft.file,
                "line": draft.line, "status": str(anchor.status), "reason": anchor.message,
            })
            continue
        if anchor.relocated_from is not None:
            report["relocated"].append({"title": draft.title, "file": draft.file, "from": anchor.relocated_from,
                                        "to": anchor.line_start})
            draft = draft.model_copy(update={"line": anchor.line_start, "line_end": anchor.line_end})
        location_anchors: list[tuple[Location, Anchor]] = []
        for location in draft.locations:
            location_anchor = verifier.anchor(location.file, location.line, location.line_end, location.evidence,
                                              indexed.get(location.file))
            if location_anchor.status in REJECTED:
                report["unsupported"].append({
                    "agent": draft.agent, "source": draft.source, "title": f"{draft.title} (location)",
                    "file": location.file, "line": location.line, "status": str(location_anchor.status),
                    "reason": location_anchor.message,
                })
                continue
            location_anchors.append((location, location_anchor))
        if draft.locations and len(location_anchors) < 2:
            continue  # a duplicate needs at least two verified locations
        anchored.append(AcceptedFinding(draft, anchor, location_anchors))
    return anchored


def _merge(anchored: list[AcceptedFinding], report: dict[str, Any]) -> list[AcceptedFinding]:
    groups: dict[tuple[str, str, int], list[list[AcceptedFinding]]] = {}
    for item in anchored:
        key = (item.draft.category, item.draft.file, item.draft.line)
        clusters = groups.setdefault(key, [])
        for cluster in clusters:
            if _families_compatible(cluster[0].draft.family, item.draft.family):
                cluster.append(item)
                break
        else:
            clusters.append([item])

    merged: list[AcceptedFinding] = []
    for clusters in groups.values():
        for cluster in clusters:
            if len(cluster) == 1:
                merged.append(cluster[0])
                continue
            cluster.sort(key=lambda i: (SEVERITY_ORDER[i.draft.severity], i.draft.source != "rule", -i.draft.confidence))
            primary = cluster[0]
            detectors = list(dict.fromkeys(d for i in cluster for d in (i.draft.detectors or [i.draft.source])))
            severities = sorted({i.draft.severity for i in cluster}, key=lambda s: SEVERITY_ORDER[s])
            if len(severities) > 1:
                report["conflicts"].append({
                    "file": primary.draft.file, "line": primary.draft.line, "title": primary.draft.title,
                    "detail": f"Detectors disagreed on severity ({', '.join(severities)})",
                    "resolution": f"Kept the highest severity ({primary.draft.severity})",
                })
            report["merged"].append({"file": primary.draft.file, "line": primary.draft.line,
                                     "title": primary.draft.title, "detectors": detectors, "count": len(cluster)})
            confidence = min(0.99, max(i.draft.confidence for i in cluster) + 0.04 * (len(cluster) - 1))
            draft = primary.draft.model_copy(update={
                "detectors": detectors,
                "confidence": round(confidence, 2),
                "annotations": [*primary.draft.annotations,
                                f"Confirmed by {len(cluster)} independent detectors ({', '.join(detectors)})."],
            })
            merged.append(AcceptedFinding(draft, primary.anchor, primary.location_anchors, len(cluster)))
    return merged


def _history_relevant(note: dict[str, Any], locations: list[Location]) -> bool:
    """A history note explains a duplicate cluster if it names one of its symbols, or names
    both the canonical implementation's file and a file holding a copy."""
    files, symbols = set(note["files"]), set(note["symbols"])
    if any(loc.symbol and loc.symbol in symbols for loc in locations):
        return True
    canonical = next((loc for loc in locations if loc.role == "canonical"), None)
    return canonical is not None and canonical.file in files and any(
        loc.role == "copy" and loc.file in files for loc in locations
    )


def _annotate(items: list[AcceptedFinding], explainer: dict[str, Any], duplicates: dict[str, Any],
              symbol_at: SymbolLookup) -> int:
    history = explainer.get("history") or []
    by_symbol = legacy_symbols(history)
    notes_about_legacy = legacy_notes(history)
    clusters = duplicates.get("clusters") or []
    added = 0
    for item in items:
        draft = item.draft
        notes: list[str] = []
        if draft.category == "duplicate":
            for note in notes_about_legacy:
                if _history_relevant(note, draft.locations):
                    source = note["source"]
                    notes.append(f"Engineering history ({source['file']}:{source['line_start']}): {note['note']}")
                if len(notes) >= 2:
                    break
        else:
            symbol = symbol_at(draft.file, draft.line)
            if symbol is not None and symbol.name in by_symbol:
                source = by_symbol[symbol.name]["source"]
                notes.append(
                    f"`{symbol.name}` is documented as legacy ({source['file']}:{source['line_start']}): "
                    "consider removing it instead of patching it."
                )
            for cluster in clusters:
                member = next((m for m in cluster["members"]
                               if m["file"] == draft.file and m["line_start"] <= draft.line <= m["line_end"]), None)
                if member:
                    notes.append(f"This code is one of {len(cluster['members'])} duplicated copies "
                                 f"({cluster['title']}); fix every copy.")
                    break
        if notes:
            item.draft = draft.model_copy(update={"annotations": [*draft.annotations, *notes]})
            added += len(notes)
    return added


def cross_validate(
    drafts: list[FindingDraft],
    verifier: SourceVerifier,
    indexed: dict[str, str],
    explainer: dict[str, Any],
    duplicates: dict[str, Any],
    symbol_at: SymbolLookup,
) -> tuple[list[AcceptedFinding], dict[str, Any]]:
    report: dict[str, Any] = {"candidates": len(drafts), "merged": [], "conflicts": [], "unsupported": [],
                              "relocated": [], "annotations": 0}
    anchored = _anchor_drafts(drafts, verifier, indexed, report)
    merged = _merge(anchored, report)
    report["annotations"] = _annotate(merged, explainer, duplicates, symbol_at)
    merged.sort(key=lambda i: (i.draft.category != "security", SEVERITY_ORDER[i.draft.severity], i.draft.file,
                               i.draft.line))
    report["accepted"] = len(merged)
    llm_claims = sum(1 for d in drafts if d.source == "llm")
    report["checks"] = [
        {"name": "Claims with evidence at the cited line", "value": len(anchored), "total": len(drafts)},
        {"name": "Duplicate detections merged", "value": len(report["merged"])},
        {"name": "Severity conflicts resolved", "value": len(report["conflicts"])},
        {"name": "Unsupported claims rejected", "value": len(report["unsupported"])},
        {"name": "Citations auto-corrected", "value": len(report["relocated"])},
        {"name": "Cross-agent annotations", "value": report["annotations"]},
        {"name": "LLM claims checked", "value": llm_claims},
    ]
    return merged, report
