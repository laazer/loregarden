"""A path a ticket names under a specialty's source root is direct routing evidence.

`lg-external-harness-954` is server-only by its own criteria, which name eleven
`server/` paths. Its ui-design criteria also describe the toast the change
produces ("button", "render", "client", "component"), and a synonym counts once
however often it appears, so it routed to `frontend_implementer` 6 to 5 on
synonyms alone. Measured over the 1,390 open classify decisions on 2026-10-14,
counting source paths changes exactly that one.
"""

from __future__ import annotations

import json

import pytest
from loregarden.models.domain import ClassifyRoute, Ticket, WorkflowStageDef
from loregarden.services import studio_routing
from loregarden.services.stage_shape import _route_label
from loregarden.services.studio_routing import resolve_classify_route

#: loregarden's implement roster as `studio-loregarden-tdd-v3` declares it.
ROUTES = [
    ClassifyRoute(
        languages=["typescript", "javascript"],
        specialties=["frontend"],
        agent_id="frontend_implementer",
    ),
    ClassifyRoute(specialties=["refactor"], agent_id="backend_implementer", skill_name="refactor"),
    ClassifyRoute(
        languages=["typescript", "javascript"],
        specialties=["refactor", "frontend"],
        agent_id="frontend_implementer",
        skill_name="refactor",
    ),
    ClassifyRoute(
        languages=["typescript", "javascript"],
        specialties=["backend"],
        agent_id="backend_implementer",
        default=True,
    ),
]
STAGE = WorkflowStageDef(
    key="implement",
    name="Implement",
    stage_type="classify",
    agent_id="backend_implementer",
    classify_routes=ROUTES,
)

#: 954's shape: UI words throughout, every file it changes under server/.
TOAST_PROSE = (
    "The error toast renders the message in the client, and the delete button stays on the page."
)
SERVER_ONLY = [
    TOAST_PROSE,
    "server/loregarden/services/worktree_lifecycle.py returns a Retirement.",
    "server/loregarden/services/baxter_chat_service.py raises the new message.",
    "server/loregarden/services/chat_branch_sweep.py records the reason.",
    "server/tests/test_chat_worktree_sandbox.py pins the exact string.",
    "No change under client/** and none to client/src/components/ToastHost.css.",
]


def _ticket(criteria: list[str], title: str = "A failed checkout removal reads as one") -> Ticket:
    return Ticket(
        external_id="t-1",
        workspace_id="ws",
        title=title,
        acceptance_criteria_json=json.dumps(criteria),
    )


def test_server_paths_outweigh_ui_prose_about_what_the_change_shows():
    assert resolve_classify_route(_ticket(SERVER_ONLY), STAGE) == ("backend_implementer", "")


def test_without_the_paths_the_same_prose_still_reads_as_frontend():
    # The control: the paths are what moved it, not a change to the synonyms.
    assert resolve_classify_route(_ticket([TOAST_PROSE]), STAGE)[0] == "frontend_implementer"


def test_client_paths_route_frontend_work_to_the_frontend_implementer():
    criteria = [
        "server/loregarden/api/ticket_handoff.py returns tree_warning.",
        "client/src/lib/terminalCommands.ts copies the command.",
        "client/src/state/toastStore.ts gains dismissToast.",
        "client/src/components/OverflowMenu.tsx returns focus.",
    ]
    assert resolve_classify_route(_ticket(criteria), STAGE)[0] == "frontend_implementer"


@pytest.mark.parametrize(
    "path",
    ["client/node_modules", "client/**", "client/.eslintcache", "server/.self-improve-restart"],
)
def test_tooling_and_glob_paths_are_not_evidence(path):
    # "A worktree without client/node_modules fails pre-push" is hook tooling,
    # not frontend work; the default route keeps it.
    ticket = _ticket([f"Nothing reads {path} any more."], title="Pre-push fails early")
    assert resolve_classify_route(ticket, STAGE) == ("backend_implementer", "")


def test_a_sentence_ending_on_a_path_does_not_change_the_path():
    ticket = _ticket(["Edit client/src/a.ts. Then client/src/a.ts again."])
    hay = studio_routing._classify_haystack(ticket)
    assert studio_routing._source_paths("client", hay) == {"client/src/a.ts"}


def test_a_path_inside_a_longer_path_does_not_count():
    # `blobert/client/x.ts` names someone else's client tree.
    ticket = _ticket(["vendor/client/src/a.ts and other/client/src/b.ts move."], title="Move")
    assert resolve_classify_route(ticket, STAGE) == ("backend_implementer", "")


# ---- the stage shape tells routes apart -----------------------------------------


def test_routes_with_the_same_agent_read_differently():
    labels = [_route_label(route) for route in ROUTES]
    assert len(set(labels)) == len(labels)
    assert "backend_implementer with skill refactor" in labels


def test_a_branching_route_names_where_it_goes():
    route = ClassifyRoute(agent_id="ticket_scoper", to_stage="test-design")
    assert _route_label(route) == "ticket_scoper then test-design"
