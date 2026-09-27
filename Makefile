# Evo Code — common tasks. Run `make` or `make help` for the list.
#
# Nothing here needs a network connection or an API key except `install`.
# Targets that start a long-running server say so; stop them with Ctrl-C.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON ?= python3
BACKEND := backend
FRONTEND := frontend
VENV := $(BACKEND)/.venv
VENV_PYTHON := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip
PYTEST := $(VENV_PYTHON) -m pytest
EVO := ./evo
DATA_DIR ?= .evo-data

# Passed to `make analyze`, `make solve`, `make fix`, `make test-repo`.
REPO ?= .
ISSUE ?=
GOAL ?=
SESSION ?=
TASK ?=
ARGS ?=

# Run the Brain without demo pacing unless the caller asks for it.
FAST_ENV := EVO_PACING_MS=0

.PHONY: help
help: ## Show this help
	@echo "Evo Code — make targets"
	@echo
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Variables: REPO=path|url  ISSUE=text  GOAL=text  SESSION=id  TASK=text  ARGS='--flags'"
	@echo "Examples:"
	@echo "  make install && make demo"
	@echo "  make analyze REPO=https://github.com/owner/repo"
	@echo "  make solve REPO=. ISSUE=\"split_lines drops bare carriage returns\""
	@echo "  make fix GOAL=\"the hardcoded secrets\""

# --------------------------------------------------------------------------- setup
.PHONY: install
install: $(VENV_PYTHON) ## Create the venv and install backend dependencies (needs network)
	@$(VENV_PIP) install --quiet --upgrade pip
	@$(VENV_PIP) install --quiet -r $(BACKEND)/requirements-dev.txt
	@echo "Backend ready. Try: make demo"

$(VENV_PYTHON):
	@echo "Creating $(VENV) with $(PYTHON)…"
	@$(PYTHON) -m venv $(VENV)

.PHONY: install-frontend
install-frontend: ## Install dependencies for the optional dashboard (needs network)
	@cd $(FRONTEND) && npm install

.PHONY: install-all
install-all: install install-frontend ## Install backend and dashboard dependencies

.PHONY: doctor
doctor: ## Check the environment: Python, FTS5, venv, node, git, Strix
	@echo "python:   $$($(PYTHON) --version 2>&1)"
	@echo "venv:     $$(test -x $(VENV_PYTHON) && $(VENV_PYTHON) --version 2>&1 || echo 'missing — run make install')"
	@echo "pytest:   $$(test -x $(VENV)/bin/pytest && $(VENV)/bin/pytest --version 2>&1 | head -1 || echo 'missing')"
	@echo "fts5:     $$($(PYTHON) -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)'); print('enabled')" 2>/dev/null || echo 'unavailable (LIKE fallback)')"
	@echo "node:     $$(node --version 2>/dev/null || echo 'not installed (only needed for the dashboard)')"
	@echo "git:      $$(git --version 2>/dev/null || echo 'not installed (needed for make apply)')"
	@echo "strix:    $$(command -v strix >/dev/null && echo 'installed' || echo 'not installed (optional deep scan)')"
	@test -x $(VENV_PYTHON) && $(EVO) mode || true

# --------------------------------------------------------------------------- tests
.PHONY: test
test: test-backend ## Run the backend test suite (alias for test-backend)

.PHONY: test-backend
test-backend: $(VENV_PYTHON) ## Run all backend tests
	@cd $(BACKEND) && .venv/bin/python -m pytest -q

.PHONY: test-fast
test-fast: $(VENV_PYTHON) ## Run backend tests, stopping at the first failure
	@cd $(BACKEND) && .venv/bin/python -m pytest -q -x

.PHONY: test-harness
test-harness: $(VENV_PYTHON) ## Run only the solve-harness tests (test runner, Solver, gate, CLI)
	@cd $(BACKEND) && .venv/bin/python -m pytest -q tests/test_solve.py

.PHONY: test-orchestration
test-orchestration: $(VENV_PYTHON) ## Run only the multi-level orchestration tests
	@cd $(BACKEND) && .venv/bin/python -m pytest -q tests/test_orchestration.py tests/test_cli.py

.PHONY: typecheck
typecheck: ## Type-check the dashboard (strict TypeScript)
	@cd $(FRONTEND) && npm run typecheck

.PHONY: build-frontend
build-frontend: ## Type-check and build the optional dashboard
	@cd $(FRONTEND) && npm run build

.PHONY: check-all
check-all: test-backend build-frontend ## Everything CI should run

# --------------------------------------------------------------------------- using Evo Code
.PHONY: demo
demo: $(VENV_PYTHON) ## Analyze the bundled ShopLite repo, then open the shell
	@$(EVO) demo $(ARGS)

.PHONY: analyze
analyze: $(VENV_PYTHON) ## Analyze a repository: make analyze REPO=path|zip|url [TASK="..."]
	@$(FAST_ENV) $(EVO) analyze $(REPO) $(if $(TASK),--task "$(TASK)",) $(ARGS)

.PHONY: open
open: $(VENV_PYTHON) ## Open the interactive shell for the latest analysis (or REPO=id|url)
	@$(EVO) open $(if $(filter-out .,$(REPO)),$(REPO),) $(ARGS)

.PHONY: tree
tree: $(VENV_PYTHON) ## Show the Brain -> lead agents -> sub-agents tree
	@$(EVO) tree $(if $(filter-out .,$(REPO)),$(REPO),) $(ARGS)

.PHONY: findings
findings: $(VENV_PYTHON) ## List verified findings of the latest analysis
	@$(EVO) findings $(if $(filter-out .,$(REPO)),$(REPO),) $(ARGS)

.PHONY: fix
fix: $(VENV_PYTHON) ## Propose and apply verified patches: make fix [GOAL="..."] [ARGS=--dry-run]
	@$(FAST_ENV) $(EVO) fix $(if $(filter-out .,$(REPO)),$(REPO),) $(if $(GOAL),--goal "$(GOAL)",) $(ARGS)

.PHONY: mode
mode: $(VENV_PYTHON) ## Show the LLM/local mode, data directory and agent roster
	@$(EVO) mode

# --------------------------------------------------------------------------- the solve harness
.PHONY: solve
solve: $(VENV_PYTHON) ## Break an issue down: make solve REPO=. ISSUE="..." (or ISSUE_FILE=path)
ifndef ISSUE
ifndef ISSUE_FILE
	$(error Provide ISSUE="the problem" or ISSUE_FILE=path/to/issue.md)
endif
endif
	@$(FAST_ENV) $(EVO) solve --repo $(REPO) \
		$(if $(ISSUE_FILE),--issue-file $(ISSUE_FILE),--issue "$(ISSUE)") $(ARGS)

.PHONY: plan
plan: $(VENV_PYTHON) ## Show the fix plan of a solve session: make plan [SESSION=id]
	@$(EVO) plan $(if $(SESSION),--session $(SESSION),) $(ARGS)

.PHONY: test-repo
test-repo: $(VENV_PYTHON) ## Run a target repository's own tests: make test-repo [SESSION=id | REPO=path]
	@$(EVO) test $(if $(SESSION),--session $(SESSION),--repo $(REPO)) $(ARGS)

.PHONY: gate
gate: $(VENV_PYTHON) ## The solve gate: is the issue fixed? make gate [SESSION=id]
	@$(EVO) check $(if $(SESSION),--session $(SESSION),) $(ARGS)

.PHONY: sessions
sessions: $(VENV_PYTHON) ## List solve sessions and their gate status
	@$(EVO) sessions $(ARGS)

.PHONY: scan
scan: $(VENV_PYTHON) ## Optional deep security scan with Strix: make scan REPO=. (needs Docker + key)
	@$(EVO) scan --repo $(REPO) $(ARGS)

# --------------------------------------------------------------------------- servers (long-running)
.PHONY: api
api: $(VENV_PYTHON) ## Start the optional HTTP API on 127.0.0.1:8000 (Ctrl-C to stop)
	@cd $(BACKEND) && .venv/bin/uvicorn main:app --reload

.PHONY: dashboard
dashboard: ## Start the optional dashboard on localhost:5173 (needs `make api` too)
	@cd $(FRONTEND) && npm run dev

# --------------------------------------------------------------------------- housekeeping
.PHONY: clean
clean: ## Remove caches and build output (keeps your analyses)
	@find . -name __pycache__ -type d -prune -not -path './$(VENV)/*' -exec rm -rf {} + 2>/dev/null || true
	@rm -rf $(BACKEND)/.pytest_cache $(FRONTEND)/dist
	@echo "Removed caches and build output."

.PHONY: clean-data
clean-data: ## Delete every stored analysis, solve session and patch (.evo-data)
	@rm -rf $(DATA_DIR)
	@echo "Removed $(DATA_DIR)."

.PHONY: distclean
distclean: clean clean-data ## Also remove the venv and node_modules
	@rm -rf $(VENV) $(FRONTEND)/node_modules
	@echo "Removed $(VENV) and node_modules. Run make install to start over."
