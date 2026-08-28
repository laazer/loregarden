"""Where a view is being looked at: the pan offset and the zoom.

Separate from ``view_layout`` because it is a separate concern with a separate
write rate. A layout is what the view *contains* — edited deliberately, stored
under a byte cap, and validated by a structural walk. A viewport is where the
user's eye is, and it changes on every pan and every zoom step. Folding it into
``CanvasLayout`` would make each of those gestures rewrite the column the cap
governs, and would teach an ``extra="forbid"`` layout model about a key that is
not layout.

The bounds are the same discipline the layout carries, for the same reason 444
exists: a stored ``Infinity`` or ``NaN`` is a viewport that scrolls nowhere and
renders nothing, and a zoom of ``0`` collapses the surface. Pan is bounded by
the canvas coordinate range ``view_layout`` already defines, so a viewport can
address the whole surface an item can be placed on and nothing beyond it.

An *absent* viewport is a real state and is not this model: it means the view
has no stored position, and the client opens the canvas at its default rather
than at the origin at zoom 0. The store spells that as an empty object.
"""

from __future__ import annotations

from loregarden.models.domain.view_layout import MAX_CANVAS_COORDINATE
from pydantic import BaseModel, ConfigDict, Field

#: A ceiling on zoom, not a UI range. The canvas offers 0.1–4; this is the far
#: wider bound that keeps a stored value finite and drawable, so a client that
#: widens its own range does not need a server change to store it. Without a
#: ceiling, ``gt=0`` admits ``1e308`` — finite, and a surface with nothing on it.
MAX_VIEWPORT_ZOOM = 100.0

#: A pan below zero is not reachable by scrolling, but it is expressible: the
#: bound mirrors the canvas coordinate range rather than inventing a narrower
#: one, so a viewport can name any point an item can occupy.
MIN_VIEWPORT_PAN = -MAX_CANVAS_COORDINATE


class ViewViewport(BaseModel):
    """Pan and zoom for one view, all three values required together.

    Required rather than defaulted: a viewport carrying only ``zoom`` would
    store a pan of 0 that the client never asked for and then restore the canvas
    somewhere it has never been. A caller with nothing to say sends no viewport
    at all, which is the absent state.

    Extra keys are refused, as on every other view body — a client sending
    ``panX`` is told, rather than having it dropped and reading back a viewport
    it did not send.
    """

    model_config = ConfigDict(extra="forbid")

    pan_x: float = Field(ge=MIN_VIEWPORT_PAN, le=MAX_CANVAS_COORDINATE, allow_inf_nan=False)
    pan_y: float = Field(ge=MIN_VIEWPORT_PAN, le=MAX_CANVAS_COORDINATE, allow_inf_nan=False)
    zoom: float = Field(gt=0, le=MAX_VIEWPORT_ZOOM, allow_inf_nan=False)


def viewport_payload(viewport: ViewViewport) -> dict:
    """The viewport as JSON-ready primitives, for storage and for responses."""
    return viewport.model_dump(mode="json")
