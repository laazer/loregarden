"""The layout model both view kinds share.

One model serves the flex grid and the canvas because both are consumed by the
same renderer contract: a set of containers, plus an arrangement over them. The
arrangement differs — a recursive split tree versus a flat positioned list — and
that is the only difference the model encodes.

Containers live in one registry keyed by id, and an arrangement *references*
them. That is what makes "orphaned reference" a real failure mode rather than a
turn of phrase, and it is what lets a container's identity survive being moved
between panes.

Structural validation happens here, on write, not at render time: a malformed
layout that reaches the database is a view that cannot be opened and cannot be
repaired from the UI that fails to open it.
"""

import math

import pytest
from loregarden.models.domain.enums import ContainerKind, ViewKind
from loregarden.models.domain.view_layout import (
    MAX_CANVAS_EXTENT,
    MAX_CONTAINERS,
    MAX_LAYOUT_NODES,
    MAX_SPLIT_DEPTH,
    layout_payload,
    parse_view_layout,
)
from pydantic import ValidationError


def _container(kind: ContainerKind = ContainerKind.TERMINAL) -> dict:
    """One registry *value*. A container's id is the key it is filed under, and
    is therefore not a field it also carries — two copies of one id can
    disagree, and this shape has only one."""
    return {"kind": kind.value, "settings": {}}


def _flex_grid(root: dict, containers: dict[str, dict] | None = None) -> dict:
    return {
        "kind": ViewKind.FLEX_GRID.value,
        "containers": containers if containers is not None else {"c1": _container()},
        "root": root,
    }


def _leaf(node_id: str, container_id: str, size: float = 1.0) -> dict:
    return {"node": "leaf", "id": node_id, "size": size, "container_id": container_id}


def _split(node_id: str, children: list[dict], orientation: str = "horizontal") -> dict:
    return {
        "node": "split",
        "id": node_id,
        "size": 1.0,
        "orientation": orientation,
        "children": children,
    }


def _canvas(items: list[dict], containers: dict[str, dict] | None = None) -> dict:
    return {
        "kind": ViewKind.CANVAS.value,
        "containers": containers if containers is not None else {"c1": _container()},
        "items": items,
    }


def _item(item_id: str, container_id: str, **overrides) -> dict:
    placement = {
        "id": item_id,
        "container_id": container_id,
        "x": 0.0,
        "y": 0.0,
        "width": 480.0,
        "height": 320.0,
        "z_index": 0,
    }
    placement.update(overrides)
    return placement


def _nested_grid() -> dict:
    """A split of a split — the shape the recursion exists for."""
    inner = _split(
        "s2",
        [_leaf("n2", "c2", size=0.5), _leaf("n3", "c3", size=0.5)],
        orientation="vertical",
    )
    inner["size"] = 0.5
    return _flex_grid(
        containers={
            "c1": _container(),
            "c2": _container(),
            "c3": _container(ContainerKind.PANEL),
        },
        root=_split("s1", [_leaf("n1", "c1", size=0.5), inner]),
    )


def test_a_nested_split_tree_round_trips():
    payload = _nested_grid()

    layout = parse_view_layout(payload)
    dumped = layout.model_dump(mode="json")

    assert parse_view_layout(dumped) == layout
    assert dumped["kind"] == ViewKind.FLEX_GRID.value
    assert list(dumped["containers"]) == ["c1", "c2", "c3"]
    assert dumped["root"]["orientation"] == "horizontal"
    assert dumped["root"]["children"][1]["orientation"] == "vertical"
    assert [child["id"] for child in dumped["root"]["children"][1]["children"]] == ["n2", "n3"]


def test_a_canvas_layout_round_trips():
    payload = _canvas(
        containers={"c1": _container(), "c2": _container(ContainerKind.WEB_EMBED)},
        items=[
            _item("p1", "c1"),
            _item("p2", "c2", x=520.0, y=40.0, z_index=1),
        ],
    )

    layout = parse_view_layout(payload)
    dumped = layout.model_dump(mode="json")

    assert parse_view_layout(dumped) == layout
    assert dumped["kind"] == ViewKind.CANVAS.value
    assert dumped["items"][1] == payload["items"][1]
    assert dumped["containers"]["c2"]["kind"] == ContainerKind.WEB_EMBED.value


def test_split_and_leaf_are_discriminated_by_their_tag():
    """The tag is the discriminator; nothing downstream may re-derive the shape."""
    layout = parse_view_layout(_nested_grid())

    assert layout.root.node == "split"
    assert layout.root.children[0].node == "leaf"
    assert layout.root.children[1].node == "split"
    assert layout.root.children[1].children[0].container_id == "c2"


def test_container_kinds_are_the_enum_members():
    layout = parse_view_layout(_nested_grid())

    kinds = [container.kind for container in layout.containers.values()]
    assert kinds == [ContainerKind.TERMINAL, ContainerKind.TERMINAL, ContainerKind.PANEL]


def test_a_panel_primitive_id_is_carried_through_unvalidated():
    """The primitive vocabulary belongs to the frontend registry, not to this model."""
    payload = _flex_grid(
        containers={
            "c1": {
                "kind": ContainerKind.PANEL.value,
                "settings": {"primitive_id": "some.registry.id"},
            }
        },
        root=_leaf("n1", "c1"),
    )

    layout = parse_view_layout(payload)

    assert layout.containers["c1"].settings == {"primitive_id": "some.registry.id"}


def test_the_registry_is_keyed_by_container_id():
    """A map, not a list — so a duplicate id is not a case to reject but a case
    that cannot be written, and a consumer looks a container up rather than
    rebuilding this index for itself."""
    layout = parse_view_layout(_nested_grid())

    assert set(layout.containers) == {"c1", "c2", "c3"}
    assert layout.containers["c3"].kind == ContainerKind.PANEL


def test_an_empty_container_id_is_rejected():
    """The key carries the non-empty rule the old ``id`` field carried."""
    payload = _flex_grid(containers={"": _container()}, root=_leaf("n1", ""))

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_unknown_container_kind_is_rejected():
    payload = _flex_grid(
        containers={"c1": {"kind": "hologram", "settings": {}}},
        root=_leaf("n1", "c1"),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_unknown_node_tag_is_rejected():
    """Only the tag is wrong here — a leaf's every other field is present."""
    payload = _flex_grid(
        root={"node": "grid", "id": "n1", "size": 1.0, "container_id": "c1"},
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


@pytest.mark.parametrize("size", [0.0, -0.25])
def test_a_non_positive_leaf_size_is_rejected(size: float):
    """A zero-width pane is unrenderable and unrecoverable from the UI.

    The siblings absorb the difference so the row still sums to 1.0, and none of
    them exceeds 1.0 either — the non-positive size is the only thing left to
    reject, whatever other bounds the implementation also enforces.
    """
    payload = _flex_grid(
        containers={"c1": _container(), "c2": _container(), "c3": _container()},
        root=_split(
            "s1",
            [
                _leaf("n1", "c1", size=size),
                _leaf("n2", "c2", size=0.5),
                _leaf("n3", "c3", size=0.5 - size),
            ],
        ),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_sibling_sizes_that_do_not_sum_to_one_are_rejected():
    payload = _flex_grid(
        containers={"c1": _container(), "c2": _container()},
        root=_split("s1", [_leaf("n1", "c1", size=0.5), _leaf("n2", "c2", size=0.3)]),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_sibling_sizes_are_summed_with_a_tolerance():
    """Six equal panes do not sum to 1.0 in binary floating point.

    ``sum([1/6] * 6)`` is 0.9999999999999999. An exact comparison would reject
    an evenly split row — about the most ordinary layout there is — so the check
    is a tolerance, and this is the test that pins it.
    """
    sixth = 1.0 / 6.0
    containers = {f"c{index}": _container() for index in range(6)}
    payload = _flex_grid(
        containers=containers,
        root=_split(
            "s1",
            [_leaf(f"n{index}", f"c{index}", size=sixth) for index in range(6)],
        ),
    )

    assert sum([sixth] * 6) != 1.0
    assert len(parse_view_layout(payload).root.children) == 6


def test_a_split_with_no_children_is_rejected():
    """A split of nothing renders as nothing, and no pane can ever be dropped
    into it — the same unrecoverable view a zero-width leaf produces.

    Sizes cannot catch this: an empty child list has nothing to sum, so a check
    written as "the children sum to 1.0" either skips it or divides by zero.
    """
    empty = _split("s2", [], orientation="vertical")
    empty["size"] = 0.2
    payload = _flex_grid(
        containers={"c1": _container(), "c2": _container()},
        root=_split(
            "s1",
            [_leaf("n1", "c1", size=0.4), empty, _leaf("n2", "c2", size=0.4)],
        ),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_flex_grid_body_under_the_canvas_tag_is_rejected():
    """The mirror of the case below. Both directions matter: a union that
    resolves on body shape rather than on the tag silently accepts whichever
    arrangement it happens to try first, so testing only one direction leaves
    the other free to pass under the wrong tag."""
    payload = {
        "kind": ViewKind.CANVAS.value,
        "containers": {"c1": _container()},
        "root": _leaf("n1", "c1"),
    }

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


@pytest.mark.parametrize("dimension", ["width", "height"])
@pytest.mark.parametrize("value", [0.0, -120.0])
def test_a_non_positive_canvas_dimension_is_rejected(dimension: str, value: float):
    payload = _canvas(items=[_item("p1", "c1", **{dimension: value})])

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_duplicate_node_ids_are_rejected():
    payload = _flex_grid(
        containers={"c1": _container(), "c2": _container()},
        root=_split("s1", [_leaf("n1", "c1", size=0.5), _leaf("n1", "c2", size=0.5)]),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_duplicate_container_id_cannot_be_expressed():
    """The registry is a map, so the duplicate the old list could carry has no
    shape here at all — the second entry is the first, not a second container.

    This replaces a rejection test. A rule enforced by the data structure needs
    no check to enforce it, and the check would be unreachable.
    """
    payload = _flex_grid(
        containers={"c1": _container(), "c1": _container(ContainerKind.PANEL)},  # noqa: F601
        root=_leaf("n1", "c1"),
    )

    layout = parse_view_layout(payload)

    assert list(layout.containers) == ["c1"]
    assert layout.containers["c1"].kind == ContainerKind.PANEL


def test_duplicate_canvas_item_ids_are_rejected():
    payload = _canvas(
        containers={"c1": _container(), "c2": _container()},
        items=[_item("p1", "c1"), _item("p1", "c2")],
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_node_that_is_its_own_ancestor_is_rejected():
    """A cycle, in the only form a nested payload can express one.

    The tree is inline rather than a table of id references, so a pointer loop
    cannot be encoded at all. What a cycle looks like on the wire is a node
    reappearing beneath itself — which is also a duplicate id, and the same
    uniqueness walk catches both. This test therefore does not distinguish a
    cycle check from the duplicate check; it pins the payload, not the rule.
    """
    inner = _split("s1", [_leaf("n2", "c2", size=1.0)], orientation="vertical")
    inner["size"] = 0.5
    payload = _flex_grid(
        containers={"c1": _container(), "c2": _container()},
        root=_split("s1", [_leaf("n1", "c1", size=0.5), inner]),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_leaf_referencing_an_unknown_container_is_rejected():
    payload = _flex_grid(containers={"c1": _container()}, root=_leaf("n1", "c-missing"))

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_canvas_item_referencing_an_unknown_container_is_rejected():
    payload = _canvas(containers={"c1": _container()}, items=[_item("p1", "c-missing")])

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_container_no_arrangement_references_is_rejected():
    """The other half of orphaning: a container nothing can ever show."""
    payload = _flex_grid(
        containers={"c1": _container(), "c2": _container()},
        root=_leaf("n1", "c1"),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_two_nodes_sharing_one_container_are_rejected():
    """One container renders in one place; two leaves on it is an ambiguous tree."""
    payload = _flex_grid(
        containers={"c1": _container()},
        root=_split("s1", [_leaf("n1", "c1", size=0.5), _leaf("n2", "c1", size=0.5)]),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_canvas_body_under_the_flex_grid_tag_is_rejected():
    """The tag selects the arrangement: tagged ``flex_grid``, a body carrying
    ``items`` and no ``root`` is not a flex grid and must not parse as one."""
    payload = {
        "kind": ViewKind.FLEX_GRID.value,
        "containers": {"c1": _container()},
        "items": [_item("p1", "c1")],
    }

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


# --- the root's size ----------------------------------------------------


def test_the_root_node_needs_no_size():
    """Size is a share of a parent's axis. The root has no parent and no
    siblings, so there is nothing for its share to be a share *of* — requiring
    it made every caller send a number the model could only ever ignore."""
    root = _split("s1", [_leaf("n1", "c1", size=0.5), _leaf("n2", "c2", size=0.5)])
    del root["size"]
    payload = _flex_grid(
        containers={"c1": _container(), "c2": _container()},
        root=root,
    )

    layout = parse_view_layout(payload)

    assert layout.root.size == 1.0


def test_a_root_leaf_needs_no_size_either():
    leaf = _leaf("n1", "c1")
    del leaf["size"]

    assert parse_view_layout(_flex_grid(root=leaf)).root.size == 1.0


# --- sizes that are numbers but not lengths ------------------------------


@pytest.mark.parametrize("size", [math.inf, -math.inf, math.nan])
def test_a_non_finite_leaf_size_is_rejected(size: float):
    """``gt=0`` admits infinity, and infinity passes any sibling-sum check that
    subtracts it from itself. A pane cannot be infinitely wide."""
    payload = _flex_grid(root=_leaf("n1", "c1", size=size))

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_leaf_size_above_one_is_rejected():
    """A share of an axis cannot exceed the axis. ``1e308`` is finite and
    positive, so only an upper bound rejects it."""
    payload = _flex_grid(root=_leaf("n1", "c1", size=1e308))

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


@pytest.mark.parametrize("dimension", ["width", "height"])
@pytest.mark.parametrize("value", [math.inf, math.nan, 1e308, MAX_CANVAS_EXTENT * 2])
def test_a_non_finite_or_absurd_canvas_dimension_is_rejected(dimension: str, value: float):
    payload = _canvas(items=[_item("p1", "c1", **{dimension: value})])

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, 1e308])
def test_a_non_finite_canvas_coordinate_is_rejected(axis: str, value: float):
    payload = _canvas(items=[_item("p1", "c1", **{axis: value})])

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_canvas_item_at_the_extremes_of_the_bounds_is_accepted():
    """The bounds are a guard against unrenderable numbers, not a small canvas."""
    payload = _canvas(
        items=[_item("p1", "c1", x=-9999.0, y=9999.0, width=MAX_CANVAS_EXTENT, height=1.0)]
    )

    assert parse_view_layout(payload).items[0].width == MAX_CANVAS_EXTENT


# --- size of the layout itself ------------------------------------------


def _split_chain(depth: int) -> dict:
    """A single-child split nested ``depth`` deep, over one container."""
    node = _leaf("n0", "c1")
    for index in range(depth):
        node = _split(f"s{index}", [node])
    return _flex_grid(root=node)


def test_a_split_chain_at_the_depth_limit_is_accepted():
    layout = parse_view_layout(_split_chain(MAX_SPLIT_DEPTH))

    assert layout.kind == ViewKind.FLEX_GRID


def test_a_split_chain_past_the_depth_limit_is_rejected():
    """An unbounded chain passes every field rule, parses, and then overflows the
    recursion limit in *serialization* — after it has been stored. Bounding the
    depth on write is the last point where refusing it is still a refusal."""
    with pytest.raises(ValidationError):
        parse_view_layout(_split_chain(MAX_SPLIT_DEPTH + 1))


def test_a_layout_with_too_many_nodes_is_rejected():
    """Nodes are bounded separately from containers and from depth, because
    neither of those bounds implies this one: a tree can stay under 256
    containers and under 32 deep and still carry thousands of nodes, by stacking
    single-child splits over every leaf.

    So this payload is legal on both other counts — 64 containers, 9 deep — and
    is refused on node count alone.
    """
    leaves = 64
    chain = 8
    assert leaves < MAX_CONTAINERS and chain + 1 < MAX_SPLIT_DEPTH
    assert 1 + leaves * (chain + 1) > MAX_LAYOUT_NODES

    branches = []
    for leaf_index in range(leaves):
        node = _leaf(f"n{leaf_index}", f"c{leaf_index}")
        for link in range(chain):
            node = _split(f"s{leaf_index}_{link}", [node])
        node["size"] = 1.0 / leaves
        branches.append(node)
    payload = _flex_grid(
        containers={f"c{index}": _container() for index in range(leaves)},
        root=_split("root", branches),
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


def test_a_layout_with_too_many_containers_is_rejected():
    """Rejected on the registry's own size, before the arrangement is walked —
    an oversized registry is oversized whether or not anything references it."""
    payload = _canvas(
        containers={f"c{index}": _container() for index in range(MAX_CONTAINERS + 1)},
        items=[_item(f"p{index}", f"c{index}") for index in range(MAX_CONTAINERS + 1)],
    )

    with pytest.raises(ValidationError):
        parse_view_layout(payload)


# --- open settings, still not silently edited -----------------------------
#
# 444. `settings` is an open mapping because its vocabulary belongs to the
# frontend primitive registry (436), not to the control plane. Open means the
# server does not know what these keys mean — not that it may quietly rewrite
# their values.


def _settings_layout(settings: dict) -> dict:
    return _flex_grid(
        containers={"c1": {"kind": ContainerKind.PANEL.value, "settings": settings}},
        root=_leaf("n1", "c1"),
    )


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
@pytest.mark.parametrize(
    ("placement", "settings"),
    [
        ("at the top level", lambda value: {"zoom": value}),
        ("nested in a mapping", lambda value: {"camera": {"zoom": value}}),
        ("nested in a list", lambda value: {"stops": [0.5, value]}),
        ("nested under a list", lambda value: {"stops": [{"at": value}]}),
    ],
)
def test_a_non_finite_number_in_settings_is_refused(placement: str, settings, value: float):
    """Refused, not rewritten.

    `layout_payload`'s `model_dump(mode="json")` turns a non-finite float into
    `None` before the byte cap or `json.dumps` sees it, so before this rule the
    write succeeded and stored `{"zoom": null}`. That is the one outcome that is
    wrong under either policy, because it breaks the round-trip guarantee the
    rest of this layout keeps.

    Depth is parametrized because a zoom one level down is the same defect: a
    camera computed from a divide-by-zero is how `Infinity` actually arrives.
    """
    with pytest.raises(ValidationError) as caught:
        parse_view_layout(_settings_layout(settings(value)))

    assert "non-finite" in str(caught.value)
    assert placement  # named for the failure report


def test_a_finite_number_in_settings_is_kept_verbatim():
    """The refusal is about non-finite values only. A primitive that declares a
    numeric setting — none does yet, and 442's camera is the obvious first — must
    still be able to store an ordinary number."""
    layout = parse_view_layout(_settings_layout({"zoom": 1.5, "offset": -0.25, "steps": 3}))

    assert layout_payload(layout)["containers"]["c1"]["settings"] == {
        "zoom": 1.5,
        "offset": -0.25,
        "steps": 3,
    }


def test_settings_the_server_has_no_vocabulary_for_still_round_trip():
    """The narrowing is number handling, not what keys settings may carry: the
    registry (436) still owns the vocabulary, so an unrecognized key of any
    shape comes back byte-identical."""
    settings = {
        "primitive_id": "some.registry.id",
        "unknown_key": {"nested": [1, "two", True, None]},
        "": "an empty key is still a key",
    }

    layout = parse_view_layout(_settings_layout(settings))

    assert layout_payload(layout)["containers"]["c1"]["settings"] == settings


def test_a_canvas_container_is_held_to_the_same_settings_rule():
    """The rule lives on the container, which both arrangements share, so
    neither view kind can be the one that still coerces."""
    with pytest.raises(ValidationError):
        parse_view_layout(
            _canvas(
                containers={"c1": {"kind": ContainerKind.PANEL.value, "settings": {"z": math.inf}}},
                items=[_item("p1", "c1")],
            )
        )
