"""
Explicit feature validation for ticket 82: Show child tickets regardless of sidebar filter state

These tests explicitly validate what the API returns and whether it matches the feature requirement.
"""

from fastapi.testclient import TestClient


class TestExplicitFeatureRequirement:
    """
    Feature Requirement (Ticket 82):
    When filters are applied to the ticket tree (by type or state), child tickets should
    still be displayed and remain connected to their parents in the hierarchy, even if
    the parent tickets don't match the active filters.

    Current Behavior (Before Implementation):
    - Only tickets matching the filter are returned
    - Parents of matching children that don't match are excluded
    - Children become orphaned/shown as roots
    - Hierarchy is broken

    Expected Behavior (After Implementation):
    - Tickets matching the filter are returned
    - Parent tickets (even if they don't match) are included to maintain hierarchy
    - Parent-child relationships are preserved
    - Orphaned children are properly nested under filtered-out parents
    """

    def test_filtered_tree_includes_parents_of_matching_children(self, client: TestClient):
        """
        SPEC: When filtering for specific type, result should include parent tickets
        that don't match the filter but have children that do match.

        This validates that the hierarchy is preserved even when parents are filtered.
        """
        # Get full tree
        full_tree = client.get("/api/tickets/tree?workspace=loregarden").json()

        # Collect all parent-child pairs where parent.type != child.type
        parent_child_pairs_different_types = []

        def collect_pairs(nodes, pairs_list):
            for node in nodes:
                if node["children"]:
                    for child in node["children"]:
                        if child["work_item_type"] != node["work_item_type"]:
                            pairs_list.append((node, child))
                collect_pairs(node["children"], pairs_list)

        collect_pairs(full_tree, parent_child_pairs_different_types)

        # For each pair, test that filtering by child type includes parent
        if parent_child_pairs_different_types:
            parent, child = parent_child_pairs_different_types[0]

            # Filter by child type (parent won't match)
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={child['work_item_type']}"
            ).json()

            # Check if parent appears in filtered tree
            def find_in_tree(nodes, target_id):
                for node in nodes:
                    if node["id"] == target_id:
                        return True
                    if find_in_tree(node.get("children", []), target_id):
                        return True
                return False

            # The parent should appear in the filtered tree
            assert find_in_tree(filtered_tree, parent["id"]), (
                f"Parent {parent['id']} (type: {parent['work_item_type']}) should be in "
                f"filtered tree even though it doesn't match the type filter "
                f"(filtering for type: {child['work_item_type']}). "
                f"This is required to show child {child['id']} with its proper parent context."
            )

    def test_children_maintain_hierarchy_position_under_parent(self, client: TestClient):
        """
        SPEC: Children of filtered-out parents should appear as children in the tree,
        not as orphaned roots.

        This validates the hierarchy structure is maintained.
        """
        # Get full tree
        full_tree = client.get("/api/tickets/tree?workspace=loregarden").json()

        # Find a parent with children of different type
        target_parent = None
        target_child = None
        for node in full_tree:
            if node["children"]:
                for child in node["children"]:
                    if child["work_item_type"] != node["work_item_type"]:
                        target_parent = node
                        target_child = child
                        break
            if target_parent:
                break

        if target_parent and target_child:
            # Filter by child type
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={target_child['work_item_type']}"
            ).json()

            # Find parent in filtered tree
            def find_parent_with_child(nodes, parent_id, child_id):
                """Find if child appears under parent in tree structure."""
                for node in nodes:
                    if node["id"] == parent_id:
                        return any(c["id"] == child_id for c in node.get("children", []))
                    result = find_parent_with_child(node.get("children", []), parent_id, child_id)
                    if result:
                        return True
                return False

            # Child should appear under parent structure
            assert find_parent_with_child(filtered_tree, target_parent["id"], target_child["id"]), (
                f"Child {target_child['id']} should appear under parent "
                f"{target_parent['id']} in the hierarchy, not as an orphaned root"
            )

    def test_state_filter_includes_parents_of_matching_children(self, client: TestClient):
        """
        SPEC: Same behavior as type filter, but for state filters.
        When filtering by state, parent tickets should be included to maintain hierarchy.
        """
        # Get full tree
        full_tree = client.get("/api/tickets/tree?workspace=loregarden").json()

        # Find parent and child with different states
        parent_child_different_states = []

        def collect_state_pairs(nodes, pairs_list):
            for node in nodes:
                if node["children"]:
                    for child in node["children"]:
                        if child["state"] != node["state"]:
                            pairs_list.append((node, child))
                collect_state_pairs(node["children"], pairs_list)

        collect_state_pairs(full_tree, parent_child_different_states)

        if parent_child_different_states:
            parent, child = parent_child_different_states[0]

            # Filter by child state
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&state={child['state']}"
            ).json()

            # Parent should appear to maintain hierarchy
            def find_in_tree(nodes, target_id):
                for node in nodes:
                    if node["id"] == target_id:
                        return True
                    if find_in_tree(node.get("children", []), target_id):
                        return True
                return False

            assert find_in_tree(filtered_tree, parent["id"]), (
                f"Parent {parent['id']} (state: {parent['state']}) should be in "
                f"filtered tree to maintain hierarchy for child {child['id']} "
                f"(state: {child['state']})"
            )

    def test_all_levels_of_hierarchy_preserved_with_filters(self, client: TestClient):
        """
        SPEC: Deep hierarchies (3+ levels) should maintain all relationships,
        with parents of matching children included even if they don't match.
        """
        full_tree = client.get("/api/tickets/tree?workspace=loregarden").json()

        # Find a deep hierarchy (3+ levels)
        deep_hierarchy = None

        def find_deep_hierarchy(nodes, path=[]):
            for node in nodes:
                if node["children"]:
                    for child in node["children"]:
                        if child["children"]:
                            # Found 3 levels
                            return [node] + [child] + [child["children"][0]]
                    result = find_deep_hierarchy(node["children"], path + [node])
                    if result:
                        return result
            return None

        deep_hierarchy = find_deep_hierarchy(full_tree)

        if deep_hierarchy and len(deep_hierarchy) >= 3:
            parent, grandchild = deep_hierarchy[1], deep_hierarchy[2]

            # Filter by grandchild type
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={grandchild['work_item_type']}"
            ).json()

            # All ancestors should be in tree to maintain hierarchy
            def find_path(nodes, target_id, path=[]):
                """Find the path to a node in the tree."""
                for node in nodes:
                    if node["id"] == target_id:
                        return path + [node["id"]]
                    result = find_path(node.get("children", []), target_id, path + [node["id"]])
                    if result:
                        return result
                return None

            path_to_grandchild = find_path(filtered_tree, grandchild["id"])
            assert path_to_grandchild is not None, (
                f"Grandchild {grandchild['id']} should be findable in filtered tree"
            )

            # Path should include parent and grandparent
            assert parent["id"] in path_to_grandchild, (
                f"Parent {parent['id']} should be in path to grandchild"
            )

    def test_matching_children_not_duplicated_at_root(self, client: TestClient):
        """
        SPEC: Children of filtered-out parents should NOT appear as orphaned roots,
        but should remain nested under their parent.

        This prevents duplicate entries and maintains proper hierarchy.
        """
        full_tree = client.get("/api/tickets/tree?workspace=loregarden").json()

        # Find parent with children of different type
        test_parent = None
        test_child = None
        for node in full_tree:
            if node["children"]:
                for child in node["children"]:
                    if child["work_item_type"] != node["work_item_type"]:
                        test_parent = node
                        test_child = child
                        break
            if test_parent:
                break

        if test_parent and test_child:
            filtered_tree = client.get(
                f"/api/tickets/tree?workspace=loregarden&work_item_type={test_child['work_item_type']}"
            ).json()

            # Check that child appears exactly once
            def count_occurrences(nodes, target_id):
                count = 0
                for node in nodes:
                    if node["id"] == target_id:
                        count += 1
                    count += count_occurrences(node.get("children", []), target_id)
                return count

            occurrence_count = count_occurrences(filtered_tree, test_child["id"])
            assert occurrence_count == 1, (
                f"Child {test_child['id']} should appear exactly once in the tree, "
                f"not duplicated at root and under parent (found {occurrence_count} times)"
            )

            # Child should only appear as child, not as root
            child_is_root = any(r["id"] == test_child["id"] for r in filtered_tree)
            assert not child_is_root, (
                f"Child {test_child['id']} should not appear as root, "
                f"should be nested under parent {test_parent['id']}"
            )
