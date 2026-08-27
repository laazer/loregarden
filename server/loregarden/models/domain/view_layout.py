"""The layout both view kinds share, validated on write.

A layout is a flat registry of containers plus an *arrangement* over them. The
arrangement is the only thing that differs between the two view kinds: a flex
grid arranges containers as a recursive split tree, a canvas as a flat list of
placements. Keeping containers in one registry is what lets a container keep its
identity while it is moved between panes — and what makes "orphaned reference" a
condition this module can actually check.

Structural validation happens here rather than at render time. A malformed
layout that reaches the database is a view that cannot be opened, and therefore
cannot be repaired from the UI that fails to open it.
"""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal

from loregarden.models.domain.enums import ContainerKind, SplitOrientation, ViewKind
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

#: Sibling fractions are authored by a UI that divides 1.0 by a pane count, so
#: an evenly split row does not sum to exactly 1.0 in binary floating point
#: (``sum([1 / 6] * 6) == 0.9999999999999999``). Compare with a tolerance wide
#: enough for accumulated division error and far narrower than any layout the
#: user could have meant.
SIZE_SUM_TOLERANCE = 1e-6

#: A layout is refused above these sizes rather than stored. The bounds are not
#: taste: an unbounded split chain is accepted by every field rule here, commits,
#: and then blows the recursion limit in *response* serialization — so every
#: later read of that workspace's view list is a 500 that no API call can undo.
#: Bounding the shape on write is the only place the refusal is still a 4xx.
#:
#: The numbers are far past any layout a person composes by dragging panes. A
#: split nested deeper than a handful is already unreadable, 32 is well beyond
#: it, and it keeps the recursive walk and the recursive dump shallow. 256
#: containers is roughly a full screen of panes an order of magnitude over; 512
#: nodes covers the interior splits a 256-leaf tree needs.
MAX_SPLIT_DEPTH = 32
MAX_CONTAINERS = 256
MAX_LAYOUT_NODES = 512

#: Canvas geometry is in CSS pixels, and ``gt=0`` alone admits ``1e308`` — a
#: width that is finite, passes every bound, and renders nowhere.
MAX_CANVAS_EXTENT = 1_000_000.0
MAX_CANVAS_COORDINATE = 10_000_000.0

#: Container ids are the registry's keys, so the non-empty rule lives on the key.
ContainerId = Annotated[str, Field(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _opaque(_value: Any) -> None:
    """Stand-in for a settings value ``json`` cannot write.

    Whether settings are *encodable* is the service's error to report — it
    already answers that with a 400 naming the offending value. Substituting
    here keeps the scan below from stopping at the first foreign object and
    missing a non-finite number behind it.
    """
    return None


class ViewContainer(_Strict):
    """One pane's content, independent of where it currently sits.

    Carries no id: it is the value of a registry keyed by id, so the id is the
    key and cannot disagree with a copy of itself.

    ``settings`` stays an open mapping: what a container needs is the container
    kind's business, and for a panel it names a primitive from the frontend
    registry — a vocabulary owned by ticket 436, not by this model. Validating
    it here would put the control plane in the way of every registry addition.
    """

    kind: ContainerKind
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("settings")
    @classmethod
    def _settings_hold_no_non_finite_number(cls, settings: dict[str, Any]) -> dict[str, Any]:
        """Refuse ``Infinity``/``NaN`` anywhere in settings, at any depth.

        Open does not mean edited. ``json.loads`` accepts the ``Infinity`` and
        ``NaN`` literals, nothing above rejects them because the server owns no
        vocabulary for these keys, and then ``layout_payload``'s
        ``model_dump(mode="json")`` rewrites them to ``null`` before either the
        byte cap or ``json.dumps`` sees them. The write succeeds and the client
        silently does not get back what it sent — the one outcome that breaks
        the round-trip guarantee every other part of this layout keeps.

        Refusing matches the typed fields beside it: ``size`` and the canvas
        ``x``/``y``/``width``/``height`` all carry ``allow_inf_nan=False`` and
        reject rather than coerce.

        ``json.dumps`` *is* the walk — it is the same traversal that does the
        coercion, asked to refuse instead — so nesting, lists, and ``bool``
        versus ``float`` need no hand-rolled recursion that could disagree with
        it. ``skipkeys`` keeps a key ``json`` cannot write from aborting the
        scan; such a key cannot arrive over the wire, where every key is a
        string.
        """
        try:
            json.dumps(settings, allow_nan=False, default=_opaque, skipkeys=True)
        except ValueError as exc:
            raise ValueError(
                "Container settings must not contain a non-finite number (Infinity or NaN)"
            ) from exc
        return settings


ContainerRegistry = Annotated[dict[ContainerId, ViewContainer], Field(max_length=MAX_CONTAINERS)]


class _StructureWalk:
    """Collects ids and container references, rejecting the first conflict.

    Shared by both arrangements because both fail the same three ways: an id
    used twice, a reference to a container that does not exist, and a container
    nothing references. It also counts, because size is a failure mode too.
    """

    def __init__(self, containers: dict[str, ViewContainer]) -> None:
        self.known: set[str] = set(containers)
        self.node_ids: set[str] = set()
        self.referenced: set[str] = set()

    def claim_node(self, node_id: str) -> None:
        if node_id in self.node_ids:
            raise ValueError(f"Duplicate node id: {node_id}")
        if len(self.node_ids) >= MAX_LAYOUT_NODES:
            raise ValueError(f"Layout has more than {MAX_LAYOUT_NODES} nodes")
        self.node_ids.add(node_id)

    def claim_depth(self, depth: int, node_id: str) -> None:
        if depth >= MAX_SPLIT_DEPTH:
            raise ValueError(f"Split nesting under node {node_id} is deeper than {MAX_SPLIT_DEPTH}")

    def claim_container(self, container_id: str) -> None:
        if container_id not in self.known:
            raise ValueError(f"Node references unknown container: {container_id}")
        if container_id in self.referenced:
            raise ValueError(f"Container placed more than once: {container_id}")
        self.referenced.add(container_id)

    def finish(self) -> None:
        orphaned = self.known - self.referenced
        if orphaned:
            raise ValueError(
                f"Container(s) no arrangement references: {', '.join(sorted(orphaned))}"
            )


#: Fraction of the parent split's axis. Zero or negative is an unrenderable pane
#: that no drag handle can ever recover, and above 1.0 no set of siblings can sum
#: to one. Defaulted rather than required because the root has no siblings to sum
#: against — its share of a parent that does not exist is the whole axis.
NodeSize = Annotated[float, Field(default=1.0, gt=0, le=1.0, allow_inf_nan=False)]

#: The root's only legal share of the axis. It is not a default a caller may
#: override: a root sized 0.3 is a number with no meaning — there is no parent
#: axis to take 30% of — and every renderer built on this model would have to
#: decide on its own whether to honour it. Three tickets consume this layout, so
#: "meaningless" would be settled three separate times unless it is refused here.
ROOT_SIZE = 1.0


class LeafNode(_Strict):
    """A pane showing one container."""

    node: Literal["leaf"] = "leaf"
    id: str = Field(min_length=1)
    size: NodeSize = 1.0
    container_id: str = Field(min_length=1)

    def walk_structure(self, walk: _StructureWalk, depth: int) -> None:
        walk.claim_node(self.id)
        walk.claim_container(self.container_id)


class SplitNode(_Strict):
    """A row or column of child nodes."""

    node: Literal["split"] = "split"
    id: str = Field(min_length=1)
    size: NodeSize = 1.0
    orientation: SplitOrientation
    #: A split of nothing renders as nothing and can never be dropped into.
    children: list[LayoutNode] = Field(min_length=1)

    def walk_structure(self, walk: _StructureWalk, depth: int) -> None:
        """Depth-first over the subtree, refusing one deeper than the bound.

        A cycle cannot be encoded in an inline tree — the shape it takes on the
        wire is a node reappearing beneath itself, which the id claim rejects.
        Depth is checked before recursing, so this walk never runs deeper than
        ``MAX_SPLIT_DEPTH`` frames however deep the payload claims to be.
        """
        walk.claim_depth(depth, self.id)
        walk.claim_node(self.id)
        total = math.fsum(child.size for child in self.children)
        if abs(total - 1.0) > SIZE_SUM_TOLERANCE:
            raise ValueError(f"Sibling sizes under node {self.id} sum to {total}, not 1.0")
        for child in self.children:
            child.walk_structure(walk, depth + 1)


LayoutNode = Annotated[LeafNode | SplitNode, Field(discriminator="node")]

SplitNode.model_rebuild()


class CanvasItem(_Strict):
    """A container placed freely on the canvas surface."""

    id: str = Field(min_length=1)
    container_id: str = Field(min_length=1)
    x: float = Field(ge=-MAX_CANVAS_COORDINATE, le=MAX_CANVAS_COORDINATE, allow_inf_nan=False)
    y: float = Field(ge=-MAX_CANVAS_COORDINATE, le=MAX_CANVAS_COORDINATE, allow_inf_nan=False)
    width: float = Field(gt=0, le=MAX_CANVAS_EXTENT, allow_inf_nan=False)
    height: float = Field(gt=0, le=MAX_CANVAS_EXTENT, allow_inf_nan=False)
    z_index: int = 0


class FlexGridLayout(_Strict):
    kind: Literal[ViewKind.FLEX_GRID] = ViewKind.FLEX_GRID
    containers: ContainerRegistry
    root: LayoutNode

    @model_validator(mode="after")
    def _structure_is_sound(self) -> FlexGridLayout:
        # Compared with the tolerance the sibling sums use, because it is the
        # same 1.0 reached by the same authoring path — being stricter about the
        # root than about the row it stands for would refuse layouts the split
        # rule accepts.
        if abs(self.root.size - ROOT_SIZE) > SIZE_SUM_TOLERANCE:
            raise ValueError(f"The root node's size must be {ROOT_SIZE}, not {self.root.size}")
        walk = _StructureWalk(self.containers)
        self.root.walk_structure(walk, 0)
        walk.finish()
        return self


class CanvasLayout(_Strict):
    kind: Literal[ViewKind.CANVAS] = ViewKind.CANVAS
    containers: ContainerRegistry
    items: list[CanvasItem]

    @model_validator(mode="after")
    def _structure_is_sound(self) -> CanvasLayout:
        walk = _StructureWalk(self.containers)
        for item in self.items:
            walk.claim_node(item.id)
            walk.claim_container(item.container_id)
        walk.finish()
        return self


ViewLayout = Annotated[FlexGridLayout | CanvasLayout, Field(discriminator="kind")]

_LAYOUT_ADAPTER: TypeAdapter[FlexGridLayout | CanvasLayout] = TypeAdapter(ViewLayout)


def parse_view_layout(payload: dict) -> FlexGridLayout | CanvasLayout:
    """Validate a layout payload, raising ``ValidationError`` if it is malformed.

    The ``kind`` tag selects the arrangement; a body is never sniffed for its
    shape, so a canvas body under the flex-grid tag is a rejection rather than a
    lucky guess.
    """
    return _LAYOUT_ADAPTER.validate_python(payload)


def layout_payload(layout: FlexGridLayout | CanvasLayout) -> dict:
    """The layout as JSON-ready primitives, for storage and for API responses."""
    return layout.model_dump(mode="json")
