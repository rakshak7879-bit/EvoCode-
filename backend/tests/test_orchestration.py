"""Multi-level orchestration: Brain (L0) → lead agents (L1) → specialist sub-agents (L2)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from agents.base import SubAgentRun, SubAgentSpec
from agents.context import AgentContext
from agents.security import SCANNED_KINDS, SCANNER_FAMILIES, SECURITY_TEAM, SecurityAgent, scan_rules
from agents.security_rules import RULES
from agents.team import NO_LLM, NOT_REQUIRED, DelegationRuntime, SubAgentOutput, plan_team, run_team
from tests.conftest import FakeLLM, analyze_demo


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []

    def team_planned(self, parent: str, runs: list[SubAgentRun]) -> None:
        self.events.append(("planned", parent, [r.key for r in runs]))

    def member_started(self, run: SubAgentRun) -> None:
        self.events.append(("started", run.key))

    def member_finished(self, run: SubAgentRun) -> None:
        self.events.append(("finished", run.key, run.status))


def test_team_runs_in_dependency_waves_and_isolates_failures(agent_context: AgentContext) -> None:
    observer = RecordingObserver()
    specs = (
        SubAgentSpec("a", "A", "first wave"),
        SubAgentSpec("b", "B", "first wave, fails"),
        SubAgentSpec("c", "C", "needs A", depends_on=("a",)),
        SubAgentSpec("d", "D", "needs B", depends_on=("b",)),
        SubAgentSpec("e", "E", "tolerates B", uses=("b", "c")),
        SubAgentSpec("llm", "LLM", "needs an LLM", requires_llm=True),
    )
    seen: dict[str, list[str]] = {}

    def a(context: AgentContext, upstream: Any) -> SubAgentOutput:
        return SubAgentOutput(summary="a done", value=1)

    def b(context: AgentContext, upstream: Any) -> SubAgentOutput:
        raise ValueError("boom")

    def c(context: AgentContext, upstream: Any) -> SubAgentOutput:
        seen["c"] = sorted(upstream)
        return SubAgentOutput(summary="c done", value=upstream["a"].value + 1)

    def never(context: AgentContext, upstream: Any) -> SubAgentOutput:
        raise AssertionError("must not run")

    async def e(context: AgentContext, upstream: Any) -> SubAgentOutput:
        seen["e"] = sorted(upstream)
        return SubAgentOutput(summary="e done")

    context = agent_context.with_delegation(DelegationRuntime(observer=observer))
    handlers = {"a": a, "b": b, "c": c, "d": never, "e": e, "llm": never}
    team = run(run_team("lead", plan_team(specs, handlers, context), context))

    runs = {r.key: r for r in team.runs}
    assert [runs[k].wave for k in "abcde"] == [1, 1, 2, 2, 3]
    assert runs["a"].status == runs["c"].status == runs["e"].status == "complete"
    assert runs["b"].status == "failed" and runs["b"].error == "ValueError: boom"
    assert runs["d"].status == "skipped" and "B" in runs["d"].reason
    assert runs["llm"].status == "skipped" and runs["llm"].reason == NO_LLM and runs["llm"].wave == 0
    assert team.value("c") == 2 and seen["c"] == ["a"], "members only receive their declared upstream outputs"
    assert seen["e"] == ["c"], "a failed soft dependency is tolerated"
    assert all(r.name == f"lead.{r.key}" and r.parent == "lead" for r in team.runs)
    notes = team.failure_notes()
    assert any(n.startswith("B unavailable") for n in notes) and any(n.startswith("D skipped") for n in notes)
    assert observer.events[0] == ("planned", "lead", ["a", "b", "c", "d", "e", "llm"])
    assert {e[1] for e in observer.events if e[0] == "finished"} == {"a", "b", "c", "d", "e"}


def test_independent_members_run_concurrently(agent_context: AgentContext) -> None:
    async def slow(context: AgentContext, upstream: Any) -> SubAgentOutput:
        await asyncio.sleep(0.25)
        return SubAgentOutput(summary="done")

    specs = (SubAgentSpec("x", "X", "slow"), SubAgentSpec("y", "Y", "slow"), SubAgentSpec("z", "Z", "slow"))
    started = time.monotonic()
    team = run(run_team("lead", plan_team(specs, {"x": slow, "y": slow, "z": slow}, agent_context), agent_context))
    assert time.monotonic() - started < 0.6
    assert all(r.status == "complete" and r.wave == 1 for r in team.runs)


def test_security_team_matches_the_single_pass_rule_engine(agent_context: AgentContext) -> None:
    files = [f for f in agent_context.files if f.kind in SCANNED_KINDS]
    result = run(SecurityAgent().run(agent_context))
    assert [f.model_dump() for f in result.findings] == [f.model_dump() for f in scan_rules(files)]
    owned = [family for families in SCANNER_FAMILIES.values() for family in families]
    assert sorted(owned) == sorted({rule.family for rule in RULES}) and len(owned) == len(set(owned))
    assert [r.key for r in result.subagents] == [spec.key for spec in SECURITY_TEAM]
    assert result.data["rules_evaluated"] == len(RULES)


def test_focused_task_narrows_the_security_team(agent_context: AgentContext) -> None:
    focused = replace(agent_context, task="Only check for hardcoded secrets")
    result = run(SecurityAgent().run(focused.with_delegation(DelegationRuntime(allow_narrowing=True))))
    runs = {r.key: r for r in result.subagents}
    assert runs["secrets"].status == "complete" and runs["secrets"].reason.startswith("Task mentions")
    assert all(runs[key].status == "skipped" and runs[key].reason == NOT_REQUIRED
               for key in ("injection", "auth", "exposure", "prompt", "llm_review"))
    assert result.findings and all(f.family == "secret" for f in result.findings)
    assert any(note.startswith("Task-focused delegation: 1/6") for note in result.notes)
    # A full analysis (the Brain does not allow narrowing) keeps the whole team for the same text.
    full = run(SecurityAgent().run(focused))
    assert all(r.status == "complete" for r in full.subagents if r.key != "llm_review")


def test_simulated_member_failure_degrades_only_that_member(agent_context: AgentContext) -> None:
    context = agent_context.with_delegation(DelegationRuntime(simulate_failures=frozenset({"security.secrets"})))
    result = run(SecurityAgent().run(context))
    runs = {r.key: r for r in result.subagents}
    assert result.status == "complete" and runs["secrets"].status == "failed"
    assert runs["injection"].status == "complete"
    assert result.findings and not any(f.family == "secret" for f in result.findings)
    assert any(note.startswith("Secrets Scanner unavailable") for note in result.notes)


def test_brain_records_the_three_level_hierarchy(client: TestClient) -> None:
    repo_id = analyze_demo(client)
    status = client.get(f"/api/repository/{repo_id}").json()
    orchestration = status["report"]["orchestration"]
    assert orchestration["levels"] == 3
    assert orchestration["totals"] == {
        "lead_agents": 4, "leads_run": 4, "lead_waves": 2, "subagents": 21,
        "subagents_complete": 17, "subagents_failed": 0, "subagents_skipped": 4,
    }
    leads = {lead["name"]: lead for lead in orchestration["leads"]}
    assert leads["security"]["wave"] == 1 and leads["walkthrough"]["wave"] == 2
    assert {m["key"]: m["wave"] for m in leads["duplicate"]["subagents"]} == {
        "functions": 1, "files": 1, "clones": 2, "reuse": 3, "llm_explain": 0}
    assert {m["key"]: m["wave"] for m in leads["explainer"]["subagents"]}["flows"] == 2

    agents = {a["name"]: a for a in status["agents"]}
    assert len(agents["security"]["subagents"]) == 6 and len(agents["walkthrough"]["subagents"]) == 3
    assert status["agents_total"] == 4, "sub-agents never count as lead agents"
    assert {entry["depth"] for entry in status["timeline"]} == {0, 1, 2}
    assert any(entry["depth"] == 2 and entry["parent"] == "explainer" and entry["message"].startswith("Flow Tracer")
               for entry in status["timeline"])


def test_focused_task_narrows_teams_through_the_brain(client: TestClient) -> None:
    repo_id = analyze_demo(client, task="Only check for hardcoded secrets")
    status = client.get(f"/api/repository/{repo_id}").json()
    assert {a["name"]: a["status"] for a in status["agents"]} == {
        "security": "complete", "duplicate": "skipped", "explainer": "skipped", "walkthrough": "skipped"}
    security = next(a for a in status["agents"] if a["name"] == "security")
    members = {m["key"]: m["status"] for m in security["subagents"]}
    assert members["secrets"] == "complete" and members["injection"] == members["auth"] == "skipped"
    findings = client.get(f"/api/findings/{repo_id}").json()
    assert findings["security"] and all(f["family"] == "secret" for f in findings["security"])


def test_simulated_subagent_failure_keeps_the_analysis_running(client_factory) -> None:  # type: ignore[no-untyped-def]
    client: TestClient = client_factory(simulate_agent_failure=("explainer.flows",))
    repo_id = analyze_demo(client)
    status = client.get(f"/api/repository/{repo_id}").json()
    assert status["status"] == "completed"
    assert all(agent["status"] == "complete" for agent in status["agents"])
    explainer = next(a for a in status["agents"] if a["name"] == "explainer")
    flows = next(m for m in explainer["subagents"] if m["key"] == "flows")
    assert flows["status"] == "failed" and "Simulated failure" in flows["error"]
    assert status["report"]["orchestration"]["totals"]["subagents_failed"] == 1
    assert any(n.startswith("Flow Tracer unavailable") for n in status["report"]["agents"]["explainer"]["notes"])
    walkthrough = client.get(f"/api/walkthrough/{repo_id}").json()
    assert walkthrough["steps"] and all(step["key"] != "flow" for step in walkthrough["steps"])


def test_llm_members_join_each_team_when_an_llm_is_configured(client_factory) -> None:  # type: ignore[no-untyped-def]
    def respond(system: str, prompt: str) -> dict[str, Any]:
        if "security vulnerabilities" in system:
            return {"findings": []}
        if "duplicate cluster" in system:
            return {"clusters": [{"id": "dup-1", "reason": "Both validate emails.", "recommendation": "Reuse."}]}
        if "Answer the developer" in system:
            return {"answer": "See backend/auth.js [1].", "citations": [1]}
        if "summary" in system:
            return {"summary": "ShopLite is a demo store."}
        return {"steps": [{"index": 1, "narration": "Welcome to ShopLite."}]}

    client: TestClient = client_factory(llm=FakeLLM(respond))
    repo_id = analyze_demo(client)
    status = client.get(f"/api/repository/{repo_id}").json()
    assert status["report"]["orchestration"]["totals"]["subagents_complete"] == 21
    for lead, member in (("security", "llm_review"), ("duplicate", "llm_explain"), ("explainer", "llm_summary"),
                         ("walkthrough", "narrator")):
        agent = next(a for a in status["agents"] if a["name"] == lead)
        run_ = next(m for m in agent["subagents"] if m["key"] == member)
        assert run_["status"] == "complete" and run_["mode"] == "llm", (lead, run_)
        assert agent["mode"] == "llm"
    answer = client.post("/api/memory/search", json={"repository_id": repo_id, "query": "Where is auth?"}).json()
    assert [step["agent"] for step in answer["trace"]] == ["intent", "retriever", "recall", "verifier", "composer"]
    assert answer["trace"][-1]["mode"] == "llm"
