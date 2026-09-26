# Architectural Decisions

Short ADR-style records. Each lists the decision, the reason and the trade-off accepted.

## D1. SQLite FTS5 instead of a vector database
**Decision:** Memory is SQLite with FTS5 (BM25) and SHA-256 hashes.
**Reason:** The MVP needs fast, reliable, zero-infrastructure local retrieval. Code questions are dominated by identifiers and keywords, which lexical search handles well, especially with split-identifier terms and synonyms.
**Trade-off:** No semantic similarity for paraphrased questions. Embeddings are planned for V2 behind the same search interface.

## D2. SHA-256 verification of every citation
**Decision:** Findings and citations store the SHA-256 of the cited lines (and of the file); status is recomputed on demand.
**Reason:** AI-generated citations must stay tied to current source. Hashing is cheap, deterministic and explainable.
**Trade-off:** Line-level hashes flag a finding as STALE when lines move; a relocation hint is shown and a re-run refreshes the citation.

## D3. Line hash decides VERIFIED vs STALE, not the whole-file hash
**Decision:** A finding stays VERIFIED if its cited lines are unchanged, even if other parts of the file changed (the file drift is still reported).
**Reason:** Precision: editing one function should not invalidate every finding in the file.
**Trade-off:** A change elsewhere could theoretically affect the finding's context (e.g., a sanitizer added upstream). Re-running the analysis resolves it.

## D4. Central Brain instead of agent-to-agent communication
**Decision:** Agents are isolated functions of an `AgentContext`; the Brain passes upstream results explicitly.
**Reason:** Centralized orchestration is deterministic, debuggable and makes cross-validation possible. Failures stay contained.
**Trade-off:** Less "emergent" collaboration between agents, which the MVP does not need.

## D5. Deterministic analysis first, LLM as an optional enhancer
**Decision:** Every agent has a complete local implementation. With an API key, the LLM adds candidate findings, explanations and narration; all LLM claims go through the same verification.
**Reason:** The demo must never depend on an external API, results must be reproducible, and the product must be honest about what ran.
**Trade-off:** Local explanations are template-based and less fluent.

## D6. Regex + brace-matching parser instead of full ASTs
**Decision:** `repo/parser.py` extracts symbols, routes, calls and data access with regexes and brace/indent matching for JS/TS/Python (Go functions too).
**Reason:** No native dependencies, works on partial or malformed files, fast enough for hackathon scope. Downstream verification catches parser mistakes.
**Trade-off:** Edge cases (regex literals with quotes, exotic syntax) can produce imprecise spans. Tree-sitter is the V2 path.

## D7. Structural clone detection with normalized token shingles
**Decision:** Normalize identifiers/literals, keep keywords and property names, compare 5-token shingles with Jaccard ≥ 0.72.
**Reason:** Catches renamed copy-paste (the most common duplication) with explainable scores and no model.
**Trade-off:** Semantic duplicates with different structure are not found locally (LLM mode can explain, not discover, them).

## D8. Prefer existing shared code in duplicate recommendations
**Decision:** If a cluster member lives in `utils/`, `lib/`, `shared/` …, it is the canonical copy and the recommendation says "reuse the existing …".
**Reason:** "What has already been implemented" is more actionable than "extract a helper".

## D9. Background task + polling instead of WebSockets
**Decision:** Analyses run as FastAPI background tasks; the UI polls status every 600 ms.
**Reason:** Simple, robust through proxies, and the status endpoint doubles as the API for scripts.
**Trade-off:** Analyses do not survive a restart (they are marked failed); no multi-worker scaling.

## D10. Demo pacing is explicit and configurable
**Decision:** `EVO_PACING_MS` keeps each Brain stage visible for a minimum time.
**Reason:** A sub-second local analysis is invisible in a demo. Pacing only delays, never changes, results, and it is off in tests (`0`).

## D11. Isolated working copies and simulated edits
**Decision:** Every analysis works on its own copy; the source viewer can replace one line in that copy.
**Reason:** Makes the stale-detection moment reliable in a live demo without touching user files or the bundled demo repo.
**Trade-off:** A write endpoint exists; it is path-guarded, single-line, UTF-8 only and can be disabled.

## D12. Repository content is data, never instructions
**Decision:** Guardrail system prompt, tagged and escaped content, secret redaction, JSON-only answers, no tools for the model, evidence-checked claims.
**Reason:** Uploaded repositories can contain prompt injections.

## D13. No authentication in the MVP
**Decision:** Local, single-user tool bound to `127.0.0.1`.
**Reason:** Scope. Clearly documented in README and SECURITY.md; required before any shared deployment.

## D14. Hash router and hand-written highlighter in the frontend
**Decision:** No router or syntax-highlighting dependencies.
**Reason:** Fewer dependencies, full control, repository text rendered only as React text nodes (no HTML injection).

## D15. GitHub via public archive download
**Decision:** Without a token, download `github.com/<owner>/<repo>/archive/HEAD.zip`; with a token, use the REST zipball endpoint. Hosts are allow-listed.
**Reason:** The anonymous REST API allows only 60 requests/hour; the archive endpoint does not use that quota. No `git` binary or hooks involved.
