"""Both ref-moving callers name the signing state of the commit they wrote (782).

`MergeOutcome.signed` exists so that a landing which produced the one kind of
commit the repository's own branch rules refuse is not reported as an
unqualified success. That is only true if the two places that move a ref say so
where anyone looks — the `logger.info` line each already emits (AC-A4).

**No test here asserts the wording**, which CLAUDE.md § *Code review agents*
forbids and the spec restates as R4. The oracle is differential: land the same
work twice, once with the decision True and once False, blank out the sha and
the names, and the two lines must still differ. A line that names the state
passes whatever words it chooses; a line that drops it cannot pass at all.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.git_merge_noco import MergeOutcome
from loregarden.services.land_ticket import LandSkip, land_ticket
from loregarden.services.target_branch import refresh_integration_branch, resolve_target_branch
from sqlmodel import Session
from tests.worktree_helpers import commit_on, git, make_repo

LAND_LOGGER = "loregarden.services.land_ticket"
TARGET_LOGGER = "loregarden.services.target_branch"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path)
    # Explicit, so the decision under test is the fixture's and not the host's.
    git(repo, "config", "commit.gpgsign", "false")
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo: Path) -> Workspace:
    ws = Workspace(slug="signing-report", name="signing-report", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace) -> Ticket:
    ms = Ticket(
        external_id="lg-ms-782",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


def _ticket_with_work(session, workspace, repo: Path, milestone, external_id: str) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Ticket {external_id}",
        branch=f"loregarden/{external_id}",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, f"{external_id}.txt", "work\n")
    return ticket


def _lines(caplog, logger_name: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger_name]


def _anonymise(line: str, *names: str) -> str:
    """The line with everything *but* the signing state removed.

    Shas and identifiers differ between two landings for reasons that have
    nothing to do with signing, so they are blanked before comparing. What is
    left is the prose, which is deliberately not pinned — only required to
    differ between the two decisions.
    """
    out = re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", line)
    for name in sorted(names, key=len, reverse=True):
        out = out.replace(name, "<name>")
    return out


# --- AC-A4: land_ticket ----------------------------------------------------


def test_a_landing_log_line_distinguishes_signed_from_unsigned(
    session, workspace, repo, milestone, caplog
):
    signed_ticket = _ticket_with_work(session, workspace, repo, milestone, "lg-r-signed")
    unsigned_ticket = _ticket_with_work(session, workspace, repo, milestone, "lg-r-unsigned")

    with caplog.at_level(logging.INFO, logger=LAND_LOGGER):
        with mock.patch(
            "loregarden.services.land_ticket.merge_without_checkout",
            return_value=MergeOutcome(ok=True, sha="a" * 40, signed=True),
        ):
            land_ticket(session, signed_ticket, workspace)
        signed_line = _lines(caplog, LAND_LOGGER)[-1]
        land_ticket(session, unsigned_ticket, workspace)
        unsigned_line = _lines(caplog, LAND_LOGGER)[-1]

    names = (
        signed_ticket.external_id,
        unsigned_ticket.external_id,
        signed_ticket.branch,
        unsigned_ticket.branch,
        signed_ticket.landed_branch or "",
        unsigned_ticket.landed_branch or "",
    )
    assert _anonymise(signed_line, *filter(None, names)) != _anonymise(
        unsigned_line, *filter(None, names)
    ), (
        "the landing line reads the same signed and unsigned, so it does not name "
        f"the state: {signed_line!r} vs {unsigned_line!r}"
    )


def test_a_landing_that_wrote_no_commit_reports_no_landing_line(
    session, workspace, repo, milestone, caplog
):
    """`signed is None` is not reported as a state — there is no line at all.

    Guards R2: a site that branched on truthiness would describe the
    `already_contained` short-circuit as an unsigned landing.
    """
    ticket = _ticket_with_work(session, workspace, repo, milestone, "lg-r-contained")
    land_ticket(session, ticket, workspace)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LAND_LOGGER):
        again = land_ticket(session, ticket, workspace)

    assert again.ok and again.skipped is LandSkip.ALREADY_LANDED
    assert not _lines(caplog, LAND_LOGGER), (
        f"nothing was written, so nothing is reported as written: {_lines(caplog, LAND_LOGGER)}"
    )


# --- AC-A4: target_branch._refresh_from_tips -------------------------------


def _integration_branch_behind_main(repo: Path) -> str:
    branch = "integration/lg-ms-782"
    git(repo, "branch", branch, "main")
    commit_on(repo, "main", "moved.txt", "base moved\n")
    return branch


def test_a_refresh_log_line_distinguishes_signed_from_unsigned(repo: Path, caplog):
    branch = _integration_branch_behind_main(repo)

    with caplog.at_level(logging.INFO, logger=TARGET_LOGGER):
        with mock.patch(
            "loregarden.services.target_branch.merge_tips",
            return_value=MergeOutcome(ok=True, sha="b" * 40, signed=True),
        ):
            assert refresh_integration_branch(repo, branch, "main")
        signed_line = _lines(caplog, TARGET_LOGGER)[-1]
        assert refresh_integration_branch(repo, branch, "main")
        unsigned_line = _lines(caplog, TARGET_LOGGER)[-1]

    assert _anonymise(signed_line, branch, "main") != _anonymise(unsigned_line, branch, "main"), (
        "the refresh line reads the same signed and unsigned, so it does not name "
        f"the state: {signed_line!r} vs {unsigned_line!r}"
    )


def test_a_refresh_with_nothing_to_merge_reports_no_refresh_line(repo: Path, caplog):
    branch = _integration_branch_behind_main(repo)
    refresh_integration_branch(repo, branch, "main")

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=TARGET_LOGGER):
        assert refresh_integration_branch(repo, branch, "main") is False

    assert not _lines(caplog, TARGET_LOGGER), (
        f"nothing was merged, so nothing is reported as merged: {_lines(caplog, TARGET_LOGGER)}"
    )
