"""The `/api/workspaces/runtime-options` payload.

It lives here rather than in ``cli_settings`` because the three discovery
modules import ``ADAPTER_BINARIES`` from ``cli_settings``, so a payload built
there could only reach them through function-local imports. Moving the one
function that needs them puts those imports at module level and removes the
cycle instead of dodging it.

``cli_settings`` keeps the static catalogs and ``resolve_runtime_effective``;
nothing in here reads the database.
"""

from __future__ import annotations

from loregarden.models.domain import Workspace, WorkspaceRuntimeSettings
from loregarden.services.cli_settings import (
    CLAUDE_EFFORT_OPTIONS,
    CLAUDE_MODEL_OPTIONS,
    CURSOR_EFFORT_MODELS,
    CURSOR_EFFORT_OPTIONS,
    CURSOR_MODEL_OPTIONS,
    LMSTUDIO_EFFORT_OPTIONS,
    OPENCODE_EFFORT_OPTIONS,
    cli_adapter_options,
    resolve_runtime_effective,
)
from loregarden.services.codex_discovery import codex_model_options
from loregarden.services.lmstudio_discovery import lmstudio_model_options
from loregarden.services.opencode_discovery import opencode_model_options


def runtime_options_payload(
    *,
    lmstudio_base_url: str = "",
    workspace: Workspace | None = None,
    ticket_runtime: WorkspaceRuntimeSettings | None = None,
) -> dict:
    """Every pin the runtime picker offers, plus the state of the three probes.

    ``claude`` and ``cursor`` have static catalogs, so they are never probed and
    never appear in ``model_discovery``. The other three answer before they have
    an answer — a cold key reports ``probing`` while its background thread runs —
    which is why the picker is told which of four states each one is in rather
    than being handed a list that means four different things.
    """
    codex_models, codex_discovery = codex_model_options()
    lmstudio_models, lmstudio_discovery = lmstudio_model_options(lmstudio_base_url)
    opencode_models, opencode_discovery = opencode_model_options()

    return {
        "cli_adapters": cli_adapter_options(),
        "claude_models": CLAUDE_MODEL_OPTIONS,
        "cursor_models": CURSOR_MODEL_OPTIONS,
        "codex_models": codex_models,
        "lmstudio_models": lmstudio_models,
        "opencode_models": opencode_models,
        "claude_efforts": CLAUDE_EFFORT_OPTIONS,
        "cursor_efforts": CURSOR_EFFORT_OPTIONS,
        "lmstudio_efforts": LMSTUDIO_EFFORT_OPTIONS,
        "opencode_efforts": OPENCODE_EFFORT_OPTIONS,
        "cursor_effort_models": sorted(CURSOR_EFFORT_MODELS),
        "effective": resolve_runtime_effective(workspace, ticket_runtime=ticket_runtime),
        "model_discovery": {
            "codex": codex_discovery,
            "lmstudio": lmstudio_discovery,
            "opencode": opencode_discovery,
        },
    }
