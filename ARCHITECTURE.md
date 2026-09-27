# Architecture

Evo Code is command-line first: `./evo` runs the scanner, SQLite memory store, Brain and agents in a
single local process, with no server, browser or external service. The same engine also backs an
optional two-process setup (a FastAPI backend plus a React SPA) for the dashboard.

Orchestration has three levels: the **Brain** (L0) understands the task and routes it; **lead
agents** (L1) own a domain; **specialist sub-agents** (L2) do the narrow work, in dependency waves.
Two lead agents run on demand rather than during analysis: the **Fixer** (`./evo fix`) turns verified
findings into reviewed patches, and the **Solver** (`./evo solve`) breaks an issue down, locates the
code and runs the repository's tests so a fix can be proven. See [AGENTS.md](AGENTS.md) and
[HARNESS.md](HARNESS.md).

## System architecture

```mermaid
flowchart TD
    Terminal["./evo CLI (primary)"] --> Pipeline
    Model[Coding model e.g. DeepSeek] -- solve · test · check --> Terminal
    User --> Frontend[React + Vite SPA optional]
    Frontend -- REST/JSON, polled status --> API[FastAPI]
    API --> Pipeline[Analysis pipeline<br/>background task]
    Pipeline --> Scanner[Repository scanner]
    Pipeline --> Indexer[Memory indexer]
    Pipeline --> Brain{{Brain orchestrator}}
    Brain --> Router[Router: task → agents]
    Brain --> Security
    Brain --> Duplicate
    Brain --> Explainer
    Brain --> Walkthrough
    Brain --> XV[Cross-validation]
    XV --> Verify[Source verification]
    Brain --> Memory[(SQLite + FTS5)]
    Indexer --> Memory
    API --> Memory
    API --> QA[Question answering]
    QA --> Memory
    QA --> Verify
    Brain -. optional .-> LLM[LLM provider<br/>OpenAI or local]
```

## Frontend architecture

- **Routing**: a tiny hash router (`#/`, `#/repo/:id/:tab`) so refreshes keep state without a router dependency.
- **Data**: `api.ts` (typed fetch wrapper with `ApiError`), `useRepository` polls `/api/repository/{id}` every 600 ms until the analysis settles, then loads findings and the walkthrough. `refreshFindings` triggers live re-verification.
- **Views**: `OverviewTab` (pipeline, Brain, agents, metrics, architecture, cross-validation, log), `FindingsTab`, `MemoryTab`, `WalkthroughTab`; a single `SourceViewer` modal is shared by all tabs.
- **Rendering safety**: repository content is rendered as React text nodes (custom highlighter), never via `innerHTML`.
- The Vite dev server proxies `/api` to `127.0.0.1:8000`, so the browser talks to one origin.

## Backend architecture

| Package | Responsibility |
| --- | --- |
| `api/` | Routes, Pydantic schemas, source viewer endpoints, dependency helpers |
| `evo_cli/` | `main.py` (commands), `console.py`, `orchestration.py` (live stream + agent tree), `views.py`, `solve_views.py`, `session.py` (interactive shell) |
| `orchestrator/` | `brain.py` (orchestration), `router.py` (task → agents), `context_builder.py`, `crossval.py`, `evidence.py` (records + live re-verification), `qa.py`, `pipeline.py`, `progress.py`, `fixing.py` (fix plans + apply), `solving.py` (solve sessions + the gate) |
| `agents/` | `base.py` contract, `team.py` (level-2 delegation runtime), `security.py` + `security_rules.py`, `duplicate.py`, `explainer.py` + `architecture.py`/`flows.py`/`history.py`, `walkthrough.py`, `fixer.py` + `fix_strategies.py`, `solver.py` |
| `memory/` | `database.py` (schema, WAL, corruption recovery), `store.py` (data access), `indexer.py`, `search.py`, `tokens.py` |
| `repo/` | `scanner.py`, `parser.py` (regex/brace-matching parser), `archive.py` (safe ZIP), `github.py`, `paths.py`, `filters.py`, `text.py` |
| `verification/` | `citations.py`: anchor (analysis time) and verify (any time) |
| `llm/` | `provider.py` (OpenAI via httpx, local mock), `prompts.py` (guardrail, redaction, content wrapping) |

`services.py` wires everything once (`build_services`), and `app_factory.create_app()` builds the FastAPI app (tests inject custom settings or providers).

## The Brain

```mermaid
stateDiagram-v2
    [*] --> understanding: task
    understanding --> retrieving: plan intents
    retrieving --> routing: context + memory focus
    routing --> running: agents queued with reasons
    running --> aggregating: wave 1 (security, duplicate, explainer) in parallel, wave 2 (walkthrough)
    aggregating --> cross_validating
    cross_validating --> verifying
    verifying --> memorizing
    memorizing --> completed
    running --> running: agent failure is isolated
```

1. **Understand**: `AgentRouter.plan()` maps task keywords to agents and records reasons; dependencies (Walkthrough → Explainer) are added automatically.
2. **Retrieve**: `ContextBuilder` loads indexed files from the working copy, parses them, runs memory searches per domain ("security focus") and recalls insights from the previous analysis.
3. **Route + execute**: agents without dependencies run concurrently; dependent agents get upstream results via `context.with_upstream()`. Each run has a timeout and is wrapped so exceptions become `status="failed"` results.
4. **Aggregate**: drafts (`FindingDraft`) from completed agents are collected.
5. **Cross-validate** (`crossval.py`): anchor every claim, reject unsupported ones, merge duplicates, resolve conflicts, add cross-agent annotations.
6. **Verify** (`evidence.py`): build finding records with SHA-256 anchors; verify Explainer and Walkthrough citations.
7. **Memorize**: replace findings, write finding/insight/history memories, diff with the previous run to record resolved findings.

## Agent architecture

Agents implement `BaseAgent.run(context) -> AgentResult`. They receive a read-only `AgentContext` (files, parsed symbols, memory reader, LLM provider, upstream results) and never touch the database, filesystem or each other. See [AGENTS.md](AGENTS.md).

## Memory engine

- **Passages**: one `symbol` passage per function/class/method, overlapping 40-line `code` windows, one `doc` passage per Markdown section, `config` windows.
- **FTS5**: `memories_fts(content, terms, path, symbol)` with `porter unicode61`; the `terms` column holds split camelCase/snake_case identifiers so "token verification" matches `verifyToken`.
- **Query planning**: stop-word removal, identifier detection, synonym expansion (auth → authentication, login, jwt …), prefix queries; every term is sanitized and quoted before `MATCH`.
- **Ranking**: BM25 (column weights content 1.0, terms 1.2, path 3.0, symbol 5.0) + boosts for exact identifiers, filename and symbol matches; overlapping passages are deduplicated.
- **Fallback**: if FTS5 is unavailable, a `LIKE` scan with term-frequency scoring is used.

## Database

```mermaid
erDiagram
    repositories ||--o{ files : contains
    repositories ||--o{ memories : remembers
    repositories ||--o{ findings : has
    repositories ||--o{ agent_runs : ran
    files ||--o{ memories : anchors
    repositories {
        text id PK
        text name
        text path
        text status
        text stage
        text brain_state
        json stats
        json report
        json timeline
        int analysis_count
    }
    files {
        int id PK
        text repository_id FK
        text path
        text language
        text kind
        text sha256
    }
    memories {
        int id PK
        int file_id FK
        text memory_type
        text content
        text symbol
        int line_start
        int line_end
        text sha256
    }
    findings {
        text id PK
        text agent
        text severity
        text title
        text file
        int line
        text status
        text sha256
        text file_sha256
        json verification
        json extra
    }
    agent_runs {
        int id PK
        int analysis_no
        text agent
        text status
        text mode
        json result
    }
```

`memories_fts` is an FTS5 table whose `rowid` equals `memories.id`. Connections are short-lived (thread-safe), WAL mode, foreign keys on.

## Source verification

```mermaid
flowchart LR
    Claim[Agent claim<br/>file · line · evidence] --> Path{safe path<br/>inside root?}
    Path -- no --> Unsafe[UNSAFE]
    Path -- yes --> Exists{file exists?}
    Exists -- no --> Missing[MISSING]
    Exists -- yes --> Line{line in range?}
    Line -- no --> Invalid[INVALID_LINE]
    Line -- yes --> Evidence{evidence at line?}
    Evidence -- no, found elsewhere --> Relocate[correct line number]
    Evidence -- nowhere --> Unsupported[UNSUPPORTED → rejected]
    Evidence -- yes --> Hash{file SHA-256 = indexed?}
    Relocate --> Hash
    Hash -- no --> Stale[STALE]
    Hash -- yes --> Verified[VERIFIED + SHA-256 of cited lines]
```

Later, `verify()` recomputes the SHA-256 of the cited lines: unchanged → VERIFIED (even if other parts of the file changed), changed → STALE with a relocation hint when identical lines moved. Hashes use one shared normalization (`repo/text.py`: CRLF→LF, trailing whitespace ignored) so scanner, indexer, verifier and source viewer always agree.

## Request lifecycle

```mermaid
sequenceDiagram
    participant UI
    participant API
    participant Pipeline
    participant Brain
    participant DB as SQLite
    UI->>API: POST /api/repository/analyze (ZIP)
    API->>API: stream to disk, size cap, safe extract
    API->>DB: create repository (queued)
    API-->>UI: 202 {repository_id, status: processing}
    API->>Pipeline: background task
    Pipeline->>DB: scan → files, index → memories + FTS5
    Pipeline->>Brain: analyze()
    Brain->>DB: stages, agent runs, timeline
    loop every 600 ms
        UI->>API: GET /api/repository/{id}
        API-->>UI: stage, brain state, agents, metrics
    end
    Brain->>DB: verified findings + insights
    UI->>API: GET /api/findings/{id} (live re-verify)
    UI->>API: POST /api/memory/search
```

## Repository ingestion

- **ZIP**: streamed to `uploads/` with a byte cap, validated, extracted to `repos/<id>/` (single top-level folder collapsed), then deleted.
- **GitHub**: URL validated against `https://github.com/<owner>/<repo>`; the archive URL is built by Evo Code; redirects are restricted to GitHub hosts over HTTPS.
- **Demo**: `demo-repo/` is copied into an isolated working copy, so the original is never modified.

## Security boundaries

Browser ↔ API (local only, no auth); API ↔ working copies (all file access through `resolve_in_root`); Brain ↔ LLM (redacted, wrapped repository content, JSON-only responses, every claim re-verified). Details in [SECURITY.md](SECURITY.md).

## Failure handling

| Layer | Strategy |
| --- | --- |
| Upload/extraction | Validation errors → HTTP 400/413 before any record is created |
| Pipeline | Exceptions recorded on the repository (`failed` + message); expected errors are user-facing |
| Agents | Timeout + exception isolation per agent; dependents run degraded |
| LLM | `LLMError` caught inside each agent → local fallback + note |
| Database | Corruption detected at startup (`PRAGMA quick_check`) → quarantine + recreate; runtime DB errors → HTTP 503 |
| Restart | In-flight analyses marked failed with a re-run hint |

## Future scaling architecture

For multi-user or large-repository use: move analysis to a job queue (e.g. a worker pool), store memories in PostgreSQL with `pg_trgm`/`tsvector` plus a vector index for semantic retrieval, keep working copies in object storage, run parsing in a sandboxed worker (gVisor/Firecracker), stream progress over SSE/WebSocket instead of polling, and add authentication and per-organization tenancy. The agent contract and verification layer stay unchanged.
