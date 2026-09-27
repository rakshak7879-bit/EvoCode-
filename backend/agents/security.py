"""Security Agent: finds vulnerabilities and risky patterns with source evidence.

The Security Agent is a lead agent (orchestration level 1). It delegates to a
team of level-2 specialists: five deterministic rule scanners, each owning a
set of rule families, run concurrently; an optional LLM Security Reviewer runs
after them and reviews the riskiest files (secrets redacted). For a focused task
("only check for hardcoded secrets") the lead delegates only to the matching
scanners. The lead merges the scanners' candidates back into one deterministic
order. LLM claims are drafts with ``source="llm"``; the Brain rejects any claim
whose evidence cannot be found at the cited lines.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, Field, ValidationError

from agents.base import AgentResult, BaseAgent, FindingDraft, SubAgentSpec
from agents.context import AgentContext, SourceFile
from agents.security_rules import RULES, SecurityRule, infer_family, is_comment_line
from agents.team import NO_LLM, Handler, SubAgentOutput, TeamResult, Upstream, plan_team, run_team
from llm.prompts import number_lines, redact_secrets, wrap_repository_content

SCANNED_KINDS = frozenset({"source", "config", "manifest", "doc"})
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


#: Level-2 team. Every rule family is owned by exactly one scanner (see SCANNER_FAMILIES).
SECURITY_TEAM: tuple[SubAgentSpec, ...] = (
    SubAgentSpec("secrets", "Secrets Scanner",
                 "Hardcoded credentials, provider key formats and committed private keys",
                 keywords=("secret", "api key", "apikey", "credential", "password", "hardcoded", "private key")),
    SubAgentSpec("injection", "Injection Analyst",
                 "SQL and command injection, eval, path traversal, XSS and mass assignment",
                 keywords=("injection", "sql", "xss", "eval", "command", "traversal", "sanitiz", "mass assignment",
                           "input validation")),
    SubAgentSpec("auth", "Auth & Crypto Auditor",
                 "JWT signature checks, token storage, weak hashing and insecure randomness",
                 keywords=("jwt", "auth", "token", "session", "crypto", "hash", "random", "signature")),
    SubAgentSpec("exposure", "Config & Exposure Auditor",
                 "CORS, TLS, plain HTTP, debug mode and exposed environment variables",
                 keywords=("cors", "tls", "https", "plain http", "debug", "misconfig", "configuration", "exposure",
                           "exposed", "environment variable", "env var", "certificate")),
    SubAgentSpec("prompt", "Prompt-Injection Sentinel",
                 "Instructions planted in repository content for AI tools",
                 keywords=("prompt", "ai safety", "planted instruction")),
    SubAgentSpec("llm_review", "LLM Security Reviewer",
                 "Reviews the riskiest files for logic flaws the rules cannot see (secrets redacted)",
                 uses=("secrets", "injection", "auth", "exposure", "prompt"), requires_llm=True,
                 keywords=("logic flaw", "authorization", "business logic", "llm review", "ai review"),
                 fallback="used deterministic rules only"),
)
SCANNER_FAMILIES: dict[str, frozenset[str]] = {
    "secrets": frozenset({"secret"}),
    "injection": frozenset({"injection", "file", "xss", "validation"}),
    "auth": frozenset({"jwt", "storage", "crypto"}),
    "exposure": frozenset({"cors", "transport", "config", "exposure"}),
    "prompt": frozenset({"prompt-injection"}),
}
_RULE_ORDER = {rule.id: index for index, rule in enumerate(RULES)}


def rules_for(families: frozenset[str]) -> tuple[SecurityRule, ...]:
    return tuple(rule for rule in RULES if rule.family in families)


def scan_file(file: SourceFile, rules: Sequence[SecurityRule] = RULES) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    per_rule: Counter[str] = Counter()
    applicable = [rule for rule in rules if rule.applies_to(file.kind, file.language)]
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


def scan_rules(files: list[SourceFile], rules: Sequence[SecurityRule] = RULES) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for file in files:
        findings.extend(scan_file(file, rules))
        if len(findings) >= MAX_FINDINGS:
            break
    return findings[:MAX_FINDINGS]


def merge_candidates(files: Sequence[SourceFile], batches: Sequence[Sequence[FindingDraft]]) -> list[FindingDraft]:
    """Merge scanner outputs into the single-pass order (file, line, rule) and apply the global cap.

    Equivalent to ``scan_rules(files)`` over all rules: each scanner keeps its
    own per-rule caps and stops only after ``MAX_FINDINGS`` of its own
    candidates, so the first ``MAX_FINDINGS`` merged candidates are identical.
    """
    order = {file.path: index for index, file in enumerate(files)}
    merged = [finding for batch in batches for finding in batch]
    merged.sort(key=lambda f: (order.get(f.file, len(order)), f.line, _RULE_ORDER.get(f.rule_id or "", len(RULES))))
    return merged[:MAX_FINDINGS]


class SecurityAgent(BaseAgent):
    name = "security"
    title = "Security Agent"
    description = "Detects hardcoded secrets, injection, insecure auth/JWT, CORS, XSS and other risky patterns."
    team = SECURITY_TEAM

    async def run(self, context: AgentContext) -> AgentResult:
        files = [f for f in context.files if f.kind in SCANNED_KINDS]
        handlers: dict[str, Handler] = {key: self._scanner(files, key) for key in SCANNER_FAMILIES}
        handlers["llm_review"] = self._make_reviewer(files)
        team = await run_team(self.name, plan_team(self.team, handlers, context, route_by_task=True), context)
        scanners = [team.run(key) for key in SCANNER_FAMILIES]
        if not any(run and run.status == "complete" for run in scanners):
            raise RuntimeError("No security scanner completed: " + "; ".join(team.failure_notes()))

        findings = self._rule_candidates(files, team)
        review = team.output("llm_review")
        notes: list[str] = []
        mode = "local"
        if review is not None:
            findings.extend(review.findings)
            mode = "llm"
            notes.append(
                f"LLM review proposed {len(review.findings)} additional candidate finding(s); "
                "the Brain verifies each one before it is accepted."
            )
        elif (reviewer := team.run("llm_review")) is not None and reviewer.reason == NO_LLM:
            notes.append("No LLM configured: deterministic rule engine only (local analysis).")
        notes.extend(team.failure_notes())
        focus = team.focus_note()
        if focus:
            notes.append(focus)

        evaluated = [rule for key in SCANNER_FAMILIES if team.completed(key) for rule in rules_for(SCANNER_FAMILIES[key])]
        by_severity = Counter(f.severity for f in findings)
        affected = len({f.file for f in findings})
        return AgentResult(
            agent=self.name,
            mode=mode,  # type: ignore[arg-type]
            summary=f"{len(findings)} findings analyzed across {affected} file(s)",
            findings=findings,
            data={
                "rules_evaluated": len(evaluated),
                "files_scanned": len(files),
                "by_severity": dict(by_severity),
                "scanners": {run.key: run.findings for run in team.runs if run.status == "complete"},
            },
            notes=notes,
            metrics={"findings": len(findings), "files_scanned": len(files)},
            subagents=team.runs,
        )

    # ------------------------------------------------------------------ team members
    @staticmethod
    def _scanner(files: list[SourceFile], key: str) -> Handler:
        rules = rules_for(SCANNER_FAMILIES[key])

        def scan(context: AgentContext, upstream: Upstream) -> SubAgentOutput:
            candidates = scan_rules(files, rules)
            hits = Counter(f.rule_id for f in candidates)
            detail = ", ".join(f"{rule_id} ×{count}" for rule_id, count in hits.most_common(3))
            return SubAgentOutput(
                summary=f"{len(candidates)} candidate(s) from {len(rules)} rule(s)" + (f": {detail}" if detail else ""),
                findings=candidates,
                metrics={"candidates": len(candidates), "rules": len(rules), "files": len(files)},
            )

        return scan

    def _make_reviewer(self, files: list[SourceFile]) -> Handler:
        async def review(context: AgentContext, upstream: Upstream) -> SubAgentOutput:
            known = merge_candidates(files, [output.findings for output in upstream.values()])
            drafts = await self._llm_review(context, known)
            return SubAgentOutput(
                summary=f"{len(drafts)} additional candidate(s) proposed for Brain verification",
                findings=drafts,
                metrics={"candidates": len(drafts)},
                mode="llm",
            )

        return review

    @staticmethod
    def _rule_candidates(files: list[SourceFile], team: TeamResult) -> list[FindingDraft]:
        batches = [output.findings for key in SCANNER_FAMILIES if (output := team.output(key)) is not None]
        return merge_candidates(files, batches)

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
