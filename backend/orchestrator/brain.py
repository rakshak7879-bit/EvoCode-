"""The Evo Code Brain: central orchestrator.

Steps: understand the task -> load repository context and memory -> route to
specialist agents -> execute them (isolated, failure-tolerant) -> aggregate ->
cross-validate -> verify against source (SHA-256) -> write verified
intelligence back to memory. Agents never talk to each other; the Brain passes
upstream results explicitly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agents.base import AgentResult, BaseAgent
from agents.context import AgentContext
from config import Settings
from llm.provider import LLMProvider
from memory.search import MemorySearch
from memory.store import MemoryRow, MemoryStore
from memory.tokens import expand_terms
from orchestrator.context_builder import ContextBuilder
from orchestrator.crossval import cross_validate
from orchestrator.evidence import anchor_citation, build_records, explainer_citations, walkthrough_citations
from orchestrator.progress import ProgressReporter
from orchestrator.qa import QuestionAnswerer
from orchestrator.router import DEFAULT_TASK, AgentRouter, TaskPlan
from repo.text import sha256_text
from verification.citations import SourceVerifier

logger = logging.getLogger("evo.brain")
INSIGHT_TYPES = ("finding", "insight", "history")


class Brain:
    def __init__(self, store: MemoryStore, search: MemorySearch, llm: LLMProvider, agents: Sequence[BaseAgent],
                 settings: Settings) -> None:
        self.store = store
        self.search = search
        self.llm = llm
        self.settings = settings
        self.agents: dict[str, BaseAgent] = {agent.name: agent for agent in agents}
        self.router = AgentRouter(list(self.agents), {agent.name: agent.requires for agent in agents})
        self.context_builder = ContextBuilder(store, search, llm)
        self.qa = QuestionAnswerer(store, search, llm, self.router)

    # ------------------------------------------------------------------ analysis
    async def analyze(self, repo_id: str, progress: ProgressReporter) -> dict[str, Any]:
        repo = self.store.get_repository(repo_id)
        if repo is None:
            raise KeyError(repo_id)
        root = Path(repo["path"])
        task = repo.get("task") or DEFAULT_TASK

        await progress.stage("understanding", "thinking", f'Understanding task: "{task}"')
        plan = self.router.plan(task)
        progress.log(f"Intents detected: {', '.join(plan.intents) or 'none, running full analysis'}")

        await progress.stage("retrieving", "retrieving", "Loading repository context and retrieving memory")
        context, summary = await asyncio.to_thread(self.context_builder.build, repo_id, repo["name"], root, task)
        progress.log(
            f"Context loaded: {summary.files} files, README {summary.readme or 'not found'}, "
            f"{len(summary.manifests)} manifest(s), {summary.memory_entries} memory entries"
        )
        focus = summary.focus.get("security", ())[:3]
        if focus:
            progress.log(f"Memory retrieval ranked security-relevant files: {', '.join(focus)}")
        if summary.prior_insights:
            progress.log(f"Recalled {summary.prior_insights} insight(s) from the previous analysis")
        prior_findings = {f["fingerprint"]: f for f in self.store.list_findings(repo_id)}

        await progress.stage("routing", "routing", f"Routing work to {len(plan.agents)} specialist agent(s)")
        for item in plan.agents:
            progress.agent_queued(item.name, item.reason)
            progress.log(f"Route → {self.agents[item.name].title}: {item.reason}", agent=item.name)
        for item in plan.skipped:
            progress.agent_skipped(item.name, item.reason)

        await progress.stage("running", "running", "Specialist agents running", pace=False)
        results = await self._execute(plan, context, progress)

        await progress.stage("aggregating", "thinking", "Aggregating and normalizing agent outputs")
        completed = {name: r for name, r in results.items() if r.status == "complete"}
        drafts = [finding for result in completed.values() for finding in result.findings]
        progress.log(f"{len(drafts)} candidate finding(s) from {len(completed)}/{len(results)} agent(s)")

        await progress.stage("cross_validating", "verifying", "Cross-validating findings across agents")
        verifier = SourceVerifier(root)
        indexed = {f.path: f.sha256 for f in self.store.list_files(repo_id)}
        explainer_data = completed["explainer"].data if "explainer" in completed else {}
        duplicate_data = completed["duplicate"].data if "duplicate" in completed else {}
        accepted, xv = cross_validate(drafts, verifier, indexed, explainer_data, duplicate_data,
                                      lambda path, line: self._symbol_at(context, path, line))
        progress.log(
            f"Cross-validation: {xv['accepted']} accepted, {len(xv['merged'])} merged, "
            f"{len(xv['conflicts'])} conflict(s), {len(xv['unsupported'])} unsupported claim(s) rejected"
        )

        await progress.stage("verifying", "verifying", "Verifying every citation against the SHA-256 index")
        records = build_records(repo_id, accepted, indexed)
        claims_verified, claims_total = self._verify_claims(completed, verifier, indexed, progress)
        verified = sum(1 for r in records if r["status"] == "verified")
        progress.log(
            f"Source verification: {verified}/{len(records)} findings verified · "
            f"{claims_verified}/{claims_total} architecture & walkthrough citations verified",
            level="success" if verified == len(records) else "warning",
        )

        await progress.stage("memorizing", "thinking", "Writing verified intelligence back to memory")
        self.store.replace_findings(repo_id, records)
        history = self._history(prior_findings, records, progress.analysis_no)
        written = self._write_insights(repo_id, records, explainer_data, history, verifier, indexed)
        progress.log(f"Memory updated with {written} insight(s) for future questions")
        if history["resolved"]:
            progress.log(f"{len(history['resolved'])} finding(s) resolved since the previous analysis", level="success")

        return {
            "task": task,
            "plan": {
                "intents": list(plan.intents),
                "full_analysis": plan.full_analysis,
                "agents": [{"name": a.name, "reason": a.reason} for a in plan.agents],
                "skipped": [{"name": a.name, "reason": a.reason} for a in plan.skipped],
            },
            "context": {
                "files": summary.files,
                "readme": summary.readme,
                "manifests": summary.manifests,
                "memory_entries": summary.memory_entries,
                "focus": {k: list(v[:5]) for k, v in summary.focus.items()},
                "drifted_files": summary.drifted_files,
            },
            "cross_validation": xv,
            "verification": {
                "findings_verified": verified,
                "findings_total": len(records),
                "claims_verified": claims_verified,
                "claims_total": claims_total,
            },
            "history": history,
            "llm": self.llm.describe(),
            "agents": {
                name: {"status": r.status, "mode": r.mode, "summary": r.summary, "notes": r.notes,
                       "duration_ms": r.duration_ms, "error": r.error}
                for name, r in results.items()
            },
        }

    async def answer(self, repo_id: str, question: str, limit: int = 6) -> dict[str, Any]:
        return await self.qa.answer(repo_id, question, limit)

    # ------------------------------------------------------------------ execution
    async def _execute(self, plan: TaskPlan, context: AgentContext, progress: ProgressReporter) -> dict[str, AgentResult]:
        planned = [self.agents[item.name] for item in plan.agents]
        first_wave = [agent for agent in planned if not agent.requires]
        second_wave = [agent for agent in planned if agent.requires]
        stagger = self.settings.pacing_ms / 1000 * 0.4

        async def delayed(agent: BaseAgent, delay: float) -> AgentResult:
            if delay:
                await asyncio.sleep(delay)
            return await self._run_agent(agent, context, progress)

        results: dict[str, AgentResult] = {}
        outputs = await asyncio.gather(*(delayed(agent, i * stagger) for i, agent in enumerate(first_wave)))
        for agent, output in zip(first_wave, outputs):
            results[agent.name] = output
        for agent in second_wave:
            upstream = {name: results[name] for name in (*agent.requires, *agent.consumes) if name in results}
            missing = [name for name in agent.requires if name not in results or results[name].status != "complete"]
            if missing:
                progress.log(f"{agent.title}: upstream {', '.join(missing)} unavailable, running degraded",
                             level="warning", agent=agent.name)
            else:
                progress.log(f"Brain passes {', '.join(upstream)} results to the {agent.title}", agent=agent.name)
            results[agent.name] = await self._run_agent(agent, context.with_upstream(upstream), progress)
        return results

    async def _run_agent(self, agent: BaseAgent, context: AgentContext, progress: ProgressReporter) -> AgentResult:
        progress.agent_running(agent.name)
        started = time.monotonic()
        try:
            if agent.name in self.settings.simulate_agent_failure:
                raise RuntimeError("Simulated failure (EVO_SIMULATE_AGENT_FAILURE)")
            raw = await asyncio.wait_for(agent.run(context), timeout=self.settings.agent_timeout_seconds)
            result = raw if isinstance(raw, AgentResult) else AgentResult.model_validate(raw)
        except asyncio.TimeoutError:
            result = AgentResult(agent=agent.name, status="failed",
                                 error=f"Timed out after {self.settings.agent_timeout_seconds:.0f}s")
        except Exception as exc:  # one agent failing must never break the analysis
            logger.exception("Agent failed", extra={"agent": agent.name, "repository_id": context.repository_id})
            result = AgentResult(agent=agent.name, status="failed", error=f"{exc.__class__.__name__}: {exc}")
        minimum = self.settings.pacing_ms * 2.5 / 1000
        elapsed = time.monotonic() - started
        if elapsed < minimum:
            await asyncio.sleep(minimum - elapsed)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        summary = result.summary if result.status == "complete" else "Failed. The rest of the analysis is still available."
        progress.agent_finished(agent.name, status=result.status, mode=result.mode, summary=summary,
                                result=result.model_dump(mode="json"), error=result.error)
        return result

    # ------------------------------------------------------------------ verification helpers
    @staticmethod
    def _symbol_at(context: AgentContext, path: str, line: int):  # type: ignore[no-untyped-def]
        file = context.file(path)
        return file.parsed.symbol_at(line) if file and file.parsed else None

    def _verify_claims(self, completed: dict[str, AgentResult], verifier: SourceVerifier, indexed: dict[str, str],
                       progress: ProgressReporter) -> tuple[int, int]:
        verified = total = 0
        for name, collect in (("explainer", explainer_citations), ("walkthrough", walkthrough_citations)):
            result = completed.get(name)
            if result is None:
                continue
            citations = collect(result.data)
            for citation in citations:
                total += 1
                verified += anchor_citation(citation, verifier, indexed)
            progress.update_agent_result(name, result.model_dump(mode="json"))
        return verified, total

    @staticmethod
    def _history(prior: dict[str, dict[str, Any]], records: list[dict[str, Any]], analysis_no: int) -> dict[str, Any]:
        current = {r["fingerprint"] for r in records}
        resolved = [
            {"title": f["title"], "file": f["file"], "line": f["line"], "severity": f["severity"],
             "category": f["category"]}
            for fp, f in prior.items() if fp not in current
        ]
        new = [r for r in records if r["fingerprint"] not in prior] if prior else []
        return {
            "analysis_no": analysis_no,
            "previous_total": len(prior),
            "resolved": resolved,
            "new": [{"title": r["title"], "file": r["file"], "line": r["line"]} for r in new],
            "unchanged": len(records) - len(new) if prior else 0,
        }

    def _write_insights(self, repo_id: str, records: list[dict[str, Any]], explainer: dict[str, Any],
                        history: dict[str, Any], verifier: SourceVerifier, indexed: dict[str, str]) -> int:
        file_ids = {f.path: f.id for f in self.store.list_files(repo_id)}
        rows: list[MemoryRow] = []
        for record in records:
            content = (
                f"{record['category'].capitalize()} finding ({record['severity']}): {record['title']} at "
                f"{record['file']}:{record['line']}. {record['description']} "
                f"Recommendation: {record['recommendation']}"
            )
            annotations = record["extra"].get("annotations") or []
            if annotations:
                content += " Notes: " + " ".join(annotations)
            rows.append(MemoryRow(file_ids.get(record["file"]), record["file"], content, "finding", record["title"],
                                  record["line"], record["line_end"], record["sha256"], expand_terms(content)))
        if explainer:
            stack = ", ".join(f"{layer}: {info['value']}" for layer, info in explainer.get("architecture", {}).items())
            flows = "; ".join(
                f"{flow['name']}: " + " → ".join(step["label"] for step in flow["steps"])
                for flow in explainer.get("request_flows", [])
            )
            content = f"Architecture insight: {explainer.get('summary', '')} Stack: {stack}. Request flows: {flows}"
            rows.append(MemoryRow(None, None, content, "insight", "Architecture summary", None, None,
                                  sha256_text(content), expand_terms(content)))
            for note in explainer.get("history", []):
                source = note["source"]
                anchor = verifier.anchor(source["file"], source["line_start"], source["line_end"], None,
                                         indexed.get(source["file"]))
                content = f"Engineering history: {note['note']}"
                rows.append(MemoryRow(file_ids.get(source["file"]), source["file"], content, "history", "history",
                                      source["line_start"], source["line_end"], anchor.evidence_sha256,
                                      expand_terms(content)))
        for item in history["resolved"]:
            content = (f"Resolved since the previous analysis: {item['title']} in {item['file']} "
                       f"(previously line {item['line']}).")
            rows.append(MemoryRow(None, item["file"], content, "history", "resolved", None, None,
                                  sha256_text(content), expand_terms(content)))
        self.store.delete_memories(repo_id, INSIGHT_TYPES)
        return self.store.insert_memories(repo_id, rows)
