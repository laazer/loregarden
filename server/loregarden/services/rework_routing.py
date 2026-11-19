"""Who a rework round goes to: a hand-off between siblings, or the owner of the files.

Two shapes of rework round on a stage several scoped implementers share
(`agent_scope.AGENT_PATH_SCOPES`), measured on the live database for Oct 20 -
Nov 18 (23 rounds across 18 tickets):

- **A hand-off.** The implementer did its half and named its sibling for the
  rest. That is routing, not a rejection, and `rework_feedback` stops charging
  it to the loop cap (`handoff_target`).
- **A reject naming files.** A reviewer's finding in `server/` went back to
  keyword routing, which picked the frontend implementer again because the
  ticket's title and criteria had not changed; it said "not my scope" and only
  the round after reached the backend. Each such reject cost two rounds.
  `owner_of_finding` pins the implementer whose scope holds every file the
  finding names, the same pin a reject naming `reroute_to_agent` sets.
"""

from __future__ import annotations

import re

from loregarden.models.domain.schemas import WorkflowStageDef
from loregarden.services.agent_scope import AGENT_PATH_SCOPES, is_path_in_scope
from loregarden.services.finding_bar import is_judging
from loregarden.services.studio_routing import stage_offers_agent

# A repo-relative path a finding cites: `server/loregarden/x.py:12`,
# `client/src/components/X.tsx`. Bare paths (`services/x.py`) are not read:
# which tree they are in is a guess, and a wrong pin costs the round it saves.
_CITED_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.\w+)")


def handoff_target(
    stage: WorkflowStageDef | None, *, agent_id: str, target_stage: str, reroute_to_agent: str
) -> str:
    """The sibling this round hands the stage to, or "" when it is a rejection.

    A hand-off re-runs the reporting agent's own stage with another agent that
    stage runs. A judging stage never hands off: what it sends back is a reject.
    """
    named = (reroute_to_agent or "").strip()
    if stage is None or not named or named == agent_id or target_stage != stage.key:
        return ""
    if is_judging(agent_id, stage) or stage_offers_agent(stage, named) is None:
        return ""
    return named


def owner_of_finding(stage: WorkflowStageDef | None, context: str) -> str:
    """The one scoped agent of ``stage`` whose scope holds every file ``context`` cites.

    "" when the finding cites no scoped file, cites files in two agents' scopes,
    or names an agent ``stage`` does not run: keyword routing decides then, as
    before. Files outside every scope (`.lefthook/`, `agent_context/`) are not
    anyone's, so they neither pick an owner nor veto one.
    """
    if stage is None:
        return ""
    owners: set[str] = set()
    for path in _CITED_PATH.findall(context or ""):
        for agent_id, prefixes in AGENT_PATH_SCOPES.items():
            if is_path_in_scope(path, prefixes):
                owners.add(agent_id)
    if len(owners) != 1:
        return ""
    (owner,) = owners
    return owner if stage_offers_agent(stage, owner) is not None else ""
