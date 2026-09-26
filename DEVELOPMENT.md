# Development

## Prerequisites

- Python **3.11+** (uses `StrEnum`; developed and tested on 3.14) with SQLite FTS5 (included in python.org and Homebrew builds)
- Node.js **20.19+ or 22.12+** (Vite 8 requirement; tested on 24) and npm

Check FTS5: `python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)'); print('FTS5 OK')"`. Without FTS5 Evo Code still works using a slower `LIKE` fallback.

## Get the code

```bash
git clone <repository-url> evo-code
cd evo-code
```

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # runtime only
pip install -r requirements-dev.txt  # + pytest (recommended)
uvicorn main:app --reload
```

The API runs on http://127.0.0.1:8000 (OpenAPI docs at `/docs`). The SQLite database and working copies are created in `../.evo-data/` (project root) so `--reload` never restarts the server when repositories are extracted.

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to `http://127.0.0.1:8000`. To serve a production build: `npm run build && npm run preview` (http://localhost:4173, also proxied). To point the SPA at another backend origin, set `VITE_API_URL` (and add that origin to `EVO_CORS_ORIGINS` on the backend).

## Environment variables

Copy the template and edit as needed (both the project root and `backend/` are checked; real environment variables win):

```bash
cp .env.example .env
```

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | empty | Empty = deterministic local analysis (UI shows **DEMO / LOCAL ANALYSIS**) |
| `OPENAI_MODEL` | `gpt-4o-mini` | Any chat model that supports JSON mode |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible servers work too |
| `EVO_LLM_PROVIDER` | `auto` | `mock` forces local mode even with a key |
| `EVO_LLM_TIMEOUT_SECONDS` | `45` | Per LLM request |
| `EVO_DATA_DIR` | `.evo-data` | Relative paths resolve from the project root |
| `EVO_MAX_UPLOAD_MB` | `50` | |
| `EVO_MAX_EXTRACTED_MB` | `200` | |
| `EVO_MAX_FILE_KB` | `512` | Larger files are skipped and listed in stats |
| `EVO_MAX_FILES` | `3000` | |
| `EVO_AGENT_TIMEOUT_SECONDS` | `120` | |
| `EVO_PACING_MS` | `350` | Demo pacing per Brain stage; `0` for instant runs |
| `EVO_ALLOW_SOURCE_EDITS` | `true` | Source viewer "Simulate a code change" |
| `EVO_SIMULATE_AGENT_FAILURE` | empty | Comma-separated agent names to force-fail |
| `EVO_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | |
| `EVO_LOG_LEVEL` / `EVO_LOG_FORMAT` | `INFO` / `text` | `json` for one JSON object per line |
| `GITHUB_TOKEN` | empty | Private repos, higher limits |
| `EVO_DEMO_REPO` | `demo-repo` | Path of the bundled demo repository |

## Tests

```bash
cd backend && source .venv/bin/activate
pytest                       # 61 tests: scanner, memory, verification, agents, API
pytest tests/test_api.py -k stale -v
```

Tests use a temporary data directory, `EVO_LLM_PROVIDER=mock` behavior and zero pacing; a `FakeLLM` covers LLM-mode validation (hallucinated claims rejected, secrets redacted from prompts, LLM failures falling back to local mode).

Frontend: `npm run build` runs `tsc -b` (strict) and the production build; `npm run typecheck` runs only the type check.

## Useful commands

```bash
# Analyze the demo repo from the CLI
curl -F use_demo=true http://127.0.0.1:8000/api/repository/analyze
# Instant analyses while developing
EVO_PACING_MS=0 uvicorn main:app --reload
# Structured JSON logs
EVO_LOG_FORMAT=json uvicorn main:app
# Reset all local data (deletes every stored analysis)
rm -rf .evo-data
```

## Project layout

See [README.md](README.md#project-structure) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| UI says "Backend offline" | Start the backend on port 8000; check `curl http://127.0.0.1:8000/api/health` |
| `address already in use` | Another process uses 8000/5173: `lsof -i :8000` |
| GitHub analysis fails with rate limit | Set `GITHUB_TOKEN` or retry later |
| "Repository not found" after restarting | The analysis lives in a different `EVO_DATA_DIR`; start a new one |
| Analysis marked failed after restart | In-flight analyses cannot survive a restart; click Re-run |
| Database recovered warning in logs | A corrupted `evo.db` was moved to `evo.db.corrupt-<timestamp>` and recreated |
