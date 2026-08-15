"""The playtest gate must name the scenes to open, read off the ticket's branch."""

from loregarden.core.workflow_loader import UNRESOLVED_SCENES_ITEM
from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.gate_checklist import (
    expand_gate_checklist_for_ticket,
    resolve_playtest_scenes,
)
from loregarden.services.git_subprocess import run_git
from sqlmodel import Session, select


def _git(repo, *args) -> None:
    run_git(["-C", str(repo), *args], check=True, capture_output=True, text=True)


def _repo_with_branch(repo, branch: str, files: list[str]):
    _git(repo, "checkout", "-b", branch)
    for name in files:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[gd_scene format=3]\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"add {len(files)} files")
    _git(repo, "checkout", "main")


def _ticket_on(db_session: Session, branch: str) -> Ticket:
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    ticket = Ticket(
        external_id=f"scenes-{branch.replace('/', '-')}",
        workspace_id=ws.id,
        title="Dash movement",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        branch=branch,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _workspace_repo(db_session: Session):
    from loregarden.services.workspace_paths import resolve_workspace_root

    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    return resolve_workspace_root(ws)


def test_resolve_playtest_scenes_lists_scene_files_from_the_branch(db_session: Session):
    repo = _workspace_repo(db_session)
    _repo_with_branch(
        repo,
        "loregarden/dash",
        [
            "scenes/levels/sandbox/dash.tscn",
            "scripts/movement/movement.gd",
            "scenes/levels/sandbox/hub.tscn",
        ],
    )
    ticket = _ticket_on(db_session, "loregarden/dash")

    assert resolve_playtest_scenes(db_session, ticket) == [
        "scenes/levels/sandbox/dash.tscn",
        "scenes/levels/sandbox/hub.tscn",
    ]


def test_resolve_playtest_scenes_returns_empty_when_branch_touches_no_scene(db_session: Session):
    repo = _workspace_repo(db_session)
    _repo_with_branch(repo, "loregarden/docs-only", ["docs/notes.md"])
    ticket = _ticket_on(db_session, "loregarden/docs-only")

    assert resolve_playtest_scenes(db_session, ticket) == []


def test_resolve_playtest_scenes_returns_none_for_an_unknown_branch(db_session: Session):
    ticket = _ticket_on(db_session, "loregarden/never-created")

    assert resolve_playtest_scenes(db_session, ticket) is None


def test_expansion_names_the_scenes_the_branch_changes(db_session: Session):
    repo = _workspace_repo(db_session)
    _repo_with_branch(repo, "loregarden/expand", ["scenes/levels/sandbox/dash.tscn"])
    ticket = _ticket_on(db_session, "loregarden/expand")

    checklist = expand_gate_checklist_for_ticket(
        db_session, ticket, ["{{playtest_scenes}}", "Check for regressions"]
    )

    assert checklist == [
        "Open `scenes/levels/sandbox/dash.tscn` in the editor, run it, and play through this "
        "change — it must reach a playable state with no errors",
        "Check for regressions",
    ]


def test_expansion_keeps_a_generic_step_when_the_branch_is_missing(db_session: Session):
    ticket = _ticket_on(db_session, "loregarden/absent")

    assert expand_gate_checklist_for_ticket(db_session, ticket, ["{{playtest_scenes}}"]) == [
        UNRESOLVED_SCENES_ITEM
    ]
