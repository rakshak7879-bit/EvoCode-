# Evo Code CLI

The terminal is Evo Code's primary interface. It runs the scanner, memory engine, Brain and agents directly in one process—no API server or browser required.

Three workflows:

| Command | What it does |
| --- | --- |
| `./evo analyze .` · `./evo demo` | Understand a repository: verified findings, architecture, searchable memory |
| `./evo fix` | Propose reviewed, verified patches for those findings |
| `./evo solve --issue "..."` | Break an issue down, locate the code, prove a fix works ([HARNESS.md](HARNESS.md)) |

## Install

From the project root:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
./evo --version
```

The launcher uses `backend/.venv/bin/python` and prints an exact setup command if the environment is missing.

## Start

```bash
./evo                 # interactive start wizard
./evo demo            # analyze bundled ShopLite repo, then enter the shell
./evo analyze ./repo  # local folder (copied into an isolated working copy)
./evo analyze repo.zip
./evo analyze https://github.com/owner/repository
```

During analysis the CLI streams the real Brain timeline:

```text
  ◉  E V O   C O D E
     Your AI Code Brain · verified source-level intelligence

Analysis pipeline
Repository → Scanner → Memory → Brain → Agents → Verification
 +00.01s  scanning           · Discovered 9 files (javascript 7, markdown 1, json 1)
 +00.02s  indexing           ✓ Indexed 40 memory passages · 21 symbols
 +00.04s  routing            · Route → Security Agent: Task mentions 'security'
 +00.06s  running            ✓ Duplicate Agent completed: 4 duplicate clusters
 +00.07s  verifying          ✓ Source verification: 16/16 findings verified
 +00.08s  completed          ✓ Analysis complete: verified intelligence ready
```

`./evo demo` and `./evo analyze` enter the interactive shell when attached to a terminal. Add `--no-shell` for one-shot use.

## Interactive shell

Prompt: `evo[REPO_ID] ›`

| Command | Purpose |
| --- | --- |
| `status` | Repository, orchestration, files, memory, finding and verification totals |
| `repos` | Local analyses stored in `.evo-data/evo.db` |
| `use ID` | Switch repository (an unambiguous 8-character prefix works) |
| `tree` | The whole L0 Brain → L1 lead agents → L2 sub-agents tree |
| `agents` | Lead agents with their sub-agents: status, timing and what each found |
| `log [N] [L0\|L1\|L2]` | Last N Brain orchestration events, optionally only up to a level |
| `findings [all\|security\|duplicate] [all\|verified\|stale]` | Re-verify and list findings |
| `show N` | Full finding, evidence, recommendation, hash and cited source |
| `source PATH [LINE[-END]]` | Show source with highlighted lines |
| `architecture` | Project summary, stack, traced flows and important files |
| `walkthrough` | Presentation-ready two-minute repository tour |
| `ask QUESTION` | Query FTS5 memory and verify every returned citation |
| `verify` | Recompute current source hashes for all findings |
| `fix [GOAL]` | Propose verified patches for the findings, then apply the ones you approve |
| `edit PATH LINE` | Prompt for a one-line edit in Evo Code's isolated working copy |
| `reanalyze [TASK]` | Re-scan that copy; update findings and resolved-history memory |
| `mode` | LLM/local mode, FTS5 availability, data path and the agent roster |
| `help`, `clear`, `exit` | Session controls (Tab completes commands, arrow keys recall history) |

The shell also reads piped commands, so a session can be scripted:

```bash
printf 'tree\nfindings security\nshow 3\n' | ./evo open REPO_ID
```

Example:

```text
evo[a1b2c3d4] › findings security
#   SEVERITY   SOURCE     FINDING                  LOCATION
1   HIGH       VERIFIED   Hardcoded API key        backend/payments.js:7

evo[a1b2c3d4] › show 1
Finding 1: Hardcoded API key
  Location: backend/payments.js:7
  SHA-256 (cited lines): c723ee95267edbfd…
  Verification: Source confirmed against current repository.

evo[a1b2c3d4] › ask Where is authentication implemented?
Brain answer
  Authentication is implemented primarily in:
  1. backend/auth.js — `requireAuth`, `hashPassword` …
  Sources
    [1] VERIFIED  backend/auth.js:1-40
  ✓ 6/6 cited sources verified against current source.
```

## One-shot commands

Every interactive view is also a direct command. Where a repository is expected you can pass the id,
an unambiguous id prefix, or the source you analyzed (a GitHub URL, a folder path or the repository
name); omit it entirely for the most recent analysis.

```bash
./evo repos
./evo status [REPO_ID]
./evo agents [REPO_ID]
./evo findings [REPO_ID] --category security --status verified --limit 10
./evo show REPO_ID 3
./evo source REPO_ID backend/auth.js --line 25-42 --context 8
./evo ask REPO_ID "Where is authentication implemented?"
./evo architecture [REPO_ID]
./evo walkthrough [REPO_ID]
./evo verify [REPO_ID]
./evo log [REPO_ID] --limit 50
./evo reanalyze REPO_ID --task "Only run a security audit"
```

### Fixing findings

```bash
./evo fix                                     # latest analysis, all security findings
./evo fix "the hardcoded secrets"             # a focused goal narrows the Fixer's team
./evo fix https://github.com/owner/repo       # any repository you analyzed: URL, folder, name or id
./evo fix --dry-run                           # only show the patches
./evo fix --yes                               # apply without asking
```

`fix` is also a command inside the interactive shell, where it acts on the repository you have open.
Every applied patch is exported to `.evo-data/patches/` so you can `git apply` it to your real repo.

The Fixer Agent delegates to five specialists: a Fix Planner picks the findings in scope, a Patch
Writer rewrites them with line-preserving strategies, a Safety Reviewer rejects anything
out of scope or unsafe, and a Fix Verifier re-runs all 22 security rules on each patched file. A
patch is only offered when the finding is gone and no new finding appears. With an API key an LLM
Patch Writer joins the team for cases the deterministic strategies cannot express.

Patches apply to Evo Code's isolated working copy only, never your original repository, and the
analysis re-runs afterwards so memory records the findings as resolved. Findings that cannot be
fixed safely (replacing `eval`, changing a password hash, moving tokens out of `localStorage`) are
listed with the reason instead of being patched.

### Solving an issue

```bash
./evo solve --repo . --issue "split_lines() drops bare carriage returns"
./evo solve --repo . --issue-file issue.md --isolated   # work on a copy
./evo test --covering            # run the tests the Test Scout found
./evo apply --patch fix.patch    # apply a unified diff
./evo check                      # the gate: exit 0 solved, exit 2 not yet
./evo report                     # what the issue was, how it was solved, and the proof
./evo sessions                   # every solve session and its gate status
```

`solve` never changes code: it breaks the issue down, ranks the suspect code with verified
citations, detects the test runner and captures the failing baseline. Writing the patch is the
model's (or your) job; `check` decides whether it worked. Exit codes and JSON shapes are in
[HARNESS.md](HARNESS.md).

### Optional deep security scan

```bash
./evo scan --check                     # can Strix run here?
./evo scan --mode quick --max-budget 5 # delegate to Strix (external tool)
```

[Strix](https://github.com/usestrix/strix) is an open-source AI pentesting tool that runs the target
in a Docker sandbox and validates findings with real exploits. It is entirely optional: it needs its
own CLI, Docker and `LLM_API_KEY`, and when any of those is missing `scan` says so and everything
else keeps working. Evo Code's own security specialists never execute repository code.

### Stale-source demonstration

```bash
./evo findings REPO_ID                 # note the Hardcoded API key number
./evo source REPO_ID backend/payments.js --line 5-10
./evo edit REPO_ID backend/payments.js 7 'const API_KEY = process.env.PAYMENT_API_KEY;'
./evo verify REPO_ID                   # 15/16 verified · 1 stale
./evo reanalyze REPO_ID                # issue disappears; memory records it resolved
```

The edit only touches `.evo-data/repos/<id>/`, never your source folder or ZIP. Disable editing with `EVO_ALLOW_SOURCE_EDITS=false`.

## Automation and JSON

Place global options before the subcommand:

```bash
./evo --json demo --no-shell > report.json
./evo --json findings REPO_ID > findings.json
./evo --json ask REPO_ID "API_KEY"
./evo --data-dir /tmp/evo-run --no-color analyze ./repo --no-shell
```

`--json` disables color and progress output for commands that support machine-readable output. Exit codes: `0` success, `1` invalid input/failed analysis, `130` interrupted.

## Options

```text
--data-dir PATH   SQLite, sessions, patches and working copies (default .evo-data)
--no-color        disable ANSI styles (`NO_COLOR=1` works too)
--json            machine-readable output where supported
--verbose         print backend logs to stderr
--version         CLI version
```

Exit codes: `0` success, `1` invalid input or a failed operation, `2` the `check` gate failed (or
Strix found vulnerabilities), `3` the test suite failed, `130` interrupted.

Analysis behavior still uses variables in [`.env.example`](.env.example): `OPENAI_API_KEY`, `EVO_PACING_MS`, size limits, agent timeout, failure simulation and more.

## Safety

The CLI has the same boundaries as the API: repository code is never executed, local folders are copied before analysis, safe ZIP extraction blocks traversal and symlinks, sensitive files such as `.env` are skipped without reading, source access is restricted to indexed paths, and secrets are redacted before optional LLM calls. See [SECURITY.md](SECURITY.md).
