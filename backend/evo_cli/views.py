"""Terminal views for repositories, findings, source, memory and walkthroughs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from evo_cli.console import Console, Style
from evo_cli.orchestration import format_duration, format_event, orchestration_line, orchestration_snapshot
from memory.store import MemoryStore
from orchestrator.evidence import reverify_findings, sort_findings
from repo.source_service import SourceDocument
from services import Services

# Keep ordering deterministic across CLI commands.
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def short_hash(value: str | None, length: int = 12) -> str:
    return (value or "")[:length]


def resolve_repository(store: MemoryStore, value: str | None) -> dict[str, Any]:
    repos = store.list_repositories(limit=500)
    if value:
        exact = next((repo for repo in repos if repo["id"] == value), None)
        if exact:
            return exact
        matches = [repo for repo in repos if repo["id"].startswith(value)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Repository prefix {value!r} is ambiguous.")
        raise ValueError(f"Repository not found: {value}")
    if not repos:
        raise ValueError("No repositories have been analyzed yet. Run `./evo demo` or `./evo analyze SOURCE`.")
    return repos[0]


def latest_agent_result(services: Services, repo: dict[str, Any], agent: str) -> dict[str, Any] | None:
    runs = services.store.list_agent_runs(repo["id"], repo["analysis_count"])
    match = next((run for run in reversed(runs) if run["agent"] == agent and run["status"] == "complete"), None)
    return match["result"] if match else None


def status_color(status: str) -> str:
    if status in {"completed", "complete", "verified"}:
        return Style.BRIGHT_GREEN
    if status in {"failed", "stale", "missing"}:
        return Style.BRIGHT_YELLOW
    if status in {"running", "processing"}:
        return Style.BRIGHT_MAGENTA
    return Style.DIM


def print_repository_header(console: Console, repo: dict[str, Any]) -> None:
    console.heading(repo["name"], f"repository {repo['id']} · {repo['source']} · analysis #{repo['analysis_count']}")


def print_repository_summary(console: Console, services: Services, repo: dict[str, Any]) -> None:
    findings = services.store.list_findings(repo["id"])
    files = services.store.list_files(repo["id"])
    counts = services.store.memory_counts(repo["id"])
    categories = Counter(f["category"] for f in findings)
    statuses = Counter(f["status"] for f in findings)
    print_repository_header(console, repo)
    console.key_value("Status", console.paint(repo["status"].upper(), Style.BOLD, status_color(repo["status"])), indent=2)
    console.key_value("Brain", repo.get("brain_message") or repo.get("stage") or "idle", indent=2)
    console.key_value("Files", len(files), indent=2)
    console.key_value("Memory", f"{sum(counts.values())} entries ({', '.join(f'{v} {k}' for k, v in counts.items())})", indent=2)
    if repo["analysis_count"]:
        console.key_value("Orchestration", orchestration_line(orchestration_snapshot(services, repo)), indent=2)
    if repo["status"] == "completed":
        console.key_value("Findings", f"{len(findings)} total · {categories['security']} security · "
                          f"{categories['duplicate']} duplicate clusters", indent=2)
        console.key_value("Verification", f"{statuses['verified']}/{len(findings)} verified · {statuses['stale']} stale", indent=2)
    if repo.get("error"):
        console.error(repo["error"])


def print_history(console: Console, repo: dict[str, Any]) -> None:
    """What changed since the previous analysis of this working copy."""
    history = (repo.get("report") or {}).get("history") or {}
    if not history.get("previous_total"):
        return
    resolved, new = history.get("resolved") or [], history.get("new") or []
    if resolved:
        console.success("Resolved since the previous analysis: " + ", ".join(
            f"{item['title']} ({item['file']}:{item['line']})" for item in resolved))
    if new:
        console.warning("New since the previous analysis: " + ", ".join(
            f"{item['title']} ({item['file']}:{item['line']})" for item in new))
    if not resolved and not new:
        console.info(f"No changes since the previous analysis ({history.get('unchanged', 0)} findings unchanged).")


def print_mode(console: Console, services: Services) -> None:
    info = services.llm.describe()
    console.heading("Runtime mode")
    console.key_value("Analysis", info["label"], indent=2)
    console.key_value("Provider", info["provider"], indent=2)
    console.key_value("Model", info["model"], indent=2)
    console.key_value("FTS5", "enabled" if services.db.fts5_enabled else "LIKE fallback", indent=2)
    console.key_value("Data", services.settings.data_dir, indent=2)
    console.key_value("Pacing", f"{services.settings.pacing_ms} ms per Brain stage (EVO_PACING_MS)", indent=2)
    roster = sum(len(agent.team) for agent in services.brain.agents.values())
    console.key_value("Agents", f"Evo Brain → {len(services.brain.agents)} lead agents → {roster} specialist "
                      "sub-agents", indent=2)


def print_repositories(console: Console, services: Services) -> list[dict[str, Any]]:
    repos = services.store.list_repositories(limit=30)
    console.heading("Repositories", "Most recent local analyses")
    rows = [
        (
            repo["id"][:8],
            repo["name"],
            repo["source"],
            console.paint(repo["status"], status_color(repo["status"])),
            f"#{repo['analysis_count']}",
        )
        for repo in repos
    ]
    console.table(("ID", "NAME", "SOURCE", "STATUS", "RUN"), rows, max_widths=(8, 38, 10, 12, 5))
    return repos


def print_agents(console: Console, services: Services, repo: dict[str, Any]) -> None:
    """Lead agents (level 1) with their specialist sub-agents (level 2) from the latest analysis."""
    snapshot = orchestration_snapshot(services, repo)
    console.heading("Agents", f"Evo Brain → lead agents → specialist sub-agents · {orchestration_line(snapshot)}")
    tee, elbow = console.glyph("├ ", "|-"), console.glyph("└ ", "`-")
    rows = []
    for lead, agent in zip(snapshot["leads"], services.brain.agents.values()):
        status = lead["status"]
        summary = (lead["error"] if status == "failed" else lead["summary"]) or agent.description
        if status == "skipped":
            summary = f"{lead['reason'] or 'Not routed'} ({lead['team_size']} specialists idle)"
        rows.append((console.paint(lead["title"], Style.BOLD), "L1", console.paint(status, status_color(status)),
                     format_duration(lead["duration_ms"]) if lead["duration_ms"] is not None else "—", summary))
        for index, member in enumerate(lead["members"]):
            connector = elbow if index == len(lead["members"]) - 1 else tee
            detail = member["summary"] if member["status"] == "complete" else (member["error"] or member["reason"])
            duration = format_duration(member["duration_ms"]) if member["status"] in {"complete", "failed"} else "—"
            rows.append((f"  {connector}{member['title']}", "L2", console.paint(member["status"],
                         status_color(member["status"])), duration, detail or ""))
    console.table(("AGENT", "LVL", "STATUS", "TIME", "RESULT"), rows, max_widths=(30, 3, 9, 7, 64))


def load_findings(services: Services, repo: dict[str, Any], *, verify: bool = True) -> list[dict[str, Any]]:
    if verify:
        findings = reverify_findings(services.store, repo["id"], Path(repo["path"]))
    else:
        findings = services.store.list_findings(repo["id"])
    return sort_findings(findings)


def _finding_status(console: Console, finding: dict[str, Any]) -> str:
    status = finding["status"].upper()
    return console.paint(status, Style.BOLD, status_color(finding["status"]))


def print_findings(
    console: Console,
    findings: list[dict[str, Any]],
    *,
    category: str = "all",
    only_status: str = "all",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    visible = [
        finding
        for finding in findings
        if (category == "all" or finding["category"] == category)
        and (only_status == "all" or finding["status"] == only_status or (only_status == "stale" and finding["status"] != "verified"))
    ]
    if limit:
        visible = visible[:limit]
    console.heading("Verified findings", f"{sum(f['status'] == 'verified' for f in visible)}/{len(visible)} current citations verified")
    if not visible:
        console.write(console.paint("No findings match this filter.", Style.DIM))
        return visible
    rows = []
    for number, finding in enumerate(visible, start=1):
        loc = f"{finding['file']}:{finding['line']}"
        rows.append((number, console.paint(finding["severity"].upper(), status_color(finding["status"]) if finding["status"] != "verified" else
                                          (Style.BRIGHT_RED if finding["severity"] in {"critical", "high"} else Style.BRIGHT_YELLOW)),
                     _finding_status(console, finding), finding["title"], loc))
    console.table(("#", "SEVERITY", "SOURCE", "FINDING", "LOCATION"), rows, max_widths=(4, 9, 11, 42, 38))
    console.write()
    console.write(self_hint(console, "Use `show N` for evidence and source, or `source PATH LINE`."))
    return visible


def self_hint(console: Console, value: str) -> str:
    return console.paint(value, Style.DIM)


def print_finding(console: Console, finding: dict[str, Any], number: int | None = None) -> None:
    prefix = f"Finding {number}: " if number else ""
    console.heading(f"{prefix}{finding['title']}", f"{finding['severity'].upper()} · {_finding_status(console, finding)}")
    console.key_value("Location", f"{finding['file']}:{finding['line']}", indent=2)
    console.key_value("Agent", f"{finding['agent']} ({finding['source']})", indent=2)
    console.key_value("Confidence", f"{round(finding['confidence'] * 100)}%", indent=2)
    if finding.get("rule_id"):
        console.key_value("Rule", f"{finding['rule_id']}{' · ' + finding['extra'].get('cwe') if finding['extra'].get('cwe') else ''}", indent=2)
    console.write()
    console.wrapped(finding["description"], indent=2)
    if finding.get("evidence"):
        console.write()
        console.write(f"  {console.paint(str(finding['line']).rjust(4), Style.DIM)}  "
                      f"{console.paint(finding['evidence'], Style.BRIGHT_CYAN)}")
    locations = finding["extra"].get("locations") or []
    if locations:
        console.write()
        console.write(console.paint("  Locations", Style.BOLD))
        for location in locations:
            star = "★" if location.get("role") == "canonical" and console.unicode else ("*" if location.get("role") == "canonical" else "-")
            label = " (already implemented)" if location.get("role") == "canonical" else ""
            console.write(f"    {console.paint(star, Style.BRIGHT_YELLOW)} {location['file']}:{location['line']} "
                          f"{location.get('symbol') or ''}{label}")
    annotations = finding["extra"].get("annotations") or []
    for annotation in annotations:
        console.info(annotation)
    console.write()
    console.write(console.paint("  Recommendation", Style.BOLD, Style.BRIGHT_GREEN))
    console.wrapped(finding["recommendation"], indent=2)
    verification = finding.get("verification") or {}
    console.write()
    console.key_value("SHA-256 (cited lines)", f"{short_hash(finding['sha256'], 16)}…", indent=2)
    console.key_value("Verification", verification.get("message", finding["status"]), indent=2)


def print_source(console: Console, source: SourceDocument, *, line_start: int = 1, line_end: int | None = None,
                 context: int = 6) -> None:
    line_start = max(1, line_start)
    line_end = max(line_start, line_end or line_start)
    first = max(1, line_start - context)
    last = min(len(source.lines), line_end + context)
    console.heading(source.record.path, f"{source.record.language} · {len(source.lines)} lines · "
                    f"SHA-256 {short_hash(source.sha256_current, 16)}…")
    if source.changed:
        console.warning(f"File changed since indexing (indexed {short_hash(source.record.sha256)}…, current {short_hash(source.sha256_current)}…).")
    for number in range(first, last + 1):
        active = line_start <= number <= line_end
        marker = ">" if active else " "
        number_text = str(number).rjust(len(str(last)))
        content = source.lines[number - 1]
        if active:
            console.write(console.paint(f"{marker} {number_text} │ {content}", Style.BOLD, Style.BRIGHT_CYAN))
        else:
            console.write(f"{console.paint(f'{marker} {number_text} │', Style.DIM)} {content}")


def answer_trace_line(console: Console, trace: list[dict[str, Any]]) -> str:
    """Compact Brain → Q&A sub-agent pipeline: parallel steps are joined with ∥."""
    arrow, parallel = console.glyph(" → ", " > "), console.glyph(" ∥ ", " || ")
    groups: list[list[dict[str, Any]]] = []
    for step in trace:
        if step.get("parallel") and groups and groups[-1][0].get("parallel"):
            groups[-1].append(step)
        else:
            groups.append([step])
    total = sum(max(step.get("duration_ms", 0) for step in group) for group in groups)
    return "Brain" + arrow + arrow.join(parallel.join(step["title"] for step in group) for group in groups) + \
        f" · {total}ms"


def print_answer(console: Console, result: dict[str, Any]) -> None:
    console.heading("Brain answer", f"{result['mode']} retrieval · intent {result['intent']}")
    if result.get("trace"):
        console.wrapped(answer_trace_line(console, result["trace"]), indent=2, subsequent=4, style=Style.DIM)
        console.write()
    console.wrapped(result["answer"], indent=2)
    citations = result.get("citations") or []
    if citations:
        console.write()
        console.write(console.paint("  Sources", Style.BOLD))
        for index, citation in enumerate(citations, start=1):
            status = citation["verification"]["status"]
            loc = citation.get("file") or "Brain memory"
            if citation.get("line_start"):
                loc += f":{citation['line_start']}-{citation['line_end']}"
            symbol = f" · {citation['symbol']}" if citation.get("symbol") else ""
            console.write(
                f"    [{index}] {console.paint(status.upper(), status_color(status))}  {loc}{symbol}"
            )
        verified = result["verification"]
        console.write()
        if verified["total"] and verified["verified"] == verified["total"]:
            console.success(f"{verified['verified']}/{verified['total']} cited sources verified against current source.")
        else:
            console.warning(f"{verified['verified']}/{verified['total']} cited sources verified.")


def print_architecture(console: Console, data: dict[str, Any]) -> None:
    console.heading(data.get("project_name", "Architecture"), data.get("summary_source", "source analysis"))
    console.wrapped(data.get("summary", "No architecture summary available."), indent=2)
    stack = data.get("architecture") or {}
    if stack:
        console.write()
        console.write(console.paint("  Stack", Style.BOLD))
        for layer, value in stack.items():
            evidence = value.get("evidence") or []
            cited = f" ({evidence[0]['file']}:{evidence[0]['line_start']})" if evidence else ""
            console.write(f"    {console.paint(layer.ljust(16), Style.MAGENTA)} {value['value']}{console.paint(cited, Style.DIM)}")
    flows = data.get("request_flows") or []
    if flows:
        console.write()
        console.write(console.paint("  Request flows", Style.BOLD))
        for flow in flows:
            labels = " → ".join(step["label"] for step in flow["steps"])
            console.wrapped(f"{flow['name']}: {labels}", indent=4, subsequent=6)
    important = data.get("important_files") or []
    if important:
        console.write()
        console.write(console.paint("  Important files", Style.BOLD))
        for item in important[:8]:
            console.write(f"    {console.paint(item['file'], Style.BRIGHT_CYAN)} — {item['reason']}")


def print_walkthrough(console: Console, data: dict[str, Any]) -> None:
    steps = data.get("steps") or []
    console.heading("Walkthrough", f"{len(steps)} steps · {data.get('total_duration', 0)} seconds")
    console.wrapped(data.get("question", "Presentation-ready repository tour"), indent=2, style=Style.DIM)
    for step in steps:
        console.write()
        duration = f"({step['duration']}s)"
        console.write(
            f"  {console.paint(str(step['index']).rjust(2), Style.BOLD, Style.BRIGHT_MAGENTA)}  "
            f"{console.paint(step['title'], Style.BOLD)} {console.paint(duration, Style.DIM)}"
        )
        console.wrapped(step.get("narration", ""), indent=6)
        for point in step.get("talking_points", []):
            console.wrapped(f"• {point}", indent=6, subsequent=8)
        citations = step.get("citations") or []
        if citations:
            refs = ", ".join(f"{c['file']}:{c['line_start']}" for c in citations)
            console.wrapped(f"Show: {refs}", indent=6, style=Style.BRIGHT_CYAN)


def print_timeline(console: Console, repo: dict[str, Any], limit: int = 30, *, level: int | None = None) -> None:
    """Last ``limit`` orchestration events; ``level`` keeps only events up to that depth (0, 1 or 2)."""
    events = repo.get("timeline") or []
    if level is not None:
        events = [event for event in events if int(event.get("depth") or 0) <= level]
    events = events[-limit:]
    scope = {0: "Brain only", 1: "Brain + lead agents"}.get(level if level is not None else -1, "all levels")
    console.heading("Brain log", f"Last {len(events)} orchestration events · {scope} (L0 Brain · L1 lead · L2 sub-agent)")
    for event in events:
        console.write(format_event(console, event, truncate=console.is_tty))
