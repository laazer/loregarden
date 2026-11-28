"""Reading the class: the one surface an operator uses.

`open_claims` answers exactly one question — *what do we currently believe
without proof, and which of those can I close now?* — and the response is
ordered by it. One undifferentiated list does not answer it.

Named `listing`, not `enumerate`: that shadows a builtin.

What the shape is defending against, in one line each:

- **Ordering before the cap.** `closable` first, then the members whose ticket
  landed while their proof never did, then the rest. A reader that caps first
  and sorts the page it got back drops exactly the members the operator came for.
- **The cap says so.** `total` is the unfiltered count and `truncated` is a
  boolean. A silent truncation reads as "that is all of them", the single
  failure a class whose value is its count cannot survive.
- **Counting over identities, never rows.** 110 claims amended once is 220
  rows against a 200-row cap; a row-counting reader reports 110 members as
  truncated, with `truncated: true` to make it look deliberate.
- **Both numbers, always.** `scanned_files == 0` with `total == 0` cannot be
  told apart from an empty class unless both are reported, so both always are.
- **No degrading to empty.** A failed query or a failed scan raises. An empty
  list and a broken read must not be the same answer.
"""

from __future__ import annotations

import json
import tokenize
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    AssertedClaimStatus,
    AssertedNotProvenKind,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.asserted_not_proven.record import (
    ClaimLifecycle,
    StoredClaim,
    exceeds_ceiling,
    from_payload,
    latest_row,
)
from loregarden.services.asserted_not_proven.schemas import AssertedNotProvenClaim
from loregarden.services.git_subprocess import run_git
from loregarden.services.ticket_documents import MAX_ARTIFACT_LIST_LIMIT
from sqlmodel import Session, col, select

#: The question, verbatim on every response. The operator reads it to know what
#: this answer is, and the ordering below is the answer to it.
QUESTION = "what do we currently believe without proof, and which of those can I close now?"

#: The tool that enters a member, named in the empty response. Empty is the
#: state this class starts in by design, so it must not read as a failure.
RECORD_TOOL = "loregarden_record_asserted_not_proven"

#: How much of a tree the code-site scan reads. The bound exists so a huge
#: checkout cannot turn a read tool into a tree walk; crossing it sets
#: `scan_truncated`, because a partial scan must not read as the complete class.
MAX_SCANNED_FILES = 5000

#: The marker a knowingly-insufficient code site carries. Validated at the site
#: by `.lefthook/scripts/py_ceiling_marker_check.py`; read here.
CEILING_MARKER = "ceiling:"

#: Source files the scan reads. Git-tracked only (R7): a build artifact or a
#: scratch file is not a member of this class.
_SCANNED_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".cjs", ".sh"})

#: The ranks members sort by, and the whole answer to the question. Lower first.
_CLOSABLE, _UNPAID, _OPEN, _SETTLED = 0, 1, 2, 3

#: A scanned marker has no owning ticket, so this field is empty rather than
#: invented. Present on every member as R4.3 requires; empty reads as "the tree
#: owns this one", and inventing an owner would make the field a reader uses to
#: OPEN the ticket unreliable exactly where it matters.
NO_OWNING_TICKET = ""


@dataclass(frozen=True)
class Member:
    """One member of the class, however it was found."""

    claim: AssertedNotProvenClaim
    lifecycle: ClaimLifecycle
    ticket_external_id: str
    origin: str
    status: AssertedClaimStatus
    unpaid_after_landing: bool
    rank: int

    def rendered(self) -> dict:
        """The member as the response carries it.

        Every field R4.3 names is present on every member, including the
        conditional ones as empty: a reader that has to branch on key presence
        cannot be written against this shape.
        """
        claim = self.claim
        return {
            "claim": claim.claim,
            "member_kind": claim.member_kind.value,
            "status": self.status.value,
            "owed_artifact": claim.owed_artifact,
            "owed_description": claim.owed_description,
            "ticket_external_id": self.ticket_external_id,
            "qualifies_because": claim.qualifies_because,
            "upstream_id": claim.upstream_id,
            "citation": claim.citation,
            "proven_today": claim.proven_today,
            "owed_environment": claim.owed_environment,
            "portable_subset": claim.portable_subset,
            "fallback": claim.fallback,
            "ceiling": claim.ceiling,
            "ceiling_quantity": claim.ceiling_quantity,
            "ceiling_unit": claim.ceiling_unit,
            "upgrade_path": claim.upgrade_path,
            "challenger": claim.challenger,
            "routed_ticket": claim.routed_ticket,
            "unpaid_after_landing": self.unpaid_after_landing,
            "origin": self.origin,
            "outcome": self.lifecycle.outcome.value if self.lifecycle.outcome else "",
            "measured_value": self.lifecycle.measured_value,
            "measured_unit": self.lifecycle.measured_unit,
        }

    def sort_key(self) -> tuple:
        """Total, so the same question asked twice gets the same answer."""
        return (
            self.rank,
            self.claim.member_kind.value,
            self.claim.claim,
            self.ticket_external_id,
            self.claim.owed_artifact,
        )


def owed_test_resolves(repo_root: Path | None, owed_artifact: str) -> bool:
    """True when the owed `path.py::test_name` exists in this checkout.

    Both halves: the file exists AND defines that test. A file-exists check
    alone closes members that are still owed, which is the one direction this
    must not be wrong in.
    """
    if repo_root is None or "::" not in owed_artifact:
        return False
    relpath, name = owed_artifact.split("::", 1)
    target = repo_root / relpath
    if not target.is_file():
        return False
    return f"def {name}(" in target.read_text(encoding="utf-8", errors="replace")


def stored_members(
    session: Session,
    *,
    workspace: Workspace,
    ticket_id: str | None,
    repo_root: Path | None,
) -> list[Member]:
    """Every claim filed on a ticket in ``workspace``, latest row per identity.

    Counted over identities: `latest_row` walks the `amends` chain, so a claim
    amended four times is one member carrying the newest text.
    """
    query = (
        select(Artifact, Ticket)
        .join(Ticket, col(Artifact.ticket_id) == col(Ticket.id))
        .where(
            Artifact.kind == ArtifactKind.ASSERTED_NOT_PROVEN,
            Ticket.workspace_id == workspace.id,
        )
    )
    if ticket_id is not None:
        query = query.where(Ticket.id == ticket_id)
    identities: dict[tuple[str, str], Ticket] = {}
    for artifact, ticket in session.exec(query).all():
        identities[(ticket.id, artifact.title)] = ticket

    members: list[Member] = []
    for (_, slug), ticket in identities.items():
        row = latest_row(session, ticket, slug)
        if row is None:
            continue
        claim, lifecycle = from_payload(json.loads(row.content_json or "{}"))
        members.append(
            _as_member(
                StoredClaim(artifact_id=row.id, slug=slug, claim=claim, lifecycle=lifecycle),
                ticket=ticket,
                repo_root=repo_root,
            )
        )
    return members


def _as_member(stored: StoredClaim, *, ticket: Ticket, repo_root: Path | None) -> Member:
    claim = stored.claim
    if stored.settled:
        return Member(
            claim=claim,
            lifecycle=stored.lifecycle,
            ticket_external_id=ticket.external_id,
            origin="record",
            status=AssertedClaimStatus.SETTLED,
            unpaid_after_landing=False,
            rank=_SETTLED,
        )
    if claim.member_kind is AssertedNotProvenKind.CODE_SITE:
        resolved = exceeds_ceiling(stored)
    else:
        resolved = owed_test_resolves(repo_root, claim.owed_artifact)
    if resolved:
        return Member(
            claim=claim,
            lifecycle=stored.lifecycle,
            ticket_external_id=ticket.external_id,
            origin="record",
            status=AssertedClaimStatus.CLOSABLE,
            unpaid_after_landing=False,
            rank=_CLOSABLE,
        )
    unpaid = ticket.state is TicketState.DONE
    return Member(
        claim=claim,
        lifecycle=stored.lifecycle,
        ticket_external_id=ticket.external_id,
        origin="record",
        status=AssertedClaimStatus.OPEN,
        unpaid_after_landing=unpaid,
        rank=_UNPAID if unpaid else _OPEN,
    )


def tracked_source_files(repo_root: Path) -> list[str]:
    """The git-tracked source files the scan may read, in a stable order.

    Through `run_git`, never a raw `subprocess.run(["git", ...])`: GIT_DIR beats
    `cwd`, so an unscrubbed call here can enumerate a different repository
    entirely. A non-zero exit raises — a scan that silently covered nothing
    reads as "the class is small", the most expensive wrong answer this has.
    """
    result = run_git(["ls-files", "-z"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"git ls-files failed in {repo_root} ({result.returncode}): "
            f"{(result.stderr or '').strip()}"
        )
    listed = [name for name in result.stdout.split("\0") if name]
    return sorted(name for name in listed if Path(name).suffix in _SCANNED_SUFFIXES)


def scan_members(repo_root: Path | None) -> tuple[list[Member], int, bool]:
    """Code sites carrying a `ceiling:` marker, with how much of the tree was read.

    Returns the members, `scanned_files`, and whether the scan was truncated.
    The two numbers are returned whatever the member count, because zero
    members with no file count is indistinguishable from a scan that read
    nothing.
    """
    if repo_root is None:
        return [], 0, False
    candidates = tracked_source_files(repo_root)
    truncated = len(candidates) > MAX_SCANNED_FILES
    read = candidates[:MAX_SCANNED_FILES]
    members: list[Member] = []
    for relpath in read:
        members.extend(_markers_in(repo_root, relpath))
    return members, len(read), truncated


def _markers_in(repo_root: Path, relpath: str) -> list[Member]:
    target = repo_root / relpath
    if not target.is_file():
        # Tracked but absent from the worktree: nothing to read, and nothing
        # read is never reported as nothing found — the file count above says
        # how many were opened.
        return []
    text = target.read_text(encoding="utf-8", errors="replace")
    comment_lines = _python_comment_lines(text) if target.suffix == ".py" else None
    found: list[Member] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if comment_lines is not None and number not in comment_lines:
            continue
        if _marker_in(line) is None:
            continue
        found.append(_scanned_member(relpath, number, line))
    return found


def _python_comment_lines(text: str) -> set[int]:
    """The lines of a Python file that really carry a comment.

    Measured against the live tree before this existed: the line scan alone
    counted 36 members, and most were the gate suite's own fixtures — comments
    written INSIDE triple-quoted strings, which no interpreter and no gate
    reads as comments. A class whose whole value is its count cannot be
    inflated by the tests that prove it, so Python is tokenized.

    A file that cannot be tokenized falls back to every line, which is the
    conservative direction: over-reporting a marker is visible to the reader,
    and dropping one silently is the failure this class exists to avoid.
    """
    lines: set[int] = set()
    try:
        for token in tokenize.generate_tokens(StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                lines.add(token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set(range(1, len(text.splitlines()) + 1))
    return lines


def _marker_in(line: str) -> str | None:
    """The ceiling clause this line's comment carries, or None.

    A comment, not a line: `CEILING_MARKER = "ceiling:"` three screens up is a
    string, and a raw line scan would enrol this module's own constant in the
    class it enumerates. For Python the caller has already narrowed to real
    comment lines; this handles the `#` / `//` languages the tokenizer does not
    cover, and in both cases requires the marker to OPEN the comment.
    """
    at = line.find(CEILING_MARKER)
    if at < 0:
        return None
    opener = max(line.rfind("#", 0, at), line.rfind("//", 0, at))
    if opener < 0:
        return None
    if line[opener:at].lstrip("#/").strip():
        # A mention of the marker inside prose is prose, and the gate that
        # validates these markers reads them the same way.
        return None
    return line[at + len(CEILING_MARKER) :].strip()


def _scanned_member(relpath: str, number: int, line: str) -> Member:
    """A marker in the tree is a member whether or not anybody filed a row.

    Otherwise the enumeration is a list of what was remembered, not of what we
    believe without proof. The claim text is the marker itself, which is the
    only statement the tree makes about it.
    """
    marker = _marker_in(line) or ""
    claim = AssertedNotProvenClaim.model_construct(
        claim=marker or f"an unexplained {CEILING_MARKER} marker",
        member_kind=AssertedNotProvenKind.CODE_SITE,
        qualifies_because=f"a {CEILING_MARKER} marker in the tree with no recorded claim",
        owed_artifact=f"{relpath}:{number}",
        owed_description="",
        owed_environment="",
        portable_subset="",
        proven_today="",
        fallback="",
        upstream_id=relpath,
        citation=f"{relpath}:{number}",
        dependent_documents=[],
        ceiling=marker,
        ceiling_quantity=None,
        ceiling_unit="",
        upgrade_path="",
        frozen=False,
        challenger="",
        routed_ticket="",
    )
    return Member(
        claim=claim,
        lifecycle=ClaimLifecycle(),
        ticket_external_id=NO_OWNING_TICKET,
        origin="scan",
        status=AssertedClaimStatus.OPEN,
        unpaid_after_landing=False,
        rank=_OPEN,
    )


def _wanted(member: Member, status: AssertedClaimStatus) -> bool:
    if status is AssertedClaimStatus.ALL:
        return True
    if status is AssertedClaimStatus.OPEN:
        # The default admits `closable` too: the question's second half is
        # "which of those can I close now?", and hiding them answers half of it.
        return member.status in (AssertedClaimStatus.OPEN, AssertedClaimStatus.CLOSABLE)
    return member.status is status


def _scan_for(ticket_id: str | None, repo_root: Path | None) -> tuple[list[Member], int, bool]:
    """The tree scan, and how much of it this answer read.

    A ticket filter skips it entirely. A scanned marker has NO owning ticket —
    `ticket_external_id` is empty on one, deliberately, because the tree owns it
    and not a ticket — so no ticket filter can match one, and running the scan
    anyway would return every marker in the checkout under a filter that asked
    for two members. Measured before this: a repo with 200 marked files and a
    ticket owning 2 members answered `total 211`, 209 of them belonging to no
    ticket, with the code_site group count reporting the whole tree rather than
    the ticket's.

    `scanned_files` is then 0, which is the literal truth R7.1 asks for — "the
    number of files actually read" — and cannot be confused with a scan that
    broke, because a failed scan RAISES (R5.4) rather than returning zero.
    """
    if ticket_id is not None:
        return [], 0, False
    return scan_members(repo_root)


def open_claims(
    session: Session,
    *,
    workspace_slug: str,
    repo_root: Path | None,
    ticket_id: str | None = None,
    member_kind: AssertedNotProvenKind | None = None,
    status: AssertedClaimStatus = AssertedClaimStatus.OPEN,
) -> dict:
    """The class, as the one question it answers."""
    workspace = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
    if workspace is None:
        raise ValueError(f"no workspace {workspace_slug!r}; the class is scoped to one")

    stored = stored_members(session, workspace=workspace, ticket_id=ticket_id, repo_root=repo_root)
    scanned, scanned_files, scan_truncated = _scan_for(ticket_id, repo_root)

    filed = {member.claim.owed_artifact for member in stored}
    members = [*stored, *(member for member in scanned if member.claim.owed_artifact not in filed)]
    if member_kind is not None:
        members = [m for m in members if m.claim.member_kind is member_kind]
    members = [m for m in members if _wanted(m, status)]
    members.sort(key=Member.sort_key)

    total = len(members)
    returned = members[:MAX_ARTIFACT_LIST_LIMIT]
    response = {
        "question": QUESTION,
        "groups": _grouped(members, returned),
        "total": total,
        "returned": len(returned),
        "truncated": len(returned) < total,
        "scanned_files": scanned_files,
        "scan_truncated": scan_truncated,
    }
    if total == 0:
        response["note"] = (
            "Nothing is recorded as believed-without-proof here. That is the state this class "
            f"starts in, not a failure: it grows by amendment. Enter one with `{RECORD_TOOL}`."
        )
    return response


def _grouped(all_members: list[Member], returned: list[Member]) -> list[dict]:
    """Members by kind, each group's count taken BEFORE the cap.

    A post-cap count would make the group counts lie, and the counts are what
    answer the question when no member is read.

    One group per kind that HAS members, driven by `counts` and never by the
    capped page. Driving it from the page drops a whole kind the moment the cap
    truncates — 236 behaviour members and 1 code_site returned a single
    behaviour group, so an operator asking "which code sites are knowingly
    insufficient?" was told the kind does not exist, with its count already
    computed and thrown away. At the measured population (low hundreds against
    a cap of 200) that is the steady state, not an edge case.

    Ordered by the kind enum, so the same question twice is the same answer and
    a kind whose members all fall outside the cap still holds its place.
    """
    counts: dict[str, int] = {}
    for member in all_members:
        kind = member.claim.member_kind.value
        counts[kind] = counts.get(kind, 0) + 1
    paged: dict[str, list[dict]] = {}
    for member in returned:
        paged.setdefault(member.claim.member_kind.value, []).append(member.rendered())
    return [
        {
            "member_kind": kind.value,
            "count": counts[kind.value],
            "members": paged.get(kind.value, []),
        }
        for kind in AssertedNotProvenKind
        if kind.value in counts
    ]


def members_for_tickets(
    session: Session,
    tickets: list[Ticket],
    *,
    repo_root: Path | None,
) -> list[Member]:
    """The open members filed on exactly ``tickets``, for the PR section.

    Settled members are not rendered: the section is what this tree still
    believes without proof.
    """
    members: list[Member] = []
    for ticket in tickets:
        rows = session.exec(
            select(Artifact).where(
                Artifact.ticket_id == ticket.id,
                Artifact.kind == ArtifactKind.ASSERTED_NOT_PROVEN,
            )
        ).all()
        for slug in sorted({row.title for row in rows}):
            row = latest_row(session, ticket, slug)
            if row is None:
                continue
            claim, lifecycle = from_payload(json.loads(row.content_json or "{}"))
            stored = StoredClaim(artifact_id=row.id, slug=slug, claim=claim, lifecycle=lifecycle)
            if stored.settled:
                continue
            members.append(_as_member(stored, ticket=ticket, repo_root=repo_root))
    members.sort(key=Member.sort_key)
    return members
