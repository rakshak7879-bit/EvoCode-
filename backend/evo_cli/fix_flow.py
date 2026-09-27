"""The fix workflow, shared by ``./evo fix`` and the interactive shell's ``fix``.

Plan patches with the Fixer Agent, show them, ask for approval unless it was
given up front, apply the approved ones to Evo Code's isolated working copy and
re-run the analysis so memory records the findings as resolved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evo_cli.console import Console, Style
from evo_cli.orchestration import format_event
from evo_cli.repository import rerun_analysis
from evo_cli.solve_views import print_fix_plan
from evo_cli.views import print_history, print_repository_summary
from services import Services


@dataclass(frozen=True)
class FixOutcome:
    """What the fix flow did, so callers can choose an exit code."""

    in_scope: int          # findings the goal selected
    verified: int          # patches that passed review and verification
    applied: int           # patches written to the working copy
    manual: int            # findings handed back to a human


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
) -> FixOutcome:
    """Plan, review and optionally apply fixes. Raises ValueError if the Fixer itself failed."""
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
    counts = FixOutcome(plan.in_scope, len(verified), 0, len(plan.manual))
    if dry_run or not verified:
        if json_output:
            console.write(json.dumps(plan.to_dict(), indent=2))
        elif not verified:
            console.write()
            if not plan.in_scope:
                console.info(f"Nothing matched this goal ({plan.scope}). "
                             "Run `./evo findings` to see what was found.")
            elif plan.manual:
                console.warning(f"None of the {plan.in_scope} finding(s) in scope can be fixed safely and "
                                "automatically; the reasons are listed above.")
            else:
                console.warning("No patch could be verified automatically.")
        return counts

    if not assume_yes:
        if json_output:
            console.write(json.dumps({**plan.to_dict(), "applied": [], "note": "pass --yes to apply"}, indent=2))
            return counts
        console.write()
        answer = console.prompt(f"Apply {len(verified)} verified patch(es) to the working copy? [y/N] › ")
        if (answer or "").strip().lower() not in {"y", "yes"}:
            console.write(console.paint("Nothing was changed.", Style.DIM))
            return counts

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
    return FixOutcome(plan.in_scope, len(verified), len(outcome.applied), len(plan.manual))
