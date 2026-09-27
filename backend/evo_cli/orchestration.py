"""Terminal rendering of Evo Code's multi-level orchestration.

Level 0 is the Evo Brain, level 1 the lead agents it routes work to, and level
2 the specialist sub-agents each lead delegates to. ``format_event`` renders a
live timeline event with its level; ``print_orchestration`` renders the whole
hierarchy of one analysis. The snapshot is built from ``agent_runs`` rather
than the final report, so it also works while an analysis is running, after a
failure, and for analyses recorded before sub-agents existed.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from evo_cli.console import Console, Style
from services import Services

STAGE_LABELS = {
    "downloading": "download",
    "extracting": "extract",
    "scanning": "scan",
    "indexing": "index",
    "understanding": "understand",
    "retrieving": "retrieve",
    "routing": "route",
    "running": "delegate",
    "aggregating": "aggregate",
    "cross_validating": "cross-validate",
    "verifying": "verify",
    "memorizing": "memorize",
}
_STATUS = {
    "complete": ("✓", "+", Style.BRIGHT_GREEN),
    "completed": ("✓", "+", Style.BRIGHT_GREEN),
    "failed": ("✗", "x", Style.BRIGHT_RED),
    "skipped": ("○", "o", Style.DIM),
    "running": ("◐", "*", Style.BRIGHT_MAGENTA),
    "processing": ("◐", "*", Style.BRIGHT_MAGENTA),
    "queued": ("·", ".", Style.DIM),
    "pending": ("·", ".", Style.DIM),
    "not run": ("·", ".", Style.DIM),
}
_LEVEL = {
    "success": ("✓", "+", Style.BRIGHT_GREEN),
    "warning": ("▲", "!", Style.BRIGHT_YELLOW),
    "error": ("✗", "x", Style.BRIGHT_RED),
}
MEMBER_TITLE_WIDTH = 26


def status_symbol(console: Console, status: str) -> str:
    fancy, plain, color = _STATUS.get(status, ("·", ".", Style.DIM))
    return console.paint(console.glyph(fancy, plain), color)


def format_duration(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "—"
    return f"{milliseconds}ms" if milliseconds < 10_000 else f"{milliseconds / 1000:.1f}s"


# --------------------------------------------------------------------------- live events
def format_event(console: Console, event: dict[str, Any], *, truncate: bool = False) -> str:
    """One timeline event: elapsed time, Brain stage, orchestration level and message."""
    elapsed = f"+{event.get('elapsed_ms', 0) / 1000:05.2f}s"
    stage = str(event.get("stage", ""))[:16].ljust(16)
    depth = min(2, max(0, int(event.get("depth") or 0)))
    level = str(event.get("level", "info"))
    if level in _LEVEL:
        fancy, plain, color = _LEVEL[level]
    elif depth == 1:
        fancy, plain, color = "◆", ">", Style.BRIGHT_CYAN
    elif depth == 2:
        fancy, plain, color = "○", "o", Style.DIM
    else:
        fancy, plain, color = "·", ".", Style.BRIGHT_CYAN
    parent = event.get("parent") if depth == 2 else None
    label = f"{parent} {console.glyph('›', '>')} " if parent else ""
    indent = "   " * depth
    message = str(event.get("message", ""))
    if truncate:
        used = len(f" {elapsed}  {stage} L{depth} {indent}x {label}")
        message = console.fit(message, max(24, console.width - used))
    return (
        f" {console.paint(elapsed, Style.DIM)}  {console.paint(stage, Style.MAGENTA)} "
        f"{console.paint(f'L{depth}', Style.DIM)} {indent}{console.paint(console.glyph(fancy, plain), color)} "
        f"{console.paint(label, Style.DIM) if label else ''}{message}"
    )


# --------------------------------------------------------------------------- snapshot
def orchestration_snapshot(services: Services, repo: dict[str, Any]) -> dict[str, Any]:
    """Brain → lead agents → specialist sub-agents for the repository's latest analysis."""
    latest: dict[str, dict[str, Any]] = {}
    for run in services.store.list_agent_runs(repo["id"], repo["analysis_count"]):
        latest[run["agent"]] = run  # later rows win
    active = repo["status"] in {"queued", "processing"}
    leads: list[dict[str, Any]] = []
    for name, agent in services.brain.agents.items():
        run = latest.get(name)
        members: list[dict[str, Any]] = []
        for spec in agent.team:
            member = latest.get(f"{name}.{spec.key}")
            if member is None:
                continue
            record = member.get("result") or {}
            members.append({
                "name": f"{name}.{spec.key}",
                "key": spec.key,
                "title": record.get("title") or spec.title,
                "description": spec.description,
                "status": member["status"],
                "wave": record.get("wave") or 0,
                "depends_on": record.get("depends_on") or [],
                "mode": member.get("mode") or record.get("mode"),
                "summary": member.get("summary") or "",
                "reason": member.get("reason") or record.get("reason") or "",
                "error": member.get("error"),
                "duration_ms": member.get("duration_ms"),
            })
        leads.append({
            "name": name,
            "title": agent.title,
            "status": run["status"] if run else ("pending" if active else "not run"),
            "wave": 2 if agent.requires else 1,
            "reason": run.get("reason") if run else None,
            "mode": run.get("mode") if run else None,
            "summary": run.get("summary") if run else None,
            "error": run.get("error") if run else None,
            "duration_ms": run.get("duration_ms") if run else None,
            "requires": list(agent.requires),
            "consumes": list(agent.consumes),
            "team_size": len(agent.team),
            "members": members,
        })
    stages: list[str] = []
    for entry in repo.get("timeline") or []:
        stage = entry.get("stage")
        if stage in STAGE_LABELS and stage not in stages:
            stages.append(stage)
    statuses = Counter(member["status"] for lead in leads for member in lead["members"])
    report = repo.get("report") or {}
    return {
        "repository_id": repo["id"],
        "analysis": repo["analysis_count"],
        "status": repo["status"],
        "stage": repo.get("stage"),
        "task": repo.get("task") or "",
        "levels": 3,
        "stages": stages,
        "leads": leads,
        "cross_validation": report.get("cross_validation") or {},
        "verification": report.get("verification") or {},
        "totals": {
            "lead_agents": len(leads),
            "leads_run": sum(1 for lead in leads if lead["status"] in {"complete", "failed", "running"}),
            "leads_complete": sum(1 for lead in leads if lead["status"] == "complete"),
            "subagents": sum(lead["team_size"] for lead in leads),
            "subagents_planned": sum(len(lead["members"]) for lead in leads),
            "subagents_complete": statuses["complete"],
            "subagents_skipped": statuses["skipped"],
            "subagents_failed": statuses["failed"],
        },
    }


def orchestration_line(snapshot: dict[str, Any]) -> str:
    totals = snapshot["totals"]
    if not totals["subagents_planned"] and not totals["leads_run"]:
        return "3 levels · no agents have run yet"
    return (
        f"3 levels · {totals['leads_run']}/{totals['lead_agents']} lead agents · "
        f"{totals['subagents_complete']}/{totals['subagents']} specialist sub-agents ran · "
        f"{totals['subagents_skipped']} skipped · {totals['subagents_failed']} failed"
    )


# --------------------------------------------------------------------------- tree
def _lead_detail(console: Console, lead: dict[str, Any]) -> str:
    status = lead["status"]
    if status == "complete":
        upstream = [*lead["requires"], *lead["consumes"]]
        received = f" {console.glyph('←', '<-')} {', '.join(upstream)}" if upstream else ""
        return f"wave {lead['wave']}{received} · {format_duration(lead['duration_ms'])} · {lead['summary'] or ''}"
    if status == "failed":
        return f"failed · {lead['error'] or lead['summary'] or 'unknown error'}"
    if status == "skipped":
        return f"skipped · {lead['reason'] or 'not routed'} ({lead['team_size']} specialists idle)"
    if status == "running":
        return "running…"
    return status


def _member_detail(member: dict[str, Any]) -> str:
    status = member["status"]
    if status == "complete":
        return member["summary"] or "complete"
    if status == "failed":
        return f"failed · {member['error'] or 'unknown error'}"
    if status == "skipped":
        return f"skipped · {member['reason'] or 'not required'}"
    if status == "running":
        return "running…"
    return status


def print_orchestration(console: Console, services: Services, repo: dict[str, Any]) -> dict[str, Any]:
    """Render the Brain → lead agents → specialist sub-agents tree; returns the snapshot."""
    snapshot = orchestration_snapshot(services, repo)
    tee, elbow = console.glyph("├─ ", "|- "), console.glyph("└─ ", "`- ")
    pipe, blank = console.glyph("│  ", "|  "), "   "

    def clip(text: str, room: int) -> str:  # one line per node on a terminal; full text in pipes/files
        return console.fit(text, max(16, room)) if console.is_tty else text

    console.heading("Multi-level orchestration",
                    f"analysis #{snapshot['analysis']} · {orchestration_line(snapshot)}")
    task = clip(snapshot["task"], console.width - 40)
    console.write(
        f"{console.paint(console.glyph('◉', '*'), Style.BOLD, Style.BRIGHT_CYAN)} "
        f"{console.paint('Evo Brain', Style.BOLD)}  {console.paint('L0', Style.DIM)}  "
        f"{status_symbol(console, repo['status'])} {repo['status']} · task: \"{task}\""
    )
    arrow = console.glyph(" → ", " > ")
    line = ""
    for label in (STAGE_LABELS[stage] for stage in snapshot["stages"]):
        candidate = f"{line}{arrow}{label}" if line else label
        if line and len(candidate) > console.width - 4:
            console.write(f"{pipe}{console.paint(line + arrow.rstrip(), Style.DIM)}")
            candidate = label
        line = candidate
    if line:
        console.write(f"{pipe}{console.paint(line, Style.DIM)}")

    for lead in snapshot["leads"]:
        detail = clip(_lead_detail(console, lead), console.width - len(lead["title"]) - 12)
        console.write(
            f"{tee}{status_symbol(console, lead['status'])} {console.paint(lead['title'], Style.BOLD)}  "
            f"{console.paint('L1', Style.DIM)}  {detail}"
        )
        members = lead["members"]
        for index, member in enumerate(members):
            connector = elbow if index == len(members) - 1 else tee
            wave = f"w{member['wave']}" if member["wave"] else "—"
            duration = format_duration(member["duration_ms"]) if member["status"] in {"complete", "failed"} else ""
            head = f"{pipe}{connector}x {member['title']:<{MEMBER_TITLE_WIDTH}} L2 {wave:<3}{duration:>7}  "
            text = clip(_member_detail(member), console.width - len(head))
            console.write(
                f"{pipe}{connector}{status_symbol(console, member['status'])} "
                f"{member['title']:<{MEMBER_TITLE_WIDTH}} {console.paint('L2', Style.DIM)} "
                f"{console.paint(f'{wave:<3}', Style.DIM)}{console.paint(f'{duration:>7}', Style.DIM)}  "
                f"{console.paint(text, Style.DIM) if member['status'] == 'skipped' else text}"
            )

    xv, verification = snapshot["cross_validation"], snapshot["verification"]
    closing = snapshot["status"] == "completed"
    console.write(
        f"{elbow}{status_symbol(console, 'complete' if closing else snapshot['status'])} "
        f"{console.paint('Cross-validation & source verification', Style.BOLD)}  {console.paint('L0', Style.DIM)}"
    )
    lines: list[str] = []
    if xv:
        lines.append(
            f"{xv.get('accepted', 0)} accepted · {len(xv.get('merged', []))} merged · "
            f"{len(xv.get('conflicts', []))} conflict(s) · {len(xv.get('unsupported', []))} unsupported claim(s) rejected"
        )
    if verification:
        lines.append(
            f"{verification.get('findings_verified', 0)}/{verification.get('findings_total', 0)} findings · "
            f"{verification.get('claims_verified', 0)}/{verification.get('claims_total', 0)} architecture & "
            "walkthrough citations verified (SHA-256)"
        )
    if not lines:
        lines.append("pending" if snapshot["status"] in {"queued", "processing"} else "not reached")
    for index, text in enumerate(lines):
        connector = elbow if index == len(lines) - 1 else tee
        console.write(f"{blank}{connector}{clip(text, console.width - 6)}")
    return snapshot
