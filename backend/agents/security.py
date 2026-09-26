"""Security Agent: finds vulnerabilities and risky patterns with source evidence.

Local mode runs the deterministic rule engine. When an LLM is configured, the
agent additionally asks it to review the riskiest files (secrets redacted).
LLM claims are returned as drafts with ``source="llm"``; the Brain rejects any
claim whose evidence cannot be found at the cited lines.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter

from pydantic import BaseModel, Field, ValidationError

from agents.base import AgentResult, BaseAgent, FindingDraft
from agents.context import AgentContext, SourceFile
from agents.security_rules import RULES, infer_family, is_comment_line
from llm.prompts import number_lines, redact_secrets, wrap_repository_content
from llm.provider import LLMError

MAX_PER_RULE_PER_FILE = 5
MAX_FINDINGS = 200
LLM_FILE_LIMIT = 6
LLM_CHAR_BUDGET = 24_000
_RISK_WORDS = re.compile(
    r"(?i)\b(auth|password|token|secret|jwt|query|exec|eval|cors|req\.body|request\.|cookie|session|crypto|"
    r"upload|admin|payment|card)\b"
)

SECURITY_SYSTEM = """Task: review the repository excerpts for security vulnerabilities that the rule engine may
have missed (logic flaws, missing authorization, missing input validation, insecure defaults).
Return JSON: {"findings": [{"file": str, "line": int, "evidence": str, "severity": "critical|high|medium|low",
"title": str, "description": str, "fix": str, "confidence": float}]}
"evidence" must be copied verbatim from the cited line. Only report issues with concrete evidence.
Do not repeat the already-known findings. Return {"findings": []} if nothing is found."""


class LLMSecurityClaim(BaseModel):
    file: str
    line: int = Field(ge=1)
    evidence: str = Field(min_length=3)
    severity: str = "medium"
    title: str = Field(min_length=3)
    description: str = ""
    fix: str = ""
    confidence: float = 0.6


def scan_file(file: SourceFile) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    per_rule: Counter[str] = Counter()
    applicable = [rule for rule in RULES if rule.applies_to(file.kind, file.language)]
    if not applicable:
        return findings
    for number, line in enumerate(file.lines, start=1):
        if not line.strip() or len(line) > 2000:  # skip blanks and minified lines
            continue
        comment = is_comment_line(line, file.language)
        for rule in applicable:
            if rule.skip_comments and comment:
                continue
            if per_rule[rule.id] >= MAX_PER_RULE_PER_FILE:
                continue
            matched, match = rule.evaluate(line)
            if not matched:
                continue
            per_rule[rule.id] += 1
            findings.append(
                FindingDraft(
                    agent="security",
                    category="security",
                    severity=rule.severity,  # type: ignore[arg-type]
                    title=rule.finding_title(match),
                    description=rule.description,
                    file=file.path,
                    line=number,
                    line_end=number,
                    evidence=rule.evidence(line, match),
                    recommendation=rule.recommendation,
                    confidence=rule.confidence,
                    source="rule",
                    rule_id=rule.id,
                    family=rule.family,
                    cwe=rule.cwe,
                    detectors=[f"rule:{rule.id}"],
                )
            )
    return findings


def scan_rules(files: list[SourceFile]) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for file in files:
        findings.extend(scan_file(file))
        if len(findings) >= MAX_FINDINGS:
            break
    return findings[:MAX_FINDINGS]


class SecurityAgent(BaseAgent):
    name = "security"
    title = "Security Agent"
    description = "Detects hardcoded secrets, injection, insecure auth/JWT, CORS, XSS and other risky patterns."

    async def run(self, context: AgentContext) -> AgentResult:
        files = [f for f in context.files if f.kind in {"source", "config", "manifest", "doc"}]
        findings = await asyncio.to_thread(scan_rules, files)
        notes: list[str] = []
        mode = "local"
        if context.llm.available:
            try:
                llm_findings = await self._llm_review(context, findings)
                findings.extend(llm_findings)
                mode = "llm"
                notes.append(
                    f"LLM review proposed {len(llm_findings)} additional candidate finding(s); "
                    "the Brain verifies each one before it is accepted."
                )
            except LLMError as exc:
                notes.append(f"LLM review unavailable ({exc}); used deterministic rules only.")
        else:
            notes.append("No LLM configured: deterministic rule engine only (local analysis).")

        by_severity = Counter(f.severity for f in findings)
        affected = len({f.file for f in findings})
        return AgentResult(
            agent=self.name,
            mode=mode,  # type: ignore[arg-type]
            summary=f"{len(findings)} findings analyzed across {affected} file(s)",
            findings=findings,
            data={
                "rules_evaluated": len(RULES),
                "files_scanned": len(files),
                "by_severity": dict(by_severity),
            },
            notes=notes,
            metrics={"findings": len(findings), "files_scanned": len(files)},
        )

    def _select_files(self, context: AgentContext, findings: list[FindingDraft]) -> list[SourceFile]:
        hits = Counter(f.file for f in findings)
        focus = {path: rank for rank, path in enumerate(context.focus.get("security", ()))}
        scored = []
        for file in context.source_files:
            score = hits.get(file.path, 0) * 3 + len(_RISK_WORDS.findall(file.text))
            if file.path in focus:
                score += max(0, 10 - focus[file.path])
            if score:
                scored.append((score, file))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [file for _, file in scored[:LLM_FILE_LIMIT]]

    async def _llm_review(self, context: AgentContext, findings: list[FindingDraft]) -> list[FindingDraft]:
        blocks: list[tuple[str, str]] = []
        budget = LLM_CHAR_BUDGET
        for file in self._select_files(context, findings):
            body = number_lines(redact_secrets(file.text).split("\n"))
            if len(body) > budget:
                body = body[:budget]
            budget -= len(body)
            blocks.append((f"FILE: {file.path}", body))
            if budget <= 0:
                break
        if not blocks:
            return []
        known = "\n".join(f"- {f.file}:{f.line} {f.title}" for f in findings[:40]) or "- none"
        prompt = (
            f"User task: {context.task}\n\nAlready-known findings (do not repeat):\n{known}\n\n"
            f"{wrap_repository_content(blocks)}"
        )
        payload = await context.llm.complete_json(system=SECURITY_SYSTEM, prompt=prompt)
        drafts: list[FindingDraft] = []
        for item in (payload.get("findings") or [])[:25]:
            try:
                claim = LLMSecurityClaim.model_validate(item)
            except ValidationError:
                continue
            severity = claim.severity.lower() if claim.severity.lower() in {"critical", "high", "medium", "low"} else "medium"
            drafts.append(
                FindingDraft(
                    agent="security",
                    category="security",
                    severity=severity,  # type: ignore[arg-type]
                    title=claim.title[:120],
                    description=(claim.description or claim.title)[:600],
                    file=claim.file.strip().lstrip("./"),
                    line=claim.line,
                    line_end=claim.line,
                    evidence=claim.evidence[:300],
                    recommendation=(claim.fix or "Review and fix the reported issue.")[:400],
                    confidence=min(max(claim.confidence, 0.0), 0.95),
                    source="llm",
                    family=infer_family(claim.title),
                    detectors=["llm"],
                )
            )
        return drafts
