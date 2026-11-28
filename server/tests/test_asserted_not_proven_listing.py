"""The enumeration: the only operator-readable surface this ticket ships.

Spec `6d686e57` R4, R5, R7, R11 (ticket AC4, AC5, AC3, AC12(a)(c)(d)(e)(f)(g)).

AC4 is called out in the ticket as OUR extension beyond the reference, and "the
one that makes the class usable": in the source document the class is
enumerable only by reading prose in two places plus one source comment, and
nothing registers, counts or queries the members.

The question this surface answers, and the one it is ordered by, is exactly
one: *what do we currently believe without proof, and which of those can I
close now?* One undifferentiated list does not answer it and does not satisfy
AC4 — which is why ordering, grouping, the cap-with-its-total, the
`unpaid_after_landing` mark and the empty-is-not-failure note are each pinned
here rather than left to the implementer.

The measured volume this is sized for (`UXD`, live `data/loregarden.db`,
read-only, 2026-11-17): 33,569 artifact rows over 3,165 tickets, 899 of them
done; closest analogues 144 `fix_coverage` and 119 `ux_answers` records; the
in-tree marker families this class sits beside are 186 `silent-ok:`, 327
`py-org: allow`, 32 `type-ok:`, 23 `ux-ok:`. A low-hundreds population.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.mcp.asserted_not_proven_tools import ASSERTED_NOT_PROVEN_TOOL_DEFINITIONS
from loregarden.mcp.operation_tools import operation_tool_schema
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AssertedClaimStatus,
    AssertedNotProvenKind,
    ClaimOutcome,
    TicketState,
)
from loregarden.services.asserted_not_proven.listing import open_claims, owed_test_resolves
from loregarden.services.asserted_not_proven.record import (
    claim_slug,
    close_claim,
    record_measurement,
    register_claim,
)
from loregarden.services.asserted_not_proven.schemas import parse_claim
from loregarden.services.asserted_not_proven.tree_section import (
    SECTION_HEADING,
    asserted_not_proven_section,
)
from loregarden.services.operations.asserted_claims import repo_root as _repo_root
from loregarden.services.ticket_documents import MAX_ARTIFACT_LIST_LIMIT
from loregarden.services.workspace_paths import resolve_workspace_root
from tests.asserted_not_proven_helpers import (
    BEHAVIOUR_CLAIM,
    CODE_SITE_CLAIM,
    claim_values,
    git,
    land_owed_test,
    listed_members,
    make_ticket,
    make_workspace,
    mark_a_code_site,
    paths_this_change_adds,
    # Fixtures, discovered by pytest once they are in this namespace. They live
    # in the helpers so the suites cannot drift apart on the checkout their
    # answers are measured against.
    repo_fixture,  # noqa: F401
    ticket_fixture,  # noqa: F401
    workspace_fixture,  # noqa: F401
)
from tests.repo_paths import REPO_ROOT

RECORD_TOOL = "loregarden_record_asserted_not_proven"


def _list(db_session, workspace, repo, **kwargs) -> dict:
    return open_claims(db_session, workspace_slug=workspace.slug, repo_root=repo, **kwargs)


# =========================================================================== #
# R4 — the class is ENUMERABLE across tickets (AC4)
# =========================================================================== #


def test_claims_on_two_different_tickets_appear_in_one_answer(db_session, workspace, repo):
    """R4.1. The ranked risk from the plan: a record landed in a substrate that
    accepts the write and surfaces nothing. A reader that can only answer
    per-ticket does not satisfy AC4 and must fail here."""
    first = make_ticket(db_session, workspace, number=1)
    second = make_ticket(db_session, workspace, number=2)
    register_claim(db_session, first, parse_claim(claim_values()))
    register_claim(
        db_session,
        second,
        parse_claim(claim_values(claim="the reaper skips a worktree whose lock is held")),
    )

    members = listed_members(_list(db_session, workspace, repo))
    assert len(members) == 2
    assert {m["ticket_external_id"] for m in members} == {first.external_id, second.external_id}


def test_an_unregistered_ceiling_marker_is_scanned_in_as_a_member(db_session, workspace, repo):
    """R4.2 / AC11. A marker in the tree is a member of the class whether or not
    anybody filed a row for it — otherwise the enumeration is a list of what was
    remembered, not of what we believe without proof."""
    source = repo / "server" / "loregarden" / "services" / "counter.py"
    source.write_text(
        "def count():\n"
        "    # ceiling: 500 tickets — upgrade: paginate the join and merge per page\n"
        "    return 1\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "marked site")

    members = listed_members(_list(db_session, workspace, repo, status=AssertedClaimStatus.ALL))
    scanned = [m for m in members if m["origin"] == "scan"]
    assert len(scanned) == 1
    assert scanned[0]["member_kind"] == AssertedNotProvenKind.CODE_SITE.value
    assert scanned[0]["owed_artifact"] == "server/loregarden/services/counter.py:2"


def test_a_registered_code_site_appears_once_not_twice(db_session, workspace, repo, ticket):
    """R4.2's other half. A member counted twice makes the count — the whole
    value of the class — wrong in the direction that looks like diligence."""
    source = repo / "server" / "loregarden" / "services" / "counter.py"
    source.write_text(
        "def count():\n"
        "    # ceiling: 500 tickets — upgrade: paginate the join and merge per page\n"
        "    return 1\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "marked site")
    register_claim(
        db_session,
        ticket,
        parse_claim(
            claim_values(CODE_SITE_CLAIM, owed_artifact="server/loregarden/services/counter.py:2")
        ),
    )

    members = listed_members(_list(db_session, workspace, repo, status=AssertedClaimStatus.ALL))
    at_site = [
        m for m in members if m["owed_artifact"] == "server/loregarden/services/counter.py:2"
    ]
    assert len(at_site) == 1
    assert at_site[0]["origin"] == "record"


def test_every_member_carries_the_fields_a_reader_needs(db_session, workspace, repo, ticket):
    """R4.3, R4.5. `qualifies_because` is required and returned on every member:
    AC4 says the class grows by amendment, "each new member joining with its
    qualifying reason", so the reason must be readable without opening the row."""
    register_claim(db_session, ticket, parse_claim(claim_values()))
    member = listed_members(_list(db_session, workspace, repo))[0]
    for field in (
        "claim",
        "member_kind",
        "status",
        "owed_artifact",
        "ticket_external_id",
        "qualifies_because",
        "upstream_id",
        "citation",
        "unpaid_after_landing",
        "origin",
    ):
        assert field in member, f"a member must carry {field}"
    assert member["qualifies_because"] == BEHAVIOUR_CLAIM["qualifies_because"]


def test_a_code_site_member_carries_its_ceiling_and_upgrade_path(
    db_session, workspace, repo, ticket
):
    """R4.3's conditional half: the ceiling is what makes the member closable."""
    register_claim(db_session, ticket, parse_claim(claim_values(CODE_SITE_CLAIM)))
    member = listed_members(_list(db_session, workspace, repo))[0]
    assert member["ceiling"] == CODE_SITE_CLAIM["ceiling"]
    assert member["upgrade_path"] == CODE_SITE_CLAIM["upgrade_path"]


def test_the_filters_narrow_and_compose(db_session, workspace, repo):
    """R4.4 / AC12(e). Exactly ticket_id, member_kind, status, workspace_slug.

    The tree carries a `ceiling:` marker of its own here. Without one this test
    passes whether or not the ticket filter is applied to the scanned half,
    because there is nothing available to leak — which is exactly how the leak
    reached verify.
    """
    mark_a_code_site(repo, "server/loregarden/services/counter.py")
    first = make_ticket(db_session, workspace, number=1)
    second = make_ticket(db_session, workspace, number=2)
    register_claim(db_session, first, parse_claim(claim_values()))
    register_claim(db_session, first, parse_claim(claim_values(CODE_SITE_CLAIM)))
    register_claim(
        db_session,
        second,
        parse_claim(claim_values(claim="the reaper skips a worktree whose lock is held")),
    )

    by_ticket = _list(db_session, workspace, repo, ticket_id=first.id)
    assert {m["ticket_external_id"] for m in listed_members(by_ticket)} == {first.external_id}
    assert [m for m in listed_members(by_ticket) if m["origin"] == "scan"] == [], (
        "a scanned marker has no owning ticket, so it cannot belong to this one"
    )

    by_kind = _list(db_session, workspace, repo, member_kind=AssertedNotProvenKind.CODE_SITE)
    assert {m["member_kind"] for m in listed_members(by_kind)} == {"code_site"}

    assert any(m["origin"] == "scan" for m in listed_members(by_kind)), (
        "unfiltered, the tree's own marker IS a member of this class"
    )

    both = _list(
        db_session,
        workspace,
        repo,
        ticket_id=first.id,
        member_kind=AssertedNotProvenKind.CODE_SITE,
    )
    assert len(listed_members(both)) == 1
    assert [group["count"] for group in both["groups"]] == [1], (
        "the per-group count under a ticket filter is that ticket's, not the tree's"
    )


def test_a_claim_in_another_workspace_is_not_in_this_answer(db_session, workspace, repo, tmp_path):
    """The workspace is a scope, not a label; the control plane holds many."""
    other = make_workspace(db_session, str(tmp_path / "other"))
    other_ticket = make_ticket(db_session, other, number=9)
    register_claim(db_session, other_ticket, parse_claim(claim_values()))
    assert listed_members(_list(db_session, workspace, repo)) == []


# =========================================================================== #
# R5 — the response: the only operator-readable surface (AC12 a,c,d,e,f)
# =========================================================================== #


def test_the_response_states_the_question_it_answers(db_session, workspace, repo, ticket):
    """AC12(a). One question, and the response is ordered by it."""
    register_claim(db_session, ticket, parse_claim(claim_values()))
    response = _list(db_session, workspace, repo)
    assert response["question"] == (
        "what do we currently believe without proof, and which of those can I close now?"
    )


def test_the_default_hides_settled_and_all_shows_them(db_session, workspace, repo, ticket):
    """R5.1, R10.8. The default answers the live question; `all` is the archive."""
    claim = parse_claim(claim_values())
    register_claim(db_session, ticket, claim)
    land_owed_test(repo, claim.owed_artifact)
    close_claim(
        db_session,
        ticket,
        claim_slug(claim.claim),
        outcome=ClaimOutcome.PROVEN,
        evidence=claim.owed_artifact,
        dependent_document_answers={},
        evidence_exists=True,
    )

    assert listed_members(_list(db_session, workspace, repo)) == []
    settled = listed_members(_list(db_session, workspace, repo, status=AssertedClaimStatus.SETTLED))
    assert len(settled) == 1
    assert settled[0]["status"] == "settled"
    assert (
        len(listed_members(_list(db_session, workspace, repo, status=AssertedClaimStatus.ALL))) == 1
    )


def test_a_truncated_response_says_so_and_states_the_unfiltered_total(db_session, workspace, repo):
    """R5.2 / AC12(c). THE non-negotiable one.

    A silent truncation reads as "that is all of them" — the single failure a
    class whose value is its count cannot survive. The cap is
    `ticket_documents.MAX_ARTIFACT_LIST_LIMIT`, imported, never redefined.
    """
    total = MAX_ARTIFACT_LIST_LIMIT + 50
    ticket = make_ticket(db_session, workspace, number=1)
    for index in range(total):
        register_claim(
            db_session,
            ticket,
            parse_claim(claim_values(claim=f"unproven behaviour number {index}")),
        )

    response = _list(db_session, workspace, repo)
    assert response["total"] == total
    assert response["returned"] == MAX_ARTIFACT_LIST_LIMIT
    assert response["truncated"] is True
    assert len(listed_members(response)) == MAX_ARTIFACT_LIST_LIMIT


def test_an_untruncated_response_says_that_too(db_session, workspace, repo, ticket):
    register_claim(db_session, ticket, parse_claim(claim_values()))
    response = _list(db_session, workspace, repo)
    assert response["truncated"] is False
    assert response["returned"] == response["total"] == 1


def test_an_empty_class_is_not_a_failure_and_says_how_to_enter_one(db_session, workspace, repo):
    """R5.3 / AC12(f). Empty is the state this class starts in, by design: it
    grows by amendment, exactly as the source document's does."""
    response = _list(db_session, workspace, repo)
    assert response["groups"] == []
    assert response["total"] == 0
    assert RECORD_TOOL in response["note"]


def test_a_populated_response_carries_no_note(db_session, workspace, repo, ticket):
    """R5's shape: `note` is present ONLY when total == 0."""
    register_claim(db_session, ticket, parse_claim(claim_values()))
    assert "note" not in _list(db_session, workspace, repo)


def test_a_failed_query_raises_and_never_degrades_to_an_empty_list(
    db_session, workspace, repo, ticket
):
    """R5.4 / AC12(f). The repo's own rule on the only surface this ships: an
    empty list and a broken query must not be the same answer. A conflict check
    here once returned `has_conflicts: False` because the check itself threw."""
    register_claim(db_session, ticket, parse_claim(claim_values()))
    with patch(
        "loregarden.services.asserted_not_proven.listing.stored_members",
        side_effect=RuntimeError("the artifacts query failed"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            _list(db_session, workspace, repo)
    assert "artifacts query" in str(excinfo.value)


def test_a_failed_scan_raises_and_never_degrades_to_an_empty_list(
    db_session, workspace, repo, ticket
):
    """R5.4's other half. A scan that silently covers nothing reads as "the
    class is small", which is the most expensive wrong answer this tool has."""
    register_claim(db_session, ticket, parse_claim(claim_values()))
    with patch(
        "loregarden.services.asserted_not_proven.listing.scan_members",
        side_effect=RuntimeError("git ls-files failed"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            _list(db_session, workspace, repo)
    assert "ls-files" in str(excinfo.value)


def test_members_are_grouped_by_kind_with_a_precap_count(db_session, workspace, repo, ticket):
    """R5.6 / AC12(e). The kinds are read for different reasons and a mixed wall
    of 150 members answers nothing; the group counts answer the question even
    when no member is read. Post-cap counts would make them lie."""
    for index in range(3):
        register_claim(
            db_session, ticket, parse_claim(claim_values(claim=f"unproven behaviour {index}"))
        )
    register_claim(db_session, ticket, parse_claim(claim_values(CODE_SITE_CLAIM)))

    counts = {
        group["member_kind"]: group["count"]
        for group in _list(db_session, workspace, repo)["groups"]
    }
    assert counts == {"behaviour": 3, "code_site": 1}


def test_the_group_count_is_the_precap_count(db_session, workspace, repo):
    """R5.6, at the cap: the count is what exists, not what was returned."""
    total = MAX_ARTIFACT_LIST_LIMIT + 10
    ticket = make_ticket(db_session, workspace, number=1)
    for index in range(total):
        register_claim(
            db_session, ticket, parse_claim(claim_values(claim=f"unproven behaviour {index}"))
        )
    group = _list(db_session, workspace, repo)["groups"][0]
    assert group["count"] == total
    assert len(group["members"]) == MAX_ARTIFACT_LIST_LIMIT


def test_closable_comes_first_then_unpaid_after_landing_then_open(db_session, workspace, repo):
    """R5.7 / AC12(a)(d). The ordering IS the answer to the question.

    `unpaid_after_landing` is the Monitor-tab failure in MCP form: 899 of 3,165
    tickets are done, and a member whose ticket landed while its owed artifact
    never appeared is exactly the 85-of-94 shape. Neither dropped, nor listed
    indistinguishably from live work.
    """
    live = make_ticket(db_session, workspace, number=1, state=TicketState.IN_PROGRESS)
    landed = make_ticket(db_session, workspace, number=2, state=TicketState.DONE)

    closable = parse_claim(
        claim_values(
            claim="the proof for this one has landed",
            owed_artifact="server/tests/test_landed.py::test_landed_proof",
        )
    )
    register_claim(db_session, live, closable)
    land_owed_test(repo, closable.owed_artifact)

    register_claim(
        db_session,
        landed,
        parse_claim(
            claim_values(
                claim="this one shipped and its proof never did",
                owed_artifact="server/tests/test_never.py::test_never_written",
            )
        ),
    )
    register_claim(
        db_session,
        live,
        parse_claim(
            claim_values(
                claim="this one is plainly open",
                owed_artifact="server/tests/test_open.py::test_still_owed",
            )
        ),
    )

    members = listed_members(_list(db_session, workspace, repo))
    assert [(m["status"], m["unpaid_after_landing"]) for m in members] == [
        ("closable", False),
        ("open", True),
        ("open", False),
    ]


def test_a_landed_ticket_whose_proof_appears_is_closable_not_unpaid(db_session, workspace, repo):
    """R5.8. The mark is about the owed artifact, not about the ticket's age."""
    landed = make_ticket(db_session, workspace, number=1, state=TicketState.DONE)
    claim = parse_claim(claim_values(owed_artifact="server/tests/test_late.py::test_arrived_late"))
    register_claim(db_session, landed, claim)

    before = listed_members(_list(db_session, workspace, repo))[0]
    assert before["status"] == "open"
    assert before["unpaid_after_landing"] is True

    land_owed_test(repo, claim.owed_artifact)
    after = listed_members(_list(db_session, workspace, repo))[0]
    assert after["status"] == "closable"
    assert after["unpaid_after_landing"] is False


def test_a_file_that_exists_without_the_test_is_not_closable(db_session, workspace, repo, ticket):
    """R5's `closable` rule names both halves: the file exists AND contains a
    literal `def test_name(`. A file-exists check alone closes members that are
    still owed, which is the one direction this must not be wrong in."""
    claim = parse_claim(
        claim_values(owed_artifact="server/tests/test_partial.py::test_not_there_yet")
    )
    register_claim(db_session, ticket, claim)
    target = repo / "server" / "tests" / "test_partial.py"
    target.write_text("def test_something_else():\n    assert True\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "partial")

    assert listed_members(_list(db_session, workspace, repo))[0]["status"] == "open"


def test_a_code_site_member_is_closable_only_on_a_recorded_measurement(
    db_session, workspace, repo, ticket
):
    """R5's CODE_SITE rule / AC12(a). "Exceeded by measured load" is a
    measurement recorded against the claim. No live probe is built, and no
    network is touched — a member with no measurement stays open."""
    claim = parse_claim(claim_values(CODE_SITE_CLAIM))
    register_claim(db_session, ticket, claim)
    assert listed_members(_list(db_session, workspace, repo))[0]["status"] == "open"

    record_measurement(db_session, ticket, claim_slug(claim.claim), value=612.0, unit="artifacts")
    assert listed_members(_list(db_session, workspace, repo))[0]["status"] == "closable"


# --------------------------------------------------------------------------- #
# R7 — the scan is bounded and says what it covered (AC12 g)
# --------------------------------------------------------------------------- #


def test_every_response_says_how_much_of_the_tree_it_read(db_session, workspace, repo, ticket):
    """R7.1, and the zero-may-be-the-detector rule: `scanned_files == 0` with
    `total == 0` is indistinguishable from an empty class unless both are
    reported. So both always are."""
    register_claim(db_session, ticket, parse_claim(claim_values()))
    response = _list(db_session, workspace, repo)
    assert response["scanned_files"] >= 1
    assert response["scan_truncated"] is False


def test_an_untracked_file_is_not_scanned(db_session, workspace, repo):
    """R7.3. The bound is git-tracked source files: a build artifact or a
    scratch file must not join the class."""
    (repo / "server" / "loregarden" / "services" / "scratch.py").write_text(
        "# ceiling: 500 tickets — upgrade: paginate the join and merge per page\nx = 1\n",
        encoding="utf-8",
    )
    response = _list(db_session, workspace, repo, status=AssertedClaimStatus.ALL)
    assert [m for m in listed_members(response) if "scratch.py" in m["owed_artifact"]] == []


def test_a_bounded_scan_says_it_was_truncated(db_session, workspace, repo):
    """R7.2. A partial scan must not read as the complete class."""
    for index in range(5):
        path = repo / "server" / "loregarden" / "services" / f"mod{index}.py"
        path.write_text(
            "# ceiling: 500 tickets — upgrade: paginate the join and merge per page\nx = 1\n",
            encoding="utf-8",
        )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "many marked sites")

    with patch("loregarden.services.asserted_not_proven.listing.MAX_SCANNED_FILES", 2):
        response = _list(db_session, workspace, repo, status=AssertedClaimStatus.ALL)
    assert response["scan_truncated"] is True
    assert response["scanned_files"] == 2


# --------------------------------------------------------------------------- #
# The read tool, through its own handler
# --------------------------------------------------------------------------- #


def test_the_read_tool_takes_exactly_four_parameters():
    """R5.5 / AC12(e). Exactly ticket_id, member_kind, status, workspace_slug —
    and no others. A filter nobody decided is a filter nobody can explain."""
    definition = next(
        d for d in ASSERTED_NOT_PROVEN_TOOL_DEFINITIONS if d["name"] == McpTool.OPEN_CLAIMS
    )
    properties = operation_tool_schema(str(definition["name"]))["properties"]
    assert set(properties) == {"ticket_id", "member_kind", "status", "workspace_slug"}
    assert set(properties["status"]["enum"]) == {"open", "closable", "settled", "all"}


def _read_tool(session, arguments: dict) -> str:
    """The tool as MCP calls it: through `execute_tool` into its operation."""
    return execute_tool(session, McpTool.OPEN_CLAIMS.value, dict(arguments))


def test_the_read_tool_returns_the_response_shape(db_session, workspace, repo, ticket):
    register_claim(db_session, ticket, parse_claim(claim_values()))
    handler = _read_tool
    response = json.loads(handler(db_session, {"workspace_slug": workspace.slug}))
    assert set(response) >= {
        "question",
        "groups",
        "total",
        "returned",
        "truncated",
        "scanned_files",
        "scan_truncated",
    }
    assert response["total"] == 1


def test_the_read_tool_refuses_an_unknown_status(db_session, workspace, repo):
    handler = _read_tool
    with pytest.raises(ValueError):
        handler(db_session, {"workspace_slug": workspace.slug, "status": "maybe"})


# =========================================================================== #
# R11 — the `## Asserted-not-proven` PR section (AC5, AC3)
# =========================================================================== #


def test_the_section_renders_the_open_claims_and_not_the_settled(db_session, workspace, repo):
    """R11.1. A settled claim in the section is noise; the section is the list
    of what this tree still believes without proof."""
    root = make_ticket(db_session, workspace, number=1)
    open_one = parse_claim(claim_values(claim="the reaper keeps a dirty checkout"))
    open_two = parse_claim(
        claim_values(
            claim="the lease survives a restart",
            owed_artifact="server/tests/test_lease.py::test_survives_restart",
        )
    )
    settled = parse_claim(
        claim_values(
            claim="the parser rejects a bare number",
            owed_artifact="server/tests/test_parse.py::test_rejects_bare_number",
        )
    )
    for claim in (open_one, open_two, settled):
        register_claim(db_session, root, claim)
    land_owed_test(repo, settled.owed_artifact)
    close_claim(
        db_session,
        root,
        claim_slug(settled.claim),
        outcome=ClaimOutcome.PROVEN,
        evidence=settled.owed_artifact,
        dependent_document_answers={},
        evidence_exists=True,
    )
    root.landed_branch = "integration/x"
    root.landed_sha = "deadbeef"
    db_session.add(root)
    db_session.commit()

    section = asserted_not_proven_section(db_session, root, branch="integration/x", repo_root=repo)
    assert SECTION_HEADING in section
    assert open_one.claim in section
    assert open_two.claim in section
    assert settled.claim not in section


def test_an_empty_class_renders_no_heading_at_all(db_session, workspace, repo, ticket):
    """R11.2. An always-rendered empty heading trains readers to skip it."""
    assert (
        asserted_not_proven_section(db_session, ticket, branch="integration/x", repo_root=repo)
        == ""
    )


def test_the_section_splits_the_guarantee_into_its_two_halves(db_session, workspace, repo, ticket):
    """R8.2's rendered half / AC5. Anything citing the guarantee cites the
    split, so the rendering must never join the two into one sentence."""
    claim = parse_claim(
        claim_values(
            owed_environment="linux, cgroup v2, a real container runtime",
            portable_subset="the path parsing and the refusal message, landed here",
            proven_today="the refusal message and the path parsing hold on every platform",
        )
    )
    register_claim(db_session, ticket, claim)
    ticket.landed_branch = "integration/x"
    ticket.landed_sha = "deadbeef"
    db_session.add(ticket)
    db_session.commit()

    section = asserted_not_proven_section(
        db_session, ticket, branch="integration/x", repo_root=repo
    )
    proven_line = next(line for line in section.splitlines() if claim.proven_today in line)
    owed_line = next(line for line in section.splitlines() if claim.owed_artifact in line)
    assert proven_line != owed_line, "the proven and the owed must be separate lines"
    assert "Proven today" in proven_line
    assert "Owed" in owed_line


def test_the_section_names_the_fallback_as_not_built(db_session, workspace, repo, ticket):
    """R11 / AC3. The fallback reaches the PR reader as named-and-unbuilt, which
    is the whole point of recording it rather than implementing it."""
    claim = parse_claim(claim_values())
    register_claim(db_session, ticket, claim)
    ticket.landed_branch = "integration/x"
    ticket.landed_sha = "deadbeef"
    db_session.add(ticket)
    db_session.commit()

    section = asserted_not_proven_section(
        db_session, ticket, branch="integration/x", repo_root=repo
    )
    assert claim.fallback in section
    assert "not built" in section.lower()


def test_a_rendered_code_site_shows_its_ceiling_and_upgrade_path(
    db_session, workspace, repo, ticket
):
    """R11.3."""
    register_claim(db_session, ticket, parse_claim(claim_values(CODE_SITE_CLAIM)))
    ticket.landed_branch = "integration/x"
    ticket.landed_sha = "deadbeef"
    db_session.add(ticket)
    db_session.commit()

    section = asserted_not_proven_section(
        db_session, ticket, branch="integration/x", repo_root=repo
    )
    assert CODE_SITE_CLAIM["ceiling"] in section
    assert CODE_SITE_CLAIM["upgrade_path"] in section


def test_publish_tree_puts_the_section_in_the_pr_body():
    """R11.4. A section builder nobody calls renders nothing."""
    source = Path("loregarden/services/publish_tree.py").read_text(encoding="utf-8")
    assert "asserted_not_proven_section" in source


# --------------------------------------------------------------------------- #
# AC12 — NO GRAPHICAL SURFACE
# --------------------------------------------------------------------------- #


def test_no_client_surface_is_added_by_this_change():
    """R5.10 / AC12. Verified at source by the ui-design stage: no client file
    filters on `artifacts.kind`, and `ticket_documents._is_document` requires
    `run_id is None` and `format == "markdown"`, so these rows never reach a
    ticket's Documents list. A later stage proposing a page comes back through
    ui-design first."""
    root = REPO_ROOT
    offenders = paths_this_change_adds(root, ("client/src/pages/", "client/src/components/"))
    if offenders is None:
        pytest.skip("no comparable origin/main in this checkout")
    assert offenders == [], f"AC12 settled NO GRAPHICAL SURFACE; this change touches {offenders}"


# --------------------------------------------------------------------------- #
# The workspace's own repo_path, which is RELATIVE on the one workspace that
# matters most (R4, R5's closable rule, R7's bound)
# --------------------------------------------------------------------------- #


def test_a_relative_repo_path_resolves_against_loregardens_root_not_the_cwd(db_session, tmp_path):
    """The live `loregarden` row stores `repo_path = "."`; every other
    workspace is absolute, which is why the factories here never caught this.

    `Path(".")` resolves against whatever cwd the caller has. Measured from the
    CLI (cwd `server/`) before the fix: the scan read 1,691 files of a
    3,489-file tree and still reported `scan_truncated: false` — a partial scan
    reading as the complete class, which is the one thing R7 exists to prevent.
    """
    workspace = make_workspace(db_session, ".")
    resolved = _repo_root(workspace)

    assert resolved is not None
    assert resolved.is_absolute(), "a relative repo_path must not reach the scan unresolved"
    assert resolved == resolve_workspace_root(workspace)
    assert (resolved / "server" / "loregarden").is_dir(), (
        "the scan root must be the repository root, not the server/ subtree it was launched from"
    )


def test_a_relative_repo_path_can_still_make_a_member_closable(db_session, tmp_path):
    """The consequence the scan count only hints at.

    `closable` is computed by opening the owed pointer under the repo root. With
    `Path(".")` the pointer never resolved, so NO member on the `loregarden`
    workspace could ever be closable and the enumerate tool's primary ordering —
    "which of those can I close now?", the question the whole response is
    ordered by — never fired.
    """
    workspace = make_workspace(db_session, ".")
    owed = (
        "server/tests/test_asserted_not_proven_listing.py"
        "::test_a_relative_repo_path_can_still_make_a_member_closable"
    )

    assert owed_test_resolves(_repo_root(workspace), owed) is True
    assert owed_test_resolves(Path("."), owed) is False, (
        "this is the defect itself: unresolved, the owed pointer never opens"
    )


def test_a_workspace_with_no_repo_still_answers_about_its_stored_rows(db_session):
    """The `None` the fix keeps: no repository means no scan, and the response
    says `scanned_files: 0` rather than pretending it read a tree."""
    workspace = make_workspace(db_session, "")
    assert _repo_root(workspace) is None

    ticket = make_ticket(db_session, workspace, number=1)
    register_claim(db_session, ticket, parse_claim(claim_values()))
    response = open_claims(
        db_session, workspace_slug=workspace.slug, repo_root=_repo_root(workspace)
    )
    assert response["total"] == 1
    assert response["scanned_files"] == 0
    assert response["scan_truncated"] is False


def test_a_kind_whose_members_all_fall_outside_the_cap_keeps_its_group(db_session, workspace, repo):
    """R5.6 / AC12(e), where the cap and the grouping meet.

    The cap is otherwise only ever exercised on a single-kind population, and
    the group counts only ever below the cap, so nothing caught a kind being
    dropped entirely once truncation starts. Two kinds, the smaller one past
    the boundary: the operator asking "which code sites are knowingly
    insufficient?" must be told one exists, even when no member of that kind
    fits on the page.

    AC12(e) is explicit that the group counts are what answer the question when
    no member is read — so a group with a true count and an empty `members`
    array is a complete answer, and a missing group is a wrong one.
    """
    ticket = make_ticket(db_session, workspace, number=1)
    behaviours = MAX_ARTIFACT_LIST_LIMIT + 36
    for index in range(behaviours):
        register_claim(
            db_session, ticket, parse_claim(claim_values(claim=f"unproven behaviour {index}"))
        )
    register_claim(db_session, ticket, parse_claim(claim_values(CODE_SITE_CLAIM)))

    response = _list(db_session, workspace, repo)
    assert response["total"] == behaviours + 1
    assert response["returned"] == MAX_ARTIFACT_LIST_LIMIT
    assert response["truncated"] is True

    groups = {group["member_kind"]: group for group in response["groups"]}
    assert set(groups) == {"behaviour", "code_site"}, (
        "a kind whose members all fall outside the cap must keep its group; its count "
        "is already known and dropping it says the kind does not exist"
    )
    assert groups["behaviour"]["count"] == behaviours
    assert groups["code_site"]["count"] == 1
    assert groups["code_site"]["members"] == [], (
        "the page legitimately holds none of them — the count is the answer here"
    )


def test_the_groups_are_ordered_by_the_kind_enum(db_session, workspace, repo, ticket):
    """Determinism across kinds: the same question twice is the same answer,
    and a kind holds its place whether or not the cap reached it."""
    register_claim(db_session, ticket, parse_claim(claim_values(CODE_SITE_CLAIM)))
    register_claim(db_session, ticket, parse_claim(claim_values()))

    ordered = [group["member_kind"] for group in _list(db_session, workspace, repo)["groups"]]
    assert ordered == [kind.value for kind in AssertedNotProvenKind if kind.value in set(ordered)]
    assert ordered == ["behaviour", "code_site"]


def test_a_ticket_filter_returns_only_that_tickets_members(db_session, workspace, repo):
    """AC12(e) NARROWING, at the volume that makes the leak obvious.

    The scanned half of the merge was concatenated unfiltered, so a ticket
    filter returned every `ceiling:` marker in the checkout. Measured on a repo
    with 200 marked files and a ticket owning 2 members: `total 211`, groups
    `[behaviour 1, code_site 210]`, 209 of them belonging to no ticket at all.

    A scanned marker has no owning ticket — `ticket_external_id` is empty on
    one by decision — so no ticket filter can match it.
    """
    marked = 200
    for index in range(marked):
        mark_a_code_site(repo, f"server/loregarden/services/mod{index}.py")
    ticket = make_ticket(db_session, workspace, number=1)
    register_claim(db_session, ticket, parse_claim(claim_values()))
    register_claim(db_session, ticket, parse_claim(claim_values(CODE_SITE_CLAIM)))

    unfiltered = _list(db_session, workspace, repo)
    assert unfiltered["total"] == marked + 2, "unfiltered, the tree's markers are members"

    filtered = _list(db_session, workspace, repo, ticket_id=ticket.id)
    assert filtered["total"] == 2
    assert {m["ticket_external_id"] for m in listed_members(filtered)} == {ticket.external_id}
    assert {group["member_kind"]: group["count"] for group in filtered["groups"]} == {
        "behaviour": 1,
        "code_site": 1,
    }, "the per-group counts under a ticket filter are the ticket's, not the tree's"


def test_a_ticket_filter_says_it_read_no_tree(db_session, workspace, repo):
    """R7.1 under narrowing: `scanned_files` is the number of files ACTUALLY
    read, and a ticket filter reads none — no scanned marker can match one.

    Zero here cannot be mistaken for a broken scan, because a failed scan
    raises rather than returning zero (R5.4, asserted next door).
    """
    mark_a_code_site(repo, "server/loregarden/services/counter.py")
    ticket = make_ticket(db_session, workspace, number=1)
    register_claim(db_session, ticket, parse_claim(claim_values()))

    assert _list(db_session, workspace, repo)["scanned_files"] >= 1
    filtered = _list(db_session, workspace, repo, ticket_id=ticket.id)
    assert filtered["scanned_files"] == 0
    assert filtered["scan_truncated"] is False
    assert filtered["total"] == 1
