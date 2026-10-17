"""Which handoff failures earn the tree sentence, and what the others say instead.

`describe_handoff_failure` composes one lead sentence — "<id>: resolve the
conflict with <target>, or run the stage supervised — the worktree is <path>."
— and until lg-milestone-that-975 both handoff boundaries put it in front of
*every* `ValueError` the renderer raised. Three of those are not about the tree
at all (`Unknown agent`, `Unknown workspace for ticket`, `Workspace repo path
does not exist`), and because the lead sentence is the only part that survives
the toast (`.toast__message` clamps at `max-height: 5.8em`, and an error toast
auto-dismisses after 9s) the tree sentence did not pad those diagnoses, it
**replaced** them. An operator whose stage names an agent the registry lost was
sent to resolve a merge conflict in a worktree.

Two things are pinned here, and they are different claims:

* **Classification** is decided at the *raise site*, by position inside
  `render_stage_prompt`: the three refusals lexically above the tree-preparation
  `try` stay plain `ValueError`s; everything raised from inside that `try`
  becomes `HandoffTreePreparationError`. Nothing at either boundary looks at
  `str(exc)` and nothing calls `isinstance` — `except` is Python's own class
  dispatch. The inverted default (unclassified → `str(exc)`) is the point: it is
  what stops the next raise site added to the renderer from re-creating this
  bug, and it is only true while the positional split holds, which nothing but a
  source test can see.
* **Message quality**, because the bare `str(exc)` of those three sites names no
  ticket and no next action, and the one instance the live database has actually
  produced reads `Unknown agent: ` with nothing after the colon (1 run of 3063,
  measured read-only 2026-10-17). So each non-tree site composes
  `<external_id>: <what failed> — <what to fix, and where>.` inside the same two
  measured limits the tree sentence obeys.

The route edge lives in `test_terminal_handoff_command.py`; the harness edge,
the executor-level texts and the source guards live here. Split because
`test_external_handoff_settlement.py` is at 2384 lines against the organization
gate's `MAX_TEST_FILE_LINES = 2500`.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.agents.executors import cli as cli_module
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.api import ticket_handoff as ticket_handoff_module
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services import external_harness as external_harness_module
from loregarden.services import handoff_tree_status as handoff_tree_status_module
from loregarden.services.external_harness import (
    begin_external_stage,
    start_external_orchestration,
)
from loregarden.services.handoff_tree_status import (
    HandoffTreePreparationError,
    describe_handoff_failure,
)
from loregarden.services.orchestration_profile import ProfileUnreadableError
from loregarden.services.target_branch import TargetBranchError
from sqlmodel import Session, select
from tests.worktree_helpers import cut_ticket_worktree, make_agent_stage_ticket, make_repo

STAGE = "work"

#: The three phrases that only a tree failure may use. Asserted as a set on
#: every non-tree message: the defect is not that the tree sentence is appended,
#: it is that the clamp leaves it as the *whole* diagnosis, so any one of them
#: surviving on a non-tree failure reproduces the bug.
#:
#: Extended by lg-milestone-that-1237, not rewritten: that ticket replaces the
#: one tree lead with four, and this tuple was blind to the two new ones, so
#: `no target branch` on a non-tree failure would have leaked undetected.
FORBIDDEN = (
    "resolve the conflict",
    "its target branch",
    "the worktree is",
    "no target branch",
    "nothing was merged",
)

#: Worst-case *realistic* identifiers, which is what the <=160 prose budget has
#: to hold: the longest external id, stage key, agent id and workspace slug this
#: installation actually carries. Measured, not invented — a budget proved
#: against short fixture ids is not a budget.
LONG_EXTERNAL_ID = "lg-milestone-that-12345xy"  # 25
LONG_STAGE_KEY = "architecture-rev"  # 16
LONG_AGENT_ID = "backend_implementer_v299"  # 24
LONG_SLUG = "loregarden-main1"  # 16

PROSE_LIMIT = 160


def _assert_lengths() -> None:
    assert (len(LONG_EXTERNAL_ID), len(LONG_STAGE_KEY), len(LONG_AGENT_ID), len(LONG_SLUG)) == (
        25,
        16,
        24,
        16,
    )


_assert_lengths()


# --- fixtures -----------------------------------------------------------------


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path / "classification", name="project")


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session, repo) -> Workspace:
    ws = Workspace(slug=f"classify-{uuid4()}", name="Classification", repo_path=str(repo))
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    return ws


@pytest.fixture(name="executor")
def executor_fixture(db_session: Session) -> CliAgentExecutor:
    return CliAgentExecutor(db_session)


def _detached(
    *,
    workspace_id: str,
    external_id: str = "lg-classify-975",
    agent_id: str = "backend_implementer",
    stage_key: str = STAGE,
) -> tuple[AgentRun, Ticket]:
    """A run and a ticket the renderer can refuse before it touches the database.

    Deliberately not persisted. All three refusals sit above every write
    `render_stage_prompt` makes, and the missing-workspace case cannot be
    persisted at all: SQLite foreign keys are enforced on this Engine, so a
    ticket row pointing at a workspace that does not exist is rejected by the
    insert rather than by the renderer.
    """
    ticket = Ticket(external_id=external_id, workspace_id=workspace_id, title="Hand it over")
    run = AgentRun(
        run_code=f"c-{uuid4()}",
        ticket_id=ticket.id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=RunStatus.RUNNING,
    )
    return run, ticket


def _refusal(executor: CliAgentExecutor, run: AgentRun, ticket: Ticket) -> str:
    with pytest.raises(ValueError) as excinfo:
        executor.render_stage_prompt(run, ticket)
    return str(excinfo.value)


def _prose(sentence: str, *machine_identifiers: str) -> str:
    """The sentence with the long machine identifiers removed, as AC12's landed
    test measures it (`test_terminal_handoff_command.py:209`).

    An absolute path is 70-130 characters on its own and a workspace UUID is 36,
    so neither fits inside the budget with the facts the operator acts on. The
    budget bounds the *prose*, and the rule the ordering obeys is that the fact
    the clamp eats must be the one recoverable from the ticket row.
    """
    for identifier in machine_identifiers:
        sentence = sentence.replace(identifier, "")
    return sentence


def _assert_no_tree_sentence(message: str) -> None:
    # Case-insensitively, because lg-milestone-that-1237's no-target lead opens
    # its second sentence with `The worktree is`: a leak that differed from the
    # listed phrase only in one capital would otherwise read as clean.
    lowered = message.lower()
    said = [phrase for phrase in FORBIDDEN if phrase in lowered]
    assert said == [], (
        f"a failure that is not about the tree is reported as one ({said}): {message!r} — "
        "the toast clamp means the lead sentence is the whole diagnosis, not a preface to it"
    )


# --- AC2: the tree sentence is earned by a class, not decided at the boundary --


def test_the_tree_sentence_has_one_input_type_and_it_is_a_valueerror():
    """AC2. The class is the classification, and its base is load-bearing.

    `ValueError` and not a fresh hierarchy: `external_harness.py:395,544,578`
    each catch `ValueError` around `_checked_out_run_view` and settle the run
    they stamped on it. A base those three do not catch strands external runs
    RUNNING forever — nothing else ever settles one (lg-milestone-that-974 AC12,
    and the open lg-milestone-that-977).
    """
    assert issubclass(HandoffTreePreparationError, ValueError), (
        "a tree-preparation failure that is not a ValueError is not caught by the three "
        "settling call sites, and the run it stamped stays RUNNING with no agent behind it"
    )
    annotation = inspect.signature(describe_handoff_failure).parameters["exc"].annotation
    assert annotation in (HandoffTreePreparationError, "HandoffTreePreparationError"), (
        f"describe_handoff_failure still takes {annotation!r}: the narrowed parameter is "
        "what says, in the signature, that this composer has exactly one legitimate input"
    )


# `DESCRIBE_BODY_SHA256` stood here — a sha256 of `describe_handoff_failure`'s
# body, asserted byte-identical because lg-milestone-that-975's AC2 narrows that
# function's parameter type and nothing else. It is retired by
# lg-milestone-that-1237, under exactly the condition the assertion below named:
# "if an edit here is genuinely wanted, it is a change to lg-milestone-that-917's
# AC12 and needs its own criterion". 1237's AC1-AC5 are that criterion — they
# replace the one lead with four — so the tripwire has done its job and goes with
# the premise it was protecting.
#
# Nothing takes its place as a hash. A hash can only be stamped from an
# implementation that already exists, so a test designer writing one would be
# pinning bytes no criterion states; and a body that four acceptance criteria
# describe does not need a hash to notice an edit. The *structural* half of the
# test is kept and re-pointed at 1237's shape, which is the durable part of what
# it was guarding: the reads and the composition that stay, and the new per-arm
# leads that must both be reachable.


def _body_after_docstring(func) -> str:
    source = inspect.getsource(func)
    tree = ast.parse(textwrap.dedent(source))
    func_def = tree.body[0]
    first = func_def.body[1]  # the statement after the docstring
    lines = textwrap.dedent(source).splitlines()
    return "\n".join(lines[first.lineno - 1 : func_def.end_lineno]) + "\n"


def _with_literals_joined(source: str) -> str:
    """`source` with implicit string-literal concatenation collapsed.

    A lead sentence is longer than the 100-character line this repo formats to,
    so it reaches the file as two adjacent literals with a newline and an indent
    between them — and *where* the formatter breaks it is a function of the
    indentation it happens to sit at. Searching the raw source for the sentence
    therefore fails on a correct composer that is merely wrapped one word
    differently, and passes only the one shape the test author happened to
    write. The fragment this test is for is the sentence, not its wrapping.
    """
    return re.sub(r'"\s*\n\s*f?"', "", source)


def test_the_composer_keeps_the_parts_no_criterion_changed():
    """What survives lg-milestone-that-1237 in `describe_handoff_failure`'s body.

    This was `test_the_composer_itself_is_not_touched`, a sha256 tripwire over
    the whole body. 1237 edits that body on purpose, so the hash goes (see the
    note above the deleted constant). What is asserted instead is the half the
    hash was really standing in for: three landed facts the ticket does not
    touch, and the two new per-arm leads that have to be reachable at all.

    The two reads are the ones a rewrite of this function is most likely to
    "tidy" into a second resolution — `resolve_ticket_root` above the `try`
    (974) and the composition that carries the caught exception's own text
    behind the lead (917 AC12, 1237 AC6). Neither has a criterion of its own in
    1237, which is exactly why they are pinned here rather than left to be
    noticed.
    """
    body = _body_after_docstring(describe_handoff_failure)
    # Pinned on the body, because these two are about *this* function's shape:
    # the tree is read once, above the guard, and the caught exception's own
    # text is carried behind whichever lead the guard chose.
    for fragment, why in (
        ("resolve_ticket_root(session, ticket, workspace)", "the best-effort tree read (974)"),
        ('f"{lead} {exc}".strip()', "the cause, behind the lead sentence (1237 AC6)"),
    ):
        assert fragment in body, f"{why} is gone from describe_handoff_failure: {fragment!r}"

    # Pinned on the **module**, not the body: plan D1 permits the two new leads
    # and the WARNING to move into private module-level composers if a size or
    # complexity gate forces the extraction, and a body-scoped assertion would
    # fail a shape the plan allows while proving nothing extra about the one it
    # prefers. What may not happen is the fact disappearing from the module, and
    # the behaviour tests in `test_handoff_tree_status.py` pin which arm reaches
    # which lead.
    # The WARNING is deliberately *not* pinned as a fragment here. It is not
    # body-scopable for the reason above, and `"logger.warning(" in
    # module_source` matches `_unreadable` and `_count_lines` as well, so it
    # would pass with the guard's own record deleted — a source assertion that
    # cannot fail for the reason it names. AC8 is behaviour and is pinned as
    # behaviour: `test_the_profile_arm_logs_what_failed_and_names_no_branch`
    # and the two class-B tests each assert exactly one WARNING per call,
    # naming the external id, the workspace slug and the caught exception.
    module_source = _with_literals_joined(inspect.getsource(handoff_tree_status_module))
    for fragment, why in (
        (
            "no target branch — the workspace's orchestration profile",
            "the profile arm's lead (1237 AC2)",
        ),
        (
            "has no target branch to be brought up to, so nothing was merged",
            "the no-target arm's lead (1237 AC3)",
        ),
    ):
        assert fragment in module_source, f"{why} is gone from handoff_tree_status: {fragment!r}"

    # 1237 AC7: the fallback this function used to reach for is gone from the
    # whole module, docstring and import block included, because no lead may
    # name a branch nobody resolved.
    assert "GitAutomationConfig" not in inspect.getsource(handoff_tree_status_module), (
        "the built-in default branch is still reachable from the module (1237 AC7)"
    )
    # 1237 AC9: still not a second resolve. The function runs *because* a
    # resolution raised.
    assert "resolve_orchestration_profile" not in body, (
        "the composer re-calls the resolver that raised (974 R6, 1214)"
    )


def _handlers(func) -> list[ast.ExceptHandler]:
    """Every `except` arm of `func`, in source order.

    Sorted by line rather than taken from `ast.walk`'s order: the order *is* the
    assertion in AC3, and walk's is breadth-first, which matches source order by
    coincidence rather than by contract.
    """
    source = textwrap.dedent(inspect.getsource(func))
    return sorted(
        (
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ExceptHandler)  # py-org: allow-isinstance - AST node walk
        ),
        key=lambda node: node.lineno,
    )


def _caught_names(caught: ast.expr | None) -> set[str]:
    """The exception names one `except` arm lists, each without its module.

    `subprocess.CalledProcessError` and a directly-imported `CalledProcessError`
    are the same arm; which spelling the module uses is not what any criterion
    is about.
    """
    if caught is None:
        return set()
    listed = caught.elts if isinstance(caught, ast.Tuple) else [caught]  # py-org: allow-isinstance
    return {ast.unparse(node).rsplit(".", 1)[-1] for node in listed}


def _handler_names(func) -> list[set[str]]:
    """The names each `except` arm of `func` catches, in source order."""
    return [_caught_names(handler.type) for handler in _handlers(func)]


def _called_names(func) -> set[str]:
    source = textwrap.dedent(inspect.getsource(func))
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):  # py-org: allow-isinstance - AST node walk
            if isinstance(node.func, ast.Name):  # py-org: allow-isinstance - AST node walk
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):  # py-org: allow-isinstance - AST node
                names.add(node.func.attr)
    return names


@pytest.mark.parametrize(
    "func",
    [
        ticket_handoff_module.build_terminal_handoff_command,
        external_harness_module._checked_out_run_view,
    ],
    ids=["route", "harness"],
)
def test_neither_boundary_decides_what_the_failure_was_about(func):
    """AC2's second half. The boundary dispatches on the class and nothing else.

    `isinstance` and a substring test on `str(exc)` both reproduce the defect in
    a form that passes every behaviour test above: they re-create "the boundary
    guesses what failed" one layer down, and the next raise site added to the
    renderer is misclassified again in silence. `except` is Python's own class
    dispatch and it is the only mechanism either boundary may use.
    """
    called = _called_names(func)
    assert "isinstance" not in called, (
        "the boundary type-switches by hand: classification belongs at the raise site, "
        "carried by the exception class"
    )
    for matcher in ("startswith", "endswith", "match", "search", "find"):
        assert matcher not in called, (
            f"the boundary reads the message text ({matcher}): the defect this ticket "
            "closes is a boundary deciding what a failure was about"
        )
    # `str(exc)` is legitimate as the message passed through unchanged, and only
    # as that. Reading it — comparing it, or testing a substring against it — is
    # the string-matching AC2 forbids, and it is a `Compare` node either way.
    source = textwrap.dedent(inspect.getsource(func))
    comparisons = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare)  # py-org: allow-isinstance - AST node walk
        and "exc" in ast.unparse(node)
    ]
    assert comparisons == [], (
        f"the boundary classifies by inspecting the exception's text: {comparisons}"
    )


# --- AC3, AC4: the classification is positional, and nothing enforces position -


def _tree_preparation_try() -> ast.Try:
    source = textwrap.dedent(inspect.getsource(CliAgentExecutor.render_stage_prompt))
    tries = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Try)  # py-org: allow-isinstance - AST node walk
    ]
    assert len(tries) == 1, (
        f"render_stage_prompt has {len(tries)} try blocks: the classification is 'inside "
        "the tree-preparation try', so a second one makes 'inside' ambiguous and this "
        "whole ticket unreadable from the source"
    )
    return tries[0]


def _raised_name(node: ast.Raise) -> str:
    exc = node.exc
    if exc is None:
        return ""  # a bare `raise`, i.e. re-raise unwrapped
    if isinstance(exc, ast.Call):  # py-org: allow-isinstance - AST node walk
        if isinstance(exc.func, ast.Name):  # py-org: allow-isinstance - AST node walk
            return exc.func.id
    if isinstance(exc, ast.Name):  # py-org: allow-isinstance - AST node walk
        return exc.id
    return "?"


def test_the_three_refusals_above_the_tree_preparation_try_stay_plain_valueerrors():
    """AC4. The positional classification, pinned where position is visible.

    This is the test that keeps the fix fixed. Nothing in Python enforces "above
    the try": a later raise added below it, or the `try` widened upward over the
    agent lookup, silently re-creates the defect, and every behaviour test in
    this module keeps passing because the vectors they drive are the three that
    already exist. Read off the AST, so re-ordering or re-wrapping the names
    cannot change the answer.
    """
    source = textwrap.dedent(inspect.getsource(CliAgentExecutor.render_stage_prompt))
    tree_try = _tree_preparation_try()
    above = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Raise)  # py-org: allow-isinstance - AST node walk
        and node.lineno < tree_try.lineno
    ]
    raised = [_raised_name(node) for node in above]
    assert raised == ["ValueError", "ValueError", "ValueError"], (
        f"the refusals above the tree-preparation try raise {raised}: there are exactly "
        "three (unknown agent, unknown workspace, missing repo path), none of them is "
        "about the tree, and the default for an unclassified failure is str(exc)"
    )
    assert "HandoffTreePreparationError" not in raised, (
        "a refusal that happens before any tree work claims to be a tree failure"
    )


def test_the_tree_preparation_handler_has_three_arms_in_the_order_that_works():
    """AC3. Arm order is load-bearing, and the wrong order is silent.

    `DirtyPrimaryCheckoutError` and `HandoffTreePreparationError` are both
    `ValueError` subclasses. A bare `except ValueError` above either one
    swallows it: the park path loses `.dirty_paths` and its own actionable
    sentence, or every real tree failure loses the tree sentence. Both are
    behaviour tests elsewhere — this reads the order itself, because an
    implementation that happens to pass today's two vectors in the wrong order
    is one new arm away from being wrong.
    """
    arms = _handler_names(CliAgentExecutor.render_stage_prompt)
    assert arms == [
        {"DirtyPrimaryCheckoutError"},
        {"TargetBranchError", "ProfileUnreadableError", "CalledProcessError"},
        {"ValueError"},
    ], (
        f"the tree-preparation handler's arms are {arms}: the dirty-checkout refusal must "
        "be first and unwrapped (it is a ValueError subclass, and the park path is built "
        "on its dirty_paths), the three named tree failures second, and the catch-all "
        "ValueError last so _refresh_onto's conflict keeps its own text"
    )
    source = textwrap.dedent(inspect.getsource(CliAgentExecutor.render_stage_prompt))
    handlers = _handlers(CliAgentExecutor.render_stage_prompt)
    first_raises = [
        _raised_name(node)
        for node in ast.walk(handlers[0])
        if isinstance(node, ast.Raise)  # py-org: allow-isinstance - AST node walk
    ]
    assert first_raises == [""], (
        f"the dirty-checkout arm raises {first_raises} instead of re-raising unwrapped: "
        "re-wrapping loses the dirty-path list the park path is built on"
    )
    for index in (1, 2):
        raises = [
            _raised_name(node)
            for node in ast.walk(handlers[index])
            if isinstance(node, ast.Raise)  # py-org: allow-isinstance - AST node walk
        ]
        assert raises == ["HandoffTreePreparationError"], (
            f"arm {index} raises {raises}: everything raised from inside the "
            "tree-preparation try is what earns the tree sentence"
        )
    assert "Could not prepare the tree for {ticket.external_id}: {exc}" in source, (
        "the wrapped text for the three named tree failures changed: the class is new, "
        "the sentence is not, and lg-milestone-that-1214/-1215 pin it"
    )


# --- AC5: the same distinction on the external-harness edge --------------------


def test_the_external_guard_has_two_arms_and_both_raise_a_plain_valueerror():
    """AC5, read off `_checked_out_run_view`'s own handlers.

    Both arms raise a plain `ValueError` on purpose: the three call sites
    (`external_harness.py:395,544,578`) catch `ValueError` and settle the run
    they stamped. Re-raising the subclass would still be caught — it is a
    `ValueError` — but it would carry a tree-preparation claim out of a function
    whose contract is "the message the harness reads", and the settle writes
    that message to `agent_runs.stderr`.
    """
    arms = _handler_names(external_harness_module._checked_out_run_view)
    assert arms == [
        {"HandoffTreePreparationError"},
        {"ProfileUnreadableError", "ValueError"},
    ], (
        f"the external guard's arms are {arms}: the subclass arm must come first (it is a "
        "ValueError, so a broader arm above it swallows every tree failure), and "
        "ProfileUnreadableError stays named in the second — it is the enumeration "
        "contract from orchestration_profile.py:44-51, not a reachability claim"
    )
    for handler in _handlers(external_harness_module._checked_out_run_view):
        raised = [
            _raised_name(node)
            for node in ast.walk(handler)
            if isinstance(node, ast.Raise)  # py-org: allow-isinstance - AST node walk
        ]
        assert raised == ["ValueError"], (
            f"an arm of the external guard raises {raised}: the three settling call sites "
            "catch ValueError, and a run they do not settle stays RUNNING forever"
        )


@pytest.fixture(name="unknown_agent_ticket")
def unknown_agent_ticket_fixture(db_session: Session, workspace: Workspace) -> Ticket:
    """A ticket parked on a stage naming an agent the registry no longer has.

    The live vector: a Workflow Studio stage keeps its `agent_id` string after
    the agent is renamed or deleted, `start_stage_execution` opens a run for it
    (it validates the stage, not the registry), and the renderer is the first
    thing that looks the agent up.
    """
    return make_agent_stage_ticket(
        db_session,
        workspace,
        external_id=f"lg-classify-agent-{uuid4().hex[:6]}",
        stage_key=STAGE,
        agent_id="an_agent_the_registry_no_longer_has",
    )


def test_the_harness_is_told_which_agent_is_missing_and_not_about_a_tree(
    db_session: Session, workspace: Workspace, unknown_agent_ticket: Ticket, repo
):
    """AC5 and AC1 on the edge where the message is the whole of what arrives.

    The MCP boundary turns this `ValueError` into an `isError` tool result whose
    text is everything the harness gets — there is no run view to click through
    to and no second surface. Before this ticket the harness was told to go and
    resolve a merge conflict in a worktree, for a stage that names an agent
    nobody has.
    """
    cut_ticket_worktree(db_session, workspace, unknown_agent_ticket, repo)
    orch_run = start_external_orchestration(
        db_session, unknown_agent_ticket, harness=ExternalHarness.CLAUDE_CODE
    )

    with pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    message = str(excinfo.value)
    _assert_no_tree_sentence(message)
    assert message.startswith(f"{unknown_agent_ticket.external_id}:"), message
    assert "an_agent_the_registry_no_longer_has" in message, (
        f"the harness cannot tell which agent is missing: {message!r}"
    )
    assert STAGE in message, f"the stage whose slot is wrong is not named: {message!r}"
    assert "Workflow Studio" in message, (
        f"the message names no next action, and the harness has no other surface to look "
        f"at: {message!r}"
    )


def test_a_tree_failure_on_the_same_path_still_carries_the_tree_sentence(
    db_session: Session, workspace: Workspace, repo
):
    """AC5's other half, and the mutant kill for "classify everything as non-tree".

    A test suite that only pins the new behaviour passes against an
    implementation that deleted `describe_handoff_failure`'s call site
    altogether. The tree sentence is still the right answer for a tree failure,
    on this edge as much as on the route.
    """
    ticket = make_agent_stage_ticket(
        db_session, workspace, external_id=f"lg-classify-tree-{uuid4().hex[:6]}", stage_key=STAGE
    )
    cut_ticket_worktree(db_session, workspace, ticket, repo)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)

    with mock.patch(
        "loregarden.agents.executors.cli.resolve_target_branch",
        side_effect=TargetBranchError("the integration branch cannot take its base"),
    ):
        with pytest.raises(ValueError) as excinfo:
            begin_external_stage(db_session, orch_run, stage_key=STAGE)

    message = str(excinfo.value)
    assert message.startswith(f"{ticket.external_id}: resolve the conflict with "), (
        f"a genuine tree-preparation failure lost the sentence it earns: {message!r}"
    )
    assert "the worktree is" in message, message
    assert "Could not prepare the tree for" in message, (
        f"the wrapped cause behind the lead sentence is gone: {message!r}"
    )


# --- AC8, AC9: the four texts, exactly -----------------------------------------


def test_an_unknown_agent_names_the_agent_the_stage_and_the_fix(
    db_session: Session, workspace: Workspace, executor: CliAgentExecutor
):
    """AC8. The sentence, verbatim, because the toast renders it verbatim."""
    run, ticket = _detached(
        workspace_id=workspace.id, agent_id="an_agent_the_registry_no_longer_has"
    )

    message = _refusal(executor, run, ticket)

    assert message == (
        f'{ticket.external_id}: stage "{STAGE}" names agent '
        '"an_agent_the_registry_no_longer_has", not in the agent registry — '
        "pick its agent in Workflow Studio."
    ), message
    _assert_no_tree_sentence(message)


def test_a_stage_that_records_no_agent_does_not_render_a_dangling_colon(
    db_session: Session, workspace: Workspace, executor: CliAgentExecutor
):
    """AC9. The one case production has actually produced.

    Measured read-only on the live database, 2026-10-17: of 3063 `agent_runs`,
    333 failed, 0 carried the tree sentence and exactly 1 carried a non-tree
    handoff message — its whole text was `Unknown agent: `, nothing after the
    colon, because `run.agent_id` was empty. 15 of 61 template stage slots
    declare no agent.

    Driven against the executor rather than the route on purpose:
    `is_agentless_stage` (`studio_routing.py:516`) treats an `agent`-type stage
    with no `agent_id` as agentless, so the route answers `400 "This stage does
    not run a CLI agent"` before the renderer. A blank `agent_id` reaches here
    from a `classify`, `parallel`, `gate` or `verify` slot, which `is_agentless_stage`
    never calls agentless. A route test would be green against a vector that
    cannot reach the code.
    """
    run, ticket = _detached(workspace_id=workspace.id, agent_id="")

    message = _refusal(executor, run, ticket)

    assert message == (
        f'{ticket.external_id}: stage "{STAGE}" records no agent, so there is nothing '
        "to run — pick its agent in Workflow Studio."
    ), message
    assert not message.rstrip().endswith(":"), f"the dangling colon is back: {message!r}"
    assert '""' not in message and "''" not in message, (
        f"the empty agent id is rendered as an empty quoted string, which reads as an "
        f"agent literally named nothing: {message!r}"
    )
    _assert_no_tree_sentence(message)


@pytest.mark.parametrize("agent_id", ["", "   ", "an_agent_the_registry_no_longer_has"])
def test_a_stage_with_no_key_says_this_stage_rather_than_an_empty_name(
    db_session: Session, workspace: Workspace, executor: CliAgentExecutor, agent_id: str
):
    """AC8's last line. `agent_runs.stage_key` defaults to `""` in the column
    (`models/domain/tables.py:358`), and a run opened outside the workflow
    cursor can carry it. `stage ""` names nothing and reads like a bug in the
    message rather than a fact about the stage; both agent arms fall back the
    same way.

    A whitespace-only `agent_id` goes down the "records no agent" arm for the
    same reason the empty one does: `"   "` in quotes is indistinguishable from
    an empty slot on screen.
    """
    run, ticket = _detached(workspace_id=workspace.id, agent_id=agent_id, stage_key="")

    message = _refusal(executor, run, ticket)

    assert "this stage" in message, f"an empty stage key was rendered as a name: {message!r}"
    assert 'stage ""' not in message, message
    assert message.startswith(f"{ticket.external_id}: this stage "), message
    assert "Workflow Studio" in message, message


def test_a_missing_workspace_row_says_what_is_missing_and_which_row(
    db_session: Session, executor: CliAgentExecutor
):
    """AC8. `Unknown workspace for ticket: <ticket id>` named the ticket by its
    UUID — the one identifier an operator cannot act on — and said nothing about
    what to do. The workspace id is the actionable one, and it goes last because
    a 36-character UUID cannot fit inside 160 with the other three facts.
    """
    missing_workspace_id = str(uuid4())
    run, ticket = _detached(workspace_id=missing_workspace_id)

    message = _refusal(executor, run, ticket)

    assert message == (
        f"{ticket.external_id}: this ticket's workspace row is missing, so no repo can "
        f"be resolved — re-point the ticket at a workspace "
        f"(workspace_id {missing_workspace_id})."
    ), message
    _assert_no_tree_sentence(message)


def test_a_repo_path_that_does_not_exist_names_the_workspace_and_the_path(
    db_session: Session, tmp_path, executor: CliAgentExecutor
):
    """AC8. The path is the fact the operator pastes, and it is last: the clamp
    eats the tail, and of the four facts the path is the one recoverable from
    workspace settings — which is where the sentence sends them.
    """
    gone = tmp_path / "a-repo-that-was-moved"
    workspace = Workspace(slug=LONG_SLUG, name="Moved", repo_path=str(gone))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    run, ticket = _detached(workspace_id=workspace.id)

    message = _refusal(executor, run, ticket)

    assert message == (
        f"{ticket.external_id}: workspace {workspace.slug}'s repo path does not exist — "
        f"fix it in workspace settings: {gone}."
    ), message
    assert message.endswith(f"{gone}."), f"the absolute path is not last: {message!r}"
    _assert_no_tree_sentence(message)


# --- AC10: the same two measured limits the tree sentence obeys ----------------


def _worst_case_messages(session: Session, tmp_path: Path, executor: CliAgentExecutor) -> dict:
    """All four non-tree texts at worst-case realistic identifiers."""
    gone = tmp_path / "a-repo-that-was-moved"
    workspace = Workspace(slug=LONG_SLUG, name="Worst case", repo_path=str(gone))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)

    def refuse(**kwargs) -> str:
        run, ticket = _detached(external_id=LONG_EXTERNAL_ID, stage_key=LONG_STAGE_KEY, **kwargs)
        return _refusal(executor, run, ticket)

    missing_workspace_id = str(uuid4())
    assert len(missing_workspace_id) == 36, "fixture: a workspace id is a 36-character UUID"
    return {
        "unknown-agent": (
            refuse(workspace_id=workspace.id, agent_id=LONG_AGENT_ID),
            (),
        ),
        "no-agent": (refuse(workspace_id=workspace.id, agent_id=""), ()),
        "missing-workspace": (
            refuse(workspace_id=missing_workspace_id),
            (missing_workspace_id,),
        ),
        "missing-repo-path": (refuse(workspace_id=workspace.id), (str(gone),)),
    }


def test_every_non_tree_sentence_fits_the_toast_it_lands_in(
    db_session: Session, tmp_path, executor: CliAgentExecutor
):
    """AC10. The same clamp, the same 9s dismissal, so the same budget.

    `.toast__message` clamps at `max-height: 5.8em` (about 188 characters at
    1600px, measured) and an error toast auto-dismisses after 9s, which is *why*
    lg-milestone-that-917's AC12 asked for one sentence. A non-tree failure
    lands in exactly that toast, so a 300-character diagnosis is as unreadable
    as the wrong one was — it just fails honestly.

    Measured the way the landed AC12 test measures it: prose, with the long
    machine identifiers (the absolute path, the 36-character workspace UUID)
    removed. Those do not fit inside 160 with the facts in any order, so they go
    last, where the clipped fact is the recoverable one.
    """
    for name, (message, machine) in _worst_case_messages(db_session, tmp_path, executor).items():
        prose = _prose(message, *machine)
        assert len(prose) <= PROSE_LIMIT, f"{name}: {len(prose)} prose characters: {prose}"
        assert message.startswith(f"{LONG_EXTERNAL_ID}:"), (
            f"{name}: the ticket is not the first fact, so the clamp can eat it: {message!r}"
        )
        assert message.count(". ") == 0 and message.endswith("."), (
            f"{name}: more than one sentence reaches the toast, and only the first "
            f"survives the clamp: {message!r}"
        )
        assert "—" in message, (
            f"{name}: the sentence does not use the em dash the tree sentence uses, so "
            f"the two surfaces read as two different voices: {message!r}"
        )
        _assert_no_tree_sentence(message)


def test_the_machine_identifiers_are_last_where_the_clamp_can_eat_them(
    db_session: Session, tmp_path, executor: CliAgentExecutor
):
    """AC10's ordering rule, which is the half a length check cannot see.

    A sentence under the cap can still put the path in the middle and push the
    action off the end. The rule the tree sentence obeys: the fact the clamp
    eats must be the one recoverable from somewhere else.
    """
    messages = _worst_case_messages(db_session, tmp_path, executor)
    for name, (message, machine) in messages.items():
        for identifier in machine:
            head, _, tail = message.partition(identifier)
            assert tail.strip(" .)") == "", (
                f"{name}: {len(tail)} characters follow the machine identifier, and the "
                f"clamp eats them before it eats the identifier: {message!r}"
            )
            assert head, f"{name}: the identifier leads the sentence: {message!r}"


# --- AC12: one text, two entry points ------------------------------------------


@pytest.mark.parametrize(
    "agent_id,what",
    [
        ("an_agent_the_registry_no_longer_has", "unknown agent"),
        ("backend_implementer", "missing workspace"),
    ],
)
def test_both_handoff_entry_points_say_the_same_thing(
    db_session: Session, workspace: Workspace, executor: CliAgentExecutor, agent_id, what
):
    """AC12. Two copies of an f-string drift; they have drifted here before.

    `render_stage_prompt` and `prepare_terminal_handoff` each refuse an unknown
    agent and a missing workspace, and the operator cannot tell which entry
    point answered them — the external harness reaches the first, the terminal
    route the second, and both land in the same places (a toast, and the failed
    run's `stderr`). A difference between them is a difference nobody can
    explain and nothing else would catch.
    """
    workspace_id = workspace.id if what == "unknown agent" else str(uuid4())
    run, ticket = _detached(workspace_id=workspace_id, agent_id=agent_id)

    with pytest.raises(ValueError) as from_render:
        executor.render_stage_prompt(run, ticket)
    with pytest.raises(ValueError) as from_terminal:
        executor.prepare_terminal_handoff(run, ticket)

    assert str(from_render.value) == str(from_terminal.value), (
        f"the two handoff entry points word the same {what} differently:\n"
        f"  render_stage_prompt:      {str(from_render.value)!r}\n"
        f"  prepare_terminal_handoff: {str(from_terminal.value)!r}"
    )


def test_the_two_shared_messages_are_composed_once_at_module_level():
    """AC12's structural half. Equality today does not keep them equal.

    Two identical f-strings pass the test above and drift on the next edit, which
    is how they got here. The composition is a module-level function — module
    level rather than a method because `CliAgentExecutor` is ~935 lines against
    the organization gate's `MAX_CLASS_LINES = 1000`, and two more methods on it
    are two lines of someone else's split.
    """
    module_source = inspect.getsource(cli_module)
    module_level = {
        node.name
        for node in ast.parse(module_source).body
        if isinstance(node, ast.FunctionDef)  # py-org: allow-isinstance - AST node walk
    }
    composers: list[list[str]] = []
    for func in (CliAgentExecutor.render_stage_prompt, CliAgentExecutor.prepare_terminal_handoff):
        source = textwrap.dedent(inspect.getsource(func))
        raises = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Raise)  # py-org: allow-isinstance - AST node walk
            and _raised_name(node) == "ValueError"
        ]
        shared = raises[:2]  # the unknown-agent and missing-workspace refusals
        assert len(shared) == 2, f"{func.__name__} no longer refuses both shared cases"
        called = []
        for node in shared:
            arg = node.exc.args[0]
            assert isinstance(arg, ast.Call), (  # py-org: allow-isinstance - AST node walk
                f"{func.__name__} builds this message inline rather than calling the "
                "shared composer, so the two entry points can drift apart again"
            )
            assert isinstance(arg.func, ast.Name), (  # py-org: allow-isinstance - AST node walk
                f"{func.__name__}'s composer is not a module-level function"
            )
            assert arg.func.id in module_level, (
                f"{arg.func.id} is not defined at module level in cli.py: a method would "
                "push CliAgentExecutor further toward MAX_CLASS_LINES = 1000"
            )
            called.append(arg.func.id)
        composers.append(called)

    assert composers[0] == composers[1], (
        f"the two entry points call different composers {composers}: that is two copies "
        "of the f-string with extra steps"
    )


def test_the_new_class_lives_with_the_composer_it_is_the_input_to():
    """AC2's placement. The class and `describe_handoff_failure` are one contract:
    the only type that earns that sentence, and the function that writes it. A
    class defined in `cli.py` and imported by the boundaries would make the
    executor the owner of the message, which is what the boundaries already got
    wrong.
    """
    assert HandoffTreePreparationError.__module__ == handoff_tree_status_module.__name__, (
        "the exception that earns the tree sentence is defined away from the function "
        f"that composes it: {HandoffTreePreparationError.__module__}"
    )
    assert (HandoffTreePreparationError.__doc__ or "").strip(), (
        "the class carries no docstring: it has to say that it is the single input that "
        "earns the tree sentence, and why its base is ValueError"
    )


# --- the default itself, on a vector that does not exist yet -------------------
#
# Everything above drives one of the three refusals that exist today, so every
# one of them passes against an implementation that special-cases exactly those
# three. The spec's claim is larger and is the reason the class exists at all:
# the default for an *unclassified* failure is `str(exc)`, so the next raise
# site added to the renderer is reported accurately without anyone remembering
# this ticket. A test for that has to raise from somewhere no criterion names,
# which is what these two do — a plain `ValueError` from the prompt build, below
# the tree-preparation `try` and therefore classified by nothing.


FOURTH_REFUSAL = "lg-classify: the skill this stage names was deleted — pick another."


def test_a_refusal_nobody_classified_keeps_its_own_text_on_the_harness_edge(
    db_session: Session, workspace: Workspace, repo
):
    """AC2's inverted default, which is the half that keeps the fix fixed.

    The three refusals this ticket names are the ones that exist; the defect was
    that the boundary spoke for *every* `ValueError` the renderer raised. An
    implementation that enumerates the three and leaves the default alone passes
    every other test in this module and re-creates the bug on the fourth raise
    site somebody adds. So the vector here is deliberately not one of the three:
    a plain `ValueError` from `_build_prompt`, which runs below the
    tree-preparation `try` and is classified by nothing.
    """
    ticket = make_agent_stage_ticket(
        db_session, workspace, external_id=f"lg-classify-4th-{uuid4().hex[:6]}", stage_key=STAGE
    )
    cut_ticket_worktree(db_session, workspace, ticket, repo)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)

    with mock.patch.object(
        CliAgentExecutor, "_build_prompt", side_effect=ValueError(FOURTH_REFUSAL)
    ):
        with pytest.raises(ValueError) as excinfo:
            begin_external_stage(db_session, orch_run, stage_key=STAGE)

    message = str(excinfo.value)
    assert message == FOURTH_REFUSAL, (
        "a failure the renderer raised from outside its tree-preparation try was still "
        f"reported as a tree failure, or re-worded on the way out: {message!r} — the "
        "default for an unclassified failure is its own text, and that default is what "
        "stops the next raise site from re-creating this defect"
    )
    _assert_no_tree_sentence(message)


def test_a_dirty_primary_checkout_keeps_its_own_sentence_on_the_harness_edge(
    db_session: Session, workspace: Workspace, repo
):
    """AC3's arm ordering, on the boundary AC3's own test does not reach.

    `DirtyPrimaryCheckoutError` is a `ValueError` subclass, and so is
    `HandoffTreePreparationError`: three arms in the renderer and two at each
    boundary, five orderings that compile and one that is right. The route has a
    behaviour test for this; the harness edge had none, and it is the edge where
    the message is the whole of what arrives — there is no run view behind it to
    click through to for the file list.

    Raised from `resolve_execution_root`, which is where the live one comes
    from, so the arm that must see it is the arm the real failure reaches.
    """
    from loregarden.models.domain import DirtyCheckoutCause, PrimaryCheckoutUse
    from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError

    ticket = make_agent_stage_ticket(
        db_session, workspace, external_id=f"lg-classify-dirty-{uuid4().hex[:6]}", stage_key=STAGE
    )
    cut_ticket_worktree(db_session, workspace, ticket, repo)
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    refusal = DirtyPrimaryCheckoutError(
        repo_root=Path(repo),
        use=PrimaryCheckoutUse.TICKET_FALLBACK,
        cause=DirtyCheckoutCause.DIRTY,
        dirty_paths=("someone-elses.txt",),
    )

    with mock.patch("loregarden.agents.executors.cli.resolve_execution_root", side_effect=refusal):
        with pytest.raises(ValueError) as excinfo:
            begin_external_stage(db_session, orch_run, stage_key=STAGE)

    message = str(excinfo.value)
    _assert_no_tree_sentence(message)
    assert message == str(refusal), (
        f"the dirty-checkout refusal was re-worded on its way to the harness: {message!r}"
    )
    assert "someone-elses.txt" in message, (
        f"the files in the way are the only actionable fact and they are gone: {message!r}"
    )


def test_the_harness_edge_settles_the_run_it_stamped_for_a_non_tree_refusal(
    db_session: Session, workspace: Workspace, unknown_agent_ticket: Ticket, repo
):
    """AC5 and AC7's second surface, which the message assertions cannot see.

    `_checked_out_run_view` stamps the run RUNNING and commits it one call
    before the renderer refuses, and nothing else ever settles an external run
    (lg-milestone-that-974 AC12, and the open lg-milestone-that-977). The
    settling call site catches `ValueError` — which is *why* the new class
    subclasses it — and writes the detail to `agent_runs.stderr`, the second
    place AC7 says this sentence lands. A non-tree arm that raised a base the
    call site does not catch would pass every text assertion in this module and
    strand the stage live with no agent behind it.
    """
    cut_ticket_worktree(db_session, workspace, unknown_agent_ticket, repo)
    orch_run = start_external_orchestration(
        db_session, unknown_agent_ticket, harness=ExternalHarness.CLAUDE_CODE
    )

    with pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    run = db_session.exec(
        select(AgentRun).where(
            AgentRun.ticket_id == unknown_agent_ticket.id, AgentRun.stage_key == STAGE
        )
    ).one()
    db_session.refresh(run)
    assert run.status == RunStatus.FAILED, (
        f"the run the harness checkout stamped was left {run.status}: nothing else ever "
        "settles an external run, so the stage reads as live forever"
    )
    assert run.stderr == str(excinfo.value), (
        f"the run view shows a different string than the harness was given: {run.stderr!r}"
    )
    _assert_no_tree_sentence(run.stderr)


def test_the_class_graph_every_arm_order_in_this_change_depends_on():
    """AC2 and AC3. The orderings are only correct against this class graph.

    Three handler orderings are load-bearing (the renderer's three arms, and two
    at each boundary), and every one of them is an assertion about subclassing
    rather than about code: `DirtyPrimaryCheckoutError` must be caught before a
    bare `ValueError`, and `HandoffTreePreparationError` before it too. Making
    either one a subclass of the other — a plausible tidy-up, since both are
    handoff refusals carrying a `ValueError` — silently collapses one arm into
    the other and every behaviour test here still passes on today's vectors.
    """
    from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError

    assert not issubclass(DirtyPrimaryCheckoutError, HandoffTreePreparationError), (
        "a dirty shared checkout is not a tree-preparation failure: it is refused before "
        "any tree work, it carries its own dirty-path list, and the park path is built on "
        "it — folding it under the tree sentence is the defect this ticket closes"
    )
    assert not issubclass(HandoffTreePreparationError, DirtyPrimaryCheckoutError), (
        "a tree-preparation failure claims a dirty_paths contract it cannot honour"
    )
    for runtime_error in (TargetBranchError, ProfileUnreadableError):
        assert not issubclass(runtime_error, HandoffTreePreparationError), (
            f"{runtime_error.__name__} became a tree-preparation failure by inheritance, "
            "so the renderer's wrapper no longer composes 'Could not prepare the tree "
            "for <id>' and both boundaries' second arm stops being reachable for it — "
            "orchestration_profile.py:44-51 keeps the RuntimeError base deliberately"
        )
    assert HandoffTreePreparationError.__mro__[1] is ValueError, (
        "HandoffTreePreparationError was given an intermediate base: the three settling "
        "call sites catch ValueError, and every layer between is a place for a future "
        "except arm to catch more than it meant to"
    )


@pytest.mark.parametrize("stage_key", ["", " ", "   ", "\t", "\n", " \t\n "])
@pytest.mark.parametrize("agent_id", ["", "   ", "an_agent_the_registry_no_longer_has"])
def test_a_stage_key_that_is_only_whitespace_is_not_a_stage_name(
    db_session: Session, workspace: Workspace, executor: CliAgentExecutor, stage_key, agent_id
):
    """AC8's fallback is written on `.strip()`, and only `""` was exercised.

    `agent_runs.stage_key` is a free-text column with a `""` default, and the
    rows that carry an unusable one carry it from a copied workflow template or
    a run opened outside the cursor — neither of which guarantees the empty
    string rather than a space or a tab. `stage " "` is worse than `stage ""`,
    not better: it looks like a stage whose name is invisible. Both agent arms
    and the whole whitespace family, as a matrix, because the condition is one
    `.strip()` for all of them and a hand-rolled `== ""` passes the empty case.
    """
    run, ticket = _detached(workspace_id=workspace.id, agent_id=agent_id, stage_key=stage_key)

    message = _refusal(executor, run, ticket)

    assert "this stage" in message, (
        f"a stage key of {stage_key!r} was rendered as the stage's name: {message!r}"
    )
    assert f'stage "{stage_key}"' not in message, message
    assert message.startswith(f"{ticket.external_id}: this stage "), message
    assert "Workflow Studio" in message, message
    _assert_no_tree_sentence(message)


def test_the_repo_path_refusal_also_reads_the_same_through_both_entry_points(
    db_session: Session, tmp_path, executor: CliAgentExecutor
):
    """AC12's rule applied to the third refusal, which shares by delegation.

    `prepare_terminal_handoff` refuses the unknown agent and the missing
    workspace itself and reaches the missing repo path only through
    `render_stage_prompt`, so the two entry points agree here *because* there is
    one raise site. That is a fact about today's structure, not a guarantee: a
    third copy added to `prepare_terminal_handoff` — the same tidy-up that put
    the first two there — would drift exactly as the other two did, and no other
    test in this change would see it.
    """
    gone = tmp_path / "a-repo-that-was-moved"
    workspace = Workspace(slug=LONG_SLUG, name="Moved", repo_path=str(gone))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    run, ticket = _detached(workspace_id=workspace.id)

    with pytest.raises(ValueError) as from_render:
        executor.render_stage_prompt(run, ticket)
    with pytest.raises(ValueError) as from_terminal:
        executor.prepare_terminal_handoff(run, ticket)

    assert str(from_render.value) == str(from_terminal.value), (
        "the two handoff entry points word a missing repo path differently:\n"
        f"  render_stage_prompt:      {str(from_render.value)!r}\n"
        f"  prepare_terminal_handoff: {str(from_terminal.value)!r}"
    )


def test_the_sentences_the_executor_actually_produces_are_not_classified_transient(
    db_session: Session, tmp_path, executor: CliAgentExecutor
):
    """AC13's corpus claim, taken from the executor rather than retyped.

    `test_transient_classifier_corpus.py` holds these four as literals, which is
    the right shape for that module — it is a record of what the column has
    contained. But a literal copy of a sentence stops protecting the sentence
    the moment either one is edited, and the classifier is a substring list that
    has drifted silently four times for exactly that reason. Here the strings
    come from the raise sites, so a reworded sentence is re-asked rather than
    assumed: `settle_failed_handoff` writes each of them to `agent_runs.stderr`,
    and a transient verdict there retries a stage that cannot succeed until a
    person edits the workflow or the workspace.
    """
    from loregarden.services.stage_report import is_transient_failure

    for name, (message, _machine) in _worst_case_messages(db_session, tmp_path, executor).items():
        assert not is_transient_failure("", message), (
            f"{name}: the classifier calls this transient, so the stage is retried "
            f"instead of being handed to the person who can fix it: {message!r}"
        )


@pytest.mark.parametrize("agent_id", [" ", "   ", "\t", "\n", " \t "])
def test_an_agent_id_that_is_only_whitespace_records_no_agent_rather_than_naming_one(
    db_session: Session, workspace: Workspace, executor: CliAgentExecutor, agent_id
):
    """AC9's arm is chosen on `.strip()`, and only `""` picked it.

    `"   "` and `""` are the same slot to the operator — `names agent "   "` is
    the dangling colon with quotes around it, and it sends them looking for an
    agent whose name is three spaces. The condition AC9 states is
    `run.agent_id.strip()`, so the whole whitespace family takes the same arm; a
    hand-rolled `if run.agent_id:` satisfies every other test in this change
    and gets this wrong, which is how the empty-agent message got here.
    """
    run, ticket = _detached(workspace_id=workspace.id, agent_id=agent_id)

    message = _refusal(executor, run, ticket)

    assert message == (
        f'{ticket.external_id}: stage "{STAGE}" records no agent, so there is nothing '
        "to run — pick its agent in Workflow Studio."
    ), (
        f"an agent id of {agent_id!r} was quoted as a name rather than read as an empty "
        f"slot: {message!r}"
    )
    assert "names agent" not in message, message
    _assert_no_tree_sentence(message)
