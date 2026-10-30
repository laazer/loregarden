"""`base_branch` has one answer: the workspace profile's (lg-milestone-that-1228).

`OVERRIDABLE` was `frozenset(GitAutomationConfig.model_fields)`, so `base_branch`
became per-ticket overridable because it is a field of the config and not because
anyone decided a ticket may pick its own base. Three spellings resolved it:
`resolve_orchestration_profile(ws).git.base_branch` (nine reads, override ignored),
`resolve_git_automation(ws, ticket).base_branch` (seven reads, override honoured at
two modules), and `config.base_branch` off a config the caller already held. The
tree's publish base and the ticket's own PR base already disagreed for such a
ticket.

Direction (b): the override layer stops carrying it, and every resolution outside
`services/git_automation_config.py` goes through one accessor, `base_branch(workspace)`.
The field stays on the model and keeps the profile's value.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
from pathlib import Path

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.services import git_automation_config as gac_module
from loregarden.services import orchestration_profile as orchestration_profile_module
from loregarden.services.git_automation_config import (
    OVERRIDABLE,
    base_branch,
    parse_override,
    resolve_git_automation,
    serialize_override,
)
from loregarden.services.git_merge_noco import rev
from loregarden.services.github_pr_service import create_ticket_pull_request
from loregarden.services.land_ticket import LandSkip, land_ticket, landing_would_succeed
from loregarden.services.orchestration_profile import (
    GitAutomationConfig,
    resolve_orchestration_profile,
)
from loregarden.services.publish_ref import publish_ref_for
from loregarden.services.publish_tree import PublishOutcome, publish_tree
from loregarden.services.target_branch import (
    integration_branch_for,
    resolve_target_branch,
    target_branch_name,
)
from loregarden.services.tree_publish_scheduler import (
    close_published_cards,
    publish_card_title,
    report_publish_failure,
)
from sqlmodel import Session, select
from tests.worktree_helpers import commit_on, git, make_repo, make_tree_ticket

LOGGER_NAME = "loregarden.services.git_automation_config"

#: The workspace profile's base, and the base a ticket tries to substitute for it.
#: Both differ from the model default so no assertion below can pass by accident.
PROFILE_BASE = "release/x"
ROGUE_BASE = "rogue/y"
ROGUE_OVERRIDE = json.dumps({"base_branch": ROGUE_BASE})

# --- AC3: the detector ------------------------------------------------------
#
# Specified on the ticket exactly, so it is not re-invented per reader: an `ast`
# walk that taints the locals bound to either resolver, then flags every
# `base_branch` read off a resolver call or a tainted name. A grep for
# `.git.base_branch` cannot do this job — it sees only the nine profile-direct
# reads and would have gone green with the override-honouring spelling intact.

RESOLVERS = frozenset({"resolve_orchestration_profile", "resolve_git_automation"})
#: The module that owns the answer, and so the one place allowed to read it.
ACCESSOR_MODULE = Path("services/git_automation_config.py")
#: The detector scans the whole package; a scan that found nothing because it
#: looked nowhere is the failure mode this floor exists to catch.
MIN_FILES_SCANNED = 100


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _calls_a_resolver(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Call) and _callee_name(sub) in RESOLVERS for sub in ast.walk(node)
    )


def _scope_body(scope: ast.AST) -> list[ast.AST]:
    """Every node in ``scope`` that is not inside a nested function scope."""
    nodes: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
        return [node.target]
    return []


def resolver_base_branch_reads(source: str, label: str) -> list[str]:
    """Every `base_branch` read that resolves a base, as ``label:lineno``."""
    tree = ast.parse(source)
    scopes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    reads: list[str] = []
    for scope in scopes:
        nodes = _scope_body(scope)
        tainted: set[str] = set()
        for node in nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            if node.value is None or not _calls_a_resolver(node.value):
                continue
            tainted.update(
                target.id for target in _assignment_targets(node) if isinstance(target, ast.Name)
            )
        for node in nodes:
            if not isinstance(node, ast.Attribute) or node.attr != "base_branch":
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            value = node.value
            resolved_here = _calls_a_resolver(value)
            resolved_above = isinstance(value, ast.Name) and value.id in tainted
            if resolved_here or resolved_above:
                reads.append(f"{label}:{node.lineno}")
    return reads


def _package_root() -> Path:
    return Path(gac_module.__file__).resolve().parent.parent


def _scan_package() -> tuple[list[str], int]:
    root = _package_root()
    reads: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative == ACCESSOR_MODULE:
            continue
        scanned += 1
        reads.extend(resolver_base_branch_reads(path.read_text(encoding="utf-8"), str(relative)))
    return reads, scanned


def test_the_detector_sees_a_resolver_read_and_is_therefore_not_vacuous():
    """A zero may be the detector. These three sources prove it is not."""
    direct = resolver_base_branch_reads(
        "def f(ws):\n    return resolve_orchestration_profile(ws).git.base_branch\n", "inline"
    )
    assert len(direct) == 1, f"the profile-direct spelling went unseen: {direct}"

    bound = resolver_base_branch_reads(
        "def f(ws, t):\n"
        "    config = resolve_git_automation(ws, t)\n"
        "    return config.base_branch\n",
        "inline",
    )
    assert len(bound) == 1, f"the override-honouring spelling went unseen: {bound}"

    held = resolver_base_branch_reads("def f(config):\n    return config.base_branch\n", "inline")
    assert held == [], "a config the caller already holds resolves nothing and is permitted"


def test_the_detector_scans_the_whole_package():
    _, scanned = _scan_package()
    assert scanned > MIN_FILES_SCANNED, (
        f"only {scanned} files scanned: the zero below would be the detector, not the code"
    )


def test_no_module_outside_the_accessor_resolves_a_base_branch_of_its_own():
    reads, _ = _scan_package()
    assert reads == [], (
        "these reads resolve a base branch outside services/git_automation_config.py, so "
        "the answer can differ by which function the caller reached; they belong on the "
        f"`base_branch(workspace)` accessor: {reads}"
    )


# --- AC4: the override layer stops carrying it ------------------------------


def test_base_branch_is_not_overridable():
    assert "base_branch" not in OVERRIDABLE
    assert "base_branch" in GitAutomationConfig.model_fields, (
        "the field stays on the model and keeps the profile's value; only the "
        "override layer stops carrying it"
    )


def test_overridable_is_the_model_fields_minus_the_deliberate_exclusions():
    """A field added to the model is overridable by default.

    `OVERRIDABLE` was derived — `frozenset(GitAutomationConfig.model_fields)` — and
    that derivation is why `base_branch` was overridable at all. A hand-listed set
    would fix this one case and silently exclude the next field anyone adds, so the
    shape has to stay a subtraction with only the exclusions spelled out.
    """
    excluded = frozenset(GitAutomationConfig.model_fields) - OVERRIDABLE
    assert excluded == frozenset({"base_branch"}), (
        f"the deliberate exclusions are not exactly {{base_branch}}: {sorted(excluded)}"
    )

    [assignment] = [
        node
        for node in ast.parse(Path(gac_module.__file__).read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "OVERRIDABLE" for target in node.targets
        )
    ]
    value = assignment.value
    assert isinstance(value, ast.BinOp) and isinstance(value.op, ast.Sub), (
        "OVERRIDABLE must be written as a subtraction from the model's fields, not "
        "as a hand-listed set: a field added to the model has to be overridable by default"
    )
    assert "model_fields" in ast.unparse(value.left), (
        f"the subtraction does not start from the model's fields: {ast.unparse(value)}"
    )
    assert isinstance(value.right, ast.Name), (
        "the exclusions must be a named set, so the warning in AC7 has a set to "
        f"iterate rather than a literal inlined here: {ast.unparse(value)}"
    )
    assert frozenset(getattr(gac_module, value.right.id)) == frozenset({"base_branch"}), (
        f"{value.right.id} is not the deliberate-exclusion set"
    )


def test_parse_override_drops_base_branch_and_keeps_the_rest():
    stored = '{"base_branch": "release/x", "commit": true}'
    assert parse_override(stored) == {"commit": True}


def test_serialize_override_stores_nothing_for_a_base_branch_only_override():
    assert serialize_override({"base_branch": PROFILE_BASE}) == ""


def test_a_stored_base_branch_does_not_round_trip():
    stored = json.dumps({"base_branch": ROGUE_BASE, "push": True})
    again = serialize_override(parse_override(stored))
    assert "base_branch" not in again
    assert json.loads(again) == {"push": True}, "the keys that are overridable still survive"


# --- AC7: the drop is not silent -------------------------------------------
#
# PATCH /api/tickets/{id} with a `git_automation` body still returns 200, and a
# `base_branch` key in it stops being stored. The module already warns for an
# override it refuses to trust (an unparseable one, a non-object one); a key it
# now refuses gets the same treatment, at the same level, for the same reason.


def _warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == LOGGER_NAME
    ]


@pytest.mark.parametrize("call", [parse_override, serialize_override])
def test_dropping_base_branch_warns_and_names_it(caplog, call):
    payload = {"base_branch": PROFILE_BASE}
    argument = json.dumps(payload) if call is parse_override else payload

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        call(argument)

    [message] = _warnings(caplog)
    assert "base_branch" in message, f"the dropped key is not named: {message}"
    assert "workspace" in message.lower(), (
        f"the warning does not say where the setting does belong: {message}"
    )


@pytest.mark.parametrize("call", [parse_override, serialize_override])
def test_a_key_that_is_not_a_field_at_all_keeps_its_silent_drop(caplog, call):
    """Pre-existing behaviour for a stale client, and out of scope here.

    The warning is scoped to the deliberate exclusions — keys that *are* fields of
    the config and were overridable yesterday. Widening it to anything unknown is a
    different change, and it would make every stale client's typo an operator
    message.
    """
    payload = {"launch_missiles": True}
    argument = json.dumps(payload) if call is parse_override else payload

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        call(argument)

    assert _warnings(caplog) == []


# --- AC8: the accessor ------------------------------------------------------


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="profile_base")
def profile_base_fixture(tmp_path, monkeypatch) -> str:
    """A workspace profile whose `git.base_branch` is its own, not the default.

    Every assertion here measures the profile's base against a ticket's rogue one.
    Left on the model default both the right answer and the built-in fallback are
    "main", and a test comparing "main" to "main" proves nothing.
    """
    orchestration = tmp_path / "orchestration"
    orchestration.mkdir()
    (orchestration / "default.yaml").write_text(
        "slug: default\n"
        "name: Default\n"
        "git:\n"
        f"  base_branch: {PROFILE_BASE}\n"
        "  commit: true\n"
        "  push: true\n"
        "  open_pr: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestration_profile_module, "orchestration_dir", lambda: orchestration)
    assert PROFILE_BASE != GitAutomationConfig().base_branch, (
        "fixture: the configured base must differ from the built-in default"
    )
    return PROFILE_BASE


@pytest.fixture(name="repo")
def repo_fixture(tmp_path, profile_base):
    """A repo whose base branch is the profile's, with a rogue branch beside it.

    `ROGUE_BASE` exists and holds the child's work already, so the two bases give
    opposite answers at every site below: a publish against the profile's base has
    work to do, and one against the rogue base would find nothing to add and skip.
    """
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    git(repo, "branch", profile_base, "main")
    git(repo, "branch", ROGUE_BASE, "main")
    git(repo, "push", "-q", "origin", profile_base, ROGUE_BASE)
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo, profile_base):
    ws = Workspace(slug="one-answer", name="one answer", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    assert resolve_orchestration_profile(ws).git.base_branch == profile_base, (
        "fixture: default.yaml is not the profile that resolved, so the configured "
        "base and the built-in default are still the same string"
    )
    return ws


@pytest.fixture(name="rogue_ticket")
def rogue_ticket_fixture(session, workspace) -> Ticket:
    ticket = Ticket(
        external_id="LG-ROGUE",
        workspace_id=workspace.id,
        title="A ticket that tried to pick its own base",
        branch="loregarden/lg-rogue",
        git_automation_json=ROGUE_OVERRIDE,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def test_the_accessor_takes_no_ticket():
    """AC2's decision, expressed in the signature.

    A ticket parameter here is the whole defect: it is what let two callers
    resolve two different bases for one repository.
    """
    parameters = list(inspect.signature(base_branch).parameters)
    assert parameters == ["workspace"], f"the accessor takes more than a workspace: {parameters}"


def test_all_three_spellings_give_the_profiles_base(session, workspace, rogue_ticket):
    answers = {
        "accessor": base_branch(workspace),
        "resolve_git_automation": resolve_git_automation(workspace, rogue_ticket).base_branch,
        "profile": resolve_orchestration_profile(workspace).git.base_branch,
    }
    assert set(answers.values()) == {PROFILE_BASE}, f"the spellings disagree: {answers}"
    assert ROGUE_BASE not in answers.values()


# --- AC11: stored values are ignored, not cleared ---------------------------


def test_reading_a_stored_base_branch_leaves_it_in_the_column(session, workspace, rogue_ticket):
    """No migration: ignore-at-read is idempotent and also covers a row a stale
    client writes tomorrow, which a one-time rewrite would not."""
    assert resolve_git_automation(workspace, rogue_ticket).base_branch == PROFILE_BASE

    session.expire_all()
    assert session.get(Ticket, rogue_ticket.id).git_automation_json == ROGUE_OVERRIDE


# --- AC10: the behaviour change, at the two sites that had one --------------
#
# `publish_tree` and `tree_publish_scheduler` both pass a ticket to
# `resolve_git_automation`, so both honoured a per-ticket base yesterday while
# `land_ticket` and `github_pr_service` ignored it. Zero tickets carry the key, so
# the practical delta is nil — but it is still a behaviour change, and these are
# the only two places it can be observed.


def _landed_rogue_tree(session, workspace, repo):
    """A milestone whose child has landed, and which claims its own base.

    The child's work is also already on `ROGUE_BASE`, so a publish that read the
    ticket's base would find nothing to add and skip.
    """
    ms = make_tree_ticket(
        session,
        workspace,
        "LG-MS",
        kind=WorkItemType.MILESTONE,
        git_automation_json=ROGUE_OVERRIDE,
    )
    child = make_tree_ticket(session, workspace, "LG-C1", parent=ms)
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    git(repo, "branch", child.branch, target)
    commit_on(repo, child.branch, "c1.txt", "child work\n")
    assert land_ticket(session, child, workspace).ok
    commit_on(repo, ROGUE_BASE, "c1.txt", "child work\n")
    return ms, target


def _pending_cards(session, root) -> list[str]:
    session.expire_all()
    return [
        approval.title
        for approval in session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
            )
        ).all()
    ]


def test_the_tree_publishes_onto_the_profiles_base_not_the_tickets(session, workspace, repo):
    ms, target = _landed_rogue_tree(session, workspace, repo)

    outcome = publish_tree(session, ms, workspace)

    assert outcome.ok and not outcome.skipped, (
        "the publish found nothing to add, which is the answer the rogue base gives: "
        f"{outcome.detail or 'skipped'}"
    )
    # `publish_ref_for`, not the branch's own name: the cut is pushed as
    # `publish/<branch>` and never as the branch itself, which
    # `test_publish_tree._published` pins. Read from the helper rather than
    # spelled, so the pattern cannot go stale against it again
    # (lg-milestone-that-1419: as written this assertion could not be reached,
    # so the parents check below — the one that measures which base the tree
    # was cut from — never ran).
    ref = f"refs/heads/{publish_ref_for(target)}"
    published = git(repo, "ls-remote", "origin", ref).stdout.split()
    assert published, f"{target} was not published as {ref}"
    parents = git(repo, "rev-list", "--parents", "-n1", published[0]).stdout.split()[1:]
    assert parents == [rev(repo, f"refs/heads/{PROFILE_BASE}")], (
        "the commit that publishes the tree was not cut from the profile's base"
    )
    assert rev(repo, f"refs/heads/{ROGUE_BASE}") not in parents


def test_the_sweep_checks_the_profiles_base_for_a_superseding_commit(session, workspace, repo):
    """`close_published_cards` asks whether the base already holds the tree's work.

    `ROGUE_BASE` holds it and the profile's base does not, so a sweep that read the
    root's own base would close a card over work that never reached the base — the
    operator loses the only thing telling them the publish failed.
    """
    ms, target = _landed_rogue_tree(session, workspace, repo)
    report_publish_failure(
        session, ms, PublishOutcome(ok=False, branch=target, detail="boom", failed_step="push")
    )

    assert close_published_cards(session) == 0, (
        "a card was closed because the rogue base holds the work; the profile's base does not"
    )
    assert _pending_cards(session, ms) == [publish_card_title(ms)], (
        "the card that tells the operator the publish failed is gone"
    )


# --- AC3, adversarially: the specified detector has spellings it cannot see --
#
# The AC3 detector taints a local bound *directly* to a resolver call, and flags
# an attribute read off that local or off the call. That is the shape the 16 reads
# take today, and the shape a straight migration leaves behind — but it is not the
# only way to resolve a base. Five spellings satisfy the AC3 detector while still
# reading a resolver's answer:
#
#   profile = resolve_orchestration_profile(ws); g = profile.git; g.base_branch
#   self._config = resolve_git_automation(ws); … self._config.base_branch
#   getattr(resolve_orchestration_profile(ws).git, "base_branch")
#   resolve_git_automation(ws, t).model_dump()["base_branch"]
#   a closure reading the enclosing function's resolved config
#
# None is exotic: the first is what a reader writes when the line gets long, and
# the fourth is what anyone serialising the config writes. A detector that asserts
# a zero while five spellings pass through it is reporting its own reach.
#
# So the census below is written the other way round — permitted *by construction*
# rather than flagged by pattern. A `base_branch` read is allowed only where the
# config it reads was handed in (a parameter of an enclosing function, never
# rebound from a resolver in that scope) or is the documented model-default
# stand-in `GitAutomationConfig()`. Every other way of getting one is a violation,
# including the ways nobody has written yet.

#: The model-default stand-in `handoff_tree_status` uses inside its unreadable-profile
#: guard, where resolving would re-call the function that just raised.
MODEL_DEFAULT_CALL = "GitAutomationConfig"


def _parameter_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    arguments = function.args
    names = {
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }
    for collected in (arguments.vararg, arguments.kwarg):
        if collected is not None:
            names.add(collected.arg)
    return names


def _resolver_tainted(scope: ast.AST) -> set[str]:
    tainted: set[str] = set()
    for node in _scope_body(scope):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            continue
        if node.value is None or not _calls_a_resolver(node.value):
            continue
        tainted.update(
            target.id for target in _assignment_targets(node) if isinstance(target, ast.Name)
        )
    return tainted


def _is_handed_in(value: ast.AST, handed_in: set[str], tainted: set[str]) -> bool:
    """Whether the config being read is one the caller already held."""
    if isinstance(value, ast.Name):
        return value.id in handed_in and value.id not in tainted
    return isinstance(value, ast.Call) and _callee_name(value) == MODEL_DEFAULT_CALL


def _string_spelling(node: ast.AST) -> bool:
    """`getattr(x, "base_branch")`, and `…["base_branch"]` off a resolver or a dump.

    The attribute walk cannot see either, and both are ordinary things to write.
    A `"base_branch"` key in a payload a module *builds* is neither, and stays
    permitted — `branch_triage_service` reports git's own base ref under that name.
    """
    if (
        isinstance(node, ast.Call)
        and _callee_name(node) == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "base_branch"
    ):
        return True
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "base_branch"
        and (_calls_a_resolver(node.value) or "model_dump" in ast.unparse(node.value))
    )


def base_branch_reads_not_handed_in(source: str, label: str) -> list[str]:
    """Every `base_branch` read whose config was not handed to the reader."""
    tree = ast.parse(source)
    reads: list[str] = []

    def walk(scope: ast.AST, handed_in: set[str], tainted: set[str]) -> None:
        for node in ast.iter_child_nodes(scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(node, handed_in | _parameter_names(node), tainted | _resolver_tainted(node))
                continue
            attribute_read = (
                isinstance(node, ast.Attribute)
                and node.attr == "base_branch"
                and isinstance(node.ctx, ast.Load)
                and not _is_handed_in(node.value, handed_in, tainted)
            )
            if attribute_read or _string_spelling(node):
                reads.append(f"{label}:{node.lineno}  {ast.unparse(node)[:72]}")
            walk(node, handed_in, tainted)

    walk(tree, set(), _resolver_tainted(tree))
    return reads


def _all_base_branch_attribute_reads(source: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and node.attr == "base_branch"
        and isinstance(node.ctx, ast.Load)
    )


def _scan_package_for_reads_not_handed_in() -> tuple[list[str], int, int]:
    root = _package_root()
    reads: list[str] = []
    scanned = 0
    permitted = 0
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative == ACCESSOR_MODULE:
            continue
        scanned += 1
        source = path.read_text(encoding="utf-8")
        found = base_branch_reads_not_handed_in(source, str(relative))
        reads.extend(found)
        permitted += _all_base_branch_attribute_reads(source) - len(found)
    return reads, scanned, permitted


#: Five ways to read a resolver's base that the AC3 detector reports zero for.
EVASIONS = {
    "a second hop off the profile": (
        "def f(ws):\n"
        "    profile = resolve_orchestration_profile(ws)\n"
        "    git_config = profile.git\n"
        "    return git_config.base_branch\n"
    ),
    "a config kept on self": (
        "class A:\n"
        "    def load(self, ws):\n"
        "        self.config = resolve_git_automation(ws)\n"
        "    def read(self):\n"
        "        return self.config.base_branch\n"
    ),
    "the getattr spelling": (
        "def f(ws):\n    return getattr(resolve_orchestration_profile(ws).git, 'base_branch')\n"
    ),
    "a key off the dumped config": (
        "def f(ws, t):\n    return resolve_git_automation(ws, t).model_dump()['base_branch']\n"
    ),
    "a closure over the resolved config": (
        "def outer(ws):\n"
        "    config = resolve_git_automation(ws)\n"
        "    def inner():\n"
        "        return config.base_branch\n"
        "    return inner()\n"
    ),
}


@pytest.mark.parametrize("spelling", sorted(EVASIONS), ids=sorted(EVASIONS))
def test_a_base_branch_nobody_handed_in_is_a_violation_however_it_is_spelled(spelling):
    """The census must not be satisfiable by moving the read one hop away."""
    found = base_branch_reads_not_handed_in(EVASIONS[spelling], "inline")
    assert len(found) == 1, f"{spelling} resolves a base and went unseen: {found}"


@pytest.mark.parametrize(
    "source",
    [
        "def f(config):\n    return config.base_branch\n",
        "def f(*, config):\n    return config.base_branch\n",
        "def f():\n    return GitAutomationConfig().base_branch\n",
        "def f(repo_root, base_branch):\n    return read_branch_tips(repo_root, base_branch)\n",
        "def f(workspace):\n    return {'base_branch': git_base_ref(workspace)}\n",
    ],
    ids=["parameter", "keyword-only", "model-default", "a-parameter-of-that-name", "a-payload-key"],
)
def test_a_config_the_caller_already_held_is_permitted_by_construction(source):
    """Permitted because it cannot disagree with the profile, not by allowlist."""
    assert base_branch_reads_not_handed_in(source, "inline") == []


def test_no_module_reads_a_base_branch_it_was_not_handed():
    reads, scanned, permitted = _scan_package_for_reads_not_handed_in()
    assert scanned > MIN_FILES_SCANNED, (
        f"only {scanned} files scanned: the zero below would be the detector, not the code"
    )
    assert permitted > 0, (
        "no permitted `base_branch` read was found anywhere, so this census is reading a "
        "package with no such reads in it at all and proves nothing about the ones there were"
    )
    assert reads == [], (
        "these reads get a base branch from somewhere other than their own caller, so the "
        "answer can still differ by which function the caller reached; they belong on the "
        f"`base_branch(workspace)` accessor: {reads}"
    )


# --- AC7, adversarially: what must NOT warn, and what the warning is driven by


def _exclusion_set_name() -> str:
    """The name of the deliberate-exclusion set, read off the `OVERRIDABLE` line."""
    [assignment] = [
        node
        for node in ast.parse(Path(gac_module.__file__).read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "OVERRIDABLE" for target in node.targets
        )
    ]
    value = assignment.value
    assert isinstance(value, ast.BinOp) and isinstance(value.right, ast.Name), (
        f"OVERRIDABLE is not a subtraction of a named set: {ast.unparse(value)}"
    )
    return value.right.id


@pytest.mark.parametrize("payload", [{}, {"commit": True}, {"push": False, "open_pr": False}])
@pytest.mark.parametrize("call", [parse_override, serialize_override])
def test_an_override_the_module_still_honours_is_not_warned_about(caplog, call, payload):
    """A warning on every call is not a warning — the operator stops reading it.

    AC7 asks for one record naming a key that stopped being stored. An
    implementation that logs whenever it filters anything satisfies the landed
    assertion (`base_branch` is in the message it happens to emit) and buries the
    signal under one record per resolution of every ticket in the workspace.
    """
    argument = json.dumps(payload) if call is parse_override else payload

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        call(argument)

    assert _warnings(caplog) == []


#: Malformed overrides, none of them naming a key — the existing warnings echo the
#: raw payload, so a payload containing the word would not tell the two apart.
MALFORMED_OVERRIDES = ["", "{", "{'a': 1}", "null", "[]", '["x"]', '"x"', "3"]


@pytest.mark.parametrize("raw", MALFORMED_OVERRIDES, ids=repr)
def test_a_malformed_override_warns_about_itself_and_not_about_a_key(caplog, raw):
    """The two warnings this module already had must not grow a third, wrong one.

    An unparseable or non-object override carries no keys, so nothing was dropped
    for being excluded. A key named in such a record points the operator at
    something they did not send, and an exception raised instead of either warning
    would take the ticket's whole pipeline down on a stale client's typo.
    """
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        assert parse_override(raw) == {}

    messages = _warnings(caplog)
    assert len(messages) <= 1, f"one malformed override produced several records: {messages}"
    assert all("base_branch" not in message for message in messages), (
        f"a malformed override was reported as a dropped key: {messages}"
    )


def test_the_warning_is_driven_by_the_exclusion_set_and_not_a_hardcoded_key(caplog, monkeypatch):
    """Mutation: exclude a second field and the warning must follow.

    `if key == "base_branch"` passes every other assertion here. It also means the
    next field anyone excludes goes back to being dropped in silence, which is the
    behaviour AC7 exists to end — so the warning has to iterate the set, not the
    one key the set happens to hold today.
    """
    excluded = frozenset({"base_branch", "commit"})
    monkeypatch.setattr(gac_module, _exclusion_set_name(), excluded)
    monkeypatch.setattr(
        gac_module, "OVERRIDABLE", frozenset(GitAutomationConfig.model_fields) - excluded
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        assert parse_override(json.dumps({"commit": True, "push": True})) == {"push": True}

    [message] = _warnings(caplog)
    assert "commit" in message, (
        f"the warning names a key rather than the set it was told to exclude: {message}"
    )


# --- AC4/AC11, adversarially: types, spellings, mutation and determinism ----


@pytest.mark.parametrize(
    "value", [None, "", 0, False, [], {}, {"nested": ROGUE_BASE}, ["a", "b"], 3.5], ids=repr
)
def test_a_rogue_base_of_any_type_is_dropped_without_raising(caplog, value):
    """The column is written by hand through the API, so the value is not a string.

    `parse_override` is reached on every resolution of the ticket, which is the
    worst possible place for a `TypeError`: a stale client's bad value would stop
    the ticket's whole pipeline rather than being ignored.
    """
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        parsed = parse_override(json.dumps({"base_branch": value, "push": True}))
        stored = serialize_override({"base_branch": value, "push": True})

    assert parsed == {"push": True}
    assert json.loads(stored) == {"push": True}
    assert len(_warnings(caplog)) == 2, "one record per call, naming the key it dropped"


@pytest.mark.parametrize(
    "key",
    ["Base_Branch", "BASE_BRANCH", " base_branch", "base_branch ", "base-branch", "basebranch"],
)
def test_a_key_that_merely_looks_like_the_excluded_one_is_not_normalized_into_it(caplog, key):
    """No defensive normalization: a key that is not a field keeps its silent drop.

    Lower-casing or stripping the stored keys here would invent an override the
    operator never sent, and would also make the AC7 warning fire for a typo —
    which AC7 puts out of scope on purpose.
    """
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        assert parse_override(json.dumps({key: ROGUE_BASE})) == {}

    assert _warnings(caplog) == []


def test_serialize_override_does_not_mutate_the_callers_dict():
    """`ticket_manual_edit` holds the body it passed in and writes the ticket from it."""
    override = {"base_branch": ROGUE_BASE, "commit": True}

    serialize_override(override)

    assert override == {"base_branch": ROGUE_BASE, "commit": True}


def test_dropping_the_key_is_deterministic_across_repeated_reads(caplog):
    """Every stage transition re-resolves the same ticket; the answer cannot drift."""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        answers = [parse_override(ROGUE_OVERRIDE) for _ in range(3)]

    assert answers == [{}, {}, {}]
    assert len(_warnings(caplog)) == 3, "a dropped key is reported every time it is read"


def test_the_other_overridable_keys_survive_a_rogue_base(session, workspace):
    """The drop is one key, not the whole override.

    An implementation that refuses an override containing an excluded key would
    pass every assertion about `base_branch` and silently restore the workspace's
    flags for the one ticket in the live database that has an override at all.
    """
    ticket = Ticket(
        external_id="LG-ROGUE-PLUS",
        workspace_id=workspace.id,
        title="A rogue base beside a real override",
        branch="loregarden/lg-rogue-plus",
        git_automation_json=json.dumps({"base_branch": ROGUE_BASE, "push": False}),
    )
    session.add(ticket)
    session.commit()

    config = resolve_git_automation(workspace, ticket)

    assert config.base_branch == PROFILE_BASE
    assert config.push is False, "the override's own key was thrown out with the excluded one"
    assert resolve_orchestration_profile(workspace).git.push is True, (
        "fixture: the profile pushes, so `push is False` can only have come from the override"
    )


def test_an_override_of_only_a_base_branch_resolves_to_the_profiles_own_config(
    session, workspace, rogue_ticket
):
    """Nothing overridable remains, so the answer is the profile's config itself."""
    assert (
        resolve_git_automation(workspace, rogue_ticket)
        == resolve_orchestration_profile(workspace).git
    )


# --- AC9, adversarially: the sites that ignored the override still ignore it --
#
# AC10 proves the two sites whose behaviour changes. The other fourteen are
# proved only by the AST census, which cannot tell `base_branch(workspace)` from
# `base_branch(workspace)` wired to the wrong answer. Two of them decide whether
# the land path runs at all, and that comparison is AC2's reason (2): under
# direction (a) a ticket could set its base to its own target and walk through the
# guard. These assert it cannot, whatever the override says.


def test_a_standalone_tickets_rogue_base_does_not_become_its_landing_target(
    session, workspace, repo
):
    """`target == base` is what tells `land_ticket` there is nothing to land onto.

    Read the ticket's base here and the comparison comes apart: the target is the
    profile's base, the base reads `rogue/y`, and landing proceeds — merging the
    base branch into itself through the integration-branch path.
    """
    ticket = make_tree_ticket(session, workspace, "LG-SOLO", git_automation_json=ROGUE_OVERRIDE)
    git(repo, "branch", ticket.branch, PROFILE_BASE)
    commit_on(repo, ticket.branch, "solo.txt", "solo work\n")

    assert target_branch_name(session, ticket, workspace) == PROFILE_BASE
    assert landing_would_succeed(session, ticket, workspace) is False

    result = land_ticket(session, ticket, workspace)

    assert result.skipped is LandSkip.BASE_TARGET, (
        f"a ticket with no tree was landed onto {result.target!r}: {result.detail}"
    )
    assert result.target == PROFILE_BASE


def test_a_tree_members_pr_refusal_cannot_be_switched_off_by_an_override(session, workspace, repo):
    """AC2's reason (2), as a test: the override is not a switch on this guard.

    The refusal exists because a tree member's work reaches the base in its tree's
    PR, and a second PR for its own branch carries the same commits again
    (lg-durable-remote-336). The guard is `target == base`, so a ticket that could
    name its own target as the base would be let through — and the duplicate PR is
    the defect the refusal was written for.
    """
    ms = make_tree_ticket(session, workspace, "LG-GUARD", kind=WorkItemType.MILESTONE)
    target = integration_branch_for(ms)
    child = make_tree_ticket(
        session,
        workspace,
        "LG-GUARD-C1",
        parent=ms,
        git_automation_json=json.dumps({"base_branch": target}),
    )
    git(repo, "branch", child.branch, PROFILE_BASE)

    with pytest.raises(ValueError, match=target):
        create_ticket_pull_request(session, child)
