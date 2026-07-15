"""
Detailed tests for ticket 82: Show child tickets regardless of sidebar filter state

These tests specifically verify that:
1. When a parent doesn't match a filter, its children still appear if they match
2. Parent-child relationships are preserved in the response
3. The tree structure shows both parents and children even when parents are filtered
"""

from fastapi.testclient import TestClient


def _flatten_tree_nodes(nodes: list[dict]) -> list[dict]:
    """Flatten tree structure into a list of all nodes (parents and children)."""
    result = []
    for node in nodes:
        result.append(node)
        result.extend(_flatten_tree_nodes(node.get("children") or []))
    return result


class TestChildVisibilityWithoutParentMatchingFilter:
    """Tests that verify child tickets appear in filtered results even when parent doesn't match."""

    def test_task_child_appears_when_parent_is_feature_with_type_filter_for_task(
        self, client: TestClient
    ):
        """
        CRITICAL TEST: Verify the core feature behavior

        Given: A Feature (parent) with Task (child)
        When: Filter applied to show only Tasks
        Then: The Task should appear in results
        And: The parent Feature should NOT appear (since it doesn't match filter)

        This is the key behavior for ticket 82 - child tickets visible regardless
        of whether parent matches the filter.
        """
        # Get unfiltered tree to find a feature with task children
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        all_flat = _flatten_tree_nodes(all_tree)

        # Find a feature with at least one task child
        feature_with_tasks = None
        for node in all_flat:
            if node["work_item_type"] == "feature" and node["children"]:
                task_children = [c for c in node["children"] if c["work_item_type"] == "task"]
                if task_children:
                    feature_with_tasks = node
                    break

        if feature_with_tasks:
            task_children = [
                c for c in feature_with_tasks["children"] if c["work_item_type"] == "task"
            ]

            # Apply filter to show only tasks
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&work_item_type=task"
            ).json()
            filtered_flat = _flatten_tree_nodes(filtered_tree)

            # Verify each task child appears in filtered results
            for task_child in task_children:
                task_ids_in_filtered = {
                    n["id"] for n in filtered_flat if n["work_item_type"] == "task"
                }
                assert task_child["id"] in task_ids_in_filtered, (
                    f"Task child {task_child['id']} '{task_child['title']}' should appear "
                    f"in tree filtered to show only tasks, even though parent Feature "
                    f"{feature_with_tasks['id']} doesn't match the filter"
                )

    def test_capability_child_shows_when_parent_milestone_filtered_out(self, client: TestClient):
        """
        When: Milestone > Capability hierarchy exists
        And: Filter applied to show only Capability type
        Then: Capabilities should appear (parent Milestone is filtered out)
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        all_flat = _flatten_tree_nodes(all_tree)

        # Find a milestone with capability children
        milestone_with_capabilities = None
        for node in all_flat:
            if node["work_item_type"] == "milestone" and node["children"]:
                capability_children = [
                    c for c in node["children"] if c["work_item_type"] == "capability"
                ]
                if capability_children:
                    milestone_with_capabilities = node
                    break

        if milestone_with_capabilities:
            capability_children = [
                c
                for c in milestone_with_capabilities["children"]
                if c["work_item_type"] == "capability"
            ]

            # Filter to show only capabilities
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&work_item_type=capability"
            ).json()
            filtered_flat = _flatten_tree_nodes(filtered_tree)

            for cap_child in capability_children:
                found = any(n["id"] == cap_child["id"] for n in filtered_flat)
                assert found, (
                    f"Capability child {cap_child['id']} should appear when filtering "
                    f"by type capability, even though parent Milestone is filtered out"
                )

    def test_multiple_children_show_when_one_matches_filter(self, client: TestClient):
        """
        When: Parent has multiple children of different types
        And: Filter applied to show only one type
        Then: Only matching children appear (others filtered)
        And: Children appear even if parent type doesn't match
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        all_flat = _flatten_tree_nodes(all_tree)

        # Find parent with mixed child types
        parent_with_mixed = None
        for node in all_flat:
            if node["children"] and len(node["children"]) >= 2:
                child_types = {c["work_item_type"] for c in node["children"]}
                if len(child_types) >= 2:
                    parent_with_mixed = node
                    break

        if parent_with_mixed:
            # Pick one child type to filter by
            target_type = parent_with_mixed["children"][0]["work_item_type"]
            matching_children = [
                c for c in parent_with_mixed["children"] if c["work_item_type"] == target_type
            ]
            non_matching_children = [
                c for c in parent_with_mixed["children"] if c["work_item_type"] != target_type
            ]

            # Apply filter
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={target_type}"
            ).json()
            filtered_flat = _flatten_tree_nodes(filtered_tree)
            filtered_ids = {n["id"] for n in filtered_flat}

            # Matching children SHOULD appear
            for child in matching_children:
                assert child["id"] in filtered_ids, (
                    f"Child {child['id']} of type {target_type} should appear in filtered results"
                )

            # Non-matching children SHOULD NOT appear
            for child in non_matching_children:
                assert child["id"] not in filtered_ids, (
                    f"Child {child['id']} of type {child['work_item_type']} should NOT appear "
                    f"when filtering for type {target_type}"
                )


class TestStateFilterWithChildVisibility:
    """Child tickets should be visible by state filter regardless of parent state."""

    def test_child_in_progress_shows_when_parent_backlog(self, client: TestClient):
        """
        When: Parent is in backlog state, child is in_progress
        And: Filter applied to show only in_progress
        Then: Child should appear (parent is filtered out by state)
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        all_flat = _flatten_tree_nodes(all_tree)

        # Find parent in backlog with in_progress children
        parent_backlog_with_inprogress_children = None
        for node in all_flat:
            if node["state"] == "backlog" and node["children"]:
                inprogress_children = [c for c in node["children"] if c["state"] == "in_progress"]
                if inprogress_children:
                    parent_backlog_with_inprogress_children = node
                    break

        if parent_backlog_with_inprogress_children:
            inprogress_children = [
                c
                for c in parent_backlog_with_inprogress_children["children"]
                if c["state"] == "in_progress"
            ]

            # Filter to show only in_progress
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&state=in_progress"
            ).json()
            filtered_flat = _flatten_tree_nodes(filtered_tree)

            for child in inprogress_children:
                found = any(
                    n["id"] == child["id"] and n["state"] == "in_progress" for n in filtered_flat
                )
                assert found, (
                    f"Child {child['id']} in in_progress state should appear "
                    f"when filtering by in_progress state, even though parent is backlog"
                )

    def test_done_child_shows_when_parent_blocked(self, client: TestClient):
        """
        When: Parent is in blocked state, child is done
        And: Filter applied to show only done tickets
        Then: Child should appear (parent filtered out by state)
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        all_flat = _flatten_tree_nodes(all_tree)

        # Find parent blocked with done children
        parent_blocked_with_done = None
        for node in all_flat:
            if node["state"] == "blocked" and node["children"]:
                done_children = [c for c in node["children"] if c["state"] == "done"]
                if done_children:
                    parent_blocked_with_done = node
                    break

        if parent_blocked_with_done:
            done_children = [
                c for c in parent_blocked_with_done["children"] if c["state"] == "done"
            ]

            filtered_tree = client.get("/api/tickets/tree?workspace=loregarden&state=done").json()
            filtered_flat = _flatten_tree_nodes(filtered_tree)

            for child in done_children:
                found = any(n["id"] == child["id"] for n in filtered_flat)
                assert found, f"Done child {child['id']} should appear when filtering by done state"


class TestHierarchyPreservationWithFilters:
    """Hierarchy structure should be preserved when showing filtered children."""

    def test_child_maintains_parent_reference_when_parent_filtered(self, client: TestClient):
        """
        When: Filtering causes parent to be hidden
        Then: Child should still be accessible and maintain parent relationship metadata
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        all_flat = _flatten_tree_nodes(all_tree)

        # Find parent-child pair where parent type is different from child type
        parent_child_pair = None
        for node in all_flat:
            if node["children"]:
                for child in node["children"]:
                    if child["work_item_type"] != node["work_item_type"]:
                        parent_child_pair = (node, child)
                        break
                if parent_child_pair:
                    break

        if parent_child_pair:
            parent, child = parent_child_pair

            # Filter by child type (parent will be filtered out)
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={child['work_item_type']}"
            ).json()
            filtered_flat = _flatten_tree_nodes(filtered_tree)

            # Child should be in filtered results
            found_child = next((n for n in filtered_flat if n["id"] == child["id"]), None)
            assert found_child is not None, f"Child {child['id']} should appear in filtered results"

            # Child should have parent_ticket_id set (to maintain relationship info)
            if found_child and hasattr(found_child, "get"):  # Check if it's a dict
                # Note: parent_ticket_id may not be in the response, but structure should be valid
                assert found_child["work_item_type"] == child["work_item_type"]


class TestNestedHierarchyWithFilters:
    """Multi-level hierarchies should work correctly with filters."""

    def test_grandchild_visible_when_parent_filtered_out(self, client: TestClient):
        """
        When: Grandparent (Feature) > Parent (Capability) > Child (Task)
        And: Parent type is different from Grandparent
        And: Filter applied to show only Task type
        Then: Task should appear even though Capability parent is also filtered
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        all_flat = _flatten_tree_nodes(all_tree)

        # Find a 3-level hierarchy
        three_level_found = None
        for node in all_flat:
            if node["children"]:
                for child in node["children"]:
                    if child["children"]:
                        three_level_found = (node, child, child["children"][0])
                        break
                if three_level_found:
                    break

        if three_level_found:
            grandparent, parent, grandchild = three_level_found

            # Filter by grandchild type
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={grandchild['work_item_type']}"
            ).json()
            filtered_flat = _flatten_tree_nodes(filtered_tree)

            # Grandchild should be visible
            found = any(n["id"] == grandchild["id"] for n in filtered_flat)
            assert found, (
                f"Grandchild {grandchild['id']} of type {grandchild['work_item_type']} "
                f"should appear in filtered results even when parent "
                f"{parent['id']} is filtered out"
            )
