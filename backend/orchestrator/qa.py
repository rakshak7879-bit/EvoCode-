"""Memory-powered question answering with verified citations.

Retrieval (FTS5) finds code/doc passages plus stored findings and insights.
Every cited passage is re-verified against the current working copy before it
is returned. With an LLM, the answer may only cite retrieved sources (invalid
citation numbers are dropped); without one, an extractive answer is composed
deterministically and labeled as local retrieval.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from llm.prompts import number_lines, redact_secrets, wrap_repository_content
from llm.provider import LLMError, LLMProvider
from memory.search import MemorySearch, QueryPlan, SearchHit
from memory.store import MemoryStore
from memory.tokens import split_identifier
from orchestrator.router import AgentRouter
from verification.citations import SourceVerifier, VerificationStatus

CODE_TYPES = ("symbol", "code", "doc", "config")
INSIGHT_TYPES = ("finding", "insight", "history")
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_FUNCTION_NAME = re.compile(
    r"(?:function\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(|def\s+(\w+))"
)
_ROUTE = re.compile(r"""\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['"`]([^'"`]+)""")
_INTERESTING_LINE = re.compile(r"[(=]|\bfunction\b|\bdef\b|\bclass\b|=>")
_IMPORT_LINE = re.compile(r"\b(require|import)\b|module\.exports|^\s*export\s*\{")
_DEFINITION_LINE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|def|class)\b|\b(?:app|router)\.(?:get|post|put|patch|delete)\("
)

QA_SYSTEM = (
    "Answer the developer's question using ONLY the numbered sources. Cite sources inline like [1]. Mention file "
    "paths and line numbers. If the sources do not answer the question, say so plainly. "
    'Return JSON: {"answer": str, "citations": [int]}'
)


def _trace_step(agent: str, title: str, started: float, detail: str, *, parallel: bool = False,
                mode: str = "local", duration_ms: int | None = None) -> dict[str, Any]:
    return {
        "agent": agent,
        "title": title,
        "status": "complete",
        "detail": detail,
        "mode": mode,
        "parallel": parallel,
        "duration_ms": duration_ms if duration_ms is not None else int((time.monotonic() - started) * 1000),
    }


def _first_sentence(text: str, limit: int = 220) -> str:
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def _relevance(name: str, plan: QueryPlan) -> int:
    parts = split_identifier(name) or re.findall(r"[a-z]+", name.lower())
    score = 0
    for term in plan.all_terms:
        if len(term) < 3:
            continue
        if any(part.startswith(term[:4]) or (len(part) >= 3 and term.startswith(part)) for part in parts):
            score += 2 if term in plan.terms else 1
    return score


def _symbols_in(hit: SearchHit, plan: QueryPlan) -> list[str]:
    if hit.symbol and hit.memory_type == "symbol":
        return [hit.symbol]
    names: list[str] = []
    for match in _FUNCTION_NAME.finditer(hit.content):
        name = next(group for group in match.groups() if group)
        if name not in names:
            names.append(name)
    for match in _ROUTE.finditer(hit.content):
        route = f"{match.group(1).upper()} {match.group(2)}"
        if route not in names:
            names.append(route)
    return sorted(names, key=lambda n: -_relevance(n, plan))


def _key_lines(plan: QueryPlan, hits: list[SearchHit], limit: int = 3) -> list[tuple[str, int, str]]:
    scored: list[tuple[float, str, int, str]] = []
    primary = set(plan.terms)
    for rank, hit in enumerate(hits[:4]):
        if hit.line_start is None or hit.memory_type not in {"symbol", "code"}:
            continue
        for offset, line in enumerate(hit.content.split("\n")):
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "#", "*")):
                continue
            lowered = line.lower()
            identifier_hits = [i for i in plan.identifiers if i in line]
            if plan.identifiers and not identifier_hits:
                continue  # exact-identifier question: only lines that contain the identifier
            matched = [t for t in plan.all_terms if len(t) > 2 and t in lowered]
            if not (matched or identifier_hits) or not _INTERESTING_LINE.search(line):
                continue
            score = len(matched) + sum(1 for t in matched if t in primary) - rank * 0.3 + 4 * len(identifier_hits)
            if _IMPORT_LINE.search(line):
                score -= 1.5
            if _DEFINITION_LINE.search(line):
                score += 0.5
            if any(re.search(rf"\b{re.escape(i)}\s*=(?!=)", line) for i in identifier_hits):
                score += 2
            scored.append((score, hit.path or "", hit.line_start + offset, stripped))
    scored.sort(key=lambda item: item[0], reverse=True)
    seen: set[tuple[str, int]] = set()
    picked = []
    for _, path, number, text in scored:
        if (path, number) in seen:
            continue
        seen.add((path, number))
        text = redact_secrets(text)
        picked.append((path, number, text if len(text) <= 140 else text[:139] + "…"))
        if len(picked) >= limit:
            break
    return picked


def _identifier_occurrences(identifier: str, hits: list[SearchHit]) -> dict[str, list[int]]:
    occurrences: dict[str, list[int]] = {}
    for hit in hits:
        if hit.line_start is None or not hit.path:
            continue
        for offset, line in enumerate(hit.content.split("\n")):
            if identifier in line:
                numbers = occurrences.setdefault(hit.path, [])
                if hit.line_start + offset not in numbers:
                    numbers.append(hit.line_start + offset)
    return {path: sorted(numbers) for path, numbers in occurrences.items()}


def _findings_summary(intent: str, plan: QueryPlan, findings: list[dict[str, Any]]) -> list[str]:
    if intent == "security":
        items = sorted((f for f in findings if f["category"] == "security"),
                       key=lambda f: (SEVERITY_RANK.get(f["severity"], 9), f["file"], f["line"]))
        if not items:
            return []
        counts = Counter(f["severity"] for f in items)
        breakdown = ", ".join(f"{counts[s]} {s}" for s in SEVERITY_RANK if counts.get(s))
        lines = [f"Evo Code found {len(items)} security finding(s) ({breakdown}), each tied to an exact source line:"]
        for number, f in enumerate(items[:6], start=1):
            lines.append(f"{number}. {f['severity'].upper()} · {f['title']} — {f['file']}:{f['line']} ({f['status']})")
        if len(items) > 6:
            lines.append(f"…and {len(items) - 6} more in the Findings tab.")
        return lines
    if intent == "duplicate":
        items = [f for f in findings if f["category"] == "duplicate"]
        if not items:
            return []

        def relevance(f: dict[str, Any]) -> int:
            names = " ".join([f["title"], *(l.get("symbol") or "" for l in f["extra"].get("locations", []))])
            return sum(1 for term in plan.terms if len(term) > 2 and term in names.lower())

        items.sort(key=relevance, reverse=True)
        lines: list[str] = []
        top = items[0]
        canonical = next((l for l in top["extra"].get("locations", []) if l.get("role") == "canonical"), None)
        if canonical and relevance(top) > 0:
            copies = len(top["extra"]["locations"]) - 1
            lines.append(f"Yes. `{canonical['symbol']}` in {canonical['file']}:{canonical['line']} already implements "
                         f"this, and {copies} other cop{'y' if copies == 1 else 'ies'} exist ({top['title']}).")
        lines.append(f"Evo Code found {len(items)} duplicate cluster(s):")
        for number, f in enumerate(items[:5], start=1):
            existing = next((l for l in f["extra"].get("locations", []) if l.get("role") == "canonical"), None)
            text = f"{number}. {f['title']}"
            if existing:
                text += f" — reuse `{existing['symbol']}` ({existing['file']}:{existing['line']})"
            lines.append(text)
        return lines
    return []


def compose_local_answer(plan: QueryPlan, intent: str, code_hits: list[SearchHit], insight_hits: list[SearchHit],
                         findings: list[dict[str, Any]] | None = None) -> str:
    if not code_hits and not insight_hits:
        return (f'No indexed memory matched "{plan.raw}". Try a file name, a function name or a keyword such as '
                '"auth" or "API_KEY".')
    lines = _findings_summary(intent, plan, findings or [])
    if not lines and intent == "history" and insight_hits:
        lines = [f"Evo Code memory holds {len(insight_hits)} related note(s):"]
        lines.extend(f"{n}. {_first_sentence(h.content)}" for n, h in enumerate(insight_hits[:4], start=1))
    listing = code_hits
    if lines and code_hits:
        lines.extend(["", "Relevant source:"])
    elif not lines:
        identifier = plan.identifiers[0] if plan.identifiers else None
        occurrences = _identifier_occurrences(identifier, code_hits) if identifier else {}
        if occurrences:
            lines.append(f"`{identifier}` appears in {len(occurrences)} indexed file(s):")
            for number, (path, numbers) in enumerate(list(occurrences.items())[:4], start=1):
                lines.append(f"{number}. {path} — line{'s' if len(numbers) > 1 else ''} "
                             f"{', '.join(str(n) for n in numbers[:6])}")
            listing = []  # the occurrence list already names every source
        else:
            subject = plan.subject[:1].upper() + plan.subject[1:]
            asks_location = intent == "location" or plan.raw.lower().startswith(("where", "which"))
            lines.append(f"{subject} is implemented primarily in:" if asks_location
                         else f'The most relevant code for "{plan.raw}" is in:')
    groups: dict[str, list[SearchHit]] = {}
    for hit in listing:
        groups.setdefault(hit.path or "(memory)", []).append(hit)
    for number, (path, hits) in enumerate(list(groups.items())[:3], start=1):
        names: list[str] = []
        for hit in hits:
            for name in _symbols_in(hit, plan):
                if name not in names:
                    names.append(name)
        names.sort(key=lambda n: -_relevance(n, plan))
        ranges = ", ".join(f"{h.line_start}-{h.line_end}" for h in hits[:2] if h.line_start)
        detail = ", ".join(f"`{name}`" for name in names[:3]) if names else "relevant passage"
        lines.append(f"{number}. {path} — {detail} (lines {ranges})")
    key_lines = _key_lines(plan, code_hits)
    if key_lines:
        lines.extend(["", "Key lines:"])
        lines.extend(f"• {path}:{number} — {text}" for path, number, text in key_lines)
    if insight_hits and intent not in {"security", "duplicate", "history"}:
        lines.extend(["", f"Related memory: {_first_sentence(insight_hits[0].content)}"])
    return "\n".join(lines)


class QuestionAnswerer:
    def __init__(self, store: MemoryStore, search: MemorySearch, llm: LLMProvider, router: AgentRouter) -> None:
        self.store = store
        self.search = search
        self.llm = llm
        self.router = router

    def _citation(self, hit: SearchHit, verifier: SourceVerifier, indexed: dict[str, str]) -> dict[str, Any]:
        snippet_lines = hit.content.split("\n")[:14]
        citation: dict[str, Any] = {
            "memory_id": hit.memory_id,
            "file": hit.path,
            "line_start": hit.line_start,
            "line_end": hit.line_end,
            "symbol": hit.symbol,
            "memory_type": hit.memory_type,
            "score": round(hit.score, 2),
            "matched": hit.matched[:8],
            "snippet": redact_secrets("\n".join(snippet_lines)),
            "snippet_truncated": len(hit.content.split("\n")) > len(snippet_lines),
            "sha256": hit.sha256,
        }
        if hit.path and hit.line_start:
            result = verifier.verify(hit.path, hit.line_start, hit.line_end, hit.sha256, indexed.get(hit.path))
            citation["verification"] = {"status": str(result.status), "message": result.message,
                                        "file_sha256_current": result.file_sha256_current}
        else:
            citation["verification"] = {"status": "derived",
                                        "message": "Derived insight stored by the Brain (built from verified claims)."}
        return citation

    async def answer(self, repo_id: str, question: str, limit: int = 6) -> dict[str, Any]:
        """Brain-orchestrated answer: Intent Classifier → Memory Retriever ∥ Insight Recall →
        Citation Verifier → Answer Composer. Each step is recorded in ``trace``."""
        repo = self.store.get_repository(repo_id)
        if repo is None:
            raise KeyError(repo_id)
        trace: list[dict[str, Any]] = []
        started = time.monotonic()
        intent = self.router.classify_question(question)
        trace.append(_trace_step("intent", "Intent Classifier", started, f"intent: {intent}"))

        async def timed_search(**kwargs: Any) -> tuple[tuple[QueryPlan, list[SearchHit]], float, int]:
            begun = time.monotonic()
            found = await asyncio.to_thread(self.search.search, repo_id, question, **kwargs)
            return found, begun, int((time.monotonic() - begun) * 1000)

        # Code retrieval and insight recall are independent: the Brain runs them in parallel.
        (code_result, code_started, code_ms), (insight_result, insight_started, insight_ms) = await asyncio.gather(
            timed_search(limit=limit, memory_types=CODE_TYPES),
            timed_search(limit=4, memory_types=INSIGHT_TYPES, per_file=2),
        )
        plan, code_hits = code_result
        _, insight_hits = insight_result
        engine = "FTS5" if self.store.db.fts5_enabled else "LIKE fallback"
        trace.append(_trace_step("retriever", "Memory Retriever", code_started,
                                 f"{len(code_hits)} code/doc passage(s) via {engine}", parallel=True,
                                 duration_ms=code_ms))
        trace.append(_trace_step("recall", "Insight Recall", insight_started,
                                 f"{len(insight_hits)} finding/insight/history memories", parallel=True,
                                 duration_ms=insight_ms))

        step_started = time.monotonic()
        verifier = SourceVerifier(Path(repo["path"]))
        indexed = {f.path: f.sha256 for f in self.store.list_files(repo_id)}
        citations = [self._citation(hit, verifier, indexed) for hit in code_hits]
        related = [self._citation(hit, verifier, indexed) for hit in insight_hits]
        checked = [c for c in citations if c["verification"]["status"] != "derived"]
        verified = sum(1 for c in checked if c["verification"]["status"] == VerificationStatus.VERIFIED)
        trace.append(_trace_step("verifier", "Citation Verifier", step_started,
                                 f"{verified}/{len(checked)} cited source(s) verified (SHA-256)"))

        step_started = time.monotonic()
        mode = "local"
        notes: list[str] = []
        answer: str | None = None
        if self.llm.available and (code_hits or insight_hits):
            try:
                answer, cited = await self._llm_answer(question, [*code_hits, *insight_hits])
                mode = "llm"
                everything = [*citations, *related]
                for number in cited:
                    everything[number - 1]["cited_by_answer"] = True
            except LLMError as exc:
                notes.append(f"LLM unavailable ({exc}); answered with local retrieval.")
        if answer is None:
            findings = self.store.list_findings(repo_id) if intent in {"security", "duplicate"} else []
            answer = compose_local_answer(plan, intent, code_hits, insight_hits, findings)
        trace.append(_trace_step("composer", "Answer Composer", step_started,
                                 "LLM answer restricted to retrieved sources" if mode == "llm"
                                 else "extractive answer from verified memory (local)", mode=mode))
        return {
            "repository_id": repo_id,
            "query": question,
            "intent": intent,
            "mode": mode,
            "answer": answer,
            "terms": list(plan.all_terms),
            "citations": citations,
            "related": related,
            "verification": {"verified": verified, "total": len(checked)},
            "notes": notes,
            "llm": self.llm.describe(),
            "trace": trace,
        }

    async def _llm_answer(self, question: str, hits: list[SearchHit]) -> tuple[str, list[int]]:
        blocks = []
        for number, hit in enumerate(hits, start=1):
            header = f"[{number}] {hit.path or 'memory'}"
            if hit.line_start:
                header += f":{hit.line_start}-{hit.line_end}"
            if hit.symbol:
                header += f" ({hit.symbol})"
            body = redact_secrets(number_lines(hit.content.split("\n"), hit.line_start or 1))
            blocks.append((f"{header} type={hit.memory_type}", body))
        result = await self.llm.complete_json(
            system=QA_SYSTEM, prompt=f"Question: {question}\n\n{wrap_repository_content(blocks)}", max_tokens=700
        )
        answer = str(result.get("answer") or "").strip()
        if not answer:
            raise LLMError("empty answer")
        cited = []
        for value in result.get("citations") or []:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= number <= len(hits) and number not in cited:
                cited.append(number)
        return redact_secrets(answer[:3000]), cited
