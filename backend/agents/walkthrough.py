"""Walkthrough Agent: a presentation-ready, ~2 minute tour of the repository.

Answers: "If I had 2 minutes to explain this repository to a judge, what
would I show?" It consumes the Explainer, Security and Duplicate results that
the Brain passes in (agents never talk to each other directly). Every step
cites source lines, which the Brain verifies.
"""

from __future__ import annotations

from typing import Any

from agents.architecture import AUTH_NAME
from agents.base import SEVERITY_ORDER, AgentResult, BaseAgent, FindingDraft
from agents.context import AgentContext
from llm.provider import LLMError

TARGET_SECONDS = 120
BASE_DURATIONS = {
    "overview": 15, "architecture": 15, "flow": 20, "auth": 15, "security": 20,
    "duplicates": 15, "history": 10, "next": 10,
}


def _citation(file: str, start: int, end: int | None = None, label: str | None = None) -> dict[str, Any]:
    return {"file": file, "line_start": max(1, start), "line_end": max(1, end or start), "label": label}


def _scale_durations(steps: list[dict[str, Any]]) -> None:
    """Fit a full walkthrough into ~2 minutes; short walkthroughs are never stretched."""
    total = sum(step["duration"] for step in steps) or 1
    if len(steps) < 5 and total <= TARGET_SECONDS:
        return
    for step in steps:
        step["duration"] = max(5, int(round(step["duration"] * TARGET_SECONDS / total / 5.0)) * 5)
    drift = TARGET_SECONDS - sum(step["duration"] for step in steps)
    if steps and drift:
        longest = max(steps, key=lambda s: s["duration"])
        longest["duration"] = max(5, longest["duration"] + drift)


def build_steps(context: AgentContext, explainer: dict[str, Any], security: list[FindingDraft],
                duplicates: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    readme = context.readme
    stack = explainer.get("architecture", {})

    overview_citations = []
    if explainer.get("summary_citation"):
        c = explainer["summary_citation"]
        overview_citations.append(_citation(c["file"], c["line_start"], c["line_end"], "Project intro"))
    elif readme:
        overview_citations.append(_citation(readme.path, 1, min(8, max(1, len(readme.lines))), "README"))
    steps.append({
        "key": "overview",
        "title": "Project Overview",
        "files": [c["file"] for c in overview_citations],
        "citations": overview_citations,
        "talking_points": [f"{layer.capitalize()}: {info['value']}" for layer, info in list(stack.items())[:4]],
        "narration": explainer.get("summary") or f"{context.repository_name}: {len(context.files)} indexed files.",
    })

    if stack:
        citations = [
            _citation(e["file"], e["line_start"], e["line_end"], f"{layer}: {e['label']}")
            for layer, info in stack.items() for e in info["evidence"][:1]
        ][:4]
        steps.append({
            "key": "architecture",
            "title": "Architecture",
            "files": list(dict.fromkeys(c["file"] for c in citations)),
            "citations": citations,
            "talking_points": [f"{layer.capitalize()} → {info['value']}" for layer, info in stack.items()],
            "narration": "Each layer of the stack is backed by a dependency declaration or import in the code.",
        })

    flows = explainer.get("request_flows") or []
    if flows:
        flow = flows[0]
        citations = [_citation(s["file"], s["line"], s["line"], f"{s['layer']}: {s['label']}") for s in flow["steps"]][:6]
        steps.append({
            "key": "flow",
            "title": f"Request Flow: {flow['name']}",
            "files": list(dict.fromkeys(c["file"] for c in citations)),
            "citations": citations,
            "talking_points": [" → ".join(s["layer"] for s in flow["steps"])],
            "narration": f"Follow {flow['trigger']} from the UI through the API to where data is read or written.",
        })

    auth_symbols = [s for s in context.symbols if AUTH_NAME.search(s.name) and s.kind != "class"]
    if auth_symbols:
        chosen = sorted(auth_symbols, key=lambda s: (not s.name.lower().startswith(("require", "verify", "auth")),
                                                      s.file))[:3]
        citations = [_citation(s.file, s.line_start, min(s.line_end, s.line_start + 14), f"{s.name}()") for s in chosen]
        steps.append({
            "key": "auth",
            "title": "Authentication",
            "files": list(dict.fromkeys(c["file"] for c in citations)),
            "citations": citations,
            "talking_points": [f"`{s.name}` in {s.file}:{s.line_start}" for s in chosen],
            "narration": "This is where identities are checked and tokens are issued or verified.",
        })

    ranked = sorted(security, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.confidence))[:3]
    if ranked:
        steps.append({
            "key": "security",
            "title": "Security Findings",
            "files": list(dict.fromkeys(f.file for f in ranked)),
            "citations": [_citation(f.file, f.line, f.line_end, f.title) for f in ranked],
            "talking_points": [f"{f.severity.upper()} · {f.title} — {f.file}:{f.line}" for f in ranked],
            "narration": "Every finding points at an exact line and is verified against the current source with SHA-256.",
        })

    clusters = duplicates.get("clusters") or []
    if clusters:
        cluster = clusters[0]
        citations = [_citation(m["file"], m["line_start"], m["line_start"], f"{m['role']}: {m['symbol'] or m['file']}")
                     for m in cluster["members"]][:5]
        steps.append({
            "key": "duplicates",
            "title": "Duplicate Logic",
            "files": list(dict.fromkeys(c["file"] for c in citations)),
            "citations": citations,
            "talking_points": [cluster["title"], cluster["recommendation"]],
            "narration": "The same logic lives in several places; Evo Code points at the copy that already exists.",
        })

    history = explainer.get("history") or []
    if history:
        notes = history[:3]
        steps.append({
            "key": "history",
            "title": "Engineering History",
            "files": list(dict.fromkeys(n["source"]["file"] for n in notes)),
            "citations": [_citation(n["source"]["file"], n["source"]["line_start"], n["source"]["line_end"], "history")
                          for n in notes],
            "talking_points": [n["note"] for n in notes],
            "narration": "Memory of earlier decisions explains why legacy code still exists and what replaced it.",
        })

    actions: list[str] = []
    action_citations = []
    for finding in ranked[:2]:
        actions.append(f"Fix: {finding.title} ({finding.file}:{finding.line}) — {finding.recommendation}")
        action_citations.append(_citation(finding.file, finding.line, finding.line_end, finding.title))
    if clusters:
        actions.append(clusters[0]["recommendation"])
    if history:
        actions.append("Remove or migrate the legacy code documented in the history notes.")
    if actions:
        steps.append({
            "key": "next",
            "title": "Next Actions",
            "files": list(dict.fromkeys(c["file"] for c in action_citations)),
            "citations": action_citations,
            "talking_points": actions[:4],
            "narration": "Verified intelligence turns into a short, prioritized to-do list.",
        })

    for index, step in enumerate(steps, start=1):
        step["index"] = index
        step["duration"] = BASE_DURATIONS.get(step["key"], 10)
    _scale_durations(steps)
    return steps


class WalkthroughAgent(BaseAgent):
    name = "walkthrough"
    title = "Walkthrough Agent"
    description = "Turns the analysis into a presentation-ready two-minute walkthrough with cited code."
    requires = ("explainer",)
    consumes = ("security", "duplicate")

    async def run(self, context: AgentContext) -> AgentResult:
        explainer = context.upstream.get("explainer")
        security = context.upstream.get("security")
        duplicate = context.upstream.get("duplicate")
        notes: list[str] = []
        explainer_data = explainer.data if explainer and explainer.status == "complete" else {}
        if not explainer_data:
            notes.append("Explainer output unavailable; walkthrough built from README and findings only.")
        security_findings = security.findings if security and security.status == "complete" else []
        duplicate_data = duplicate.data if duplicate and duplicate.status == "complete" else {}
        steps = build_steps(context, explainer_data, security_findings, duplicate_data)

        mode = "local"
        if context.llm.available and steps:
            try:
                await self._polish(context, steps)
                mode = "llm"
            except LLMError as exc:
                notes.append(f"LLM narration unavailable ({exc}); using template narration.")
        else:
            notes.append("Template narration (local analysis).")
        total = sum(step["duration"] for step in steps)
        return AgentResult(
            agent=self.name,
            mode=mode,  # type: ignore[arg-type]
            summary=f"{len(steps)} steps generated · {total}s walkthrough",
            data={
                "question": "If I had 2 minutes to explain this repository to a judge, what would I show?",
                "steps": steps,
                "total_duration": total,
                "target_seconds": TARGET_SECONDS,
            },
            notes=notes,
            metrics={"steps": len(steps), "seconds": total},
        )

    async def _polish(self, context: AgentContext, steps: list[dict[str, Any]]) -> None:
        outline = [{"index": s["index"], "title": s["title"], "talking_points": s["talking_points"]} for s in steps]
        system = (
            "Rewrite the narration for each walkthrough step as one or two confident, concrete sentences for a live "
            'demo. Do not invent files or facts. Return JSON: {"steps": [{"index": int, "narration": str}]}'
        )
        result = await context.llm.complete_json(system=system, prompt=f"Walkthrough outline (data): {outline}",
                                                 max_tokens=900)
        by_index = {s["index"]: s for s in steps}
        for item in result.get("steps") or []:
            if isinstance(item, dict) and item.get("index") in by_index and item.get("narration"):
                by_index[item["index"]]["narration"] = str(item["narration"])[:400]
