"""What the handoff boundary is able to say about the tree it hands over.

`describe_handoff_tree` is read *after* the refresh, at the boundary, by the two
surfaces that hand a tree to something this process does not supervise: the
terminal-handoff route and the external-harness checkout. It is not plumbed out
of `ensure_ticket_branch` — two of the three dispatch callers do not want it,
and "this tree is behind its target" is true whatever left it behind.

The question it answers for the operator is the one the old response did not:
"can I paste this and trust the tree it will run in?" So the two answers that
must never collapse into one are "it is current" and "we could not tell".
"""

import inspect
import textwrap
import warnings
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.models.domain import AgentRun, Approval, Ticket, WorkItemType, Workspace
from loregarden.services import handoff_tree_status
from loregarden.services import orchestration_profile as orchestration_profile_module
from loregarden.services.integration_conflict_card import conflict_card_title
from loregarden.services.integration_merge_agent import MergeResolution
from loregarden.services.integration_merge_budget import INTEGRATION_MERGE_STAGE
from loregarden.services.orchestration_profile import (
    GitAutomationConfig,
    ProfileUnreadableError,
    resolve_orchestration_profile,
)
from loregarden.services.target_branch import (
    TargetBranchError,
    integration_branch_for,
    resolve_target_branch,
    target_branch_name,
)
from loregarden.services.ticket_worktree import resolve_ticket_root
from sqlmodel import Session, select
from tests.handoff_lead_helpers import (
    LONGEST_EXTERNAL_ID,
    LONGEST_PROFILE_PATH,
    LONGEST_WORKTREE_PATH,
    NO_TARGET_PROSE_LIMIT,
    PROFILE_PROSE_LIMIT,
    conflict_lead,
    no_target_lead,
    padded_dir,
    profile_lead,
    prose,
)
from tests.worktree_helpers import (
    commit_on,
    cut_ticket_worktree,
    git,
    make_agent_stage_ticket,
    make_repo,
)

#: AC8. `server/pyproject.toml` sets no `[tool.pytest.ini_options]`
#: `filterwarnings` and no `addopts`, and nothing else in the tree sets them, so
#: AC1's `-W error::sqlalchemy.exc.SAWarning` is a flag a human types: pre-push
#: and CI never pass it, and AC1 alone therefore carries no standing protection.
#: This marker is what makes the module's own runs carry it. It is free given
#: AC1 — AC1 already requires that exact filter to report 0 failed here, so the
#: marker cannot make this module stricter than the criterion — and it does
#: **not** substitute for the behavioural assertions below, which name the
#: defect and survive a filter being switched off again.
pytestmark = pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")


def describe(repo: Path, *, target_branch: str = "", ticket_external_id: str = ""):
    """`describe_handoff_tree`, over the one value a handoff boundary hands it.

    AC6 (lg-milestone-that-974): the tree and the branch it was brought up to
    arrive as a single `PreparedTree`, because they are two readings of one
    resolution and a signature that takes them separately is what let a caller
    mix sources. Every behaviour test below is unchanged — only how the pair
    reaches the function is.

    Imported at call time rather than at module scope: `PreparedTree` does not
    exist yet, and a module-level import of a missing name is a *collection*
    error, which aborts the session rather than failing these tests on their
    own.
    """
    from loregarden.services.handoff_tree_status import PreparedTree, describe_handoff_tree

    return describe_handoff_tree(
        PreparedTree(repo_root=repo, target_branch=target_branch),
        ticket_external_id=ticket_external_id,
    )


TICKET = "lg-milestone-that-917"
TARGET = "integration/LG-MS"


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    """A repo on `main`, with a target branch that has nothing extra on it yet.

    Built in a fixture rather than in the test bodies: a broken fixture is an
    ERROR, where the same breakage inside a test body reads as a real finding
    about the code under test.
    """
    repo = make_repo(tmp_path)
    git(repo, "branch", TARGET)
    return repo


def _ahead(repo: Path, commits: int) -> None:
    """Put `commits` commits on the target branch that the checkout lacks."""
    for n in range(commits):
        commit_on(repo, TARGET, f"landed-{n}.txt", f"a sibling's work {n}\n")


def _dirty(repo: Path, files: int) -> None:
    for n in range(files):
        (repo / f"in-flight-{n}.txt").write_text("an operator is still writing this\n")


# --- the failure sentence -----------------------------------------------------


@pytest.fixture(name="failed_handoff_ticket")
def failed_handoff_ticket_fixture(db_session: Session, repo: Path) -> Ticket:
    """A ticket on a real workspace, so `describe_handoff_failure` resolves a
    tree and a target the way the route does rather than against a stub."""
    workspace = Workspace(
        slug=f"handoff-failure-{uuid4()}", name="Handoff failure", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    return make_agent_stage_ticket(db_session, workspace, external_id=TICKET, stage_key="work")


def test_a_target_that_cannot_resolve_still_produces_the_sentence_not_a_500(
    db_session: Session, failed_handoff_ticket: Ticket, caplog
):
    """AC1(B), AC3, AC8. The worst case: the thing that failed *is* the target.

    `describe_handoff_failure` runs on a path where the tree or the target may
    be exactly what broke, so it still has to compose a sentence rather than let
    the route's intended 400 become an unhandled 500. If anything escapes this
    handler the ticket id and the next step both vanish at the moment the
    operator needs them most.

    What the sentence *says* is what lg-milestone-that-1237 changes. It named
    `GitAutomationConfig().base_branch` — a stand-in nobody resolved — and told
    the operator to resolve a conflict with it, on an input where no merge was
    attempted and no conflict exists. Input class B gets the no-target lead
    instead: the honest state, and the one action executable from where the
    operator is standing.

    `TargetBranchError` is raised here through a patched `target_branch_name`
    because this test is about the composition; AC17(B) pins the same arm
    without a mock, on a workspace-bound ticket sitting directly under an
    initiative.
    """
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")
    with (
        mock.patch.object(
            handoff_tree_status,
            "target_branch_name",
            side_effect=TargetBranchError("integration/LG-MS does not exist"),
        ),
        caplog.at_level("WARNING"),
    ):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, failed_handoff_ticket, conflict
        )

    workspace = db_session.get(Workspace, failed_handoff_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, failed_handoff_ticket, workspace)
    # AC13: built from the identifiers, not parsed out of the detail. This lead
    # is two sentences, so `_lead()` would hand back `… run the stage
    # supervised.` with the worktree clause cut off — a string the operator
    # never sees, against which every assertion below would pass while the real
    # lead was wrong.
    lead = no_target_lead(TICKET, repo_root)

    assert detail.startswith(lead), f"expected the no-target lead:\n{lead!r}\ngot\n{detail!r}"
    # AC10, measured with the single absolute path this lead names removed.
    measurable = prose(lead, exempt=repo_root)
    assert len(measurable) <= NO_TARGET_PROSE_LIMIT, f"{len(measurable)}: {lead}"
    # AC12/AC14: the old lead's verb was wrong for this input and so was its
    # branch. Both absences are measured on the subtracted lead, because a
    # workspace repo path may contain a branch name as a substring.
    assert "conflict" not in measurable.lower(), (
        f"the lead still tells the operator to resolve a conflict that does not exist: {lead}"
    )
    assert GitAutomationConfig().base_branch not in measurable, (
        f"the lead names a branch nobody resolved: {lead}"
    )
    assert "supervised" in lead, f"the next step is missing: {lead}"
    # AC6: the rule that refused still follows the lead, unchanged.
    assert str(conflict) in detail, "the full failure text is still carried after the lead"
    # AC8: the resolve failure stays observable, and the record says what failed
    # rather than naming a branch.
    records = _handler_warnings(caplog)
    assert len(records) == 1, (
        f"expected exactly one WARNING from the guard, got {[r.getMessage() for r in records]}"
    )
    assert "integration/LG-MS does not exist" in records[0].getMessage(), (
        f"a target that could not resolve was swallowed silently: {records[0].getMessage()}"
    )


# --- lg-milestone-that-1214: the guard's other arm ----------------------------
#
# `target_branch_name` reads the workspace's base branch — one accessor,
# `git_automation_config.base_branch`, since lg-milestone-that-1419, and the
# profile resolver inline before that —
# on its first line (target_branch.py:144), and that call raises pydantic's
# `ValidationError` — a **`ValueError`** subclass, not a `TargetBranchError`,
# because `TargetBranchError` subclasses `RuntimeError` (target_branch.py:61) —
# whenever the workspace's orchestration profile YAML is malformed. So the
# narrow guard above missed it, *and* its fallback re-called the very function
# that had just raised. On the terminal-handoff route that is a 500 whose body
# is `{"detail": "Internal Server Error"}`, with `settle_failed_handoff` on the
# next line never reached: the run stays RUNNING with no agent behind it.
#
# Both arms are therefore required (`except (TargetBranchError, ValueError)`),
# and the in-handler fallback has to be one that cannot itself raise.


@pytest.fixture(name="malformed_profile_ticket")
def malformed_profile_ticket_fixture(
    db_session: Session, repo: Path, tmp_path: Path, monkeypatch
) -> Ticket:
    """A ticket whose workspace resolves a **genuinely malformed** profile (V1).

    No mocking: the file on disk is the defect. `monitor.autofix` names
    `stage_thrash`, which is not in `AUTO_FIXABLE_CONDITIONS`, so
    `MonitorConfig._only_auto_fixable` raises and `OrchestrationProfile.model_validate`
    turns it into a `ValidationError` (orchestration_profile.py:181-190, 294).

    AC10 — the malformed file must be the one that actually resolves.
    `resolve_orchestration_profile` takes the **first existing** candidate:
    `<orchestration_profile_slug>.yaml`, then `<workspace.slug>.yaml`, then
    `default.yaml` (orchestration_profile.py:307-319). So the workspace's slug
    is a uuid with no file of its own in the per-test directory and
    `orchestration_profile_slug` is left unset, which leaves `default.yaml` the
    only candidate that exists. The fixture then asserts the resolve really does
    raise *before* handing the subject over: a `default.yaml` written for a
    workspace that resolves something else exercises nothing while passing
    green, and that is the one way this whole vector can lie.

    `lru_cache` on `_parse_profile` caches successful parses only, so there is
    no cache to clear between calls.
    """
    orchestration = tmp_path / "orchestration-malformed"
    orchestration.mkdir()
    (orchestration / "default.yaml").write_text(
        "slug: default\nname: Default\nmonitor:\n  autofix: [stage_thrash]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestration_profile_module, "orchestration_dir", lambda: orchestration)

    workspace = Workspace(
        slug=f"malformed-profile-{uuid4()}", name="Malformed profile", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    assert not workspace.orchestration_profile_slug, (
        "fixture: a profile slug would resolve a different candidate than default.yaml"
    )
    # Migrated with lg-milestone-that-1215: the load seam now classifies every
    # unreadable shape as `ProfileUnreadableError`, so the schema-invalid file
    # this fixture writes no longer resolves as a bare `ValidationError` either.
    # The assertion is kept rather than loosened — it is what proves `default.yaml`
    # is the candidate that resolved — and the validator's own text still has to
    # be carried through the wrap, because that text is the only thing naming the
    # field that is wrong.
    with pytest.raises(ProfileUnreadableError) as excinfo:
        resolve_orchestration_profile(workspace)
    assert "Conditions cannot be auto-fixed" in str(excinfo.value), (
        f"fixture: default.yaml is not the file that resolved — {excinfo.value}"
    )
    return make_agent_stage_ticket(db_session, workspace, external_id=TICKET, stage_key="work")


def _lead(detail: str) -> str:
    """The detail up to its first `". "`, which is the lead for **class D only**.

    lg-milestone-that-1237 AC13: two of the four leads are two sentences, so
    this returns them with their actionable half (`Fix {path}.`, `The worktree
    is {repo_root}.`) cut off — a string the operator never sees. Left here
    deliberately rather than made multi-sentence-aware, because the class-D lead
    (AC5) is genuinely one sentence and its tests must stay unedited; every
    guard-arm assertion builds its expected lead instead, with
    `profile_lead`/`no_target_lead`, and checks `detail.startswith(expected)`.
    `test_the_single_sentence_split_cuts_both_new_leads_in_half` pins the trap so
    nobody rediscovers it by writing a green test against the wrong string.
    """
    head, _, _ = detail.partition(". ")
    return head if head == detail else f"{head}."


def _handler_body() -> str:
    """`describe_handoff_failure`'s code, with its prose removed.

    The docstring is checked on its own terms by the AC5 test below, and it has
    to name `resolve_orchestration_profile` to say what the read does *not*
    raise — so a source check that does not separate the two cannot tell a
    remaining call from an accurate sentence about one.
    """
    source = inspect.getsource(handoff_tree_status.describe_handoff_failure)
    docstring = handoff_tree_status.describe_handoff_failure.__doc__ or ""
    return source.replace(docstring, "")


def _without_prose(text: str) -> str:
    """One spelling of a claim, however it was wrapped, quoted or commented."""
    return " ".join(text.replace("`", "").replace("#", "").split())


def _handler_warnings(caplog) -> list:
    return [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and record.name == handoff_tree_status.__name__
    ]


def _the_failure_production_would_carry(session: Session, ticket: Ticket):
    """The `exc` the real call path hands `describe_handoff_failure` for `ticket`.

    `cli.py:558-571` wraps whatever its tree-preparation `try` raised —
    `HandoffTreePreparationError(f"Could not prepare the tree for {id}: {exc}")`
    for the three named classes — so on every input where the *read* below
    fails, that read already failed one frame up and the rule that refused is
    in `exc`'s own text before this function composes anything.

    Which is why the arms that assert "the rule that refused survives" have to
    use this and not a merge conflict. AC6 fixes the composition at
    `f"{lead} {exc}".strip()` and AC3 fixes the lead, so neither carries
    `resolve_exc`: against a hand-written `ValueError("CONFLICT (content): …")`
    the assertion is unsatisfiable by any implementation the criteria allow,
    and the implementer's only way to green is to append the caught exception
    a second time, which AC6 forbids. The conflict text is also impossible
    here on its own terms — an arm with no target branch is an arm where no
    merge was attempted.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    try:
        resolved = target_branch_name(session, ticket, workspace)
    except (TargetBranchError, ProfileUnreadableError, ValueError) as exc:
        return handoff_tree_status.HandoffTreePreparationError(
            f"Could not prepare the tree for {ticket.external_id}: {exc}"
        )
    raise AssertionError(
        f"{ticket.external_id} resolves to {resolved!r}: this is input class D, and "
        "the guard this helper is for never fires"
    )


def _profile_path(session: Session, ticket: Ticket) -> Path:
    """The absolute path `ProfileUnreadableError` carries for `ticket`'s profile.

    Read off the attribute, which is what AC2 requires of the implementation
    too: `ProfileUnreadableError.path` is already absolute and already
    `.resolve()`d (orchestration_profile.py:60-63). A test that parsed the path
    back out of `str(exc)` would agree with a composer that did the same, and
    the point of the attribute is that neither has to.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    with pytest.raises(ProfileUnreadableError) as excinfo:
        resolve_orchestration_profile(workspace)
    return excinfo.value.path


def test_a_malformed_orchestration_profile_still_produces_the_sentence_not_a_500(
    db_session: Session, malformed_profile_ticket: Ticket, caplog
):
    """AC1(A), AC2. The lead sentence, with the profile itself unreadable.

    Input class A names the thing that failed and the file to open, and nothing
    else. It must NOT name `repo_root`: nothing in a worktree fixes malformed
    YAML, and a second absolute path pushes the actionable one past the toast
    clamp — `.toast__message` holds about 188 characters at 1600px and an error
    toast auto-dismisses after 9s.
    """
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")
    profile_path = _profile_path(db_session, malformed_profile_ticket)
    with caplog.at_level("WARNING"):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, malformed_profile_ticket, conflict
        )

    workspace = db_session.get(Workspace, malformed_profile_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, malformed_profile_ticket, workspace)
    lead = profile_lead(TICKET, profile_path)

    assert detail.startswith(lead), f"expected the profile lead:\n{lead!r}\ngot\n{detail!r}"
    measurable = prose(lead, exempt=profile_path)
    assert len(measurable) <= PROFILE_PROSE_LIMIT, f"{len(measurable)}: {lead}"
    assert str(repo_root) not in lead, (
        f"the profile lead names a second absolute path, which pushes the one the "
        f"operator actually has to open past the toast clamp: {lead}"
    )
    assert "conflict" not in measurable.lower(), (
        f"malformed YAML is not a conflict, and nothing was merged: {lead}"
    )
    assert GitAutomationConfig().base_branch not in measurable, (
        f"the lead names a branch nobody resolved: {lead}"
    )
    assert "CONFLICT" not in lead, f"git's merge output reached the lead: {lead}"
    assert str(conflict) in detail, "the full failure text is still carried after the lead"


# `test_a_fallback_branch_is_not_reported_as_a_resolved_one` stood here. Its
# claim was that the guard's WARNING must label the branch it named as a
# stand-in rather than a resolved answer — and under AC7 no arm names a branch
# at all, so there is no stand-in left to label and the claim is not merely
# false, it is unstatable. What it was really protecting is that the resolve
# failure stays **observable** (CLAUDE.md, *No silent failures*) with the caught
# exception's own text in the record: the only text anywhere naming which field
# of which YAML is wrong, because the toast clamp eats everything past the lead.
# That half is AC8 and it is asserted below, on this arm, and again on the
# `TargetBranchError` and plain-`ValueError` arms further down.


def test_the_profile_arm_logs_what_failed_and_names_no_branch(
    db_session: Session, malformed_profile_ticket: Ticket, caplog
):
    """AC8, on the arm where the record is the only place the cause survives.

    Deleting the `GitAutomationConfig()` fallback removes the log's current
    subject — "naming the built-in default branch 'main' instead of one read
    from the workspace's orchestration profile" — and the temptation once the
    subject is gone is to drop the log with it. It must stay: a resolve failure
    on a real path is a real failure.

    Asserted together with the returned sentence, deliberately. The mutation
    that matters here is the one that removes the guard and leaves the logging
    alone, because that is the one a test measuring only the log goes green
    against.
    """
    workspace = db_session.get(Workspace, malformed_profile_ticket.workspace_id)
    profile_path = _profile_path(db_session, malformed_profile_ticket)
    with caplog.at_level("WARNING"):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, malformed_profile_ticket, ValueError("CONFLICT (content): shared.txt")
        )

    assert detail.startswith(profile_lead(TICKET, profile_path)), detail

    records = _handler_warnings(caplog)
    assert len(records) == 1, (
        f"expected exactly one WARNING from the guard, got {[r.getMessage() for r in records]}"
    )
    message = records[0].getMessage()
    assert TICKET in message, message
    assert workspace.slug in message, (
        f"the workspace whose profile is wrong is not named: {message}"
    )
    assert "Conditions cannot be auto-fixed" in message, (
        f"the caught exception's own text — the only text naming which field of which "
        f"YAML is wrong — was dropped: {message}"
    )
    # Same subtraction as AC10 and AC12, for the same reason: the record carries
    # one absolute path, and a four-character branch name hides inside a path.
    assert GitAutomationConfig().base_branch not in message.replace(str(profile_path), ""), (
        f"the record names a branch nobody resolved, which is what this ticket "
        f"removes from the sentence and must not reintroduce in the log: {message}"
    )


def test_the_handler_does_not_resolve_the_profile_it_is_standing_in_for(
    db_session: Session, failed_handoff_ticket: Ticket, caplog
):
    """AC7 and AC9. No second resolve, and no stand-in branch either.

    974/1214's invariant survives this ticket unchanged: the handler must not
    re-call `resolve_orchestration_profile`, the function that just raised. With
    that call patched to raise for **every** caller there is nowhere left for
    the handler to hide one, so this pins the fix rather than its shape.

    What changes is the other half. `assert "GitAutomationConfig" in body` stood
    here, requiring the handler's fallback be the built-in default rather than a
    second resolve. AC7 deletes the fallback outright, so the assertion
    **inverts**: the name must be absent from the whole module, import block and
    docstring included. The invariant it protected — the handler does not
    re-call the resolver that raised — is the `resolve_orchestration_profile`
    pair above, kept verbatim.

    `resolve_ticket_root` runs before the `try` and is deliberately not
    hardened: it reaches only the worktree table and `resolve_workspace_root`
    (ticket_worktree.py:38-50, workspace_paths.py:54), never a profile and never
    a target branch, so this function has exactly one exposure.
    """
    assert not hasattr(handoff_tree_status, "resolve_orchestration_profile"), (
        "the module still imports the resolver the handler is standing in for"
    )
    body = _handler_body()
    assert "resolve_orchestration_profile" not in body, (
        "the handler still re-calls the resolver that raised to get its fallback"
    )
    # AC7, on the whole module rather than the handler body: the import block
    # and the docstring are the other two places the deleted fallback was
    # recorded, and a lead that reaches a branch name through either is the
    # defect with a new spelling. This is `grep -n GitAutomationConfig
    # server/loregarden/services/handoff_tree_status.py` returning nothing.
    module_source = Path(handoff_tree_status.__file__).read_text(encoding="utf-8")
    assert "GitAutomationConfig" not in module_source, (
        "the built-in default branch is still reachable from the module whose leads "
        "may never name a branch nobody resolved"
    )

    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")
    with (
        mock.patch(
            "loregarden.services.target_branch.base_branch",
            side_effect=ValueError(
                "Conditions cannot be auto-fixed: stage_thrash. "
                "Auto-fixable: emptied_group, stale_cursor."
            ),
        ),
        caplog.at_level("WARNING"),
    ):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, failed_handoff_ticket, conflict
        )

    workspace = db_session.get(Workspace, failed_handoff_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, failed_handoff_ticket, workspace)
    # A plain `ValueError` out of the read — the mock raises `ValueError`, not
    # `ProfileUnreadableError` — so this is input class B and the lead is AC3's.
    assert detail.startswith(no_target_lead(TICKET, repo_root)), detail
    assert str(conflict) in detail, "the full failure text is still carried after the lead"
    assert len(_handler_warnings(caplog)) == 1, (
        f"expected exactly one WARNING, got {[r.getMessage() for r in _handler_warnings(caplog)]}"
    )


def test_the_false_premise_is_gone_from_both_places_974_recorded_it():
    """AC5. The read does not raise `TargetBranchError` via the profile
    resolver, and two places said it did.

    lg-milestone-that-974's AC9, the `describe_handoff_failure` docstring, and
    the comment in the `TargetBranchError` test above all carried the same
    sentence. The guard was still right to keep — `subtree_root` ->
    `_refuse_initiative` / `_require_initiative_child` genuinely raises
    `TargetBranchError` — but the false half of the premise is the one that hid
    this gap for a whole ticket: it made a guard that already covered the
    profile look complete.

    So both sources are named in both files, and the claim itself is asserted
    gone rather than merely improved. Checked on text with the backticks,
    comment markers and line wrapping removed, so re-wrapping the same claim
    does not slip past — which is also why this docstring says "via" and not the
    word the check looks for: the file it reads is this one.
    """
    # Built from the resolver's own name rather than written out, so the claim
    # being searched for never appears literally in the file being searched.
    false_premise = f"through {resolve_orchestration_profile.__name__}"
    docstring = _without_prose(handoff_tree_status.describe_handoff_failure.__doc__ or "")
    comment = _without_prose(Path(__file__).read_text(encoding="utf-8"))

    for label, text in (("the docstring", docstring), ("this test module", comment)):
        assert false_premise not in text, (
            f"{label} still claims the read raises TargetBranchError through the "
            "profile resolver, which is the premise that hid this gap"
        )
        assert "subtree_root" in text, (
            f"{label} does not name the source that really does raise TargetBranchError"
        )
        assert "ValueError" in text, (
            f"{label} does not name the profile's own ValueError, the source that "
            "walked through the narrow guard"
        )

    # `assert "GitAutomationConfig" in docstring` stood here, requiring the
    # docstring record that the in-guard fallback was the built-in default
    # rather than a second resolve. AC7 deletes that fallback, so there is
    # nothing left for the docstring to record and the assertion is unstatable.
    # Its real invariant — the handler does not re-call the resolver that raised
    # — is asserted twice above and again in
    # `test_the_handler_does_not_resolve_the_profile_it_is_standing_in_for`,
    # which also now asserts the name is absent from the whole module (AC7).


def test_both_guard_arms_are_named_because_neither_covers_the_other(db_session: Session):
    """AC16's reason, asserted on the class hierarchy rather than the handlers.

    This is the fact that makes all three names necessary rather than tidy:
    narrowing to `ValueError` alone drops the `TargetBranchError` path 974
    deliberately kept, and dropping `ValueError` drops the unnameable
    integration branch AC3 covers. It stays true whichever way the names are
    split across `except` clauses, which is why it is asserted here and the
    split itself is asserted on the AST below.
    """
    assert issubclass(TargetBranchError, RuntimeError)
    assert not issubclass(TargetBranchError, ValueError), (
        "if this ever becomes true one arm collapses into another — and the two "
        "handlers below must be re-read, not left as is"
    )


# ---------------------------------------------------------------------------
# lg-milestone-that-974: the pair crosses the boundary as one value, and the
# error renderer reads the name instead of resolving it.
#
# `render_stage_prompt` resolved the target branch to pass as
# `ensure_ticket_branch(start_point=...)` and then returned only
# `(prompt, repo_root)`, so each of its two handoff callers resolved the same
# answer a second time and the error path a third. `resolve_target_branch` is
# not a read: it creates the integration branch, merges the base into it, can
# dispatch a blocking `integration_merge_agent` turn, and files an
# `integration_conflict` inbox card on conflict (target_branch.py:269-312).
#
# So the pair becomes one noun. A 3-tuple would have preserved a caller's
# ability to take `repo_root` from here and `target_branch` from somewhere
# else, which **is** the defect.
# ---------------------------------------------------------------------------


@pytest.fixture(name="conflicting_target")
def conflicting_target_fixture(db_session: Session, failed_handoff_ticket: Ticket, repo: Path):
    """The worst case for AC10: an integration branch that cannot take its base.

    `describe_handoff_failure` runs *because* a resolution raised, and on this
    tree resolving again re-attempts the merge, can dispatch a merge-agent turn
    synchronously inside a 400 response, and re-files the `integration_conflict`
    card — purely to name a branch in a sentence.

    The fixture only sets the state up. It deliberately does not resolve to
    prove the conflict is real, because resolving is the very write the test
    measures the absence of; the test's closing control does it instead, after
    the measurement.
    """
    workspace = db_session.get(Workspace, failed_handoff_ticket.workspace_id)
    assert workspace is not None
    milestone = Ticket(
        external_id=f"LG-MS-974-{uuid4()}",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    db_session.add(milestone)
    db_session.commit()
    db_session.refresh(milestone)
    failed_handoff_ticket.parent_ticket_id = milestone.id
    db_session.add(failed_handoff_ticket)
    db_session.commit()
    db_session.refresh(failed_handoff_ticket)

    target = target_branch_name(db_session, failed_handoff_ticket, workspace)
    assert target.startswith("integration/"), (
        f"fixture: the ticket must land on an integration branch, not {target!r} — "
        "a ticket that lands on its base resolves no refs and proves nothing here"
    )
    git(repo, "branch", target)
    commit_on(repo, target, "clash.txt", "integration side\n")
    commit_on(repo, "main", "clash.txt", "main side\n")
    return target, milestone


def _refs(repo: Path) -> str:
    return git(repo, "for-each-ref").stdout


def _conflict_cards(session: Session, root: Ticket) -> list[Approval]:
    """The `integration_conflict` inbox cards filed on a tree's root.

    Matched on the title `integration_conflict_card` builds, not on a substring:
    that title is the key the module keeps to one card per branch, so nothing
    else can produce it and a reword cannot make this read zero.
    """
    workspace = session.get(Workspace, root.workspace_id)
    assert workspace is not None
    title = conflict_card_title(
        integration_branch_for(root), resolve_orchestration_profile(workspace).git.base_branch
    )
    return list(
        session.exec(
            select(Approval).where(Approval.ticket_id == root.id, Approval.title == title)
        ).all()
    )


def test_the_failure_sentence_writes_no_ref_and_spends_no_merge_turn(
    db_session: Session, failed_handoff_ticket: Ticket, repo: Path, conflicting_target
):
    """AC10. The error renderer is a renderer.

    It runs after a failed tree preparation, so if the failure *was* an
    integration conflict, resolving the target again re-attempts the merge that
    just failed — and the cost is not theoretical: `resolve_target_branch`
    dispatches `integration_merge_agent` synchronously and files an
    `integration_conflict` inbox card before raising. All of that to put a
    branch name in one sentence.

    Four observations, then one control. The control is what keeps the zeros
    honest: an inert fixture would report the same four zeros as a correct
    implementation, so the test ends by resolving for real and asserting the
    card it refused to file *does* get filed then.
    """
    target, milestone = conflicting_target
    refs_before = _refs(repo)
    resolved: list[str] = []

    def spy(session, ticket, workspace, *, repo_root, **extra):
        resolved.append(str(repo_root))
        return resolve_target_branch(session, ticket, workspace, repo_root=repo_root, **extra)

    with (
        mock.patch("loregarden.services.target_branch.resolve_target_branch", spy),
        mock.patch("loregarden.services.ticket_worktree.resolve_target_branch", spy),
    ):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session,
            failed_handoff_ticket,
            ValueError("CONFLICT (content): Merge conflict in shared.txt"),
        )

    assert not hasattr(handoff_tree_status, "resolve_target_branch"), (
        "the module still binds the write-path resolver; the read is `target_branch_name`"
    )
    assert resolved == [], f"the error renderer resolved its target {len(resolved)} times"
    assert target in detail, f"the branch still has to reach the sentence: {detail}"
    assert _refs(repo) == refs_before, (
        "naming a branch in a sentence wrote a ref: the integration branch was "
        "created, moved, or merged into"
    )
    assert (
        db_session.exec(
            select(AgentRun).where(AgentRun.stage_key == INTEGRATION_MERGE_STAGE)
        ).first()
        is None
    ), "the error renderer dispatched a blocking merge-agent turn inside a 400"
    assert _conflict_cards(db_session, milestone) == [], (
        "the error renderer filed an `integration_conflict` inbox card"
    )

    # The control. Resolving this tree is what the renderer used to do, and on
    # this tree it files the card — so the zeros above are the implementation
    # being right rather than the fixture being inert. The merge turn itself is
    # refused here (and only here) so the control costs no agent run; the
    # measurement above ran under the default automation, where a renderer that
    # still resolved WOULD have started one.
    workspace = db_session.get(Workspace, failed_handoff_ticket.workspace_id)
    with (
        mock.patch(
            "loregarden.services.target_branch.merge_base_by_agent",
            return_value=MergeResolution(False, "refused for the control"),
        ),
        pytest.raises(TargetBranchError),
    ):
        resolve_target_branch(db_session, failed_handoff_ticket, workspace, repo_root=repo)
    assert _conflict_cards(db_session, milestone), (
        "fixture control: resolving this tree files no card either, so the "
        "assertions above would pass against a renderer that still resolves"
    )


# --- lg-milestone-that-1214: what the designed vectors cannot see -------------
#
# Every test above measures the fallback against `GitAutomationConfig().base_branch`
# == "main", and the spec's own assumption (2) is that the *resolved* value is
# "main" too, because no orchestration YAML in the tree sets `git.base_branch`.
# That makes the two spellings of the fallback — the built-in default and a
# second `resolve_orchestration_profile(workspace).git.base_branch` — produce the
# same string in every one of them. AC9 exists precisely because that agreement
# is an accident, so the tests below break it: a workspace whose profile *is*
# readable and names a base branch of its own, which is the one input that can
# tell the fix from the defect's other half.
#
# The second gap they close is the arm the designed suite measures asymmetrically.
# AC9 binds "whenever the guard fires", and the handler has two arms; a fix that
# adds a rich WARNING on the `ValueError` arm and leaves 974's thin one on the
# `TargetBranchError` arm passes every test above while reporting a fallback as a
# resolved value on half its inputs.


CONFIGURED_BASE = "release/configured-base"


@pytest.fixture(name="configured_base_ticket")
def configured_base_ticket_fixture(
    db_session: Session, repo: Path, tmp_path: Path, monkeypatch
) -> Ticket:
    """A ticket whose workspace resolves a **readable** profile with its own base.

    The inverse of `malformed_profile_ticket`: here the profile parses, and the
    only thing that fails is the target lookup. So the two spellings of the
    in-handler fallback disagree — the built-in default is `main`, the resolved
    value is `CONFIGURED_BASE` — and a sentence can finally say which one it
    read.

    Same resolution rule as the malformed fixture (AC10): the slug is a uuid
    with no file of its own in the per-test directory and
    `orchestration_profile_slug` is unset, so `default.yaml` is the only
    candidate that exists. The fixture asserts the resolve really returns that
    base before handing the subject over — a `default.yaml` that resolved to
    something else would leave this test measuring `main` against `main` again,
    which is the exact blindness it was written to remove.
    """
    orchestration = tmp_path / "orchestration-configured-base"
    orchestration.mkdir()
    (orchestration / "default.yaml").write_text(
        f"slug: default\nname: Default\ngit:\n  base_branch: {CONFIGURED_BASE}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestration_profile_module, "orchestration_dir", lambda: orchestration)

    workspace = Workspace(
        slug=f"configured-base-{uuid4()}", name="Configured base", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    assert not workspace.orchestration_profile_slug, (
        "fixture: a profile slug would resolve a candidate other than default.yaml"
    )
    assert resolve_orchestration_profile(workspace).git.base_branch == CONFIGURED_BASE, (
        "fixture: default.yaml is not the file that resolved, so the configured base "
        "and the built-in default are still the same string and this proves nothing"
    )
    assert CONFIGURED_BASE != GitAutomationConfig().base_branch, (
        "fixture: the configured base must differ from the built-in default"
    )
    return make_agent_stage_ticket(db_session, workspace, external_id=TICKET, stage_key="work")


def test_no_branch_the_read_would_have_returned_reaches_the_lead(
    db_session: Session, configured_base_ticket: Ticket, caplog
):
    """AC12, on the one input where a branch name is distinguishable at all.

    `test_the_fallback_is_the_built_in_default_and_not_the_configured_base`
    stood here. Its claim was that the branch in the sentence is the built-in
    default and not a second resolve of the workspace's profile; AC7 deletes the
    fallback, so no lead names a branch and "which of the two is it" has no
    answer. The input it was built for is still the only one that can tell a
    branch name from a coincidence, though: against every other fixture in this
    module both spellings read `main`, which is four characters long and turns
    up inside paths. Here the profile parses and names `CONFIGURED_BASE`, so
    "no branch name in the lead" is finally decidable.

    Three checks, which is AC12's definition of "no branch name": the built-in
    default absent, the literal `integration/` absent, and the branch
    `target_branch_name` would have returned had it succeeded absent. All three
    on the lead with its one exempt absolute path removed.
    """
    with (
        mock.patch.object(
            handoff_tree_status,
            "target_branch_name",
            side_effect=TargetBranchError("integration/LG-MS does not exist"),
        ),
        caplog.at_level("WARNING"),
    ):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, configured_base_ticket, ValueError("CONFLICT (content): shared.txt")
        )

    workspace = db_session.get(Workspace, configured_base_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, configured_base_ticket, workspace)
    lead = no_target_lead(TICKET, repo_root)
    assert detail.startswith(lead), f"expected the no-target lead:\n{lead!r}\ngot\n{detail!r}"

    measurable = prose(lead, exempt=repo_root)
    assert GitAutomationConfig().base_branch not in measurable, lead
    assert "integration/" not in measurable, lead
    assert CONFIGURED_BASE not in measurable, (
        "the lead names the branch the read would have returned had it succeeded, "
        f"which is a branch nobody resolved wearing a different spelling: {lead}"
    )
    # And nowhere in the whole detail either: resolving the workspace's profile
    # for a branch name is the second half of 974/1214's defect, and
    # `CONFIGURED_BASE` is the only string in this module that can see it.
    assert CONFIGURED_BASE not in detail, (
        f"the handler resolved the workspace's profile for a branch name: {detail!r}"
    )


def test_every_arm_of_the_guard_logs_the_same_three_facts(
    db_session: Session, configured_base_ticket: Ticket, caplog
):
    """AC8 binds every firing of the guard, which is now two `except` clauses.

    The fourth fact goes. 1214's record carried which ticket, which workspace,
    which branch was used, and that the branch was a built-in default *because*
    the profile could not be read. AC7 deletes the branch, so facts three and
    four go with it — and they are precisely the two a reader could mistake for
    a resolved answer, which is why 1214 pinned them in the first place. What
    remains is the three that are still true: which ticket, which workspace, and
    the caught exception's own text.

    This is the `TargetBranchError` arm, the one a fix can get wrong while
    staying green: log on the new `except ProfileUnreadableError` clause and
    leave this one silent, and only this test goes red.
    """
    workspace = db_session.get(Workspace, configured_base_ticket.workspace_id)
    cause = TargetBranchError("integration/LG-MS does not exist")
    with (
        mock.patch.object(handoff_tree_status, "target_branch_name", side_effect=cause),
        caplog.at_level("WARNING"),
    ):
        handoff_tree_status.describe_handoff_failure(
            db_session, configured_base_ticket, ValueError("CONFLICT (content): shared.txt")
        )

    records = _handler_warnings(caplog)
    assert len(records) == 1, (
        f"expected exactly one WARNING from the guard, got {[r.getMessage() for r in records]}"
    )
    message = records[0].getMessage()
    assert TICKET in message, message
    assert workspace.slug in message, (
        f"the workspace whose target could not be read is not named: {message}"
    )
    assert str(cause) in message, (
        f"the caught exception's own text, the only text naming what actually failed, "
        f"was dropped: {message}"
    )
    # AC8's other half, on both spellings of a branch name. `CONFIGURED_BASE`
    # would mean the handler read the workspace's profile; the built-in default
    # would mean the deleted fallback came back as a log line.
    assert CONFIGURED_BASE not in message, (
        f"the record names the workspace's configured base as though it had been "
        f"read, when no branch was resolved at all: {message}"
    )
    assert GitAutomationConfig().base_branch not in message, (
        f"the record names a branch nobody resolved: {message}"
    )


def test_the_guard_names_two_arms_over_three_classes_and_nothing_broader(db_session: Session):
    """AC16. Two `except` clauses, and their union is exactly the three names.

    `test_the_guard_names_both_arms_and_nothing_broader` stood here and asserted
    `len(handlers) == 1`, with the stated rationale "AC9's single WARNING shape
    is truthful for both arms". lg-milestone-that-1237 retires that rationale:
    the profile arm and the no-target arm compose different leads (AC2, AC3),
    and arm-specific prose out of one handler is only reachable through
    `isinstance(resolve_exc, ProfileUnreadableError)`, which the
    `py-organization` no-`isinstance` gate forbids. `except`-clause dispatch is
    both the gate-clean form and the simplest one.

    The clause's *real* intent survives intact and is what is asserted here: no
    dropped arm, and nothing broader than the three names. Widening either
    handler to `Exception` would turn a `TypeError` anywhere under
    `target_branch_name` into "this ticket has no target branch" — a confident
    wrong answer, where the 500 it replaced was at least visible. That is why
    `test_a_programming_error_under_the_target_lookup_is_not_a_fallback_branch`
    passes unedited.

    Read off the AST rather than the text, so re-ordering, re-wrapping or
    aliasing the names cannot change the answer.
    """
    import ast

    source = textwrap.dedent(inspect.getsource(handoff_tree_status.describe_handoff_failure))
    handlers = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ExceptHandler)  # py-org: allow-isinstance - AST node walk
    ]
    assert len(handlers) == 2, (
        f"describe_handoff_failure has {len(handlers)} except handlers: the profile arm "
        "and the no-target arm compose different leads, and one handler reaching "
        "arm-specific prose needs an isinstance the organization gate forbids"
    )

    caught = [
        {
            node.id
            for node in ast.walk(handler.type)
            if isinstance(node, ast.Name)  # py-org: allow-isinstance - AST node walk
        }
        if handler.type is not None
        else set()
        for handler in handlers
    ]
    assert set().union(*caught) == {"TargetBranchError", "ProfileUnreadableError", "ValueError"}, (
        f"the two handlers catch {[sorted(names) for names in caught]}: their union is all "
        "three arms and nothing else. TargetBranchError and ProfileUnreadableError both "
        "subclass RuntimeError, so neither is covered by ValueError; and ValueError cannot "
        "be dropped, because validate_branch_name reaches this handler through "
        "integration_branch_for with one, and a ticket whose integration branch cannot be "
        "named has no target branch rather than a 500"
    )
    for names in caught:
        assert names, (
            "an except handler catching nothing is a bare except in disguise, and it "
            "catches the programming errors this guard must still propagate"
        )
        assert not names & {"Exception", "BaseException"}, (
            f"a handler is widened to {sorted(names)}: `except Exception` would report a "
            "programming error as 'this ticket has no target branch'"
        )

    # AC1's other half: selection is by `except`-clause dispatch and by nothing
    # else. `isinstance(resolve_exc, ProfileUnreadableError)` inside one handler
    # reaches the same two leads and would satisfy every behaviour test in this
    # module — and it is a type switch written by hand, which the
    # `py-organization` gate forbids and which puts the classification back at
    # the boundary that `except` already does for free.
    called = {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)  # py-org: allow-isinstance - AST node walk
        and isinstance(node.func, ast.Name)  # py-org: allow-isinstance - AST node walk
    }
    assert "isinstance" not in called, (
        "the composer type-switches on the caught exception by hand: the two leads are "
        "selected by except-clause dispatch, which is Python's own class dispatch"
    )


def test_the_target_is_read_exactly_once_per_call(
    db_session: Session, configured_base_ticket: Ticket
):
    """AC9. `target_branch_name` is called once, and it is still the only read.

    The function runs *because* a resolution raised, so the invariant 974 R6 and
    -1214 pinned is that it reads rather than resolves — and reads once. Two
    reads would be the shape a two-clause split invites: a `try` per arm, or a
    retry inside the profile handler, either of which re-pays for a lookup that
    has already failed and gives the two leads two chances to disagree about
    what happened.

    Driven on the arm where the read *succeeds*, deliberately: on a failing arm
    a second call would raise again and be swallowed by the same guard, so the
    count is only observable here.
    """
    reads = []
    real = handoff_tree_status.target_branch_name

    def spy(session, ticket, workspace):
        reads.append(ticket.external_id)
        return real(session, ticket, workspace)

    with mock.patch.object(handoff_tree_status, "target_branch_name", spy):
        handoff_tree_status.describe_handoff_failure(
            db_session, configured_base_ticket, ValueError("CONFLICT (content): shared.txt")
        )

    assert reads == [TICKET], f"the target was read {len(reads)} times: {reads}"
    assert not hasattr(handoff_tree_status, "resolve_target_branch"), (
        "the module binds the write-path resolver; the read is `target_branch_name`"
    )


def test_a_target_that_resolves_is_never_logged_as_a_fallback(
    db_session: Session, configured_base_ticket: Ticket, caplog
):
    """The inverse of AC9, which nothing above measures.

    Every test on this handler drives it through the guard, so a fix that logs
    the fallback WARNING unconditionally — or substitutes the built-in default
    whether or not the read succeeded — reads green throughout. Then every
    handoff failure in the system claims the branch it names is a stand-in, and
    the record AC9 exists to make meaningful is noise on the path where the
    target was read perfectly well.
    """
    target = target_branch_name(
        db_session,
        configured_base_ticket,
        db_session.get(Workspace, configured_base_ticket.workspace_id),
    )
    assert target, "fixture: the target must resolve for this test to mean anything"

    with caplog.at_level("WARNING"):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, configured_base_ticket, ValueError("CONFLICT (content): shared.txt")
        )

    assert f"resolve the conflict with {target}," in _lead(detail), (
        f"the resolved target was replaced by a fallback: {detail!r}"
    )
    assert _handler_warnings(caplog) == [], (
        "a handoff whose target resolved logged the fallback warning anyway: "
        f"{[record.getMessage() for record in _handler_warnings(caplog)]}"
    )


def test_the_sentence_is_the_same_on_every_call_and_warns_once_per_call(
    db_session: Session, malformed_profile_ticket: Ticket, caplog
):
    """Determinism, on the path where a cache is involved.

    `_parse_profile` is `lru_cache`d and caches successful parses only, so a
    malformed profile raises on every call — but "exactly one WARNING" has to
    mean one *per invocation*, not one per process. A guard that memoised its
    fallback, or a handler that logged on first failure only, would leave the
    second operator to hit the same failure and find nothing in the log.
    """
    conflict = ValueError("CONFLICT (content): shared.txt")
    details = []
    for attempt in range(3):
        caplog.clear()
        with caplog.at_level("WARNING"):
            details.append(
                handoff_tree_status.describe_handoff_failure(
                    db_session, malformed_profile_ticket, conflict
                )
            )
        assert len(_handler_warnings(caplog)) == 1, (
            f"call {attempt + 1} emitted {[r.getMessage() for r in _handler_warnings(caplog)]}"
        )

    assert len(set(details)) == 1, f"the same failure described itself three ways: {details}"


def test_a_ticket_whose_workspace_is_gone_still_produces_a_sentence(db_session: Session):
    """AC1(C), AC4. No workspace row, so no tree and no target.

    `describe_handoff_failure` must return a string for every input reachable on
    its own call path (R1), and a ticket whose workspace row is missing reaches
    it the same way any other does. Nothing else in this module exercises the
    `workspace is None` branch, so an implementation that moved the resolve out
    of the `if` — the obvious shape once the fallback stops needing the
    workspace — would raise `AttributeError` here and be caught by no test.

    What it *says* is AC4. It read "resolve the conflict with its target branch,
    or run the stage supervised — the worktree is ." : a conflict that does not
    exist, against a branch that is a placeholder, in a tree that is the
    server's own working directory. The worktree clause is omitted entirely,
    because `Path(".")` is a path-shaped non-answer rather than something the
    operator can open. AC4 keys that omission on `workspace is None` and not on
    `work_item_type`, so this input and the real initiative below get the same
    sentence — the clause is as empty for one as for the other.
    """
    # Not persisted: the database enforces the foreign key, so a ticket whose
    # workspace row is missing is only reachable as the in-memory value a caller
    # already holds — which is exactly how the route and the harness hold it
    # when the row was deleted under them.
    orphan = Ticket(external_id=f"lg-orphan-{uuid4()}", workspace_id=str(uuid4()), title="Orphan")
    conflict = ValueError("CONFLICT (content): shared.txt")

    detail = handoff_tree_status.describe_handoff_failure(db_session, orphan, conflict)

    lead = no_target_lead(orphan.external_id, None)
    # Equality for the same reason as the real-initiative test above: the
    # criterion here is a clause that must not be there, and `startswith` is
    # blind to one that is.
    assert detail == f"{lead} {conflict}", (
        f"expected the class-C lead, with no tree named:\n{lead!r}\ngot\n{detail!r}"
    )
    # AC4/AC10: this lead names no path, so its prose is the whole lead and
    # there is nothing to exempt from the measurement.
    assert len(prose(lead)) <= NO_TARGET_PROSE_LIMIT, f"{len(prose(lead))}: {lead}"
    assert "worktree" not in detail.removesuffix(str(conflict)), (
        f"the class-C lead names a tree, and the only tree it could name is the bare "
        f"Path('.') — the server's working directory: {detail!r}"
    )
    assert "its target branch" not in detail, (
        "the placeholder is gone: the lead says there is no target branch rather than "
        f"naming one that does not exist: {detail!r}"
    )
    assert str(conflict) in detail, "the failure text is still carried after the lead"


def test_it_returns_rather_than_propagating_when_the_profile_will_not_parse(
    db_session: Session, malformed_profile_ticket: Ticket
):
    """lg-milestone-that-1214's repro, kept, now reading AC2's lead.

    It lived in `test_open_defect_repros.py` as `xfail(strict=True)` while the
    defect was open; `strict` would have turned the XPASS into a failure if it
    had been left behind, so the move was part of the fix rather than tidying
    after it. Kept as a test of its own beside AC1's because it asserts the one
    thing AC1's cannot: the *escape* is caught and reported, so a regression
    names the type that got out instead of erroring somewhere up the stack.
    """
    profile_path = _profile_path(db_session, malformed_profile_ticket)
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    try:
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, malformed_profile_ticket, conflict
        )
    except ValueError as escaped:
        pytest.fail(
            "describe_handoff_failure raised instead of returning: "
            f"{type(escaped).__name__}: {escaped}"
        )

    lead = profile_lead(TICKET, profile_path)
    assert detail.startswith(lead), f"expected the profile lead:\n{lead!r}\ngot\n{detail!r}"
    assert str(conflict) in detail, "the full failure text is still carried after the lead"


# --- lg-milestone-that-1215: the profile that does not parse at all -----------
#
# The sibling input class 1214's tuple does not reach. 1214 widened the guard to
# `except (TargetBranchError, ValueError)`, which covers a profile that *parses*
# and fails schema validation — pydantic's `ValidationError` is a `ValueError`.
# It does not cover a profile that does not parse, and there are four such shapes
# (measured): a truncated file is a `ruamel` `ParserError`, a tab-indented one a
# `ScannerError`, and a document that is a sequence or a scalar rather than a
# mapping is a `TypeError`. None of them is a `ValueError`, so the observable
# failure was 1214's unchanged — the renderer propagated, the terminal-handoff
# route 500ed with `{"detail": "Internal Server Error"}`, and the run it stamped
# was never settled.
#
# The repro for it lived in `test_open_defect_repros.py` as `xfail(strict=True)`
# until the load seam started raising one named `ProfileUnreadableError`; it is
# here, unmarked, because `strict` turns an XPASS into a failure and leaving it
# behind would be the regression rather than the fix.
#
# Two shapes, not four: these two are the two *mechanisms* reaching this handler
# (a parse that refuses the bytes, and a document the parser accepts but the
# loader cannot index). The other two ride the same two paths and are pinned at
# the seam itself, in `test_orchestration_profile_loading.py`.

UNPARSEABLE_PROFILES = {
    "corrupt-syntax": "slug: default\nname: [unclosed\n",
    "sequence-document": "- one\n- two\n",
}


@pytest.fixture(name="unparseable_profile_ticket", params=sorted(UNPARSEABLE_PROFILES))
def unparseable_profile_ticket_fixture(
    request, db_session: Session, repo: Path, tmp_path: Path, monkeypatch
) -> Ticket:
    """A ticket whose workspace resolves a profile that will not parse (V3).

    Same construction as `malformed_profile_ticket` and the same trap: the
    unreadable file has to be the one that actually resolves.
    `resolve_orchestration_profile` takes the **first existing** candidate —
    `<orchestration_profile_slug>.yaml`, then `<workspace.slug>.yaml`, then
    `default.yaml` — so the slug is a uuid with no file of its own and
    `orchestration_profile_slug` is left unset, and the fixture asserts the
    resolve really raises before handing the subject over. A `default.yaml`
    written for a workspace that resolves something else exercises nothing while
    passing green.

    What differs from the schema-invalid fixture is the *type*, which is the
    whole point: this file is not a `ValueError` at any layer of the stack, so a
    guard that only names `ValueError` cannot reach it however it is reordered.
    `ProfileUnreadableError` is asserted here as the fixture's own check — if the
    seam ever stopped classifying, every test below would fail for a reason that
    has nothing to do with this handler.
    """
    orchestration = tmp_path / "orchestration-unparseable"
    orchestration.mkdir()
    (orchestration / "default.yaml").write_text(
        UNPARSEABLE_PROFILES[request.param], encoding="utf-8"
    )
    monkeypatch.setattr(orchestration_profile_module, "orchestration_dir", lambda: orchestration)

    workspace = Workspace(
        slug=f"unparseable-profile-{uuid4()}", name="Unparseable profile", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    assert not workspace.orchestration_profile_slug, (
        "fixture: a profile slug would resolve a different candidate than default.yaml"
    )
    with pytest.raises(ProfileUnreadableError) as excinfo:
        resolve_orchestration_profile(workspace)
    assert not isinstance(  # py-org: allow-isinstance - the type is the finding
        excinfo.value, ValueError
    ), (
        f"fixture: this profile raises {type(excinfo.value).__name__}, a ValueError, so "
        "lg-milestone-that-1214's guard already covers it and this vector proves nothing"
    )
    assert str((orchestration / "default.yaml").resolve()) in str(excinfo.value), (
        f"fixture: the error does not name the file that failed — {excinfo.value}"
    )
    return make_agent_stage_ticket(db_session, workspace, external_id=TICKET, stage_key="work")


def test_describe_handoff_failure_returns_a_sentence_when_the_profile_yaml_will_not_parse(
    db_session: Session, unparseable_profile_ticket: Ticket
):
    """AC1(A), AC2, for the shapes that do not parse at all.

    The escape is caught and reported here rather than left to propagate, so a
    regression names the type that got out instead of erroring somewhere up the
    stack — which is what this repro asserted while the defect was open, and the
    reason it is kept beside the schema-invalid one.

    These two shapes are the ones that arrive as a `ProfileUnreadableError`
    which is **not** a `ValueError` (the fixture asserts that), so they can only
    reach the profile arm through the name `ProfileUnreadableError`. A handler
    that kept the three names in one clause and dispatched on the exception's
    text would pass the schema-invalid test and fail here.
    """
    profile_path = _profile_path(db_session, unparseable_profile_ticket)
    workspace = db_session.get(Workspace, unparseable_profile_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, unparseable_profile_ticket, workspace)
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    try:
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, unparseable_profile_ticket, conflict
        )
    except Exception as escaped:  # noqa: BLE001 - any escape is the finding
        pytest.fail(
            "describe_handoff_failure raised instead of returning: "
            f"{type(escaped).__name__}: {escaped}"
        )

    lead = profile_lead(TICKET, profile_path)
    assert detail.startswith(lead), f"expected the profile lead:\n{lead!r}\ngot\n{detail!r}"
    assert len(prose(lead, exempt=profile_path)) <= PROFILE_PROSE_LIMIT, (
        f"{len(prose(lead, exempt=profile_path))}: {lead}"
    )
    assert str(repo_root) not in lead, f"the profile lead names a second absolute path: {lead}"
    assert "CONFLICT" not in lead, f"git's merge output reached the lead: {lead}"
    assert str(conflict) in detail, "the full failure text is still carried after the lead"


def test_the_unparseable_profile_is_logged_with_the_same_three_facts(
    db_session: Session, unparseable_profile_ticket: Ticket, caplog
):
    """AC8. One WARNING from this handler's own call, on the new shapes too.

    On this path the caught exception is a `ProfileUnreadableError`, so its own
    text is the only thing in the record that says which profile an operator has
    to go and open; a handler that logged the exception's class, or a summary of
    it, would leave the record true and useless.

    "Exactly one" is scoped to this call, not to the request: the dispatch-path
    budget fallback logs its own WARNING earlier in the same request, which is
    correct. `_handler_warnings` filters to this module for that reason.
    """
    workspace = db_session.get(Workspace, unparseable_profile_ticket.workspace_id)
    profile_path = _profile_path(db_session, unparseable_profile_ticket)
    with caplog.at_level("WARNING"):
        handoff_tree_status.describe_handoff_failure(
            db_session,
            unparseable_profile_ticket,
            ValueError("CONFLICT (content): shared.txt"),
        )

    records = _handler_warnings(caplog)
    assert len(records) == 1, (
        f"expected exactly one WARNING from the guard, got {[r.getMessage() for r in records]}"
    )
    message = records[0].getMessage()
    assert TICKET in message, message
    assert workspace.slug in message, (
        f"the workspace whose target could not be read is not named: {message}"
    )
    assert "could not be read as an orchestration profile" in message, (
        f"the caught exception's own text was dropped, and with it the only text in the "
        f"record naming the file that will not parse: {message}"
    )
    assert GitAutomationConfig().base_branch not in message.replace(str(profile_path), ""), (
        f"the record names a branch nobody resolved: {message}"
    )


def test_a_programming_error_under_the_target_lookup_is_not_a_fallback_branch(
    db_session: Session, configured_base_ticket: Ticket, caplog
):
    """AC2's behaviour half. The guard is widened by one *name*, not opened up.

    `except Exception` would satisfy every other test in this module, including
    the AST one if that were deleted, and would turn a `TypeError` or an
    `AttributeError` anywhere under `target_branch_name` into a sentence telling
    an operator to resolve a conflict against `main`. That is a worse outcome
    than the 500 this ticket removes: the 500 is visible, and this would be a
    confident wrong answer.

    `TypeError` specifically, because it is the shape two of the six unreadable
    profiles raise today — so the one-word fix at the load seam ("catch
    TypeError") and the one-word fix here ("catch Exception") both pass the
    criteria and both relabel a bug in our own code as a configuration problem.
    """
    bug = TypeError("'NoneType' object is not subscriptable")
    with (
        mock.patch.object(handoff_tree_status, "target_branch_name", side_effect=bug),
        caplog.at_level("WARNING"),
        pytest.raises(TypeError) as excinfo,
    ):
        handoff_tree_status.describe_handoff_failure(
            db_session, configured_base_ticket, ValueError("CONFLICT (content): shared.txt")
        )

    assert excinfo.value is bug, (
        f"a different error came out than went in: {type(excinfo.value).__name__}"
    )
    assert _handler_warnings(caplog) == [], (
        "a programming error was reported as a fallback branch: "
        f"{[record.getMessage() for record in _handler_warnings(caplog)]}"
    )


# --- lg-milestone-that-1237: the lead each input class earns -------------------
#
# `describe_handoff_failure` composed exactly one lead for every input, and
# `target_branch` came from a guarded read whose `except` set
# `GitAutomationConfig().base_branch` — the built-in default `main`, a stand-in
# nobody resolved. So on every input where the read failed the operator was told
# to "resolve the conflict with main", on a path where no merge was attempted and
# no conflict exists, naming a branch that is not the workspace's base except by
# coincidence.
#
# Four leads replace it, selected by `except`-clause dispatch plus the existing
# `if workspace is not None` and by nothing else:
#
#   A  ProfileUnreadableError from the read  -> the profile lead (AC2)
#   B  TargetBranchError, or a plain ValueError, from the read -> no-target (AC3)
#   C  workspace is None, never enters the try -> no-target, no worktree clause (AC4)
#   D  the read succeeded -> today's lead, byte for byte (AC5)
#
# The tests below pin each of the four on the real function, at the largest
# realistic volume read from the live database (AC11): a 29-character
# `external_id`, an 83-character profile path, an 88-character worktree. A cap
# proved against a 6-character id and a bare `tmp_path` proves nothing about the
# surface, and the two padded fixtures say so where they pad.


@pytest.fixture(name="long_worktree_ticket")
def long_worktree_ticket_fixture(db_session: Session, tmp_path: Path) -> Ticket:
    """AC11 for the no-target arm: a 29-character id over an 88-character tree.

    44 live worktrees are 88 characters and `tmp_path` reaches nowhere near it
    on this machine, so the repository is built under a deliberately padded
    directory name rather than left at whatever length the test's own name
    happens to give it.
    """
    tree = padded_dir(tmp_path, LONGEST_WORKTREE_PATH, stem="worktree")
    repo = make_repo(tmp_path, name=tree.name)
    assert len(str(repo)) >= LONGEST_WORKTREE_PATH, (
        f"fixture: the worktree is {len(str(repo))} characters, short of the "
        f"{LONGEST_WORKTREE_PATH} AC11 measures the cap at: {repo}"
    )
    workspace = Workspace(
        slug=f"long-worktree-{uuid4()}", name="Long worktree", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    return make_agent_stage_ticket(
        db_session, workspace, external_id=LONGEST_EXTERNAL_ID, stage_key="work"
    )


@pytest.fixture(name="long_profile_ticket")
def long_profile_ticket_fixture(
    db_session: Session, repo: Path, tmp_path: Path, monkeypatch
) -> Ticket:
    """AC11 for the profile arm: a 29-character id over an 83-character profile.

    4 live orchestration profiles are 80-83 characters. Same resolution trap as
    the other profile fixtures (the unreadable file has to be the one that
    actually resolves), and the same padding note as the worktree fixture: the
    directory name is padded on purpose so the measured path reaches the live
    maximum.
    """
    orchestration = padded_dir(
        tmp_path, LONGEST_PROFILE_PATH - len("/default.yaml"), stem="profile"
    )
    orchestration.mkdir()
    profile = orchestration / "default.yaml"
    profile.write_text("slug: default\nname: [unclosed\n", encoding="utf-8")
    assert len(str(profile.resolve())) >= LONGEST_PROFILE_PATH, (
        f"fixture: the profile path is {len(str(profile.resolve()))} characters, short "
        f"of the {LONGEST_PROFILE_PATH} AC11 measures the cap at: {profile}"
    )
    monkeypatch.setattr(orchestration_profile_module, "orchestration_dir", lambda: orchestration)

    workspace = Workspace(slug=f"long-profile-{uuid4()}", name="Long profile", repo_path=str(repo))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    assert not workspace.orchestration_profile_slug, (
        "fixture: a profile slug would resolve a candidate other than default.yaml"
    )
    with pytest.raises(ProfileUnreadableError) as excinfo:
        resolve_orchestration_profile(workspace)
    assert excinfo.value.path == profile.resolve(), (
        f"fixture: default.yaml is not the file that resolved — {excinfo.value.path}"
    )
    return make_agent_stage_ticket(
        db_session, workspace, external_id=LONGEST_EXTERNAL_ID, stage_key="work"
    )


@pytest.fixture(name="initiative_child_ticket")
def initiative_child_ticket_fixture(db_session: Session, repo: Path) -> Ticket:
    """AC17(B). A workspace-bound TASK sitting directly under an initiative.

    The only `TargetBranchError` arm reachable at this call site, and reached
    here without a mock. `VALID_HIERARCHY[INITIATIVE]` is
    `[MILESTONE, FEATURE, BUG]` (work_item_types.py:26), so a TASK trips
    `_require_initiative_child` -> `validate_parent_child`, whose `ValueError`
    is wrapped as a `TargetBranchError` (target_branch.py:120-128).

    `_refuse_initiative` is the other `TargetBranchError` source and it is
    **dead here**: it fires only for a ticket that is itself an initiative, and
    `workspace_id` is NULL iff `work_item_type is INITIATIVE`
    (ticket_workspace_binding.py:13-24), so such a ticket never enters the `try`
    at all — it is input class C, and `real_initiative` below covers it.

    The fixture asserts its own vector before handing the subject over. A test
    written against a real initiative that silently exercised class C while
    claiming to cover the guard is exactly what AC17 forbids, and it is the
    mistake this fixture exists to make impossible.
    """
    workspace = Workspace(
        slug=f"initiative-child-{uuid4()}", name="Initiative child", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    initiative = Ticket(
        external_id=f"init-{uuid4().hex[:8]}",
        workspace_id=None,
        title="An initiative",
        work_item_type=WorkItemType.INITIATIVE,
    )
    db_session.add(initiative)
    db_session.commit()
    db_session.refresh(initiative)

    ticket = make_agent_stage_ticket(
        db_session, workspace, external_id=LONGEST_EXTERNAL_ID, stage_key="work"
    )
    ticket.parent_ticket_id = initiative.id
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with pytest.raises(TargetBranchError) as excinfo:
        target_branch_name(db_session, ticket, workspace)
    assert "hierarchy does not allow" in str(excinfo.value), (
        f"fixture: this is not the initiative-child TargetBranchError — {excinfo.value}"
    )
    return ticket


@pytest.fixture(name="unnameable_branch_ticket")
def unnameable_branch_ticket_fixture(db_session: Session, repo: Path) -> Ticket:
    """AC17(B'). A tree whose integration branch cannot be named.

    The third class the guard catches, and the one no criterion before AC3
    covered: `integration_branch_for` composes `integration/<root external_id>`
    and `validate_branch_name` rejects it against `^[A-Za-z0-9._/-]+$`
    (git_branch.py:26,63) with a plain `ValueError`. A space in the root's id is
    enough, and ids with spaces are reachable — `respell_external_id` runs at
    the tool boundary, not at the model.

    Reached without a mock for the same reason as the initiative-child fixture:
    the operator-facing fact is that this ticket has no target branch, and a
    patched `target_branch_name` would prove only that the test knows which
    exception to raise. AC3 groups this input with `TargetBranchError` under one
    lead because the fact is identical and a third prose budget buys nothing
    actionable; the trailing exception text names which rule refused.
    """
    workspace = Workspace(
        slug=f"unnameable-branch-{uuid4()}", name="Unnameable branch", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    milestone = Ticket(
        external_id="LG MS spaced 1237",
        workspace_id=workspace.id,
        title="A milestone whose id cannot be a branch",
        work_item_type=WorkItemType.MILESTONE,
    )
    db_session.add(milestone)
    db_session.commit()
    db_session.refresh(milestone)

    ticket = make_agent_stage_ticket(
        db_session, workspace, external_id=LONGEST_EXTERNAL_ID, stage_key="work"
    )
    ticket.parent_ticket_id = milestone.id
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with pytest.raises(ValueError) as excinfo:
        target_branch_name(db_session, ticket, workspace)
    assert type(excinfo.value) is ValueError, (
        f"fixture: this arm must be the plain ValueError, not a "
        f"{type(excinfo.value).__name__} another name already covers"
    )
    assert "Invalid branch name" in str(excinfo.value), (
        f"fixture: the ValueError is not the branch-name validation's — {excinfo.value}"
    )
    return ticket


@pytest.fixture(name="real_initiative")
def real_initiative_fixture(db_session: Session) -> Ticket:
    """AC17(C). A real initiative row, which never enters the `try`.

    `workspace_id` is NULL iff `work_item_type is INITIATIVE`
    (ticket_workspace_binding.py:13-24) and the guard sits inside
    `if workspace is not None` (handoff_tree_status.py:166-170), so this is the
    path a real initiative actually takes — the one that read "resolve the
    conflict with its target branch … the worktree is ." and that no original
    criterion named.
    """
    initiative = Ticket(
        external_id=f"init-{uuid4().hex[:8]}",
        workspace_id=None,
        title="An initiative",
        work_item_type=WorkItemType.INITIATIVE,
    )
    db_session.add(initiative)
    db_session.commit()
    db_session.refresh(initiative)
    assert initiative.workspace_id is None, (
        "fixture: an initiative with a workspace would enter the try and exercise "
        "the guard instead of the arm this covers"
    )
    return initiative


# --- AC2, AC3, AC4, AC5: the four leads, at live volume -----------------------


def test_the_profile_lead_names_the_profile_and_only_the_profile(
    db_session: Session, long_profile_ticket: Ticket
):
    """AC2, AC10, AC11. Input class A, at the live maximum of both identifiers.

    Asserted as `startswith` against a string built from the identifiers, so
    "the lead" means the composed lead and not whatever the first `". "` split
    returns (AC13). The profile path is taken from
    `ProfileUnreadableError.path`, which is also where the composer must read
    it: already absolute, already `.resolve()`d, never parsed out of
    `str(exc)`.

    The em dash is U+2014 and the wording is exact, because this string is what
    the toast renders verbatim — there is no second surface on which an operator
    can re-read a reworded version of it.
    """
    profile_path = _profile_path(db_session, long_profile_ticket)
    workspace = db_session.get(Workspace, long_profile_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, long_profile_ticket, workspace)
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    detail = handoff_tree_status.describe_handoff_failure(db_session, long_profile_ticket, conflict)

    lead = profile_lead(LONGEST_EXTERNAL_ID, profile_path)
    assert detail.startswith(lead), f"expected the profile lead:\n{lead!r}\ngot\n{detail!r}"
    assert "—" in lead, "the em dash is U+2014 and the wording is exact"
    # AC14: the action is executable from where the operator is standing — the
    # file to edit, named absolutely. Neither arm may be prose with no verb.
    assert "Fix " in lead, f"the profile arm names no action: {lead}"
    # AC10, at the 29-character live maximum: 109 against a cap of 115.
    measurable = prose(lead, exempt=profile_path)
    assert len(measurable) <= PROFILE_PROSE_LIMIT, f"{len(measurable)}: {lead}"
    # AC2: exactly one absolute path, and it is the profile's.
    assert str(profile_path) in lead, (
        f"the file the operator has to open is not named absolutely: {lead}"
    )
    assert str(repo_root) not in lead, (
        f"the lead names the worktree as well, and nothing in a worktree fixes "
        f"malformed YAML: {lead}"
    )
    assert str(conflict) in detail, "the parse reason still follows the lead (AC6)"


def test_the_no_target_lead_says_nothing_was_merged_and_names_the_tree(
    db_session: Session, long_worktree_ticket: Ticket, caplog
):
    """AC3, AC10, AC11. Input class B, at the live maximum of both identifiers.

    `so nothing was merged` is load-bearing and asserted on its own: it is the
    fact the old sentence got wrong by implying a merge had been attempted and
    conflicted. `{repo_root}` is the `resolve_ticket_root` value already
    computed above the `try`, not a second resolution.
    """
    workspace = db_session.get(Workspace, long_worktree_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, long_worktree_ticket, workspace)
    assert len(str(repo_root)) >= LONGEST_WORKTREE_PATH, (
        f"fixture: {len(str(repo_root))} characters is short of the live maximum"
    )
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    with (
        mock.patch.object(
            handoff_tree_status,
            "target_branch_name",
            side_effect=TargetBranchError("integration/LG-MS does not exist"),
        ),
        caplog.at_level("WARNING"),
    ):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, long_worktree_ticket, conflict
        )

    lead = no_target_lead(LONGEST_EXTERNAL_ID, repo_root)
    assert detail.startswith(lead), f"expected the no-target lead:\n{lead!r}\ngot\n{detail!r}"
    assert "so nothing was merged" in lead, (
        f"the fact the old sentence got wrong — it implied a merge had been attempted "
        f"and conflicted — is not in the lead: {lead}"
    )
    # AC10, at the 29-character live maximum: 152 against a cap of 160.
    measurable = prose(lead, exempt=repo_root)
    assert len(measurable) <= NO_TARGET_PROSE_LIMIT, f"{len(measurable)}: {lead}"
    assert str(repo_root) in lead, f"the tree the operator runs the stage in: {lead}"
    assert str(conflict) in detail, "the rule that refused still follows the lead (AC6)"


def test_a_ticket_sitting_directly_under_an_initiative_has_no_target_branch(
    db_session: Session, initiative_child_ticket: Ticket, caplog
):
    """AC17(B), on the real function with no mock in it.

    This is the only reachable `TargetBranchError` arm at this call site, and
    the input AC1 names when it says "an initiative ticket" — read as the
    initiative-related error rather than a real initiative, because a real
    initiative cannot reach the guard at all.

    AC8 is measured on this arm too, so the three facts are pinned against an
    exception the test did not author.
    """
    workspace = db_session.get(Workspace, initiative_child_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, initiative_child_ticket, workspace)
    # The failure the real call path carries for this input, not a merge
    # conflict: nothing merged here, and AC6's composition has nowhere to put a
    # refusal the caller did not bring. See `_the_failure_production_would_carry`.
    failure = _the_failure_production_would_carry(db_session, initiative_child_ticket)

    with caplog.at_level("WARNING"):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, initiative_child_ticket, failure
        )

    lead = no_target_lead(LONGEST_EXTERNAL_ID, repo_root)
    assert detail == f"{lead} {failure}", (
        f"expected the no-target lead and nothing but the cause behind it:\n"
        f"{lead!r}\ngot\n{detail!r}"
    )
    assert "hierarchy does not allow" in detail, (
        f"the rule that refused is the only text naming why there is no target "
        f"branch, and it is gone: {detail!r}"
    )
    records = _handler_warnings(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    message = records[0].getMessage()
    assert LONGEST_EXTERNAL_ID in message, message
    assert workspace.slug in message, message
    assert "hierarchy does not allow" in message, message


def test_an_unnameable_integration_branch_shares_the_no_target_lead(
    db_session: Session, unnameable_branch_ticket: Ticket, caplog
):
    """AC3's third input and AC17(B'), on the real function with no mock in it.

    `validate_branch_name` raises a plain `ValueError` through
    `integration_branch_for`, and AC3 groups it with `TargetBranchError` under
    one lead: the fact is identical from the operator's side, and a third prose
    budget buys nothing actionable. So the assertion is that the lead is
    **byte-identical** to the `TargetBranchError` arm's — a third wording here
    is a failure, not an improvement.
    """
    workspace = db_session.get(Workspace, unnameable_branch_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, unnameable_branch_ticket, workspace)
    failure = _the_failure_production_would_carry(db_session, unnameable_branch_ticket)

    with caplog.at_level("WARNING"):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, unnameable_branch_ticket, failure
        )

    lead = no_target_lead(LONGEST_EXTERNAL_ID, repo_root)
    assert detail == f"{lead} {failure}", (
        f"the plain-ValueError arm composed a third lead of its own:\n{lead!r}\ngot\n{detail!r}"
    )
    assert "Invalid branch name" in detail, (
        f"the rule that refused was dropped, and it is the only text saying which of "
        f"the three class-B inputs this was: {detail!r}"
    )
    # AC8 on the third arm: one WARNING per call, naming what failed.
    records = _handler_warnings(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    message = records[0].getMessage()
    assert LONGEST_EXTERNAL_ID in message, message
    assert workspace.slug in message, message
    assert "Invalid branch name" in message, message


def test_a_real_initiative_gets_the_no_target_lead_with_no_tree_named(
    db_session: Session, real_initiative: Ticket
):
    """AC4 and AC17(C). The second of the two inputs that reach `workspace is None`.

    `test_a_ticket_whose_workspace_is_gone_still_produces_a_sentence` is the
    other, and both get the same sentence because AC4 keys the omission on
    `workspace is None` rather than on `work_item_type`. In both cases
    `repo_root` would be the bare `Path(".")` — the server's working directory,
    not a tree the operator can open — so the clause is as empty for one as for
    the other.
    """
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    detail = handoff_tree_status.describe_handoff_failure(db_session, real_initiative, conflict)

    lead = no_target_lead(real_initiative.external_id, None)
    # Equality, not `startswith`, and the difference is the whole criterion. The
    # omission AC4 asks for is the *absence* of a clause at the end of the lead,
    # and `startswith` cannot see an absence: a lead that appended
    # "The worktree is ." still starts with this one. Asserting `"worktree" not
    # in lead` does not help either — `lead` is the string this test just built,
    # so that assertion is about the helper and passes whatever the composer did.
    # (Checked: a composer naming `Path(".")` here is green against every other
    # test in all four modules.)
    assert detail == f"{lead} {conflict}", (
        f"expected the class-C lead, with no tree named:\n{lead!r}\ngot\n{detail!r}"
    )
    assert "worktree" not in detail.removesuffix(str(conflict)), (
        f"the only tree this arm could name is the bare Path('.'), which is a "
        f"path-shaped non-answer: {detail!r}"
    )
    assert "its target branch" not in detail, detail
    assert str(conflict) in detail, "the failure text still follows the lead (AC6)"


def test_a_resolved_target_still_reads_exactly_as_it_did(
    db_session: Session, configured_base_ticket: Ticket
):
    """AC5. Input class D composes today's lead byte for byte.

    The real conflicting-refresh case is what this composer was written for
    (lg-milestone-that-917 AC12), and lg-milestone-that-975's classification
    says it is the only failure that earns the tree sentence at all. Neither
    changes here. Asserted as an equality against `conflict_lead`, so a
    "while we are in here" reword of the one lead no criterion touches fails.
    """
    workspace = db_session.get(Workspace, configured_base_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, configured_base_ticket, workspace)
    target = target_branch_name(db_session, configured_base_ticket, workspace)
    assert target == CONFIGURED_BASE, "fixture: the target must resolve for this to mean anything"
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    detail = handoff_tree_status.describe_handoff_failure(
        db_session, configured_base_ticket, conflict
    )

    lead = conflict_lead(TICKET, target, repo_root)
    assert detail == f"{lead} {conflict}", (
        f"input class D's lead is not today's, byte for byte:\n{lead!r}\ngot\n{detail!r}"
    )


# --- AC6, AC7, AC12, AC13: the properties that hold across every arm ----------


# AC6 is asserted in two tests rather than one, and the seam is a fixture
# constraint rather than a choice. Every profile fixture in this module
# monkeypatches `orchestration_profile_module.orchestration_dir` to its own
# directory, so two of them in one test signature means the second one to run
# decides what EVERY workspace resolves — which silently moves the other arms
# onto the profile arm. The first draft of this test took
# `long_profile_ticket` and `initiative_child_ticket` together and errored in
# `initiative_child_ticket`'s own assertion, because `target_branch_name` raised
# `ProfileUnreadableError` where the fixture required a `TargetBranchError`.
# That is the AC17 trap one layer down: a test that believes it covers an arm it
# has quietly moved off.


def test_the_exception_text_follows_the_lead_on_the_profile_arm(
    db_session: Session, long_profile_ticket: Ticket
):
    """AC6, input class A. The parse reason follows the lead, never replaces it."""
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")
    profile_path = _profile_path(db_session, long_profile_ticket)

    detail = handoff_tree_status.describe_handoff_failure(db_session, long_profile_ticket, conflict)

    lead = profile_lead(LONGEST_EXTERNAL_ID, profile_path)
    assert detail == f"{lead} {conflict}", (
        f"the composition is not `f'{{lead}} {{exc}}'.strip()`:\n{lead!r}\ngot\n{detail!r}"
    )


def test_the_exception_text_follows_the_lead_on_every_other_arm(
    db_session: Session,
    initiative_child_ticket: Ticket,
    unnameable_branch_ticket: Ticket,
    real_initiative: Ticket,
    configured_base_ticket: Ticket,
):
    """AC6 over the other three input classes: B, B', C and D.

    The composition stays `f"{lead} {exc}".strip()` with `exc` unchanged, so the
    rule that refused still follows the lead and the untruncated string still
    reaches the failed run's `stderr`, the run view and the external harness's
    `isError` text — however little of it the toast can show.

    Four inputs in one test deliberately: the claim is "on every arm", and four
    near-identical tests would let one be forgotten when a fifth input class
    arrives. These four share one readable profile, so unlike the profile arm
    they can be driven together.
    """
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    for ticket in (
        initiative_child_ticket,
        unnameable_branch_ticket,
        real_initiative,
        configured_base_ticket,
    ):
        detail = handoff_tree_status.describe_handoff_failure(db_session, ticket, conflict)
        assert detail.endswith(str(conflict)), (
            f"{ticket.external_id}: the exception's own text is not carried after the "
            f"lead: {detail!r}"
        )
        assert detail.index(str(conflict)) > 0, (
            f"{ticket.external_id}: the exception text came before the lead: {detail!r}"
        )
        assert detail.startswith(f"{ticket.external_id}: "), (
            f"the lead does not open with the ticket id: {detail!r}"
        )


def test_the_built_in_default_branch_is_gone_from_the_module(db_session: Session):
    """AC7. `grep -n GitAutomationConfig handoff_tree_status.py` returns nothing.

    Three places, asserted as one: the import block, a handler body, and the
    docstring. The name being importable at all is what lets the next edit reach
    a branch nobody resolved, and the docstring is where this module recorded
    the fallback as deliberate — a sentence that is now false.
    """
    module_source = Path(handoff_tree_status.__file__).read_text(encoding="utf-8")

    assert "GitAutomationConfig" not in module_source, (
        "the built-in default branch is still reachable from the module whose leads "
        "may never name a branch nobody resolved"
    )
    assert not hasattr(handoff_tree_status, "GitAutomationConfig"), (
        "the module still binds the built-in default under some other spelling"
    )


def _assert_no_conflict_and_no_branch_name(arm: str, lead: str, exempt: Path) -> None:
    """AC12, on one arm's lead with its one exempt absolute path removed.

    UX1 is the question the lead answers from the operator's side: "why did this
    handoff stop, and what do I fix?" So on every firing of the guard the lead
    names the thing that failed and the next action, and never an unresolved
    branch.

    The subtraction is the same one AC10 uses for the prose cap, and it is what
    makes either claim decidable at all: a workspace repo path may contain
    `main` as a substring, and a profile path certainly can. "No branch name" is
    three checks — the built-in default absent, the literal `integration/`
    absent, and (where the input can tell them apart) the branch the read would
    have returned absent, which
    `test_no_branch_the_read_would_have_returned_reaches_the_lead` adds against
    `CONFIGURED_BASE`.
    """
    measurable = prose(lead, exempt=exempt)
    assert "conflict" not in measurable.lower(), (
        f"{arm}: the lead still tells the operator to resolve a conflict, on an input "
        f"where nothing was merged and nothing conflicted: {lead}"
    )
    assert GitAutomationConfig().base_branch not in measurable, (
        f"{arm}: the built-in default branch is back in the lead: {lead}"
    )
    assert "integration/" not in measurable, (
        f"{arm}: an integration branch is named on an arm where none resolved: {lead}"
    )


def test_the_profile_arm_names_no_conflict_and_no_branch(
    db_session: Session, long_profile_ticket: Ticket
):
    """AC12 on the profile arm. Driven alone, for the fixture reason noted above."""
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")
    profile_path = _profile_path(db_session, long_profile_ticket)

    detail = handoff_tree_status.describe_handoff_failure(db_session, long_profile_ticket, conflict)

    lead = profile_lead(LONGEST_EXTERNAL_ID, profile_path)
    assert detail.startswith(lead), f"expected the profile lead:\n{lead!r}\ngot\n{detail!r}"
    _assert_no_conflict_and_no_branch_name("profile", lead, profile_path)


def test_neither_no_target_arm_names_a_conflict_or_a_branch(
    db_session: Session, initiative_child_ticket: Ticket, unnameable_branch_ticket: Ticket
):
    """AC12 on both class-B arms, on real inputs with no mock in either.

    Both reach the guard through a readable profile, so unlike the profile arm
    they can be driven in one test — and driving them together is the point: the
    two share one lead (AC3), so a fix that gave one of them its own wording
    fails here rather than passing on the arm that happened to be tested.
    """
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    for ticket in (initiative_child_ticket, unnameable_branch_ticket):
        workspace = db_session.get(Workspace, ticket.workspace_id)
        repo_root = resolve_ticket_root(db_session, ticket, workspace)
        detail = handoff_tree_status.describe_handoff_failure(db_session, ticket, conflict)

        lead = no_target_lead(ticket.external_id, repo_root)
        assert detail.startswith(lead), f"expected the no-target lead:\n{lead!r}\ngot\n{detail!r}"
        _assert_no_conflict_and_no_branch_name(ticket.external_id, lead, repo_root)


def test_the_single_sentence_split_cuts_both_new_leads_in_half(
    db_session: Session, long_profile_ticket: Ticket, long_worktree_ticket: Ticket
):
    """AC13 / R1, pinned as a test rather than left to a convention.

    `_lead()` here, `_first_sentence()` in `test_terminal_handoff_command.py`
    and the inline `head, _, _ = detail.partition(". ")` idiom in all three
    modules were written when every input composed one sentence. Two of the four
    leads are now two sentences, so all three extractors hand back the lead with
    its actionable half — `Fix {path}.`, `The worktree is {repo_root}.` — cut
    off. A cap or a path assertion written against that string passes while the
    real lead is wrong, and it is a string the operator never sees. This is the
    highest-risk item in the change, which is why it gets an assertion of its
    own rather than a comment.

    So: the split's answer and the real lead disagree, on both new arms. The way
    to pin a lead is `detail.startswith(expected)` against an expected string
    built from the identifiers; the helpers stay for input class D (AC5), whose
    lead is genuinely one sentence.
    """
    conflict = ValueError("CONFLICT (content): shared.txt")
    profile_path = _profile_path(db_session, long_profile_ticket)
    workspace = db_session.get(Workspace, long_worktree_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, long_worktree_ticket, workspace)

    profile_detail = handoff_tree_status.describe_handoff_failure(
        db_session, long_profile_ticket, conflict
    )
    with mock.patch.object(
        handoff_tree_status,
        "target_branch_name",
        side_effect=TargetBranchError("integration/LG-MS does not exist"),
    ):
        no_target_detail = handoff_tree_status.describe_handoff_failure(
            db_session, long_worktree_ticket, conflict
        )

    for label, detail, lead in (
        ("profile", profile_detail, profile_lead(LONGEST_EXTERNAL_ID, profile_path)),
        ("no-target", no_target_detail, no_target_lead(LONGEST_EXTERNAL_ID, repo_root)),
    ):
        assert detail.startswith(lead), f"{label}: expected\n{lead!r}\ngot\n{detail!r}"
        half = _lead(detail)
        assert half != lead, (
            f"{label}: the single-`. ` split now returns the whole lead, so this test "
            "has stopped measuring anything — which means one of the two new leads "
            "became one sentence and AC2/AC3's wordings need re-reading, not this "
            "assertion deleting"
        )
        assert lead.startswith(half), (
            f"{label}: the split returns something that is not even a prefix of the "
            f"lead, so the trap this pins has changed shape: {half!r}"
        )


# --- lg-milestone-that-1237, test-break: four mutants the suite above survives -
#
# Each test below was written against a reference implementation of the four
# arms and then re-run against one mutation of it. The mutation is named in the
# docstring; without the test it is green across all four modules.


@pytest.fixture(name="worktree_ticket")
def worktree_ticket_fixture(db_session: Session, repo: Path) -> Ticket:
    """A ticket whose stages already cut it a worktree of its own.

    Nothing else in this module has one, and until it does `resolve_ticket_root`
    and `Path(workspace.repo_path)` are the same string in every fixture here —
    so the sentence can name either and no test can tell. AC3 says which:
    "`{repo_root}` is the `resolve_ticket_root` value already computed above the
    `try`". That is the tree the operator would run the stage in; the workspace
    root is a checkout holding somebody else's branch.
    """
    workspace = Workspace(
        slug=f"handoff-worktree-{uuid4()}", name="Handoff worktree", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    ticket = make_agent_stage_ticket(
        db_session, workspace, external_id=LONGEST_EXTERNAL_ID, stage_key="work"
    )
    worktree = cut_ticket_worktree(db_session, workspace, ticket, repo)
    assert resolve_ticket_root(db_session, ticket, workspace) == worktree, (
        "fixture: the ticket's tree is not the one the sentence has to name"
    )
    assert worktree != Path(workspace.repo_path), (
        "fixture: the two candidate paths are identical, so this fixture distinguishes nothing"
    )
    return ticket


def test_the_no_target_lead_names_the_ticket_s_tree_not_the_workspace_checkout(
    db_session: Session, worktree_ticket: Ticket
):
    """AC3. Mutant: `repo_root = Path(workspace.repo_path)` instead of the tree read.

    Survives every other test in all four modules, because no fixture anywhere
    gives a ticket a worktree — so the two paths are the same string and the
    sentence can name the wrong one undetectably. It is not a cosmetic
    difference: AC3's action is "run the stage supervised" *in the named tree*,
    and the workspace checkout is the one place that stage must not run, since
    it holds whatever branch somebody else left on it.
    """
    workspace = db_session.get(Workspace, worktree_ticket.workspace_id)
    worktree = resolve_ticket_root(db_session, worktree_ticket, workspace)
    failure = handoff_tree_status.HandoffTreePreparationError(
        f"Could not prepare the tree for {worktree_ticket.external_id}: "
        "integration/LG-MS does not exist"
    )

    with mock.patch.object(
        handoff_tree_status,
        "target_branch_name",
        side_effect=TargetBranchError("integration/LG-MS does not exist"),
    ):
        detail = handoff_tree_status.describe_handoff_failure(db_session, worktree_ticket, failure)

    assert detail == f"{no_target_lead(LONGEST_EXTERNAL_ID, worktree)} {failure}", (
        f"the lead does not name the tree the stage would run in: {detail!r}"
    )
    assert not detail.startswith(no_target_lead(LONGEST_EXTERNAL_ID, Path(workspace.repo_path))), (
        f"the lead names the workspace checkout, which holds another branch and is "
        f"the one tree this stage must not be run in: {detail!r}"
    )


def test_the_profile_lead_names_the_file_the_error_carries_and_no_other(
    db_session: Session, worktree_ticket: Ticket, tmp_path: Path
):
    """AC2. Mutant: the profile path recomputed, rather than read off the error.

    `orchestration_dir() / f"{workspace.slug}.yaml"` is the obvious recompute
    and it is green against every profile fixture in this suite — but
    `resolve_orchestration_profile` tries three candidates in order
    (`orchestration_profile.py:365-377`), so the file that actually failed to
    parse is routinely `default.yaml` or the `orchestration_profile_slug`
    override. The recompute then names a path that does not exist, in the one
    sentence whose whole job is "the file to edit, named absolutely" (AC14).

    Driven through a `ProfileUnreadableError` whose `path` is **no** candidate
    of this workspace's, so only reading the attribute can produce it. The tree
    is a real worktree as well, so the arm is also pinned against naming either
    path it has in hand (AC2: exactly one absolute path, and it is the
    profile's).
    """
    workspace = db_session.get(Workspace, worktree_ticket.workspace_id)
    worktree = resolve_ticket_root(db_session, worktree_ticket, workspace)
    profile_path = padded_dir(tmp_path, LONGEST_PROFILE_PATH, stem="elsewhere") / "default.yaml"
    unreadable = ProfileUnreadableError(profile_path, "mapping values are not allowed here")
    assert workspace.slug not in str(unreadable.path), (
        "fixture: a recompute from the slug could reach this path by coincidence"
    )
    failure = handoff_tree_status.HandoffTreePreparationError(
        f"Could not prepare the tree for {worktree_ticket.external_id}: {unreadable}"
    )

    with mock.patch.object(handoff_tree_status, "target_branch_name", side_effect=unreadable):
        detail = handoff_tree_status.describe_handoff_failure(db_session, worktree_ticket, failure)

    lead = profile_lead(LONGEST_EXTERNAL_ID, unreadable.path)
    assert detail == f"{lead} {failure}", (
        f"the profile the operator has to fix is not the one the error carries:\n"
        f"{lead!r}\ngot\n{detail!r}"
    )
    assert str(worktree) not in lead and str(workspace.repo_path) not in lead, (
        f"the profile arm names a tree as well, and nothing in a tree fixes malformed YAML: {lead}"
    )


def test_the_lead_and_the_cause_are_separated_by_exactly_one_space_on_every_arm(
    db_session: Session,
    initiative_child_ticket: Ticket,
    real_initiative: Ticket,
    configured_base_ticket: Ticket,
):
    """AC6. Mutant: `f"{lead}  {exc}"`, or `f"{lead}{exc}"`, on one arm.

    Class B and class C are asserted with `startswith` plus `endswith`
    elsewhere, and both spellings satisfy that pair — the first renders a
    double space the toast shows, the second runs the git output into the
    sentence's full stop. Equality over all three is the whole assertion, and
    it is cheaper than three more near-identical tests.
    """
    for ticket in (initiative_child_ticket, real_initiative, configured_base_ticket):
        failure = handoff_tree_status.HandoffTreePreparationError(
            f"Could not prepare the tree for {ticket.external_id}: a cause with words"
        )
        detail = handoff_tree_status.describe_handoff_failure(db_session, ticket, failure)
        head, sep, tail = detail.rpartition(str(failure))
        assert sep and not tail, f"{ticket.external_id}: the cause is not last: {detail!r}"
        assert head.endswith(". ") and not head.endswith(".  "), (
            f"{ticket.external_id}: the lead and the cause are not separated by exactly "
            f"one space: {detail!r}"
        )


def test_a_cause_with_no_text_leaves_no_trailing_space_on_any_arm(
    db_session: Session,
    initiative_child_ticket: Ticket,
    unnameable_branch_ticket: Ticket,
    real_initiative: Ticket,
    configured_base_ticket: Ticket,
):
    """AC6. Mutant: the `.strip()` dropped from `f"{lead} {exc}".strip()`.

    It is in the criterion verbatim and nothing measures it, because every
    other test passes an exception with text. An empty one is not hypothetical:
    `cli.py:572` carries `str(exc)` byte-identical for the unclassified arm, and
    `test_an_entirely_empty_detail_still_records_a_readable_decision`
    (`test_external_handoff_settlement.py`) exists because a detail with no
    text reaches the settlement path today. A dropped `.strip()` puts a
    trailing space on the 400 `detail`, on the harness's `isError` text and on
    the decision a person reads.
    """
    for ticket in (
        initiative_child_ticket,
        unnameable_branch_ticket,
        real_initiative,
        configured_base_ticket,
    ):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, ticket, handoff_tree_status.HandoffTreePreparationError("")
        )
        assert detail == detail.strip(), (
            f"{ticket.external_id}: a cause with no text left whitespace on the end of "
            f"the sentence: {detail!r}"
        )
        assert detail.endswith("."), (
            f"{ticket.external_id}: the sentence does not end in its own full stop: {detail!r}"
        )


def test_a_target_that_resolves_to_nothing_keeps_today_s_placeholder(
    db_session: Session, configured_base_ticket: Ticket
):
    """AC5. Mutant: `{target_branch}` without today's `or 'its target branch'`.

    Input class D is "the read succeeded", and a read can succeed with an empty
    answer: `target_branch_name` returns `resolve_orchestration_profile(...)
    .git.base_branch` for a ticket that is its own root, and `base_branch: ""`
    parses (verified — the field is a plain `str` with a default, with no
    minimum length). The placeholder is what stands in then, and AC5 says class
    D is today's lead byte for byte.

    AC4 deletes the same placeholder from the class-C lead, so this is the one
    remaining input that may produce it — which is exactly how a tidy-up that
    deletes it everywhere goes unnoticed: the only test naming the string
    asserts it is **gone**.
    """
    workspace = db_session.get(Workspace, configured_base_ticket.workspace_id)
    repo_root = resolve_ticket_root(db_session, configured_base_ticket, workspace)
    failure = handoff_tree_status.HandoffTreePreparationError(
        "CONFLICT (content): Merge conflict in shared.txt"
    )

    with mock.patch.object(handoff_tree_status, "target_branch_name", return_value=""):
        detail = handoff_tree_status.describe_handoff_failure(
            db_session, configured_base_ticket, failure
        )

    assert detail == f"{conflict_lead(TICKET, 'its target branch', repo_root)} {failure}", (
        f"an empty resolved target left the lead naming no branch at all: {detail!r}"
    )


# --- AC5, AC6: the class-C arm asks for no NULL primary key -------------------
#
# `describe_handoff_failure` opened with `session.get(Workspace,
# ticket.workspace_id)`, and class C is reached two ways: a ticket whose
# workspace row is gone (a real id, no row) and an **initiative**, whose
# `workspace_id` is NULL by construction —
# `CheckConstraint("(workspace_id IS NULL) = (work_item_type = 'initiative')",
# name="ck_tickets_workspace_binding")`, tables.py:139. The second passed `None`
# as the primary key, which SQLAlchemy answers with `SAWarning: fully NULL
# primary key identity cannot load any object. This condition may raise an
# error in a future release.` — so the arm that exists to compose a lead when
# there is no workspace rested on a lookup SQLAlchemy reserves the right to turn
# into an exception, on the error path, where raising means the operator gets a
# 500 instead of the refusal this whole lineage exists to give them.
#
# These two are not interchangeable and neither is the module marker above.
# The first asserts the behaviour (no warning, same lead) in the shape
# `tests/test_initiative_management.py:63`
# `test_reading_an_initiative_raises_no_null_key_warning` already uses for the
# first instance of this same defect. The second says AC3's words literally —
# "`session.get(Workspace, ...)` is not called at all" — because a warning's
# absence is a proxy for that and not the thing itself: a future SQLAlchemy that
# simply drops the warning would leave the first test green over the same call.


def _null_key_warnings(caught) -> list[str]:
    return [str(w.message) for w in caught if "NULL primary key" in str(w.message)]


def _lookup_pair(call) -> tuple[object, object]:
    """The `(entity, ident)` a recorded `Session.get` call asked for.

    Read off both spellings rather than `call.args[:2]`, so a fix that passes
    the primary key by keyword is measured and not silently excused.
    """
    entity = call.args[0] if call.args else call.kwargs.get("entity")
    ident = call.args[1] if len(call.args) > 1 else call.kwargs.get("ident")
    return entity, ident


def test_a_real_initiative_reaches_the_class_c_lead_with_no_null_key_warning(
    db_session: Session, real_initiative: Ticket
):
    """AC5. The fixed behaviour, asserted directly rather than as an `xfail`.

    The recording block is deliberate and is not made redundant by the module
    marker: `catch_warnings` + `simplefilter("always")` replaces the filter set
    for the duration, so the SAWarning is *collected* here instead of raised.
    That is the difference between this test failing with its own sentence —
    naming the warning text and the call that emitted it — and failing with a
    bare `SAWarning` propagating out of production code, which reads as a
    harness problem. It also means this assertion keeps working if AC8's marker
    is ever removed, which is the point of having both.
    """
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        detail = handoff_tree_status.describe_handoff_failure(db_session, real_initiative, conflict)

    assert not _null_key_warnings(caught), (
        "the class-C arm looked a NULL primary key up in the database — the "
        "behaviour SQLAlchemy says it may turn into an exception, on the arm "
        f"that composes a refusal: {_null_key_warnings(caught)}"
    )
    lead = no_target_lead(real_initiative.external_id, None)
    assert detail == f"{lead} {conflict}", (
        f"the arm must still be taken and still compose the same lead — it is "
        f"byte-pinned by lg-milestone-that-1237's oracle:\n{lead!r}\ngot\n{detail!r}"
    )


def test_the_class_c_arm_issues_no_primary_key_lookup_for_an_initiative(
    db_session: Session, real_initiative: Ticket
):
    """AC6. AC3's words, literally: `session.get` is not called at all.

    The warning's absence above is a proxy for this; this is the claim. The
    session's own `get` is wrapped rather than `sqlmodel.Session.get` patched,
    so production runs unchanged and the recording cannot alter what the lookup
    would have returned.

    The `catch_warnings` block is here for the same reason as above: without it
    AC8's marker turns the pre-fix SAWarning into an exception and this test
    fails on the raise rather than on the pair it is here to name.
    """
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with mock.patch.object(db_session, "get", wraps=db_session.get) as session_get:
            detail = handoff_tree_status.describe_handoff_failure(
                db_session, real_initiative, conflict
            )

    null_key_lookups = [
        call for call in session_get.call_args_list if _lookup_pair(call) == (Workspace, None)
    ]
    assert not null_key_lookups, (
        f"`session.get(Workspace, None)` was issued {len(null_key_lookups)} time(s) on "
        f"the initiative path — `ticket.workspace_id` is NULL by construction there "
        f"(ck_tickets_workspace_binding), so the question the arm means is whether "
        f"there is a workspace id at all, asked before the database is asked to "
        f"look one up: {session_get.call_args_list}"
    )
    assert detail == f"{no_target_lead(real_initiative.external_id, None)} {conflict}", (
        f"the arm is still class C and the lead is unchanged: {detail!r}"
    )


def test_a_falsy_but_real_workspace_id_is_still_looked_up(db_session: Session, repo: Path):
    """AC2's guard spells `is not None`, and this is the input that tells them apart.

    The two spellings agree on every value but the empty string, which is falsy
    and is nevertheless a legal non-NULL primary key: `workspace_id` is a plain
    `str | None` column (tables.py:164) and `ck_tickets_workspace_binding`
    constrains only the NULL/initiative pair. Under truthiness this ticket's
    workspace is never looked up, and a workspace that exists — with a tree the
    operator could have opened — is reported absent.

    It is also the **positive control** for the wrapper the AC6 test measures
    with: that test asserts an absence, so it passes if the wrapper watches a
    `get` production never reaches. Here the same wrapper on the same argument
    must *record* the lookup.
    """
    workspace = Workspace(
        id="",
        slug=f"falsy-id-{uuid4()}",
        name="A workspace whose id is the empty string",
        repo_path=str(repo),
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    assert workspace.id == "", (
        "fixture: the empty-string primary key did not survive the insert, so "
        "this input cannot tell the two spellings of the guard apart"
    )

    ticket = make_agent_stage_ticket(
        db_session, workspace, external_id=LONGEST_EXTERNAL_ID, stage_key="work"
    )
    assert ticket.workspace_id == "", (
        f"fixture: the ticket must carry the falsy id itself: {ticket.workspace_id!r}"
    )
    repo_root = resolve_ticket_root(db_session, ticket, workspace)
    conflict = ValueError("CONFLICT (content): Merge conflict in shared.txt")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with mock.patch.object(db_session, "get", wraps=db_session.get) as session_get:
            detail = handoff_tree_status.describe_handoff_failure(db_session, ticket, conflict)

    assert (Workspace, "") in [_lookup_pair(call) for call in session_get.call_args_list], (
        f"the workspace was never looked up, so the guard asked whether the id is "
        f"truthy rather than whether it is NULL — `''` is a real row's primary key: "
        f"{session_get.call_args_list}"
    )
    assert str(repo_root) in detail, (
        f"the workspace exists and has a tree, so this is not the class-C arm; a "
        f"lead naming no tree means the lookup was skipped: {detail!r}"
    )
    assert detail != f"{no_target_lead(ticket.external_id, None)} {conflict}", (
        f"a real workspace was reported as absent: {detail!r}"
    )
    # Not the point of the test, but free: the real lookup emits no NULL-key
    # warning either, which is what makes `""` a legal key rather than a second
    # spelling of NULL.
    assert not _null_key_warnings(caught), (
        f"a non-NULL primary key was reported as a NULL identity: {_null_key_warnings(caught)}"
    )


#: AC9's six sites, named here because AC12 allows exactly one test module and
#: this is it. Each is reachable with an initiative for the same reason the
#: class-C arm is: the id is NULL by construction, not by accident.
GUARDED_WORKSPACE_LOOKUP_MODULES = (
    "handoff_tree_status.py",
    "handoff_writer.py",
    "external_harness.py",
    "stage_retry_budget.py",
    "parallel_stage.py",
    "stage_fanout_service.py",
)

_BARE_WORKSPACE_LOOKUP = "session.get(Workspace, ticket.workspace_id)"
_ACCESSOR_CALL = "workspace_for_ticket("


@pytest.mark.parametrize("module_name", GUARDED_WORKSPACE_LOOKUP_MODULES)
def test_every_lineage_workspace_lookup_goes_through_the_accessor(module_name: str):
    """AC9, re-pointed by lg-milestone-that-1418 at where the guard now lives.

    Each sibling site is followed immediately by an absent-workspace branch, so
    the guard changes no behaviour there and a behavioural test over one would
    pass identically with and without it — green, and proving nothing. But the
    claim AC9 makes is about the *call*: on an initiative, no primary key
    lookup is issued. That is readable off the source, so skipping one of the
    six is red rather than a reviewer's catch.

    What changed: AC9's fix was six inline `... if ticket.workspace_id is not
    None else None` ternaries, and lg-milestone-that-1418 found 58 more sites
    with the same defect under other receiver names. It moved the read to one
    accessor, `services.ticket_workspace_binding.workspace_for_ticket`, these
    six with it (the canonical idiom left inlined anywhere is a template for
    the next copy), so the assertion inverts: the raw lookup must be **gone**
    from each module and the accessor called instead. The package-wide version
    of the claim, spelled against the expression shape and over every receiver
    name, is `tests/test_ticket_workspace_one_answer.py`.
    """
    source = (Path(handoff_tree_status.__file__).parent / module_name).read_text(encoding="utf-8")
    collapsed = " ".join(source.split())

    assert _ACCESSOR_CALL in collapsed, (
        f"services/{module_name} calls no `{_ACCESSOR_CALL}...)`. If the site moved or was "
        f"renamed, this oracle is now watching nothing: re-point it or delete it, but do "
        f"not leave it green over an absence."
    )
    bare = collapsed.count(_BARE_WORKSPACE_LOOKUP)
    assert bare == 0, (
        f"services/{module_name} holds {bare} `Workspace` lookup(s) keyed inline on "
        f"`ticket.workspace_id`, asking the database to load a row by a primary key that "
        f"is NULL for every initiative (ck_tickets_workspace_binding, tables.py:139) — "
        f"SAWarning today, an exception in a future SQLAlchemy. One module owns that read: "
        f"call `workspace_for_ticket(session, ticket)` from "
        f"`services/ticket_workspace_binding.py`, whose guard is spelled `is not None` and "
        f"not truthiness, because `''` is a legal non-NULL key."
    )
