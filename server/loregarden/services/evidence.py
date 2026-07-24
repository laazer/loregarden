"""Evidence artifacts — what a stage offers as proof, and what it proves it against.

A stage used to close on its own `outcome=pass`, with green tests as the only
corroboration. Tests passing says the code does what its tests say; it does not
say the feature works on the surface a user touches. Evidence is the record of
that second claim, stamped with the commit it was captured against so a verifier
can tell proof from a leftover.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.models.domain import Artifact, Ticket, Workspace
from loregarden.services.git_commit_push_service import head_commit_sha, working_tree_paths
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

ARTIFACT_KIND = "evidence"

# What a piece of evidence is. Closed rather than free-form so a gate can ask
# "is there a real-surface proof for this commit?" and get a reliable answer.
EVIDENCE_KINDS = (
    # A failing-then-passing test — the floor. Cheap, and proves the logic.
    "test_red_green",
    # Output captured from the surface a user actually touches: an HTTP
    # response, a screenshot, a database row. The ceiling.
    "real_surface",
    # An independent verifier's confirm/refute of a stage's done-claim.
    "verify_verdict",
    # The full regression suite, green at a specific commit. Lets a later stage
    # (e.g. the gatekeeper review) trust it was already run rather than re-run it.
    "full_suite_green",
)

FULL_SUITE_EVIDENCE_KIND = "full_suite_green"


def resolve_head_sha(session: Session, ticket: Ticket) -> str:
    """HEAD of the ticket's workspace, or "" when it cannot be resolved."""
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        return ""
    return head_commit_sha(resolve_workspace_root(workspace))


def evidence_for_commit(
    session: Session,
    ticket: Ticket,
    *,
    commit_sha: str = "",
    evidence_kind: str = "",
) -> list[Artifact]:
    """Evidence attached to `ticket`, optionally narrowed to a commit and kind.

    Passing `commit_sha` is what makes this useful to a gate: evidence captured
    before the last source edit proves nothing about the code being gated.
    """
    query = select(Artifact).where(
        Artifact.ticket_id == ticket.id,
        Artifact.kind == ARTIFACT_KIND,
    )
    if commit_sha:
        query = query.where(Artifact.commit_sha == commit_sha)
    if evidence_kind:
        query = query.where(Artifact.evidence_kind == evidence_kind)
    return list(session.exec(query).all())


def has_evidence(
    session: Session,
    ticket: Ticket,
    *,
    commit_sha: str = "",
    evidence_kind: str = "",
) -> bool:
    """Whether any matching evidence exists — the predicate a gate blocks on."""
    return bool(
        evidence_for_commit(session, ticket, commit_sha=commit_sha, evidence_kind=evidence_kind)
    )


def evidence_kinds_at_head(session: Session, ticket: Ticket, repo_root: Path) -> set[str]:
    """The evidence kinds proven for the *exact current tree*: recorded at the
    current HEAD with a clean working tree.

    The clean-tree requirement closes the gap the commit stamp alone leaves:
    evidence is keyed only to HEAD, so an uncommitted edit would leave HEAD (and
    the evidence) unchanged while the tree a downstream stage sees is no longer
    the one that was proven. Empty when HEAD is unresolved or anything is dirty —
    the proof no longer covers what's there.
    """
    head = resolve_head_sha(session, ticket)
    if not head or working_tree_paths(repo_root):
        return set()
    return {
        artifact.evidence_kind
        for artifact in evidence_for_commit(session, ticket, commit_sha=head)
        if artifact.evidence_kind
    }


def full_suite_green_at_head(session: Session, ticket: Ticket, repo_root: Path) -> bool:
    """Whether the full suite is already proven green for the exact tree a stage
    is about to test — `full_suite_green` recorded at HEAD with a clean tree."""
    return FULL_SUITE_EVIDENCE_KIND in evidence_kinds_at_head(session, ticket, repo_root)
