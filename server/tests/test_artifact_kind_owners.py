"""Writer-owned artifact kinds are refused at every agent-facing door (lg-milestone-that-1423).

`artifacts.kind` is an open `str` column on purpose — ~27 agent-coined kinds in
the live database are that feature being used, and `ArtifactKind`'s own
docstring keeps it open. But one TIER of those kinds is read **as proof** by
consumers that judge the agent writing them, and nothing checked that the agent
was not the writer. `invariant_registry._has_a_passing_receipt` clears a landing
when any `land_verification` artifact on the ticket says `status == "passed"`,
checking nothing about `run_id`, `commit_sha`, or whether `candidate_sha`
relates to `ticket.landed_sha` — so the agent whose landing the detector judges
could mint the row that silences it. Measured 2026-10-23 on the 1419 branch
through `mcp.artifact_tools.attach_artifact`: the detector went from reporting
the landing to reporting nothing, `1 passed in 0.98s`.

The design these tests pin, so the implementer is not guessing at names:

- **One new module**, `loregarden/services/artifact_kind_owners.py`, holding
  `WRITER_OWNED_KINDS: Mapping[str, str]` (kind → the service or tool that owns
  producing it) and one pure function `refuse_if_writer_owned(kind: str) -> None`
  that raises `ValueError` naming the kind, its owner, and the tool to use
  instead. No Session, no database, importing only
  `loregarden.models.domain.enums` — owners are named as strings, so the guard
  carries none of the land-verification dependency tree and stays cycle-free
  from `mcp/` and `api/`. It lives in `services/` because `api/` must not import
  `mcp/`.
- **Called as the first statement of each agent-facing door**, not inside
  `OrchestrationCallbackService.attach_artifact`. Write authority is a property
  of the CALLER — did this string come from a model? — and not of the function.
  `mcp/fix_coverage_tools.py:84`, `mcp/ux_answers_tools.py:66` and
  `mcp/artifact_tools.py:62` all pass a reserved kind through that method as
  CODE, so a guard there would break three working tools and need a
  `platform_write=` keyword on a 14-caller method: a switch the next contributor
  flips to silence a refusal. The dedicated writers never reach the boundary
  guard at all, so AC4 holds by construction rather than by a branch.

Why the census is two-dimensional (AC3) rather than one call through one door:
AC3 as the ticket first stated it named only `loregarden_attach_artifact`, and
that is satisfiable with doors 2 and 3 open — every criterion green, the forgery
intact, found next at a security review, which is where this one was found. So
the census iterates the mapping ITSELF crossed with all three doors, and AC5
adds an AST census over the *column*, so a fourth writer fails with the question
it has to answer rather than by review.

What is deliberately NOT reserved, because reserving one of these breaks every
stage that attaches its own output: the open `ArtifactKind` members (`context`,
`log`, `diff`, `error`, `test`, `plan`, `pr`, `workflow_gate`, …) and the
agent-coined kinds (`source_analysis`, `test_strategy`, `spike`).
`handoff_not_validated` is not reserved either: it records the ABSENCE of proof,
so an agent writing one claims nothing.
"""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient
from loregarden.mcp import artifact_tools, document_tools, fix_coverage_tools, ux_answers_tools
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    DomainEvent,
    EventType,
    GateOutcome,
    OrchestrationRun,
    Ticket,
    TicketState,
    UnresolvedArtifactKind,
    Workspace,
)
from loregarden.services.artifact_kind_owners import (
    WRITER_OWNED_KINDS,
    refuse_if_writer_owned,
)
from loregarden.services.invariant_registry import INVARIANTS, LAND_VERIFICATION_RAN
from loregarden.services.land_verification import VerifyOutcome, record_land_verification
from loregarden.services.land_verification_receipt_writer import (
    LAND_VERIFICATION_RECEIPT_WRITER_SINCE,
)
from loregarden.services.operations import Caller, OperationId, Transport, run_operation
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.reconciliation import PERIODIC_STEPS
from loregarden.services.ticket_documents import write_document
from loregarden.testing.factories import (
    make_agent_run,
    make_artifact,
    make_orchestration_run,
    make_ticket,
)
from sqlalchemy import text
from sqlmodel import Session, select
from tests.repo_paths import REPO_ROOT

PACKAGE = REPO_ROOT / "server" / "loregarden"

#: The reserved set, written out so a kind DROPPED from the mapping fails too.
#: A kind is reserved when a consumer reads it as proof AND a named writer owns
#: producing it — not when an agent merely should not usually write it.
#:
#: The three `unresolved_*` kinds are here as deliberate defence in depth: the
#: service method keeps its own `UnresolvedArtifactKind` refusal
#: (`orchestration_callbacks.py:968`), whose scope is WIDER than these three
#: doors because it covers every internal caller — gate_recovery,
#: parallel_stage, handoff_writer, the inbox — and `test_unresolved_ledger.py`
#: asserts it at the service level with its message. Two guards, two scopes, and
#: the PR says so rather than leaving a later refactor to tidy one away.
RESERVED_KINDS = frozenset(
    {
        "land_verification",
        "evidence",
        "fix_coverage",
        "ux_answers",
        "handoff",
        "unresolved_open",
        "unresolved_waiver",
        "unresolved_clear",
    }
)

#: Kinds an agent legitimately attaches. The guard must pass every one of them:
#: `handoff_not_validated` records the absence of proof, so writing one claims
#: nothing, and `source_analysis` is one of the ~27 live agent coinages the open
#: column exists for.
OPEN_KINDS = (
    ArtifactKind.CONTEXT.value,
    ArtifactKind.LOG.value,
    ArtifactKind.DIFF.value,
    ArtifactKind.ERROR.value,
    ArtifactKind.TEST.value,
    ArtifactKind.PLAN.value,
    ArtifactKind.PR.value,
    ArtifactKind.WORKFLOW_GATE.value,
    ArtifactKind.HANDOFF_NOT_VALIDATED.value,
    "source_analysis",
    "test_strategy",
    "spike",
)

_FORGED_TITLE = "Land verification — passed — integration/lg-ms-1"
_FORGED_BODY = {"status": "passed", "stage_key": "verify"}


# --------------------------------------------------------------------------- #
# The mapping itself. The direction check: neither side may drift alone.
# --------------------------------------------------------------------------- #


def test_the_reserved_set_is_exactly_the_kinds_this_ticket_names():
    """A literal frozenset against the mapping's keys, in both directions.

    The census below iterates `WRITER_OWNED_KINDS` rather than a copied list, so
    a kind quietly REMOVED from the mapping would take its own census case with
    it and every remaining case would still pass. This is the half of the
    contract an iteration over the mapping cannot assert.
    """
    assert frozenset(WRITER_OWNED_KINDS) == RESERVED_KINDS, (
        f"the mapping no longer reserves {sorted(RESERVED_KINDS - set(WRITER_OWNED_KINDS))} "
        f"and newly reserves {sorted(set(WRITER_OWNED_KINDS) - RESERVED_KINDS)}. A kind "
        "dropped from the mapping is a forgeable record again; a kind added is a refusal "
        "for every stage that attaches it."
    )


def test_every_reserved_kind_names_the_writer_that_owns_it():
    """The owner is what makes the refusal actionable, so it is not optional.

    R2's measurement is why: two `handoff` rows in the live database were
    hand-written in 2026-09 after `write_handoff` refused them on a catalog gap.
    Under this guard that agent can no longer file one — which is the behaviour
    change the PR states — so the refusal has to name where to go instead.
    """
    for kind, owner in WRITER_OWNED_KINDS.items():
        assert owner and owner.strip(), f"{kind} names no owner"
        assert "loregarden" in owner, (
            f"{kind} is owned by {owner!r}, which names no module a person can open"
        )


def test_the_guard_is_pure_and_passes_every_kind_an_agent_legitimately_attaches():
    """The open column is a real capability, and the guard must not narrow it.

    Checked through the pure function with no Session and no database, which is
    the whole point of its placement: authority is decided from the string.
    """
    for kind in OPEN_KINDS:
        assert refuse_if_writer_owned(kind) is None, f"{kind} is not a writer-owned kind"


@pytest.mark.parametrize("kind", sorted(RESERVED_KINDS))
def test_the_guard_refuses_a_reserved_kind_naming_it_and_its_owner(kind: str):
    with pytest.raises(ValueError) as refusal:
        refuse_if_writer_owned(kind)

    message = str(refusal.value)
    assert kind in message, message
    assert WRITER_OWNED_KINDS[kind] in message, message


# --------------------------------------------------------------------------- #
# AC3's census: every reserved kind × every agent-facing door.
# The test name is literal in the `def`: the fix-coverage PR check greps for it.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Harness:
    session: Session
    engine: object
    ticket: Ticket
    run: OrchestrationRun
    client: TestClient


@dataclass(frozen=True)
class _Door:
    #: How an agent reaches it, as the refusal has to be surfaced there.
    name: str
    #: Performs the forgery and returns the refusal message a caller would read.
    refuse: Callable[[_Harness, str], str]


def _attach_artifact_over_mcp(harness: _Harness, kind: str) -> dict[str, Any]:
    """`loregarden_attach_artifact` as an agent sends it: the operation, over MCP."""
    return run_operation(
        harness.session,
        OperationId.ARTIFACT_ATTACH,
        {
            "run_id": harness.run.id,
            "kind": kind,
            "title": _FORGED_TITLE,
            "content_json": json.dumps(_FORGED_BODY),
        },
        Caller(transport=Transport.MCP),
    )


def _door_one_the_mcp_tool(harness: _Harness, kind: str) -> str:
    """`loregarden_attach_artifact` — the `artifact_attach` operation's `kind`.

    The door the ticket named. It is now one operation shared with door 2
    rather than a tool function of its own, so the guard sits in the handler
    and both transports meet it. Surfaced as a tool `ValueError`, in the shape
    `test_unresolved_ledger.py:1156` already proves reaches the agent as a tool
    error rather than a crash.
    """
    with pytest.raises(ValueError) as refusal:
        _attach_artifact_over_mcp(harness, kind)
    return str(refusal.value)


def _door_two_the_http_callback(harness: _Harness, kind: str) -> str:
    """`POST /api/orchestration/runs/{run_id}/attach_artifact` — `body.kind`.

    No auth dependency on the router, and `body.kind` goes straight to
    `svc.attach_artifact`. AC6 is asserted here: **400**, matching the seven
    existing `HTTPException(400, str(exc))` ValueError mappings in
    `api/orchestration.py` (lines 128, 145, 211, 323, 376, 402, 424; 422 is used
    nowhere in that router). A bare `ValueError` reaching the client is a 500 —
    `main.py` handles only `RequestValidationError` — so an operator would meet
    "Internal Server Error" for a rule the platform meant to state.
    """
    response = harness.client.post(
        f"/api/orchestration/runs/{harness.run.id}/attach_artifact",
        json={"kind": kind, "title": _FORGED_TITLE, "content": _FORGED_BODY},
    )
    assert response.status_code == 400, (
        f"door 2 answered {response.status_code} for kind {kind!r}, not 400. A refusal must "
        "read as a refusal: 500 is what an unmapped service ValueError becomes, and 422 is "
        f"used nowhere in this router. Body: {response.text[:400]}"
    )
    return str(response.json().get("detail", ""))


def _door_three_write_document(harness: _Harness, kind: str) -> str:
    """`services.ticket_documents.write_document` — a free-form `kind` param.

    It constructs `Artifact(...)` directly, bypassing the service method
    entirely, which is why the guard cannot live there. Reached by an agent
    through `loregarden_write_document`.

    It cannot forge a PASSING receipt today — its content object is fixed to
    `{format, body, summary, version, supersedes}`, so
    `_has_a_passing_receipt`'s `.get("status")` is `None` — which is why it is
    guarded and in this census but carries no repro of its own.
    """
    with pytest.raises(ValueError) as refusal:
        write_document(
            harness.session,
            harness.ticket,
            title=_FORGED_TITLE,
            body="A forged document under a writer-owned kind.",
            kind=kind,
        )
    return str(refusal.value)


DOORS = (
    _Door("loregarden_attach_artifact (operations.artifact_attach)", _door_one_the_mcp_tool),
    _Door("POST /api/orchestration/runs/{run_id}/attach_artifact", _door_two_the_http_callback),
    _Door("services.ticket_documents.write_document", _door_three_write_document),
)


@pytest.fixture(name="harness")
def harness_fixture(client: TestClient, isolated_db) -> _Harness:
    """A ticket and an orchestration run every door can be aimed at.

    Built here and not in a test body so a broken premise is an ERROR. The
    `client` fixture seeds the database and owns the app's session override, so
    door 2 writes through its own Session: artifact counts are taken from a
    fresh Session rather than this one, or the identity map would answer from
    before the request.
    """
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        ticket = make_ticket(
            session,
            workspace_id=workspace.id,
            external_id="lg-census-1423",
            title="A ticket every door can be aimed at",
        )
        run = make_orchestration_run(
            session, workspace_id=workspace.id, ticket_id=ticket.id, run_code="ORCH-CENSUS"
        )
        yield _Harness(session=session, engine=isolated_db, ticket=ticket, run=run, client=client)


def _artifact_kinds(harness: _Harness) -> list[str]:
    """Every artifact row on the harness ticket, read through a fresh Session."""
    with Session(harness.engine) as fresh:
        rows = fresh.exec(select(Artifact).where(Artifact.ticket_id == harness.ticket.id)).all()
    return [row.kind for row in rows]


def test_the_census_refuses_every_writer_owned_kind_at_every_agent_facing_door(harness: _Harness):
    """Every reserved kind, at every door, refused with its owner named.

    Iterated from `WRITER_OWNED_KINDS` itself and crossed with all three doors,
    rather than written out: a kind ADDED to the mapping is covered without
    anyone remembering to add a case, and a door is covered for every kind
    rather than for the one the reporter happened to try. The literal reserved
    set is checked separately, above, so a kind dropped from the mapping fails
    too.

    This test's name is literal in the `def` — no f-string, no runtime-built id
    — because it is AC7's `prevented_by` and the fix-coverage PR check greps the
    checkout for it.
    """
    before = sorted(_artifact_kinds(harness))
    failures: list[str] = []

    for kind, owner in sorted(WRITER_OWNED_KINDS.items()):
        for door in DOORS:
            message = door.refuse(harness, kind)
            if kind not in message:
                failures.append(f"{door.name} refused {kind!r} without naming the kind: {message}")
            if owner not in message:
                failures.append(
                    f"{door.name} refused {kind!r} without naming its owner {owner!r}: {message}"
                )

    assert not failures, "\n".join(failures)
    assert sorted(_artifact_kinds(harness)) == before, (
        "a refused write still left an artifacts row behind: "
        f"{sorted(set(_artifact_kinds(harness)) - set(before))}"
    )


def test_every_door_still_accepts_an_open_kind(harness: _Harness):
    """The control for the census: each door is reachable and writes when allowed.

    Without it, a door whose refusal came from a 404, a validation error or a
    typo'd route would read as a passing census case.
    """
    assert run_operation(
        harness.session,
        OperationId.ARTIFACT_ATTACH,
        {
            "run_id": harness.run.id,
            "kind": "source_analysis",
            "title": "An agent's own output",
            "content_json": "{}",
        },
        Caller(transport=Transport.MCP),
    )["artifact_id"]
    response = harness.client.post(
        f"/api/orchestration/runs/{harness.run.id}/attach_artifact",
        json={"kind": "test_strategy", "title": "An agent's own output", "content": {}},
    )
    assert response.status_code == 200, response.text
    write_document(
        harness.session,
        harness.ticket,
        title="A document under an open kind",
        body="Allowed.",
        kind="spike",
    )

    assert {"source_analysis", "test_strategy", "spike"} <= set(_artifact_kinds(harness))


# --------------------------------------------------------------------------- #
# AC5's census: a fourth writer of the column cannot appear unnoticed.
# Same shape as `test_land_verification_receipt.py:930-977`.
# --------------------------------------------------------------------------- #

#: The functions that put a value into `artifacts.kind`. `Artifact(...)` is the
#: construction, `make_artifact` the test factory wrapping it, and
#: `attach_artifact` / `write_document` the two service entry points that take a
#: kind and build the row for you.
_KIND_WRITERS = frozenset({"attach_artifact", "write_document", "Artifact", "make_artifact"})

#: Every writer of the `kind` column under `loregarden/` whose value comes from
#: DATA rather than from code, measured by `_data_driven_kind_writers` below.
#: Nine, not the three doors the ticket named, and the gap is the point of this
#: census: the walk is over the capability (a kind that arrives as a value) and
#: not over the list of doors somebody remembered.
#:
#: Each entry states why it is safe, because "it is in the frozen set" is not a
#: reason:
#:
#: - `services/operations/run_callbacks.py::_attach_artifact` — **doors 1 and
#:   2**, which are now one operation (`artifact_attach`). `loregarden_attach_artifact`
#:   over MCP and `POST /api/orchestration/runs/{run_id}/attach_artifact` both
#:   reach it, and `call.kind` goes straight to `svc.attach_artifact`; the REST
#:   router has no auth dependency. GUARDED in the handler, which is why both
#:   transports are covered by one guard; the refusal surfaces as a tool
#:   `ValueError` and as a 400 over REST (AC6, `run_rest_operation`).
#: - `mcp/document_tools.py::write_document_tool` — a FOURTH agent-facing door,
#:   found by this census and not by the ticket:
#:   `arguments.get("kind") or DEFAULT_DOCUMENT_KIND`. It needs no guard of its
#:   own because it funnels into door 3 below, which is guarded — but it is
#:   named here rather than left for the next reader to rediscover.
#: - `services/ticket_documents.py::write_document` — **door 3**. A free-form
#:   `kind` param carrying a `# py-org: allow-string` waiver, constructing
#:   `Artifact(...)` directly and bypassing the service method. GUARDED.
#: - `services/orchestration_callbacks.py::attach_artifact` — the shared service
#:   method, deliberately NOT guarded: 14 callers, three of which legitimately
#:   pass a reserved kind as code (`mcp/fix_coverage_tools.py:84`,
#:   `mcp/ux_answers_tools.py:66`, `mcp/artifact_tools.py:62`). It keeps its own
#:   narrower `UnresolvedArtifactKind` refusal, whose scope is every internal
#:   caller and therefore wider than these doors.
#: - `services/artifact_service.py::_upsert_artifact` — a private pass-through;
#:   all five of its callers are in that module and pass their kind as code.
#: - `services/run_completion.py::persist_run_artifacts` — `item.get("kind")`
#:   over dicts built in code (`run_evidence.run_context_artifacts`, the failure
#:   row, `_stage_report_artifact`). `artifacts` reaches `complete_run` only
#:   from in-process executors; no API body and no MCP argument supplies it.
#: - `testing/factories.py::make_artifact` — test support, and the way a suite
#:   plants the rows a guard exists to refuse (this module's own fixtures use it).
#: - `testing/prod_shape.py::_add_worked_history` — the scenario builder, over a
#:   `row["kind"]` from its own spec, writing a test database and never the live one.
DATA_DRIVEN_KIND_WRITERS = frozenset(
    {
        ("loregarden/mcp/document_tools.py", "write_document_tool"),
        ("loregarden/services/operations/run_callbacks.py", "_attach_artifact"),
        ("loregarden/services/artifact_service.py", "_upsert_artifact"),
        ("loregarden/services/orchestration_callbacks.py", "attach_artifact"),
        ("loregarden/services/run_completion.py", "persist_run_artifacts"),
        ("loregarden/services/ticket_documents.py", "write_document"),
        ("loregarden/testing/factories.py", "make_artifact"),
        ("loregarden/testing/prod_shape.py", "_add_worked_history"),
    }
)


def _written_as_code(node: ast.AST) -> bool:
    """Whether this `kind=` value is written in the source rather than taken from data.

    A string literal, an enum member (an attribute chain rooted at a
    CamelCase name, so `MonitorArtifactKind.FINDING.value` counts), a
    SCREAMING_SNAKE module constant aliasing one, or a conditional between two
    of those. Everything else — a parameter, an attribute of a request body, a
    `.get()` off an arguments dict, an `or` with one of those on either side —
    is a kind that arrives as a value, which is what this census counts.
    """
    if isinstance(node, ast.Constant):  # py-org: allow-isinstance
        return isinstance(node.value, str)  # py-org: allow-isinstance
    if isinstance(node, ast.Attribute):  # py-org: allow-isinstance
        root: ast.AST = node
        while isinstance(root, ast.Attribute):  # py-org: allow-isinstance
            root = root.value
        return isinstance(root, ast.Name) and root.id[:1].isupper()  # py-org: allow-isinstance
    if isinstance(node, ast.Name):  # py-org: allow-isinstance
        return node.id.isupper()
    if isinstance(node, ast.IfExp):  # py-org: allow-isinstance
        return _written_as_code(node.body) and _written_as_code(node.orelse)
    return False


def _data_driven_kind_writers() -> set[tuple[str, str]]:
    """Every `kind=<not written as code>` on an artifact writer, by file and function."""
    found: set[tuple[str, str]] = set()
    for source in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef
            ):  # py-org: allow-isinstance
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):  # py-org: allow-isinstance
                    continue
                func = sub.func
                called = (
                    func.attr
                    if isinstance(func, ast.Attribute)  # py-org: allow-isinstance
                    else getattr(func, "id", None)
                )
                if called not in _KIND_WRITERS:
                    continue
                for keyword in sub.keywords:
                    if keyword.arg == "kind" and not _written_as_code(keyword.value):
                        relative = str(source.relative_to(REPO_ROOT / "server"))
                        found.add((relative, node.name))
    return found


def test_a_fourth_writer_of_the_kind_column_cannot_appear_unnoticed():
    """AC5. The walk is over the column, not over the list of guards.

    A census written against the GUARD would have passed green with doors 2 and
    3 open, because it would only ever have asked "is every call site I know
    about guarded?". This asks the question the ticket could not: who puts a
    value into `artifacts.kind`? The answer was nine sites and four
    agent-facing doors, where the ticket named one.

    This is the lg-milestone-that-1418 learning applied to a different column:
    derive the census from the capability, so a new writer reds a test rather
    than passing a review.
    """
    writers = _data_driven_kind_writers()

    assert writers, "the walk found no writer at all, so it asserts nothing"
    assert writers == DATA_DRIVEN_KIND_WRITERS, (
        f"`artifacts.kind` is written from data by {sorted(writers - DATA_DRIVEN_KIND_WRITERS)} "
        f"as well, and no longer by {sorted(DATA_DRIVEN_KIND_WRITERS - writers)}. Every one is "
        "a writer that can be handed a reserved kind: either call "
        "`refuse_if_writer_owned` as its first statement, or state in "
        "DATA_DRIVEN_KIND_WRITERS why an agent cannot reach it."
    )


def test_no_artifact_aware_module_sets_kind_after_construction():
    """The companion to the walk above, which only sees `kind=` at a call.

    `Artifact.kind` is never assigned after the row is built — measured: the
    four `.kind = …` assignments under `loregarden/` are on a run error, a view,
    a reference row and a living-map item, none of whose modules so much as
    mention `Artifact`. So construction plus the writer calls are the whole
    surface, and a future `artifact.kind = payload["kind"]` fails here.
    """
    offenders: list[tuple[str, str]] = []
    for source in sorted(PACKAGE.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        if "Artifact" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            targets = (
                [*node.targets]
                if isinstance(node, ast.Assign)  # py-org: allow-isinstance
                else [node.target]
                if isinstance(node, ast.AnnAssign | ast.AugAssign)  # py-org: allow-isinstance
                else []
            )
            for target in targets:
                if (
                    isinstance(target, ast.Attribute) and target.attr == "kind"
                ):  # py-org: allow-isinstance
                    offenders.append((str(source.relative_to(REPO_ROOT / "server")), "kind"))

    assert offenders == [], (
        f"{offenders} sets `.kind` in a module that knows about Artifact. If that is an "
        "artifact row, it is a writer of the column this census covers and belongs in "
        "DATA_DRIVEN_KIND_WRITERS behind the guard."
    )


# --------------------------------------------------------------------------- #
# AC4. The service writers land unchanged, and by construction.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Landing:
    session: Session
    ticket: Ticket
    #: An existing `agent_runs.id`, so a run-stamped receipt can be written at all.
    run_id: str


@pytest.fixture(name="landed_ticket")
def landed_ticket_fixture(client: TestClient, isolated_db):
    """A done-and-landed ticket the `land_verification_ran` detector reports."""
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        since = LAND_VERIFICATION_RAN.since
        resolved = datetime(since.year, since.month, since.day, tzinfo=timezone.utc) + timedelta(
            days=1
        )
        ticket = make_ticket(
            session,
            workspace_id=workspace.id,
            external_id="lg-receipt-1423",
            title="A landing with a real receipt",
            state=TicketState.DONE,
            resolved_at=resolved,
        )
        ticket.resolved_at = resolved
        ticket.landed_sha = "c0ffee" + "0" * 34
        ticket.landed_branch = "integration/lg-ms-1"
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        assert {v.subject_id for v in LAND_VERIFICATION_RAN.detect(session)} == {ticket.id}, (
            "fixture: the detector does not report this landing, so nothing below means "
            "anything. Check DONE, a non-empty landed_sha, resolved_at at or after "
            f"{since}, and an armed land_verify_commands."
        )
        # A real run, because `artifacts.run_id` is a foreign key and SQLite
        # enforces it here: a receipt stamped with an invented run id fails on
        # the constraint rather than on the detector it is meant to probe.
        run = make_agent_run(
            session, workspace_id=workspace.id, ticket_id=ticket.id, run_code="RUN-FORGED"
        )
        yield _Landing(session=session, ticket=ticket, run_id=run.id)


def _outcome(result: GateOutcome) -> VerifyOutcome:
    return VerifyOutcome(
        outcome=result,
        ok=result is GateOutcome.PASSED,
        command="bash .lefthook/scripts/server-tests.sh",
        exit_code=0 if result is GateOutcome.PASSED else 1,
        output="…",
        detail="",
    )


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (GateOutcome.PASSED, "passed"),
        (GateOutcome.FAILED, "failed"),
        (GateOutcome.DISABLED, "disabled"),
    ],
)
def test_the_service_writer_still_writes_every_receipt_status(landed_ticket, result, status):
    """`record_land_verification` builds `Artifact(...)` and never reaches the guard.

    AC4 holds by CONSTRUCTION, not by a branch: the guard sits at the doors that
    take a kind from a model, and this writer passes
    `ArtifactKind.LAND_VERIFICATION` as code from inside the service that owns
    it. No exemption, no flag, nothing for a reviewer to audit — which is the
    whole reason the guard is not inside
    `OrchestrationCallbackService.attach_artifact`.
    """
    session, ticket = landed_ticket.session, landed_ticket.ticket

    receipt = record_land_verification(
        session,
        ticket,
        branch=ticket.landed_branch,
        target="main",
        candidate_sha="dec0de" + "0" * 34,
        outcome=_outcome(result),
    )

    assert receipt.kind == ArtifactKind.LAND_VERIFICATION
    assert json.loads(receipt.content_json)["status"] == status
    assert receipt.title == f"Land verification — {status} — {ticket.landed_branch}"


def test_a_real_passing_receipt_still_clears_the_detector(landed_ticket):
    """The other half of AC4: the guard must not break the record's purpose.

    A refusal that also stopped the owning service from writing would leave the
    detector reporting every landing forever — a guard that reads exactly like a
    guard working, in the other direction.
    """
    session, ticket = landed_ticket.session, landed_ticket.ticket

    record_land_verification(
        session,
        ticket,
        branch=ticket.landed_branch,
        target="main",
        candidate_sha="dec0de" + "0" * 34,
        outcome=_outcome(GateOutcome.PASSED),
    )

    assert {v.subject_id for v in LAND_VERIFICATION_RAN.detect(session)} == set()


def test_no_keyword_was_added_to_the_shared_service_method():
    """The rejected alternative, pinned so it cannot arrive by a later refactor.

    A `platform_write: bool = False` on `OrchestrationCallbackService.attach_artifact`
    is a switch the next contributor passes `True` to silence a refusal, across
    14 call sites nobody can audit at a glance.
    """
    parameters = set(inspect.signature(OrchestrationCallbackService.attach_artifact).parameters)

    assert parameters == {
        "self",
        "ticket",
        "kind",
        "title",
        "content",
        "run_id",
        "evidence_kind",
        "commit_sha",
    }, (
        f"the shared service method now takes {sorted(parameters)}. Write authority is a "
        "property of the caller, so it is decided at the doors that take a kind from a model "
        "— never by a flag on the method every writer shares."
    )


def test_the_service_methods_own_unresolved_refusal_is_unchanged(harness: _Harness):
    """The existing narrower refusal stays, and is asserted here as well as in
    `test_unresolved_ledger.py:588`, because this change reserves the same three
    kinds at the doors and a later refactor could read the service guard as the
    duplicate. Its scope is WIDER: every internal caller — gate_recovery,
    parallel_stage, handoff_writer, the inbox — not just the three doors.
    """
    svc = OrchestrationCallbackService(harness.session)
    for kind in UnresolvedArtifactKind:
        with pytest.raises(ValueError, match="reserved for the unresolved ledger"):
            svc.attach_artifact(harness.ticket, kind=kind, title="forged", content={})


# --------------------------------------------------------------------------- #
# AC7. Re-entry registered, and honest about what it cannot see.
# --------------------------------------------------------------------------- #

INVARIANT_NAME = "land_verification_receipt_is_service_written"

#: The keys `record_land_verification` writes that a forger has no reason to
#: copy. `status` and `stage_key` are excluded on purpose: they are the only two
#: `_has_a_passing_receipt` and the client's `reportVerdict` read, so they are
#: exactly what a forgery DOES carry, and a detector keyed on them would see
#: nothing.
SERVICE_WRITTEN_KEYS = frozenset(
    {"outcome", "branch", "target", "candidate_sha", "command", "exit_code", "output"}
)


def _invariant():
    found = [inv for inv in INVARIANTS if inv.name == INVARIANT_NAME]
    assert len(found) == 1, (
        f"expected exactly one invariant named {INVARIANT_NAME!r}, found {len(found)}"
    )
    return found[0]


def test_the_re_entry_invariant_is_registered_as_this_ticket_declares_it():
    invariant = _invariant()

    assert invariant.ticket == "lg-milestone-that-1423"
    assert invariant.statement == (
        "every land-verification receipt was written by record_land_verification"
    )
    # Report-only: the row is garbage, but deleting a ticket's artifact
    # unprompted is a person's call.
    assert invariant.repair is None
    assert invariant.unrepaired_action.strip(), "a report-only invariant owes a person an action"
    assert invariant.reentry.value == "defect"


def test_the_invariants_prevented_by_names_the_census_test_in_this_checkout():
    """A `DEFECT` invariant's `prevented_by` must resolve to a test that exists.

    The census is the prevention; this invariant is only the backstop.
    """
    invariant = _invariant()
    path, _, name = invariant.prevented_by.partition("::")

    assert path == "server/tests/test_artifact_kind_owners.py", invariant.prevented_by
    assert name == "test_the_census_refuses_every_writer_owned_kind_at_every_agent_facing_door", (
        invariant.prevented_by
    )
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert f"def {name}(" in source, f"{invariant.prevented_by} is not in the checkout"


def test_the_existing_landing_invariant_is_not_repointed():
    """`LAND_VERIFICATION_RAN` answers a different question and keeps its own test.

    Repointing it would leave "every recorded landing has a passing receipt"
    proved by a test about who may write one.
    """
    assert LAND_VERIFICATION_RAN.prevented_by == (
        "server/tests/test_land_verification_receipt.py"
        "::test_every_landing_that_records_a_landed_sha_writes_a_receipt"
    )


def test_a_receipt_the_owning_service_wrote_is_not_a_violation(landed_ticket):
    session, ticket = landed_ticket.session, landed_ticket.ticket
    record_land_verification(
        session,
        ticket,
        branch=ticket.landed_branch,
        target="main",
        candidate_sha="dec0de" + "0" * 34,
        outcome=_outcome(GateOutcome.PASSED),
    )

    assert {v.subject_id for v in _invariant().detect(session)} == set()


def test_the_detectors_required_keys_are_the_ones_the_service_actually_writes(landed_ticket):
    """Measured against a receipt `record_land_verification` really produced.

    Pinned this way round on purpose: a detector keyed on a hand-copied list
    empties itself the day the receipt body changes, and reports nothing while
    the surface says there are no violations. Dropping any one key must make the
    row detectable, so a change to the body fails a test instead.
    """
    session, ticket = landed_ticket.session, landed_ticket.ticket
    receipt = record_land_verification(
        session,
        ticket,
        branch=ticket.landed_branch,
        target="main",
        candidate_sha="dec0de" + "0" * 34,
        outcome=_outcome(GateOutcome.PASSED),
    )
    body = json.loads(receipt.content_json)

    assert SERVICE_WRITTEN_KEYS <= set(body), (
        f"the receipt no longer carries {sorted(SERVICE_WRITTEN_KEYS - set(body))}, so the "
        "detector's required keys name fields nothing writes and it reports every receipt"
    )
    assert "status" not in SERVICE_WRITTEN_KEYS and "stage_key" not in SERVICE_WRITTEN_KEYS, (
        "`status` and `stage_key` are what a forgery carries, so they discriminate nothing"
    )

    for key in sorted(SERVICE_WRITTEN_KEYS):
        incomplete = {k: v for k, v in body.items() if k != key}
        receipt.content_json = json.dumps(incomplete)
        session.add(receipt)
        session.commit()

        assert {v.subject_id for v in _invariant().detect(session)} == {receipt.id}, (
            f"a receipt missing {key!r} is not detected, so that key is not a discriminator "
            "and SERVICE_WRITTEN_KEYS overstates what the detector checks"
        )

    receipt.content_json = json.dumps(body)
    session.add(receipt)
    session.commit()
    assert {v.subject_id for v in _invariant().detect(session)} == set()


def test_a_receipt_carrying_a_run_id_is_a_violation(landed_ticket):
    """The second discriminator. `record_land_verification` sets no `run_id`, so
    a row that carries one was written through a tool that stamps the agent's
    run — which is every door in this census.
    """
    session, ticket = landed_ticket.session, landed_ticket.ticket
    receipt = record_land_verification(
        session,
        ticket,
        branch=ticket.landed_branch,
        target="main",
        candidate_sha="dec0de" + "0" * 34,
        outcome=_outcome(GateOutcome.PASSED),
    )
    receipt.run_id = landed_ticket.run_id
    session.add(receipt)
    session.commit()

    assert {v.subject_id for v in _invariant().detect(session)} == {receipt.id}


def test_the_violation_names_the_ticket_and_what_a_person_should_look_at(landed_ticket):
    session, ticket = landed_ticket.session, landed_ticket.ticket
    receipt = record_land_verification(
        session,
        ticket,
        branch=ticket.landed_branch,
        target="main",
        candidate_sha="dec0de" + "0" * 34,
        outcome=_outcome(GateOutcome.PASSED),
    )
    receipt.run_id = landed_ticket.run_id
    session.add(receipt)
    session.commit()

    violation = next(iter(_invariant().detect(session)))

    assert violation.ticket_id == ticket.id
    assert violation.subject_id == receipt.id
    assert ticket.external_id in violation.detail, violation.detail


def test_a_forgery_that_copies_every_key_and_sets_no_run_id_is_invisible_to_the_detector(
    landed_ticket: _Landing,
):
    """The detector's limit, pinned as behaviour rather than asserted as prose.

    Both discriminators are after-the-fact properties of the row, so a forger
    that passes `run_id=None` AND copies all nine keys
    `record_land_verification` writes is indistinguishable from the service's
    own output — and for `evidence` (which legitimately carries `run_id`) and
    `write_document` (which legitimately does not) there is no discriminator at
    all. So this invariant is a BACKSTOP for the forgery shapes that are
    visible; `refuse_if_writer_owned` at the doors is the prevention.

    Asserted here, and failing if a later change makes the detector appear to
    catch this, because the alternative is a reader taking its zero for
    coverage of the reserved set — a guard failing that reads exactly like a
    guard working. If someone does find a discriminator for this shape, this
    test is the right place to fail and be rewritten.
    """
    session, ticket = landed_ticket.session, landed_ticket.ticket
    real = record_land_verification(
        session,
        ticket,
        branch=ticket.landed_branch,
        target="main",
        candidate_sha="dec0de" + "0" * 34,
        outcome=_outcome(GateOutcome.PASSED),
    )
    body = json.loads(real.content_json)
    session.delete(real)
    session.commit()

    forged = make_artifact(
        session,
        ticket_id=ticket.id,
        kind=ArtifactKind.LAND_VERIFICATION,
        title=f"Land verification — passed — {ticket.landed_branch}",
        content={**body, "candidate_sha": "f0rged" + "0" * 34},
        run_id=None,
    )

    assert {v.subject_id for v in _invariant().detect(session)} == set(), (
        "the detector now reports a receipt that copies every key and carries no run_id. "
        "That is a better detector than AC7 claims — rewrite this test and the PR's stated "
        "limit together, so the invariant's coverage and what it says about itself agree."
    )
    assert {v.subject_id for v in LAND_VERIFICATION_RAN.detect(session)} == set(), (
        "control: the forged row did clear the landing detector, which is why the guard at "
        "the doors and not this invariant is the fix"
    )
    assert forged.run_id is None


# --------------------------------------------------------------------------- #
# The census above asks each door the question in the one spelling the reporter
# used. These ask it the way an agent that WANTS the row would: in a spelling
# the guard does not recognise, through the door nobody named, and with a value
# that is not a string at all. Measured against a guard wired exactly as the
# spec words it — `refuse_if_writer_owned` as the first statement of each of
# the three doors — where the whole census above was green and the rows below
# were written anyway.
# --------------------------------------------------------------------------- #

#: Spellings of a reserved kind that are not the reserved kind's literal value.
#: `write_document` normalizes its kind AFTER the guard would run
#: (`ticket_documents.py:69`, `kind = kind.strip() or DEFAULT_DOCUMENT_KIND`),
#: so a guard placed as "the first statement" of that function is handed
#: `" land_verification"` and the row is filed under `"land_verification"`.
#: Measured, with the naive wiring: three `land_verification` rows and one
#: `handoff` row stored past a green census.
_SPELLINGS = (
    "{kind}",
    " {kind}",
    "{kind} ",
    "  {kind}  ",
    "\t{kind}\n",
)


def _stored_reserved_kinds(harness: _Harness) -> list[str]:
    """Rows on the harness ticket whose STORED kind is writer-owned.

    The oracle AC3 means, read off the row rather than off the argument: a
    refusal that inspects the string an agent sent proves nothing if the writer
    transforms that string into a reserved kind one line later.
    """
    return sorted(kind for kind in _artifact_kinds(harness) if kind in WRITER_OWNED_KINDS)


def _door_four_the_document_tool(harness: _Harness, kind: str) -> str:
    """`loregarden_write_document` — `arguments.get("kind") or DEFAULT_DOCUMENT_KIND`.

    The door `DATA_DRIVEN_KIND_WRITERS` found and the ticket does not name. Its
    entry there says it needs no guard of its own "because it funnels into door
    3, which is guarded" — true of the code and worth asserting rather than
    believing, since it is the only door whose safety is inherited.
    """
    with pytest.raises(ValueError) as refusal:
        document_tools.write_document_tool(
            harness.session,
            {
                "ticket_id": harness.ticket.external_id,
                "title": _FORGED_TITLE,
                "body": "A forged document under a writer-owned kind.",
                "kind": kind,
                "include_ancestors": True,
            },
        )
    return str(refusal.value)


def _attempt(harness: _Harness, door_name: str, kind: str) -> None:
    """Send `kind` at one door and swallow nothing but the refusal we expect.

    Any exception is recorded by the caller through the rows it did or did not
    leave behind; the assertion is over the database, not over the call.
    """
    try:
        if door_name == "loregarden_attach_artifact (operations.artifact_attach)":
            _attach_artifact_over_mcp(harness, kind)
        elif door_name == "services.ticket_documents.write_document":
            write_document(
                harness.session,
                harness.ticket,
                title=f"A forged document — {kind!r}",
                body="A forged document under a writer-owned kind.",
                kind=kind,
            )
        else:
            document_tools.write_document_tool(
                harness.session,
                {
                    "ticket_id": harness.ticket.external_id,
                    "title": f"A forged document — {kind!r}",
                    "body": "A forged document under a writer-owned kind.",
                    "kind": kind,
                    "include_ancestors": True,
                },
            )
    except ValueError:  # the refusal under test; the rows are the assertion
        harness.session.rollback()


@pytest.mark.parametrize("spelling", _SPELLINGS)
def test_no_door_can_store_a_row_whose_kind_is_writer_owned(harness: _Harness, spelling: str):
    """AC3's oracle stated over the stored row, across spellings of the kind.

    The census above asserts that a call is refused and that the kind and owner
    are named. That is the right assertion for the exact value, and it is blind
    to the case that matters most here, because `artifacts.kind` is what every
    reader matches on: `landing_status._refusals`,
    `landing_refused.recent_refusals` and `handoff_store`'s latest-handoff read
    all select `Artifact.kind == <reserved>` and none of them sees the argument
    a door was handed.

    So the question is not "was the string refused?" but "can a row whose kind
    is writer-owned exist?". Measured against the naive wiring, the answer was
    yes for every padded spelling at both document doors.
    """
    doors = (
        "loregarden_attach_artifact (operations.artifact_attach)",
        "services.ticket_documents.write_document",
        "mcp.document_tools.write_document_tool",
    )
    for kind in sorted(WRITER_OWNED_KINDS):
        for door in doors:
            _attempt(harness, door, spelling.format(kind=kind))

    assert _stored_reserved_kinds(harness) == [], (
        f"spelled {spelling.format(kind='<kind>')!r}, a door stored "
        f"{_stored_reserved_kinds(harness)}. A writer that normalizes its kind after the "
        "guard runs — `write_document` strips it on the next line — turns a refusal into a "
        "spelling test: the guard must decide on the value the row will carry."
    )


@pytest.mark.parametrize("kind", sorted(RESERVED_KINDS))
def test_the_fourth_door_refuses_every_reserved_kind_and_names_its_owner(
    harness: _Harness, kind: str
):
    """The one door whose safety is inherited, asserted rather than commented.

    `DATA_DRIVEN_KIND_WRITERS` names `mcp/document_tools.py::write_document_tool`
    and says it needs no guard of its own. Nothing checked that: the census
    crosses its kinds with three doors, and this is the fourth. If door 3's
    guard ever moves to its caller, or the tool grows its own `Artifact(...)`,
    this is where it fails.
    """
    message = _door_four_the_document_tool(harness, kind)

    assert kind in message, message
    assert WRITER_OWNED_KINDS[kind] in message, message
    assert _stored_reserved_kinds(harness) == [], _stored_reserved_kinds(harness)


def test_a_refused_write_publishes_no_artifact_created_event(harness: _Harness):
    """A refusal must not be announced as a write.

    Both writers publish `ARTIFACT_CREATED` after the row (`attach_artifact`
    and `write_document`), and the artifacts panel and the event feed read it.
    A guard placed after construction but before the commit would leave the
    refused kind in an event payload — the feed saying a row exists that does
    not, which is the inverse of a silent failure and just as wrong.
    """
    with Session(harness.engine) as fresh:
        before = len(
            fresh.exec(
                select(DomainEvent).where(DomainEvent.type == EventType.ARTIFACT_CREATED)
            ).all()
        )

    for kind in sorted(WRITER_OWNED_KINDS):
        for door in DOORS:
            door.refuse(harness, kind)

    with Session(harness.engine) as fresh:
        published = fresh.exec(
            select(DomainEvent).where(DomainEvent.type == EventType.ARTIFACT_CREATED)
        ).all()

    assert len(published) == before, (
        f"{len(published) - before} ArtifactCreated event(s) were published for writes that "
        f"were refused: {[row.payload_json for row in published[before:]]}"
    )


# --------------------------------------------------------------------------- #
# AC4 the other way round: the guard must not break the writers that pass a
# reserved kind as code. "By construction" is a claim about where the guard
# sits, and the only thing that distinguishes it from a guard in the shared
# service method is whether these three still work.
# --------------------------------------------------------------------------- #


def test_the_three_tools_that_pass_a_reserved_kind_as_code_still_write_their_row(
    harness: _Harness,
):
    """`attach_evidence`, `record_fix_coverage` and `record_ux_answers`.

    Each is an agent tool that writes a RESERVED kind legitimately, passing it
    as code from the service that owns it — `evidence.ARTIFACT_KIND`,
    `ArtifactKind.FIX_COVERAGE`, `ArtifactKind.UX_ANSWERS` — through
    `OrchestrationCallbackService.attach_artifact`. They are the entire reason
    the guard is at the doors and not in that method, and the three tickets
    whose stages break first if it moves.

    `test_no_keyword_was_added_to_the_shared_service_method` catches only the
    version of that mistake that also adds a `platform_write=` keyword. A guard
    dropped into the method with no keyword leaves this ticket's own census
    green and silently refuses every bug ticket's fix coverage, every UX answer
    and every piece of evidence in the system.
    """
    session, ticket = harness.session, harness.ticket
    svc = OrchestrationCallbackService(session)

    artifact_tools.attach_evidence(
        session,
        svc,
        ticket,
        {
            "evidence_kind": "test_red_green",
            "title": "A red-to-green run",
            "content_json": json.dumps({"output": "1 passed"}),
        },
        run_id=None,
    )
    next(iter(ux_answers_tools.HANDLERS.values()))(
        session,
        {
            "ticket_id": ticket.external_id,
            "no_change_reason": (
                "server-only guard at the artifact doors; no user-facing surface is touched"
            ),
        },
    )
    next(iter(fix_coverage_tools.HANDLERS.values()))(
        session,
        {
            "ticket_id": ticket.external_id,
            "existing": "measured read-only on the live database; no forged receipt exists",
            "reentry": f"`{INVARIANT_NAME}`, registered report-only in invariant_registry",
            "prevention": (
                "server/tests/test_artifact_kind_owners.py::"
                "test_the_census_refuses_every_writer_owned_kind_at_every_agent_facing_door"
            ),
            "learning": (
                "a record a detector trusts must not be writable by the subject it judges"
            ),
        },
    )

    assert {"evidence", "ux_answers", "fix_coverage"} <= set(_artifact_kinds(harness)), (
        f"the harness ticket carries {sorted(set(_artifact_kinds(harness)))}. A reserved kind "
        "passed as CODE by the service that owns it must still be written: these three tools "
        "are why the guard belongs at the doors rather than in the method they share."
    )


# --------------------------------------------------------------------------- #
# The guard's own edges: the value it is handed comes from a model, so its type
# and its spelling are both the agent's to choose.
# --------------------------------------------------------------------------- #

#: Every `kind` an agent can actually put in an MCP argument dict. Door 1 reads
#: it with `arguments.get("kind", "log")`, so an explicit `"kind": null` is
#: `None` rather than the default, and a JSON list or object arrives as one.
_NON_STRING_KINDS = (None, 7, True, 1.5, ["land_verification"], {"kind": "land_verification"})


@pytest.mark.parametrize("kind", _NON_STRING_KINDS)
def test_the_guard_answers_a_kind_that_is_not_a_string_without_faulting(kind):
    """A guard on an agent-supplied value does not get to assume the type.

    AC6's rule is that the refusal reads as a refusal rather than as a
    control-plane fault, and the guard is the new code that can produce one:
    with the naive wiring, `{"kind": ["land_verification"]}` at door 1 came back
    as `TypeError: unhashable type: 'list'` out of a mapping lookup, which
    reaches an agent as a crash it cannot read and an operator as a 500.

    Either answer is correct here — refuse it as a `ValueError`, or pass it
    through as not-a-reserved-kind and let the writer's own constraint reject it
    (today a `None` kind is a NOT NULL violation at the column). What the guard
    may not do is fault on the lookup.
    """
    try:
        assert refuse_if_writer_owned(kind) is None
    except ValueError:
        pass  # a refusal is an answer: the guard decided, and said so


def test_the_reserved_mapping_cannot_be_rewritten_at_runtime():
    """The mapping is the authority list, so it is not a mutable module global.

    Every door reads it on every call. A plain `dict` means any importer — a
    later service, a conftest, a test that wanted to "temporarily" allow one
    kind — can delete `land_verification` from it for the rest of the process,
    and nothing in this module would notice: the census iterates the mapping,
    so a kind removed at runtime takes its own case with it.
    """
    with pytest.raises(TypeError):
        WRITER_OWNED_KINDS["land_verification"] = "me"  # type: ignore[index]

    assert "land_verification" in WRITER_OWNED_KINDS


def test_the_guard_is_deterministic_and_holds_no_state():
    """Same question, same answer, whatever order it is asked in.

    The guard is called on every artifact write in the process. A cache, a
    counter or a first-call-only check would make the refusal depend on what
    ran before it — and the census, which asks 24 questions in a loop over one
    session, is exactly the shape that would hide it.
    """
    for _ in range(3):
        for kind in sorted(WRITER_OWNED_KINDS):
            with pytest.raises(ValueError):
                refuse_if_writer_owned(kind)
        for kind in OPEN_KINDS:
            assert refuse_if_writer_owned(kind) is None


# --------------------------------------------------------------------------- #
# AC5's census is only as good as its classifier and its reach. Both are code
# in this module, and neither has an oracle: `DATA_DRIVEN_KIND_WRITERS` is
# compared against the walk's own output, so a classifier that quietly called
# more things "written as code" would shrink both sides of a passing test. The
# `assert writers` line catches the walk returning nothing; nothing catches it
# returning eight of nine.
# --------------------------------------------------------------------------- #

#: `kind=<expression>` → is that kind written in the source, or does it arrive
#: as a value? The right-hand column is what AC5's walk must believe, and the
#: last two rows are the classifier's known blind spots, asserted so they are
#: visible rather than discovered by the next forgery.
_CLASSIFIER_CASES = (
    ('f(kind="land_verification")', True, "a literal is the kind the author chose"),
    ("f(kind=ArtifactKind.LAND_VERIFICATION)", True, "an enum member is a closed vocabulary"),
    ("f(kind=MonitorArtifactKind.FINDING.value)", True, "…and so is one of its values"),
    ("f(kind=EVIDENCE_ARTIFACT_KIND)", True, "a SCREAMING_SNAKE alias of one"),
    ('f(kind=ArtifactKind.PLAN if x else "log")', True, "a choice between two coded kinds"),
    ("f(kind=body.kind)", False, "an attribute of a request body — door 2"),
    ('f(kind=arguments.get("kind", "log"))', False, "an MCP argument — door 1"),
    ("f(kind=kind)", False, "a parameter — door 3"),
    ('f(kind=arguments.get("kind") or DEFAULT_DOCUMENT_KIND)', False, "the fourth door"),
    ('f(kind=row["kind"])', False, "a subscript off data"),
    ('f(kind=f"{prefix}_log")', False, "built from a value at runtime"),
    ("f(kind=str(payload.kind))", False, "laundered through a call"),
    ('f(kind=payload.get("kind") if payload else arguments["kind"])', False, "both arms data"),
    (
        'f(kind=ArtifactKind.PLAN if x else arguments["kind"])',
        False,
        "one coded arm does not make the other one coded",
    ),
    (
        "f(kind=KIND_FROM_BODY)",
        True,
        "BLIND SPOT: a SCREAMING_SNAKE name is taken for an enum alias, so a module "
        "constant assigned from data reads as code and its writer drops out of the walk",
    ),
    (
        "f(kind=Body.kind)",
        True,
        "BLIND SPOT: an attribute chain rooted at a CamelCase name is taken for an enum, "
        "so a class-shaped holder of agent data reads as code",
    ),
)


@pytest.mark.parametrize(("source", "coded", "why"), _CLASSIFIER_CASES)
def test_the_census_classifier_tells_a_coded_kind_from_one_that_arrives_as_data(
    source: str, coded: bool, why: str
):
    """The oracle `_written_as_code` does not otherwise have.

    Without this, the only check on the classifier is that the walk finds
    SOMETHING, and a classifier loosened by one `isinstance` branch would move
    a real door into the "written as code" column, drop it from the walk, and
    be reported by `test_a_fourth_writer_of_the_kind_column_cannot_appear_unnoticed`
    as a writer that is "no longer" there — a message that reads like a door
    being closed.

    The two blind spots are asserted as blind spots on purpose. They are the
    shapes a new door would have to take to hide from AC5's walk, so a reader
    deciding whether the census is complete can see the answer here instead of
    inferring it from the implementation.
    """
    tree = ast.parse(source)
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)  # py-org: allow-isinstance
    )
    keyword = next(k for k in call.keywords if k.arg == "kind")

    assert _written_as_code(keyword.value) is coded, f"{source} — {why}"


#: Every writer of the `kind` column that AC5's walk reaches, and the signature
#: property that makes the walk complete: `kind` is keyword-only on all of them.
_KIND_WRITER_SIGNATURES = (
    ("loregarden.models.domain", "Artifact"),
    ("loregarden.services.orchestration_callbacks", "OrchestrationCallbackService"),
    ("loregarden.services.ticket_documents", "write_document"),
    ("loregarden.testing.factories", "make_artifact"),
)


def test_every_artifact_writer_takes_its_kind_as_a_keyword_only_argument():
    """What makes AC5's walk able to see every writer at all.

    `_data_driven_kind_writers` reads `sub.keywords` for `arg == "kind"`. A
    writer that accepted its kind positionally would be invisible to it:
    `make_artifact(session, ticket_id, payload["kind"])` passes the walk, passes
    the frozen set, and writes any kind it likes. Measured on this tree, all 25
    positional arguments to a kind writer are leading ones (`session`,
    `ticket`), `kind` is keyword-only everywhere, and no call splats `**` into
    one — so the walk is complete today, and this is the signature that keeps it
    complete.
    """
    offenders: list[str] = []
    for module_name, attribute in _KIND_WRITER_SIGNATURES:
        module = __import__(module_name, fromlist=[attribute])
        target = getattr(module, attribute)
        callable_under_test = (
            target.attach_artifact if attribute.endswith("Service") else target  # noqa: B009
        )
        parameters = inspect.signature(callable_under_test).parameters
        if "kind" not in parameters:
            offenders.append(f"{module_name}.{attribute} takes no `kind` any more")
            continue
        if parameters["kind"].kind is not inspect.Parameter.KEYWORD_ONLY:
            offenders.append(
                f"{module_name}.{attribute} takes `kind` as "
                f"{parameters['kind'].kind.description}, so AC5's walk over `kind=` keywords "
                "cannot see a caller that passes it positionally"
            )

    assert offenders == [], "\n".join(offenders)


def test_no_caller_splats_a_dict_into_an_artifact_writer():
    """The other way past a walk that reads keywords by name.

    `Artifact(**payload)` and `svc.attach_artifact(ticket, **arguments)` carry a
    kind the walk cannot see and `_written_as_code` is never asked about.
    Measured: zero such calls under `loregarden/`.
    """
    splats: list[tuple[str, str, int]] = []
    for source in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):  # py-org: allow-isinstance
                continue
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)  # py-org: allow-isinstance
                else getattr(func, "id", None)
            )
            if called in _KIND_WRITERS and any(k.arg is None for k in node.keywords):
                splats.append((str(source.relative_to(REPO_ROOT / "server")), called, node.lineno))

    assert splats == [], (
        f"{splats} splat a dict into an artifact writer, so the kind they write is invisible "
        "to AC5's walk. Pass `kind=` explicitly, through the guard."
    )


def test_the_artifact_row_is_never_constructed_under_an_alias():
    """`Artifact` is in `_KIND_WRITERS` by NAME, so an alias hides a writer.

    `from loregarden.models.domain import Artifact as Row` followed by
    `Row(kind=arguments["kind"])` is a new direct writer of the column that
    AC5's walk does not match and no guard sees — the R5 case the ticket accepts
    as a limit, narrowed to the one form of it a test can close.
    """
    aliased: list[tuple[str, str]] = []
    for source in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):  # py-org: allow-isinstance
                continue
            for alias in node.names:
                if alias.name in _KIND_WRITERS and alias.asname:
                    aliased.append(
                        (
                            str(source.relative_to(REPO_ROOT / "server")),
                            f"{alias.name} as {alias.asname}",
                        )
                    )

    assert aliased == [], (
        f"{aliased} import an artifact writer under another name, which AC5's walk matches by "
        "name and therefore cannot see. Import it as itself."
    )


#: Spellings that are NOT the reserved kind, and must not be refused by the
#: guard itself. Paired with
#: `test_no_door_can_store_a_row_whose_kind_is_writer_owned`, these two pin the
#: fix to one shape: the writer normalizes, then calls an exact guard.
_NOT_THE_RESERVED_KIND = (
    "EVIDENCE",
    "Land_Verification",
    " evidence",
    "evidence ",
    "evidence_summary",
    "land_verification_notes",
    "my_handoff",
)


@pytest.mark.parametrize("kind", _NOT_THE_RESERVED_KIND)
def test_the_guard_decides_on_the_exact_value_and_does_not_normalize_it(kind: str):
    """The guard is exact, and the writer is what owes it the right value.

    This is the counterweight to the stored-row census above, because the
    tempting fix for that failure is a `.strip().lower()` inside the guard —
    and that is the defensive normalization this repo rejects by name.
    `_has_a_passing_receipt`'s own docstring says so for the reader side: the
    comparison is exact, and normalizing there "would clear a landing whose
    receipt the client itself colours `warn`". The same argument holds here. A
    guard that lower-cases its argument reserves `EVIDENCE` and `Evidence`,
    neither of which any reader matches, and starts refusing coinages the open
    column exists for.

    So the strip belongs where the value is decided — `write_document` already
    normalizes its kind, and must do it BEFORE it asks — and this guard answers
    about the string it is given, which is the string the row will carry.
    """
    assert refuse_if_writer_owned(kind) is None, (
        f"{kind!r} is not a writer-owned kind: it is not what any reader matches on, and a "
        "guard that normalizes its way to a refusal reserves spellings nobody reads and "
        "refuses coinages the open column exists for. Normalize at the writer, ask exactly."
    )


# --------------------------------------------------------------------------- #
# AC1 / AC2. The reproduction, moved here from `test_open_defect_repros.py` in
# the same change as the fix: the fix turns its `xfail(strict=True)` into an
# XPASS, so leaving it there would be a red suite rather than a closed defect.
# The marker is gone and the assertions are unchanged — this is the baseline
# measured on 2026-10-23 against the lg-milestone-that-1419 branch, where the
# identical call took the detector from reporting the landing to reporting
# nothing (`1 passed in 0.98s`), now inverted.
# --------------------------------------------------------------------------- #

#: The forgery, exactly as the baseline measured it. `status` and `stage_key`
#: are the only two keys `_has_a_passing_receipt` and the client's
#: `reportVerdict` read, so a forger needs no more than these: the detector
#: checks nothing about who wrote the row — not `run_id`, not `commit_sha`, not
#: whether `candidate_sha` relates to `ticket.landed_sha`.
_FORGED_RECEIPT = {"status": "passed", "stage_key": "verify"}


class _ForgeryUnderTest(NamedTuple):
    session: Session
    ticket: Ticket
    #: The `artifact_attach` arguments, naming the run the forging agent holds.
    arguments: dict


@pytest.fixture
def forged_land_verification_receipt(db_session: Session) -> _ForgeryUnderTest:
    """A done-and-landed ticket the `land_verification_ran` detector reports.

    Every assertion about the setup lives here, not in the test body. It was
    written that way while this was an `xfail` repro — `xfail` absorbs
    call-phase failures, so a repro whose fixture is broken looks exactly like a
    healthy open defect — and it stays that way because the premise is still
    what makes the test mean anything. The detector's workspace condition is
    easy to get wrong: a landing in a workspace whose `land_verify_commands` is
    empty is NOT a per-landing violation.
    """
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    since = LAND_VERIFICATION_RAN.since
    resolved = datetime(since.year, since.month, since.day, tzinfo=timezone.utc) + timedelta(days=1)
    ticket = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="lg-forge-1423",
        title="A landing the detector reports",
        state=TicketState.DONE,
        resolved_at=resolved,
    )
    ticket.resolved_at = resolved
    ticket.landed_sha = "c0ffee" + "0" * 34
    ticket.landed_branch = "integration/lg-ms-1"
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    run = make_orchestration_run(
        db_session, workspace_id=workspace.id, ticket_id=ticket.id, run_code="ORCH-FORGE"
    )

    assert {v.subject_id for v in LAND_VERIFICATION_RAN.detect(db_session)} == {ticket.id}, (
        "fixture: the detector does not report this landing, so silencing it would prove "
        "nothing. Check the four conditions — DONE, a non-empty landed_sha, a resolved_at at "
        f"or after {since}, and a workspace whose land_verify_commands is armed."
    )
    return _ForgeryUnderTest(
        session=db_session,
        ticket=ticket,
        arguments={
            "run_id": run.id,
            "kind": ArtifactKind.LAND_VERIFICATION.value,
            "title": f"Land verification — passed — {ticket.landed_branch}",
            "content_json": json.dumps(_FORGED_RECEIPT),
        },
    )


def test_a_forged_passing_land_verification_receipt_is_refused(
    forged_land_verification_receipt: _ForgeryUnderTest,
) -> None:
    """AC1. The agent being judged cannot write the record.

    The consequence is asserted FIRST, and the refusal's shape after it, because
    the consequence is the defect: the baseline inverted exactly this line, from
    the detector reporting the landing to the detector reporting nothing. The
    refusal is then pinned on top, because the three assertions are satisfiable
    independently — a refusal that still writes the row leaves the detector
    silenced, and a detector that keeps reporting while the row is written is a
    reader hardened against one forgery shape rather than a write the platform
    refuses.

    The call is captured rather than wrapped in `pytest.raises` for that
    ordering alone: the refusal is asserted below, so nothing here is swallowed.
    """
    under_test = forged_land_verification_receipt
    session, ticket = under_test.session, under_test.ticket

    refusal: ValueError | None = None
    try:
        run_operation(
            session,
            OperationId.ARTIFACT_ATTACH,
            under_test.arguments,
            Caller(transport=Transport.MCP),
        )
    except ValueError as exc:  # asserted below, never discarded
        refusal = exc

    assert {v.subject_id for v in LAND_VERIFICATION_RAN.detect(session)} == {ticket.id}, (
        "the forged receipt silenced the detector on the landing it was judging"
    )
    receipts = session.exec(
        select(Artifact).where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == ArtifactKind.LAND_VERIFICATION,
        )
    ).all()
    assert receipts == [], f"the forgery wrote {len(receipts)} receipt row(s)"

    assert refusal is not None, "the forged receipt was accepted without a refusal"
    message = str(refusal)
    assert ArtifactKind.LAND_VERIFICATION.value in message, message
    assert "loregarden.services.land_verification.record_land_verification" in message, message


# --------------------------------------------------------------------------- #
# AC8. The re-entry detector reads only what it reports on.
#
# `_receipts_no_service_wrote` shipped as
# `select(Artifact).where(Artifact.kind == LAND_VERIFICATION)` — every column of
# every receipt ever written, decided in Python. Measured read-only on the live
# database 2026-11-13 and re-confirmed at the spec stage:
# `violations=0 elapsed=0.338s peak_python_alloc=37.4MB` over 414 receipts and
# 22,255,476 bytes of `content_json` (largest row 1,360,978 bytes), against a
# `reconcile_interval_seconds` of 30.0. The repo's own answer to the same
# question is twenty lines away: `_landed_without_a_verified_suite` selects
# named columns scoped to a candidate id set.
#
# Two decisions these tests pin, both made at the spec stage rather than left
# to the implementer:
#
# - **No `created_at >= SINCE` bound.** A report-only invariant re-reports its
#   pre-`since` backlog on every pass BY DESIGN — that is what report-only is
#   for — so a `created_at` bound would quietly convert "we have no backlog
#   today" into "we can never see one". Today's measured zero is a fact about
#   2026-11-13, not a guarantee about a receipt someone finds next month. What
#   bounds the load instead is the DISCRIMINATOR pushed into SQL, so the rows
#   materialised are the rows reported.
# - **The missing-key test sits under a `json_valid` guard.** SQLite's
#   `json_extract` RAISES on an unreadable body (verified on 3.51.0:
#   `select json_extract('not json','$.a')` → `Error: stepping, malformed
#   JSON`), and `OR` is not a guaranteed short circuit. Unguarded, the one case
#   AC8 explicitly preserves — an unreadable body is a VIOLATION, not an
#   exception — becomes an exception that takes the whole reconcile pass down,
#   which is the failure mode the detector's own docstring says it avoids.
# --------------------------------------------------------------------------- #


class _CountingResult:
    """A real query result that records how many rows it handed over.

    Counted at the terminal call rather than eagerly, so a chained
    `.scalars()` / `.unique()` / `.mappings()` reads the same as a bare
    `.all()` and records once. `first()` records one row even though the
    statement may have matched many — the detector does not use it, and a
    rewrite that does is reading one row by definition.
    """

    def __init__(self, result, record: Callable[[int], None]):
        self._result = result
        self._record = record

    def all(self) -> list:
        rows = list(self._result.all())
        self._record(len(rows))
        return rows

    def fetchall(self) -> list:
        return self.all()

    def first(self):
        row = self._result.first()
        self._record(0 if row is None else 1)
        return row

    def one(self):
        row = self._result.one()
        self._record(1)
        return row

    def one_or_none(self):
        row = self._result.one_or_none()
        self._record(0 if row is None else 1)
        return row

    def scalars(self) -> _CountingResult:
        return _CountingResult(self._result.scalars(), self._record)

    def unique(self) -> _CountingResult:
        return _CountingResult(self._result.unique(), self._record)

    def mappings(self) -> _CountingResult:
        return _CountingResult(self._result.mappings(), self._record)

    def __iter__(self):
        return iter(self.all())

    def __getattr__(self, name):
        return getattr(self._result, name)


class _CountingConnection:
    """The raw connection, counting the rows a `text()` statement returns.

    A `json_each` or `json_valid` formulation is the kind of SQL someone
    reaches for `text()` to write, and a connection `execute` does not pass
    through the `Session` at all — so without this it would read as zero rows
    and the cost assertion below would pass on an unscoped scan.
    """

    def __init__(self, connection, record: Callable[[int], None]):
        self._connection = connection
        self._record = record

    def execute(self, statement, parameters=None, **kwargs) -> _CountingResult:
        return _CountingResult(
            self._connection.execute(statement, parameters, **kwargs), self._record
        )

    def exec_driver_sql(self, statement, parameters=None, **kwargs) -> _CountingResult:
        return _CountingResult(
            self._connection.exec_driver_sql(statement, parameters, **kwargs), self._record
        )

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _CountingSession:
    """A real `Session` that records how many rows each query returned.

    Rows and not statements, and not the identity map: the question AC8 asks is
    "how much does one reconcile pass read?", and that is answered the same way
    whether the detector selects whole entities or named columns. The identity
    map would answer it wrongly — the fixture's own rows are already persistent,
    so a `select(Artifact)` repopulates them without SQLAlchemy reporting a
    load, and the unscoped query would read as free.

    Every route to a row is counted, not just `Session.exec`, and the cost test
    asserts the list is non-empty. `exec` alone was the hole this ticket's own
    framing predicts: a rewrite reaching for `session.execute(...)`,
    `session.scalars(...)` or `session.connection().execute(text(…))` — all of
    them plausible for the `json_valid` guard AC8 requires — would have been
    measured at zero rows and read exactly like the fix working. Anything not
    wrapped here fails loudly instead, because no counted query at all is an
    assertion failure rather than a pass.

    The pass-through signatures mirror SQLAlchemy's own; `**kwargs` is the
    proxy's job and not a parameter bag of ours.

    `session.get(Ticket, …)` does not go through any of them and is not
    counted; one ticket lookup per REPORTED violation is the detector's own
    shape, not its scan.
    """

    def __init__(self, session: Session):
        self._session = session
        self.row_counts: list[int] = []

    def _record(self, rows: int) -> None:
        self.row_counts.append(rows)

    def exec(self, statement, **kwargs) -> _CountingResult:
        return _CountingResult(self._session.exec(statement, **kwargs), self._record)

    def execute(self, statement, parameters=None, **kwargs) -> _CountingResult:
        return _CountingResult(self._session.execute(statement, parameters, **kwargs), self._record)

    def scalars(self, statement, parameters=None, **kwargs) -> _CountingResult:
        return _CountingResult(self._session.scalars(statement, parameters, **kwargs), self._record)

    def scalar(self, statement, parameters=None, **kwargs):
        value = self._session.scalar(statement, parameters, **kwargs)
        self._record(0 if value is None else 1)
        return value

    def query(self, *entities) -> _CountingResult:
        return _CountingResult(self._session.query(*entities), self._record)

    def connection(self, **kwargs) -> _CountingConnection:
        return _CountingConnection(self._session.connection(**kwargs), self._record)

    def __getattr__(self, name):
        return getattr(self._session, name)


def _service_written_body(landing: _Landing) -> dict:
    """The receipt body `record_land_verification` really writes, read off one.

    Taken from the writer rather than hand-copied, for the reason
    `test_the_detectors_required_keys_are_the_ones_the_service_actually_writes`
    gives: a fixture keyed on a copied list stops discriminating the day the
    body changes, and reports nothing while the surface says all is well.
    """
    receipt = record_land_verification(
        landing.session,
        landing.ticket,
        branch=landing.ticket.landed_branch,
        target="main",
        candidate_sha="dec0de" + "0" * 34,
        outcome=_outcome(GateOutcome.PASSED),
    )
    body = json.loads(receipt.content_json)
    landing.session.delete(receipt)
    landing.session.commit()
    return body


def _receipt(landing: _Landing, *, body, run_id=None, created_at=None) -> Artifact:
    """One land-verification receipt row, with `body` stored verbatim.

    `body` may be a dict (serialised the way the writers store it) or a raw
    string, which is how the unreadable-body cases are built: `make_artifact`
    serialises a dict and so cannot express them.
    """
    receipt = make_artifact(
        landing.session,
        ticket_id=landing.ticket.id,
        kind=ArtifactKind.LAND_VERIFICATION,
        title=_FORGED_TITLE,
        content={},
        run_id=run_id,
        created_at=created_at,
    )
    receipt.content_json = json.dumps(body) if isinstance(body, dict) else body
    landing.session.add(receipt)
    landing.session.commit()
    landing.session.refresh(receipt)
    return receipt


#: Bodies no reader can take a `status` out of, each a violation rather than an
#: exception. The first two are what the `json_valid` guard is for; the last
#: three are valid JSON that is not an object, which `json_extract` answers
#: without raising and `json_each` does not.
_UNREADABLE_BODIES = (
    pytest.param("not json at all", id="not-json"),
    pytest.param("", id="empty"),
    pytest.param("[]", id="json-array"),
    pytest.param("null", id="json-null"),
    pytest.param('"passed"', id="json-string"),
    pytest.param("0", id="json-number"),
    pytest.param("true", id="json-boolean"),
)


@pytest.mark.parametrize("body", _UNREADABLE_BODIES)
def test_a_receipt_whose_body_cannot_be_read_is_a_violation_not_an_exception(
    landed_ticket: _Landing, body: str
):
    """The trap AC8 names, pinned as behaviour.

    `run_invariant` is ONE step of `PERIODIC_STEPS`: a detector that raises
    stops reporting for every ticket from then on, while the surface says there
    are no violations — a guard failing that reads exactly like a guard working,
    which is this ticket's own framing one level up.
    """
    session = landed_ticket.session
    receipt = _receipt(landed_ticket, body=body)

    violations = _invariant().detect(session)

    assert {v.subject_id for v in violations} == {receipt.id}, (
        f"a receipt whose content_json is {body!r} was not reported. If this raised instead, "
        "the missing-key test needs a json_valid / json_type(…)='object' guard: SQLite's "
        "json_extract raises `malformed JSON` on an unreadable body and OR does not reliably "
        "short circuit."
    )


def test_a_receipt_written_before_the_invariants_since_is_still_reported(
    landed_ticket: _Landing,
):
    """No `created_at >= LAND_VERIFICATION_RECEIPT_WRITER_SINCE` bound.

    The decision, not an accident of the current query: report-only invariants
    re-report their backlog on every pass because nothing else will ever clear
    it, so a `since` bound in the DETECTOR would silence an old forged receipt
    found later. `since` belongs where it already is — telling backlog from
    re-entry when a violation is reported — not in the scan.
    """
    session = landed_ticket.session
    long_before = datetime(2026, 1, 2, 3, 4, 5)
    assert long_before.date() < LAND_VERIFICATION_RECEIPT_WRITER_SINCE, "fixture"
    receipt = _receipt(
        landed_ticket,
        body=_service_written_body(landed_ticket),
        run_id=landed_ticket.run_id,
        created_at=long_before,
    )

    violations = [v for v in _invariant().detect(session) if v.subject_id == receipt.id]

    assert violations, (
        "a run-stamped receipt created before the invariant's `since` is not reported, so the "
        "scan is bounded by created_at. A report-only invariant's backlog is exactly what it "
        "exists to keep reporting; today's measured zero is a fact about today."
    )
    assert violations[0].entered_at == receipt.created_at, (
        "the violation lost the row's created_at, so `since` can no longer tell this backlog "
        f"row from a re-entry: {violations[0].entered_at!r}"
    )


def test_the_violation_still_names_the_ticket_and_the_receipt_id(landed_ticket: _Landing):
    """Preserved explicitly, because a column-selecting rewrite is where a
    `detail` built from a whole entity quietly loses a field."""
    session = landed_ticket.session
    receipt = _receipt(
        landed_ticket, body=_service_written_body(landed_ticket), run_id=landed_ticket.run_id
    )

    violation = next(v for v in _invariant().detect(session) if v.subject_id == receipt.id)

    assert landed_ticket.ticket.external_id in violation.detail, violation.detail
    assert receipt.id in violation.detail, violation.detail
    assert violation.ticket_id == landed_ticket.ticket.id


def test_the_detector_does_not_materialise_every_receipt(landed_ticket: _Landing):
    """The cost criterion itself: only violating rows are read.

    Measured against a fixture holding receipts both inside and outside the
    detector's scope — 30 the service wrote and 2 it did not — and counted at
    the rows each query returns, so an implementation that selects named
    columns and one that pushes the discriminators into SQL are judged by the
    same number. The shipped query reads all 32; the bound here is 8, loose
    enough for a `json_each` shape that returns more than one row per candidate
    and far below the whole column.
    """
    session = landed_ticket.session
    body = _service_written_body(landed_ticket)
    clean = [_receipt(landed_ticket, body=body) for _ in range(30)]
    stamped = _receipt(landed_ticket, body=body, run_id=landed_ticket.run_id)
    incomplete = _receipt(landed_ticket, body={k: v for k, v in body.items() if k != "outcome"})
    total = len(clean) + 2

    counting = _CountingSession(session)
    violations = _invariant().detect(counting)

    assert {v.subject_id for v in violations} == {stamped.id, incomplete.id}, (
        "the fixture's own answer changed, so the count below measures the wrong thing"
    )
    assert counting.row_counts, (
        "the detector issued no query this instrument counted, so this test asserts nothing — "
        "a zero that reads exactly like the fix working. Either it reached the rows by a route "
        "`_CountingSession` does not wrap, or it stopped querying at all; wrap the route and "
        "re-measure rather than trusting the empty list."
    )
    assert max(counting.row_counts, default=0) < total, (
        f"one query returned {max(counting.row_counts, default=0)} of {total} receipts, so the "
        "detector still reads every receipt ever written and decides in Python. Push both "
        "discriminators into the where clause, or select named columns scoped to a bounded "
        "set, the way invariant_registry._landed_without_a_verified_suite already does for "
        "this same kind and column."
    )
    assert sum(counting.row_counts) <= 8, (
        f"the detector read {sum(counting.row_counts)} rows in total "
        f"({counting.row_counts}) to report {len(violations)} violations over {total} receipts"
    )


def test_the_invariant_is_still_scheduled_once_in_the_reconcile_pass():
    """A faster detector that nothing runs reports nothing.

    `reconciliation._invariant` names its step after the invariant, so the step
    list is readable as the schedule.
    """
    named = [step for step in PERIODIC_STEPS if step.name == INVARIANT_NAME]

    assert len(named) == 1, (
        f"{INVARIANT_NAME} is scheduled {len(named)} times in PERIODIC_STEPS: "
        f"{[s.name for s in PERIODIC_STEPS]}"
    )


# --------------------------------------------------------------------------- #
# AC8, broken. Four ways the permitted rewrites pass the criterion's own tests
# while losing something the criterion says is unchanged.
# --------------------------------------------------------------------------- #


def test_the_cost_instrument_counts_every_route_to_a_row(landed_ticket: _Landing):
    """The instrument, proven before its number is trusted.

    `test_the_detector_does_not_materialise_every_receipt` is an assertion about
    a count, so it is only as good as the count — and the count is the thing
    this ticket keeps meeting: an instrument that measures nothing reads exactly
    like a detector that reads nothing. AC8 permits a `json_valid` /
    `json_extract` / `json_each` formulation, which is the SQL somebody writes
    through `session.execute` or `text()` rather than `Session.exec`, so each of
    those routes is counted here against a known number of rows. If a later
    rewrite takes a route this fails on, the fix is to wrap it — never to
    believe the zero.
    """
    session = landed_ticket.session
    body = _service_written_body(landed_ticket)
    for _ in range(3):
        _receipt(landed_ticket, body=body)
    statement = select(Artifact).where(Artifact.kind == ArtifactKind.LAND_VERIFICATION)

    counting = _CountingSession(session)
    assert len(counting.exec(statement).all()) == 3
    assert len(counting.execute(statement).all()) == 3
    assert len(counting.scalars(statement).all()) == 3
    assert len(list(counting.exec(statement))) == 3
    assert (
        len(
            counting.exec(
                select(Artifact.id, Artifact.run_id).where(
                    Artifact.kind == ArtifactKind.LAND_VERIFICATION
                )
            ).all()
        )
        == 3
    )
    assert (
        len(
            counting.connection()
            .execute(
                text("select id from artifacts where kind = :kind"),
                {"kind": ArtifactKind.LAND_VERIFICATION.value},
            )
            .all()
        )
        == 3
    )

    assert counting.row_counts == [3, 3, 3, 3, 3, 3], (
        "a route to a row went uncounted, so the cost assertion can pass on a query that reads "
        f"the whole column: {counting.row_counts}"
    )


def test_the_detector_reports_nothing_about_a_kind_it_does_not_own(landed_ticket: _Landing):
    """The `kind` filter survives being pushed into SQL beside the discriminators.

    The rewrite AC8 asks for moves `kind == LAND_VERIFICATION` into a `where`
    clause that now also carries `run_id` and a `json_valid`-guarded key test,
    and scopes the select to "a bounded set" — which is exactly the edit that
    drops or loosens one of the three predicates. Every row here carries BOTH
    discriminators (a `run_id`, and a body with none of the required keys), so a
    detector that lost the kind filter reports all of them and this fails;
    `log` is the open kind every stage attaches, so the blast radius is the
    whole artifacts table.

    The unreadable body on a kind this detector does not own is the second half:
    an unguarded `json_extract` over a scan the kind filter no longer bounds
    raises on it, and `run_invariant` reports nothing for every ticket from then
    on while the surface says there are no violations.
    """
    session = landed_ticket.session
    for kind in (
        ArtifactKind.EVIDENCE,
        ArtifactKind.HANDOFF,
        ArtifactKind.FIX_COVERAGE,
        ArtifactKind.UX_ANSWERS,
        ArtifactKind.LOG,
        ArtifactKind.CONTEXT,
        UnresolvedArtifactKind.OPEN,
    ):
        make_artifact(
            session,
            ticket_id=landed_ticket.ticket.id,
            kind=kind,
            title=_FORGED_TITLE,
            content={},
            run_id=landed_ticket.run_id,
        )
    unreadable = make_artifact(
        session,
        ticket_id=landed_ticket.ticket.id,
        kind=ArtifactKind.LOG,
        title=_FORGED_TITLE,
        content={},
        run_id=landed_ticket.run_id,
    )
    unreadable.content_json = "not json at all"
    session.add(unreadable)
    session.commit()

    violations = _invariant().detect(session)

    assert violations == [], (
        "the detector reported a row whose kind is not land_verification: "
        f"{[v.detail for v in violations]}. Its statement is about ONE kind, and the rows it "
        "just claimed were forged receipts are an agent's own legitimate artifacts."
    )


def test_a_receipt_missing_several_keys_is_reported_once_and_names_them_all(
    landed_ticket: _Landing,
):
    """One row, one violation — which `json_each` is the way to lose.

    AC8 permits a formulation that "returns more than one row per candidate",
    and the loose row bound above is written to allow it. A `json_each` join
    that tests the required keys one at a time returns one row per MISSING key,
    and a detector that builds a violation per returned row reports this receipt
    three times: the findings surface gets three entries for one garbage row,
    the re-entry escalation counts three defects, and nothing in the criterion's
    own tests notices because they all compare SETS of `subject_id`.

    The detail naming every missing key is the same assertion from the other
    side: it is what tells a person which half of the body is wrong, and a
    per-key shape can only name one.
    """
    session = landed_ticket.session
    body = _service_written_body(landed_ticket)
    dropped = ("outcome", "branch", "candidate_sha")
    receipt = _receipt(landed_ticket, body={k: v for k, v in body.items() if k not in dropped})

    reported = [v for v in _invariant().detect(session) if v.subject_id == receipt.id]

    assert len(reported) == 1, (
        f"one receipt missing {len(dropped)} keys was reported {len(reported)} times: "
        f"{[v.detail for v in reported]}. A violation is a ROW, not a row times a predicate."
    )
    for key in dropped:
        assert key in reported[0].detail, (
            f"the violation does not name the missing {key!r}, so a person reading it cannot "
            f"tell what is wrong with the body: {reported[0].detail}"
        )


def test_one_unreadable_receipt_does_not_change_the_pass_or_its_repeat(
    landed_ticket: _Landing,
):
    """The malformed row in company, and the pass run twice.

    The criterion's unreadable-body cases each hold ONE receipt, so a guard that
    is wrong in company — marking its neighbours violations, or suppressing
    them — is green there. And a `json_extract` that raises does not only lose
    this pass: the failed statement leaves the session's transaction in a state
    where the next read raises too, so the second call is what tells a handled
    row from a detector that took the connection down with it. Determinism is
    the point: `run_invariant` runs this on a 30-second timer.
    """
    session = landed_ticket.session
    body = _service_written_body(landed_ticket)
    clean = [_receipt(landed_ticket, body=body) for _ in range(3)]
    stamped = _receipt(landed_ticket, body=body, run_id=landed_ticket.run_id)
    incomplete = _receipt(landed_ticket, body={k: v for k, v in body.items() if k != "output"})
    malformed = _receipt(landed_ticket, body="not json at all")
    expected = {stamped.id, incomplete.id, malformed.id}

    first = _invariant().detect(session)
    second = _invariant().detect(session)

    assert {v.subject_id for v in first} == expected, (
        "with a malformed receipt in the same pass the detector reported "
        f"{sorted({v.subject_id for v in first})} rather than the three violating rows; the "
        f"{len(clean)} receipts the service wrote are not violations and must stay unreported"
    )
    assert {v.subject_id for v in second} == expected, (
        "the second pass over the same rows disagreed with the first, so the detector is not "
        "deterministic — most likely a statement raised and left the transaction unusable, "
        "which on the reconcile timer means every later pass reports nothing"
    )
    for violation in first:
        assert violation.entered_at is not None, f"{violation.detail} carries no entered_at"
        assert violation.detail.strip(), violation.subject_id
