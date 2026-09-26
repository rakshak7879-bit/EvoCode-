"""Task understanding and agent routing.

Deterministic and explainable: every routing decision carries a reason that
is shown in the dashboard ("Task mentions 'security'").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_TASK = (
    "Analyze this repository for security issues, duplicate logic, architecture and generate a walkthrough."
)

INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "security": ("security", "secure", "vulnerab", "secret", "injection", "xss", "cors", "jwt", "audit", "risk",
                 "credential", "exploit"),
    "duplicate": ("duplicate", "duplicat", "redundan", "copy", "copies", "clone", "dry", "repeated", "similar"),
    "explainer": ("architecture", "explain", "understand", "overview", "structure", "how does", "what does",
                  "summary", "summar", "map "),
    "walkthrough": ("walkthrough", "walk through", "presentation", "present", "demo", "tour", "onboard", "pitch",
                    "judge"),
}
FULL_ANALYSIS_WORDS = ("full analysis", "everything", "analyze this repository", "analyse this repository", "all agents")

QUESTION_INTENTS: dict[str, tuple[str, ...]] = {
    "security": ("security", "vulnerab", "secret", "injection", "risk", "unsafe", "attack", "leak", "api key",
                 "issue", "finding"),
    "duplicate": ("duplicate", "duplicat", "redundan", "copy", "copies", "repeated", "reuse", "already implemented",
                  "already exist"),
    "history": ("history", "legacy", "deprecated", "old ", "previous", "before", "replaced", "changed"),
    "architecture": ("architecture", "stack", "structure", "overview", "how does", "what does", "flow", "built"),
    "location": ("where", "which file", "located", "find ", "defined"),
}


@dataclass(frozen=True)
class AgentPlan:
    name: str
    reason: str


@dataclass(frozen=True)
class TaskPlan:
    task: str
    intents: tuple[str, ...]
    agents: tuple[AgentPlan, ...]
    skipped: tuple[AgentPlan, ...]
    full_analysis: bool

    @property
    def agent_names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.agents)


class AgentRouter:
    def __init__(self, available: Sequence[str], dependencies: dict[str, tuple[str, ...]] | None = None) -> None:
        self.available = tuple(available)
        self.dependencies = dependencies or {}

    def plan(self, task: str | None) -> TaskPlan:
        text = (task or DEFAULT_TASK).strip() or DEFAULT_TASK
        lowered = f" {text.lower()} "
        matched: dict[str, str] = {}
        for intent, keywords in INTENT_KEYWORDS.items():
            if intent not in self.available:
                continue
            hit = next((k for k in keywords if k in lowered), None)
            if hit:
                matched[intent] = f"Task mentions '{hit.strip()}'"
        explicit_full = any(word in lowered for word in FULL_ANALYSIS_WORDS)
        full = not matched or explicit_full
        if not matched:
            selected = {name: "No specific focus detected: full analysis" for name in self.available}
        else:
            selected = dict(matched)
            if explicit_full:
                for name in self.available:
                    selected.setdefault(name, "Full analysis requested")
        for name in list(selected):
            for dependency in self.dependencies.get(name, ()):
                if dependency in self.available and dependency not in selected:
                    selected[dependency] = f"Required by the {name} agent"
        ordered = tuple(AgentPlan(name, selected[name]) for name in self.available if name in selected)
        skipped = tuple(AgentPlan(name, "Not required for this task") for name in self.available if name not in selected)
        return TaskPlan(text, tuple(matched), ordered, skipped, full)

    @staticmethod
    def classify_question(question: str) -> str:
        lowered = f" {question.lower()} "
        for intent in ("security", "duplicate", "history", "architecture", "location"):
            if any(keyword in lowered for keyword in QUESTION_INTENTS[intent]):
                return intent
        return "general"
