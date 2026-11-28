"""Fixtures shared by the asserted-not-proven suites (spec `6d686e57`).

Two suites need the same well-formed claim and the same ticket: one pins the
record's validation and lifecycle, the other pins the enumeration and the PR
section. A second copy of this payload would drift, and a drifted copy still
passes its own assertions.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    Artifact,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from sqlmodel import Session

#: A BEHAVIOUR claim with every required field answered. The owed artifact is a
#: resolvable `path.py::test_name`, as R2 requires of every document-shaped kind.
BEHAVIOUR_CLAIM: dict[str, Any] = {
    "claim": "the worktree reaper never removes a checkout with uncommitted work",
    "member_kind": "behaviour",
    "qualifies_because": "observed once while drafting; no checked-in test exercises it",
    "owed_artifact": "server/tests/test_worktree_lifecycle.py::test_reaper_keeps_dirty_checkouts",
    "fallback": "refuse every removal and report the cost to the operator — named, not built",
    "upstream_id": "lg-code-knowledge-1055",
    "citation": "WG §.lefthook/scripts/workspace-gates.sh:34",
}

#: A CODE_SITE claim: the owed artifact is the marked line, and the ceiling is
#: the quantity that lets the member be closed rather than only accumulate.
CODE_SITE_CLAIM: dict[str, Any] = {
    "claim": "the artifact listing loads every row of a ticket before it filters",
    "member_kind": "code_site",
    "qualifies_because": "a knowingly insufficient read, marked at the site with ceiling:",
    "owed_artifact": "server/loregarden/services/ticket_documents.py:196",
    "fallback": "page the read and merge per page — named, not built",
    "upstream_id": "lg-code-knowledge-1055",
    "citation": "TD §services/ticket_documents.py:196",
    "ceiling": "500 artifacts on one ticket",
    "ceiling_quantity": 500.0,
    "ceiling_unit": "artifacts",
    "upgrade_path": "paginate the read and merge per page",
}

#: A DRAFTED_DEFAULT claim: frozen, and challenged by an artifact class in
#: backticks, never by a role (AC7 is verbatim on that point).
DRAFTED_DEFAULT_CLAIM: dict[str, Any] = {
    "claim": "the enumerate cap of 200 members is the right ceiling for this class",
    "member_kind": "drafted_default",
    "qualifies_because": "a drafted number the parent does not pin; registered and frozen",
    "owed_artifact": "server/tests/test_asserted_not_proven_listing.py::test_the_cap_is_stated",
    "fallback": "raise the cap and paginate instead — named, not built",
    "upstream_id": "lg-code-knowledge-1055",
    "citation": "UXD §AC12(c)",
    "frozen": True,
    "challenger": "`spec-review`",
}

#: A DOCUMENT_DEFECT claim: a defect in a document this one depends on, routed
#: to a ticket rather than silently absorbed (AC9).
DOCUMENT_DEFECT_CLAIM: dict[str, Any] = {
    "claim": "the plan names the workspace-gate list CHECKERS; it is GATES",
    "member_kind": "document_defect",
    "qualifies_because": "a defect in a document this spec depends on, found while drafting",
    "owed_artifact": "server/tests/test_ceiling_marker_gate.py::test_the_gate_is_in_the_workspace_gates_list",
    "fallback": "read the list from the dispatcher at gate time — named, not built",
    "upstream_id": "lg-code-knowledge-1055",
    "citation": "PLAN §step 0",
    "routed_ticket": "lg-code-knowledge-1055",
}


def claim_values(base: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """A copy of ``base`` (BEHAVIOUR by default) with ``overrides`` applied."""
    values = dict(base if base is not None else BEHAVIOUR_CLAIM)
    values.update(overrides)
    return values


def make_workspace(session: Session, repo_path: str) -> Workspace:
    workspace = Workspace(slug=f"anp-{uuid4().hex[:8]}", name="ANP", repo_path=repo_path)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def make_ticket(
    session: Session,
    workspace: Workspace,
    *,
    title: str = "A capability that believes something without proof",
    state: TicketState = TicketState.IN_PROGRESS,
    number: int = 1,
) -> Ticket:
    ticket = Ticket(
        external_id=f"{workspace.slug}-{number}",
        ticket_number=number,
        workspace_id=workspace.id,
        title=title,
        work_item_type=WorkItemType.CAPABILITY,
        state=state,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def artifact_payload(session: Session, artifact_id: str) -> dict[str, Any]:
    artifact = session.get(Artifact, artifact_id)
    assert artifact is not None, f"no artifact {artifact_id}"
    return json.loads(artifact.content_json or "{}")


# --------------------------------------------------------------------------- #
# The git repository the listing suites need, because the code-site scan is
# bounded to git-tracked files (R7) and `closable` is computed from the
# checkout (R5). Shared so the two suites cannot drift apart on the fixture
# their answers are measured against.
# --------------------------------------------------------------------------- #

_SCRUBBED_ENV_KEYS = ("GIT_DIR", "GIT_WORK_TREE")


def git(repo: Path, *args: str) -> None:
    """Run git in ``repo`` with `GIT_DIR`/`GIT_WORK_TREE` scrubbed.

    Git exports an absolute `GIT_DIR` into hooks, and it beats `cwd`, so an
    unscrubbed call here operates on the pushing repository instead of the
    throwaway one.
    """
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV_KEYS}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def build_repo(root: Path) -> Path:
    """A committed checkout shaped like this repo's own tree."""
    (root / "server" / "tests").mkdir(parents=True)
    (root / "server" / "loregarden" / "services").mkdir(parents=True)
    git(root, "init", "-q", ".")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "server" / "loregarden" / "services" / "ticket_documents.py").write_text(
        "MAX = 200\n", encoding="utf-8"
    )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


def land_owed_test(repo: Path, reference: str) -> None:
    """Make an owed `path.py::test_name` resolve in the checkout."""
    path, name = reference.split("::")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"def {name}():\n    assert True\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"land {name}")


def listed_members(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Every member in an `open_claims` response, in its rendered order."""
    return [member for group in response["groups"] for member in group["members"]]


@pytest.fixture(name="repo")
def repo_fixture(tmp_path) -> Path:
    return build_repo(tmp_path / "ws")


@pytest.fixture(name="workspace")
def workspace_fixture(db_session, repo):
    return make_workspace(db_session, str(repo))


@pytest.fixture(name="ticket")
def ticket_fixture(db_session, workspace):
    return make_ticket(db_session, workspace, number=1)


def mark_a_code_site(repo: Path, relpath: str, *, line: str = "500 tickets") -> str:
    """Commit a file carrying one valid `ceiling:` marker; return its pointer.

    Per-test rather than in `build_repo`, deliberately. Marking the shared
    fixture was tried and measured: 26 landed tests go red, because the exact
    counts the adversarial suite exists to pin — "does the class count 1 or 2?"
    — all shift by one, as does `test_an_empty_class_is_not_a_failure`.
    Re-baselining 26 contract assertions to cover one filter is the wrong
    trade; a test that needs a scanned member available to leak asks for one.
    """
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def read():\n"
        f"    # ceiling: {line} — upgrade: paginate the join and merge per page\n"
        "    return 1\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"a marked site in {relpath}")
    return f"{relpath}:2"
