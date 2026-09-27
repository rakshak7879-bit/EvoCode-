# Agents

```text
L2 sub-agent → L1 lead agent → L0 Brain → Verification
```

Agents produce **claims**. Only the Brain turns claims into findings, and only after cross-validation and source verification.

Orchestration has three levels. The Brain (L0) routes work to lead agents (L1); each lead delegates
to its own team of specialist sub-agents (L2). A sub-agent never touches the database, the
filesystem or another sub-agent: it receives a read-only context plus the outputs of the members it
declared as dependencies, and returns a `SubAgentOutput`. The runtime is `agents/team.py`.

| Lead agent (L1) | Sub-agents (L2) | When it runs |
| --- | --- | --- |
| 🛡 Security | Secrets Scanner · Injection Analyst · Auth & Crypto Auditor · Config & Exposure Auditor · Prompt-Injection Sentinel · *LLM Security Reviewer* | every analysis |
| ♻ Duplicate Code | Function Extractor · Identical-File Detector · Clone Detector · Reuse Advisor · *LLM Clone Explainer* | every analysis |
| 🧭 Explainer | Stack Detector · Module Mapper · Route Mapper · History Analyst · Flow Tracer · Summary Writer · *LLM Summarizer* | every analysis |
| 🎬 Walkthrough | Storyline Planner · Timing Planner · *LLM Narrator* | every analysis |
| 🔧 Fixer | Fix Planner · Patch Writer · *LLM Patch Writer* · Safety Reviewer · Fix Verifier | `./evo fix` |
| 🧩 Solver | Issue Analyst · Code Locator · Test Scout · Reproducer · Plan Writer · *LLM Solution Reviewer* | `./evo solve` |

*Italic* members only run when an API key is configured; otherwise they are skipped and labeled, and
the lead reports what it fell back to. 27 sub-agents in total, 21 of them in the analysis agents.

Members run in dependency waves: everything whose dependencies are satisfied runs concurrently.
`depends_on` is a hard dependency (the member is skipped if it cannot be satisfied), `uses` is a soft
one (a failure upstream is tolerated). A member failing never fails its lead, and a lead failing
never fails the analysis. `./evo tree` shows the resulting hierarchy for any analysis.

## Brain responsibilities

| Step | What the Brain does | Code |
| --- | --- | --- |
| Understand task | Keyword intents → agent plan with human-readable reasons; adds dependencies | `orchestrator/router.py` |
| Load context | Reads indexed files from the working copy, parses them, recalls previous insights | `orchestrator/context_builder.py` |
| Retrieve memory | Runs FTS5 searches per domain (security, duplicate, explainer focus) | `ContextBuilder.build` |
| Route + execute | Wave 1 in parallel, wave 2 with upstream results; timeouts; failure isolation; supplies each lead's delegation runtime | `Brain._execute`, `Brain._run_agent` |
| Record the hierarchy | The full L0 → L1 → L2 tree with per-member status, wave and timing | `Brain._orchestration`, `orchestrator/progress.py` |
| Aggregate | Collects `FindingDraft`s from completed agents | `Brain.analyze` |
| Cross-validate | Anchors claims, rejects unsupported ones, merges, resolves conflicts, annotates | `orchestrator/crossval.py` |
| Verify | SHA-256 anchors for findings, locations and citations | `orchestrator/evidence.py`, `verification/citations.py` |
| Update memory | Findings, architecture insight, history notes, resolved findings | `Brain._write_insights` |
| Answer questions | Retrieval + verified citations (+ optional LLM restricted to retrieved sources) | `orchestrator/qa.py` |

## Agent contract

```python
class BaseAgent(ABC):
    name: ClassVar[str]              # "security"
    title: ClassVar[str]             # "Security Agent"
    description: ClassVar[str]
    requires: ClassVar[tuple[str, ...]] = ()   # upstream results the Brain must pass in
    consumes: ClassVar[tuple[str, ...]] = ()   # optional upstream results
    team: ClassVar[tuple[SubAgentSpec, ...]] = ()   # the level-2 specialists it delegates to

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult: ...
```

A lead delegates by planning and running its team:

```python
team = await run_team(self.name, plan_team(self.team, handlers, context), context)
suspects = team.value("locator", [])        # a member's output, or a default if it did not run
notes = team.failure_notes()                # human-readable degradation notes
```

```python
@dataclass(frozen=True)
class SubAgentSpec:
    key: str                     # "secrets" → run name "security.secrets"
    title: str                   # "Secrets Scanner"
    description: str
    depends_on: tuple[str, ...] = ()   # hard: skipped if unavailable
    uses: tuple[str, ...] = ()         # soft: waited for, failure tolerated
    requires_llm: bool = False         # skipped (and labeled) in local mode
    keywords: tuple[str, ...] = ()     # selects this member for a focused task
    fallback: str = ""                 # what the lead does without it
```

```python
class AgentResult(BaseModel):
    agent: str
    status: Literal["complete", "failed", "skipped"] = "complete"
    mode: Literal["llm", "local"] = "local"
    summary: str                    # shown on the agent card
    findings: list[FindingDraft]    # claims for the Brain to verify
    data: dict[str, Any]            # agent-specific structured output
    notes: list[str]                # e.g. "LLM unavailable; used local rules"
    metrics: dict[str, float]
    error: str | None
```

```python
class FindingDraft(BaseModel):
    agent: str
    category: Literal["security", "duplicate"]
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str
    description: str
    file: str
    line: int                       # >= 1
    line_end: int | None
    evidence: str | None            # text that must appear at file:line
    recommendation: str
    confidence: float               # 0..1
    source: Literal["rule", "llm", "heuristic"]
    rule_id: str | None
    family: str | None              # used to merge equivalent detections
    locations: list[Location]       # duplicates: every copy
    similarity: float | None
    annotations: list[str]
    detectors: list[str]
```

Everything is Pydantic-validated; random prose is never an internal API. If an agent returns something that does not validate, the Brain marks it failed.

## Agent isolation

- Agents receive a frozen `AgentContext` and return a value. They do not write to SQLite, the filesystem or global state, and they never call other agents.
- The only shared services inside the context are read-only: a `MemoryReader` bound to one repository and the `LLMProvider`.
- Upstream data flows only through the Brain (`context.with_upstream({...})`).

## Context passed to agents

| Field | Content |
| --- | --- |
| `files` | `SourceFile(path, language, kind, size, sha256, tags, text, lines, parsed)` for every indexed file |
| `parsed` | Symbols, imports, exports, routes, API calls, outbound HTTP, data access, legacy markers |
| `memory` | `search(query, limit, memory_types)` over the repository's FTS5 memory |
| `focus` | Files ranked by memory retrieval per domain (e.g. security-relevant files) |
| `prior_insights` | Insights written by the previous analysis |
| `upstream` | Results of required/consumed agents |
| `llm` | `available`, `complete_json(system, prompt)` |
| `task` | The user's task text |

## Output schemas

### Security Agent (`security`)

`findings[]` with `category="security"`, `rule_id` (one of 22 rules in `agents/security_rules.py`), `cwe`, `evidence` (secrets masked as `sk-d[REDACTED]`). `data = {rules_evaluated, files_scanned, by_severity}`. With an LLM, the riskiest files (redacted) are reviewed and extra claims are returned with `source="llm"`.

### Duplicate Code Agent (`duplicate`)

```json
{
  "clusters": [{
    "id": "dup-1", "kind": "function", "title": "Email validation duplicated in 4 places",
    "similarity": 1.0, "severity": "medium",
    "members": [{ "file": "utils/validation.js", "line_start": 3, "line_end": 10, "symbol": "validateEmail", "role": "canonical" }],
    "canonical": { "file": "utils/validation.js", "symbol": "validateEmail" },
    "reason": "4 functions share 100% of their normalized structure…",
    "recommendation": "Reuse the existing `validateEmail` in utils/validation.js:3 (already implemented and exported)…"
  }],
  "duplicates": [{ "file_a": "utils/validation.js", "line_a": 3, "file_b": "backend/users.js", "line_b": 8,
                   "similarity": 1.0, "reason": "…", "recommendation": "…" }],
  "functions_compared": 18, "threshold": 0.72
}
```

Method: string-aware tokenization, comments dropped, local identifiers → `I`, strings → `S`, numbers → `N` (keywords and property names kept), 5-token shingles, Jaccard ≥ 0.72, union-find clustering, canonical copy preferred in `utils/`, `lib/`, `shared/` … Identical files (same SHA-256) are reported too.

### Explainer Agent (`explainer`)

`data = {project_name, summary, summary_source, architecture{layer: {value, values, evidence[]}}, languages, modules[], routes[], request_flows[{name, trigger, resolved, steps[{layer, label, file, line, symbol}]}], important_files[{file, score, reason}], history[{note, files, symbols, kind, source}], capabilities, entrypoints, import_graph}`. Stack claims come from manifests and imports; request flows are traced from client API calls to backend routes, middleware, called functions and data access.

### Walkthrough Agent (`walkthrough`)

`requires=("explainer",)`, `consumes=("security", "duplicate")`. `data = {question, steps[{index, key, title, duration, files, citations, talking_points, narration}], total_duration, target_seconds: 120}`. Runs in degraded mode (README + findings) if the Explainer failed.

## Error handling

- **Timeout**: `EVO_AGENT_TIMEOUT_SECONDS` (default 120 s) → failed result.
- **Exception**: caught per agent, logged with a stack trace, surfaced as `error` on the agent card; other agents continue.
- **LLM errors**: each agent catches `LLMError` and falls back to its deterministic analysis with a note; `mode` stays `local`.
- **Demo**: `EVO_SIMULATE_AGENT_FAILURE=walkthrough` forces a failure to show graceful degradation.

## Verification

The Brain applies the same pipeline to every claim regardless of source:

1. Safe path → file exists → line in range.
2. Evidence must appear at the cited lines (whitespace-insensitive, `[REDACTED]` acts as a wildcard). If it appears elsewhere, the line is corrected; if nowhere, the claim is **rejected as unsupported** and listed under cross-validation.
3. File SHA-256 must equal the indexed hash (else STALE).
4. The SHA-256 of the cited lines is stored; later checks compare it to detect STALE findings.

LLM claims are therefore never trusted by construction: a hallucinated file, line or snippet cannot become a finding.

## How to create a new agent

1. **Create** `backend/agents/license.py`:

   ```python
   from agents.base import AgentResult, BaseAgent, FindingDraft
   from agents.context import AgentContext

   class LicenseAgent(BaseAgent):
       name = "license"
       title = "License Agent"
       description = "Flags source files without a license header."

       async def run(self, context: AgentContext) -> AgentResult:
           findings = [
               FindingDraft(
                   agent=self.name, category="security", severity="info",
                   title="Missing license header", description="…",
                   file=f.path, line=1, evidence=f.lines[0][:120] if f.lines else None,
                   recommendation="Add an SPDX header.", confidence=0.6, source="rule",
               )
               for f in context.source_files
               if f.lines and "SPDX-License-Identifier" not in f.text[:500]
           ]
           return AgentResult(agent=self.name, summary=f"{len(findings)} files without a header", findings=findings)
   ```

2. **Register** it in `services.py` (`agents = [..., LicenseAgent()]`).
3. **Route** it: add intent keywords in `orchestrator/router.py` (`INTENT_KEYWORDS["license"] = ("license", "spdx")`).
4. **Show** it (optional): add a node/icon in `BrainOrchestrator.tsx` and `AgentCard.tsx` (unknown agents still appear in the status API).
5. **Test** it: add a case to `backend/tests/test_agents.py` using the `agent_context` fixture.

Keep agents pure: read the context, return structured data, cite exact lines.
