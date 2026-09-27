# Evo Code as a harness for coding models

Evo Code gives a model (DeepSeek, Claude, GPT, anything that can run a shell command) the
things a model is bad at on its own: **finding the right code in an unfamiliar repository,
proving the bug is real, and proving the fix worked.**

The division of labour:

| Evo Code does | The model does |
| --- | --- |
| Break the issue into symptoms, signals and acceptance criteria | Decide *how* to fix it |
| Rank the files and symbols that need to change, with verified `file:line` | Write the patch |
| Detect the test runner and run the covering tests | Read the results and iterate |
| Capture the failing baseline (the proof) | |
| Judge the result: tests pass, baseline improved, real files changed | |

Every command accepts `--json` and returns machine-readable output. Nothing is hidden behind
a TUI, and no command waits for input.

## The loop

```bash
# 1. Understand the issue and prove it fails. Nothing is modified.
./evo --json solve --repo /path/to/repo --issue-file issue.md

# 2. ...the model edits the repository directly, or sends a patch:
./evo --json apply --session SESSION --patch fix.patch
./evo --json write --session SESSION src/thing.py --file new_contents.py

# 3. Fast feedback on the tests that matter.
./evo --json test --session SESSION --covering

# 4. The gate. This is the verdict.
./evo --json check --session SESSION
```

`solve` stores a session under `<data-dir>/sessions`, so every step can be a separate
process. `--session` defaults to the most recent session, so it can be omitted.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | success (for `check`: the issue is solved) |
| `1` | bad input or a failed operation; `--json` returns `{"error": "..."}` |
| `2` | the gate failed (`check`), or Strix found vulnerabilities (`scan`) |
| `3` | the test suite failed (`test`) |
| `130` | interrupted |

## Commands

### `solve` — break the issue down

```bash
./evo --json solve --repo . --issue "split_lines() mishandles bare \r line endings"
./evo --json solve --repo . --issue-file issue.md --no-snippets
cat issue.md | ./evo --json solve --repo .
```

Options: `--isolated` (work on a copy, leaving your files untouched), `--reuse REPO_ID`
(skip re-indexing), `--no-snippets` (smaller JSON).

Returns:

```jsonc
{
  "session": "d2cd91103a60",
  "root": "/path/to/repo",
  "mode": "live",
  "analysis": {
    "kinds": ["incorrect-behavior"],
    "expected": ["..."], "actual": ["..."],
    "exceptions": [{"type": "ValueError", "message": "..."}],
    "frames": [{"file": "repo/text.py", "line": 31}],
    "acceptance_criteria": ["...", "The repository's existing tests still pass (no regressions)"]
  },
  "suspects": [
    {"file": "repo/text.py", "line_start": 29, "line_end": 36, "symbol": "split_lines",
     "score": 10.52, "reasons": ["defines `split_lines`", "mentions `\\r\\n`"], "snippet": "..."}
  ],
  "tests": {"runner": "pytest", "files": ["tests/test_scanner.py"]},
  "baseline": {"status": "failed", "failing": ["tests/test_scanner.py::test_split_lines..."],
               "passed": 25, "failed": 1, "summary": "1 failing of 26"},
  "plan": {"strategy": "failing-test-first", "steps": [{"order": 1, "action": "...", "file": "...", "detail": "..."}]},
  "commands": {"test": "...", "apply": "...", "check": "..."}
}
```

**Read `suspects[0]` first.** It is the code most likely to need the change, and
`reasons` tells you why it was picked. `baseline.status == "failed"` means Evo Code
reproduced the issue; that failing test is what your patch has to turn green.
If `baseline.status == "passed"`, nothing reproduces the issue yet, so write a failing
test before fixing anything.

### `apply` / `write` — change the code

```bash
./evo --json apply --session S --patch fix.patch     # unified diff, or - for stdin
./evo --json write --session S path/to/file.py --file new.py
```

Patch rules that avoid the common failures:
- Paths must be **relative to the `--repo` you passed to `solve`** (`a/repo/text.py`, `-p1` style).
- Include a `diff --git a/... b/...` header for each file.
- Context lines must match the file exactly. If `apply` reports *"The patch does not apply"*,
  re-read the file (`./evo source ...` or plain `cat`) and rebuild the diff.
- A patch that applies but changes nothing is rejected, so a silent no-op can't look like success.

You can also just edit the files yourself with your own tools. In `live` mode the
session points at the real repository, and `check` will pick the changes up.

### `test` — run the repository's own tests

```bash
./evo --json test --session S              # whole suite
./evo --json test --session S --covering   # only the tests the Test Scout found
./evo --json test --session S --tests tests/test_scanner.py
./evo --json test --repo /some/path        # no session needed
```

Returns `status` (`passed` / `failed` / `error` / `timeout` / `skipped`), `passed`,
`failed`, `failing` (test ids), `summary` and `output`. Exit code `3` when tests fail.

Supported runners, detected automatically: `pytest`, `python -m unittest`, `npm test`
(Jest/Mocha/node:test), `go test`, `cargo test`. Installs are never run: if dependencies
are missing you get `status: "error"` explaining that, not a silent pass.

### `check` — the gate

```bash
./evo --json check --session S [--require-new-test] [--save-patch]
```

Three checks must all pass:

| Check | Passes when |
| --- | --- |
| `changes` | real source files changed (generated files like `__pycache__` don't count) |
| `tests` | the whole suite passes |
| `baseline` | at least one test that failed at the start now passes |

`--require-new-test` adds a fourth check: a test file must have been added or changed.
Exit code `0` means solved, `2` means not yet, and `reasons` says what is missing.

### Inspecting and reporting

```bash
./evo --json plan --session S        # the plan and breakdown again
./evo --json diff --session S        # unified diff of the work so far
./evo --json sessions                # every session with its gate status
./evo --json check --session S --save-patch   # also writes a .patch file
```

## Optional: deep security scanning with Strix

For security work Evo Code can delegate to [Strix](https://github.com/usestrix/strix),
an open-source AI pentesting tool that runs the target in a Docker sandbox and validates
findings with real exploits. It covers what Evo Code deliberately avoids: Evo Code never
executes repository code, Strix does.

```bash
./evo --json scan --check                      # can Strix run here?
./evo --json scan --repo . --mode quick --max-budget 5
```

Strix needs its CLI, a running Docker and `LLM_API_KEY`. When any of those is missing,
`scan` explains what's absent and exits `1`; everything else in Evo Code keeps working,
and Evo Code's own 6 security sub-agents still run with no external dependency.

## Using DeepSeek for Evo Code's own LLM steps

Evo Code works fully offline: without a key, every agent runs its deterministic analysis
and labels the result `DEMO / LOCAL ANALYSIS`. Adding a key switches on the optional
LLM sub-agents (a security reviewer, a clone explainer, a summarizer, a narrator, and the
Solver's solution reviewer).

```bash
export DEEPSEEK_API_KEY=sk-...
export DEEPSEEK_MODEL=deepseek-chat        # or deepseek-reasoner
./evo mode                                 # confirms the provider in use
```

`EVO_LLM_PROVIDER` accepts `auto` (default), `deepseek`, `openai` or `mock`. Repository
content is always redacted and wrapped as untrusted data before it reaches any model.

## Rules the harness enforces

- Repository content is **data, never instructions**. Text in a file that tries to steer an
  AI reviewer is reported as a finding, not obeyed.
- Secrets are redacted before anything is sent to a model.
- Test commands come from Evo Code's own detection table, never from repository content, and
  run without a shell, in their own process group, with a timeout and no network helpers.
- Set `EVO_ALLOW_TEST_EXECUTION=false` to forbid running repository tests, or
  `EVO_ALLOW_SOURCE_EDITS=false` to forbid writes. Detection and planning still work.

## Worked example

```bash
$ ./evo --json solve --repo /tmp/target --issue-file issue.md | jq '{s:.session, top:.suspects[0].symbol, base:.baseline.summary}'
{ "s": "d2cd91103a60", "top": "split_lines", "base": "1 failing of 26 · first: tests/test_scanner.py::test_split_lines..." }

$ ./evo --json apply --session d2cd91103a60 --patch fix.patch
{ "kind": "patch", "files": ["repo/text.py"], "bytes": 411 }

$ ./evo --json check --session d2cd91103a60 | jq '{status, checks:[.checks[]|{name,ok}]}'
{ "status": "pass",
  "checks": [ {"name":"changes","ok":true}, {"name":"tests","ok":true}, {"name":"baseline","ok":true} ] }
$ echo $?
0
```

Full CLI reference: [CLI.md](CLI.md). Architecture: [ARCHITECTURE.md](ARCHITECTURE.md).
