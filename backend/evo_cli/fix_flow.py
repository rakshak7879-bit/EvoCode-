"""The fix workflow, shared by ``./evo fix`` and the interactive shell's ``fix``.

Plan patches with the Fixer Agent, show them, ask for approval unless it was
given up front, apply the approved ones to Evo Code's isolated working copy and
re-run the analysis so memory records the findings as resolved.
"""

from __future__ import annotations

import json
from typing import Any

from evo_cli.console import Console, Style
from evo_cli.orchestration import format_event
from evo_cli.repository import rerun_analysis
from evo_cli.solve_views import print_fix_plan
from evo_cli.views import print_history, print_repository_summary
from services import Services


async def run_fix(
    services: Services,
    console: Console,
    repo: dict[str, Any],
    goal: str,
    *,
    assume_yes: bool = False,
    dry_run: bool = False,
    reanalyze: bool = True,
    json_output: bool = False,
) -> tuple[bool, int]:
    """Returns ``(something_was_applied, patch_count)``. Raises ValueError on a fix failure."""
    if not json_output:
        console.heading("Fixing verified findings", "L0 Evo Brain → L1 Fixer Agent → L2 specialist sub-agents")
    sink = None if json_output else (lambda event: console.write(format_event(console, event,
                                                                            truncate=console.is_tty)))
    plan = await services.fixes.plan(repo["id"], goal or "fix all security issues", sink)
    if plan.error:
        raise ValueError(plan.error)
    if not json_output:
        print_fix_plan(console, plan)

    verified = plan.verified
    if dry_run or not verified:
        if json_output:
            console.write(json.dumps(plan.to_dict(), indent=2))
        elif not verified:
            console.write()
            console.warning("No patch could be verified automatically; the findings above need a human.")
        return False, len(verified)

    if not assume_yes:
        if json_output:
            console.write(json.dumps({**plan.to_dict(), "applied": [], "note": "pass --yes to apply"}, indent=2))
            return False, len(verified)
        console.write()
        answer = console.prompt(f"Apply {len(verified)} verified patch(es) to the working copy? [y/N] › ")
        if (answer or "").strip().lower() not in {"y", "yes"}:
            console.write(console.paint("Nothing was changed.", Style.DIM))
            return False, len(verified)

    outcome = services.fixes.apply(repo["id"], verified)
    if not json_output:
        console.write()
        console.success(f"Applied {len(outcome.applied)} patch(es) to {len(outcome.files)} file(s) in the "
                        "isolated working copy. Your original repository was not changed.")
        for proposal, reason in outcome.conflicts:
            console.warning(f"{proposal.id} skipped: {reason}")
        if outcome.patch_path:
            console.info(f"Patch saved: {outcome.patch_path} (apply it to your repo with `git apply`)")
    if reanalyze and outcome.applied:
        updated = await rerun_analysis(services, repo["id"], console, json_output=json_output)
        if not json_output:
            print_repository_summary(console, services, updated)
            print_history(console, updated)
    if json_output:
        console.write(json.dumps({**plan.to_dict(), **outcome.to_dict()}, indent=2))
    return bool(outcome.applied), len(verified)
