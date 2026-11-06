"""A directory too big for one pass is split, not skipped.

beluga's `packages/mac` (895 files) was one map leaf. `assign_slice` fills to the
pass's budget (60), so a leaf over it was never picked while anything ranked was
left, and five passes read around the repo's whole UI. The map now descends into
a directory larger than the budget; a stored map taken before that is re-taken at
the same commit; a ranked path the new map split hands its turn to its leaves; a
read recorded on it before the split still counts; and a leaf no split can break
up (loose files) takes what budget is left instead of waiting forever.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

import loregarden
import pytest
from loregarden.config import settings
from loregarden.models.domain.survey_enums import LicenceUse, SurveyReadState
from loregarden.services.git_subprocess import run_git
from loregarden.services.invariant_registry import SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD
from loregarden.services.reference_survey import feeder, map_repair
from loregarden.services.reference_survey.coverage import (
    is_leaf,
    map_tree,
    oversized_directory_leaves,
    tree_file_total,
)
from loregarden.services.reference_survey.map_repair import unsplit_maps
from loregarden.services.reference_survey.passes import (
    assign_slice,
    coverage_rows,
    leaf_states,
    map_leaves,
    read_states,
    record_reads,
    store_map,
)
from loregarden.services.reference_survey.schemas import ReadIn
from sqlmodel import Session
from tests.survey_support import build_world, running_pass, write_tree

BUDGET = 4

#: `big/ui` (6 files in two subdirectories) is over the budget and splittable;
#: `flat` holds 6 loose files, which no split can break up.
FILES = {
    **{f"big/ui/a/{n}.ts": "x" for n in range(3)},
    **{f"big/ui/b/{n}.ts": "x" for n in range(3)},
    **{f"flat/{n}.py": "x" for n in range(6)},
    "small/x.py": "x",
}


@pytest.fixture(name="tree")
def tree_fixture(tmp_path):
    return write_tree(tmp_path / "clone", FILES)


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tree):
    session = Session(isolated_db)
    world = build_world(session)
    # running_pass stores the unsplit map, as every source mapped before the fix has.
    source, survey_pass = running_pass(session, world, tree)
    yield session, source, survey_pass, tree
    session.close()


def _leaf_paths(entries) -> dict[str, int]:
    paths = {e.path for e in entries}
    return {e.path: e.file_count for e in entries if is_leaf(e.path, paths)}


def _refresh(session, source, tree) -> None:
    with mock.patch.object(settings, "reference_survey_pass_file_budget", BUDGET):
        feeder._refresh_map(session, source, tree, source.mapped_commit)
    session.refresh(source)


def test_the_map_is_unchanged_without_a_budget_and_splits_only_what_is_over_it(tree) -> None:
    plain = map_tree(tree)
    assert map_tree(tree, split_over=100) == plain
    assert "big/ui" in _leaf_paths(plain) and _leaf_paths(plain)["big/ui"] == 6

    split = map_tree(tree, split_over=BUDGET)
    leaves = _leaf_paths(split)
    assert "big/ui" not in leaves
    assert (leaves["big/ui/a"], leaves["big/ui/b"]) == (3, 3)
    assert leaves["flat/."] == 6, "loose files stay one leaf: there is nothing to split"
    assert tree_file_total(split) == tree_file_total(plain) == len(FILES)
    assert oversized_directory_leaves({e.path: e.file_count for e in plain}, BUDGET) == ["big/ui"]
    assert oversized_directory_leaves({e.path: e.file_count for e in split}, BUDGET) == []


def test_a_map_taken_before_splitting_is_re_taken_at_the_same_commit(ctx) -> None:
    session, source, _pass, tree = ctx
    commit = source.mapped_commit
    _refresh(session, source, tree)
    leaves = {row.path for row in map_leaves(coverage_rows(session, source.id))}
    assert {"big/ui/a", "big/ui/b"} <= leaves and "big/ui" not in leaves
    assert source.mapped_commit == commit


def test_a_map_with_nothing_left_to_split_is_not_re_stored(ctx) -> None:
    session, source, _pass, tree = ctx
    _refresh(session, source, tree)
    with mock.patch.object(feeder, "store_map") as store:
        _refresh(session, source, tree)
    store.assert_not_called()


def test_a_slice_takes_the_split_pieces_of_a_directory_too_big_for_it(ctx) -> None:
    session, source, survey_pass, tree = ctx
    _refresh(session, source, tree)
    sliced = assign_slice(session, survey_pass, file_budget=BUDGET)
    assert sliced and set(sliced) <= {"big/ui/a", "big/ui/b", "flat/.", "small/x.py", "small"}
    assert "big/ui/a" in sliced, "a 3-file piece of the 6-file directory fits the 4-file budget"


def test_a_ranked_path_the_map_has_since_split_hands_its_turn_to_its_leaves(ctx) -> None:
    session, source, survey_pass, tree = ctx
    rows = {row.path: row for row in coverage_rows(session, source.id)}
    rows["big/ui"].priority = 1
    session.add(rows["big/ui"])
    session.commit()
    _refresh(session, source, tree)
    assert assign_slice(session, survey_pass, file_budget=BUDGET)[0] == "big/ui/a"


def test_a_leaf_no_split_can_break_takes_the_budget_that_is_left(ctx) -> None:
    session, source, survey_pass, tree = ctx
    _refresh(session, source, tree)
    record_reads(
        session,
        survey_pass.id,
        [
            ReadIn(path=path, state=SurveyReadState.READ, files_read=count)
            for path, count in (("big/ui/a", 3), ("big/ui/b", 3))
        ],
    )
    sliced = assign_slice(session, survey_pass, file_budget=BUDGET)
    assert "flat/." in sliced, "the 6-file loose leaf is over the 4-file budget yet still assigned"


def test_a_read_recorded_before_the_split_still_covers_the_pieces(ctx) -> None:
    session, source, survey_pass, tree = ctx
    record_reads(
        session,
        survey_pass.id,
        [
            ReadIn(path="big/ui", state=SurveyReadState.READ, files_read=6),
            ReadIn(path="flat", state=SurveyReadState.PARTIAL, files_read=2),
        ],
    )
    _refresh(session, source, tree)
    rows = coverage_rows(session, source.id)
    states = leaf_states(rows, read_states(session, source.id))
    assert states["big/ui/a"] is SurveyReadState.READ
    assert states["big/ui/b"] is SurveyReadState.READ
    assert "flat/." not in states, "a partial read of the parent says nothing about which leaf"
    assert not {"big/ui/a", "big/ui/b"} & set(
        assign_slice(session, survey_pass, file_budget=BUDGET)
    )


def test_a_directory_at_the_depth_limit_is_not_re_mapped_on_every_pass(
    isolated_db, tmp_path
) -> None:
    deep = "d1/d2/d3/d4/d5/d6"
    tree = write_tree(
        tmp_path / "deep", {f"{deep}/{sub}/{n}.ts": "x" for sub in "ab" for n in range(3)}
    )
    with Session(isolated_db) as session:
        source, _pass = running_pass(session, build_world(session), tree)
        _refresh(session, source, tree)
        stored = {row.path: row.file_count for row in coverage_rows(session, source.id)}
        assert stored[deep] == 6 and is_leaf(deep, set(stored)), "the split stops at the limit"
        assert oversized_directory_leaves(stored, BUDGET) == [], "and that is not a violation"
        with mock.patch.object(feeder, "store_map") as store:
            _refresh(session, source, tree)
        store.assert_not_called()


def test_a_directory_of_loose_files_splits_into_its_loose_leaf(tmp_path) -> None:
    """Otherwise a split map's oversized `a/logs` would look exactly like an
    unsplit one, and the invariant could not tell a map that needs re-taking
    from one that cannot be split further."""
    tree = write_tree(tmp_path / "loose", {f"a/logs/{n}.txt": "x" for n in range(6)})
    leaves = _leaf_paths(map_tree(tree, split_over=BUDGET))
    assert leaves == {"a/logs/.": 6}
    plain = {e.path: e.file_count for e in map_tree(tree)}
    assert oversized_directory_leaves(plain, BUDGET) == ["a/logs"]


def _store_map_calls() -> list[ast.Call]:
    calls = []
    for path in Path(loregarden.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "store_map":
                calls.append(node)
    return calls


def test_every_map_the_feeder_stores_splits_what_no_pass_can_hold(ctx) -> None:
    """The guard for `survey_maps_split_what_no_pass_can_hold`, at every writer.

    Every `store_map` call in the server is handed `map_tree(..., split_over=…)`,
    so a writer added later that maps unsplit fails here. And the feeder, given
    the unsplit map every source stored before the fix, leaves none behind.
    """
    calls = _store_map_calls()
    assert len(calls) >= 2, "the feeder and the repair both write maps"
    for call in calls:
        entries = call.args[2]
        assert getattr(entries.func, "id", "") == "map_tree", ast.unparse(call)
        assert "split_over" in {kw.arg for kw in entries.keywords}, ast.unparse(call)

    session, source, _pass, tree = ctx
    with mock.patch.object(settings, "reference_survey_pass_file_budget", BUDGET):
        assert [item.source.id for item in unsplit_maps(session)] == [source.id]
        feeder._refresh_map(session, source, tree, source.mapped_commit)
        assert unsplit_maps(session) == []


@pytest.fixture(name="cloned")
def cloned_fixture(ctx):
    """ctx's tree as a git clone, with the unsplit map stored at its HEAD."""
    session, source, survey_pass, tree = ctx
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "tree"]):  # fmt: skip
        run_git(args, cwd=tree, check=True, capture_output=True)
    head = run_git(["rev-parse", "HEAD"], cwd=tree, check=True, capture_output=True, text=True)
    store_map(session, source, map_tree(tree), head.stdout.strip())
    with (
        mock.patch.object(settings, "reference_survey_pass_file_budget", BUDGET),
        mock.patch.object(map_repair, "clone_path_for", return_value=tree),
    ):
        yield session, source, tree


def test_the_invariant_re_splits_a_stored_map_from_the_clone_at_its_commit(cloned) -> None:
    session, source, _tree = cloned
    violations = SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.detect(session)
    assert [v.subject_id for v in violations] == [source.id]
    assert "big/ui" in violations[0].detail

    assert SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.repair(session, violations) == 1
    assert SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.detect(session) == []
    leaves = {row.path for row in map_leaves(coverage_rows(session, source.id))}
    assert {"big/ui/a", "big/ui/b"} <= leaves


def test_the_invariant_leaves_a_map_alone_when_the_clone_has_moved_on(cloned) -> None:
    """The map must describe its own commit; a clone at another one cannot repair it."""
    session, source, tree = cloned
    (tree / "big/ui/a/new.ts").write_text("x")
    for args in (
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "moved"],
    ):
        run_git(args, cwd=tree, check=True, capture_output=True)
    violations = SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.detect(session)

    assert SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.repair(session, violations) == 0
    assert [v.subject_id for v in SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.detect(session)] == [
        source.id
    ]


def test_a_repair_re_checks_and_counts_only_what_it_changed(cloned) -> None:
    """The world moves between detect and repair; a map the feeder split in
    between is not repaired twice, nor counted."""
    session, _source, _tree = cloned
    violations = SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.detect(session)
    assert SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.repair(session, violations) == 1
    with mock.patch.object(map_repair, "store_map") as store:
        assert SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.repair(session, violations) == 0
    store.assert_not_called()


def test_a_source_refused_on_licence_is_not_reported(cloned) -> None:
    """Nothing reads an excluded source, so its map has nothing to starve."""
    session, source, _tree = cloned
    source.licence_use = LicenceUse.EXCLUDED
    session.add(source)
    session.commit()
    assert SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD.detect(session) == []
