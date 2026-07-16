"""Ticket tree filtering: matching tickets stay anchored to their hierarchy.

Filtering the sidebar narrows which tickets *match*, not which tickets are reachable. A ticket
that matches the filter must always appear in its real position in the tree, even when every
ancestor above it was filtered out. Ancestors are carried along purely as scaffolding.

Fixtures build their own hierarchy in a dedicated workspace so assertions are unconditional —
they never depend on the shape of seed data.
"""

import pytest
from fastapi.testclient import TestClient

WORKSPACE = "tree-filter-fixture"


def _flatten(nodes: list[dict]) -> list[dict]:
    flat = []
    for node in nodes:
        flat.append(node)
        flat.extend(_flatten(node.get("children") or []))
    return flat


def _ids(nodes: list[dict]) -> set[str]:
    return {n["id"] for n in _flatten(nodes)}


def _find(nodes: list[dict], node_id: str) -> dict | None:
    return next((n for n in _flatten(nodes) if n["id"] == node_id), None)


def _tree(client: TestClient, query: str = "") -> list[dict]:
    suffix = f"&{query}" if query else ""
    resp = client.get(f"/api/tickets/tree?workspace={WORKSPACE}{suffix}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make(client: TestClient, title: str, work_item_type: str, parent: str | None = None) -> str:
    resp = client.post(
        "/api/tickets",
        json={
            "workspace_slug": WORKSPACE,
            "title": title,
            "work_item_type": work_item_type,
            "parent_ticket_id": parent,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _set_state(client: TestClient, ticket_id: str, state: str) -> None:
    resp = client.patch(f"/api/tickets/{ticket_id}", json={"state": state})
    assert resp.status_code == 200, resp.text


@pytest.fixture
def hierarchy(client: TestClient) -> dict[str, str]:
    """A full-depth tree, following VALID_HIERARCHY (milestone > feature > capability > task).

    milestone_root (milestone, backlog)
      └── feature_mid (feature, backlog)
            ├── capability_mid (capability, backlog)
            │     └── task_leaf (task, backlog)
            └── bug_leaf (bug, backlog)
    lonely_milestone (milestone, backlog)  — no descendants

    task_leaf sits three ancestors deep, so a filter matching only it exercises the whole
    upward walk rather than a single parent hop.
    """
    resp = client.post(
        "/api/workspaces",
        json={"slug": WORKSPACE, "name": "Tree Filter Fixture"},
    )
    assert resp.status_code == 201, resp.text

    milestone_root = _make(client, "root milestone", "milestone")
    feature_mid = _make(client, "mid feature", "feature", milestone_root)
    capability_mid = _make(client, "mid capability", "capability", feature_mid)
    return {
        "milestone_root": milestone_root,
        "feature_mid": feature_mid,
        "capability_mid": capability_mid,
        "task_leaf": _make(client, "leaf task", "task", capability_mid),
        "bug_leaf": _make(client, "leaf bug", "bug", feature_mid),
        "lonely_milestone": _make(client, "lonely milestone", "milestone"),
    }


class TestMatchingTicketsStayAnchored:
    def test_type_filter_keeps_full_ancestor_chain(self, client: TestClient, hierarchy):
        """AC1/AC3/AC4: a matching task keeps all three ancestors, not just its direct parent.

        Regression: an earlier fix only walked up one level, so the milestone root vanished and
        the chain broke on any hierarchy deeper than parent-and-child.
        """
        tree = _tree(client, "work_item_type=task")
        ids = _ids(tree)

        assert hierarchy["task_leaf"] in ids
        assert hierarchy["capability_mid"] in ids, "capability ancestor dropped"
        assert hierarchy["feature_mid"] in ids, "feature ancestor dropped"
        assert hierarchy["milestone_root"] in ids, "milestone ancestor dropped"

    def test_matching_ticket_keeps_its_real_position(self, client: TestClient, hierarchy):
        """A filtered-in task must nest under its parent, not get re-parented to the root."""
        tree = _tree(client, "work_item_type=task")

        roots = {n["id"] for n in tree}
        assert hierarchy["task_leaf"] not in roots, "task orphaned to root"
        assert roots == {hierarchy["milestone_root"]}

        feature = _find(tree, hierarchy["feature_mid"])
        assert [c["id"] for c in feature["children"]] == [hierarchy["capability_mid"]]

        capability = _find(tree, hierarchy["capability_mid"])
        assert [c["id"] for c in capability["children"]] == [hierarchy["task_leaf"]]

    def test_state_filter_keeps_ancestor_chain(self, client: TestClient, hierarchy):
        """AC2: same anchoring when the ancestors are excluded by state rather than type."""
        _set_state(client, hierarchy["task_leaf"], "done")
        tree = _tree(client, "state=done")
        ids = _ids(tree)

        assert hierarchy["task_leaf"] in ids
        assert hierarchy["capability_mid"] in ids
        assert hierarchy["feature_mid"] in ids
        assert hierarchy["milestone_root"] in ids
        assert hierarchy["bug_leaf"] not in ids, "backlog sibling should not match state=done"


class TestNonMatchingTicketsExcluded:
    def test_ancestor_without_matching_descendant_is_excluded(self, client: TestClient, hierarchy):
        """Scaffolding is only carried for ancestors that actually anchor a match."""
        tree = _tree(client, "work_item_type=task")
        assert hierarchy["lonely_milestone"] not in _ids(tree)

    def test_filter_still_applies_to_children(self, client: TestClient, hierarchy):
        """AC5: ancestors are scaffolding — siblings that don't match stay out."""
        tree = _tree(client, "work_item_type=task")
        assert hierarchy["bug_leaf"] not in _ids(tree)

    def test_matching_ancestor_appears_without_children(self, client: TestClient, hierarchy):
        """A ticket that matches on its own right shows up even with no matching descendants."""
        tree = _tree(client, "work_item_type=milestone")
        ids = _ids(tree)

        assert hierarchy["milestone_root"] in ids
        assert hierarchy["lonely_milestone"] in ids
        assert hierarchy["feature_mid"] not in ids
        assert _find(tree, hierarchy["milestone_root"])["children"] == []

    def test_intermediate_ancestor_dropped_when_deeper_match_is_filtered_out(
        self, client: TestClient, hierarchy
    ):
        """A capability is scaffolding only; with no task match it has no reason to appear."""
        _set_state(client, hierarchy["task_leaf"], "done")
        ids = _ids(_tree(client, "work_item_type=task&state=blocked"))
        assert ids == set()


class TestTreeIntegrity:
    def test_no_duplicate_nodes(self, client: TestClient, hierarchy):
        """An ancestor reachable from two matching leaves must still appear exactly once."""
        tree = _tree(client, "work_item_type=task&work_item_type=bug")
        flat = _flatten(tree)
        assert len(flat) == len({n["id"] for n in flat})

    def test_child_count_reflects_returned_children(self, client: TestClient, hierarchy):
        """child_count must describe the tree as returned, or the UI renders phantom children."""
        tree = _tree(client, "work_item_type=task")
        for node in _flatten(tree):
            assert node["child_count"] == len(node["children"]), node["external_id"]

    def test_multiple_type_filters_union(self, client: TestClient, hierarchy):
        """AC6: two leaves at different depths match, sharing one anchored chain."""
        tree = _tree(client, "work_item_type=task&work_item_type=bug")
        ids = _ids(tree)

        assert {hierarchy["task_leaf"], hierarchy["bug_leaf"]} <= ids
        feature = _find(tree, hierarchy["feature_mid"])
        assert {c["id"] for c in feature["children"]} == {
            hierarchy["capability_mid"],
            hierarchy["bug_leaf"],
        }

    def test_combined_type_and_state_filters(self, client: TestClient, hierarchy):
        """AC6: type and state compose as AND against the matching ticket."""
        _set_state(client, hierarchy["task_leaf"], "done")

        ids = _ids(_tree(client, "work_item_type=task&state=done"))
        assert hierarchy["task_leaf"] in ids
        assert hierarchy["milestone_root"] in ids

        assert _tree(client, "work_item_type=task&state=blocked") == []

    def test_unfiltered_tree_returns_everything(self, client: TestClient, hierarchy):
        tree = _tree(client)
        assert _ids(tree) == set(hierarchy.values())

    def test_no_matches_returns_empty_list(self, client: TestClient, hierarchy):
        assert _tree(client, "state=wont_do") == []
