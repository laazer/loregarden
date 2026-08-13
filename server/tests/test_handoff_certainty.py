"""What a handoff claim is worth, and whether it still holds.

The regression this guards is the one that made the field necessary: a required
item counted as met because its `evidence` string was non-empty, so "ran the
suite, all green" scored exactly as well as an attached test artifact. A claim
now has to name proof, and the proof has to still be about the current code.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import Artifact, ClaimCertainty, Ticket, Workspace
from loregarden.services.handoff_certainty import (
    ClaimStanding,
    certainty_of,
    standing_of,
    unresolvable_evidence,
)
from loregarden.services.handoff_store import latest_handoff_doc
from loregarden.services.handoff_writer import HandoffWriteError, write_handoff
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = make_repo(tmp_path, name="repo")
    (repo / "project_board" / "checkpoints").mkdir(parents=True)
    return repo


@pytest.fixture(name="ticket")
def ticket_fixture(session, repo):
    ws = Workspace(slug="wsx", name="WSX", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(external_id="t1-demo", workspace_id=ws.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _evidence(session, ticket, *, commit_sha: str) -> Artifact:
    artifact = Artifact(
        ticket_id=ticket.id,
        kind="evidence",
        title="suite green",
        evidence_kind="full_suite",
        commit_sha=commit_sha,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", name)
    return _head(repo)


def _item(**overrides) -> dict:
    item = {
        "item_key": "test_suite_complete",
        "item": "Test suite complete",
        "required": True,
        "status": "complete",
        "evidence": "ran it, all green",
    }
    item.update(overrides)
    return item


# -- levels -------------------------------------------------------------------


def test_an_omitted_certainty_is_inferred_not_proof():
    """Defaulting the other way would silently promote every claim ever written
    before this field existed."""
    assert certainty_of(_item()) == ClaimCertainty.INFERRED


def test_prose_evidence_alone_does_not_prove(session, ticket):
    standing = standing_of(session, ticket, _item())

    assert standing.certainty == ClaimCertainty.INFERRED
    assert not standing.proves


def test_a_human_confirmation_proves_without_an_artifact(session, ticket):
    """The sign-off is the evidence; there is no artifact to point at."""
    standing = standing_of(session, ticket, _item(certainty=ClaimCertainty.USER_CONFIRMED.value))

    assert standing.proves
    assert not standing.stale


def test_a_verified_claim_backed_at_the_current_commit_proves(session, ticket, repo):
    artifact = _evidence(session, ticket, commit_sha=_head(repo))

    standing = standing_of(
        session,
        ticket,
        _item(certainty="verified", evidence_artifact_id=artifact.id),
    )

    assert standing.proves
    assert not standing.stale


# -- staleness ----------------------------------------------------------------


def test_evidence_from_before_the_last_edit_goes_stale(session, ticket, repo):
    artifact = _evidence(session, ticket, commit_sha=_head(repo))
    _commit(repo, "changed.py")

    standing = standing_of(
        session,
        ticket,
        _item(certainty="verified", evidence_artifact_id=artifact.id),
    )

    assert standing.stale
    assert not standing.proves
    # The level it was claimed at survives: a stale VERIFIED and a stale
    # USER_CONFIRMED are not equally worrying, and collapsing both into a single
    # STALE level throws away which one you have.
    assert standing.certainty == ClaimCertainty.VERIFIED


def test_a_stale_flag_is_derived_never_written(session, ticket):
    """`stale` is not a level an agent can claim — the enum has no member for it,
    so naming one is rejected at the boundary."""
    with pytest.raises(ValueError):
        ClaimCertainty("stale")


def test_evidence_that_no_longer_exists_does_not_prove(session, ticket):
    """A stored handoff can outlive the artifact it points at."""
    standing = standing_of(
        session,
        ticket,
        _item(certainty="verified", evidence_artifact_id="gone-forever"),
    )

    assert not standing.proves


def test_an_unreadable_repo_does_not_make_everything_stale(session, ticket, tmp_path):
    """Nothing to compare is not the same as proof of change. Flagging it would
    fire on every workspace whose repo is not mounted; the boundary check is what
    catches an unreadable tree."""
    ws = session.get(Workspace, ticket.workspace_id)
    ws.repo_path = str(tmp_path / "not-a-repo")
    session.add(ws)
    session.commit()
    artifact = _evidence(session, ticket, commit_sha="")

    standing = standing_of(
        session,
        ticket,
        _item(certainty="verified", evidence_artifact_id=artifact.id),
    )

    assert not standing.stale


def test_standing_defaults_to_the_weak_claim():
    assert ClaimStanding().certainty == ClaimCertainty.INFERRED
    assert not ClaimStanding().proves


# -- the write path -----------------------------------------------------------


def test_verified_without_a_resolvable_artifact_is_reported_not_stored(session, ticket):
    result = write_handoff(
        session,
        ticket_id=ticket.external_id,
        workspace_slug="wsx",
        from_agent="test_designer",
        to_agent="test_breaker",
        checklist=[_item(certainty="verified", evidence_artifact_id="nope")],
    )

    assert result["status"] == "FAIL"
    assert result["artifact_id"] == ""
    assert any(v["rule"] == "handoff_evidence_unresolvable" for v in result["violations"])
    assert "test_suite_complete" in result["violations"][0]["message"]
    assert result["remediation_hints"]
    assert latest_handoff_doc(session, ticket.id) is None


def test_an_artifact_belonging_to_another_ticket_does_not_count(session, ticket, repo):
    other = Ticket(external_id="t2-other", workspace_id=ticket.workspace_id, title="other")
    session.add(other)
    session.commit()
    session.refresh(other)
    stolen = _evidence(session, other, commit_sha=_head(repo))

    bad = unresolvable_evidence(
        session, ticket, [_item(certainty="verified", evidence_artifact_id=stolen.id)]
    )

    assert bad == ["test_suite_complete"]


def test_every_bad_item_is_reported_at_once(session, ticket):
    """One exception per bad item would burn an agent turn apiece."""
    bad = unresolvable_evidence(
        session,
        ticket,
        [
            _item(item_key="a", certainty="verified"),
            _item(item_key="b", certainty="verified", evidence_artifact_id="missing"),
            _item(item_key="c"),
        ],
    )

    assert bad == ["a", "b"]


def test_an_unknown_certainty_is_rejected_at_the_boundary(session, ticket):
    with pytest.raises(HandoffWriteError, match="certainty must be one of"):
        write_handoff(
            session,
            ticket_id=ticket.external_id,
            workspace_slug="wsx",
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=[_item(certainty="probably")],
        )


def test_verified_items_count_toward_the_met_counter(session, ticket, repo):
    """The counter now tracks claims that stand, not strings that are non-empty."""
    artifact = _evidence(session, ticket, commit_sha=_head(repo))

    result = write_handoff(
        session,
        ticket_id=ticket.external_id,
        workspace_slug="wsx",
        from_agent="test_designer",
        to_agent="test_breaker",
        checklist=[
            _item(certainty="verified", evidence_artifact_id=artifact.id),
            _item(item_key="test_all_runnable", item="All tests runnable"),
        ],
    )

    assert result["required_items_met"] == 1
    assert result["total_required_items"] == 2


def test_stale_evidence_stops_counting(session, ticket, repo):
    artifact = _evidence(session, ticket, commit_sha=_head(repo))
    _commit(repo, "moved-on.py")

    result = write_handoff(
        session,
        ticket_id=ticket.external_id,
        workspace_slug="wsx",
        from_agent="test_designer",
        to_agent="test_breaker",
        checklist=[_item(certainty="verified", evidence_artifact_id=artifact.id)],
    )

    assert result["required_items_met"] == 0


def test_the_stored_document_carries_the_certainty(session, ticket):
    write_handoff(
        session,
        ticket_id=ticket.external_id,
        workspace_slug="wsx",
        from_agent="test_designer",
        to_agent="test_breaker",
        checklist=[_item(certainty="user_confirmed")],
    )

    doc = latest_handoff_doc(session, ticket.id)
    assert doc["handoff"]["checklist"][0]["certainty"] == "user_confirmed"
