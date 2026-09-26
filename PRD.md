# Evo Code — Product Requirements Document

Status: MVP (hackathon build) · Owner: Evo Code team

## 1. Executive summary

Evo Code is a persistent AI harness that turns a codebase and previous engineering work into verified, reusable memory. A central orchestrator, the **Brain**, indexes a repository into searchable memory, delegates analysis to specialist agents (security, duplicates, architecture, walkthrough), cross-validates their claims and verifies every important conclusion against the actual source with SHA-256. The differentiator is **verified codebase intelligence**: AI work → structured memory → retrieval → specialist agents → verified intelligence → better next action.

## 2. Problem

AI coding tools generate answers and edits without durable understanding of the whole codebase or its history. Their citations are often unverifiable or silently outdated, so developers cannot tell which conclusions are still true. Knowledge about what was already implemented (a shared validator, a deprecated path) is lost, and the same logic gets rewritten again.

## 3. Target users

Primary: developers, engineering teams, hackathon teams, technical leads, AI-assisted developers.
Secondary: code reviewers, students, open-source maintainers, software architects.

## 4. User personas

| Persona | Need | Evo Code moment |
| --- | --- | --- |
| **Maya, new team member** | Understand an unfamiliar repo fast | Explainer map + two-minute walkthrough |
| **Raj, tech lead** | Know the real risks before a release | Verified security findings with exact lines |
| **Lena, reviewer** | Trust AI claims | SHA-256 verification, STALE detection |
| **Sam, hackathon judge** | Grasp a project in 2 minutes | Walkthrough Agent presentation mode |

## 5. Current alternatives

- Chat-based AI assistants: fluent answers, weak or unverifiable citations, no persistent repo memory.
- Static analyzers (Semgrep, CodeQL, linters): precise rules, no architecture understanding or Q&A.
- Code search (grep, Sourcegraph): finds text, does not explain or cross-check.
- Documentation: goes stale; not tied to source hashes.

## 6. Product vision

Every engineering conclusion (by an AI or a human) becomes a verified, cited, reusable memory that stays tied to the source and is invalidated when the source changes.

## 7. Goals

1. Ingest a repository in seconds and build searchable memory.
2. Produce security, duplicate, architecture and walkthrough intelligence through one orchestrated flow.
3. Tie every important claim to file + line + SHA-256 and detect staleness.
4. Answer codebase questions with verified citations.
5. Work fully offline; use an LLM only as an optional enhancement, and label which mode ran.

## 8. Non-goals (MVP)

Code modification or auto-fixing, dependency installation, executing repository code, multi-user accounts/authentication, vector databases, cloud deployment, IDE plugins, CI integration.

## 9. User stories

- As a developer, I upload a ZIP or paste a GitHub URL and see analysis progress live.
- As a developer, I see security findings with the exact file and line, a recommendation and verification status.
- As a developer, I see where logic is duplicated and which implementation already exists.
- As a newcomer, I read an evidence-backed architecture summary and request flows.
- As a presenter, I get a two-minute walkthrough with the code to show.
- As a reviewer, I change the source and see the affected finding turn STALE.
- As a developer, I ask "Where is authentication implemented?" and get cited, verified sources.
- As an operator, I can run everything without an API key, and the UI tells me it is local analysis.

## 10. Functional requirements

| ID | Requirement |
| --- | --- |
| F1 | Accept ZIP uploads, public GitHub URLs and the bundled demo repository. |
| F2 | Skip `.git`, `node_modules`, virtualenvs, `__pycache__`, `dist`, `build`, `coverage` and sensitive files (`.env`, keys). |
| F3 | Detect languages, README, manifests (package.json, requirements.txt, pyproject.toml …), Dockerfiles, configs, entry points. |
| F4 | Compute SHA-256 per file and per indexed passage; store metadata in SQLite. |
| F5 | Index symbol, code-window, doc-section and config passages into FTS5; support identifier, natural-language, filename, symbol and keyword search. |
| F6 | Brain: understand task, load context, route agents with reasons, execute, aggregate, cross-validate, verify, write memory. |
| F7 | Security Agent returns structured findings with severity, evidence, fix and confidence. |
| F8 | Duplicate Agent returns clusters, pairwise duplicates, similarity and a recommendation that prefers existing shared code. |
| F9 | Explainer Agent returns summary, stack, modules, request flows, important files and history, all with citations. |
| F10 | Walkthrough Agent returns timed steps (≈120 s) with citations. |
| F11 | Verification statuses: VERIFIED, STALE, MISSING, INVALID_LINE, UNSUPPORTED. |
| F12 | Findings are re-verified live when requested; re-analysis records resolved findings in memory. |
| F13 | Memory Q&A returns an answer with verified citations. |
| F14 | Dashboard: Home + Dashboard (Overview, Findings, Memory, Walkthrough), animated Brain, source viewer. |

## 11. Non-functional requirements

- Demo repository analysis completes in < 10 s locally (≈5 s with demo pacing, < 1 s without).
- No external services required; SQLite file database.
- One failing agent never fails the analysis.
- Strict TypeScript, typed Python with Pydantic contracts, structured logging.
- Accessible UI: keyboard navigation, visible focus, text labels in addition to color, reduced-motion support.

## 12. Core workflows

1. **Analyze**: Home → upload/URL/demo → pipeline + Brain animation → metrics → verified findings.
2. **Investigate**: Findings → open finding → source viewer at the exact line → hashes.
3. **Stale loop**: edit the cited line (working copy) → finding turns STALE → re-run → finding resolved in memory.
4. **Ask**: Memory tab → question → answer + verified sources → open source.
5. **Present**: Walkthrough tab → presenter mode with timed steps and code previews.

## 13. Agent responsibilities

| Agent | Responsibility | Output |
| --- | --- | --- |
| Security | Vulnerabilities and risky patterns with line evidence | `findings[]` |
| Duplicate | Repeated or near-identical implementations | `clusters[]`, `duplicates[]`, findings |
| Explainer | What the repo does and how it is built | summary, architecture, modules, flows, history |
| Walkthrough | Two-minute presentation | `steps[]` |
| Brain (orchestrator) | Routing, aggregation, cross-validation, verification, memory | report, verified findings |

Details: [AGENTS.md](AGENTS.md).

## 14. Memory requirements

- Tables: `repositories`, `files`, `memories`, `findings`, `agent_runs` (+ FTS5 index `memories_fts`).
- Memory types: `code`, `symbol`, `doc`, `config` (from source) and `finding`, `insight`, `history` (written by the Brain).
- Every source-anchored memory stores line range + SHA-256 of those lines.
- Corrupted databases are quarantined and recreated automatically.

## 15. Verification requirements

- Claims must cite an existing file and line; cited evidence must appear at that location (auto-corrected when only the line number is off).
- File SHA-256 at analysis time must match the indexed hash, otherwise STALE.
- Later checks compare the current SHA-256 of the cited lines with the recorded one: unchanged → VERIFIED, changed → STALE (with relocation hint).
- Verification applies to findings, duplicate locations, architecture evidence, request-flow hops, walkthrough citations and Q&A citations.

## 16. UI requirements

Dark developer-tool aesthetic; Home (hero, URL + ZIP input, demo button, feature row) and Dashboard (Overview, Findings, Memory, Walkthrough). Required elements: animated Brain with agent connections, agent status cards, pipeline strip, metrics derived from real data, finding cards with severity + verification + SHA-256, source viewer with highlighted line, memory panel with citations, walkthrough player. See [DESIGN.md](DESIGN.md).

## 17. API requirements

REST/JSON over FastAPI: analyze, status, re-analyze, findings, memory search, walkthrough, source, source edit, health, list. See [API.md](API.md).

## 18. Security requirements

Treat repositories as untrusted: never execute code or install dependencies, never follow symlinks, block traversal, cap sizes, skip sensitive files, redact secrets in outputs and LLM prompts, treat repository text as data (prompt-injection safe), keep API keys server-side. See [SECURITY.md](SECURITY.md).

## 19. Error states

| Case | Behavior |
| --- | --- |
| Invalid / encrypted / unsafe ZIP | HTTP 400 with a clear message |
| Upload too large | HTTP 413 |
| No supported files | Analysis status `failed` with message |
| Huge / binary / malformed file | Skipped or decoded with replacement, counted in stats |
| Missing API key | Local deterministic mode, labeled in UI |
| LLM failure | Agent falls back to local analysis, note recorded |
| Agent failure / timeout | Agent marked failed; other agents and verification continue |
| Corrupted SQLite DB | Quarantined and recreated on startup |
| Source hash mismatch | Finding/citation marked STALE |
| GitHub rate limit / 404 | Analysis `failed` with actionable message |
| Server restart mid-analysis | Marked failed with "re-run" message |

## 20. MVP scope

Everything in sections 10–19 for JavaScript/TypeScript and Python repositories (other languages are indexed and searchable; symbol parsing is JS/TS/Python/Go).

## 21. Future scope

Embeddings and semantic retrieval, PR analysis, CI/CD, Slack/Linear/Notion, IDE extensions, organization-wide memory. See [ROADMAP.md](ROADMAP.md).

## 22. Success criteria

- A judge understands the product and sees a verified finding, a stale transition and a cited answer within 4 minutes.
- Demo repo: ≥ 10 security findings, ≥ 3 duplicate clusters, 100 % of findings verified on first analysis.
- Zero crashes across the golden path, with or without an LLM.

## 23. Acceptance criteria

- [x] ZIP upload, scan, index, SHA-256 per file
- [x] SQLite + FTS5 memory; search and citations work
- [x] Brain receives task, loads context, selects agents, aggregates, verifies
- [x] Security, Duplicate, Explainer, Walkthrough agents work
- [x] File path, line, source check, SHA-256 check, stale detection
- [x] Home, Dashboard, Brain animation, agent status, findings, source viewer, memory search
- [x] PRD, Architecture, Design, API, Agents, Security, Development, Demo, Decisions, Roadmap docs
