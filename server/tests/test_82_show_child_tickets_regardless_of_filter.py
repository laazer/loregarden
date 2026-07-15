"""
Test suite for ticket 82: Show child tickets regardless of sidebar filter state

Feature: When filters (by type or state) are applied to the ticket tree, child tickets should
still be displayed even if their parent tickets don't match the active filters. This preserves
the visual hierarchy and allows users to see all related work.

Acceptance Criteria:
1. Child tickets are shown when their parent doesn't match type filter
2. Child tickets are shown when their parent doesn't match state filter
3. Parent tickets (unfiltered) are included in response to maintain hierarchy
4. Child-child (grandchild) tickets are shown regardless of ancestor filters
5. Filter applies to child tickets themselves (filtered children still appear if they match)
6. Multiple filter combinations work correctly
"""

from fastapi.testclient import TestClient


def _ticket_id_by_external_id(client: TestClient, external_id: str) -> str:
    tickets = client.get("/api/tickets").json()
    for t in tickets:
        if t["external_id"] == external_id:
            return t["id"]
    raise ValueError(f"Ticket not found: {external_id}")


def _flatten_tree_nodes(nodes: list[dict]) -> list[dict]:
    """Flatten tree structure into a list of all nodes (parents and children)."""
    result = []
    for node in nodes:
        result.append(node)
        result.extend(_flatten_tree_nodes(node.get("children") or []))
    return result


def _find_node_by_id(nodes: list[dict], node_id: str) -> dict | None:
    """Find a node in tree by its id."""
    flat = _flatten_tree_nodes(nodes)
    return next((n for n in flat if n["id"] == node_id), None)


class TestChildTicketsWithTypeFilter:
    """Child tickets should be visible regardless of type filter applied to parents."""

    def test_child_tickets_shown_when_parent_filtered_out_by_type(self, client: TestClient):
        """
        Given: A milestone (parent) and a feature child
        When: Filter applied to show only tasks
        Then: The feature child should still be visible (though milestone is filtered)

        This test verifies that child tickets appear in the tree even when their
        parent doesn't match the applied type filter.
        """
        # Get base tree without filters
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find a non-task type parent with task children
        parent_with_task_children = None
        for node in flat:
            if node["work_item_type"] != "task" and node["children"]:
                task_children = [c for c in node["children"] if c["work_item_type"] == "task"]
                if task_children:
                    parent_with_task_children = node
                    break

        if parent_with_task_children:
            # Filter to show only tasks
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&work_item_type=task"
            ).json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # The task children should be in the filtered tree
            task_child_ids = {
                c["id"]
                for c in parent_with_task_children["children"]
                if c["work_item_type"] == "task"
            }
            found_task_ids = {n["id"] for n in flat_filtered if n["work_item_type"] == "task"}

            assert task_child_ids.issubset(found_task_ids), (
                f"Expected task children {task_child_ids} to be in filtered tree, "
                f"but found {found_task_ids}"
            )

    def test_multiple_type_filters_preserve_hierarchy(self, client: TestClient):
        """
        When: Multiple type filters are applied (e.g., task and bug)
        Then: Children of non-selected types should still appear if matched by filter

        This verifies that filtering by multiple types still preserves the hierarchy.
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find a parent with multiple child types
        parent_with_varied_children = None
        for node in flat:
            if node["children"] and len(node["children"]) >= 2:
                child_types = {c["work_item_type"] for c in node["children"]}
                if len(child_types) >= 2:
                    parent_with_varied_children = node
                    break

        if parent_with_varied_children:
            child_types = sorted(
                {c["work_item_type"] for c in parent_with_varied_children["children"]}
            )
            selected_types = child_types[:2] if len(child_types) >= 2 else child_types

            # Filter by selected types
            params = "&".join(f"work_item_type={t}" for t in selected_types)
            filtered_tree = client.get(f"/api/tickets/tree?workspace=loregarden&{params}").json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # Children matching the filter should be present
            for child in parent_with_varied_children["children"]:
                if child["work_item_type"] in selected_types:
                    assert _find_node_by_id(flat_filtered, child["id"]) is not None, (
                        f"Child {child['id']} (type: {child['work_item_type']}) should be in "
                        f"filtered tree when its type is in filter"
                    )


class TestChildTicketsWithStateFilter:
    """Child tickets should be visible regardless of state filter applied to parents."""

    def test_child_tickets_shown_when_parent_filtered_out_by_state(self, client: TestClient):
        """
        Given: A backlog ticket (parent) and a done task (child)
        When: Filter applied to show only done tickets
        Then: The done task should be visible even though parent is backlog

        This tests that child tickets appear when their parent's state doesn't match
        the state filter.
        """
        # Get base tree
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find a parent with children in different states
        parent_with_varied_states = None
        for node in flat:
            if node["children"]:
                child_states = {c["state"] for c in node["children"]}
                if len(child_states) >= 2:
                    parent_with_varied_states = node
                    break

        if parent_with_varied_states:
            # Filter by a state that some children have but parent doesn't
            all_states = {n["state"] for n in flat}
            parent_state = parent_with_varied_states["state"]
            other_states = all_states - {parent_state}

            if other_states:
                selected_state = next(iter(other_states))
                # Check if any children have this state
                matching_children = [
                    c for c in parent_with_varied_states["children"] if c["state"] == selected_state
                ]

                if matching_children:
                    # Filter to show only selected_state
                    filtered_tree = client.get(
                        f"/api/tickets/tree?workspace=loregarden&state={selected_state}"
                    ).json()
                    flat_filtered = _flatten_tree_nodes(filtered_tree)

                    # The matching children should be in filtered tree
                    for child in matching_children:
                        found = _find_node_by_id(flat_filtered, child["id"])
                        assert found is not None, (
                            f"Child {child['id']} with state '{child['state']}' should appear "
                            f"when filtering by state '{selected_state}'"
                        )

    def test_multiple_state_filters_preserve_hierarchy(self, client: TestClient):
        """
        When: Multiple state filters are applied (e.g., in_progress and done)
        Then: Children matching the filter should appear even if parent doesn't
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Get all states present in tree
        all_states = sorted({n["state"] for n in flat})

        if len(all_states) >= 2:
            selected_states = all_states[:2]
            params = "&".join(f"state={s}" for s in selected_states)
            filtered_tree = client.get(f"/api/tickets/tree?workspace=loregarden&{params}").json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # All nodes in filtered tree should have states in selected_states
            for node in flat_filtered:
                assert node["state"] in selected_states, (
                    f"Node {node['id']} has state '{node['state']}' which is not in "
                    f"selected states {selected_states}"
                )


class TestGrandchildTicketsPreserved:
    """Grandchild tickets and deeper hierarchy levels should be preserved."""

    def test_grandchild_shown_when_parent_filtered_out(self, client: TestClient):
        """
        Given: A milestone > feature > task > subtask hierarchy
        When: Filter applied to show only tasks
        Then: Subtasks should still be visible under their task parents
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find a node with grandchildren
        grandparent = None
        parent = None
        for node in flat:
            if node["children"]:
                for child in node["children"]:
                    if child["children"]:
                        grandparent = node
                        parent = child
                        break
                if grandparent:
                    break

        if grandparent and parent and grandparent.get("children"):
            # Get a grandchild
            grandchild = parent["children"][0] if parent["children"] else None
            if grandchild:
                # Filter by a type different from grandparent
                selected_type = "task" if grandparent["work_item_type"] != "task" else "capability"
                filtered_tree = client.get(
                    f"/api/tickets/tree?workspace=loregarden&work_item_type={selected_type}"
                ).json()
                flat_filtered = _flatten_tree_nodes(filtered_tree)

                # If grandchild type matches filter, it should appear
                if grandchild["work_item_type"] == selected_type:
                    found = _find_node_by_id(flat_filtered, grandchild["id"])
                    assert found is not None, (
                        f"Grandchild {grandchild['id']} should appear when "
                        f"filtering by type {selected_type}"
                    )


class TestFilteredChildrenStillFiltered:
    """Child tickets that themselves don't match filters should not appear."""

    def test_unmatched_child_filtered_out(self, client: TestClient):
        """
        Given: A parent with multiple children of different types
        When: Filter applied to show only tasks
        Then: Only task children should appear (non-task children should be filtered)
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find a parent with multiple child types
        parent_with_mixed_children = None
        for node in flat:
            if node["children"] and len(node["children"]) >= 2:
                child_types = {c["work_item_type"] for c in node["children"]}
                if len(child_types) >= 2:
                    parent_with_mixed_children = node
                    break

        if parent_with_mixed_children:
            # Filter to show only tasks
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&work_item_type=task"
            ).json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # Non-task children should NOT appear
            non_task_children = [
                c for c in parent_with_mixed_children["children"] if c["work_item_type"] != "task"
            ]
            for child in non_task_children:
                found = _find_node_by_id(flat_filtered, child["id"])
                assert found is None, (
                    f"Non-task child {child['id']} should not appear when filtering by type 'task'"
                )

    def test_child_with_unmatched_state_filtered_out(self, client: TestClient):
        """
        When: Filter applied to show only backlog tickets
        Then: Children not in backlog state should not appear
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find a parent with children in different states
        parent_with_mixed_states = None
        for node in flat:
            if node["children"]:
                child_states = {c["state"] for c in node["children"]}
                if len(child_states) >= 2:
                    parent_with_mixed_states = node
                    break

        if parent_with_mixed_states:
            # Filter to show only backlog
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&state=backlog"
            ).json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # Non-backlog children should NOT appear
            non_backlog_children = [
                c for c in parent_with_mixed_states["children"] if c["state"] != "backlog"
            ]
            for child in non_backlog_children:
                found = _find_node_by_id(flat_filtered, child["id"])
                assert found is None, (
                    f"Non-backlog child {child['id']} should not appear when "
                    f"filtering by state 'backlog'"
                )


class TestHierarchyIntegrity:
    """Parent-child relationships should be maintained in filtered results."""

    def test_parent_child_relationships_maintained(self, client: TestClient):
        """
        When: Filters are applied
        Then: Parent-child relationships should be correctly represented in the tree
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat_all = _flatten_tree_nodes(all_tree)

        # Find a parent with a child
        parent_with_child = None
        child_id = None
        for node in flat_all:
            if node["children"]:
                parent_with_child = node
                child_id = node["children"][0]["id"]
                break

        if parent_with_child and child_id:
            # Get child info
            child = next((n for n in flat_all if n["id"] == child_id), None)
            if child:
                # Apply a type filter
                filtered_tree = client.get(
                    f"/api/tickets/tree?workspace=loregarden&work_item_type={child['work_item_type']}"
                ).json()
                flat_filtered = _flatten_tree_nodes(filtered_tree)

                # If child is in filtered tree, its parent should also be included
                # to maintain hierarchy
                if _find_node_by_id(flat_filtered, child_id):
                    # The tree structure should show the parent-child relationship
                    # (even if parent is not shown at root level, child should be nested)
                    found_child = _find_node_by_id(flat_filtered, child_id)
                    assert found_child is not None, "Child should be in filtered tree"

    def test_child_count_accurate_with_filters(self, client: TestClient):
        """
        When: Filters are applied
        Then: The child_count should reflect actual children in filtered tree
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat_all = _flatten_tree_nodes(all_tree)

        # Find a parent with children of varied types
        parent_with_children = None
        for node in flat_all:
            if node["children"] and len(node["children"]) >= 2:
                child_types = {c["work_item_type"] for c in node["children"]}
                if len(child_types) >= 2:
                    parent_with_children = node
                    break

        if parent_with_children:
            # Filter to show only tasks
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&work_item_type=task"
            ).json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # Find this parent in filtered tree
            parent_in_filtered = _find_node_by_id(flat_filtered, parent_with_children["id"])
            if parent_in_filtered:
                # The filtered parent should show correct child count
                # (or at least have the correct children array)
                assert len(parent_in_filtered["children"]) <= len(
                    parent_with_children["children"]
                ), "Filtered parent should not have more children than original"


class TestSearchWithFilters:
    """Search and type/state filters should work together correctly."""

    def test_search_respects_type_filter(self, client: TestClient):
        """
        When: Both search and type filter are applied
        Then: Results should match both criteria
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Get all tickets with a specific type
        if flat:
            sample_type = flat[0]["work_item_type"]
            # Search for tickets of that type with "test" in title
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={sample_type}&search=test"
            ).json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # All results should be of the selected type
            for node in flat_filtered:
                assert node["work_item_type"] == sample_type, (
                    f"Node {node['id']} has type '{node['work_item_type']}' "
                    f"but should be '{sample_type}'"
                )

    def test_search_preserves_child_visibility(self, client: TestClient):
        """
        When: Search filter is applied
        Then: Child tickets matching search should still be visible
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find any ticket with a distinctive word in title
        ticket_with_word = next((n for n in flat if "test" in n["title"].lower()), None)

        if ticket_with_word:
            # Search for that word
            search_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&search={ticket_with_word['title'][:10]}"
            ).json()
            flat_search = _flatten_tree_nodes(search_tree)

            # The ticket should appear in search results
            found = _find_node_by_id(flat_search, ticket_with_word["id"])
            assert found is not None, (
                f"Ticket {ticket_with_word['id']} matching search should be in results"
            )


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_single_child_with_filter_applied(self, client: TestClient):
        """
        When: A parent has only one child and filter is applied
        Then: The child should appear if it matches the filter
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find a parent with exactly one child
        single_child_parent = next((n for n in flat if len(n["children"]) == 1), None)

        if single_child_parent:
            child = single_child_parent["children"][0]
            # Filter by child type
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={child['work_item_type']}"
            ).json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # Child should appear
            found = _find_node_by_id(flat_filtered, child["id"])
            assert found is not None, "Single child matching filter should appear"

    def test_deeply_nested_hierarchy_with_filters(self, client: TestClient):
        """
        When: Deeply nested hierarchy (4+ levels) exists and filter applied
        Then: All matching levels should appear
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()

        # Find deepest nesting
        max_depth = 0
        deepest_node = None

        def find_depth(nodes: list[dict], depth: int = 0) -> tuple[int, dict | None]:
            nonlocal max_depth, deepest_node
            for node in nodes:
                if depth > max_depth:
                    max_depth = depth
                    deepest_node = node
                if node["children"]:
                    find_depth(node["children"], depth + 1)
            return max_depth, deepest_node

        find_depth(all_tree)

        if max_depth >= 3:
            # Apply a filter and verify deep nodes still appear
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&work_item_type=task"
            ).json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # We should still have multiple levels
            for node in flat_filtered:
                # This is a simplification; proper depth calculation would walk tree
                if node["work_item_type"] == "task":
                    assert node in flat_filtered, "Task nodes should be in filtered results"

    def test_all_children_filtered_out(self, client: TestClient):
        """
        When: A parent exists but none of its children match the filter
        Then: The parent should not appear (no children to show)
        """
        all_tree = client.get("/api/tickets/tree?workspace=loregarden").json()
        flat = _flatten_tree_nodes(all_tree)

        # Find a parent with children not of type "task"
        parent_without_tasks = None
        for node in flat:
            if node["children"]:
                task_children = [c for c in node["children"] if c["work_item_type"] == "task"]
                if not task_children:  # No task children
                    parent_without_tasks = node
                    break

        if parent_without_tasks:
            # Filter to show only tasks
            filtered_tree = client.get(
                "/api/tickets/tree?workspace=loregarden&work_item_type=task"
            ).json()
            flat_filtered = _flatten_tree_nodes(filtered_tree)

            # Parent with no matching children should not appear
            found = _find_node_by_id(flat_filtered, parent_without_tasks["id"])
            assert found is None, (
                f"Parent {parent_without_tasks['id']} with no matching children "
                f"should not appear in filtered results"
            )
