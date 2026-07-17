import pytest
from loregarden.models.domain import TicketStudioDraftItem, WorkItemType
from loregarden.services.draft_hierarchy import (
    DraftHierarchyError,
    find_hierarchy_violations,
    repair_draft_hierarchy,
    topo_sort_draft_items,
)


def _item(ref: str, work_item_type: str, parent_ref: str | None = None) -> TicketStudioDraftItem:
    return TicketStudioDraftItem(
        ref=ref,
        work_item_type=WorkItemType(work_item_type),
        parent_ref=parent_ref,
        title=f"{ref} title",
    )


def _repair(items, parent_type=None):
    return repair_draft_hierarchy(
        items,
        parent_type=parent_type,
        root_title="Scope root",
        root_description="The brief",
    )


def _parent_of(items, ref):
    by_ref = {item.ref: item for item in items}
    parent_ref = by_ref[ref].parent_ref
    return by_ref[parent_ref] if parent_ref else None


class TestRepairInsertsMissingLayers:
    def test_tasks_under_a_feature_share_one_inserted_capability(self):
        """The scoper's milestone > feature > task output — the layer it skips most."""
        items = _repair(
            [
                _item("m1", "milestone"),
                _item("f1", "feature", "m1"),
                _item("t1", "task", "f1"),
                _item("t2", "task", "f1"),
            ]
        )

        capability = _parent_of(items, "t1")
        assert capability.work_item_type == WorkItemType.CAPABILITY
        assert _parent_of(items, "t2").ref == capability.ref
        assert _parent_of(items, capability.ref).ref == "f1"
        assert not find_hierarchy_violations(items, parent_type=None)

    def test_flat_parentless_tasks_gain_a_full_spine(self):
        """Smart import's shape: a batch of root tasks with no hierarchy at all."""
        items = _repair([_item(f"t{index}", "task") for index in range(23)])

        capability = _parent_of(items, "t0")
        feature = _parent_of(items, capability.ref)
        milestone = _parent_of(items, feature.ref)
        assert [capability.work_item_type, feature.work_item_type, milestone.work_item_type] == [
            WorkItemType.CAPABILITY,
            WorkItemType.FEATURE,
            WorkItemType.MILESTONE,
        ]
        assert milestone.parent_ref is None
        # 23 tasks + exactly one shared spine, not one spine each.
        assert len(items) == 26
        assert all(_parent_of(items, f"t{index}").ref == capability.ref for index in range(23))
        assert not find_hierarchy_violations(items, parent_type=None)

    def test_root_milestone_carries_the_session_brief(self):
        items = _repair([_item("f1", "feature")])

        milestone = _parent_of(items, "f1")
        assert milestone.title == "Scope root"
        assert milestone.description == "The brief"

    def test_session_parent_supplies_the_upper_layers(self):
        """With a feature parent ticket, roots need a capability — not a new milestone."""
        items = _repair([_item("t1", "task")], parent_type=WorkItemType.FEATURE)

        capability = _parent_of(items, "t1")
        assert capability.work_item_type == WorkItemType.CAPABILITY
        assert capability.parent_ref is None
        assert len(items) == 2
        assert not find_hierarchy_violations(items, parent_type=WorkItemType.FEATURE)

    def test_wrappers_precede_the_items_that_depend_on_them(self):
        items = _repair(
            [_item("t1", "task", "f1"), _item("m1", "milestone"), _item("f1", "feature", "m1")]
        )

        order = [item.ref for item in items]
        for item in items:
            if item.parent_ref:
                assert order.index(item.parent_ref) < order.index(item.ref)

    def test_inserted_refs_do_not_collide_with_existing_ones(self):
        items = _repair([_item("m1", "milestone"), _item("m1-feature", "task", "m1")])

        assert len({item.ref for item in items}) == len(items)
        assert not find_hierarchy_violations(items, parent_type=None)


class TestRepairLeavesValidDraftsAlone:
    def test_a_legal_draft_is_unchanged(self):
        original = [
            _item("m1", "milestone"),
            _item("f1", "feature", "m1"),
            _item("c1", "capability", "f1"),
            _item("t1", "task", "c1"),
        ]
        items = _repair(list(original))

        assert [(item.ref, item.parent_ref) for item in items] == [
            ("m1", None),
            ("f1", "m1"),
            ("c1", "f1"),
            ("t1", "c1"),
        ]

    def test_bugs_hang_off_any_chain_type_untouched(self):
        items = _repair(
            [
                _item("m1", "milestone"),
                _item("bug-m", "bug", "m1"),
                _item("f1", "feature", "m1"),
                _item("bug-f", "bug", "f1"),
            ]
        )

        assert _parent_of(items, "bug-m").ref == "m1"
        assert _parent_of(items, "bug-f").ref == "f1"

    def test_a_root_bug_gains_only_a_milestone(self):
        items = _repair([_item("bug-1", "bug")])

        assert _parent_of(items, "bug-1").work_item_type == WorkItemType.MILESTONE
        assert len(items) == 2

    def test_empty_draft_is_returned_as_is(self):
        assert _repair([]) == []


class TestRepairRejectsWhatItCannotFix:
    def test_child_outranking_its_parent_is_rejected(self):
        with pytest.raises(DraftHierarchyError) as exc:
            _repair([_item("t1", "task"), _item("f1", "feature", "t1")])

        assert exc.value.violations == ["f1 (feature) cannot be a child of t1 (task)"]

    def test_a_bug_never_takes_children(self):
        with pytest.raises(DraftHierarchyError) as exc:
            _repair([_item("bug-1", "bug"), _item("t1", "task", "bug-1")])

        assert "t1 (task) cannot be a child of bug-1 (bug)" in exc.value.violations

    def test_a_parent_ref_cycle_is_rejected(self):
        """A cycle used to recurse forever in the commit-time sort."""
        items = [_item("a", "capability", "b"), _item("b", "capability", "a")]

        with pytest.raises(DraftHierarchyError) as exc:
            _repair(items)

        assert exc.value.violations == [
            "a: parent_ref chain forms a cycle",
            "b: parent_ref chain forms a cycle",
        ]

    def test_unknown_parent_ref_is_rejected(self):
        with pytest.raises(DraftHierarchyError) as exc:
            _repair([_item("t1", "task", "nope")])

        assert "nope" in str(exc.value)

    def test_leaf_session_parent_is_rejected(self):
        with pytest.raises(DraftHierarchyError) as exc:
            _repair([_item("t1", "task")], parent_type=WorkItemType.TASK)

        assert exc.value.violations == [
            "t1 (task) cannot be a child of the session's parent work item (task)"
        ]

    def test_every_violation_is_reported_not_just_the_first(self):
        with pytest.raises(DraftHierarchyError) as exc:
            _repair(
                [_item("t1", "task"), _item("f1", "feature", "t1"), _item("c1", "capability", "t1")]
            )

        assert len(exc.value.violations) == 2


class TestTopoSortDraftItems:
    def test_children_follow_their_parents(self):
        items = topo_sort_draft_items(
            [_item("t1", "task", "c1"), _item("c1", "capability", "f1"), _item("f1", "feature")]
        )

        assert [item.ref for item in items] == ["f1", "c1", "t1"]

    def test_cyclic_items_are_kept_not_dropped(self):
        items = topo_sort_draft_items(
            [_item("f1", "feature"), _item("a", "capability", "b"), _item("b", "capability", "a")]
        )

        assert {item.ref for item in items} == {"f1", "a", "b"}
        assert items[0].ref == "f1"


class TestFindHierarchyViolations:
    def test_a_cycle_is_reported(self):
        items = [_item("a", "capability", "b"), _item("b", "capability", "a")]

        violations = find_hierarchy_violations(items, parent_type=None)

        assert "a: parent_ref chain forms a cycle" in violations
        assert "b: parent_ref chain forms a cycle" in violations

    def test_lists_every_offending_item(self):
        items = [
            _item("m1", "milestone"),
            _item("f1", "feature", "m1"),
            _item("t1", "task", "f1"),
            _item("t2", "task", "f1"),
        ]

        violations = find_hierarchy_violations(items, parent_type=None)

        assert len(violations) == 2
        assert all("cannot be a child of f1 (feature)" in violation for violation in violations)

    def test_non_milestone_root_without_a_session_parent_is_reported(self):
        violations = find_hierarchy_violations([_item("t1", "task")], parent_type=None)

        assert len(violations) == 1
        assert "must be a milestone or feature" in violations[0]

    def test_root_is_checked_against_the_session_parent(self):
        violations = find_hierarchy_violations(
            [_item("t1", "task")], parent_type=WorkItemType.FEATURE
        )

        assert len(violations) == 1
        assert "session's parent work item (feature)" in violations[0]

    def test_a_legal_draft_has_no_violations(self):
        items = [
            _item("m1", "milestone"),
            _item("f1", "feature", "m1"),
            _item("c1", "capability", "f1"),
            _item("t1", "task", "c1"),
            _item("bug-1", "bug", "c1"),
        ]

        assert find_hierarchy_violations(items, parent_type=None) == []
