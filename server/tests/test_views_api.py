"""The view store's REST surface, and the single ordered sidebar it feeds.

Views and pinned built-in pages are two kinds of entry in *one* list. Ordering
therefore lives on ``sidebar_entries`` alone — a view has no rank of its own —
so a reorder that interleaves the two kinds is expressible, and neither kind can
drift out of the other's ranking.

Every rejection here asserts two things: the status is a 4xx, and the store is
unchanged afterwards. A malformed layout that lands half-written is worse than
one refused, because the view it produces cannot be opened to be repaired.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.api import views as views_api
from loregarden.db.enum_integrity import _enum_columns
from loregarden.models.domain import SidebarEntry, View, Workspace
from loregarden.models.domain.enums import ContainerKind, SidebarEntryKind, ViewKind
from loregarden.services import view_service
from loregarden.services.view_service import MAX_PAGE_KEY_LENGTH
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

VIEWS = "/api/workspaces/loregarden/views"
SIDEBAR = "/api/workspaces/loregarden/sidebar-entries"


def _container(kind: ContainerKind = ContainerKind.TERMINAL) -> dict:
    """A registry value; the container's id is the key it is filed under."""
    return {"kind": kind.value, "settings": {}}


def _leaf(node_id: str, container_id: str, size: float = 1.0) -> dict:
    return {"node": "leaf", "id": node_id, "size": size, "container_id": container_id}


def _grid_layout(root: dict | None = None, containers: dict[str, dict] | None = None) -> dict:
    return {
        "kind": ViewKind.FLEX_GRID.value,
        "containers": containers if containers is not None else {"c1": _container()},
        "root": root if root is not None else _leaf("n1", "c1"),
    }


def _canvas_layout() -> dict:
    return {
        "kind": ViewKind.CANVAS.value,
        "containers": {"c1": _container(ContainerKind.WEB_EMBED)},
        "items": [
            {
                "id": "p1",
                "container_id": "c1",
                "x": 0.0,
                "y": 0.0,
                "width": 480.0,
                "height": 320.0,
                "z_index": 0,
            }
        ],
    }


def _split(children: list[dict]) -> dict:
    return {
        "node": "split",
        "id": "s1",
        "size": 1.0,
        "orientation": "horizontal",
        "children": children,
    }


def _create_view(
    client: TestClient,
    title: str = "Grid",
    *,
    layout: dict | None = None,
    base: str = VIEWS,
) -> dict:
    """No ``kind`` on the wire: the view's kind is its layout's kind."""
    res = client.post(
        base,
        json={
            "title": title,
            "icon": "grid",
            "layout": layout if layout is not None else _grid_layout(),
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _pin(client: TestClient, page_key: str, base: str = SIDEBAR) -> dict:
    res = client.post(base, json={"page_key": page_key})
    assert res.status_code == 201, res.text
    return res.json()


def _entries(client: TestClient, base: str = SIDEBAR) -> list[dict]:
    res = client.get(base)
    assert res.status_code == 200, res.text
    return res.json()


def _other_workspace(db_session: Session) -> Workspace:
    workspace = Workspace(slug="other", name="Other", repo_path=".")
    db_session.add(workspace)
    db_session.commit()
    return workspace


# --- CRUD ---------------------------------------------------------------


def test_a_new_workspace_has_no_views(client: TestClient):
    assert client.get(VIEWS).json() == []


def test_a_flex_grid_view_round_trips_through_the_api(client: TestClient):
    created = _create_view(client, "Board")

    fetched = client.get(f"{VIEWS}/{created['id']}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["title"] == "Board"
    assert body["icon"] == "grid"
    assert body["kind"] == ViewKind.FLEX_GRID.value
    assert body["layout"] == _grid_layout()


def test_a_canvas_view_round_trips_through_the_api(client: TestClient):
    created = _create_view(client, "Sketch", layout=_canvas_layout())

    body = client.get(f"{VIEWS}/{created['id']}").json()
    assert body["kind"] == ViewKind.CANVAS.value
    assert body["layout"] == _canvas_layout()


def test_creating_a_view_lists_it(client: TestClient):
    created = _create_view(client, "Board")

    assert [view["id"] for view in client.get(VIEWS).json()] == [created["id"]]


def test_a_view_can_be_updated(client: TestClient):
    created = _create_view(client, "Board")
    new_layout = _grid_layout(
        containers={"c1": _container(), "c2": _container(ContainerKind.PANEL)},
        root=_split([_leaf("n1", "c1", 0.25), _leaf("n2", "c2", 0.75)]),
    )

    res = client.patch(
        f"{VIEWS}/{created['id']}",
        json={"title": "Renamed", "icon": "layout", "layout": new_layout},
    )

    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["title"] == "Renamed"
    assert updated["icon"] == "layout"
    assert updated["layout"] == new_layout


def test_a_partial_update_leaves_the_other_fields_alone(client: TestClient):
    """PATCH, not PUT. Every other test sends all three fields, so an
    implementation that overwrites an omitted field with its default — blanking
    a layout on a rename — passes all of them."""
    created = _create_view(client, "Board")

    res = client.patch(f"{VIEWS}/{created['id']}", json={"title": "Renamed"})

    assert res.status_code == 200, res.text
    body = client.get(f"{VIEWS}/{created['id']}").json()
    assert body["title"] == "Renamed"
    assert body["icon"] == "grid"
    assert body["layout"] == _grid_layout()


def test_updating_an_unknown_view_is_a_404(client: TestClient):
    res = client.patch(f"{VIEWS}/no-such-view", json={"title": "Renamed"})

    assert res.status_code == 404


def test_a_view_can_be_deleted(client: TestClient):
    created = _create_view(client, "Board")

    assert client.delete(f"{VIEWS}/{created['id']}").status_code == 200
    assert client.get(VIEWS).json() == []
    assert client.get(f"{VIEWS}/{created['id']}").status_code == 404


def test_deleting_a_view_removes_its_sidebar_entry(client: TestClient):
    created = _create_view(client, "Board")
    _pin(client, "tickets")

    client.delete(f"{VIEWS}/{created['id']}")

    entries = _entries(client)
    assert [entry["page_key"] for entry in entries] == ["tickets"]
    assert [entry["position"] for entry in entries] == [0]


def test_reading_an_unknown_view_is_a_404(client: TestClient):
    assert client.get(f"{VIEWS}/no-such-view").status_code == 404


def test_views_of_an_unknown_workspace_are_a_404(client: TestClient):
    assert client.get("/api/workspaces/no-such-ws/views").status_code == 404


# --- enums --------------------------------------------------------------


def test_the_kind_columns_are_enum_columns():
    """A bare string column is what let a hand-written value break every list query."""
    covered = {(table, column) for table, column, _, _ in _enum_columns()}

    assert ("views", "kind") in covered
    assert ("sidebar_entries", "entry_kind") in covered


def test_view_kind_persists_by_value_not_by_member_name(client: TestClient, db_session: Session):
    view = _create_view(client, "Board", layout=_canvas_layout())

    stored = db_session.execute(
        text("SELECT kind FROM views WHERE id = :id"), {"id": view["id"]}
    ).one()

    assert stored[0] == ViewKind.CANVAS.value


def test_sidebar_entry_kind_persists_by_value_not_by_member_name(
    client: TestClient, db_session: Session
):
    _create_view(client, "Board")

    stored = db_session.execute(text("SELECT entry_kind FROM sidebar_entries")).all()

    assert [row[0] for row in stored] == [SidebarEntryKind.VIEW.value]


def test_container_kind_persists_by_value(client: TestClient, db_session: Session):
    view = _create_view(
        client,
        "Board",
        layout=_grid_layout(containers={"c1": _container(ContainerKind.WEB_EMBED)}),
    )

    stored = db_session.execute(
        text("SELECT layout_json FROM views WHERE id = :id"), {"id": view["id"]}
    ).one()

    layout = json.loads(stored[0])
    assert layout["containers"]["c1"]["kind"] == ContainerKind.WEB_EMBED.value


# --- rejections ---------------------------------------------------------


def _assert_rejected(client: TestClient, layout: dict) -> None:
    before = client.get(VIEWS).json()
    before_entries = _entries(client)

    res = client.post(VIEWS, json={"title": "Bad", "icon": "", "layout": layout})

    assert 400 <= res.status_code < 500, res.status_code
    assert client.get(VIEWS).json() == before
    assert _entries(client) == before_entries


def test_an_unknown_container_kind_is_rejected(client: TestClient):
    _assert_rejected(
        client,
        _grid_layout(containers=[{"id": "c1", "kind": "hologram", "settings": {}}]),
    )


def test_a_zero_size_leaf_is_rejected(client: TestClient):
    _assert_rejected(
        client,
        _grid_layout(
            containers={"c1": _container(), "c2": _container()},
            root=_split([_leaf("n1", "c1", 0.0), _leaf("n2", "c2", 1.0)]),
        ),
    )


def test_a_negative_size_leaf_is_rejected(client: TestClient):
    """Spread over three panes so the row still sums to 1.0 and no sibling
    exceeds it — the negative size is the only thing left to reject."""
    _assert_rejected(
        client,
        _grid_layout(
            containers={"c1": _container(), "c2": _container(), "c3": _container()},
            root=_split(
                [_leaf("n1", "c1", -0.25), _leaf("n2", "c2", 0.5), _leaf("n3", "c3", 0.75)]
            ),
        ),
    )


def test_sizes_that_do_not_sum_within_tolerance_are_rejected(client: TestClient):
    _assert_rejected(
        client,
        _grid_layout(
            containers={"c1": _container(), "c2": _container()},
            root=_split([_leaf("n1", "c1", 0.5), _leaf("n2", "c2", 0.3)]),
        ),
    )


def test_a_duplicate_node_id_is_rejected(client: TestClient):
    _assert_rejected(
        client,
        _grid_layout(
            containers={"c1": _container(), "c2": _container()},
            root=_split([_leaf("n1", "c1", 0.5), _leaf("n1", "c2", 0.5)]),
        ),
    )


def test_a_node_that_is_its_own_ancestor_is_rejected(client: TestClient):
    _assert_rejected(
        client,
        _grid_layout(
            containers={"c1": _container(), "c2": _container()},
            root=_split(
                [
                    _leaf("n1", "c1", 0.5),
                    {
                        "node": "split",
                        "id": "s1",
                        "size": 0.5,
                        "orientation": "vertical",
                        "children": [_leaf("n2", "c2", 1.0)],
                    },
                ]
            ),
        ),
    )


def test_a_leaf_pointing_at_no_container_is_rejected(client: TestClient):
    _assert_rejected(client, _grid_layout(root=_leaf("n1", "c-missing")))


def test_a_container_nothing_references_is_rejected(client: TestClient):
    _assert_rejected(
        client,
        _grid_layout(containers={"c1": _container(), "c2": _container()}, root=_leaf("n1", "c1")),
    )


def test_the_views_kind_is_its_layouts_kind(client: TestClient):
    """Replaces a test that a *sent* kind disagreeing with the layout's kind is
    rejected. There is no second field to disagree: the create body carries the
    layout, and the layout's tag is what the view's kind is stored as."""
    grid = _create_view(client, "Board")
    canvas = _create_view(client, "Sketch", layout=_canvas_layout())

    assert grid["kind"] == ViewKind.FLEX_GRID.value
    assert canvas["kind"] == ViewKind.CANVAS.value


def test_sending_a_kind_alongside_the_layout_is_rejected(client: TestClient):
    """The dropped field is refused, not ignored. Silently dropping it would let
    a client believe it had chosen a kind that the layout actually decided."""
    before = client.get(VIEWS).json()

    res = client.post(
        VIEWS,
        json={"kind": "canvas", "title": "Bad", "icon": "", "layout": _grid_layout()},
    )

    assert 400 <= res.status_code < 500, res.status_code
    assert client.get(VIEWS).json() == before


def test_sending_a_kind_to_the_update_is_rejected_too(client: TestClient):
    """The same body, refused on POST, must not be accepted on PATCH.

    A client told its ``kind`` is invalid on create and silently obeyed on
    update learns the wrong lesson twice over — and the update is where it
    would most plausibly try to change a kind."""
    created = _create_view(client, "Board")

    res = client.patch(f"{VIEWS}/{created['id']}", json={"kind": "canvas", "title": "Renamed"})

    assert 400 <= res.status_code < 500, res.status_code
    body = client.get(f"{VIEWS}/{created['id']}").json()
    assert body["title"] == "Board"
    assert body["kind"] == ViewKind.FLEX_GRID.value


def test_an_unknown_view_kind_is_rejected(client: TestClient):
    """The kind now lives in the layout, so that is where an unknown one is sent."""
    bad = _grid_layout()
    bad["kind"] = "diorama"

    _assert_rejected(client, bad)


def test_a_rejection_leaves_an_existing_view_and_its_entry_alone(client: TestClient):
    """The "writes nothing" guarantee, checked against a store that is not empty."""
    _create_view(client, "Keep me")

    _assert_rejected(client, _grid_layout(root=_leaf("n1", "c-missing")))

    assert [view["title"] for view in client.get(VIEWS).json()] == ["Keep me"]


def test_a_rejected_update_writes_none_of_its_fields(client: TestClient):
    """The valid fields travel in the same request as the bad layout, so an
    implementation that applies them before validating the layout is caught —
    a layout-only payload would let it look clean."""
    created = _create_view(client, "Board")

    res = client.patch(
        f"{VIEWS}/{created['id']}",
        json={
            "title": "Renamed",
            "icon": "layout",
            "layout": _grid_layout(root=_leaf("n1", "c-missing")),
        },
    )

    assert 400 <= res.status_code < 500, res.status_code
    body = client.get(f"{VIEWS}/{created['id']}").json()
    assert body["layout"] == _grid_layout()
    assert body["title"] == "Board"
    assert body["icon"] == "grid"


# --- the one ordered sidebar --------------------------------------------


def test_creating_a_view_appends_its_sidebar_entry(client: TestClient):
    first = _create_view(client, "First")
    second = _create_view(client, "Second")

    entries = _entries(client)
    assert [entry["view_id"] for entry in entries] == [first["id"], second["id"]]
    assert [entry["position"] for entry in entries] == [0, 1]
    assert {entry["entry_kind"] for entry in entries} == {SidebarEntryKind.VIEW.value}


def test_pinning_a_built_in_page_appends_an_entry(client: TestClient):
    _create_view(client, "Board")
    pinned = _pin(client, "queue")

    entries = _entries(client)
    assert entries[-1]["id"] == pinned["id"]
    assert entries[-1]["entry_kind"] == SidebarEntryKind.PAGE.value
    assert entries[-1]["page_key"] == "queue"
    # The unused half of an entry is empty, not null — the column is TEXT NOT NULL
    # on both sides so a reader never has to branch on which kind it is holding.
    assert entries[-1]["view_id"] == ""
    assert entries[0]["page_key"] == ""


def test_pinning_the_same_page_twice_returns_the_first_entry(client: TestClient):
    """Pinning twice is the same request twice, not a second tab. Without the
    dedupe the sidebar grows a duplicate tab that only differs by id."""
    _create_view(client, "Board")
    first = _pin(client, "queue")

    again = _pin(client, "queue")

    assert again["id"] == first["id"]
    assert again["position"] == first["position"]
    entries = _entries(client)
    assert [entry["page_key"] for entry in entries] == ["", "queue"]
    assert [entry["position"] for entry in entries] == [0, 1]


def test_unpinning_removes_the_entry_and_closes_the_gap(client: TestClient):
    pinned = _pin(client, "tickets")
    view = _create_view(client, "Board")

    assert client.delete(f"{SIDEBAR}/{pinned['id']}").status_code == 200

    entries = _entries(client)
    assert [entry["view_id"] for entry in entries] == [view["id"]]
    assert [entry["position"] for entry in entries] == [0]


def test_unpinning_an_unknown_entry_is_a_404(client: TestClient):
    assert client.delete(f"{SIDEBAR}/no-such-entry").status_code == 404


def test_a_reorder_interleaving_views_and_pinned_pages_holds(client: TestClient):
    """The point of one ranking: a page may sit between two views."""
    first = _create_view(client, "First")
    second = _create_view(client, "Second")
    pinned = _pin(client, "tickets")

    entry_of = {entry["id"]: entry for entry in _entries(client)}
    first_entry = next(e for e in entry_of.values() if e["view_id"] == first["id"])
    second_entry = next(e for e in entry_of.values() if e["view_id"] == second["id"])

    res = client.patch(
        SIDEBAR,
        json={"entry_ids": [second_entry["id"], pinned["id"], first_entry["id"]]},
    )
    assert res.status_code == 200, res.text

    entries = _entries(client)
    assert [entry["id"] for entry in entries] == [
        second_entry["id"],
        pinned["id"],
        first_entry["id"],
    ]
    assert [entry["position"] for entry in entries] == [0, 1, 2]


def test_the_reorder_survives_a_fresh_read(client: TestClient):
    first = _create_view(client, "First")
    second = _create_view(client, "Second")
    entries = _entries(client)
    client.patch(SIDEBAR, json={"entry_ids": [entries[1]["id"], entries[0]["id"]]})

    assert [entry["view_id"] for entry in _entries(client)] == [second["id"], first["id"]]


def test_views_are_listed_in_sidebar_order(client: TestClient):
    """The list endpoint reads the one ranking rather than inventing a second."""
    first = _create_view(client, "First")
    second = _create_view(client, "Second")
    entries = _entries(client)
    client.patch(SIDEBAR, json={"entry_ids": [entries[1]["id"], entries[0]["id"]]})

    assert [view["id"] for view in client.get(VIEWS).json()] == [second["id"], first["id"]]


def test_a_partial_reorder_is_rejected(client: TestClient):
    """Half a permutation cannot produce a total order — refuse it, do not guess."""
    _create_view(client, "First")
    _create_view(client, "Second")
    before = _entries(client)

    res = client.patch(SIDEBAR, json={"entry_ids": [before[1]["id"]]})

    assert 400 <= res.status_code < 500, res.status_code
    assert _entries(client) == before


def test_a_reorder_repeating_an_entry_is_rejected(client: TestClient):
    """Same length as the list, and still not a permutation.

    An implementation that guards only on ``len(entry_ids)`` and then writes
    ``position = index`` accepts this, ranks one entry twice, and leaves the
    other at whatever rank it already held.
    """
    _create_view(client, "First")
    _create_view(client, "Second")
    before = _entries(client)

    res = client.patch(SIDEBAR, json={"entry_ids": [before[0]["id"], before[0]["id"]]})

    assert 400 <= res.status_code < 500, res.status_code
    assert _entries(client) == before


def test_a_reorder_naming_an_unknown_entry_is_rejected(client: TestClient):
    _create_view(client, "First")
    before = _entries(client)

    res = client.patch(SIDEBAR, json={"entry_ids": [before[0]["id"], "no-such-entry"]})

    assert 400 <= res.status_code < 500, res.status_code
    assert _entries(client) == before


# --- workspace scoping --------------------------------------------------


def test_views_are_scoped_to_their_workspace(client: TestClient, db_session: Session):
    _other_workspace(db_session)
    mine = _create_view(client, "Mine")

    assert client.get("/api/workspaces/other/views").json() == []
    assert client.get(f"/api/workspaces/other/views/{mine['id']}").status_code == 404


def test_sidebar_entries_are_scoped_to_their_workspace(client: TestClient, db_session: Session):
    _other_workspace(db_session)
    _create_view(client, "Mine")
    _pin(client, "tickets")

    assert _entries(client, base="/api/workspaces/other/sidebar-entries") == []


def test_positions_restart_per_workspace(client: TestClient, db_session: Session):
    _other_workspace(db_session)
    _create_view(client, "Mine")
    _create_view(client, "Also mine")
    _create_view(client, "Theirs", base="/api/workspaces/other/views")

    assert [entry["position"] for entry in _entries(client)] == [0, 1]
    theirs = _entries(client, base="/api/workspaces/other/sidebar-entries")
    assert [entry["position"] for entry in theirs] == [0]


def test_unpinning_another_workspaces_entry_is_a_404(client: TestClient, db_session: Session):
    _other_workspace(db_session)
    pinned = _pin(client, "tickets")

    res = client.delete(f"/api/workspaces/other/sidebar-entries/{pinned['id']}")

    assert res.status_code == 404
    assert [entry["id"] for entry in _entries(client)] == [pinned["id"]]


def test_a_reorder_cannot_name_another_workspaces_entry(client: TestClient, db_session: Session):
    _other_workspace(db_session)
    _create_view(client, "Mine")
    _create_view(client, "Theirs", base="/api/workspaces/other/views")
    mine = _entries(client)
    theirs = _entries(client, base="/api/workspaces/other/sidebar-entries")

    res = client.patch(SIDEBAR, json={"entry_ids": [mine[0]["id"], theirs[0]["id"]]})

    assert 400 <= res.status_code < 500, res.status_code
    assert _entries(client) == mine


def test_sidebar_entries_of_an_unknown_workspace_are_a_404(client: TestClient):
    assert client.get("/api/workspaces/no-such-ws/sidebar-entries").status_code == 404


def test_updating_a_view_through_another_workspace_is_a_404(
    client: TestClient, db_session: Session
):
    """The write half of scoping. Read and delete are checked below and above;
    an update that resolves the view by id alone would slip between them."""
    _other_workspace(db_session)
    mine = _create_view(client, "Mine")

    res = client.patch(f"/api/workspaces/other/views/{mine['id']}", json={"title": "Stolen"})

    assert res.status_code == 404
    assert client.get(f"{VIEWS}/{mine['id']}").json()["title"] == "Mine"


def test_deleting_a_view_through_another_workspace_is_a_404(
    client: TestClient, db_session: Session
):
    _other_workspace(db_session)
    mine = _create_view(client, "Mine")

    assert client.delete(f"/api/workspaces/other/views/{mine['id']}").status_code == 404
    assert client.get(f"{VIEWS}/{mine['id']}").status_code == 200


# --- bounded layouts ----------------------------------------------------


def _split_chain(depth: int) -> dict:
    """A single-child split nested ``depth`` deep over one container."""
    node = _leaf("n0", "c1")
    for index in range(depth):
        node = {
            "node": "split",
            "id": f"s{index}",
            "size": 1.0,
            "orientation": "horizontal",
            "children": [node],
        }
    return _grid_layout(root=node)


def test_an_unbounded_split_chain_is_refused_and_leaves_the_store_readable(client: TestClient):
    """The failure this bound exists for is not the rejection — it is what
    happened without one. A chain this deep passed every field rule and
    *committed*; the 500 only arrived afterwards, in response serialization, and
    then every later read of this workspace's views raised the same 500 forever.
    A stored view that cannot be listed cannot be deleted through the UI either.

    So this asserts three things in order: the status is a 4xx, nothing was
    written, and the list endpoint still answers.
    """
    _create_view(client, "Keep me")
    before = client.get(VIEWS).json()

    res = client.post(VIEWS, json={"title": "Deep", "icon": "", "layout": _split_chain(200)})

    assert 400 <= res.status_code < 500, res.status_code
    listed = client.get(VIEWS)
    assert listed.status_code == 200, listed.text
    assert listed.json() == before
    assert [entry["view_id"] for entry in _entries(client)] == [before[0]["id"]]


def test_a_split_chain_within_the_bound_is_accepted(client: TestClient):
    """The bound refuses absurd layouts, not nested ones — the recursion this
    resource exists for still works."""
    created = _create_view(client, "Nested", layout=_split_chain(8))

    assert client.get(f"{VIEWS}/{created['id']}").status_code == 200


def test_a_root_sized_below_one_is_refused(client: TestClient):
    """A root's size is its share of a parent axis, and the root has no parent.

    Accepting it stores a number with no meaning that three separate consumers of
    this layout would each have to decide whether to honour. Refusing it settles
    that once, here.
    """
    _assert_rejected(client, _grid_layout(root=_leaf("n1", "c1", 0.3)))


def test_a_root_split_sized_below_one_is_refused(client: TestClient):
    """The same rule on the other node kind — a split root is the common case,
    and a check written on ``LeafNode`` alone would miss every real layout."""
    root = _split([_leaf("n1", "c1", 0.5), _leaf("n2", "c2", 0.5)])
    root["size"] = 0.5

    _assert_rejected(
        client,
        _grid_layout(containers={"c1": _container(), "c2": _container()}, root=root),
    )


def test_a_root_of_size_one_is_still_accepted(client: TestClient):
    """The rule refuses a meaningless share, not the field."""
    created = _create_view(client, "Whole", layout=_grid_layout(root=_leaf("n1", "c1", 1.0)))

    assert client.get(f"{VIEWS}/{created['id']}").json()["layout"]["root"]["size"] == 1.0


def test_a_layout_of_twenty_thousand_containers_is_refused(client: TestClient):
    """Depth is not the only unbounded dimension: a flat canvas of 20k
    containers was accepted and turned every list response into megabytes."""
    count = 20_000
    _assert_rejected(
        client,
        {
            "kind": ViewKind.CANVAS.value,
            "containers": {f"c{index}": _container() for index in range(count)},
            "items": [
                {
                    "id": f"p{index}",
                    "container_id": f"c{index}",
                    "x": 0.0,
                    "y": 0.0,
                    "width": 100.0,
                    "height": 100.0,
                    "z_index": 0,
                }
                for index in range(count)
            ],
        },
    )


# --- bounded bytes ------------------------------------------------------


def _fat_layout(container_count: int = 4) -> dict:
    """Within every cardinality bound and far over the byte cap.

    The bounds above count containers and nodes, and a count is not a size: four
    containers is nothing while their ``settings`` carry a megabyte between them.
    ``settings`` is an open mapping by design, so nothing else narrows it.
    """
    padding = "x" * 100_000
    containers = {
        f"c{index}": {"kind": ContainerKind.PANEL.value, "settings": {"blob": padding}}
        for index in range(container_count)
    }
    children = [
        _leaf(f"n{index}", f"c{index}", 1 / container_count) for index in range(container_count)
    ]
    return _grid_layout(containers=containers, root=_split(children))


def test_a_layout_over_the_byte_cap_is_refused(client: TestClient):
    """Cardinality is not size, and the stored column is returned whole by every
    ``GET /views``. Without a byte cap one accepted write makes every later read
    of that workspace expensive, for as long as the view exists."""
    _assert_rejected(client, _fat_layout())


def test_an_update_to_an_oversized_layout_is_refused_too(client: TestClient):
    """PATCH is the same store. A body refused on create and accepted — or 500ing
    — on update is the cap applied to only half the write paths."""
    created = _create_view(client, "Board")

    res = client.patch(f"{VIEWS}/{created['id']}", json={"layout": _fat_layout()})

    assert 400 <= res.status_code < 500, res.status_code
    assert client.get(f"{VIEWS}/{created['id']}").json()["layout"] == _grid_layout()


def test_a_layout_under_the_byte_cap_is_accepted(client: TestClient):
    """The cap refuses a payload no composed view produces, not settings."""
    padding = "x" * 1_000
    layout = _grid_layout(
        containers={"c1": {"kind": ContainerKind.PANEL.value, "settings": {"blob": padding}}}
    )

    created = _create_view(client, "Chunky", layout=layout)

    assert client.get(f"{VIEWS}/{created['id']}").json()["layout"] == layout


def test_an_oversized_page_key_is_refused(client: TestClient):
    """A page key is stored, unique per workspace, and returned on every sidebar
    read — so an unbounded one is served on every request forever."""
    before = _entries(client)

    res = client.post(SIDEBAR, json={"page_key": "k" * (MAX_PAGE_KEY_LENGTH + 1)})

    assert 400 <= res.status_code < 500, res.status_code
    assert _entries(client) == before


# --- non-finite numbers -------------------------------------------------


@pytest.mark.parametrize(
    ("field", "literal"),
    [
        pytest.param("width", "Infinity", id="canvas-width-infinity"),
        pytest.param("width", "NaN", id="canvas-width-nan"),
        pytest.param("x", "Infinity", id="canvas-x-infinity"),
        pytest.param("height", "-Infinity", id="canvas-height-negative-infinity"),
    ],
)
def test_a_non_finite_canvas_number_is_a_4xx_not_a_500(
    client: TestClient, field: str, literal: str
):
    """The bounds exist to make this a 4xx, and on their own they did not.

    ``json.loads`` accepts the ``Infinity``/``NaN`` literals that ``json.dumps``
    refuses to write back. The field rule rejects the value correctly, and then
    the 422 body — which echoes the input that failed — cannot be rendered, so
    the rejection turns into the 500 the bound was added to prevent.
    """
    layout = _canvas_layout()
    layout["items"][0][field] = None
    body = json.dumps({"title": "Bad", "icon": "", "layout": layout})
    body = body.replace(f'"{field}": null', f'"{field}": {literal}')
    before = client.get(VIEWS).json()

    res = client.post(VIEWS, content=body, headers={"content-type": "application/json"})

    assert 400 <= res.status_code < 500, res.status_code
    assert client.get(VIEWS).json() == before


@pytest.mark.parametrize("literal", ["Infinity", "NaN"])
def test_a_non_finite_node_size_is_a_4xx_not_a_500(client: TestClient, literal: str):
    """The same defect on the other bounded float, reached through a grid."""
    layout = _grid_layout(root=_leaf("n1", "c1", 1.0))
    layout["root"]["size"] = None
    body = json.dumps({"title": "Bad", "icon": "", "layout": layout})
    body = body.replace('"size": null', f'"size": {literal}')
    before = client.get(VIEWS).json()

    res = client.post(VIEWS, content=body, headers={"content-type": "application/json"})

    assert 400 <= res.status_code < 500, res.status_code
    assert client.get(VIEWS).json() == before


def test_a_rejection_carrying_a_finite_input_still_reports_it(client: TestClient):
    """The scrub is the fallback, not the path.

    Dropping the echoed input from every 422 would also make the non-finite case
    a 4xx, and would quietly cost every other endpoint the value a client needs
    to see what it sent. A rejection that encodes must come back untouched.
    """
    res = client.post(VIEWS, json={"title": "Bad", "icon": "", "layout": _fat_layout(0)})

    assert res.status_code == 422, res.text
    assert any("input" in error for error in res.json()["detail"])


# --- a view's entry is not unpinnable -----------------------------------


def test_a_views_sidebar_entry_cannot_be_unpinned(client: TestClient):
    """Unpin scopes to built-in pages. A view's entry is the only thing that
    ranks it, and no request can recreate one — pinning takes a page key — so
    removing it would leave the view stored, unlisted, and unreachable.
    """
    view = _create_view(client, "Board")
    entry = _entries(client)[0]

    res = client.delete(f"{SIDEBAR}/{entry['id']}")

    assert 400 <= res.status_code < 500, res.status_code
    assert [e["id"] for e in _entries(client)] == [entry["id"]]
    assert [v["id"] for v in client.get(VIEWS).json()] == [view["id"]]


def test_unpinning_a_page_still_works_alongside_a_view(client: TestClient):
    """The other side of the same rule: the refusal is scoped to the kind, not
    a blanket ban on DELETE."""
    _create_view(client, "Board")
    pinned = _pin(client, "tickets")

    assert client.delete(f"{SIDEBAR}/{pinned['id']}").status_code == 200
    assert [entry["page_key"] for entry in _entries(client)] == [""]


# --- concurrency --------------------------------------------------------

WORKERS = 6
RUNS = 5


def _at_the_race(barrier: threading.Barrier):
    """``_next_position`` wrapped so every worker is held there once.

    A select-then-insert race is a microsecond wide, so six threads fired at an
    endpoint almost never land in it — the reviewer caught it, but a test that
    relies on that is a test that reports "fixed" for a fix that is not there.
    Holding each worker between the dedupe read and its insert makes the overlap
    the thing under test rather than the thing being hoped for. Only each
    worker's first call waits, so a retry after a lost race runs free — and
    "first" is tracked per barrier, not on a thread local, because the endpoint
    threads are the app's own pool and outlive one run of the race.
    """
    real_next_position = view_service._next_position
    lock = threading.Lock()
    already_waited: set[int] = set()

    def next_position(session, workspace_id: str) -> int:
        position = real_next_position(session, workspace_id)
        with lock:
            first_pass = already_waited.isdisjoint({threading.get_ident()})
            already_waited.add(threading.get_ident())
        if first_pass:
            barrier.wait()
        return position

    return next_position


def _raced(client: TestClient, request):
    """Run ``request`` on WORKERS threads, all released together."""
    barrier = threading.Barrier(WORKERS, timeout=30)
    with mock.patch.object(view_service, "_next_position", _at_the_race(barrier)):
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            return list(pool.map(request, range(WORKERS)))


def test_an_append_survives_a_gap_in_the_positions(client: TestClient, db_session: Session):
    """The next rank is one past the highest, not the entry count.

    Those agree only while the ranks are dense, which makes the count a fact
    derived from an invariant instead of from the column being appended to. One
    gap — a hand-edited row, a half-applied migration — and the count lands on a
    rank that is already taken, and every append after it is a 409 the caller
    can do nothing about.
    """
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    db_session.add(
        SidebarEntry(
            workspace_id=workspace.id,
            position=0,
            entry_kind=SidebarEntryKind.PAGE,
            page_key="tickets",
        )
    )
    db_session.add(
        SidebarEntry(
            workspace_id=workspace.id,
            position=2,
            entry_kind=SidebarEntryKind.PAGE,
            page_key="queue",
        )
    )
    db_session.commit()

    pinned = _pin(client, "runs")

    assert pinned["position"] == 3


def test_the_database_refuses_a_second_entry_for_one_pinned_page(
    client: TestClient, db_session: Session
):
    """The dedupe rule, asserted where it now lives.

    The threaded test below cannot pin this on its own: its racers all compute
    the same rank, so the unique *rank* would refuse the duplicate even with no
    rule about page keys at all. The interleaving that needs this constraint —
    a peer commits between another request's dedupe read and its insert, so the
    two ranks differ and only the key collides — is not one a barrier can
    schedule. Write it directly instead.
    """
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    _pin(client, "queue")

    db_session.add(
        SidebarEntry(
            workspace_id=workspace.id,
            position=99,
            entry_kind=SidebarEntryKind.PAGE,
            page_key="queue",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_two_views_do_not_collide_on_their_empty_page_key(client: TestClient):
    """The other half of that constraint. It is a plain UNIQUE only because a
    view entry's page_key is NULL — stored as '' every view entry in a workspace
    would collide with every other, and the second view would be unwritable."""
    first = _create_view(client, "First")
    second = _create_view(client, "Second")

    assert [entry["view_id"] for entry in _entries(client)] == [first["id"], second["id"]]


def test_pinning_the_same_page_concurrently_makes_one_entry(client: TestClient):
    """Dedupe by select-then-insert is a check with nothing behind it. These
    endpoints are sync, so FastAPI runs them in a threadpool and two pins really
    do overlap; neither transaction can see the other's uncommitted row, so both
    inserted — a duplicate tab, twice at the same position.

    Repeated, because a concurrency fix that works once is not yet evidence.
    """
    _create_view(client, "Board")

    for _ in range(RUNS):
        results = _raced(client, lambda _: client.post(SIDEBAR, json={"page_key": "queue"}))

        assert {res.status_code for res in results} == {201}, [r.status_code for r in results]
        assert len({res.json()["id"] for res in results}) == 1
        entries = _entries(client)
        assert [entry["page_key"] for entry in entries] == ["", "queue"]
        assert [entry["position"] for entry in entries] == [0, 1]


def test_creating_views_concurrently_gives_each_its_own_rank(client: TestClient):
    """Appending reads the highest rank and writes one past it — the same
    read-then-write. The unique rank is what makes the loser retry instead of
    seating a second entry on a rank another one already holds."""
    for _ in range(RUNS):
        results = _raced(
            client,
            lambda index: client.post(
                VIEWS, json={"title": f"V{index}", "icon": "", "layout": _grid_layout()}
            ),
        )

        assert {res.status_code for res in results} == {201}, [r.status_code for r in results]
        entries = _entries(client)
        assert [entry["position"] for entry in entries] == list(range(len(entries)))
        assert len({entry["view_id"] for entry in entries}) == len(entries)


def _at_the_read(barrier: threading.Barrier, waiters: int):
    """``list_entries`` wrapped so the first ``waiters`` calls are held there.

    Every renumbering path reads the whole sidebar and then writes every row it
    read, so the overlap that breaks them is a peer committing between those two
    steps. That window is microseconds wide; holding both requests at the read
    makes it the thing under test rather than the thing being hoped for.
    """
    real_list_entries = view_service.list_entries
    lock = threading.Lock()
    held = {"count": 0}

    def list_entries(session, workspace_id: str):
        rows = real_list_entries(session, workspace_id)
        with lock:
            first_pass = held["count"] < waiters
            held["count"] += 1
        if first_pass:
            barrier.wait()
        return rows

    return list_entries


def _entry_id_of(client: TestClient, page_key: str) -> str:
    return next(entry for entry in _entries(client) if entry["page_key"] == page_key)["id"]


def _raced_at_the_read(requests: list):
    """Run each request on its own thread, all released from one shared read."""
    barrier = threading.Barrier(len(requests), timeout=30)
    with mock.patch.object(view_service, "list_entries", _at_the_read(barrier, len(requests))):
        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            return [future.result() for future in [pool.submit(req) for req in requests]]


def test_a_reorder_overlapping_a_delete_is_a_4xx_not_a_500(client: TestClient):
    """Renumbering stages an UPDATE for every row it read. A peer that deletes
    one of them in between makes that statement match fewer rows than staged, and
    SQLAlchemy raises ``StaleDataError`` — which nothing caught, so a reorder that
    merely lost a race answered 500 and told the client nothing it could act on.

    Repeated, because a concurrency fix that works once is not yet evidence.
    """
    _create_view(client, "Board")
    for run in range(RUNS):
        keys = [f"page{run}-{index}" for index in range(3)]
        for key in keys:
            _pin(client, key)
        entries = _entries(client)
        ids = [entry["id"] for entry in entries]
        victim = next(entry for entry in entries if entry["page_key"] == keys[-1])["id"]

        statuses = [
            res.status_code
            for res in _raced_at_the_read(
                [
                    lambda: client.patch(SIDEBAR, json={"entry_ids": list(reversed(ids))}),
                    lambda: client.delete(f"{SIDEBAR}/{victim}"),
                ]
            )
        ]

        assert all(status < 500 for status in statuses), statuses
        # Whatever the reorder answered, the store is still readable, the unpin
        # removed exactly the entry it named, and no two entries share a rank.
        surviving = _entries(client)
        assert {entry["id"] for entry in surviving} == set(ids) - {victim}
        positions = [entry["position"] for entry in surviving]
        assert len(set(positions)) == len(positions)

        for key in keys[:-1]:
            assert client.delete(f"{SIDEBAR}/{_entry_id_of(client, key)}").status_code == 200


def test_a_reorder_overlapping_a_view_delete_is_a_4xx_not_a_500(client: TestClient):
    """The other renumbering delete, and the one that removes two rows.

    ``delete_view`` reads the sidebar to find the view's entry *before* it writes
    anything, so unlike an unpin it has a window a peer can commit inside — and
    it then renumbers through ``_close_gaps``, which was missed by the same fix
    that hardened create and pin. Both halves land in one request here.
    """
    for run in range(RUNS):
        view = _create_view(client, f"Board{run}")
        for index in range(2):
            _pin(client, f"page{run}-{index}")
        ids = [entry["id"] for entry in _entries(client)]

        statuses = [
            res.status_code
            for res in _raced_at_the_read(
                [
                    lambda: client.patch(SIDEBAR, json={"entry_ids": list(reversed(ids))}),
                    lambda: client.delete(f"{VIEWS}/{view['id']}"),
                ]
            )
        ]

        assert all(status < 500 for status in statuses), statuses
        assert client.get(VIEWS).json() == []
        surviving = _entries(client)
        assert len(surviving) == 2
        positions = [entry["position"] for entry in surviving]
        assert len(set(positions)) == len(positions)

        for index in range(2):
            assert (
                client.delete(f"{SIDEBAR}/{_entry_id_of(client, f'page{run}-{index}')}").status_code
                == 200
            )


def _deleting_between_the_load_and_the_write(peer: Session, view_id: str):
    """``get_view`` wrapped so a peer's DELETE commits before the update writes.

    That window is the whole bug: the update reads the view, a peer removes it,
    and the UPDATE the ORM staged then matches no rows. Committing the delete
    from a second session pins the window open rather than hoping two threads
    interleave the one way that shows it.

    The peer removes the view's sidebar entry with it, which is what
    ``delete_view`` does — a view row dropped on its own leaves the entry
    pointing at nothing, and foreign keys are enforced, so that peer would fail
    on its own write instead of opening the window this test is about.
    """
    real_get_view = views_api.get_view

    def get_view(session: Session, workspace_id: str, requested_id: str):
        view = real_get_view(session, workspace_id, requested_id)
        gone = peer.get(View, view_id)
        if gone is not None:
            for entry in peer.exec(
                select(SidebarEntry).where(SidebarEntry.view_id == view_id)
            ).all():
                peer.delete(entry)
            peer.delete(gone)
            peer.commit()
        return view

    return get_view


@pytest.mark.parametrize("body_key", ["title", "layout"])
def test_an_update_overlapping_a_delete_is_a_404_not_a_500(
    client: TestClient, db_session: Session, body_key: str
):
    """The view is gone by the time the update writes, and that is a 404.

    ``update_view`` staged an UPDATE for a row a peer had already deleted, so
    SQLAlchemy raised ``StaleDataError`` and the request answered 500 — for a
    request whose only fault was losing a race it cannot win by retrying. The
    answer is the one a request a millisecond later already gets.

    Both bodies, because the layout branch writes two more columns than the
    rename does and a guard placed inside that branch would pass on only one.
    """
    view = _create_view(client, "Board")
    body = {"title": "Renamed"} if body_key == "title" else {"layout": _grid_layout()}

    with mock.patch.object(
        views_api, "get_view", _deleting_between_the_load_and_the_write(db_session, view["id"])
    ):
        res = client.patch(f"{VIEWS}/{view['id']}", json=body)

    assert res.status_code == 404, res.text
    assert client.get(f"{VIEWS}/{view['id']}").status_code == 404


# --- pinning a view's tab (472) ------------------------------------------
#
# The sidebar's Tools section is derived from the client's page catalog rather
# than stored, so the built-in pages are no longer pinnable at all and `pinned`
# only ever describes a view's tab. The endpoints for pinned pages stay, and
# stay tested above, because databases seeded before 472 still carry those rows.


def test_a_new_views_tab_is_not_pinned(client: TestClient):
    """A new tab lands in Tabs. Pinning is a favourite, and nothing can decide
    on the user's behalf that the thing they just made is one."""
    _create_view(client, "Board")

    assert _entries(client)[0]["pinned"] is False


def test_a_views_tab_can_be_pinned_and_unpinned(client: TestClient):
    _create_view(client, "Board")
    entry = _entries(client)[0]

    pinned = client.patch(f"{SIDEBAR}/{entry['id']}", json={"pinned": True})
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True
    assert _entries(client)[0]["pinned"] is True

    unpinned = client.patch(f"{SIDEBAR}/{entry['id']}", json={"pinned": False})
    assert unpinned.status_code == 200
    assert _entries(client)[0]["pinned"] is False


def test_pinning_a_tab_leaves_the_ranking_alone(client: TestClient):
    """The two sections share one ordering. Pinning says which of them draws the
    tab — moving it in the ranking as well would silently reorder the sidebar
    behind a control that says nothing about order."""
    first = _create_view(client, "First")
    second = _create_view(client, "Second")
    third = _create_view(client, "Third")
    before = [(entry["id"], entry["position"]) for entry in _entries(client)]
    middle = _entries(client)[1]

    client.patch(f"{SIDEBAR}/{middle['id']}", json={"pinned": True})

    assert [(entry["id"], entry["position"]) for entry in _entries(client)] == before
    assert [view["id"] for view in client.get(VIEWS).json()] == [
        first["id"],
        second["id"],
        third["id"],
    ]


def test_pinning_a_tab_twice_is_the_same_request_twice(client: TestClient):
    _create_view(client, "Board")
    entry = _entries(client)[0]

    client.patch(f"{SIDEBAR}/{entry['id']}", json={"pinned": True})
    again = client.patch(f"{SIDEBAR}/{entry['id']}", json={"pinned": True})

    assert again.status_code == 200
    assert again.json()["pinned"] is True
    assert again.json()["position"] == entry["position"]


def test_a_pinned_pages_entry_cannot_be_pinned(client: TestClient):
    """Only a view's tab has a section to be moved between. A page entry is a
    leftover from before Tools became static and is drawn nowhere, so the write
    would have no effect the caller could ever see."""
    pinned_page = _pin(client, "queue")

    res = client.patch(f"{SIDEBAR}/{pinned_page['id']}", json={"pinned": True})

    assert res.status_code == 400, res.text
    assert _entries(client)[0]["pinned"] is False


def test_pinning_an_unknown_entry_is_a_404(client: TestClient):
    assert client.patch(f"{SIDEBAR}/no-such-entry", json={"pinned": True}).status_code == 404


def test_pinning_another_workspaces_entry_is_a_404(client: TestClient, db_session: Session):
    _create_view(client, "Ours")
    other = Workspace(slug="other", name="Other", repo_path="/tmp/other")
    db_session.add(other)
    db_session.commit()
    theirs = client.post(
        "/api/workspaces/other/views",
        json={"title": "Theirs", "icon": "", "layout": _grid_layout()},
    )
    assert theirs.status_code == 201
    their_entry = client.get("/api/workspaces/other/sidebar-entries").json()[0]

    res = client.patch(f"{SIDEBAR}/{their_entry['id']}", json={"pinned": True})

    assert res.status_code == 404
    assert client.get("/api/workspaces/other/sidebar-entries").json()[0]["pinned"] is False


def test_an_extra_key_on_the_pin_body_is_refused(client: TestClient):
    """`extra="forbid"`, as on the view routes: a field accepted on one and
    silently ignored on the other reads as the field being supported."""
    _create_view(client, "Board")
    entry = _entries(client)[0]

    res = client.patch(f"{SIDEBAR}/{entry['id']}", json={"pinned": True, "position": 7})

    assert res.status_code == 422
    assert _entries(client)[0]["pinned"] is False


def test_deleting_a_pinned_views_tab_still_deletes_the_view(client: TestClient):
    """Pinning must not become a second way to make a view undeletable."""
    view = _create_view(client, "Board")
    entry = _entries(client)[0]
    client.patch(f"{SIDEBAR}/{entry['id']}", json={"pinned": True})

    assert client.delete(f"{VIEWS}/{view['id']}").status_code == 200
    assert _entries(client) == []


# --- non-finite numbers in settings (444) --------------------------------


def _raw_json(client: TestClient, method: str, url: str, body: str):
    """A body carrying the ``Infinity``/``NaN`` literals.

    ``json.dumps`` refuses to write them, so the request cannot be built with
    ``json=``. ``json.loads`` — which is what parses the request server-side —
    accepts them, which is exactly how one reaches the layout model in
    production.
    """
    return client.request(method, url, content=body, headers={"content-type": "application/json"})


def _layout_with_settings(raw_settings: str) -> str:
    return (
        '{"kind": "flex_grid",'
        f' "containers": {{"c1": {{"kind": "panel", "settings": {raw_settings}}}}},'
        ' "root": {"node": "leaf", "id": "n1", "size": 1.0, "container_id": "c1"}}'
    )


@pytest.mark.parametrize(
    "raw_settings",
    [
        pytest.param('{"zoom": Infinity}', id="infinity"),
        pytest.param('{"zoom": -Infinity}', id="negative infinity"),
        pytest.param('{"zoom": NaN}', id="nan"),
        pytest.param('{"camera": {"zoom": Infinity}}', id="nested"),
        pytest.param('{"stops": [0.5, Infinity]}', id="inside a list"),
    ],
)
def test_creating_a_view_with_a_non_finite_setting_is_refused(
    client: TestClient, raw_settings: str
):
    """Before this rule the POST returned 201 and stored ``{"zoom": null}``: the
    dump to JSON coerced the value before the byte cap or ``json.dumps`` saw it,
    so nothing downstream had a chance to object. The client got a 201 for a
    write that silently was not the one it made."""
    body = f'{{"title": "Board", "icon": "grid", "layout": {_layout_with_settings(raw_settings)}}}'

    res = _raw_json(client, "POST", VIEWS, body)

    assert res.status_code == 422, res.text
    assert client.get(VIEWS).json() == []


def test_the_refusal_renders_a_422_body_rather_than_a_500(client: TestClient):
    """The echoed ``input`` is the settings map that could not be written back,
    so the error body is the one place this rejection could still become a 500.
    The path a client acts on survives."""
    layout = _layout_with_settings('{"z": NaN}')
    body = f'{{"title": "Board", "icon": "grid", "layout": {layout}}}'

    res = _raw_json(client, "POST", VIEWS, body)

    assert res.status_code == 422
    locations = [error["loc"] for error in res.json()["detail"]]
    assert any("settings" in location for location in locations), res.text


def test_updating_a_view_with_a_non_finite_setting_is_refused(client: TestClient):
    """The rule lives on the layout model, which both routes validate through,
    so PATCH inherits it without a second copy of the check — and the stored
    layout is left exactly as it was."""
    created = _create_view(client, "Board")
    before = client.get(f"{VIEWS}/{created['id']}").json()["layout"]

    layout = _layout_with_settings('{"zoom": Infinity}')

    res = _raw_json(client, "PATCH", f"{VIEWS}/{created['id']}", f'{{"layout": {layout}}}')

    assert res.status_code == 422, res.text
    assert client.get(f"{VIEWS}/{created['id']}").json()["layout"] == before


def test_a_finite_settings_value_still_round_trips_through_the_api(client: TestClient):
    """The narrowing is number handling only. Keys the server has no vocabulary
    for — the registry's business, not the control plane's — come back
    byte-identical, numbers included.

    Asserted on the response *bytes* rather than on the parsed mapping, because
    the damage this rule exists to catch is at that level: parsed-dict equality
    cannot see a value re-encoded on the way through, and a `null` where a
    number went in is only one of the ways that shows up.
    """
    settings = {"primitive_id": "some.registry.id", "zoom": 1.5, "offset": -0.25, "steps": 3}
    layout = _grid_layout(
        containers={"c1": {"kind": ContainerKind.PANEL.value, "settings": settings}}
    )

    created = _create_view(client, "Board", layout=layout)

    fetched = client.get(f"{VIEWS}/{created['id']}")
    assert json.dumps(settings, separators=(",", ":")) in fetched.text
    assert fetched.json()["layout"]["containers"]["c1"]["settings"] == settings
