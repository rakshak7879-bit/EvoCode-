# Roadmap

## MVP (this repository)

- ZIP, public GitHub URL and demo repository ingestion with safe extraction
- Scanner with ignore rules, sensitive-file skipping and SHA-256 per file
- SQLite + FTS5 memory: symbols, code windows, doc sections, configs, findings, insights, history
- Brain orchestrator: task routing with reasons, parallel agents, failure isolation, cross-validation, verification, memory updates
- Security (22 rules), Duplicate (structural clones + existing-implementation hints), Explainer (stack, flows, history), Walkthrough (2-minute tour) agents
- Source verification with VERIFIED / STALE / MISSING / INVALID_LINE / UNSUPPORTED, live re-verification, resolved-finding memory
- Memory Q&A with verified citations
- Optional OpenAI enrichment with redaction and prompt-injection guardrails
- React dashboard: Home, Overview, Findings, Memory, Walkthrough, source viewer
- 61 backend tests, strict TypeScript build

## V2

- **Embeddings and semantic retrieval** (hybrid BM25 + vector) behind the existing search interface
- **Tree-sitter parsing** for precise symbols across more languages
- **GitHub integration**: app installation, private repos, webhooks, incremental re-indexing by commit
- **Pull-request analysis**: findings introduced or resolved by a diff, verified against the PR head
- **Code review** comments with verified citations
- **CI/CD integration**: fail builds on new high-severity verified findings, SARIF export
- **Slack, Linear, Notion**: push verified findings and walkthroughs, create tickets with citations
- **IDE extensions** (VS Code / Kiro): inline verified findings and memory Q&A
- Streaming progress (SSE), authentication, multi-user workspaces

## V3

- **Organization-wide engineering memory** across repositories, decisions and incidents
- **Long-term agent memory**: agents learn from accepted/rejected findings
- **Distributed agent execution** in sandboxed workers
- **Team analytics**: risk trends, duplication trends, time-to-resolve
- **Personalized coding agents** grounded in verified team memory
- **Autonomous engineering workflows**: propose fixes, open PRs, and verify the fix removed the finding

Items in V2/V3 are intentionally not implemented in the MVP.
