# API Reference

Base URL: `http://127.0.0.1:8000` (the Vite dev server proxies `/api` from `http://localhost:5173`). All responses are JSON. Interactive OpenAPI docs: `/docs`.

> The API has **no authentication** and is intended for local use only. See [SECURITY.md](SECURITY.md).

Repository ids are 16 lowercase hex characters. Ids that do not match `^[a-f0-9]{8,32}$` return `422`.

## Errors

Errors use FastAPI's shape:

```json
{ "detail": "The uploaded file is not a valid ZIP archive." }
```

| Status | Meaning |
| --- | --- |
| 400 | Invalid input (bad ZIP, unsafe path in archive, non-GitHub URL, both/neither source given, invalid source edit) |
| 403 | Source edits disabled (`EVO_ALLOW_SOURCE_EDITS=false`) |
| 404 | Repository / file / walkthrough not found |
| 409 | An analysis is already running for this repository |
| 413 | Upload or file too large |
| 422 | Validation error (malformed id, empty query …) |
| 500 | Unexpected server error (details only in server logs) |
| 503 | Memory database unavailable |

---

## `GET /api/health`

```json
{
  "status": "ok",
  "version": "0.1.0",
  "llm": { "provider": "local", "model": "deterministic-rules", "available": false, "mode": "local", "label": "DEMO / LOCAL ANALYSIS" },
  "fts5": true,
  "database_recovered": false,
  "allow_source_edits": true,
  "demo_repo_available": true,
  "limits": { "max_upload_mb": 50, "max_file_kb": 512, "max_files": 3000 }
}
```

## `POST /api/repository/analyze`

`multipart/form-data`. Provide **exactly one** source.

| Field | Type | Description |
| --- | --- | --- |
| `file` | file (`.zip`) | Repository archive |
| `github_url` | string | `https://github.com/<owner>/<repo>` (optionally `/tree/<branch>`) |
| `use_demo` | `true` | Analyze the bundled demo repository |
| `task` | string, optional | Instruction for the Brain (drives agent routing). Default: full analysis. |

```bash
curl -F file=@my-repo.zip http://127.0.0.1:8000/api/repository/analyze
curl -F github_url=https://github.com/octocat/Hello-World http://127.0.0.1:8000/api/repository/analyze
curl -F use_demo=true -F "task=Only run a security audit" http://127.0.0.1:8000/api/repository/analyze
```

`202 Accepted`

```json
{ "repository_id": "542f382cb8864d66", "status": "processing" }
```

ZIP validation and extraction happen before the response (errors → `400`/`413`); scanning, indexing and the Brain run in the background. GitHub downloads also run in the background (errors appear in the status as `failed`).

## `GET /api/repository/{id}`

Live analysis status (poll until `status` is `completed` or `failed`).

```json
{
  "repository_id": "542f382cb8864d66",
  "name": "demo-repo (ShopLite)",
  "source": "demo",
  "status": "completed",
  "stage": "completed",
  "brain": { "state": "complete", "stage": "completed", "message": "Verified intelligence ready" },
  "files": 9,
  "agents_completed": 4,
  "agents_total": 4,
  "findings": 16,
  "analysis_count": 1,
  "metrics": {
    "files_analyzed": 9, "memory_entries": 62, "security_findings": 12,
    "duplicate_clusters": 4, "verified_findings": 16, "stale_findings": 0, "total_findings": 16
  },
  "agents": [
    { "name": "security", "title": "Security Agent", "status": "complete", "mode": "local",
      "summary": "12 findings analyzed across 5 file(s)", "reason": "Task mentions 'security'", "duration_ms": 877, "error": null }
  ],
  "timeline": [
    { "ts": "2026-09-26T20:03:10.101+00:00", "elapsed_ms": 1805, "stage": "routing", "level": "info",
      "message": "Route → Security Agent: Task mentions 'security'", "agent": "security" }
  ],
  "stats": { "languages": { "javascript": 7, "markdown": 1, "json": 1 }, "skipped": {}, "memory": { "memories": 40 } },
  "report": {
    "plan": { "intents": ["security", "duplicate", "explainer", "walkthrough"], "agents": [], "skipped": [] },
    "cross_validation": { "candidates": 16, "accepted": 16, "merged": [], "conflicts": [], "unsupported": [], "relocated": [], "checks": [] },
    "verification": { "findings_verified": 16, "findings_total": 16, "claims_verified": 56, "claims_total": 56 },
    "history": { "analysis_no": 1, "resolved": [], "new": [] }
  },
  "llm": { "mode": "local", "label": "DEMO / LOCAL ANALYSIS" }
}
```

`status`: `queued | processing | completed | failed`. `stage`: `queued, downloading, extracting, scanning, indexing, understanding, retrieving, routing, running, aggregating, cross_validating, verifying, memorizing, completed, failed`. Agent `status`: `pending | queued | running | complete | failed | skipped`. Metrics that are not yet known are `null`.

## `POST /api/repository/{id}/reanalyze`

Optional JSON body `{ "task": "..." }`. Re-runs scan → index → Brain on the current working copy (including simulated edits). Returns `202 {repository_id, status}`; `409` if already running. After completion, `report.history.resolved` lists findings that disappeared.

## `GET /api/findings/{id}?verify=true`

Returns findings grouped by category. With `verify=true` (default) every finding and duplicate location is re-verified against the working copy and the new status is persisted.

```json
{
  "repository_id": "542f382cb8864d66",
  "mode": "local",
  "security": [
    {
      "id": "3f0c9a1d2b7e4c55",
      "agent": "security",
      "category": "security",
      "severity": "high",
      "title": "Hardcoded API key",
      "description": "A credential is assigned directly in source code…",
      "file": "backend/payments.js",
      "line": 7,
      "line_end": 7,
      "evidence": "const API_KEY = \"sk-d[REDACTED]\";",
      "recommendation": "Move the value to environment configuration or a secret manager and rotate the exposed credential.",
      "confidence": 0.9,
      "source": "rule",
      "rule_id": "hardcoded-secret",
      "status": "verified",
      "sha256": "c723ee95267edbfd…",
      "file_sha256": "d75cefd0e7405a5e…",
      "verification": {
        "status": "verified",
        "message": "Source confirmed against current repository.",
        "file_sha256_indexed": "d75cefd0…", "file_sha256_current": "d75cefd0…",
        "evidence_sha256_expected": "c723ee95…", "evidence_sha256_current": "c723ee95…",
        "file_changed": false, "lines_changed": false, "relocated_line": null,
        "checked_at": "2026-09-26T20:03:14+00:00"
      },
      "locations": [],
      "annotations": [],
      "detectors": ["rule:hardcoded-secret"],
      "cwe": "CWE-798"
    }
  ],
  "duplicates": [
    {
      "title": "Email validation duplicated in 4 places",
      "similarity": 1.0,
      "locations": [
        { "file": "utils/validation.js", "line": 3, "symbol": "validateEmail", "role": "canonical", "verification": { "status": "verified" } },
        { "file": "backend/users.js", "line": 8, "symbol": "validateUserEmail", "role": "copy", "verification": { "status": "verified" } }
      ],
      "recommendation": "Reuse the existing `validateEmail` in utils/validation.js:3 (already implemented and exported)…"
    }
  ],
  "architecture": { "project_name": "ShopLite", "summary": "…", "architecture": {}, "request_flows": [], "important_files": [], "history": [] },
  "verification": { "total": 16, "verified": 16, "stale": 0, "missing": 0, "other": 0, "checked_at": "…", "live": true },
  "cross_validation": { "checks": [] },
  "history": { "resolved": [] }
}
```

Verification `status`: `verified | stale | missing | invalid_line | unsupported | unsafe_path`.

## `POST /api/memory/search`

```json
{ "repository_id": "542f382cb8864d66", "query": "Where is authentication implemented?", "limit": 6 }
```

`query`: 1–500 characters; `limit`: 1–20.

```json
{
  "query": "Where is authentication implemented?",
  "intent": "location",
  "mode": "local",
  "answer": "Authentication is implemented primarily in:\n1. backend/auth.js — `requireAuth`, `hashPassword`, … (lines 1-40, 31-69)\n…",
  "terms": ["authentication", "auth", "login", "jwt", "token", "session", "password"],
  "citations": [
    {
      "memory_id": 12, "file": "backend/auth.js", "line_start": 1, "line_end": 40,
      "symbol": null, "memory_type": "code", "score": 9.24,
      "snippet": "// Authentication routes and middleware…",
      "verification": { "status": "verified", "message": "Source confirmed against current repository." }
    }
  ],
  "related": [ { "memory_type": "finding", "verification": { "status": "verified" } } ],
  "verification": { "verified": 6, "total": 6 },
  "notes": []
}
```

`intent`: `location | security | duplicate | history | architecture | general`. Snippets and answers never repeat detected secret values.

## `GET /api/walkthrough/{id}`

```json
{
  "mode": "local",
  "question": "If I had 2 minutes to explain this repository to a judge, what would I show?",
  "total_duration": 120,
  "target_seconds": 120,
  "steps": [
    {
      "index": 1, "key": "overview", "title": "Project Overview", "duration": 15,
      "files": ["README.md"],
      "citations": [{ "file": "README.md", "line_start": 1, "line_end": 3, "label": "Project intro", "verification": { "status": "verified" } }],
      "talking_points": ["Frontend: Vanilla JavaScript (browser DOM)"],
      "narration": "ShopLite is a small e-commerce store…"
    }
  ],
  "verification": { "verified": 26, "total": 26 }
}
```

`404` when the Walkthrough Agent did not complete in the latest analysis.

## `GET /api/source/{id}?path=backend/payments.js`

Only indexed files can be read.

```json
{
  "path": "backend/payments.js", "language": "javascript", "content": "// Payment processing routes.\n…",
  "line_count": 49, "size": 1622,
  "sha256_current": "d75cefd0…", "sha256_indexed": "d75cefd0…", "changed": false, "editable": true
}
```

`400` for traversal/absolute paths, `404` for files that are not indexed or no longer exist.

## `POST /api/source/{id}/edit`

Replaces one line in Evo Code's **working copy** (never the original upload or `demo-repo/`) to demonstrate stale detection.

```json
{ "path": "backend/payments.js", "line": 7, "content": "const API_KEY = process.env.PAYMENT_API_KEY;" }
```

```json
{ "path": "backend/payments.js", "line": 7, "previous_content": "const API_KEY = \"sk-demo-secret\";",
  "sha256_before": "d75cefd0…", "sha256_after": "433c3f56…" }
```

`400` (multi-line content, line out of range, non-UTF-8 file, unsafe path), `403` (disabled), `409` (analysis running).

## `GET /api/repositories`

The 12 most recent analyses: `[{ repository_id, name, source, status, created_at, updated_at }]`.
