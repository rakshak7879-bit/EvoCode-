# Contributing

Thanks for helping improve Evo Code.

## Setup

Follow [DEVELOPMENT.md](DEVELOPMENT.md): backend in `backend/` (Python venv), frontend in `frontend/` (npm).

## Workflow

1. Create a branch from `main` (`feature/<topic>` or `fix/<topic>`).
2. Keep changes focused; update docs when behavior or APIs change.
3. Run the checks below before opening a pull request.
4. Open a PR with a short summary, what you tested, and screenshots for UI changes.

## Checks

```bash
cd backend && source .venv/bin/activate && pytest
cd frontend && npm run build
```

Both must pass. Add or update tests for every behavior change (fixtures in `backend/tests/conftest.py` provide an indexed demo repository, an `AgentContext`, API clients and a `FakeLLM`).

## Code style

- **Python**: type hints everywhere, Pydantic models for contracts, small functions, `logging.getLogger("evo.<area>")` with structured `extra={...}`, parameterized SQL only, no new dependencies without discussion.
- **TypeScript**: strict mode, no `any`, `import type` for types, components in `components/`, tab views in `views/`, data access only through `api.ts`.
- **UI**: follow [DESIGN.md](DESIGN.md); pair colors with text/icons; support keyboard use and reduced motion.

## Adding things

- **A security rule**: add a `SecurityRule` to `backend/agents/security_rules.py` (single-line evidence, CWE, recommendation) and a precision test (a positive and a negative example) in `tests/test_agents.py`.
- **An agent**: see [AGENTS.md](AGENTS.md#how-to-create-a-new-agent).
- **An API endpoint**: Pydantic response model in `api/schemas.py`, route in `api/`, test in `tests/test_api.py`, docs in [API.md](API.md).

## Security

Treat repository content as untrusted in every change: no execution, all file access through `repo/paths.resolve_in_root`, no secrets in logs or responses, and every new claim type must be verifiable. Report vulnerabilities privately (see [SECURITY.md](SECURITY.md)).

## Demo repository

`demo-repo/` is intentionally vulnerable. If you change it, update the expectations in the tests and in [DEMO.md](DEMO.md).
