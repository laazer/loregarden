"""`loregarden eval gates` — the gate-based eval scorecard, read from the database.

    loregarden eval gates                          # table, all workspaces
    loregarden eval gates --workspace blobert --since 2026-07-28
    loregarden eval gates --json [--episodes]      # the full scorecard (and episodes)

Read-only and deterministic; see `services.gate_eval` for the definitions.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timezone

from loregarden.cli.errors import UsageError
from loregarden.db.session import engine
from loregarden.services.gate_eval import GateScorecard, ScoreLine, gate_scorecard
from sqlmodel import Session

_COLUMNS = (
    ("episodes", "eps"),
    ("first_attempt_clean_rate", "clean1st"),
    ("first_pass_rate", "pass1st"),
    ("harness_blocked_rate", "harness"),
    ("repair_episodes", "repairs"),
    ("repair_recovery_rate", "recover"),
    ("identical_resubmission_rate", "identical"),
    ("attempts_to_pass_median", "med"),
    ("attempts_to_pass_max", "max"),
)


def _day(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    except ValueError as exc:
        raise UsageError(f"not a YYYY-MM-DD date: {value!r}") from exc


def _cell(key: str, value: float | int | None) -> str:
    if value is None:
        return "-"
    # Rates and the median are fractional; counts and the max are not.
    return f"{value:.2f}" if key.endswith(("_rate", "_median")) else str(value)


def _row(label: str, line: ScoreLine) -> str:
    data = line.model_dump()
    return f"{label:<40} " + " ".join(f"{_cell(key, data[key]):>9}" for key, _ in _COLUMNS)


def render_table(card: GateScorecard) -> str:
    header = f"{'':<40} " + " ".join(f"{name:>9}" for _, name in _COLUMNS)
    lines = [header, _row("ALL", card.overall)]
    for ws, line in card.by_workspace.items():
        lines.append(_row(ws, line))
        lines += [_row(f"  {tr}", tl) for tr, tl in card.by_transition[ws].items()]
    lines.append("")
    lines.append("by producer (adapter/model of first attempt):")
    lines += [_row(f"  {key}", line) for key, line in card.by_producer.items()]
    lines.append("")
    lines.append("harness causes: " + json.dumps(card.harness_causes))
    lines.append(f"producer joins: {card.producer_coverage.model_dump_json()}")
    lines.append(f"unknown failures: {len(card.unknown_failures)}")
    lines.append(f"unreadable events: {card.unreadable_events}")
    lines.append(f"never passed ({len(card.never_passed)}):")
    lines += [
        f"  {n.workspace} {n.ticket_ref} {n.transition} attempts={n.attempts} "
        f"state={n.ticket_state.value} kinds={','.join(k.value for k in n.failure_kinds)}"
        for n in card.never_passed
    ]
    return "\n".join(lines)


def _run(args: argparse.Namespace) -> str:
    since, until = _day(args.since), _day(args.until)
    with Session(engine) as session:
        card, episodes = gate_scorecard(
            session, workspace_slug=args.workspace, since=since, until=until
        )
    if not args.json:
        return render_table(card)
    out = {"scorecard": card.model_dump(mode="json")}
    if args.episodes:
        out["episodes"] = [ep.model_dump(mode="json") for ep in episodes]
    return json.dumps(out, indent=2, sort_keys=True)


def register(sub: argparse._SubParsersAction) -> None:
    gates = sub.add_parser("gates", help="Scorecard of agent performance against transition gates.")
    gates.add_argument("--workspace", help="Workspace slug; all when omitted.")
    gates.add_argument("--since", help="Only events on or after this day (YYYY-MM-DD, UTC).")
    gates.add_argument("--until", help="Only events before this day (YYYY-MM-DD, UTC).")
    gates.add_argument("--json", action="store_true", help="Emit the scorecard as JSON.")
    gates.add_argument(
        "--episodes", action="store_true", help="With --json, include every episode."
    )
    gates.set_defaults(run=_run)
