"""Every capped common asset the CLI executor embeds fits its cap, on every transport.

A cap that bites is reported (lg-workflow-integrity-91) but still cuts: the
control-plane module rendered 12,272 chars for the CLI transport against its
12,000 cap, so every cursor and external-harness run lost the whole closing
"Which tool for which situation" section. This fails CI when a doc outgrows its
cap instead of letting it be cut at runtime.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from loregarden.agents.mcp_context import (
    CONTROL_PLANE_MODULE_CAP,
    MEMORY_PROTOCOL_MODULE_CAP,
    UI_PRIMITIVES_MODULE_CAP,
    load_loregarden_mcp_doc,
    load_memory_protocol_doc,
    load_ui_primitives_doc,
)
from loregarden.config import settings
from loregarden.models.domain import ControlPlaneTransport

CAPPED_ASSETS: dict[str, tuple[Callable[[ControlPlaneTransport], str], int]] = {
    "control-plane module": (
        lambda transport: load_loregarden_mcp_doc(transport=transport),
        CONTROL_PLANE_MODULE_CAP,
    ),
    "memory protocol module": (
        lambda _transport: load_memory_protocol_doc(settings.agent_context_dir),
        MEMORY_PROTOCOL_MODULE_CAP,
    ),
    "chat UI primitives": (
        lambda _transport: load_ui_primitives_doc(settings.agent_context_dir),
        UI_PRIMITIVES_MODULE_CAP,
    ),
}


@pytest.mark.parametrize("transport", list(ControlPlaneTransport), ids=lambda t: t.value)
@pytest.mark.parametrize("asset", sorted(CAPPED_ASSETS))
def test_capped_common_asset_fits_its_cap(asset: str, transport: ControlPlaneTransport):
    render, cap = CAPPED_ASSETS[asset]

    body = render(transport)

    assert body, f"{asset} rendered empty for {transport.value}: the doc is missing"
    assert len(body) <= cap, (
        f"{asset} renders {len(body)} chars for {transport.value}, over its {cap} cap; "
        "the tail would be cut from every such run. Shorten the doc."
    )
