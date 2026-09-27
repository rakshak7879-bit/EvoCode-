"""Fixer Agent: turns verified findings into reviewed, verified patches.

The Fixer is a lead agent (orchestration level 1) that the Brain runs on demand
for a fix goal ("fix the hardcoded secrets"), never during a normal analysis.
Its level-2 team works as a pipeline:

1. Fix Planner       chooses the verified findings in scope for the goal
2. Patch Writer      rewrites them with deterministic strategies (fix_strategies.py)
   LLM Patch Writer  proposes patches for the rest when an LLM is configured
3. Safety Reviewer   rejects patches that are out of scope, unsafe or leak secrets
4. Fix Verifier      re-runs the security rules on the patched file in memory and
                     keeps a patch only if the finding is gone and nothing new appears

The Fixer never writes files. The Brain shows every verified patch to the user,
applies only approved ones to Evo Code's isolated working copy and re-runs the
analysis so memory records the findings as resolved.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from agents.base import AgentResult, BaseAgent, SubAgentSpec
from agents.context import AgentContext, SourceFile
from agents.fix_strategies import STRATEGIES, NoFix, manual_reason
from agents.security import SECURITY_TEAM, SCANNER_FAMILIES, scan_file
from agents.security_rules import KNOWN_KEY, RULES, SECRET_ASSIGNMENT
from agents.team import NO_LLM, Handler, SubAgentOutput, Upstream, plan_team, run_team
from llm.prompts import number_lines, redact_secrets, wrap_repository_content

SEVERITIES = ("critical", "high", "medium", "low")
ALL_WORDS = ("everything", "all issues", "all findings", "all problems", "every issue", "all of them", "fix all")
SECURITY_WORDS = ("security", "vulnerab", "insecure", "unsafe")
DANGEROUS = re.compile(r"(?<![\w.$])eval\s*\(|\bnew\s+Function\s*\(|child_process|\bexec(?:Sync)?\s*\(|os\.system")
MAX_SPAN = 6
LLM_CONTEXT = 6
_FAMILY_KEYWORDS = {spec.key: spec.keywords for spec in SECURITY_TEAM if spec.key in SCANNER_FAMILIES}
#: Specific words that select individual rules ("fix the sql injection" should not also fix XSS).
RULE_WORDS: dict[str, tuple[str, ...]] = {
    "sql-injection": ("sql",),
    "dom-xss": ("xss", "innerhtml", "cross-site"),
    "unsafe-eval": ("eval",),
    "insecure-cors": ("cors",),
    "jwt-no-verify": ("jwt", "signature"),
    "insecure-http": ("http ", "https", "plain http", "unencrypted"),
    "prompt-injection": ("prompt",),
    "exposed-env": ("env var", "environment variable", "process.env", "debug endpoint"),
    "weak-hash": ("md5", "sha1", "weak hash"),
    "token-localstorage": ("localstorage",),
    "hardcoded-secret": ("secret", "api key", "apikey", "credential", "password", "hardcoded"),
    "known-key-format": ("secret", "api key", "apikey", "credential", "hardcoded"),
}
_RULE_FAMILY = {rule.id: rule.family for rule in RULES}

LLM_FIX_SYSTEM = """Task: propose minimal security fixes for the numbered findings.
For each finding, replace ONLY the cited lines (you may include up to 2 adjacent lines) with corrected code.
Keep the same number of lines, keep indentation and style, do not add imports or new dependencies.
Never write secrets; load them from configuration instead. If a safe minimal fix is impossible, omit the finding.
Return JSON: {"fixes": [{"finding": int, "line_start": int, "line_end": int, "replacement": [str],
"explanation": str}]}"""


class FixProposal(BaseModel):
    id: str
    number: int  # the finding's number in `findings` (severity order)
    finding_id: str
    title: str
    severity: str
    category: str
    rule_id: str | None = None
    file: str
    line_start: int = Field(ge=1)
    before: list[str]
    after: list[str]
    strategy: str
    explanation: str
    source: Literal["rule", "llm"] = "rule"
    notes: list[str] = Field(default_factory=list)
    status: Literal["proposed", "rejected", "verified"] = "proposed"
    rejection: str | None = None
    verification: str | None = None

    @property
    def line_end(self) -> int:
        return self.line_start + len(self.before) - 1


class LLMFix(BaseModel):
    finding: int
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    replacement: list[str]
    explanation: str = ""


# --------------------------------------------------------------------------- goal understanding
def goal_scope(goal: str, findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Findings addressed by a natural-language fix goal, plus a description of the scope."""
    lowered = f" {goal.lower()} "
    numbers = {int(n) for n in re.findall(r"(?:#|\bfindings?\s+|\bnumbers?\s+|\b)(\d{1,3})\b", lowered)}
    numbers = {n for n in numbers if 1 <= n <= len(findings)}
    if numbers:
        chosen = [finding for index, finding in enumerate(findings, start=1) if index in numbers]
        return chosen, "finding " + ", ".join(f"#{n}" for n in sorted(numbers))

    rules = {rule_id for rule_id, words in RULE_WORDS.items() if any(w in lowered for w in words)}
    families = set() if rules else {family for key, words in _FAMILY_KEYWORDS.items()
                                    if any(w in lowered for w in words) for family in SCANNER_FAMILIES[key]}
    severities = {s for s in SEVERITIES if re.search(rf"\b{s}\b", lowered)}
    if "high" in severities or "severe" in lowered or "critical" in lowered:
        severities |= {"critical", "high"}
    wants_duplicates = any(w in lowered for w in ("duplicate", "duplicat", "clone", "copy-paste", "copies"))
    wants_security = any(w in lowered for w in SECURITY_WORDS) or bool(families) or bool(rules)
    wants_all = any(w in lowered for w in ALL_WORDS) or not (wants_security or wants_duplicates or severities)

    chosen: list[dict[str, Any]] = []
    for finding in findings:
        if finding["category"] == "duplicate":
            if wants_duplicates or (wants_all and not wants_security):
                chosen.append(finding)
            continue
        rule_id = finding.get("rule_id") or ""
        if rules and rule_id not in rules:
            continue
        if families and _RULE_FAMILY.get(rule_id, finding["extra"].get("family")) not in families:
            continue
        if severities and finding["severity"] not in severities:
            continue
        if wants_security or severities or wants_all:
            chosen.append(finding)
    parts = []
    if rules:
        parts.append("/".join(sorted(rules)))
    if families:
        parts.append("/".join(sorted(families)))
    if severities:
        parts.append("/".join(s for s in SEVERITIES if s in severities) + " severity")
    if wants_duplicates:
        parts.append("duplicates")
    if not parts:
        parts.append("all security findings" if wants_security else "all findings")
    return chosen, " · ".join(parts)


# --------------------------------------------------------------------------- helpers
def patched_file(file: SourceFile, proposal: FixProposal) -> SourceFile:
    lines = list(file.lines)
    lines[proposal.line_start - 1 : proposal.line_end] = proposal.after
    return replace(file, lines=lines, text="\n".join(lines))


def _candidates(file: SourceFile) -> Counter[tuple[str, str]]:
    return Counter((f.rule_id or "", (f.evidence or "").strip()) for f in scan_file(file, RULES))


def review_proposal(proposal: FixProposal, file: SourceFile | None, finding: dict[str, Any]) -> str | None:
    """Return a rejection reason, or ``None`` if the patch is safe to verify."""
    if file is None:
        return "The file is no longer part of the working copy."
    if proposal.line_end > len(file.lines) or file.lines[proposal.line_start - 1 : proposal.line_end] != proposal.before:
        return "The patch does not match the current source (the file changed)."
    if len(proposal.after) != len(proposal.before):
        return "The patch changes the number of lines; only in-place rewrites are applied automatically."
    if len(proposal.before) > MAX_SPAN:
        return f"The patch touches more than {MAX_SPAN} lines."
    cited = set(range(finding["line"], finding["line_end"] + 1))
    touched = {proposal.line_start + i for i, (a, b) in enumerate(zip(proposal.before, proposal.after)) if a != b}
    if not touched:
        return "The patch does not change anything."
    if not cited & touched or any(min(abs(line - c) for c in cited) > 4 for line in touched):
        return "The patch changes lines that are not part of the finding."
    new_text = "\n".join(proposal.after)
    if any("\n" in line or "\r" in line for line in proposal.after):
        return "Replacement lines must not contain line breaks."
    if "[REDACTED" in new_text:
        return "The patch contains a redaction placeholder instead of real code."
    if KNOWN_KEY.search(new_text) or (SECRET_ASSIGNMENT.search(new_text)
                                      and not SECRET_ASSIGNMENT.search("\n".join(proposal.before))):
        return "The patch introduces a hardcoded credential."
    if DANGEROUS.search(new_text) and not DANGEROUS.search("\n".join(proposal.before)):
        return "The patch introduces dynamic code or shell execution."
    if len(new_text) > 4000:
        return "The patch is too large."
    return None


def verify_proposal(proposal: FixProposal, file: SourceFile) -> tuple[bool, str]:
    """Re-run every security rule on the patched file (in memory, never on disk)."""
    before, after = _candidates(file), _candidates(patched_file(file, proposal))
    target = proposal.rule_id or ""
    patched = patched_file(file, proposal)
    still = [f for f in scan_file(patched, RULES) if f.rule_id == target
             and proposal.line_start <= f.line <= proposal.line_end]
    if proposal.category == "security" and still:
        return False, f"Rule {target} still matches the patched lines."
    introduced = after - before
    if introduced:
        rules = ", ".join(sorted({rule for rule, _ in introduced}))
        return False, f"The patch introduces new findings ({rules})."
    return True, (f"Rule {target} no longer matches · all {len(RULES)} security rules re-run on {proposal.file}: "
                  "no new findings")


# --------------------------------------------------------------------------- the agent
FIXER_TEAM: tuple[SubAgentSpec, ...] = (
    SubAgentSpec("planner", "Fix Planner",
                 "Understands the fix goal and selects the verified findings in scope"),
    SubAgentSpec("patcher", "Patch Writer",
                 "Rewrites findings with deterministic, line-preserving fix strategies", depends_on=("planner",)),
    SubAgentSpec("llm_patcher", "LLM Patch Writer",
                 "Proposes minimal patches for findings without a deterministic strategy (secrets redacted)",
                 depends_on=("planner",), requires_llm=True, fallback="those findings need a manual fix"),
    SubAgentSpec("reviewer", "Safety Reviewer",
                 "Rejects out-of-scope, unsafe or secret-leaking patches",
                 depends_on=("planner",), uses=("patcher", "llm_patcher")),
    SubAgentSpec("verifier", "Fix Verifier",
                 "Re-runs the security rules on each patched file and keeps only fixes that work",
                 depends_on=("reviewer",)),
)


class FixerAgent(BaseAgent):
    name = "fixer"
    title = "Fixer Agent"
    description = "Turns verified findings into reviewed, verified patches for the isolated working copy."
    team = FIXER_TEAM

    async def run(self, context: AgentContext) -> AgentResult:
        handlers: dict[str, Handler] = {
            "planner": self._plan,
            "patcher": self._patch,
            "llm_patcher": self._llm_patch,
            "reviewer": self._review,
            "verifier": self._verify,
        }
        team = await run_team(self.name, plan_team(self.team, handlers, context), context)
        plan = team.value("planner")
        if plan is None:
            raise RuntimeError("The Fix Planner did not complete: " + "; ".join(team.failure_notes()))
        proposals: list[FixProposal] = team.value("verifier") or []
        if not team.completed("verifier"):
            # Without the verifier nothing is safe to apply: report everything as rejected.
            reviewed = team.value("reviewer") or []
            proposals = [p.model_copy(update={"status": "rejected", "rejection": "Not verified"}) for p in reviewed]
        manual = list(plan["manual"])
        for key in ("patcher", "llm_patcher"):
            manual.extend((team.value(key) or {}).get("unfixed", []))
        if not team.completed("llm_patcher"):
            manual.extend(plan["llm_targets_manual"])
        verified = [p for p in proposals if p.status == "verified"]
        notes = team.failure_notes()
        if (run := team.run("llm_patcher")) is not None and run.reason == NO_LLM and plan["llm_targets_manual"]:
            notes.append("No LLM configured: only deterministic fix strategies were used.")
        return AgentResult(
            agent=self.name,
            mode="llm" if any(p.source == "llm" for p in verified) else "local",
            summary=(f"{len(verified)} verified patch(es) · {len(proposals) - len(verified)} rejected · "
                     f"{len(manual)} need a manual fix"),
            data={
                "goal": plan["goal"],
                "scope": plan["scope"],
                "in_scope": plan["in_scope"],
                "proposals": [p.model_dump(mode="json") for p in proposals],
                "manual": sorted(manual, key=lambda item: item["number"]),
            },
            notes=notes,
            metrics={"verified": len(verified), "rejected": len(proposals) - len(verified), "manual": len(manual)},
            subagents=team.runs,
        )

    # ------------------------------------------------------------------ team members
    @staticmethod
    def _findings(context: AgentContext) -> tuple[str, list[dict[str, Any]]]:
        source = context.upstream["findings"]
        return source.data["goal"], source.data["findings"]

    def _plan(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        goal, findings = self._findings(context)
        numbered = [{**finding, "number": index} for index, finding in enumerate(findings, start=1)]
        chosen, scope = goal_scope(goal, numbered)
        rule_targets, llm_targets, manual = [], [], []
        for finding in chosen:
            entry = {"number": finding["number"], "title": finding["title"], "file": finding["file"],
                     "line": finding["line"], "severity": finding["severity"]}
            if finding["status"] != "verified":
                manual.append({**entry, "reason": f"Finding is {finding['status'].upper()}: the source changed since "
                                                  "the analysis. Run `reanalyze` first."})
            elif finding["category"] == "duplicate":
                manual.append({**entry, "reason": manual_reason(finding)})
            elif (finding.get("rule_id") or "") in STRATEGIES:
                rule_targets.append(finding)
            else:
                llm_targets.append(finding)
        llm_manual = [{"number": f["number"], "title": f["title"], "file": f["file"], "line": f["line"],
                       "severity": f["severity"], "reason": manual_reason(f)} for f in llm_targets]
        return SubAgentOutput(
            summary=(f"Goal scope: {scope} · {len(chosen)} finding(s) in scope · {len(rule_targets)} with a fix "
                     f"strategy · {len(llm_targets)} without · {len(manual)} not fixable automatically"),
            value={"goal": goal, "scope": scope, "in_scope": len(chosen), "rule_targets": rule_targets,
                   "llm_targets": llm_targets, "llm_targets_manual": llm_manual, "manual": manual},
            metrics={"in_scope": len(chosen), "rule_targets": len(rule_targets), "llm_targets": len(llm_targets)},
        )

    @staticmethod
    def _proposal(finding: dict[str, Any], file: SourceFile, line_start: int, after: list[str], strategy: str,
                  explanation: str, notes: list[str], source: str) -> FixProposal:
        return FixProposal(
            id="", number=finding["number"], finding_id=finding["id"], title=finding["title"],
            severity=finding["severity"], category=finding["category"], rule_id=finding.get("rule_id"),
            file=finding["file"], line_start=line_start, before=file.lines[line_start - 1: line_start - 1 + len(after)],
            after=after, strategy=strategy, explanation=explanation, notes=notes, source=source,  # type: ignore[arg-type]
        )

    def _patch(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        proposals: list[FixProposal] = []
        unfixed: list[dict[str, Any]] = []
        for finding in upstream["planner"].value["rule_targets"]:
            file = context.file(finding["file"])
            try:
                if file is None:
                    raise NoFix("The file is no longer part of the working copy.")
                rewrite = STRATEGIES[finding["rule_id"]](file, finding)
            except NoFix as exc:
                unfixed.append({"number": finding["number"], "title": finding["title"], "file": finding["file"],
                                "line": finding["line"], "severity": finding["severity"], "reason": str(exc)})
                continue
            proposals.append(self._proposal(finding, file, rewrite.line_start, list(rewrite.after), rewrite.strategy,
                                            rewrite.explanation, list(rewrite.notes), "rule"))
        strategies = Counter(p.strategy for p in proposals)
        detail = ", ".join(f"{name} ×{count}" for name, count in strategies.most_common(4))
        return SubAgentOutput(
            summary=f"{len(proposals)} patch(es) written" + (f": {detail}" if detail else "")
                    + (f" · {len(unfixed)} pattern(s) not rewritable" if unfixed else ""),
            value={"proposals": proposals, "unfixed": unfixed},
            findings=[],
            metrics={"patches": len(proposals), "unfixed": len(unfixed)},
        )

    async def _llm_patch(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        targets: list[dict[str, Any]] = upstream["planner"].value["llm_targets"]
        if not targets:
            return SubAgentOutput(summary="No findings left for the LLM", value={"proposals": [], "unfixed": []})
        blocks, by_number = [], {}
        for finding in targets[:8]:
            file = context.file(finding["file"])
            if file is None:
                continue
            start = max(1, finding["line"] - LLM_CONTEXT)
            end = min(len(file.lines), finding["line_end"] + LLM_CONTEXT)
            body = number_lines([redact_secrets(line) for line in file.lines[start - 1: end]], start)
            blocks.append((f"FINDING {finding['number']}: {finding['title']} at {finding['file']}:"
                           f"{finding['line']}-{finding['line_end']} ({finding['recommendation']})", body))
            by_number[finding["number"]] = finding
        payload = await context.llm.complete_json(system=LLM_FIX_SYSTEM, prompt=wrap_repository_content(blocks),
                                                  max_tokens=1400)
        proposals: list[FixProposal] = []
        answered: set[int] = set()
        for item in (payload.get("fixes") or [])[:12]:
            try:
                fix = LLMFix.model_validate(item)
            except ValidationError:
                continue
            finding = by_number.get(fix.finding)
            file = context.file(finding["file"]) if finding else None
            if finding is None or file is None or fix.finding in answered or fix.line_end > len(file.lines):
                continue
            if fix.line_end - fix.line_start + 1 != len(fix.replacement):
                continue
            answered.add(fix.finding)
            proposals.append(self._proposal(finding, file, fix.line_start, [str(line) for line in fix.replacement],
                                            "llm-rewrite", fix.explanation[:400] or "LLM-proposed minimal fix.",
                                            ["Proposed by the LLM; verified by Evo Code's rules before it is shown."],
                                            "llm"))
        unfixed = [{"number": f["number"], "title": f["title"], "file": f["file"], "line": f["line"],
                    "severity": f["severity"], "reason": "The LLM did not propose a usable minimal fix. "
                    + manual_reason(f)} for number, f in by_number.items() if number not in answered]
        return SubAgentOutput(summary=f"{len(proposals)} LLM patch(es) proposed for {len(by_number)} finding(s)",
                              value={"proposals": proposals, "unfixed": unfixed}, mode="llm",
                              metrics={"patches": len(proposals)})

    def _review(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        _, findings = self._findings(context)
        by_id = {finding["id"]: finding for finding in findings}
        proposals = [p for key in ("patcher", "llm_patcher") if key in upstream for p in upstream[key].value["proposals"]]
        reviewed: list[FixProposal] = []
        claimed: dict[str, set[int]] = {}
        for index, proposal in enumerate(sorted(proposals, key=lambda p: (p.number, p.source != "rule")), start=1):
            proposal = proposal.model_copy(update={"id": f"fix-{index}"})
            reason = review_proposal(proposal, context.file(proposal.file), by_id[proposal.finding_id])
            lines = set(range(proposal.line_start, proposal.line_end + 1))
            if reason is None and claimed.get(proposal.file, set()) & lines:
                reason = "Overlaps another patch in the same file."
            if reason is None:
                claimed.setdefault(proposal.file, set()).update(lines)
            else:
                proposal = proposal.model_copy(update={"status": "rejected", "rejection": reason})
            reviewed.append(proposal)
        rejected = sum(1 for p in reviewed if p.status == "rejected")
        return SubAgentOutput(summary=f"{len(reviewed) - rejected}/{len(reviewed)} patch(es) passed safety review",
                              value=reviewed, metrics={"passed": len(reviewed) - rejected, "rejected": rejected})

    def _verify(self, context: AgentContext, upstream: Upstream) -> SubAgentOutput:
        verified: list[FixProposal] = []
        for proposal in upstream["reviewer"].value:
            if proposal.status == "rejected":
                verified.append(proposal)
                continue
            file = context.file(proposal.file)
            ok, message = verify_proposal(proposal, file) if file else (False, "File missing.")
            verified.append(proposal.model_copy(update={"status": "verified" if ok else "rejected",
                                                        "verification": message if ok else None,
                                                        "rejection": None if ok else message}))
        count = sum(1 for p in verified if p.status == "verified")
        checked = sum(1 for p in upstream["reviewer"].value if p.status != "rejected")
        return SubAgentOutput(summary=f"{count}/{checked} patch(es) verified: the finding is gone and no new findings "
                                      "appear", value=verified, metrics={"verified": count})
